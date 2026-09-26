import test from 'node:test';
import assert from 'node:assert/strict';
import { adoptSelections, changeSelection, conditionsMatch, controlValues,
  parameterEnabled, selectedValues, validValue, sliderRange, ENDPOINT_PRESETS } from '../src/features/modelParameters.ts';

const scope = { endpoint: 'https://test.invalid/v1', model: 'model-unknown', context: 'a'.repeat(64), binding: 'b'.repeat(64) };
const spec = (extra = {}) => ({ type: 'number', status: 'supported', source: 'metadata', default_mode: 'omit', ...extra });
const contract = { schema_version: 2, scope, capabilities: {}, constraints: {}, parameters: {
  effort: spec({ type: 'enum', values: ['off', 'economy', 'thorough'] }),
  temp: spec({ values: [0, .5, 1], requires: { effort: ['off'] } }),
  budget: spec({ type: 'integer', minimum: 0, maximum: 32768, step: 1024, wire_location: 'extra_body', wire_path: ['thinking', 'budget_tokens'] }),
  unseen_flag: spec({ type: 'boolean' }),
}};

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
