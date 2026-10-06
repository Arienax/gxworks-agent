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
    <summary>{t("能力发现与程序审阅")}</summary>
    <p>{t("已交付候选")}: {view.candidates.map(row => String(row.name)).join(", ") || t("无")}</p>
    <p>{t("程序实际使用")}: {view.used.map(row => `${row.name} (${row.count})`).join(", ") || t("未记录")}</p>
    {view.findings.map((row, i) => <p key={i}>{String(row.message || "")}
      {Array.isArray(row.locations) && <span> ({row.locations.map(locationText).filter(Boolean).join("; ")})</span>}
    </p>)}
    <p className="muted">{t("建议仅供复核，不自动修改程序或证明替换等价。")}</p>
    <p>{t("检查覆盖")}: {view.coverage.filter(row => row.status === "checked").length} {t("已检查")}, {view.coverage.filter(row => row.status !== "checked").length} {t("未验证")}</p>
    <ul>{view.coverage.map((row, i) => <li key={i}>{t(labels[String(row.check)] || String(row.check))}: {t(row.status === "checked" ? "已检查" : "未验证")}</li>)}</ul>
    {!!view.conditionBarriers.length && <ul>{view.conditionBarriers.map((row, i) => <li key={i}>
      {t(labels[String(row.reason)] || String(row.reason))}
      {Array.isArray(row.locations) && ` (${row.locations.map(locationText).filter(Boolean).join("; ")})`}
    </li>)}</ul>}
    {!view.behaviorVerified && <p className="muted">{t("本地审阅不证明工艺行为、原生编译或设备运行正确。")}</p>}
    {(view.omitted > 0 || view.gaps.length > 0) && <p>{t("预算遗漏")}: {view.omitted}; {t("事实缺口")}: {view.gaps.length}</p>}
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
    {typeof view.tokens.net_delta === "number" && <p className="muted">{t("知识上下文估算增量")}: {view.tokens.net_delta}; {t("候选简表")}: {String(view.tokens.candidate_briefs || 0)}; {t("新增事实")}: {String(view.tokens.new_facts || 0)}. {t("本地估算，不是供应商计费用量。")}</p>}
  </details>;
}
