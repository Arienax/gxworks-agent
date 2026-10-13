import assert from "node:assert/strict";
import test from "node:test";
import { LatestRead, reconcileById, startPolling } from "../src/lifecycle/requests.ts";

const defer = () => { let resolve, reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setTimeout(resolve, 5));

test("newer reads and disposal invalidate all older completions", () => {
  const owner = new LatestRead();
  const first = owner.begin(), second = owner.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(first.current(), false);
  first.cancel();
  assert.equal(second.current(), true, "old cleanup must not cancel a newer request");
  owner.cancel();
  assert.equal(second.current(), false);
  assert.equal(second.signal.aborted, true);
});

test("unchanged snapshots share list and record identities; changed records do not", () => {
  const before = [{ id: "1", state: { status: "running" } }, { id: "2", state: { status: "completed" } }];
  assert.equal(reconcileById(before, structuredClone(before)), before);
  const next = reconcileById(before, [{ id: "1", state: { status: "completed" } }, structuredClone(before[1])]);
  assert.notEqual(next, before);
  assert.notEqual(next[0], before[0]);
  assert.equal(next[1], before[1]);
  assert.deepEqual(reconcileById(before, []), []);
  assert.deepEqual(reconcileById(before, [...before].reverse()).map(x => x.id), ["2", "1"]);
});

test("slow polls never overlap and many invalidations coalesce to one fresh read", async () => {
  const requests = [], results = [];
  const poll = startPolling({ interval: 10, read: () => { const pending = defer(); requests.push(pending); return pending.promise; }, apply: value => results.push(value), error: assert.fail });
  try {
    await new Promise(resolve => setTimeout(resolve, 35));
    assert.equal(requests.length, 1, "completion, not a wall-clock interval, starts the next poll");
    for (let i = 0; i < 100; i++) poll.refresh();
    requests[0].resolve("old");
    await tick();
    assert.equal(requests.length, 2);
    assert.deepEqual(results, [], "an invalidated response cannot overwrite local mutation state");
    requests[1].resolve("new");
    await tick();
    assert.deepEqual(results, ["new"]);
  } finally { poll.stop(); }
});

test("hidden panels suspend polling and resume with exactly one fresh snapshot", async () => {
  const requests = [], results = [];
  const poll = startPolling({ interval: 10, read: () => { const pending = defer(); requests.push(pending); return pending.promise; }, apply: value => results.push(value), error: assert.fail });
  try {
    poll.pause(); requests[0].resolve("hidden");
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(requests.length, 1); assert.deepEqual(results, []);
    poll.resume(); poll.resume();
    assert.equal(requests.length, 2);
    requests[1].resolve("visible"); await tick();
    assert.deepEqual(results, ["visible"]);
  } finally { poll.stop(); }
});

test("failure recovers on next poll, but disposal suppresses errors and cancels transport", async () => {
  const requests = [], errors = [];
  const poll = startPolling({ interval: 5, read: signal => { const pending = defer(); requests.push({ ...pending, signal }); return pending.promise; }, apply: assert.fail, error: error => errors.push(error.message) });
  requests[0].reject(new Error("temporary failure"));
  await new Promise(resolve => setTimeout(resolve, 15));
  assert.deepEqual(errors, ["temporary failure"]);
  assert.equal(requests.length, 2);
  poll.stop(); assert.equal(requests[1].signal.aborted, true);
  requests[1].reject(new Error("after unmount")); await tick();
  assert.deepEqual(errors, ["temporary failure"]);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(requests.length, 2);
});
