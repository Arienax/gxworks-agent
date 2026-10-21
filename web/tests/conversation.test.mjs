import assert from "node:assert/strict";
import test from "node:test";
import { conversationRoute } from "../src/features/conversationRouting.ts";
import { clarificationQuestions, clarificationSubmission } from "../src/features/clarificationAnswers.ts";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const initial = { task: "create", workflow: "direct", editAction: "edit", targetMode: "ladder",
  hasVersions: false, hasSpec: false, reviewRequested: false, continuingDirect: false };
const route = changes => conversationRoute({ ...initial, ...changes });

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
