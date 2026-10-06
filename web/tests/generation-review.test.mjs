import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { generationReview } from "../src/features/generationReview.ts";

test("old metadata remains readable and does not manufacture coverage", () => {
  assert.equal(generationReview({}).available, false);
  assert.equal(generationReview(null).behaviorVerified, false);
  assert.deepEqual(generationReview({}).conditionBarriers, []);
});

test("Core condition barriers preserve locations and remain separate from behavior acceptance", () => {
  const barriers = [{ reason: "instruction_fact_gap", locations: [{ rung_id: 2, branch_id: 1 }] }];
  const view = generationReview({ maintainability_review: { condition_review: { barriers } } });
  assert.deepEqual(view.conditionBarriers, barriers);
  assert.equal(view.behaviorVerified, false);
});

test("review component renders condition limits and rung-only findings without a missing branch label", () => {
  const path = new URL("../src/features/CapabilityReview.tsx", import.meta.url);
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  new Function("require", "module", "exports", code)(
    name => name === "./generationReview" ? { generationReview } : require(name), module, module.exports);
  const markup = renderToStaticMarkup(createElement(module.exports.CapabilityReview, {
    t: key => key, metadata: { maintainability_review: {
      findings: [{ message: "可复核公共条件", locations: [{ rung_id: 2 }] }],
      coverage: [{ check: "output_effect_footprints", status: "unverified" }],
      condition_review: { barriers: [{ reason: "instruction_fact_gap", locations: [{ rung_id: 3, branch_id: 1 }] }] },
    } },
  }));
  assert.match(markup, /输出写入范围/);
  assert.match(markup, /指令写入范围缺少适用事实/);
  assert.match(markup, /梯级 3 \/ 支路 1/);
  assert.doesNotMatch(markup, /undefined|支路<[^>]*>undefined|instruction_fact_gap/);
});

test("only delivered candidates are displayed and missing evidence stays visible", () => {
  const data = { generation_handoff: { capability_discovery: {
    candidates: [{ id: "one", name: "CopyBlock" }, { id: "two", name: "ResetRange" }],
    included_candidates: ["one"], omitted_candidates: ["two"], omitted_evidence: ["missing"],
    gaps: [{ reason: "unverified" }], tokens: { net_delta: 123 },
  } }, maintainability_review: {
    used_capabilities: [{ name: "MOV", count: 3 }], findings: [{ replacement_verified: false }],
    coverage: [{ check: "inventory", status: "checked" }, { check: "behavior", status: "unverified" }],
    behavior_verified: false,
  } };
  const view = generationReview(data);
  assert.deepEqual(view.candidates.map(c => c.name), ["CopyBlock"]);
  assert.equal(view.omitted, 2);
  assert.equal(view.gaps.length, 1);
  assert.equal(view.tokens.net_delta, 123);
  assert.equal(view.behaviorVerified, false);
  assert.equal(view.findings[0].replacement_verified, false);
});
