import { useEffect, useState } from "react";
import { Button } from "../components/ui";
import { changeSelection, controlValues, parameterEnabled, selectedValues, validValue } from "./modelParameters";
import type { CapabilityContract, Descriptor, Options, Selection, UserModelSettings } from "./modelParameters";

function ScalarInput({ name, desc, selection, disabled, onChange }: {
  name: string; desc: Descriptor; selection: Selection; disabled: boolean; onChange: (selection: Selection) => void;
}) {
  const text = selection.mode === "value" ? String(selection.value) : "";
  const [draft, setDraft] = useState(text);
  useEffect(() => setDraft(text), [text]);
  const numeric = desc.type === "integer" || desc.type === "number";
  return <input id={`parameter-${name}`} aria-label={name} type={numeric ? "number" : "text"}
    min={desc.minimum} max={desc.maximum} step={desc.step ?? (desc.type === "integer" ? 1 : "any")}
    maxLength={256} value={draft} disabled={disabled}
    onChange={e => {
      const next = e.target.value;
      setDraft(next);
      if (next === "") onChange({ mode: "omit" });
      else {
        const value = numeric ? Number(next) : next;
        if (validValue(desc, value)) onChange({ mode: "value", value });
      }
    }}
    onBlur={() => setDraft(text)} />;
}

export function ModelParameters({ contract, settings, defaults, overrides, onChange, disabled, t }: {
  contract: CapabilityContract; settings: UserModelSettings; defaults: string; overrides: string;
  onChange: (settings: UserModelSettings) => void; disabled: boolean; t: (s: string) => string;
}) {
  let groups: [Options, Options];
  try {
    groups = [JSON.parse(defaults), JSON.parse(overrides)];
    if (groups.some(v => !v || typeof v !== "object" || Array.isArray(v))) return null;
  } catch { return null; }
  if (!contract.scope) return <p className="muted">{t("检测模型能力后显示参数滑条。")}</p>;
  const values = selectedValues(contract, settings, ...groups);
  const change = (name: string, selection: Selection) => onChange(changeSelection(contract, settings, name, selection, ...groups));
  const labels = { supported: "支持参数校验", accepted: "仅确认接口接受，可能被忽略", unknown: "尚未确认支持",
    unsupported: "接口不支持", fixed: "固定值，不可调节", conditional: "条件支持" };
  const parameter = ([name, desc]: [string, Descriptor]) => {
    if (desc.status === "unsupported") return null;
    const choices = controlValues(desc), value = values[name];
    const selection = settings.parameters?.[name] || { mode: "inherit" };
    const display = selection.mode === "omit" ? t("服务默认值") : selection.mode === "inherit" ? t("继承工作流与配置默认值") : String(value);
    const enabled = parameterEnabled(name, contract, values);
    const adjustable = ["supported", "conditional"].includes(desc.status) && enabled;
    const id = `parameter-${name}`;
    const hasNumber = desc.type === "integer" || desc.type === "number";
    return <div className="parameter-control" key={name} data-parameter={name}>
      <div className="parameter-heading"><label htmlFor={id}>{desc.label || name}</label>
        <output htmlFor={id} aria-live="polite">{display}</output></div>
      <p className="muted">{t(labels[desc.status])} · {t(desc.source === "metadata" ? "服务元数据" : desc.source === "manual" ? "手动声明" : desc.source === "legacy" ? "旧配置迁移" : "主动检测")}</p>
      {adjustable && <>
        {desc.type === "boolean" ? <label><input id={id} role="switch" type="checkbox" aria-label={name}
          checked={value === true} disabled={disabled} onChange={e => change(name, { mode: "value", value: e.target.checked })} />{t("启用")}</label>
        : choices.length > 0 ? <>
          <input id={id} aria-label={name} type="range" min={0} max={choices.length} step={1}
            value={selection.mode === "value" ? Math.max(0, choices.indexOf(value as string | number) + 1) : 0}
            aria-valuetext={display} disabled={disabled} onChange={e => {
              const index = Number(e.target.value);
              change(name, index === 0 ? { mode: "omit" } : { mode: "value", value: choices[index - 1] });
            }} />
          <div className="parameter-scale"><span>{t("服务默认值")}</span><span>{String(choices[0])} → {String(choices.at(-1))}</span></div>
        </> : <ScalarInput name={name} desc={desc} selection={selection} disabled={disabled}
          onChange={selection => change(name, selection)} />}
        <label className="muted">{t("参数来源")}<select aria-label={`${name} mode`} value={selection.mode} disabled={disabled}
          onChange={e => change(name, e.target.value === "value" ? { mode: "value", value: choices[0] ?? (hasNumber ? desc.minimum ?? 0 : "") } : { mode: e.target.value as "inherit" | "omit" })}>
          <option value="omit">{t("服务默认值")}</option><option value="inherit">{t("继承工作流与配置默认值")}</option>
          <option value="value">{t("用户指定值")}</option></select></label>
      </>}
      {!enabled && <p className="notice">{t("参数条件未满足，请调整关联参数或重新检测。")}</p>}
      {desc.status === "fixed" && <p>{t("固定值")}: {String(desc.values?.[0])}</p>}
      {(desc.requires || desc.conflicts_with || contract.constraints?.[name]) &&
        <p className="muted mono">{JSON.stringify({ ...contract.constraints?.[name], requires: desc.requires || contract.constraints?.[name]?.requires, conflicts_with: desc.conflicts_with || contract.constraints?.[name]?.conflicts_with })}</p>}
      {selection.mode === "value" && <Button variant="ghost" disabled={disabled} onClick={() => change(name, { mode: "omit" })}>{t("恢复服务默认值")}</Button>}
    </div>;
  };
  const entries = Object.entries(contract.parameters || {});
  const uncertain = entries.filter(([, d]) => ["unknown", "accepted"].includes(d.status));
  return <section className="model-parameters" aria-label={t("模型参数")}>
    <div className="capability-grid" aria-label={t("模型能力合同")}>
      {Object.entries(contract.capabilities || {}).map(([name, cap]) => <div key={name}>
        <code>{name}</code>: {cap.status} · {cap.source}{cap.modes?.length ? ` · ${cap.modes.join(" / ")}` : ""}
        {cap.value != null ? ` · ${String(cap.value)}` : ""}
        {(cap.requires || cap.conflicts_with) && <p className="mono">{JSON.stringify({ requires: cap.requires, conflicts_with: cap.conflicts_with })}</p>}
      </div>)}
    </div>
    {entries.filter(([, d]) => !["unknown", "accepted"].includes(d.status)).map(parameter)}
    {uncertain.length > 0 && <details><summary>{t("尚未验证的高级参数")}</summary>{uncertain.map(parameter)}</details>}
  </section>;
}
