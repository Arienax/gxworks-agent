import { ConditionNormalization } from "./ConditionNormalization";
type Fields = Record<string, unknown>;
const fields = (value: unknown): Fields => value && typeof value === "object" && !Array.isArray(value) ? value as Fields : {};
const values = (value: unknown) => Array.isArray(value) ? value.map(String).join(", ") : "";

export function ChangeSummary({summary,t}:{summary:Fields;t:(s:string)=>string}) {
  const impact=fields(summary.impact), scope=fields(summary.change_scope);
  if(!summary.impact)return null;
  return <details className="saved-change-summary">
    <summary>{t("本版变更")}{values(impact.network_ids) ? ` · ${values(impact.network_ids)}` : ""}</summary>
    <p>{t("修改范围")}：{summary.change_scope ? [values(scope.network_ids),values(scope.addresses)].filter(Boolean).join(" · ") : t("整个程序")}</p>
    <p>{t("变更网络")}：{values(impact.network_ids) || "—"}</p>
    <p>{t("涉及地址")}：{values(impact.addresses) || "—"}</p>
    <p>{t("软元件注释变化")}：{values(impact.device_comments) || "—"}</p>
    {!!impact.network_order_changed && <p>{t("网络顺序已变化")}</p>}
    {!!values(impact.metadata_fields) && <p>{t("程序属性变化")}：{values(impact.metadata_fields)}</p>}
    <ConditionNormalization value={summary.normalization} t={t}/>
  </details>;
}
