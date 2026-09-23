import { useEffect, useRef, useState } from "react";
import { Button } from "../components/ui";
import { changeSelection, controlValues, hardDomain, parameterEnabled, selectedValues, sliderRange, validValue } from "./modelParameters";
import type { CapabilityContract, Descriptor, Scalar, Selection, UserModelSettings } from "./modelParameters";

function ScalarInput({ name, desc, selection, disabled, onChange }: {
  name: string; desc: Descriptor; selection: Selection; disabled: boolean; onChange: (selection: Selection) => void;
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
    onChange={e => {
      const next = e.target.value;
      setDraft(next);
      const value = numeric ? Number(next) : next;
      const ok = next === "" || validValue(desc, value);
      setInvalid(!ok);
      e.target.setCustomValidity(ok ? "" : "Invalid declared parameter value");
      if (ok) onChange(next === "" ? {mode:"omit"} : {mode:"value",value});
    }} />{invalid && <p role="alert">该值不符合声明类型或范围，尚未应用。</p>}</>;
}

export function ModelParameters({ contract, settings, onChange, disabled, t, onVerify }: {
  contract: CapabilityContract; settings: UserModelSettings;
  onChange: (settings: UserModelSettings) => void; disabled: boolean; t: (s: string) => string;
  onVerify?: (target: string, kind: "parameter" | "capability", value?: Scalar) => void;
}) {
  if (!contract.scope) return <p className="muted">{t("选择模型后点击“加载能力配置”；只解析本地预设，不调用模型生成。")}</p>;
  const values = selectedValues(contract, settings);
  const change = (name: string, selection: Selection) => onChange(changeSelection(contract, settings, name, selection));
  const labels = {supported:"已声明支持",accepted:"仅确认接口接受，是否生效未知",unknown:"未验证，可手动设置",
    unsupported:"声明不支持",fixed:"固定值",conditional:"条件支持"};
  const sources: Record<string,string> = {catalog:"本地预设",metadata:"服务元数据",manual:"手动声明",generic:"通用参考模板",probe:"历史验证",observation:"使用记录",legacy:"旧配置"};
  const parameter = ([name, desc]: [string, Descriptor]) => {
    const choices = controlValues(desc), value = values[name], domain = hardDomain(desc);
    const selection = settings.parameters?.[name] || {mode:"inherit"};
    const enabled = parameterEnabled(name, contract, values);
    const editable = !["fixed","unsupported"].includes(desc.status);
    const range = sliderRange(desc);
    const chosen = selection.mode === "value";
    const conflict = chosen && (!editable || !validValue(desc, selection.value) || !enabled);
    return <div className="parameter-control" key={name} data-parameter={name} data-invalid={conflict || undefined}>
      <div className="parameter-heading"><label htmlFor={`parameter-${name}`}>{desc.label || name}</label>
        <output aria-live="polite">{chosen ? String(selection.value) : t(selection.mode === "omit" ? "服务默认值（不发送）" : "继承运行时默认")}</output></div>
      <p className="muted">{t(labels[desc.status])} · {t(sources[desc.source] || desc.source)}</p>
      {editable && <>
        <select aria-label={`${name} mode`} value={selection.mode} disabled={disabled}
          onChange={e => change(name,e.target.value === "value" ? {mode:"value",value:choices[0] ?? (desc.type === "boolean" ? false : ["number","integer"].includes(desc.type) ? range?.minimum ?? domain.minimum ?? 0 : "")} : {mode:e.target.value as "omit" | "inherit"})}>
          <option value="omit">{t("服务默认值（不发送）")}</option><option value="inherit">{t("继承运行时默认")}</option><option value="value">{t("用户指定值")}</option>
        </select>
        {desc.type === "boolean" ? <label><input id={`parameter-${name}`} role="switch" type="checkbox" aria-label={name}
          checked={value === true} disabled={disabled} onChange={e => change(name,{mode:"value",value:e.target.checked})} />{t("启用")}</label>
        : choices.length > 0 ? <>
          <input type="range" aria-label={`${name} slider`} min={0} max={Math.max(0,choices.length-1)} step={1}
            value={Math.max(0,choices.indexOf(value as Scalar))} disabled={disabled}
            onChange={e => change(name,{mode:"value",value:choices[Number(e.target.value)]})} />
          <select aria-label={`${name} choice`} value={chosen && choices.includes(selection.value) ? String(choices.indexOf(selection.value)) : ""} disabled={disabled}
            onChange={e => {const v=e.target.value === "" ? undefined : choices[Number(e.target.value)];if(v!==undefined)change(name,{mode:"value",value:v});}}>
            <option value="">{t("选择值")}</option>{choices.map((v,i)=><option key={i} value={i}>{String(v)}</option>)}
          </select>
          {!domain.values && <ScalarInput name={name} desc={desc} selection={selection} disabled={disabled} onChange={s=>change(name,s)} />}
        </> : <>
          {range && <><input type="range" aria-label={`${name} slider`} min={range.minimum} max={range.maximum} step={range.step}
            value={typeof value === "number" ? Math.max(range.minimum,Math.min(range.maximum,value)) : range.minimum}
            disabled={disabled} onChange={e=>change(name,{mode:"value",value:Number(e.target.value)})} />
            <p className="muted">{range.minimum} – {range.maximum} · {t("滑条精度")}: {range.step} · {t("输入框可填写更精确数值")}</p></>}
          <ScalarInput name={name} desc={desc} selection={selection} disabled={disabled} onChange={s=>change(name,s)} />
        </>}
        {desc.domain?.enforcement !== "hard" && <p className="muted">{t("参考范围不是服务限制；用户指定值会发送给服务，是否生效尚未确认。")}</p>}
        {chosen && onVerify && <Button disabled={disabled || conflict} onClick={()=>onVerify(name,"parameter",selection.value)}>{t("验证当前值（1 次请求）")}</Button>}
      </>}
      {desc.evidence?.accepted_values?.length ? <p className="muted">{t("历史接受样本（不是可选值限制）")}: {desc.evidence.accepted_values.join(", ")}</p> : null}
      {desc.evidence?.observations?.length ? <p className="muted">{t("最近使用记录（仅对应当时请求组合）")}: {desc.evidence.observations.slice(0,3).map(e=>`${e.value}: ${e.outcome}`).join("; ")}</p> : null}
      {!enabled && <p className="notice">{t("关联条件未满足；可先编辑，保存前需要调整关联参数或恢复默认值。")}</p>}
      {conflict && <p role="alert">{t("当前显式选择与能力声明冲突，未自动删除。请调整后再保存。")}</p>}
      {desc.status === "fixed" && <p>{t("固定值")}: {String(domain.values?.[0])}</p>}
      {(desc.requires || desc.conflicts_with) && <p className="muted mono">{JSON.stringify({requires:desc.requires,conflicts_with:desc.conflicts_with})}</p>}
    </div>;
  };
  const entries = Object.entries(contract.parameters || {});
  const advanced = ([,d]:[string,Descriptor]) => d.ui_hint?.advanced || d.status === "unsupported";
  return <section className="model-parameters" aria-label={t("模型参数")}>
    <div className="capability-grid" aria-label={t("模型能力合同")}>
      {Object.entries(contract.capabilities || {}).map(([name,cap])=><div key={name}>
        <code>{name}</code>: {cap.status} · {t(sources[cap.source] || cap.source)}{cap.modes?.length ? ` · ${cap.modes.join(" / ")}` : ""}
        {cap.evidence?.observations?.length ? <p className="muted">{t("实际使用中出现过对应输出；不等于完整能力保证")}</p> : null}
        {onVerify && ["tools","structured_output"].includes(name) && cap.status !== "unsupported" && (name !== "structured_output" || !cap.modes?.length || cap.modes.includes("json_object")) && <Button disabled={disabled}
          onClick={()=>onVerify(name,"capability",name==="structured_output" ? "json_object" : undefined)}>{t("单项验证（1 次请求）")}</Button>}
      </div>)}
    </div>
    {entries.filter(e=>!advanced(e)).map(parameter)}
    {entries.some(advanced) && <details><summary>{t("高级参数与不支持项")}</summary>{entries.filter(advanced).map(parameter)}</details>}
  </section>;
}
