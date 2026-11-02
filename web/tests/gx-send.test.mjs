import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { submitGXSend } from "../src/features/gxSend.ts";

const selection = { projectId: "p1", projectName: "Test project", versionId: "v1",
  activeVersionId: "v1", epoch: 1, requestId: "one-confirmation" };

function sendHook() {
  const slots = [], effects = [];
  let cursor = 0, sequence = 0;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useEffect(effect, deps) {
      const index = cursor++, previous = slots[index];
      if (!previous || deps.some((value, i) => !Object.is(value, previous[i]))) effects.push(effect);
      slots[index] = deps;
    },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL("../src/workbench/useGXSend.ts", import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  new Function("require", "module", "exports", code)((name) => {
    if (name === "react") return react;
    if (name === "../features/gxSend") return { submitGXSend };
    if (name === "../api/client") return { key: () => `send-${++sequence}`, activeJob: () => false };
    assert.fail(`Unexpected hook dependency: ${name}`);
  }, module, module.exports);
  const options = {
    workspace: { pid: "p1", vid: "v1", project: { id: "p1", name: "Line", active_version_id: "v1" },
      version: { target_mode: "ladder" }, capture: () => 1 },
    feed: { jobs: [] }, canWrite: true, preview: null, operations: { gx_import: true },
  };
  return { options, render() {
    cursor = 0;
    const value = module.exports.useGXSend(options);
    for (const effect of effects.splice(0)) effect();
    return value;
  } };
}

for (const change of ["cancel", "project", "version", "activeVersion"]) {
  test(`the project path is discarded on ${change} and never reused by the next send`, () => {
    const host = sendHook();
    let view = host.render();
    view.requestGXSend(); view = host.render();
    const requestId = view.gxSend.requestId;
    view.setGXSend((current) => ({ ...current, projectPath: "D:/old.gxw" }));
    view = host.render();
    assert.equal(view.gxSend.projectPath, "D:/old.gxw");
    if (change === "cancel") view.setGXSend(null);
    if (change === "project") {
      host.options.workspace.pid = "p2";
      host.options.workspace.project = { ...host.options.workspace.project, id: "p2" };
    }
    if (change === "version") host.options.workspace.vid = "v2";
    if (change === "activeVersion") host.options.workspace.project.active_version_id = "v2";
    host.render(); view = host.render();
    assert.equal(view.gxSend, null);
    view.requestGXSend(); view = host.render();
    assert.equal(view.gxSend.projectPath, undefined);
    assert.notEqual(view.gxSend.requestId, requestId);
  });
}

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
      action: "gx_import", project_id: "p1", version_id: "v1", request_id: "one-confirmation",
    } });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].path, mode === "full" ? "/jobs/job1" : "/proposals/proposal1/decision");
    assert.equal(calls[1].method, mode === "full" ? "GET" : "POST");
  });
}

test("a stale selection does not create a proposal", async () => {
  await assert.rejects(submitGXSend(selection, () => false, () => assert.fail("Must not send")), /项目或版本已变化/);
});

for (const projectPath of ["  D:\\中文目录\\线体3.gxw  ", "", "   "]) {
  test(`the current project path is trimmed and empty values are omitted: ${JSON.stringify(projectPath)}`, async () => {
    const calls = [];
    await submitGXSend({ ...selection, projectPath }, () => true, async (path, method, body) => {
      calls.push({ path, method, body });
      return { id: "existing", status: "accepted" };
    });
    assert.deepEqual(calls[0].body, {
      action: "gx_import", project_id: "p1", version_id: "v1", request_id: "one-confirmation",
      ...(projectPath.trim() ? { project_path: projectPath.trim() } : {}),
    });
    assert.equal(calls.length, 1);
  });
}

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
