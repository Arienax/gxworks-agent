import assert from "node:assert/strict";
import test from "node:test";
import { mergeTrace, sampleAt, signalPath, formatSignal } from "../src/features/simulatorTimelineData.ts";

const sample = (seq, at_ms, x) => ({ seq, at_ms, values: { x }, errors: {} });
const state = (view_id, samples = []) => ({ view_id, config: { title: view_id }, samples, events: [], sequence: samples.at(-1)?.seq || 0 });

test("incremental sampling merges once and a new run cannot reuse old values", () => {
  const first = state("a", [sample(1, 0, 0)]);
  const second = mergeTrace(first, state("a", [sample(1, 0, 0), sample(2, 100, 1)]));
  assert.equal(second.samples.length, 2);
  const unchanged = mergeTrace(second, state("a"));
  assert.equal(unchanged.samples, second.samples);
  assert.deepEqual(mergeTrace(second, state("b")).samples, []);
});

test("one shared cursor never takes a value from the future or fills a missing read", () => {
  const samples = [sample(1, 100, 2), sample(2, 200, null), sample(3, 400, 8)];
  assert.equal(sampleAt(samples, 99), null);
  assert.equal(sampleAt(samples, 199).values.x, 2);
  assert.equal(sampleAt(samples, 300).values.x, null);
  assert.equal(formatSignal(null), "—");
  assert.equal(formatSignal(false), "OFF");
});

test("waveforms leave gaps and BOOL tracks use a step transition", () => {
  const points = [sample(1, 0, false), sample(2, 100, true), sample(3, 200, null), sample(4, 300, false)];
  const path = signalPath(points, { id: "x", data_type: "BOOL" }, 400, 0, 1);
  assert.equal(path.match(/M/g).length, 2);
  assert.match(path, /H250V/);
});

test("numeric display retains the differences between float32 neighbours", () => {
  assert.notEqual(formatSignal(0.04999999701976776), formatSignal(0.05000000074505806));
  assert.equal(formatSignal(2147483646.5), "2147483646.5");
});
