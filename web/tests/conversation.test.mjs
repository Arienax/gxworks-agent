import assert from "node:assert/strict";
import test from "node:test";
import { conversationRoute } from "../src/features/conversationRouting.ts";

const initial = { task: "create", workflow: "direct", editAction: "edit", targetMode: "ladder",
  hasVersions: false, hasSpec: false, reviewRequested: false, continuingDirect: false };
const route = changes => conversationRoute({ ...initial, ...changes });

test("creation separates direct generation, analysis and confirmed generation", () => {
  assert.equal(route({}).kind, "direct_generation");
  assert.equal(route({ workflow: "review" }).kind, "analysis");
  const confirmed = route({ workflow: "review", hasSpec: true });
  assert.equal(confirmed.kind, "generation");
  assert.equal(confirmed.submitLabel, "按规格生成");
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

test("full regeneration first obtains a specification and explicitly selects regeneration", () => {
  const editing = { task: "edit", editAction: "regenerate", hasVersions: true };
  assert.equal(route(editing).kind, "analysis");
  const confirmed = route({ ...editing, hasSpec: true });
  assert.equal(confirmed.kind, "generation");
  assert.equal(confirmed.generationAction, "regenerate");
  assert.equal(confirmed.requiresText, false);
  assert.equal(confirmed.submitLabel, "按规格重新生成");
  assert.equal(route({ ...editing, hasSpec: true, reviewRequested: true }).kind, "analysis");
});

test("clarification wording belongs only to active Direct creation", () => {
  assert.equal(route({ continuingDirect: true }).submitLabel, "补充并继续");
  assert.equal(route({ continuingDirect: true, task: "edit", hasVersions: true }).submitLabel, "修改当前程序");
  assert.equal(route({ continuingDirect: true, workflow: "review", hasSpec: true }).submitLabel, "按规格生成");
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
