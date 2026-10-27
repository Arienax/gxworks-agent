// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import { Button } from "../components/ui";
import { changeSelection, controlValues, hardDomain, parameterEnabled, selectedValues, sliderRange, validValue, selectionFor, modifiedParameters, parameterTitle } from "./modelParameters";
import type { CapabilityContract, Descriptor, Scalar, Selection, UserModelSettings } from "./modelParameters";

function ScalarInput({ name, desc, selection, disabled, onChange, t, suggestions = [] }: {
  name: string; desc: Descriptor; selection: Selection; disabled: boolean; onChange: (selection: Selection) => void;
  t: (s: string) => string;
  suggestions?: Scalar[];
}) {
  const text = selection.mode === "value" ? String(selection.value) : "";
  const [draft, setDraft] = useState(text);
  const [invalid, setInvalid] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { setDraft(text); setInvalid(false); input.current?.setCustomValidity(""); }, [text]);
  const numeric = desc.type === "integer" || desc.type === "number";
  const domain = hardDomain(desc);
  return <><input ref={input} id={`parameter-${name}`} aria-label={`${name} value`} type={numeric ? "number" : "text"}
    min={domain.minimum} max={domain.maximum} step={desc.type === "integer" ? 1 : "any"}
    maxLength={256} value={draft} disabled={disabled} aria-invalid={invalid}
    list={suggestions.length ? `parameter-${name}-values` : undefined}
    onChange={e => {
      const next = e.target.value;
      setDraft(next);
      const value = numeric ? Number(next) : next;
      const ok = next === "" || validValue(desc, value);
      setInvalid(!ok);
      e.target.setCustomValidity(ok ? "" : t("输入值不符合要求。"));
      if (ok) onChange(next === "" ? {mode:"omit"} : {mode:"value",value});
    }} />
    {suggestions.length > 0 && <datalist id={`parameter-${name}-values`}>
      {suggestions.map((value, index) => <option key={index} value={String(value)} />)}
    </datalist>}
    {invalid && <p role="alert">{t("输入值不符合要求。")}</p>}</>;
}

export function ModelParameters({ contract, settings, onChange, disabled, t, onVerify, diagnostics = false }: {
  contract: CapabilityContract; settings: UserModelSettings;
  onChange: (settings: UserModelSettings) => void; disabled: boolean; t: (s: string) => string;
  onVerify?: (target: string, kind: "parameter" | "capability", value?: Scalar) => void;
  diagnostics?: boolean;
}) {
  const [modifiedOnly, setModifiedOnly] = useState(false);
  if (!contract.scope) return null;
  const values = selectedValues(contract, settings);
  const modified = modifiedParameters(contract, settings);
  const change = (name: string, selection: Selection) => onChange(changeSelection(contract, settings, name, selection));
  const labels = {supported:"已声明支持",accepted:"仅确认接口接受，是否生效未知",unknown:"未验证，可手动设置",
    unsupported:"声明不支持",fixed:"固定值",conditional:"条件支持"};
  const sources: Record<string,string> = {catalog:"本地预设",metadata:"服务元数据",manual:"手动声明",generic:"通用参考模板",probe:"历史验证",observation:"使用记录",legacy:"旧配置"};
  const parameter = ([name, desc]: [string, Descriptor]) => {
    const choices = controlValues(desc), value = values[name], domain = hardDomain(desc);
    const selection = selectionFor(name, settings);
    const enabled = parameterEnabled(name, contract, values, settings);
    const editable = !["fixed","unsupported"].includes(desc.status);
    const range = sliderRange(desc);
    const chosen = selection.mode === "value";
    const conflict = chosen && (!editable || !validValue(desc, selection.value) || !enabled);
    return <div className="parameter-control" key={name} data-parameter={name} data-invalid={!diagnostics && conflict || undefined}>
      <div className="parameter-heading"><span><strong>{t(parameterTitle(name, desc))}</strong>
        {(diagnostics || name !== "reasoning_effort") && <small className="muted mono">{name}</small>}
      </span>{diagnostics && <output>{chosen ? String(selection.value) : t(selection.mode === "omit" ? "服务默认" : "跟随应用设置 · 默认")}</output>}</div>
      {diagnostics && <p className="muted">{t(labels[desc.status])} · {t(sources[desc.source] || desc.source)}</p>}
      {editable && !diagnostics && <div className="parameter-inputs">
        <select aria-label={`${name} mode`} value={selection.mode} disabled={disabled}
          onChange={e => change(name,e.target.value === "value" ? {mode:"value",value:choices[0] ?? (desc.type === "boolean" ? false : ["number","integer"].includes(desc.type) ? range?.minimum ?? domain.minimum ?? 0 : "")} : {mode:e.target.value as "omit" | "inherit"})}>
          <option value="omit">{t("服务默认")}</option><option value="inherit">{t("跟随应用设置")}</option><option value="value">{t("自定义")}</option>
        </select>
        {chosen && (desc.type === "boolean" ? <label><input id={`parameter-${name}`} role="switch" type="checkbox" aria-label={name}
          checked={value === true} disabled={disabled} onChange={e => change(name,{mode:"value",value:e.target.checked})} />{t("启用")}</label>
        : domain.values?.length ?
          <select aria-label={`${name} choice`} value={chosen && choices.includes(selection.value) ? String(choices.indexOf(selection.value)) : ""} disabled={disabled}
            onChange={e => {const v=e.target.value === "" ? undefined : choices[Number(e.target.value)];if(v!==undefined)change(name,{mode:"value",value:v});}}>
            <option value="">{t("选择值")}</option>{choices.map((v,i)=><option key={i} value={i}>{String(v)}</option>)}
          </select>
        : <ScalarInput name={name} desc={desc} selection={selection} disabled={disabled} suggestions={choices} onChange={s=>change(name,s)} t={t} />)}
      </div>}
      {diagnostics && chosen && onVerify && <Button disabled={disabled || conflict} onClick={()=>onVerify(name,"parameter",selection.value)}>{t("验证当前值（1 次请求）")}</Button>}
      {diagnostics && !!desc.evidence?.accepted_values?.length && <p className="muted">{t("历史接受样本（不是可选值限制）")}: {desc.evidence.accepted_values.join(", ")}</p>}
      {diagnostics && !!desc.evidence?.observations?.length && <p className="muted">{t("最近使用记录（仅对应当时请求组合）")}: {desc.evidence.observations.slice(0,3).map(e=>`${e.value}: ${e.outcome}`).join("; ")}</p>}
      {diagnostics && !enabled && !conflict && <p className="notice">{t("请先调整相关设置或恢复默认。")}</p>}
      {conflict && <p role="alert">{t("当前值不适用于此模型，请调整或恢复默认。")}</p>}
      {desc.status === "fixed" && <p>{t("固定值")}: {String(domain.values?.[0])}</p>}
      {diagnostics && (desc.requires || desc.conflicts_with) && <p className="muted mono">{JSON.stringify({requires:desc.requires,conflicts_with:desc.conflicts_with})}</p>}
      {/* Non-editable descriptors have no mode selector, but stale explicit
          selections must remain removable without erasing other parameters. */}
      {!diagnostics && chosen && !editable && <Button variant="ghost" disabled={disabled}
        onClick={()=>change(name,{mode:"omit"})}>{t("恢复服务默认值")}</Button>}
    </div>;
  };
  const entries = Object.entries(contract.parameters || {});
  const thinking = entries.filter(([name, d]) => name === "reasoning_effort" && !["unsupported", "fixed"].includes(d.status));
  const advanced = entries.filter(e => !thinking.includes(e) &&
    (!["unsupported", "fixed"].includes(e[1].status) || selectionFor(e[0], settings).mode === "value"));
  const groups = [
    ["采样设置", ["temperature", "top_p", "frequency_penalty", "presence_penalty", "seed"]],
    ["生成长度限制", ["max_tokens", "max_completion_tokens"]],
    ["服务专用参数", []],
  ] as const;
  return <section className={diagnostics ? "model-diagnostics" : "model-parameters"} aria-label={t(diagnostics ? "兼容性详情" : "模型参数")}>
    {diagnostics ? <>
    <div className="capability-grid" aria-label={t("模型能力合同")}>
      {Object.entries(contract.capabilities || {}).map(([name,cap])=><div key={name}>
        <code>{name}</code>: {cap.status} · {t(sources[cap.source] || cap.source)}{cap.modes?.length ? ` · ${cap.modes.join(" / ")}` : ""}
        {onVerify && ["tools","structured_output"].includes(name) && cap.status !== "unsupported" && (name !== "structured_output" || !cap.modes?.length || cap.modes.includes("json_object")) && <Button disabled={disabled}
          onClick={()=>onVerify(name,"capability",name==="structured_output" ? "json_object" : undefined)}>{t("单项验证（1 次请求）")}</Button>}
      </div>)}
    </div>
    {entries.map(parameter)}</> : <>
      {thinking.map(parameter)}
      <div className="advanced-parameters">
        <label><input type="checkbox" checked={modifiedOnly} onChange={e=>setModifiedOnly(e.target.checked)} />{t("仅看已修改")}</label>
        {groups.map(([title, names], index) => {
          const rows = advanced.filter(([name]) => (index === 2 ? !groups.slice(0,2).some(([, ns]) => (ns as readonly string[]).includes(name)) :
            (names as readonly string[]).includes(name)) && (!modifiedOnly || modified.includes(name)));
          return rows.length > 0 && <section key={title}><h4>{t(title)}</h4>{rows.map(parameter)}</section>;
        })}
        {modifiedOnly && !modified.some(n=>advanced.some(([name])=>name===n)) && <p>{t("没有已修改的高级参数")}</p>}
      </div>
    </>}
  </section>;
}
