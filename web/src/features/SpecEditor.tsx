// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useId } from "react";
import { Check, RefreshCw } from "lucide-react";
import type { Spec, ApiSchema } from "../api/client";
import { Button } from "../components/ui";
import { ChoiceInput } from "../components/ChoiceInput";

const comparisonLabels = {
  behavior: "行为与适用条件",
  program_size: "程序规模",
  scan_work: "扫描处理量",
  maintenance: "扩展与维护",
  recommendation: "推荐理由",
} as const;

function approachComparison(approach: ApiSchema["SpecificationApproach"]) {
  const rows = new Map<string, string[]>();
  function add(label: string, value: string) {
    if (!value.trim()) return;
    const values = rows.get(label) || [];
    if (!values.includes(value.trim())) rows.set(label, [...values, value.trim()]);
  }
  for (const [field, label] of [["pros", "优点"], ["cons", "代价与限制"]] as const) {
    const value = approach[field];
    if (typeof value !== "string") continue;
    let key = label as string;
    for (const line of value.split(/\r?\n/)) {
      const match = /^\s*(behavior|program_size|scan_work|maintenance|recommendation)\s*[:：]\s*(.*)$/.exec(line);
      if (match) key = comparisonLabels[match[1] as keyof typeof comparisonLabels];
      add(key, match ? match[2] : line);
    }
  }
  const choices = approach.instruction_choices;
  if (Array.isArray(choices)) {
    for (const choice of choices) {
      if (choice && typeof choice === "object" && !Array.isArray(choice) && typeof choice.opcode === "string") {
        add("关键指令", typeof choice.role === "string" && choice.role.trim()
          ? `${choice.opcode} · ${choice.role}` : choice.opcode);
      }
    }
  }
  return ["关键指令", comparisonLabels.behavior, comparisonLabels.program_size,
    comparisonLabels.scan_work, comparisonLabels.maintenance, "优点", "代价与限制",
    comparisonLabels.recommendation].flatMap(label => rows.has(label) ? [{ label, values: rows.get(label)! }] : []);
}

function implementationSymbols(approach?: ApiSchema["SpecificationApproach"] | null) {
  const choices = approach?.instruction_choices;
  if (!Array.isArray(choices)) return "";
  const names = choices.flatMap((choice) => {
    if (choice && typeof choice === "object" && !Array.isArray(choice) && typeof choice.opcode === "string") {
      return [choice.opcode];
    }
    return [];
  });
  return [...new Set(names)].join(" · ");
}

export function SpecEditor({
  value: draft,
  document,
  onRefresh,
  onSaveDraft,
  t,
  disabled,
  onSave,
  onChange,
  issues,
}: {
  value: Spec | null;
  document?: ApiSchema["SpecDocument"] | null;
  onRefresh?: () => void;
  onSaveDraft?: (spec: Spec) => void;
  t: (key: string) => string;
  disabled: boolean;
  onSave: (spec: Spec) => void;
  onChange: (spec: Spec) => void;
  issues: { path: string; message: string }[];
}) {
  const approachGroup = useId();
  if (!draft)
    return <div className="panel-empty">{t("请先输入控制需求。")}</div>;

  const confirmed = document?.confirmed_spec;
  const parameters = draft.parameters || [];
  const openQuestions = draft.open_questions || [];
  const parameterIds = new Set(parameters.flatMap((row) => row.id?.trim() ? [row.id.trim()] : []));
  const independentQuestions = openQuestions.map((row, index) => ({ row, index })).filter(({ row }) =>
    typeof row.related_missing_info !== "string" || !parameterIds.has(row.related_missing_info.trim()));
  // Old servers may still return the linked rows. Persist only independent questions there as well.
  const reviewedDraft = independentQuestions.length === openQuestions.length ? draft
    : { ...draft, open_questions: independentQuestions.map(({ row }) => row) };
  const approaches = draft.approaches || [];
  const approachId = (approach?: ApiSchema["SpecificationApproach"] | null) =>
    approach?.approach_id || approach?.id || "";
  const selectedId = approachId(draft.selected_approach);
  const showApproaches = approaches.length > 0 &&
    (!selectedId || !confirmed || !!document?.differences?.length);
  const selectedSymbols = implementationSymbols(draft.selected_approach);
  const issueAt = (path: string) =>
    issues.filter((issue) => issue.path.startsWith(path)).map((issue) => issue.message).join("；");
  const wasBlocking = (path: string) => document?.issues?.errors?.some((issue) => issue.path?.startsWith(path));

  const parameterQuestions = parameters.map((row, index) => ({
    row, index, error: issueAt(`$.parameters[${index}]`),
  })).filter(({ row, index, error }) => {
    if (error || wasBlocking(`$.parameters[${index}]`)) return true;
    const original = document?.draft?.parameters?.[index] || row;
    const value = String(original.value ?? "");
    if (value.trim() && original.source === "confirmed_request_fact") return false;
    return !confirmed?.parameters?.some((previous) =>
      (row.id && previous.id ? row.id === previous.id : !!row.name && row.name === previous.name)
      && value === String(previous.value ?? ""));
  });

  const unanswered = independentQuestions.map(({ row, index }) => ({
    row, index, error: issueAt(`$.open_questions[${index}]`),
  })).filter(({ row, index, error }) => {
    if (error || wasBlocking(`$.open_questions[${index}]`)) return true;
    const original = document?.draft?.open_questions?.[index] || row;
    return !confirmed?.open_questions?.some((previous) =>
      row.id === previous.id && row.text === previous.text && original.answer === previous.answer);
  });

  return (
    <fieldset className="spec-editor" disabled={disabled}>
      <label>
        {t("需求摘要")}
        <textarea rows={4} value={draft.summary || ""}
          onChange={(event) => onChange({ ...draft, summary: event.target.value })} />
      </label>

      {(parameterQuestions.length > 0 || unanswered.length > 0 || showApproaches) &&
        <div className="section-label">{t("确认问题")}</div>}
      {parameterQuestions.map(({ row, index, error }) => (
        <ChoiceInput key={row.id || index} t={t}
          label={String(row.question || row.label || row.name || row.id)}
          required={!!row.required && !row.required_when}
          hint={row.suggested_default != null ? `${t("建议值（待确认）")}：${String(row.suggested_default)}`
            : row.required_when ? t("根据相关选项确认") : undefined}
          error={error} value={String(row.value ?? "")}
          options={Array.isArray(row.options) ? row.options.filter((option) => typeof option === "string" && !!option.trim()) : []}
          onChange={(value) => onChange({ ...draft, parameters: parameters.map((item, i) =>
            i === index ? { ...item, value, source: "user" } : item) })}
        />
      ))}
      {unanswered.map(({ row, index, error }) => (
        <ChoiceInput key={row.id} t={t} label={row.text}
          required={row.required === undefined || row.required === true}
          error={error} value={String(row.answer ?? "")} options={[]}
          onChange={(answer) => onChange({ ...draft, open_questions: openQuestions.map((item, i) =>
            i === index ? { ...item, answer: answer.trim() ? answer : "" } : item) })}
        />
      ))}
      {showApproaches && (
        <fieldset className="approach-choices">
          <legend>{t("编程方案")}</legend>
          {approaches.map((approach, index) => {
            const id = approachId(approach);
            const selected = selectedId === id && !!id;
            const comparison = approachComparison(approach);
            return (
              <label key={id || index} className={selected ? "selected" : ""}>
                <input type="radio" name={approachGroup} checked={selected}
                  onChange={() => onChange({ ...draft, selected_approach: approach })} />
                <span>
                  <strong>{approach.name || approach.title || id || index + 1}</strong>
                  {approach.description && <small><b>{t("实现方式")}</b> {approach.description}</small>}
                  {comparison.map(({ label, values }) => (
                    <small key={label} className="approach-detail">
                      <b>{t(label)}</b>
                      <span>{values.join("\n")}</span>
                    </small>
                  ))}
                </span>
              </label>
            );
          })}
        </fieldset>
      )}
      {!showApproaches && selectedSymbols && (
        <div className="section-label">
          {draft.selected_approach?.name || t("编程方案")} · {selectedSymbols}
        </div>
      )}

      {issues.filter(({ path }) => !path.startsWith("$.parameters[") && !path.startsWith("$.open_questions["))
        .map((issue, i) => <p className="error-text" role="alert" key={i}>{issue.message}</p>)}
      <div className="spec-actions">
        {onRefresh && <Button variant="ghost" onClick={onRefresh}
          aria-label={t("重新读取文件（放弃未保存编辑）")} title={t("重新读取文件（放弃未保存编辑）")}>
          <RefreshCw size={15} />
        </Button>}
        {onSaveDraft && <Button onClick={() => onSaveDraft(reviewedDraft)}>{t("保存草稿")}</Button>}
        <Button variant="primary" onClick={() => onSave(reviewedDraft)}>
          <Check size={15} />
          {t("确认需求")}
        </Button>
      </div>
    </fieldset>
  );
}
