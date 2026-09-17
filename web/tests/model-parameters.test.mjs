import test from 'node:test';
import assert from 'node:assert/strict';
import { adoptSelections, changeSelection, clearKnown, conditionsMatch, controlValues, effectiveValue,
  parameterEnabled, selectedValues, validValue, ENDPOINT_PRESETS } from '../src/features/modelParameters.ts';

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
  assert.equal(controlValues(contract.parameters.budget).length, 33);
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 1, step: .25 })), [0, .25, .5, .75, 1]);
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 1 })), []);
  assert.deepEqual(controlValues(spec({ minimum: 0, maximum: 10000, step: .001 })), []);
  for (const status of ['unknown', 'accepted', 'unsupported']) assert.deepEqual(controlValues(spec({ status, values: [0, 1] })), []);
});
test('adoption separates observed schemas, defaults and selections', () => {
  const defaults = { effort: 'off', temp: .5, extra_body: { thinking: { budget_tokens: 8192, type: 'enabled' } } };
  const before = JSON.stringify([contract, defaults]);
  const user = adoptSelections(contract, {}, defaults, {});
  assert.deepEqual(user.parameters.budget, { mode: 'value', value: 8192 });
  assert.deepEqual(user.parameters.unseen_flag, { mode: 'omit' });
  assert.equal(JSON.stringify([contract, defaults]), before);
});
test('changing a dependency clears its selected dependents without erasing the observation', () => {
  const user = adoptSelections(contract, {}, { effort: 'off', temp: .5 }, {});
  const next = changeSelection(contract, user, 'effort', { mode: 'value', value: 'thorough' });
  assert.deepEqual(next.parameters.temp, { mode: 'omit' });
  assert.deepEqual(user.parameters.temp, { mode: 'value', value: .5 });
  assert.equal(parameterEnabled('temp', contract, selectedValues(contract, next)), false);
  assert.deepEqual(contract.parameters.temp.requires, { effort: ['off'] });
});
test('explicit server default and inherit are separate states', () => {
  const user = adoptSelections(contract, {}, {}, {});
  const next = changeSelection(contract, user, 'effort', { mode: 'omit' });
  assert.equal(selectedValues(contract, next, { effort: 'thorough' }).effort, null);
  next.parameters.effort = { mode: 'inherit' };
  assert.equal(selectedValues(contract, next, { effort: 'thorough' }).effort, 'thorough');
});
test('false is an explicit boolean value, never numeric zero or absence', () => {
  const user = changeSelection(contract, {}, 'unseen_flag', { mode: 'value', value: false });
  assert.equal(selectedValues(contract, user).unseen_flag, false);
  assert.equal(validValue(contract.parameters.unseen_flag, 0), false);
  assert.equal(validValue(spec({ values: [0, 1] }), true), false);
});
test('only verified discrete values are selectable; declared grids validate fractions', () => {
  assert.equal(validValue(contract.parameters.temp, .7), false);
  assert.equal(validValue(spec({ minimum: 0, maximum: 2, step: .1 }), .7), true);
  assert.equal(validValue(spec({ minimum: 0, maximum: 2, step: .1 }), .75), false);
  assert.equal(validValue(contract.parameters.budget, 8193), false);
  assert.equal(validValue(spec(), NaN), false);
});
test('scope switch clears every declared path but keeps unrelated extensions', () => {
  const defaults = { effort: 'off', temp: 1, extra_body: { thinking: { budget_tokens: 4096, type: 'enabled' }, unrelated: true } };
  const overrides = { budget: 1024 };
  const [first, second] = clearKnown(defaults, overrides, contract);
  assert.deepEqual(first, { extra_body: { thinking: { type: 'enabled' }, unrelated: true } });
  assert.deepEqual(second, {});
  assert.equal(defaults.extra_body.thinking.budget_tokens, 4096);
});
test('fixed and unsupported controls use server omission; unknown remains advanced', () => {
  const c = { ...contract, parameters: { fixed: spec({ status: 'fixed', values: [1] }), no: spec({ status: 'unsupported' }), unknown: spec({ status: 'unknown' }) } };
  const user = adoptSelections(c, {}, { fixed: 1, no: 0 }, {});
  assert.deepEqual(user.parameters.fixed, { mode: 'omit' });
  assert.deepEqual(user.parameters.no, { mode: 'omit' });
  assert.deepEqual(controlValues(c.parameters.unknown), []);
});
test('SDK extra-body precedence and deletion are preserved for legacy defaults', () => {
  assert.equal(effectiveValue({ budget: 1024, extra_body: { thinking: { budget_tokens: 4096 } } }, {}, 'budget', contract.parameters.budget), 4096);
  assert.equal(effectiveValue({ budget: 1024, extra_body: { thinking: { budget_tokens: 4096 } } }, { extra_body: null }, 'budget', contract.parameters.budget), 1024);
});
test('constraints use strict scalar identity and conjunctive requirements', () => {
  assert.equal(conditionsMatch({ requires: { a: [true] } }, { a: 1 }), false);
  assert.equal(conditionsMatch({ requires: { a: [true], b: ['low', 'none'] } }, { a: true, b: 'none' }), true);
  assert.equal(conditionsMatch({ conflicts_with: ['a'] }, { a: false }), false);
});
test('presets contain endpoints, never model names or tuning defaults', () => {
  for (const entry of ENDPOINT_PRESETS) assert.deepEqual(Object.keys(entry).sort(), ['name', 'url']);
});
