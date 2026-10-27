// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { generationReview } from "./generationReview";

const labels: Record<string, string> = {
  capability_calls_and_repeated_writes: "调用与重复写入位置",
  shared_conditions_and_contiguous_groups: "公共条件与连续分支归并",
  condition_read_stability: "条件读值稳定性",
  output_effect_footprints: "输出写入范围",
  replacement_equivalence_and_process_behavior: "替换等价性与工艺行为",
  object_call_inventory: "工程对象调用",
  connection_inventory: "连接位置与端点记录",
  explicit_connection_endpoints: "显式连接端点引用",
  graph_replacement_equivalence_and_process_behavior: "图形替换等价性与工艺行为",
  lexically_identifiable_calls: "源码中可识别的调用",
  line_initial_literal_assignments: "行首字面赋值与重复位置",
  ST_types_control_flow_and_equivalence: "ST类型、控制流程与等价性",
  no_scoped_manual_match: "未找到适用范围内的手册证据",
  not_available_in_registry_scope: "当前型号目录无可用形式",
  not_in_current_language_catalog: "当前语言工程目录无此调用",
  no_complete_fact_unit_within_budget: "预算内无完整事实单元",
  no_complete_fact_unit: "未找到完整事实单元",
  discovery_unavailable: "本地能力发现暂不可用",
  selection_policy_budget_omitted: "选择要求因预算被省略",
  edge_evaluation_site: "边沿条件保留原求值位置",
  volatile_or_unverified_read: "读值稳定性尚未验证",
  stateful_or_opaque_parallel: "并联块含状态或未知条件",
  unsupported_condition: "条件形式尚未覆盖",
  unsupported_comparison: "比较形式尚未覆盖",
  unsupported_output: "输出形式尚未覆盖",
  control_flow_or_logic_stack: "保留流程与逻辑栈边界",
  implicit_or_external_effect: "隐式状态或外部动作尚未覆盖",
  instruction_fact_gap: "指令写入范围缺少适用事实",
  unresolved_operand_effect: "操作数写入范围尚未解析",
  construction_requirement_not_confirmed: "引用的行为要求尚未确认",
  dependency_reference_missing: "依赖引用不存在",
  multiple_state_writers_require_execution_review: "多个功能写入同一状态，执行顺序尚需验证",
  binding_identity_missing: "缺少稳定的信号身份",
  binding_identity_conflict: "信号身份对应的地址有冲突",
  conservative_write_extent: "写入范围采用设备区域的保守估计",
};

export function CapabilityReview({ metadata, t }: { metadata: unknown; t: (key: string) => string }) {
  const view = generationReview(metadata);
  const locationText = (value: unknown) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return "";
    const row = value as Record<string, unknown>;
    if (row.rung_id !== undefined) return `${t("梯级")} ${row.rung_id}${row.branch_id !== undefined ? ` / ${t("支路")} ${row.branch_id}` : ""}`;
    if (row.node_id !== undefined) {
      const wires = Array.isArray(row.connections) ? row.connections.map(item => {
        if (!item || typeof item !== "object") return "";
        const connection = item as Record<string, unknown>;
        return typeof connection.connection_index === "number" ? String(connection.connection_index + 1) : "";
      }).filter(Boolean) : [];
      return `${t("对象")} ${row.node_id}${wires.length ? ` / ${t("连接")} ${wires.join(", ")}` : ""}`;
    }
    if (row.line !== undefined) return `${t("行")} ${row.line}, ${t("列")} ${row.column}`;
    return "";
  };
  if (!view.available) return null;
  return <details className="capability-review">
    <summary>{t("程序检查详情")}</summary>
    <p>{t("参考指令")}: {view.candidates.map(row => String(row.name)).join(", ") || t("无")}</p>
    <p>{t("程序实际使用")}: {view.used.map(row => `${row.name} (${row.count})`).join(", ") || t("未记录")}</p>
    {view.findings.map((row, i) => <p key={i}>{String(row.message || "")}
      {Array.isArray(row.locations) && <span> ({row.locations.map(locationText).filter(Boolean).join("; ")})</span>}
    </p>)}
    <p>{t("检查覆盖")}: {view.coverage.filter(row => row.status === "checked").length} {t("已检查")}, {view.coverage.filter(row => row.status !== "checked").length} {t("未验证")}</p>
    <ul>{view.coverage.map((row, i) => <li key={i}>{t(labels[String(row.check)] || String(row.check))}: {t(row.status === "checked" ? "已检查" : "未验证")}</li>)}</ul>
    {!!view.conditionBarriers.length && <ul>{view.conditionBarriers.map((row, i) => <li key={i}>
      {t(labels[String(row.reason)] || String(row.reason))}
      {Array.isArray(row.locations) && ` (${row.locations.map(locationText).filter(Boolean).join("; ")})`}
    </li>)}</ul>}
    {!view.behaviorVerified && <p>{t("运行行为")}: {t("未验证")}</p>}
    {!!view.compositionUnits.length && <div>
      <p>{t("功能组合参考")}</p>
      <ul>{view.compositionUnits.map((unit, i) => {
        const value = unit.value && typeof unit.value === "object" ? unit.value as Record<string, unknown> : {};
        return <li key={i}>{String(value.label || value.purpose || unit.id)}
          {typeof value.confirmation === "string" && <span> ({t(value.confirmation === "candidate" ? "候选建议" : value.confirmation)})</span>}
          {Array.isArray(value.preconditions) && <span>: {value.preconditions.map(String).join("; ")}</span>}
          {Array.isArray(value.sources) && value.sources.map((source, j) => {
            if (!source || typeof source !== "object") return null;
            const s = source as Record<string, unknown>;
            return <span key={j}> [{String(s.manual_id || s.identity || s.id || "")}]</span>;
          })}
        </li>;
      })}</ul>
      <p>{t("候选建议保留原确认要求，工程与运行验证分别记录。")}</p>
    </div>}
    {!!view.omittedComposition.length && <p>{t("部分组合参考因上下文预算未送达")}: {view.omittedComposition.length}</p>}
    {!!view.compositionGaps.length && <ul>{view.compositionGaps.map((row, i) => <li key={i}>
      {String(row.unit_id || row.binding_id || row.device || "")}: {t(labels[String(row.reason)] || String(row.reason || ""))}
    </li>)}</ul>}
    {!!view.candidates.length && <ul>{view.candidates.map(row => <li key={String(row.id)}>
      {String(row.name)}: {String(row.purpose || "")}
      {Array.isArray(row.unknown) && !!row.unknown.length && <span> ({t("未验证")}: {row.unknown.map(String).join(", ")})</span>}
      {Array.isArray(row.sources) && row.sources.map((source, i) => {
        if (!source || typeof source !== "object" || Array.isArray(source)) return null;
        const s = source as Record<string, unknown>;
        const pages = s.pdf_page || (Array.isArray(s.pdf_pages) ? s.pdf_pages.join(", ") : "");
        return <span key={i}> [{String(s.manual_id || s.identity || s.id || "")}{pages ? `, PDF ${pages}` : ""}]</span>;
      })}
    </li>)}</ul>}
    {!!view.gaps.length && <ul>{view.gaps.map((row, i) => <li key={i}>{String(row.target || row.function || "")}: {t(labels[String(row.reason)] || String(row.reason || ""))}</li>)}</ul>}
  </details>;
}
