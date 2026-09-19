import { useId } from "react";
import type { components } from "../api/generated";
import type { Locale } from "../i18n";
import "./AnalysisModePicker.css";

export type AnalysisMode = components["schemas"]["JobCreate"]["analysis_mode"];

const labels: Record<Locale, { title: string; direct: string; directHint: string; design: string; designHint: string }> = {
  "zh-CN": {
    title: "分析模式",
    direct: "Direct 直接实现",
    directHint: "按当前需求给出一个明确方案，不比较其他架构。",
    design: "Design 方案设计",
    designHint: "探索可行架构，给出 1～3 个方案供选择。",
  },
  en: {
    title: "Analysis mode",
    direct: "Direct implementation",
    directHint: "Produce one concrete implementation without comparing architectures.",
    design: "Design exploration",
    designHint: "Explore feasible architectures and offer 1–3 alternatives.",
  },
  ja: {
    title: "分析モード",
    direct: "Direct 直接実装",
    directHint: "他の構成と比較せず、要件に沿った一つの実装案を作成します。",
    design: "Design 方式設計",
    designHint: "実現可能な構成を検討し、1～3 案を提示します。",
  },
};

export function AnalysisModePicker({ locale, value, onChange }: {
  locale: Locale;
  value: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
}) {
  const group = useId();
  const text = labels[locale];
  return (
    <fieldset className="analysis-mode-picker">
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
