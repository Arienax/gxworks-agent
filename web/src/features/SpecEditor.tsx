import { useEffect, useId, useRef, useState } from "react";
import { Plus, Check } from "lucide-react";
import { api } from "../api/client";
import type { Spec, Json } from "../api/client";
import { Button } from "../components/ui";
import { ChoiceInput } from "../components/ChoiceInput";

export function SpecEditor({
  value,
  t,
  disabled,
  onSave,
  onChange,
  issues,
}: {
  value: Spec | null;
  t: (key: string) => string;
  disabled: boolean;
  onSave: (spec: Spec) => void;
  onChange: (spec: Spec) => void;
  issues: { path: string; message: string }[];
}) {
  const [draft, setDraft] = useState<Spec | null>(value);
  const [raw, setRaw] = useState("");
  const [parseError, setParseError] = useState(false);
  const [ioPending, setIOPending] = useState(false), [ioError, setIOError] = useState("");
  const [ioEditing, setIOEditing] = useState<number | null>(null);
  const current = useRef(value), ioRequest = useRef(0);
  const approachGroup = useId();
  const parseSpec = (text: string): Spec => {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error("Invalid specification");
    return parsed;
  };
  useEffect(() => {
    if (current.current !== value) { current.current = value; ioRequest.current++; setIOPending(false); setIOError(""); }
    setDraft(value);
    setRaw(JSON.stringify(value, null, 2));
    setParseError(false);
  }, [value]);
  if (!draft)
    return <div className="panel-empty">{t("先分析需求以建立规格草稿。")}</div>;
  function patch(next: Spec) {
    current.current = next;
    setDraft(next);
    onChange(next);
    setRaw(JSON.stringify(next, null, 2));
    setParseError(false);
  }
  async function editIORow(index: number | null, address?: string) {
    const source = current.current;
    if (!source) return;
    const requestId = ++ioRequest.current;
    const sourceRow = index === null ? undefined : source.io_table?.[index];
    if (index !== null && sourceRow) patch({ ...source, io_table: source.io_table?.map((row, i) => i === index ? { ...row, address: address ?? "" } : row) });
    setIOEditing(index); setIOPending(true); setIOError("");
    try {
      const row = await api<Record<string, Json>>("/spec/io-row", "POST", { row: sourceRow || null, address: address ?? null });
      if (ioRequest.current !== requestId || !current.current) return;
      const latest = current.current;
      patch({ ...latest, io_table: index === null ? [...(latest.io_table || []), row] : (latest.io_table || []).map((item, i) => i === index ? { ...item, address: row.address, kind: row.kind } : item) });
    } catch (error) { if (ioRequest.current === requestId) setIOError(error instanceof Error ? error.message : String(error)); }
    finally { if (ioRequest.current === requestId) setIOPending(false); }
  }
  const approachId = (a?: Record<string, Json>) => String(a?.approach_id || a?.id || "");
  const rows = draft.io_table || [];
  const parameters = draft.parameters || [];
  return (
    <fieldset className="spec-editor" disabled={disabled}>
      <div className="spec-intro">
        <h3>{t("确认控制需求")}</h3>
        <p>{t("选择方案和实际接线，确认后再生成候选程序。")}</p>
      </div>
      <label>
        {t("需求摘要")}
        <textarea
          value={draft.summary || ""}
          onChange={(e) => patch({ ...draft, summary: e.target.value })}
        />
      </label>
      {(draft.approaches || []).length > 0 && (
        <fieldset className="approach-choices">
          <legend>{t("编程方案")}</legend>
          {draft.approaches?.map((a, i) => (
            <label key={approachId(a) || i} className={approachId(draft.selected_approach) === approachId(a) ? "selected" : ""}>
              <input type="radio" name={approachGroup} value={approachId(a)}
                checked={approachId(draft.selected_approach) === approachId(a)}
                onChange={() => patch({ ...draft, selected_approach: a })} />
              <span><strong>{String(a.name || a.title || approachId(a) || i + 1)}</strong>
                {!!a.description && <small>{String(a.description)}</small>}
                {!!a.generation_guide && <small>{t("方案实现说明（不是额外硬约束）")}：{String(a.generation_guide)}</small>}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {!!draft.selected_approach?.implementation_preferences && <details>
        <summary>{t("所选方案的实现建议（不作为硬约束）")}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.selected_approach.implementation_preferences, null, 2)}</pre>
      </details>}
      {!!draft.engineering_context && <details>
        <summary>{t("原始需求与检索来源记录")}</summary>
        <p>{t("记录用于核对来源，不代表方案已验证；原始需求、方案建议和硬契约分别保留。")}</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.engineering_context, null, 2)}</pre>
      </details>}
      {parameters.length > 0 && <div className="section-label">{t("确认问题")}</div>}
      {parameters.map((p, i) => (
        <ChoiceInput key={String(p.id || i)} t={t}
          label={String(p.question || p.label || p.name || p.id)}
          required={!!p.required && !p.required_when}
          hint={p.suggested_default ? `${t("建议值（待确认）")}：${String(p.suggested_default)}` : p.required_when ? t("根据相关选项确认") : undefined}
          error={issues.filter((issue) => issue.path.startsWith(`$.parameters[${i}]`)).map((issue) => issue.message).join("；")}
          value={String(p.value ?? "")}
          options={Array.isArray(p.options) ? p.options.filter((v): v is string => typeof v === "string" && !!v.trim()) : []}
          onChange={(value) => patch({ ...draft, parameters: parameters.map((v, j) => i === j ? { ...v, value, source: "user" } : v) })}
        />
      ))}
      <div className="section-label">
        {t("I/O 分配")}
        <Button
          variant="ghost"
          aria-label={t("新增 I/O")}
          disabled={ioPending}
          onClick={() => void editIORow(null)}
        >
          <Plus size={14} />
        </Button>
      </div>
      {rows.map((row, i) => (
        <div className="io-row" key={i}>
          <input
            aria-label={`I/O ${i + 1}`}
            className="mono"
            disabled={ioPending && ioEditing !== i}
            value={String(row.address || "")}
            placeholder="X0"
            onChange={(e) => void editIORow(i, e.target.value)}
          />
          <input
            aria-label={`${t("动作")} ${i + 1}`}
            value={String(row.purpose || row.description || row.label || "")}
            onChange={(e) =>
              patch({
                ...draft,
                io_table: rows.map((r, j) =>
                  i === j
                    ? {
                        ...r,
                        label: e.target.value,
                        purpose: e.target.value,
                        description: e.target.value,
                      }
                    : r,
                ),
              })
            }
          />
          <button
            className="text-button"
            disabled={ioPending}
            aria-label={t("清除")}
            onClick={() =>
              (ioRequest.current++, setIOPending(false), patch({ ...draft, io_table: rows.filter((_, j) => i !== j) }))
            }
          >
            ×
          </button>
        </div>
      ))}
      <label>
        {t("备注")}
        <textarea
          value={draft.user_notes || ""}
          onChange={(e) => patch({ ...draft, user_notes: e.target.value })}
        />
      </label>
      <details>
        <summary>{t("高级规格数据")}</summary>
        <textarea
          className="mono raw-editor"
          value={raw}
          onChange={(e) => {
            setRaw(e.target.value);
            try {
              parseSpec(e.target.value);
              setParseError(false);
            } catch {
              setParseError(true);
            }
          }}
          onBlur={() => {
            try {
              ioRequest.current++; setIOPending(false); setIOError(""); patch(parseSpec(raw));
            } catch {
              setParseError(true);
            }
          }}
        />
      </details>
      {issues.filter((issue) => !issue.path.startsWith("$.parameters[")).map((issue, i) => <p className="error-text" role="alert" key={i}>{issue.message}</p>)}
      {ioError && <p role="alert" className="error-text">{ioError}</p>}
      {parseError && (
        <p role="alert" className="error-text">
          {t("规格数据不是有效 JSON 对象。")}
        </p>
      )}
      <Button
        variant="primary"
        disabled={disabled || parseError || ioPending || !!ioError}
        onClick={() => {
          try {
            onSave(parseSpec(raw));
          } catch {
            setParseError(true);
          }
        }}
      >
        <Check size={15} />
        {t("确认规格")}
      </Button>
    </fieldset>
  );
}
