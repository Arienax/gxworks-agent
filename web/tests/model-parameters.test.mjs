import test from "node:test";
import assert from "node:assert/strict";
import { controlValues, changeParameter, effectiveValue, reconcileParameters, temperatureModeMatches, ENDPOINT_PRESETS } from "../src/features/modelParameters.ts";

test("sliders only expose declared or validated values", () => {
  assert.deepEqual(controlValues({ status: "supported", values: ["low", "high", "max"] }), ["low", "high", "max"]);
  for (const status of ["accepted", "unknown", "unsupported"]) assert.deepEqual(controlValues({ status, values: ["high"] }), []);
  assert.deepEqual(controlValues({ status: "supported", minimum: 0, maximum: 1, step: .25 }), [0, .25, .5, .75, 1]);
  assert.deepEqual(controlValues({ status: "supported", minimum: 0, maximum: 1, step: 0 }), []);
});

test("changing a slider clears competing overrides, not unrelated extensions", () => {
  const defaults = { temperature: 1, extra_body: { reasoning_effort: "low", enable_thinking: true } };
  const overrides = { reasoning_effort: "max", temperature: 1.2, extra_body: { temperature: .9, thinking: { type: "enabled" } } };
  const before = JSON.stringify([defaults, overrides]);
  const next = changeParameter(defaults, overrides, "reasoning_effort", "high");
  assert.equal(effectiveValue(...next, "reasoning_effort"), "high");
  assert.equal(effectiveValue(...next, "temperature"), undefined);
  assert.equal(next[0].extra_body.enable_thinking, true);
  assert.equal(next[1].extra_body.thinking.type, "enabled");
  assert.equal(JSON.stringify([defaults, overrides]), before);
});

test("server default omits the parameter rather than guessing a value", () => {
  const next = changeParameter({ temperature: .5 }, { extra_body: { temperature: 1 } }, "temperature", null);
  assert.equal(effectiveValue(...next, "temperature"), undefined);
});

test("unsupported or fixed parameters are removed when adopting detection", () => {
  const next = reconcileParameters({ temperature: .5, reasoning_effort: "medium" }, {}, { parameters: {
    temperature: { status: "fixed", values: [1] }, reasoning_effort: { status: "supported", values: ["low", "high"] },
  } });
  assert.equal(effectiveValue(...next, "temperature"), undefined);
  assert.equal(effectiveValue(...next, "reasoning_effort"), undefined);
});

test("temperature contracts cannot be reused after changing reasoning mode", () => {
  assert.equal(temperatureModeMatches({ reasoning_effort: "none" }, "high"), false);
  assert.equal(temperatureModeMatches({ reasoning_effort: null }, undefined), true);
});

test("presets contain endpoints, never model names or sampling defaults", () => {
  assert.equal(ENDPOINT_PRESETS.length, 7);
  assert(ENDPOINT_PRESETS.every(p => Object.keys(p).sort().join() === "name,url"));
  assert(ENDPOINT_PRESETS.some(p => p.url.endsWith("/v1beta/openai")));
});


test("SDK extra-body precedence includes explicit deletion", () => {
  const defaults = { reasoning_effort: "low", extra_body: { reasoning_effort: "high" } };
  assert.equal(effectiveValue(defaults, { reasoning_effort: "max" }, "reasoning_effort"), "high");
  assert.equal(effectiveValue(defaults, { reasoning_effort: "max", extra_body: null }, "reasoning_effort"), "max");
  assert.equal(effectiveValue(defaults, { reasoning_effort: "max", extra_body: { reasoning_effort: null } }, "reasoning_effort"), "max");
});
