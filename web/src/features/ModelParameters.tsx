import { Button } from "../components/ui";
import { changeParameter, controlValues, effectiveValue, temperatureModeMatches } from "./modelParameters";
import type { Options, ParameterName, ParameterSupport } from "./modelParameters";

export function ModelParameters({ support, defaults, overrides, onChange, disabled, t }: {
  support: ParameterSupport;
  defaults: string;
  overrides: string;
  onChange: (defaults: string, overrides: string) => void;
  disabled: boolean;
  t: (s: string) => string;
}) {
  let groups: [Options, Options];
  try {
    groups = [JSON.parse(defaults), JSON.parse(overrides)];
    if (groups.some(item => !item || typeof item !== "object" || Array.isArray(item))) return null;
  } catch { return null; }
  if (!support.parameters) return <p className="muted">{t("检测模型能力后显示参数滑条。")}</p>;
  const effort = effectiveValue(...groups, "reasoning_effort");
  const change = (name: ParameterName, value: string | number | null) => {
    const [nextDefaults, nextOverrides] = changeParameter(...groups, name, value);
    onChange(JSON.stringify(nextDefaults, null, 2), JSON.stringify(nextOverrides, null, 2));
  };
  const labels = {
    supported: "支持参数校验", accepted: "仅确认接口接受，可能被忽略", unknown: "尚未确认支持",
    unsupported: "接口不支持", fixed: "固定值，不可调节",
  };
  return <section className="model-parameters" aria-label={t("模型参数")}>
    {(["reasoning_effort", "temperature"] as const).map(name => {
      const descriptor = support.parameters?.[name];
      if (!descriptor) return null;
      const values = controlValues(descriptor);
      const value = effectiveValue(...groups, name);
      const modeMatches = name !== "temperature" || temperatureModeMatches(descriptor, effort);
      const adjustable = descriptor.status === "supported" && values.length > 0 && modeMatches;
      const position = value == null ? 0 : values.indexOf(value as string | number) + 1;
      const display = value == null ? t("服务默认值") : String(value);
      return <div className="parameter-control" key={name}>
        <div className="parameter-heading">
          <label htmlFor={`parameter-${name}`}>{name === "reasoning_effort" ? t("推理强度") : t("温度")} <code>{name}</code></label>
          <output htmlFor={`parameter-${name}`} aria-live="polite">{display}</output>
        </div>
        <p className="muted">{t(labels[descriptor.status])} · {t(descriptor.source === "metadata" ? "服务元数据" : descriptor.source === "manual" ? "手动声明" : "主动检测")}</p>
        {adjustable && <>
          <input id={`parameter-${name}`} aria-label={name} type="range" min={0} max={values.length}
            step={1} value={Math.max(0, position)} aria-valuetext={display} disabled={disabled}
            onChange={event => {
              const index = Number(event.target.value);
              change(name, index === 0 ? null : values[index - 1]);
            }} />
          <div className="parameter-scale"><span>{t("服务默认值")}</span><span>{String(values[0])} → {String(values.at(-1))}</span></div>
        </>}
        {!modeMatches && <p className="notice">{t("推理设置已改变，请重新检测温度支持。")}</p>}
        {descriptor.status === "fixed" && <p>{t("固定值")}: {String(values[0])}</p>}
        {value != null && <Button variant="ghost" disabled={disabled} onClick={() => change(name, null)}>{t("恢复服务默认值")}</Button>}
      </div>;
    })}
  </section>;
}
