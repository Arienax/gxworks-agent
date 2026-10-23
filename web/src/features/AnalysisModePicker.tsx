import { useId } from "react";
import type { components } from "../api/generated";
import type { Locale } from "../i18n";
import "./AnalysisModePicker.css";

export type AnalysisMode = components["schemas"]["JobCreate"]["analysis_mode"];

const labels: Record<Locale, { title: string; direct: string; design: string }> = {
  "zh-CN": {
    title: "方案",
    direct: "单方案",
    design: "比较方案",
  },
  en: {
    title: "Approach",
    direct: "One approach",
    design: "Compare approaches",
  },
  ja: {
    title: "方式の検討",
    direct: "一つの方式",
    design: "方式を比較",
  },
};

export function AnalysisModePicker({ locale, value, onChange, disabled = false, compact = false }: {
  locale: Locale;
  value: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const group = useId();
  const text = labels[locale];
  if (compact) return (
    <label className="composer-select">
      <span className="sr-only">{text.title}</span>
      <select aria-label={text.title} value={value} disabled={disabled}
        onChange={(event) => onChange(event.target.value as AnalysisMode)}>
        <option value="direct">{text.direct}</option>
        <option value="design">{text.design}</option>
      </select>
    </label>
  );
  return (
    <fieldset className="analysis-mode-picker" disabled={disabled}>
      <legend>{text.title}</legend>
      {(["direct", "design"] as const).map((mode) => (
        <label key={mode} className={value === mode ? "selected" : ""}>
          <input type="radio" name={group} value={mode} checked={value === mode}
            onChange={() => onChange(mode)} />
          <span>
            <strong>{text[mode]}</strong>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
