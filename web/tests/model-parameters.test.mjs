import test from 'node:test';
import assert from 'node:assert/strict';
import { adoptSelections, changeSelection, conditionsMatch, controlValues,
  parameterEnabled, selectedValues, validValue, sliderRange, ENDPOINT_PRESETS, selectionFor, modifiedParameters,
  editableReasoningLevels, reasoningChoices, reasoningOverride, rebindReasoningSelection,
  parseRequestParameterRows, requestParameterRows, withRequestParameters } from '../src/features/modelParameters.ts';

const scope = { endpoint: 'https://test.invalid/v1', model: 'model-unknown', context: 'a'.repeat(64), binding: 'b'.repeat(64) };
const spec = (extra = {}) => ({ type: 'number', status: 'supported', source: 'metadata', default_mode: 'omit', ...extra });
const contract = { schema_version: 2, scope, capabilities: {}, constraints: {}, parameters: {
  effort: spec({ type: 'enum', values: ['off', 'economy', 'thorough'] }),
  temp: spec({ values: [0, .5, 1], requires: { effort: ['off'] } }),
  budget: spec({ type: 'integer', minimum: 0, maximum: 32768, step: 1024, wire_location: 'extra_body', wire_path: ['thinking', 'budget_tokens'] }),
  unseen_flag: spec({ type: 'boolean' }),
}};

test('custom request rows preserve JSON types and existing selections', () => {
  const c = { ...contract, parameters: { ...contract.parameters,
    reasoning_effort: spec({ type: 'enum', values: ['low', 'high'] }),
  }};
  const saved = { scope, parameters: {
    temp: { mode: 'value', value: .5 }, reasoning_effort: { mode: 'value', value: 'low' },
  }};
  assert.deepEqual(requestParameterRows(saved), [{ name: 'temp', value: '0.5' }]);
  const parsed = parseRequestParameterRows([
    { name: 'top_k', value: '30' }, { name: 'flag', value: 'false' },
    { name: 'stop', value: '["END"]' }, { name: 'vendor', value: '{"enabled":true}' },
    { name: 'label', value: 'plain text' }, { name: '', value: '' },
  ], c);
  assert.deepEqual(parsed, { parameters: { top_k: 30, flag: false, stop: ['END'], vendor: { enabled: true }, label: 'plain text' } });
  const next = withRequestParameters(saved, parsed.parameters);
  assert.deepEqual(next.parameters.temp, { mode: 'omit' });
  assert.deepEqual(next.parameters.reasoning_effort, saved.parameters.reasoning_effort);
  assert.deepEqual(changeSelection(c, next, 'reasoning_effort', { mode: 'value', value: 'high' }).request_parameters, parsed.parameters);
});

test('custom request rows reject invalid JSON, duplicate names and invalid declared values', () => {
  const parse = (...pairs) => parseRequestParameterRows(pairs.map(([name, value]) => ({ name, value })), contract);
  assert.ok(parse(['stop', '["END"']).error);
  assert.ok(parse(['top_k', '1e999']).error);
  assert.ok(parse(['top_k', '10'], ['top_k', '30']).error);
  assert.ok(parse(['reasoning_effort', 'high']).error);
  assert.equal(parse(['temp', '0.7']).field, 'temp');
});

test('arbitrary metadata controls, booleans and large integer ranges need no name-specific UI', () => {
  assert.deepEqual(controlValues(contract.parameters.effort), ['off', 'economy', 'thorough']);
  assert.deepEqual(controlValues(contract.parameters.unseen_flag), [false, true]);
  assert.deepEqual(controlValues(contract.parameters.budget), []);
  assert.deepEqual(sliderRange(contract.parameters.budget), {minimum:0,maximum:32768,step:1024});
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 1, step: .25 })), []);
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 1 })), []);
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 10000, step: .001 })), []);
  for (const status of ['unknown', 'accepted']) assert.equal(validValue(spec({ status, source:"generic" }), .733), true);
});
test('adoption is driven only by canonical user selections and descriptor defaults', () => {
  const before = JSON.stringify(contract);
  const user = adoptSelections(contract, {
    scope,
    parameters: { budget: { mode: 'value', value: 8192 } },
  });
  assert.deepEqual(user.parameters.budget, { mode: 'value', value: 8192 });
  assert.deepEqual(user.parameters.unseen_flag, { mode: 'omit' });
  assert.equal(JSON.stringify(contract), before);
});
test('changing a dependency preserves the explicit value and exposes its conflict', () => {
  const user = adoptSelections(contract, {scope, parameters:{
    effort:{mode:'value',value:'off'}, temp:{mode:'value',value:.5}
  }});
  const next = changeSelection(contract, user, 'effort', { mode: 'value', value: 'thorough' });
  assert.deepEqual(next.parameters.temp, { mode: 'value', value:.5 });
  assert.deepEqual(user.parameters.temp, { mode: 'value', value: .5 });
  assert.equal(parameterEnabled('temp', contract, selectedValues(contract, next)), false);
  assert.deepEqual(contract.parameters.temp.requires, { effort: ['off'] });
});
test('explicit server default and inherit are separate states', () => {
  const user = adoptSelections(contract, {});
  const next = changeSelection(contract, user, 'effort', { mode: 'omit' });
  assert.equal(selectedValues(contract, next).effort, null);
  next.parameters.effort = { mode: 'inherit' };
  assert.equal(selectedValues(contract, next).effort, null);
});
test('false is an explicit boolean value, never numeric zero or absence', () => {
  const user = changeSelection(contract, {}, 'unseen_flag', { mode: 'value', value: false });
  assert.equal(selectedValues(contract, user).unseen_flag, false);
  assert.equal(validValue(contract.parameters.unseen_flag, 0), false);
  assert.equal(validValue(spec({ values: [0, 1] }), true), false);
});
test('only declared numeric enums constrain values; declared grids validate fractions', () => {
  assert.equal(validValue(contract.parameters.temp, .7), false);
  assert.equal(validValue(spec({ minimum: 0, maximum: 2, step: .1 }), .7), true);
  assert.equal(validValue(spec({ minimum: 0, maximum: 2, step: .1 }), .75), false);
  assert.equal(validValue(contract.parameters.budget, 8193), false);
  assert.equal(validValue(spec(), NaN), false);
});
test('fixed and unsupported controls use server omission; unknown remains advanced', () => {
  const c = { ...contract, parameters: { fixed: spec({ status: 'fixed', values: [1] }), no: spec({ status: 'unsupported' }), unknown: spec({ status: 'unknown' }) } };
  const user = adoptSelections(c, {});
  assert.deepEqual(user.parameters.fixed, { mode: 'omit' });
  assert.deepEqual(user.parameters.no, { mode: 'omit' });
  assert.deepEqual(controlValues(c.parameters.unknown), []);
});
test('constraints use strict scalar identity and conjunctive requirements', () => {
  assert.equal(conditionsMatch({ requires: { a: [true] } }, { a: 1 }), false);
  assert.equal(conditionsMatch({ requires: { a: [true], b: ['low', 'none'] } }, { a: true, b: 'none' }), true);
  assert.equal(conditionsMatch({ conflicts_with: ['a'] }, { a: false }), false);
});
test('presets contain endpoints, never model names or tuning defaults', () => {
  for (const entry of ENDPOINT_PRESETS) assert.deepEqual(Object.keys(entry).sort(), ['name', 'source', 'url']);
  // A preset is an address plus its provenance; duplicates would make the
  // select ambiguous and a non-https entry would be a typo, not a preset.
  assert.equal(new Set(ENDPOINT_PRESETS.map(entry => entry.url)).size, ENDPOINT_PRESETS.length);
  for (const entry of ENDPOINT_PRESETS) {
    assert.match(entry.url, /^https:\/\//);
    assert.ok(['local', 'dsh'].includes(entry.source));
  }
});

test('quick effort uses the declared levels and keeps existing unrelated settings', () => {
  const c = { scope, parameters: {
    reasoning_effort: spec({type: 'enum', values: ['none', 'low', 'high']}),
    temperature: spec(),
  }};
  const settings = { scope, parameters: {temperature: {mode: 'value', value: .5}} };
  const choices = reasoningChoices(c, settings);
  assert.deepEqual(choices.options.map(option => option.value), ['', 'none', 'low', 'high']);
  assert.deepEqual(choices.options.at(-1).settings.parameters, {
    temperature: {mode: 'value', value: .5}, reasoning_effort: {mode: 'value', value: 'high'},
  });
  assert.deepEqual(choices.options[0].settings.parameters.reasoning_effort, {mode: 'omit'});
  assert.equal(reasoningChoices({scope, parameters: {
    reasoning_effort: spec({type: 'enum', source: 'generic', status: 'unknown', ui_hint: {suggestions: ['high']}}),
  }}, settings), null);
});

test('quick effort switches thinking on and off without discarding dependent values', () => {
  const c = { scope, parameters: {
    reasoning_effort: spec({type: 'enum', values: ['low', 'high', 'max']}),
    thinking_mode: spec({type: 'enum', values: ['enabled', 'disabled']}),
    temperature: spec({requires: {thinking_mode: ['disabled']}}),
  }};
  const choices = reasoningChoices(c, {scope, parameters: {thinking_mode: {mode: 'value', value: 'disabled'}}});
  const choose = (menu, value) => menu.options.find(option => option.value === value);
  assert.equal(choices.current, 'none');
  assert.deepEqual(choose(choices, 'high').settings.parameters, {
    thinking_mode: {mode: 'value', value: 'enabled'}, reasoning_effort: {mode: 'value', value: 'high'},
  });
  assert.deepEqual(choose(choices, 'none').settings.parameters, {
    thinking_mode: {mode: 'value', value: 'disabled'}, reasoning_effort: {mode: 'omit'},
  });
  const withTemperature = reasoningChoices(c, {scope, parameters: {temperature: {mode: 'value', value: .5}}});
  assert.equal(choose(withTemperature, 'high').disabled, true);
  assert.equal(choose(withTemperature, 'none').disabled, false);
});

test('custom levels keep their names when API values are aliased or omitted', () => {
  const levels = {off: null, low: 'eco', high: 'deep', max: 'deep'};
  const descriptor = reasoningOverride({wire_location: 'extra_body', wire_path: ['reasoning', 'effort']}, levels);
  const c = {scope, parameters: {reasoning_effort: descriptor}};
  const menu = reasoningChoices(c, {});
  const choose = (value) => menu.options.find(option => option.value === value).settings;
  assert.deepEqual(menu.options.map(option => option.value), ['', 'off', 'low', 'high', 'max']);
  assert.deepEqual(choose('max').parameters.reasoning_effort, {mode: 'value', value: 'deep', level: 'max'});
  assert.equal(reasoningChoices(c, choose('max')).current, 'max');
  assert.deepEqual(choose('off').parameters.reasoning_effort, {mode: 'omit', level: 'off'});
  assert.equal(reasoningChoices(c, choose('off')).current, 'off');
  assert.deepEqual(choose('').parameters.reasoning_effort, {mode: 'omit'});
  assert.deepEqual(descriptor.wire_path, ['reasoning', 'effort']);
  assert.deepEqual(rebindReasoningSelection(choose('max').parameters.reasoning_effort, {...levels, max: 'ultra'}),
    {mode: 'value', level: 'max', value: 'ultra'});
  assert.deepEqual(rebindReasoningSelection(choose('max').parameters.reasoning_effort, {high: 'deep'}), {mode: 'omit'});
  assert.equal(reasoningChoices({scope, parameters: {reasoning_effort: reasoningOverride(descriptor)}}, {}), null);
});

test('legacy custom settings keep their declared levels or their one saved choice', () => {
  const oldEnum = spec({type: 'enum', source: 'manual', values: ['none', 'low', 'high', 'max']});
  assert.deepEqual(editableReasoningLevels(oldEnum), {off: 'none', low: 'low', high: 'high', max: 'max'});
  const contract = {scope, parameters: {
    reasoning_effort: spec({type: 'enum', status: 'unknown', source: 'generic', ui_hint: {suggestions: ['high']}}),
  }};
  const saved = {scope, parameters: {reasoning_effort: {mode: 'value', value: 'deep-vendor'}}};
  const picker = reasoningChoices(contract, saved);
  assert.equal(picker.current, 'deep-vendor');
  assert.deepEqual(picker.options.map(option => option.value), ['', 'deep-vendor']);
  assert.deepEqual(picker.options[0].settings.parameters.reasoning_effort, {mode: 'omit'});
  assert.equal(reasoningChoices(contract, picker.options[0].settings), null);
});

test('partial samples never invent a numeric range or imply a fixed parameter', () => {
  const partial = spec({ source: 'probe', values: [1], scan: 'partial' });
  assert.deepEqual(controlValues(partial), []);
  assert.equal(validValue(partial, .733), true);
  assert.equal(partial.status, 'supported');
});

test('extending a quick domain preserves explicit choices and omission', () => {
  const quick = { ...contract, parameters: { effort: spec({ type: 'enum', source: 'probe', values: ['economy'], scan: 'partial' }) } };
  const deep = { ...quick, parameters: { effort: spec({ type: 'enum', source: 'probe', values: ['off', 'economy', 'thorough'], scan: 'complete' }) } };
  for (const selection of [{ mode: 'value', value: 'economy' }, { mode: 'omit' }, { mode: 'inherit' }]) {
    const before = JSON.stringify(quick);
    const user = adoptSelections(deep, { scope, parameters: { effort: selection } });
    assert.deepEqual(user.parameters.effort, selection);
    assert.equal(JSON.stringify(quick), before);
  }
});

test('continuous numeric domain never borrows the observed sample grid', () => {
  const desc = spec({ source:'catalog', domain:{minimum:0,maximum:2,source:'catalog',enforcement:'hard'},
    ui_hint:{step:.01}, evidence:{accepted_values:[0,.5,1,1.5,2]} });
  assert.deepEqual(controlValues(desc), []);
  assert.deepEqual(sliderRange(desc), {minimum:0,maximum:2,step:.01});
  for (const v of [.73,.733,0,2]) assert.equal(validValue(desc,v),true);
  assert.equal(validValue(desc,2.1),false);
});
test('unknown suggested ranges are not validation limits or presumed support', () => {
  const desc=spec({status:'unknown',source:'generic',ui_hint:{minimum:0,maximum:2,step:.01}});
  assert.deepEqual(sliderRange(desc),{minimum:0,maximum:2,step:.01});
  assert.equal(validValue(desc,3.17),true);
  assert.equal(desc.status,'unknown');
});
test('unknown effort suggestions permit user-defined strings, without claiming an enum', () => {
  const desc=spec({type:'enum',source:'generic',status:'unknown',ui_hint:{suggestions:['low','medium','high']}});
  assert.deepEqual(controlValues(desc),['low','medium','high']);
  assert.equal(validValue(desc,'max'),true);
  assert.equal(validValue(desc,false),false);
});
test('domain updates never silently erase an invalid explicit selection', () => {
  const c={...contract,parameters:{temp:spec({domain:{minimum:0,maximum:1,enforcement:'hard',source:'metadata'}})}};
  const previous={scope,parameters:{temp:{mode:'value',value:1.73}}};
  const adopted=adoptSelections(c,previous);
  assert.deepEqual(adopted.parameters.temp,previous.parameters.temp);
  assert.equal(validValue(c.parameters.temp,1.73),false);
});
test('exclusive numeric bounds and multipleOf are independent of slider precision', () => {
  const desc=spec({domain:{minimum:0,exclusive_maximum:2,enforcement:'hard',source:'metadata'},ui_hint:{maximum:1.99,step:.01}});
  assert.equal(validValue(desc,1.999),true);
  assert.equal(validValue(desc,2),false);
  const grid=spec({domain:{minimum:.1,maximum:1,multiple_of:.2,enforcement:'hard',source:'metadata'}});
  assert.equal(validValue(grid,.2),true);
  assert.equal(validValue(grid,.3),false);
});

test('range presentation respects zero-anchored multiples and exclusive bounds', () => {
  const d={type:'number',status:'supported',source:'manual',domain:{enforcement:'hard',minimum:.1,maximum:1,multiple_of:.2}};
  assert.deepEqual(sliderRange(d),{minimum:.2,maximum:1,step:.2});
  assert.equal(validValue(d,.2),true); assert.equal(validValue(d,.3),false);
  const exclusive={type:'number',status:'supported',source:'metadata',domain:{enforcement:'hard',minimum:0,maximum:2,exclusive_minimum:0,exclusive_maximum:2},ui_hint:{minimum:0,maximum:2,step:.01}};
  assert.deepEqual(sliderRange(exclusive),{minimum:.01,maximum:1.99,step:.01});
  assert.equal(validValue(exclusive,1.999),true);
});

for (const status of ['unsupported', 'fixed']) {
  test(`a stale explicit ${status} choice is preserved until explicitly cleared`, () => {
    const c = { ...contract, parameters: {
      temp: spec({ status, domain: { values: [.25], enforcement: 'hard', source: 'manual' } }),
      flag: spec({ type: 'boolean' }),
    } };
    const previous = { scope, parameters: {
      temp: { mode: 'value', value: .733 }, flag: { mode: 'value', value: false },
    } };
    const before = JSON.stringify(previous);
    const adopted = adoptSelections(c, previous);
    assert.deepEqual(adopted.parameters.temp, previous.parameters.temp);
    const cleared = changeSelection(c, adopted, 'temp', { mode: 'omit' });
    assert.deepEqual(cleared.parameters.temp, { mode: 'omit' });
    assert.equal(selectedValues(c, cleared).temp, null);
    assert.deepEqual(cleared.parameters.flag, { mode: 'value', value: false });
    assert.deepEqual(adopted.parameters.temp, { mode: 'value', value: .733 });
    assert.equal(JSON.stringify(previous), before);
    assert.deepEqual(adoptSelections(c, cleared), cleared);
  });
}


test('opening saved settings preserves implicit inheritance rather than introducing omit', () => {
  const saved = {scope, parameters:{budget:{mode:'value',value:8192}}};
  const adopted = adoptSelections(contract, saved, true);
  assert.deepEqual(adopted, saved);
  assert.deepEqual(selectionFor('unseen_flag', adopted), {mode:'inherit'});
  assert.deepEqual(modifiedParameters(contract, adopted), ['budget']);
});

test('inherited dependency values are deferred to the backend request preview', () => {
  const saved = {scope, parameters:{temp:{mode:'value',value:.5}, effort:{mode:'inherit'}}};
  assert.equal(parameterEnabled('temp',contract,selectedValues(contract,saved),saved), true);
  saved.parameters.effort={mode:'value',value:'thorough'};
  assert.equal(parameterEnabled('temp',contract,selectedValues(contract,saved),saved), false);
});
