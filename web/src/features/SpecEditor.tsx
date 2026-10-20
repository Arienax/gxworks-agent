import { useEffect, useId, useRef, useState } from "react";
import { Plus, Check } from "lucide-react";
import { api } from "../api/client";
import type { Spec, Json } from "../api/client";
import { Button } from "../components/ui";
import { ChoiceInput } from "../components/ChoiceInput";

type SpecPresentation = { operation_intents: { effects: string[]; parameters: string[]; enable: string; execution: string; required_state: string[] }[];
  behavior_constraints: { source?: string; accept?: string; enable?: string }[] };

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
  const [presentation, setPresentation] = useState<{ source: Spec; value: SpecPresentation } | null>(null);
  const [presentationError, setPresentationError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setPresentationError("");
    if (draft && ((Array.isArray(draft.operation_intents) && draft.operation_intents.length) || (Array.isArray(draft.behavior_constraints) && draft.behavior_constraints.length))) {
      api<SpecPresentation>("/spec/presentation", "POST", { spec: draft }).then(value => {
        if (!cancelled) setPresentation({ source: draft, value });
      }).catch(error => { if (!cancelled) setPresentationError(error instanceof Error ? error.message : String(error)); });
    }
    return () => { cancelled = true; };
  }, [draft]);
  const description = presentation?.source === draft ? presentation.value : null;
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
    return <div className="panel-empty">{t("请先输入控制需求。")}</div>;
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
  const operationIntents = Array.isArray(draft.operation_intents) ? draft.operation_intents.filter((v): v is Record<string, Json> => !!v && typeof v === "object" && !Array.isArray(v)) : [];
  const confirmedIntentIds = Array.isArray(draft.confirmed_operation_intent_ids) ? draft.confirmed_operation_intent_ids.map(String) : [];
  const behaviors = Array.isArray(draft.behavior_constraints) ? draft.behavior_constraints.filter((v): v is Record<string, Json> => !!v && typeof v === "object" && !Array.isArray(v)) : [];
  const confirmedBehaviorIds = Array.isArray(draft.confirmed_behavior_ids) ? draft.confirmed_behavior_ids.map(String) : [];
  const rawPlan = draft.selected_approach?.construction_plan;
  const plan = rawPlan && typeof rawPlan === "object" && !Array.isArray(rawPlan) ? rawPlan : {};
  const methods: Record<string, string> = { first_scan_isolation: "首次扫描初始化隔离", source_history: "源信号边沿与接受条件分离", scan_union: "同扫描事件合并", priority_snapshot: "按优先级选择一次转换" };
  return (
    <fieldset className="spec-editor" disabled={disabled}>
      <div className="spec-intro">
        <h3>{t("确认控制需求")}</h3>
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
                {!!a.generation_guide && <small>{t("实现建议")}：{String(a.generation_guide)}</small>}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {!!draft.selected_approach?.implementation_preferences && <details>
        <summary>{t("实现建议")}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.selected_approach.implementation_preferences, null, 2)}</pre>
      </details>}
      {!!draft.intent_context && <details>
        <summary>{t("原始需求")}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.intent_context, null, 2)}</pre>
      </details>}
      {presentationError && <p className="error-text" role="alert">{presentationError}</p>}
      {operationIntents.length > 0 && <fieldset>
        <legend>{t("核对指令效果")}</legend>
        {operationIntents.map((intent, index) => <label key={String(intent.id)}>
          <input type="checkbox" checked={intent.status === "confirmed" || confirmedIntentIds.includes(String(intent.id))}
            disabled={intent.status === "confirmed"}
            onChange={(e) => patch({ ...draft, confirmed_operation_intent_ids: e.target.checked
              ? [...confirmedIntentIds, String(intent.id)] : confirmedIntentIds.filter((id) => id !== String(intent.id)) })} />
          <span>{String(intent.label || intent.id)}
            {description?.operation_intents[index]?.effects.map((text, i) => <small key={i}>{text}</small>)}
            {description?.operation_intents[index]?.parameters.map((text, i) => <small key={i}>{text}</small>)}
            <small>{t("使能条件")}：{description?.operation_intents[index]?.enable || t("正在加载…")}</small>
            <small>{t(description?.operation_intents[index]?.execution || "正在加载…")}</small>
            {description?.operation_intents[index]?.required_state.map((text, i) => <small key={i}>{t("必要状态")}：{text}</small>)}
          </span>
        </label>)}
      </fieldset>}
      {behaviors.length > 0 && <fieldset>
        <legend>{t("核对行为关系")}</legend>
        {behaviors.map((relation, index) => <label key={String(relation.id)}>
          <input type="checkbox" checked={relation.status === "confirmed" || confirmedBehaviorIds.includes(String(relation.id))}
            disabled={relation.status === "confirmed"}
            onChange={(e) => patch({ ...draft, confirmed_behavior_ids: e.target.checked
              ? [...confirmedBehaviorIds, String(relation.id)] : confirmedBehaviorIds.filter((id) => id !== String(relation.id)) })} />
          <span>{String(relation.label || relation.id)}
            {relation.source && <small>{t("源信号")}：{description?.behavior_constraints[index]?.source || t("正在加载…")}</small>}
            {relation.accept && <small>{t("接受条件")}：{description?.behavior_constraints[index]?.accept || t("正在加载…")}</small>}
            {relation.enable && <small>{t("执行条件")}：{description?.behavior_constraints[index]?.enable || t("正在加载…")}</small>}
            {relation.provenance && typeof relation.provenance === "object" && !Array.isArray(relation.provenance) &&
              Array.isArray(relation.provenance.evidence) && relation.provenance.evidence.map((e, i) => <small key={i}>{String(e)}</small>)}
          </span>
        </label>)}
      </fieldset>}
      {!!draft.selected_approach?.construction_plan && <details>
        <summary>{t("所选方案的构造方法")}</summary>
        {Array.isArray(plan.instances) && plan.instances.map((item, i) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return null;
          const relation = behaviors.find((row) => row.id === item.requirement_id);
          return <p key={i}>{String(relation?.label || item.requirement_id)}：{t(methods[String(item.method)] || "方法待核对")}
            {Array.isArray(item.depends_on) && item.depends_on.length > 0 && <small>{t("在依赖步骤完成后执行")}</small>}</p>;
        })}
        {Array.isArray(plan.internal_ranges) && plan.internal_ranges.map((region, i) => region && typeof region === "object" && !Array.isArray(region)
          ? <p key={i}>{t("可用内部地址")}：{String(region.start)}–{String(region.end)}</p> : null)}
      </details>}
      {!!draft.decision_receipt && <details>
        <summary>{t("分析记录")}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.decision_receipt, null, 2)}</pre>
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
          {t("需求数据格式有误。")}
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
        {t("确认需求")}
      </Button>
    </fieldset>
  );
}
