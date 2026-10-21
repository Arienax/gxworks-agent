import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { generationReview } from "../src/features/generationReview.ts";
import { loadJapanese, translate } from "../src/i18n.ts";
import { clarificationQuestions } from "../src/features/clarificationAnswers.ts";

function resultComponent(reactOverrides = {}) {
  const path = new URL("../src/features/GenerationResult.tsx", import.meta.url);
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  new Function("require", "module", "exports", code)(name => {
    if (name === "react") return { ...require(name), ...reactOverrides };
    if (name === "../api/client") return { api: () => { throw Error("Unexpected request"); }, freshGxCsvUrl: () => "/export" };
    if (name === "../components/ui") return { Button: props => createElement("button", props) };
    if (name === "./CapabilityReview") return { CapabilityReview: () => null };
    if (name === "./DirectClarification") return { DirectClarification: () => createElement("form", {}, "历史澄清") };
    if (name === "./clarificationAnswers") return { clarificationQuestions };
    return require(name);
  }, module, module.exports);
  return module.exports;
}

test("Direct program actions precede collapsible generator notes with no required form", () => {
  const { GenerationResult } = resultComponent();
  const markup = renderToStaticMarkup(createElement(GenerationResult, {
    result: { id: "job", projectId: "project", versionId: "v0001", needsInput: false,
      metadata: { generator_notes: { source: "generator", decisions: ["已采用内部状态"], open_items: ["需要调整时可以主动修改"] } } },
    busy: false, onOpen() {}, onRetry() {}, onRepair() {}, onSpec() {}, t: key => key,
  }));
  const ordered = ["程序已保存", "查看程序", "重新导出 GX Works2 CSV", "实现说明", "可选调整"];
  let previous = -1;
  for (const text of ordered) { const index = markup.indexOf(text); assert.ok(index > previous, text); previous = index; }
  assert.match(markup, /<details><summary>实现说明<\/summary>/);
  assert.match(markup, /<details><summary>可选调整<\/summary>/);
  assert.doesNotMatch(markup, /<form|<input|disabled|<details open/);
});

test("only completed historical needs_input jobs expose clarification; failure ends loading", () => {
  const { useGenerationResult, GenerationResult } = resultComponent({
    useState: () => [{ id: "job", value: { version_id: "v1", generation: {
      generator_notes: { source: "generator", decisions: ["需要采用内部状态"], open_items: ["建议调整"] },
    } } }, () => {}], useEffect: () => {},
  });
  const job = { id: "job", kind: "direct_generation", project_id: "project", status: "completed" };
  const result = useGenerationResult(job, 0);
  assert.equal(result.needsInput, false); assert.equal(result.loading, false); assert.equal(result.versionId, "v1");
  const failed = useGenerationResult({ ...job, status: "failed", result: null }, 0);
  assert.equal(failed.loading, false); assert.equal(failed.needsInput, false); assert.equal(failed.id, "");
  const old = useGenerationResult({ ...job, result: { status: "needs_input" } }, 0);
  assert.equal(old.needsInput, true);
  const markup = renderToStaticMarkup(createElement(GenerationResult, {
    result: { ...old, questions: [{ id: "level", question: "X0电平？" }] }, busy: false,
    clarification: { answers: {}, onChange() {}, disabled: false }, t: key => key,
  }));
  assert.match(markup, /<form>历史澄清<\/form>/);
});

test("generation repair button follows the server capability", () => {
  const path = new URL("../src/features/JobFailure.tsx", import.meta.url);
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} }, require = createRequire(path);
  new Function("require", "module", "exports", code)(
    name => name === "../components/ui" ? { Button: props => createElement("button", props) } : require(name), module, module.exports);
  for (const kind of ["generation", "direct_generation"]) for (const repairable of [true, false, undefined]) {
    const markup = renderToStaticMarkup(createElement(module.exports.JobFailure, {
      job: { kind, error_code: "generation_validation_failed", error_details: {
        repairable, violations: [{ path: "content.r.1.h", reason: "invalid_ladder_structure" }],
      } }, t: key => key, onRepair: () => {},
    }));
    assert.equal(/<button[^>]*>让 AI 修复<\/button>/.test(markup), repairable === true);
    assert.match(markup, /content.r.1.h/);
  }
});

test("composition displays Core delivery and gaps without manufacturing confirmation", () => {
  const unit = { id: "equipment:a", value: { label: "设备A", confirmation: "candidate" } };
  const view = generationReview({ generation_handoff: { capability_discovery: {
    composition_units: [unit], included_composition_units: [unit.id],
    omitted_composition_units: [{ id: "relation:x", reason: "remaining_evidence_budget" }],
    composition: { gaps: [{ reason: "binding_identity_conflict" }] },
  } } });
  assert.deepEqual(view.compositionUnits, [unit]);
  assert.equal(view.compositionUnits[0].value.confirmation, "candidate");
  assert.equal(view.omittedComposition.length, 1);
  assert.equal(view.compositionGaps[0].reason, "binding_identity_conflict");
  assert.equal(view.behaviorVerified, false);
});

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

const nativePlanPath = new URL("../src/features/NativeEngineeringPlan.tsx", import.meta.url);
const nativePlanCode = ts.transpileModule(readFileSync(nativePlanPath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const nativePlanModule = { exports: {} };
new Function("require", "module", "exports", nativePlanCode)(
  createRequire(nativePlanPath), nativePlanModule, nativePlanModule.exports);
const renderNativePlan = (commands, locale = "zh-CN") => renderToStaticMarkup(createElement(
  nativePlanModule.exports.NativeEngineeringPlan,
  { value: { schema_version: 1, commands }, t: text => translate(locale, text) },
));

test("native ST Program creation shows the exact body, declarations and explicit existing task", () => {
  const markup = renderNativePlan([{
    operation: "create_st_program", source_name: "计算输出_ST", resource: "MAIN", task: "Task_01",
    scope_mode: "existing_task", declarations: [
      { name: "Values", data_type: "ARRAY[0..2] OF INT", class_name: "VAR", initial_value: "[3(0)]" },
      { name: "Ready", data_type: "BOOL", class_name: "VAR", initial_value: "FALSE",
        device: "M100", iec_address: "%MX100", comment: "就绪 < 检查" },
      { name: "Gain", data_type: "INT", class_name: "VAR_CONSTANT", initial_value: "0" },
    ], st_text: "IF Values[0] < 2 THEN\n  Result := 0;\nEND_IF;",
  }]);
  assert.match(markup, /<strong>建立 ST 程序<\/strong>/);
  assert.match(markup, /计算输出_ST/);
  assert.match(markup, /加入现有任务/);
  assert.match(markup, /MAIN \/ Task_01/);
  assert.match(markup, /<code>Values<\/code>: ARRAY\[0\.\.2\] OF INT · VAR = \[3\(0\)\]/);
  assert.match(markup, /<code>Ready<\/code>: BOOL · VAR · M100 · %MX100 = FALSE · 就绪 &lt; 检查/);
  assert.match(markup, /<code>Gain<\/code>: INT · VAR_CONSTANT = 0/);
  assert.ok(markup.includes("<pre><code>IF Values[0] &lt; 2 THEN\n  Result := 0;\nEND_IF;</code></pre>"));
  assert.doesNotMatch(markup, /功能块|create_st_program|existing_task|undefined|\[object Object\]/);
});

test("native ST Program body-only edits remain distinct from FB creation and task append", () => {
  const markup = renderNativePlan([
    { operation: "edit_st_program", source_name: "Existing_ST", st_text: "Result := Result + 1;\n" },
    { operation: "create_st_fb", source_name: "ScaleBlock", declarations: [], st_text: "Output := Input;" },
  ]);
  assert.match(markup, /<strong>修改 ST 程序<\/strong><span> · Existing_ST<\/span>/);
  assert.ok(markup.includes("<pre><code>Result := Result + 1;\n</code></pre>"));
  assert.match(markup, /<strong>建立 ST 功能块<\/strong><span> · ScaleBlock<\/span>/);
  assert.doesNotMatch(markup, /加入现有任务|undefined|existing_task|edit_st_program/);
});

test("native diagram review keeps port and wire endpoints, via points and destructive actions readable", () => {
  const markup = renderNativePlan([
    { operation: "create_fbd_call", caller: "POU_01", definition: "ScaleBlock", instance: "ScaleInst",
      arguments: { Input: "Command", Output: "Result" } },
    { operation: "edit_fbd_graph", caller: "POU_01", actions: [
      { action: "rebind_terminal", node: "n100", symbol: "NewInput" },
      { action: "delete_wire", wire: "w200" },
      { action: "move_node", node: "n300", x: 12, y: 8 },
      { action: "add_wire", id: "reroute", from: { node: "n100", port: "OUT" },
        to: { node: "n300", port: "Input" }, via: [[10, 4], [10, 8]] },
      { action: "add_wire", id: "tap", from: { wire: "reroute:1", point: [10, 6] },
        to: { node: "n400", port: "IN" }, via: [] },
      { action: "delete_terminal", node: "n500" },
    ] },
  ]);
  assert.match(markup, /建立 FBD 调用和连接/);
  assert.match(markup, /ScaleBlock · ScaleInst/);
  assert.match(markup, /<code>Input<\/code> → <code>Command<\/code>/);
  assert.match(markup, /<code>Output<\/code> → <code>Result<\/code>/);
  assert.match(markup, /<strong>修改图形连接<\/strong><p>POU_01<\/p>/);
  const descriptions = [
    "更新端子绑定 n100 → NewInput", "删除连接线 w200", "移动图形对象 n300 → (12, 8)",
    "连接端口 n100.OUT → n300.Input · 经过 (10, 4) → (10, 8)",
    "连接端口 reroute:1 (10, 6) → n400.IN", "删除端子 n500",
  ];
  let previous = -1;
  for (const description of descriptions) {
    const position = markup.indexOf(description);
    assert.ok(position > previous, `missing or reordered diagram action: ${description}`);
    previous = position;
  }
  assert.doesNotMatch(markup, /edit_fbd_graph|add_wire|delete_wire|move_node|undefined|\[object Object\]/);
});

for (const [locale, labels] of [
  ["en", ["Create an ST program", "Edit an ST program", "Add to an existing task", "Connect ports", "Via", "Move diagram object", "Delete wire"]],
  ["ja", ["STプログラムを作成", "STプログラムを編集", "既存のタスクに追加", "ポートを接続", "経由", "図形を移動", "接続線を削除"]],
]) {
  test(`native Program and graph previews use ${locale} labels while retaining source identities`, async () => {
    // Japanese labels load on demand; the workbench requests the table on locale change.
    if (locale === "ja") await loadJapanese();
    const markup = renderNativePlan([
      { operation: "create_st_program", source_name: "计算输出_ST", resource: "MAIN", task: "Task_01",
        scope_mode: "existing_task", declarations: [], st_text: "Result := 1;" },
      { operation: "edit_st_program", source_name: "Existing_ST", st_text: "Result := 2;" },
      { operation: "edit_fbd_graph", caller: "POU_01", actions: [
        { action: "add_wire", id: "route", from: { node: "n10", port: "OUT" },
          to: { node: "n20", port: "IN" }, via: [[4, 2]] },
        { action: "move_node", node: "n20", x: 8, y: 4 },
        { action: "delete_wire", wire: "w30" },
      ] },
    ], locale);
    for (const label of labels) assert.ok(markup.includes(label), `missing ${locale} translation: ${label}`);
    for (const identity of ["计算输出_ST", "Existing_ST", "MAIN / Task_01", "n10.OUT → n20.IN", "(4, 2)", "n20 → (8, 4)", "w30"])
      assert.ok(markup.includes(identity), `source identity changed: ${identity}`);
    assert.doesNotMatch(markup, /建立 ST 程序|修改 ST 程序|加入现有任务|连接端口|经过|移动图形对象|删除连接线|existing_task/);
  });
}
