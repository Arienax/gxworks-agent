import { updateTest, updateStep, reorderStep } from "../src/features/simulationDraft.ts";
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

test("editing a simulation step preserves the saved suite and unrelated branches", () => {
  const step = (id) => Object.freeze({ id, at_ms: 0, set: Object.freeze({ X0: 0 }),
    expect: Object.freeze([]), wait_for: Object.freeze([]) });
  const first = Object.freeze({ name: "First", steps: Object.freeze([step("a"), step("b")]) });
  const second = Object.freeze({ name: "Second", steps: Object.freeze([step("c")]) });
  const suite = Object.freeze({ name: "Saved", tests: Object.freeze([first, second]) });
  const edited = updateTest(suite, 0, test => updateStep(test, 1, old => ({ ...old, at_ms: 20 })));
  assert.equal(suite.tests[0].steps[1].at_ms, 0);
  assert.equal(edited.tests[0].steps[1].at_ms, 20);
  assert.equal(edited.tests[1], second);
  assert.equal(edited.tests[0].steps[0], first.steps[0]);
  assert.equal(edited.tests[0].steps[1].set, first.steps[1].set);
  const moved = reorderStep(edited.tests[0], 1, -1);
  assert.deepEqual(moved.steps.map(step => step.id), ["b", "a"]);
  assert.equal(moved.steps[0], edited.tests[0].steps[1]);
  assert.equal(reorderStep(moved, 0, -1), moved);
});


test("draft tracking clears reverted edits without visiting unchanged descendants", async () => {
  const { savedDraft, editDraft } = await import("../src/lifecycle/drafts.ts");
  const untouched = {};
  Object.defineProperty(untouched, "payload", { enumerable: true, get() { throw Error("unchanged branch visited"); } });
  const saved = { tests: [{ name: "First", steps: [{ at_ms: 0 }] }, untouched] };
  let draft = editDraft(savedDraft(saved), updateTest(saved, 0, test => updateStep(test, 0, step => ({ ...step, at_ms: 20 }))));
  assert.ok(draft.changes);
  draft = editDraft(draft, updateTest(draft.value, 0, test => updateStep(test, 0, step => ({ ...step, at_ms: 0 }))));
  assert.equal(draft.changes, undefined);
});

test("draft changes match saved values through structural edits and save adoption", async () => {
  const { savedDraft, editDraft, acceptDraft } = await import("../src/lifecycle/drafts.ts");
  const initial = { tests: [{ name: "First", steps: [{ at_ms: 0 }] }], options: { mode: null } };
  const values = [
    { ...initial, tests: [...initial.tests, { name: "Second", steps: [] }] },
    { ...initial, tests: [] },
    { tests: initial.tests },
    { ...initial, options: { mode: false, extra: "x" } },
    structuredClone(initial),
  ];
  let draft = savedDraft(initial);
  for (const value of values) {
    draft = editDraft(draft, value);
    assert.equal(!!draft.changes, JSON.stringify(value) !== JSON.stringify(initial));
  }
  const submitted = draft.value;
  const edited = { ...submitted, options: { mode: true } };
  const saved = structuredClone(submitted);
  const duringSave = acceptDraft(editDraft(draft, edited), submitted, saved);
  assert.equal(duringSave.value, edited);
  assert.ok(duringSave.changes);
  assert.equal(editDraft(duringSave, saved).changes, undefined);
  const clean = acceptDraft(draft, submitted, saved);
  assert.equal(clean.value, saved);
  assert.equal(clean.changes, undefined);
});
