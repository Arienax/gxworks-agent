import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("../", import.meta.url));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => {
  let resolve;
  const promise = new Promise((complete) => { resolve = complete; });
  return { promise, resolve };
};
const source = (name, kind = "program", language = "ladder") => ({
  source_name: name + (kind === "program" ? ".Program.pou" : ".pou"), name, language,
  unit_kind: kind, readable: true, offline_editable: true, reason: null, program_kind: 1,
});
const main = source("MAIN"), second = source("SECOND"), fb = source("FB1", "function_block", "fbd");
const info = (programs) => ({
  programs, cpu_menu: "FX3U/FX3UC", codepage: 936, selected_source: null,
  device_comments: {}, issues: [], comment_tables: [], write_backend: "offline", native_required: false,
});
const file = { name: "warehouse.gxw", size: 100, data: "Z3h3" };
const imported = {
  status: "imported", project_id: "p-import", version_id: "v2", proposal_id: "proposal-1",
  project: { id: "p-import", target_mode: "ladder", active_version_id: "v2", versions: [{ id: "v2" }] },
};

// The browser APIs and transport are isolated. Execute the actual hooks and
// JSX event handlers, while retaining explicit control over render/effect and
// response ordering; no project, model, native adapter, or server is started.
class FileReaderStub {
  readAsDataURL(value) {
    queueMicrotask(() => {
      if (this.aborted) return;
      this.result = "data:application/octet-stream;base64," + value.data;
      this.onload?.();
    });
  }
  abort() { this.aborted = true; this.onabort?.(); }
}
const originalReader = globalThis.FileReader;
test.before(() => { globalThis.FileReader = FileReaderStub; });
test.after(() => {
  if (originalReader === undefined) delete globalThis.FileReader;
  else globalThis.FileReader = originalReader;
});

function sandbox() {
  let currentHost, sequence = 0, transport;
  const calls = [], modules = new Map();
  const react = {
    ...React,
    useState(initial) {
      const host = currentHost, index = host.cursor++;
      if (!(index in host.slots)) host.slots[index] = typeof initial === "function" ? initial() : initial;
      return [host.slots[index], (value) => {
        host.slots[index] = typeof value === "function" ? value(host.slots[index]) : value;
      }];
    },
    useRef(initial) {
      const host = currentHost, index = host.cursor++;
      if (!(index in host.slots)) host.slots[index] = { current: initial };
      return host.slots[index];
    },
    useEffect(effect, deps) {
      const host = currentHost, index = host.cursor++, previous = host.slots[index];
      if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps?.[i]))) {
        host.slots[index] = { deps, cleanup: previous?.cleanup };
        host.effects.push(() => { previous?.cleanup?.(); host.slots[index].cleanup = effect(); });
      }
    },
  };
  const client = {
    api: async (...args) => { calls.push(args); return transport(...args); },
    key: () => `gxw-request-${++sequence}`, activeJob: () => false,
  };
  function load(relative) {
    const filename = path.resolve(root, relative);
    if (modules.has(filename)) return modules.get(filename).exports;
    const code = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const module = { exports: {} };
    modules.set(filename, module);
    const localRequire = (specifier) => {
      if (specifier === "react") return react;
      if (specifier.endsWith("/api/client") || specifier === "./client") return client;
      if (specifier.endsWith("/components/ui")) return {
        Button: (props) => React.createElement("button", props),
        Modal: (props) => props.open ? React.createElement("section", {}, props.children) : null,
      };
      if (specifier === "../features/ConversationControls") return { ConversationControls: () => null };
      if (specifier.startsWith(".")) {
        const stem = path.resolve(path.dirname(filename), specifier);
        const target = [stem, stem + ".ts", stem + ".tsx"].find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
        if (target) return load(target);
      }
      return require(specifier);
    };
    new Function("require", "module", "exports", code)(localRequire, module, module.exports);
    return module.exports;
  }
  function host(hook, options) {
    const instance = {
      slots: [], cursor: 0, effects: [], options,
      render() { currentHost = this; this.cursor = 0; this.value = hook(this.options); currentHost = null; return this.value; },
      flush() { for (const effect of this.effects.splice(0)) effect(); },
    };
    instance.render(); instance.flush();
    return instance;
  }
  function flow(programs = [main]) {
    const events = [];
    transport = async (route) => route === "/gxw/inspect" ? info(programs) : imported;
    const instance = host(load("src/workbench/useGXWImport.ts").useGXWImport, {
      enabled: true, t: (text) => text,
      onOpen: () => events.push("open"), onClose: () => events.push("close"), onImported: (value) => events.push(value),
    });
    return { instance, events };
  }
  return { load, host, flow, calls, setTransport: (value) => { transport = value; } };
}

test("GXW defaults to one program and never implicitly chooses a function block", () => {
  const { defaultGXWProgram } = sandbox().load("src/api/gxw.ts");
  assert.equal(defaultGXWProgram([main, fb]), main.source_name);
  assert.equal(defaultGXWProgram([main, second]), "");
  assert.equal(defaultGXWProgram([fb]), "");
});


test("saved-source selection is read-only and discards responses from the previous version", async () => {
  const s = sandbox(), slow = deferred();
  const setError = assert.fail;
  s.setTransport(async (route) => {
    const query = new URLSearchParams(route.split("?")[1]);
    const name = query.get("program") || main.source_name;
    if (name === second.source_name && query.get("version_id") === "v1") return slow.promise;
    return { source_name: name, language: name === fb.source_name ? "fbd" : "ladder", model: {}, programs: [main, second, fb] };
  });
  const hook = s.load("src/workbench/useGXWSource.ts").useGXWSource;
  const h = s.host((options) => hook(options.pid, options.vid, options.version, true, setError), {
    pid: "project", vid: "v1", version: { program_name: main.source_name, artifacts: [{ id: "gxw", available: true }] },
  });
  await tick(); h.render(); h.flush();
  assert.equal(h.value.view.source_name, main.source_name);
  h.value.select(second.source_name); h.render(); h.flush();
  h.options = { ...h.options, vid: "v2", version: { ...h.options.version, program_name: fb.source_name } };
  h.render(); h.flush(); await tick(); h.render(); h.flush();
  assert.equal(h.value.view.source_name, fb.source_name);
  slow.resolve({ source_name: second.source_name, language: "ladder", model: {}, programs: [main, second, fb] });
  await tick(); h.render(); h.flush();
  assert.equal(h.value.view.source_name, fb.source_name);
  assert.ok(s.calls.every(([route, method]) => route.startsWith("/gxw/source?") && method === "GET"));
});


test("a joint proposal shows the source identity and difference of every changed language", () => {
  const { DiffView } = sandbox().load("src/workbench/ResultViews.tsx");
  const markup = renderToStaticMarkup(React.createElement(DiffView, { t: (text) => text, value: {
    kind: "st", has_changes: true, unified_diff: "active program",
    source_changes: [
      { source_name: "Program1.Program.pou", language: "st", unit_kind: "program", unified_diff: "-Counter := ;\n+Counter := 8;" },
      { source_name: "Calc.Program\\Lib.lnb", language: "fbd", unit_kind: "function_block", unified_diff: "+new connection" },
    ],
  } }));
  assert.ok(markup.includes("Program1.Program.pou") && markup.includes("Calc.Program\\Lib.lnb"));
  assert.ok(markup.includes("Counter := 8;") && markup.includes("new connection"));
});

test("an empty workspace can import, adopt, and explicitly import the same file again", async () => {
  const s = sandbox(), { instance: h, events } = s.flow();
  await h.value.open([file]); h.render();
  assert.equal(h.value.program, main.source_name);
  assert.equal(h.value.busy, false);
  await h.value.save(); h.render();
  assert.equal(events.filter((event) => typeof event === "object").length, 1);
  assert.equal(h.value.upload, null);
  const firstId = s.calls.find(([route]) => route === "/gxw/import")[2].request_id;
  await h.value.open([file]); h.render(); await h.value.save();
  const imports = s.calls.filter(([route]) => route === "/gxw/import");
  assert.equal(imports.length, 2);
  assert.notEqual(imports[1][2].request_id, firstId);
  assert.ok(imports.every(([, method, body]) => method === "POST" && !Object.hasOwn(body, "project_id")));
});

test("multiple programs require a checked explicit selection before importing", async () => {
  const s = sandbox(), { instance: h } = s.flow([main, second, fb]);
  await h.value.open([file]); h.render();
  assert.equal(h.value.program, "");
  await h.value.save();
  assert.equal(s.calls.filter(([route]) => route === "/gxw/import").length, 0);
  await h.value.selectProgram(second.source_name); h.render();
  assert.equal(s.calls.at(-1)[0], "/gxw/inspect");
  assert.equal(s.calls.at(-1)[2].program, second.source_name);
  await h.value.save();
  assert.equal(s.calls.at(-1)[2].program, second.source_name);
});

test("a function block remains selectable when explicitly chosen", async () => {
  const s = sandbox(), { instance: h } = s.flow([fb]);
  await h.value.open([file]); h.render();
  assert.equal(h.value.program, "");
  await h.value.selectProgram(fb.source_name); h.render(); await h.value.save();
  assert.equal(s.calls.at(-1)[2].program, fb.source_name);
});

test("mixed files and oversized projects are rejected before any transport", async () => {
  for (const files of [[file, { name: "diagram.png", size: 1 }], [{ ...file, size: 31 * 1024 * 1024 }]]) {
    const s = sandbox(), { instance: h } = s.flow();
    await h.value.open(files); h.render();
    assert.ok(h.value.error);
    assert.equal(h.value.busy, false);
    assert.equal(s.calls.length, 0);
  }
});

test("an uncertain import response keeps its idempotency key for explicit retry", async () => {
  const s = sandbox(), { instance: h, events } = s.flow();
  let attempts = 0;
  s.setTransport(async (route) => {
    if (route === "/gxw/inspect") return info([main]);
    if (++attempts === 1) throw new Error("response lost");
    return imported;
  });
  await h.value.open([file]); h.render(); await h.value.save(); h.render();
  assert.match(h.value.error, /response lost/);
  const failedId = s.calls.at(-1)[2].request_id;
  await h.value.save();
  assert.equal(s.calls.at(-1)[2].request_id, failedId);
  assert.equal(events.filter((event) => typeof event === "object").length, 1);
});

test("changing the selected source starts a different import request", async () => {
  const s = sandbox(), { instance: h } = s.flow([main, second]);
  await h.value.open([file]); h.render(); await h.value.selectProgram(main.source_name); h.render();
  s.setTransport(async (route) => { if (route === "/gxw/import") throw new Error("response lost"); return info([main, second]); });
  await h.value.save(); const previousId = s.calls.at(-1)[2].request_id; h.render();
  await h.value.selectProgram(second.source_name); h.render();
  s.setTransport(async () => imported); await h.value.save();
  assert.notEqual(s.calls.at(-1)[2].request_id, previousId);
});

test("closed inspections discard late responses", async () => {
  const s = sandbox(), { instance: h } = s.flow(), pending = deferred();
  s.setTransport(() => pending.promise);
  const operation = h.value.open([file]); await tick(); h.render(); h.value.close();
  pending.resolve(info([main])); await operation; h.render();
  assert.equal(h.value.inspection, null);
  assert.equal(h.value.filename, "");
});

test("a source inspection blocks stale import handlers before the next render", async () => {
  const s = sandbox(), { instance: h } = s.flow([main, second]);
  await h.value.open([file]); h.render(); await h.value.selectProgram(main.source_name); h.render();
  const staleSave = h.value.save, pending = deferred();
  s.setTransport(() => pending.promise);
  const inspecting = h.value.selectProgram(second.source_name);
  await staleSave();
  assert.equal(s.calls.filter(([route]) => route === "/gxw/import").length, 0);
  pending.resolve(info([main, second])); await inspecting;
});

test("read-only sessions cannot inspect or import GXW files", async () => {
  const s = sandbox(), { instance: h } = s.flow();
  h.options.enabled = false; h.render(); await h.value.open([file]);
  assert.equal(s.calls.length, 0);
});

test("GXW projects never enter image attachment transport or composer state", async () => {
  const s = sandbox();
  const composer = s.host(() => s.load("src/workbench/useComposer.ts").useComposer("p", "v", (text) => text));
  await assert.rejects(() => composer.value.upload([file]), /GXW/);
  assert.equal(s.calls.length, 0);
  assert.deepEqual(composer.value.attachments, []);
});

function find(element, predicate) {
  if (!element || typeof element !== "object") return null;
  if (predicate(element)) return element;
  for (const child of React.Children.toArray(element.props?.children)) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}

test("the empty-project picker resets identical filenames and drop events use GXW import", () => {
  const s = sandbox(), opened = [];
  const props = {
    analysisMode: "direct", attachments: [], canSubmit: false, canWrite: true,
    composerRoute: { requiresText: true, placeholder: "需求", submitLabel: "生成" }, editAction: "edit",
    guarded: (action) => action(), gxwImport: { open: (files) => opened.push(files) },
    gxwSource: { available: false },
    hasSavedVersions: false, intent: "direct_generation", jobs: [], locale: "zh-CN", network: null,
    pid: "", project: null, scopeAddresses: "", scopeEnabled: false, scopeNetworks: "", t: (text) => text,
    task: "create", text: "", workflow: "direct", setModal() {}, setText() {},
  };
  const panel = s.host(() => s.load("src/workbench/ComposerPanel.tsx").ComposerPanel(props)).value;
  const input = find(panel, (element) => element.type === "input" && element.props.accept === ".gxw");
  const event = { target: { files: [file], value: "same-file.gxw" } };
  input.props.onChange(event); assert.equal(event.target.value, ""); input.props.onChange(event);
  assert.equal(opened.length, 2);
  assert.equal(find(panel, (element) => element.props?.["aria-label"] === "导入 GXW").props.disabled, false);
  const area = find(panel, (element) => element.type === "div" && String(element.props.className).startsWith("composer"));
  let prevented = false;
  area.props.onDrop({ preventDefault() { prevented = true; }, dataTransfer: { files: [file] } });
  assert.equal(prevented, true); assert.equal(opened.length, 3);
});

test("the import dialog preserves program, FB and FUNCTION identities in separate groups", () => {
  const s = sandbox(), { GXWImportDialog } = s.load("src/workbench/GXWImportDialog.tsx");
  const markup = renderToStaticMarkup(React.createElement(GXWImportDialog, {
    open: true, enabled: true, t: (text) => text,
    model: { inspection: info([main, second, fb, source("Function1", "function", "st")]), filename: file.name, program: "", phase: "", busy: false, selectProgram() {}, close() {}, save() {} },
  }));
  assert.match(markup, /optgroup label="程序"/);
  assert.match(markup, /optgroup label="功能块"/);
  assert.match(markup, /optgroup label="函数"/);
  assert.match(markup, /Function1.pou/);
  assert.match(markup, /selected=""[^>]*>请选择要导入的对象/);
  assert.match(markup, /disabled=""[^>]*>导入工程/);
});

test("instruction and comment edits remain visible together, including table identity and empty rows", () => {
  const { DiffView } = sandbox().load("src/workbench/ResultViews.tsx");
  const markup = renderToStaticMarkup(React.createElement(DiffView, { t: (text) => text, value: {
    kind: "ladder", has_changes: true, before_network_count: 9, after_network_count: 9,
    changes: [{ marker: "~", network: 2, before: "AND< D1 K55", after: "AND< D1 K54" }],
    unified_diff: "comment-only supplemental text",
    device_comment_changes: [{ address: "S0", before: "legacy projection", after: "stale projection" }],
    comment_changes: [
      { table: "Comment.qcd", address: "S0", before: "Runing", after: "离线写回验收：完整保留其余程序与注释" },
      { table: "Comment.qcd", address: "S1", before: "", after: null },
    ],
  } }));
  assert.ok(markup.includes("AND&lt; D1 K55") && markup.includes("AND&lt; D1 K54"));
  assert.ok(markup.includes("comment-only supplemental text"));
  assert.ok(markup.includes("Comment.qcd") && markup.includes("完整保留其余程序与注释"));
  assert.ok(markup.includes("空注释") && markup.includes("—"));
  assert.ok(!markup.includes("stale projection"));
});

test("comment-only ST, FBD and SFC changes have a visible comment table", () => {
  const { DiffView } = sandbox().load("src/workbench/ResultViews.tsx");
  for (const kind of ["st", "fbd", "sfc"]) {
    const markup = renderToStaticMarkup(React.createElement(DiffView, { t: (text) => text, value: {
      kind, has_changes: true, unified_diff: "source comments",
      comment_changes: [{ table: "Existing.qcd", address: "D0", before: "", after: "full text" }],
    } }));
    assert.match(markup, /<summary>软元件注释变化<\/summary>/);
    assert.ok(markup.includes("Existing.qcd") && markup.includes("full text") && markup.includes("空注释"));
  }
});

test("adopting an imported project keeps its returned version through selection effects", async () => {
  const s = sandbox(), session = { read_only: false };
  const previousLocation = globalThis.location, previousHistory = globalThis.history;
  globalThis.location = { search: "" }; globalThis.history = { replaceState() {} };
  try {
    let projects = [];
    s.setTransport(async (route) => route === "/projects" ? { projects } : projects.find((project) => route === "/projects/" + project.id));
    const workspace = s.host(() => s.load("src/workbench/useProjectWorkspace.ts").useProjectWorkspace(session, assert.fail));
    await tick(); workspace.render(); workspace.flush();
    const fresh = { id: "new-project", name: "new", target_mode: "ladder", active_version_id: "v1", versions: [{ id: "v1" }, { id: "v2" }] };
    projects = [fresh]; workspace.value.adoptProject(fresh, "v2"); workspace.render(); workspace.flush();
    await tick(); workspace.render(); workspace.flush();
    assert.equal(workspace.value.pid, "new-project"); assert.equal(workspace.value.vid, "v2");
    assert.equal(workspace.value.project.id, "new-project");
  } finally {
    if (previousLocation === undefined) delete globalThis.location; else globalThis.location = previousLocation;
    if (previousHistory === undefined) delete globalThis.history; else globalThis.history = previousHistory;
  }
});

test("a project list started before GXW import cannot replace the imported selection", async () => {
  const s = sandbox(), session = { read_only: false }, oldRead = deferred();
  const previousLocation = globalThis.location, previousHistory = globalThis.history;
  globalThis.location = { search: "" }; globalThis.history = { replaceState() {} };
  try {
    const old = { id: "old-project", versions: [{ id: "v1" }], active_version_id: "v1" };
    const fresh = { id: "imported-project", versions: [{ id: "v2" }], active_version_id: "v2" };
    let calls = 0;
    s.setTransport(async (route) => route === "/projects" ? ++calls === 1 ? oldRead.promise : { projects: [fresh, old] } : fresh);
    const workspace = s.host(() => s.load("src/workbench/useProjectWorkspace.ts").useProjectWorkspace(session, assert.fail));
    workspace.value.adoptProject(fresh, "v2");
    oldRead.resolve({ projects: [old] });
    await tick(); workspace.render(); workspace.flush();
    await tick(); workspace.render(); workspace.flush();
    assert.equal(workspace.value.pid, "imported-project");
    assert.equal(workspace.value.vid, "v2");
    assert.equal(workspace.value.project.id, "imported-project");
    assert.ok(workspace.value.projects.some((row) => row.id === "imported-project"));
  } finally {
    if (previousLocation === undefined) delete globalThis.location; else globalThis.location = previousLocation;
    if (previousHistory === undefined) delete globalThis.history; else globalThis.history = previousHistory;
  }
});
