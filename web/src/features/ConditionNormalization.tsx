type Entry = {message?: unknown; network_ids?: unknown};
const entries = (value: unknown): Entry[] => Array.isArray(value)
  ? value.filter((item): item is Entry => !!item && typeof item === "object") : [];

export function ConditionNormalization({value, t}: {value: unknown; t: (key: string) => string}) {
  if (!value || typeof value !== "object") return null;
  const report = value as Record<string, unknown>;
  const changes = entries(report.changes), skipped = entries(report.skipped);
  if (!changes.length && !skipped.length) return null;
  const list = (items: Entry[]) => <ul>{items.map((item, index) => <li key={index}>
    {Array.isArray(item.network_ids) && <span className="mono">{item.network_ids.join(", ")} · </span>}
    {t(String(item.message || ""))}
  </li>)}</ul>;
  return <div className="condition-normalization">
    {!!changes.length && <><strong>{t("条件整理")}</strong>{list(changes)}</>}
    {!!skipped.length && <details><summary>{t("保留原结构的原因")}</summary>{list(skipped)}</details>}
  </div>;
}
