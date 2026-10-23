import assert from "node:assert/strict";
import test from "node:test";
import { conversationRoute } from "../src/features/conversationRouting.ts";
import { clarificationQuestions, clarificationSubmission } from "../src/features/clarificationAnswers.ts";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { listedJobStatus } from "../src/workbench/jobLabels.ts";

const initial = { task: "create", workflow: "direct", editAction: "edit", targetMode: "ladder",
  hasVersions: false, hasSpec: false, reviewRequested: false, continuingDirect: false };
const route = changes => conversationRoute({ ...initial, ...changes });

test("task history preserves unresolved generation and failed execution outcomes", () => {
  for (const [kind, status, result, expected] of [
    ["execution", "completed", { status: "failed" }, "failed"],
    ["execution", "completed", { status: "interrupted" }, "failed"],
    ["execution", "completed", { status: "conflict" }, "failed"],
    ["direct_generation", "completed", { status: "needs_input" }, "needs_input"],
    ["generation", "completed", { status: "contract_mismatch" }, "contract_mismatch"],
    ["generation", "completed", { proposal_id: "proposal-1" }, "candidate_ready"],
    ["generation", "completed", { version_id: "v0001", proposal_id: "proposal-1" }, "saved"],
    ["generation", "running", { status: "needs_input" }, "running"],
    ["review", "completed", null, "completed"],
  ]) {
    const job = { id: "history-job", kind, status, result };
    assert.equal(listedJobStatus(job, "other-job", "saved"), expected);
    assert.equal(listedJobStatus(job, "history-job", "loading_result"), "loading_result");
  }
});

test("canvas shows only the current program language, including imported programs and candidates", () => {
  const path = new URL("../src/workbench/ProgramWorkspace.tsx", import.meta.url);
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  const dependencies = {
    "../api/client": { activeJob: () => false },
    "../components/RetainedPanel": { RetainedPanel: ({ active, children }) => active ? children() : null },
    "../components/ui": { Button: ({ children, ...props }) => createElement("button", props, children) },
    "../features/ProjectToolbar": { ProjectToolbar: () => null },
    "../workbench/EditorContent": { EditorContent: ({ visibleTab }) => createElement("article", { "data-view": visibleTab }) },
  };
  new Function("require", "module", "exports", code)(name => dependencies[name] || require(name), module, module.exports);
  const labels = { ladder: "梯形图", fbd: "FBD", sfc: "SFC", st: "ST" };
  const cases = [
    ...Object.keys(labels).map(target => ({ target, expected: target })),
    { target: "ladder", document: "st", expected: "st" },
    { target: "st", document: "fbd", expected: "fbd" },
    { target: "ladder", document: "st", candidate: "sfc", expected: "sfc" },
    { target: "st", document: "fbd", candidate: "ladder", expected: "ladder" },
    { target: "fbd", tab: "reports", expected: "fbd" },
  ];
  for (const { target, document, candidate, tab = "ladder", expected } of cases) {
    const markup = renderToStaticMarkup(createElement(module.exports.ProgramWorkspace, {
      pid: "project", project: { id: "project", target_mode: target },
      editorProps: { version: document ? { target_mode: document } : undefined },
      preview: candidate ? { target_mode: candidate } : null,
      jobs: [], operations: {}, panel: "agent", tab, t: value => value,
    }));
    const nav = markup.match(/<nav\b[^>]*>(.*?)<\/nav>/s)?.[1];
    assert.ok(nav);
    for (const [language, label] of Object.entries(labels)) {
      assert.equal(nav.includes(`aria-label="${label}"`), language === expected, JSON.stringify({ target, document, candidate, language }));
    }
    assert.match(nav, /aria-label="诊断"/);
    assert.match(nav, /aria-label="仿真记录"/);
    assert.match(nav, /aria-label="工程交付摘要"/);
    const active = tab === "reports" ? tab : expected;
    assert.ok(markup.includes(`data-view="${active}"`));
    assert.equal((markup.match(/data-view=/g) || []).length, 1);
  }
});

test("creation separates direct generation, analysis and confirmed generation", () => {
  assert.equal(route({}).kind, "direct_generation");
  assert.equal(route({ workflow: "review" }).kind, "analysis");
  const confirmed = route({ workflow: "review", hasSpec: true });
  assert.equal(confirmed.kind, "generation");
  assert.equal(confirmed.submitLabel, "按需求生成");
  assert.equal(confirmed.requiresText, false);
  assert.equal(confirmed.generationAction, undefined);
  assert.equal(route({ workflow: "review", hasSpec: true, reviewRequested: true }).kind, "analysis");
  assert.equal(route({ hasVersions: true }).available, false);
});

test("ST and FBD retain analysis and confirmed generation without a Direct route", () => {
  for (const targetMode of ["st", "fbd"]) {
    assert.equal(route({ targetMode }).kind, "analysis");
    assert.equal(route({ targetMode, hasSpec: true }).kind, "generation");
    assert.equal(route({ targetMode, task: "edit", editAction: "regenerate", hasVersions: true }).available, false);
  }
});

test("modification requires a baseline and an actual change request, including Direct-created programs", () => {
  assert.equal(route({ task: "edit" }).available, false);
  for (const hasSpec of [false, true]) {
    const editing = route({ task: "edit", hasVersions: true, hasSpec });
    assert.equal(editing.kind, "generation");
    assert.equal(editing.generationAction, "edit");
    assert.equal(editing.requiresText, true);
    assert.equal(editing.submitLabel, "修改当前程序");
    assert.doesNotMatch(editing.placeholder, /已确认规格/);
  }
});

test("an existing SFC version submits an edit through the shared generation route", () => {
  const editing = route({ targetMode: "sfc", task: "edit", hasVersions: true });
  assert.equal(editing.kind, "generation");
  assert.equal(editing.available, true);
  assert.equal(editing.generationAction, "edit");
  assert.equal(editing.requiresText, true);
  assert.equal(route({ targetMode: "sfc", task: "edit", hasVersions: false }).available, false);
});

test("full regeneration first obtains a specification and explicitly selects regeneration", () => {
  const editing = { task: "edit", editAction: "regenerate", hasVersions: true };
  assert.equal(route(editing).kind, "analysis");
  const confirmed = route({ ...editing, hasSpec: true });
  assert.equal(confirmed.kind, "generation");
  assert.equal(confirmed.generationAction, "regenerate");
  assert.equal(confirmed.requiresText, false);
  assert.equal(confirmed.submitLabel, "按需求重新生成");
  assert.equal(route({ ...editing, hasSpec: true, reviewRequested: true }).kind, "analysis");
});

test("clarification wording belongs only to active Direct creation", () => {
  assert.equal(route({ continuingDirect: true }).submitLabel, "补充并继续");
  assert.equal(route({ continuingDirect: true, task: "edit", hasVersions: true }).submitLabel, "修改当前程序");
  assert.equal(route({ continuingDirect: true, workflow: "review", hasSpec: true }).submitLabel, "按需求生成");
});

test("questions remain questions across project stages and remembered workflow choices", () => {
  for (const hasVersions of [false, true]) for (const hasSpec of [false, true]) {
    const question = route({ task: "question", hasVersions, hasSpec, workflow: "review",
      editAction: "regenerate", continuingDirect: true, reviewRequested: true });
    assert.equal(question.kind, "agent");
    assert.equal(question.available, true);
    assert.equal(question.requiresText, true);
    assert.equal(question.generationAction, undefined);
    assert.equal(question.submitLabel, "提问");
  }
});

test("Direct answers bind to individual questions and never accept suggested defaults", () => {
  const questions = clarificationQuestions([
    { id: "start", question: "启动电平？", default: "1", options: ["1", "0"] },
    { id: "stop", question: "停止电平？", required: true },
    { id: "extra", question: "补充？", required: false },
  ]);
  assert.deepEqual(clarificationSubmission(questions, {}), { answers: {}, complete: false });
  assert.equal(clarificationSubmission(questions, { start: "1", stop: "  " }).complete, false);
  const draft = { start: "1", stop: " 0 ", extra: "", stale: "another job" };
  assert.deepEqual(clarificationSubmission(questions, draft), { answers: { start: "1", stop: "0" }, complete: true });
  assert.equal(draft.stop, " 0 ");
  assert.equal(clarificationSubmission([], draft).complete, false);
  assert.deepEqual(clarificationQuestions(null), []);
});

test("Direct renders a separate answer box for each question, with editable suggested choices", () => {
  const path = new URL("../src/features/DirectClarification.tsx", import.meta.url);
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  new Function("require", "module", "exports", code)(require, module, module.exports);
  const questions = clarificationQuestions([
    { id: "delay", question: "延时几秒？", options: ["1秒", "2秒"], default: "2秒" },
    { id: "stop", question: "停止后如何处理？" },
  ]);
  const markup = renderToStaticMarkup(createElement(module.exports.DirectClarification, {
    questions, answers: { delay: "1秒" }, disabled: false, onChange() {}, t: key => key,
  }));
  assert.equal((markup.match(/<fieldset/g) || []).length, 2);
  assert.equal((markup.match(/<textarea/g) || []).length, 2);
  assert.match(markup, /aria-label="延时几秒？"/);
  assert.match(markup, /aria-label="停止后如何处理？"/);
  assert.match(markup, /aria-pressed="true"[^>]*>1秒/);
  assert.match(markup, /aria-pressed="false"[^>]*>2秒/);
  assert.doesNotMatch(markup, /undefined/);
});
