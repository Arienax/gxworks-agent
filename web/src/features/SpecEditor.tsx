import { useEffect, useId, useRef, useState } from "react";
import { Plus, Check } from "lucide-react";
import { api } from "../api/client";
import type { Spec, Json } from "../api/client";
import { Button } from "../components/ui";
import { ChoiceInput } from "../components/ChoiceInput";

function describeExpression(value: Json | undefined, depth = 0): string {
  if (!value || typeof value !== "object" || Array.isArray(value) || depth > 24) return "待核对";
  const args = Array.isArray(value.args) ? value.args.map((a) => describeExpression(a, depth + 1)) : [];
  if (value.op === "constant") return String(value.value);
  if (["device", "parameter", "state"].includes(String(value.op))) {
    const type = value.type && typeof value.type === "object" && !Array.isArray(value.type) ? value.type : {};
    return String(value.name) + (type.kind === "int" ? `（${type.signed ? "有符号" : "无符号"}${type.bits}位）` : "");
  }
  const operators: Record<string, string> = { gt: "大于", ge: "大于等于", lt: "小于", le: "小于等于", eq: "等于", ne: "不等于", add: "+", sub: "−", mul: "×", div: "÷", and: "且", or: "或", xor: "异或", bit_and: "按位与", bit_or: "按位或", bit_xor: "按位异或", shift_left: "左移", shift_right: "右移" };
  if (value.op === "vector") return `[${args.join("，")}]`;
  if (value.op === "not") return `非（${args[0]}）`;
  if (value.op === "bit_not") return `按位取反（${args[0]}）`;
  if (operators[String(value.op)]) return `（${args.join(` ${operators[String(value.op)]} `)}）`;
  return "待核对";
}

function describeEffect(value: Json): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "待核对";
  const target = value.target && typeof value.target === "object" && !Array.isArray(value.target) ? value.target : {};
  const address = String(target.device || "未指定结果地址") + (target.offset ? `（偏移 ${target.offset}）` : "");
  if (value.kind === "external_action") return `外部设备调用，读出结果保存到 ${address}`;
  if (value.kind === "range_copy") return `${describeExpression(value.source)} 起始的 ${describeExpression(value.count)} 个元素复制到 ${address}`;
  if (value.kind === "range_shift") return `${address} 起始的 ${describeExpression(value.count)} 个元素${value.direction === "left" ? "左移" : "右移"} ${describeExpression(value.shift)} 位，由 ${describeExpression(value.source)} 补入`;
  return `${address} = ${describeExpression(value.value)}`;
}

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
  const operationIntents = Array.isArray(draft.operation_intents) ? draft.operation_intents.filter((v): v is Record<string, Json> => !!v && typeof v === "object" && !Array.isArray(v)) : [];
  const confirmedIntentIds = Array.isArray(draft.confirmed_operation_intent_ids) ? draft.confirmed_operation_intent_ids.map(String) : [];
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
      {!!draft.intent_context && <details>
        <summary>{t("原始用户意图")}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.intent_context, null, 2)}</pre>
      </details>}
      {operationIntents.length > 0 && <fieldset>
        <legend>{t("核对指令效果")}</legend>
        <p>{t("确认下面的结果关系和使能条件。待核对项不会作为生成正确性的依据。")}</p>
        {operationIntents.map((intent) => <label key={String(intent.id)}>
          <input type="checkbox" checked={intent.status === "confirmed" || confirmedIntentIds.includes(String(intent.id))}
            disabled={intent.status === "confirmed"}
            onChange={(e) => patch({ ...draft, confirmed_operation_intent_ids: e.target.checked
              ? [...confirmedIntentIds, String(intent.id)] : confirmedIntentIds.filter((id) => id !== String(intent.id)) })} />
          <span>{String(intent.label || intent.id)}
            {Array.isArray(intent.effects) && intent.effects.map((effect, i) => <small key={i}>{describeEffect(effect)}</small>)}
            {intent.parameters && typeof intent.parameters === "object" && !Array.isArray(intent.parameters) &&
              Object.entries(intent.parameters).map(([name, value]) => <small key={name}>{name} = {describeExpression(value)}</small>)}
            <small>{t("使能条件")}：{describeExpression(intent.enable)}</small>
            <small>{intent.execution && typeof intent.execution === "object" && !Array.isArray(intent.execution) && intent.execution.trigger === "rising"
              ? t("条件从断开变为成立时执行") : t("条件成立期间执行")}</small>
            {intent.execution && typeof intent.execution === "object" && !Array.isArray(intent.execution) &&
              intent.execution.required_state && typeof intent.execution.required_state === "object" && !Array.isArray(intent.execution.required_state) &&
              Object.entries(intent.execution.required_state).map(([name, state]) => <small key={name}>
                {t("必要状态")}：{name} = {typeof state === "boolean" ? (state ? "ON" : "OFF") : String(state)}</small>)}
          </span>
        </label>)}
      </fieldset>}
      {!!draft.decision_receipt && <details>
        <summary>{t("确认前分析记录（仅审计）")}</summary>
        <p>{t("分析与检索记录单独保存，不进入生成规格；可从任务诊断 ZIP 追溯。")}</p>
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
