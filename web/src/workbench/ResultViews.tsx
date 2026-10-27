// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { Json } from "../api/client";
const stringify = (value: unknown) => JSON.stringify(value, null, 2);

export function AcceptedMessage({
  kind,
  text,
}: {
  kind: string;
  text: string;
  t: (key: string) => string;
}) {
  let structured: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) structured = parsed;
  } catch {
    /* Accepted prose is rendered verbatim. */
  }
  if (kind === "analysis" && structured)
    return (
      <div className="accepted-content">
        <p>{String(structured.summary || "")}</p>
      </div>
    );
  return <pre className="accepted-content">{text}</pre>;
}

export function DiffView({ value, t }: { value: unknown; t: (key: string) => string }) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return <DataView value={value} />;
  const diff = value as Record<string, Json>;
  const changes = Array.isArray(diff.changes) ? (diff.changes as Record<string, Json>[]) : [];
  const comments = Array.isArray(diff.comment_changes) && diff.comment_changes.length
    ? (diff.comment_changes as Record<string, Json>[])
    : Array.isArray(diff.device_comment_changes)
      ? (diff.device_comment_changes as Record<string, Json>[]) : [];
  const commentTables = comments.some((change) => typeof change.table === "string");
  const sources = Array.isArray(diff.source_changes)
    ? diff.source_changes as Record<string, Json>[] : [];
  const commentText = (value: Json | undefined) => value === "" ? t("空注释") : String(value ?? "—");
  return (
    <div className="candidate-diff">
      <h3>{t("差异数据")}</h3>
      {sources.length > 0 && (
        <section>
          <h4>{t("程序修改")}</h4>
          {sources.map((source) => (
            <details key={String(source.source_name)} open={sources.length === 1}>
              <summary>{String(source.source_name)} · {String(source.language).toUpperCase()}</summary>
              <pre>{String(source.unified_diff || "")}</pre>
            </details>
          ))}
        </section>
      )}
      {diff.kind === "fbd" ? (
        <>
          <p>
            {t("对象")} {String(diff.before_object_count)} → {String(diff.after_object_count)} ·{" "}
            {t("导线")} {String(diff.before_wire_count)} → {String(diff.after_wire_count)}
          </p>
          <p>{t(diff.declarations_changed ? "声明表有变化" : "声明表无变化")}</p>
          {diff.has_changes === false ? (
            <p>{t("程序内容无变化")}</p>
          ) : (
            <details>
              <summary>{t("查看详细对象差异")}</summary>
              <pre>{String(diff.unified_diff || "")}</pre>
            </details>
          )}
        </>
      ) : (
        <>
          {diff.has_changes === false ? (
            <p>{t("程序内容无变化")}</p>
          ) : typeof diff.unified_diff === "string" && !changes.length ? (
            <pre>{diff.unified_diff}</pre>
          ) : (
            <>
              <p>
                {t("网络")} {String(diff.before_network_count ?? 0)} →{" "}
                {String(diff.after_network_count ?? 0)}
              </p>
              {changes.map((change, i) => (
                <details key={i} open={changes.length === 1}>
                  <summary>
                    <span className="mono">
                      {String(change.marker)} {String(change.network)}
                    </span>{" "}
                    {String(change.comment || "")}
                  </summary>
                  <div className="diff-columns">
                    <div>
                      <strong>{t("变更前")}</strong>
                      <DataView value={change.before} />
                    </div>
                    <div>
                      <strong>{t("变更后")}</strong>
                      <DataView value={change.after} />
                    </div>
                  </div>
                </details>
              ))}
              {typeof diff.unified_diff === "string" && <pre>{diff.unified_diff}</pre>}
              {diff.network_order_changed === true && (
                <DataView
                  value={{
                    before: diff.before_network_order,
                    after: diff.after_network_order,
                  }}
                />
              )}
              {!!diff.property_changes && <DataView value={diff.property_changes} />}
            </>
          )}
        </>
      )}
      {comments.length > 0 && (
        <details open>
          <summary>{t("软元件注释变化")}</summary>
          <table>
            <thead><tr>
              {commentTables && <th>{t("注释表")}</th>}
              <th>{t("地址")}</th><th>{t("变更前")}</th><th>{t("变更后")}</th>
            </tr></thead>
            <tbody>{comments.map((change, i) => (
              <tr key={i}>
                {commentTables && <td>{String(change.table ?? "—")}</td>}
                <td className="mono">{String(change.address ?? "—")}</td>
                {change.reason ? <td colSpan={2}>{String(change.reason)}</td> : <>
                  <td>{commentText(change.before)}</td><td>{commentText(change.after)}</td>
                </>}
              </tr>
            ))}</tbody>
          </table>
        </details>
      )}
      {!!diff.declaration_changes && <details>
        <summary>{t("声明表有变化")}</summary>
        <DataView value={diff.declaration_changes} />
      </details>}
    </div>
  );
}

export function DataView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
    return <span className="data-value">{String(value)}</span>;
  if (Array.isArray(value))
    return (
      <div className="data-list">
        {value.length ? (
          value.map((v, i) => (
            <div key={i}>
              <DataView value={v} />
            </div>
          ))
        ) : (
          <span className="muted">—</span>
        )}
      </div>
    );
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 20) return <pre className="data-json">{stringify(value)}</pre>;
  return (
    <dl className="data-fields">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{fieldLabel(key)}</dt>
          <dd>
            <DataView value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function fieldLabel(key: string) {
  const labels: Record<string, [string, string, string]> = {
    summary: ["摘要", "Summary", "概要"],
    status: ["状态", "Status", "状態"],
    report_type: ["检查类型", "Review type", "検査種類"],
    report_id: ["报告编号", "Report ID", "レポートID"],
    base_version_id: ["基础版本", "Base version", "基準バージョン"],
    version_id: ["版本", "Version", "バージョン"],
    findings: ["检查发现", "Findings", "検出事項"],
    messages: ["校验说明", "Validation notes", "検証結果"],
    created_at: ["创建时间", "Created", "作成日時"],
    updated_at: ["更新时间", "Updated", "更新日時"],
    schema_version: ["格式版本", "Format version", "形式バージョン"],
    added: ["新增网络", "Added networks", "追加ネットワーク"],
    deleted: ["删除网络", "Removed networks", "削除ネットワーク"],
    modified: ["修改网络", "Changed networks", "変更ネットワーク"],
    changes: ["变更内容", "Changes", "変更内容"],
    before: ["变更前", "Before", "変更前"],
    after: ["变更后", "After", "変更後"],
    before_network_count: ["原网络数", "Original networks", "変更前ネットワーク数"],
    after_network_count: ["候选网络数", "Candidate networks", "候補ネットワーク数"],
    device_comments_changed: ["软元件注释变化", "Device comments changed", "デバイスコメント変更"],
    severity: ["严重程度", "Severity", "重要度"],
    description: ["说明", "Description", "説明"],
    network: ["网络", "Network", "ネットワーク"],
    instruction_count: ["指令数", "Instructions", "命令数"],
    comment: ["注释", "Comment", "コメント"],
    passed: ["验证通过", "Passed", "検証合格"],
    valid: ["校验有效", "Valid", "有効"],
  };
  const index =
    document.documentElement.lang === "en" ? 1 : document.documentElement.lang === "ja" ? 2 : 0;
  return labels[key]?.[index] || key.replaceAll("_", " ");
}
