import { useId } from "react";
import type { components } from "../api/generated";
import type { Locale } from "../i18n";
import "./AnalysisModePicker.css";

export type AnalysisMode = components["schemas"]["JobCreate"]["analysis_mode"];

const labels: Record<Locale, { title: string; direct: string; directHint: string; design: string; designHint: string }> = {
  "zh-CN": {
    title: "方案策略",
    direct: "单方案",
    directHint: "按当前需求给出一个明确方案，不比较其他架构。",
    design: "比较方案",
    designHint: "探索可行架构，给出 1～3 个方案供选择。",
  },
  en: {
    title: "Approach strategy",
    direct: "One approach",
    directHint: "Produce one concrete implementation without comparing architectures.",
    design: "Compare approaches",
    designHint: "Explore feasible architectures and offer 1–3 alternatives.",
  },
  ja: {
    title: "方式の検討",
    direct: "一つの方式",
    directHint: "他の構成と比較せず、要件に沿った一つの実装案を作成します。",
    design: "方式を比較",
    designHint: "実現可能な構成を検討し、1～3 案を提示します。",
  },
};

export function AnalysisModePicker({ locale, value, onChange, disabled = false }: {
  locale: Locale;
  value: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
  disabled?: boolean;
}) {
  const group = useId();
  const text = labels[locale];
  return (
    <fieldset className="analysis-mode-picker" disabled={disabled}>
      <legend>{text.title}</legend>
      {(["direct", "design"] as const).map((mode) => (
        <label key={mode} className={value === mode ? "selected" : ""}>
          <input type="radio" name={group} value={mode} checked={value === mode}
            onChange={() => onChange(mode)} aria-describedby={`${group}-${mode}-hint`} />
          <span>
            <strong>{text[mode]}</strong>
            <small id={`${group}-${mode}-hint`}>{mode === "direct" ? text.directHint : text.designHint}</small>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
