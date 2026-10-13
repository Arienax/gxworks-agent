import assert from "node:assert/strict";
import test from "node:test";
import { submitGXSend } from "../src/features/gxSend.ts";

const selection = { projectId: "p1", projectName: "Test project", versionId: "v1",
  activeVersionId: "v1", epoch: 1, requestId: "one-confirmation" };

for (const mode of ["ask", "auto", "full"]) {
  test(`${mode}: one confirmation dispatches through the existing approval protocol`, async () => {
    const calls = [];
    const proposal = { id: "proposal1", status: "pending", ...(mode === "full" ? { execution_job_id: "job1" } : {}) };
    const job = { id: "job1" };
    const request = async (path, method = "GET", body) => {
      calls.push({ path, method, body });
      if (path === "/proposals") return proposal;
      return method === "GET" ? job : { job };
    };
    const result = await submitGXSend(selection, () => true, request);
    assert.deepEqual(result, { proposal, job });
    assert.deepEqual(calls[0], { path: "/proposals", method: "POST", body: {
      action: "gx_import", project_id: "p1", version_id: "v1", request_id: "one-confirmation", manual_backup_acknowledged: true,
    } });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].path, mode === "full" ? "/jobs/job1" : "/proposals/proposal1/decision");
    assert.equal(calls[1].method, mode === "full" ? "GET" : "POST");
  });
}

test("a stale selection does not create a proposal", async () => {
  await assert.rejects(submitGXSend(selection, () => false, () => assert.fail("Must not send")), /项目或版本已变化/);
});

test("switching project or version during proposal creation cannot grant approval", async () => {
  let current = true;
  const calls = [];
  await submitGXSend(selection, () => current, async path => {
    calls.push(path);
    current = false;
    return { id: "old", status: "pending" };
  });
  assert.deepEqual(calls, ["/proposals"]);
});

for (const status of ["executing", "accepted", "failed", "interrupted", "conflict", "rejected"]) {
  test(`a ${status} proposal replay never submits another decision`, async () => {
    const calls = [];
    await submitGXSend(selection, () => true, async path => { calls.push(path); return { id: "existing", status }; });
    assert.deepEqual(calls, ["/proposals"]);
  });
}

for (const failingCall of [1, 2]) {
  test(`a network failure at call ${failingCall} is not automatically retried`, async () => {
    let count = 0;
    await assert.rejects(submitGXSend(selection, () => true, async () => {
      if (++count === failingCall) throw new Error("disconnected");
      return { id: "existing", status: "pending" };
    }), /disconnected/);
    assert.equal(count, failingCall);
  });
}
