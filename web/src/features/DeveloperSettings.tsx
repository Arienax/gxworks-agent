// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

export function DeveloperSettingsPanel({
  constructionExamples,
  freshConfirmedGeneration,
  onConstructionExamplesChange,
  onFreshConfirmedGenerationChange,
  disabled,
  t,
}: {
  constructionExamples: boolean;
  freshConfirmedGeneration: boolean;
  onConstructionExamplesChange: (value: boolean) => void;
  onFreshConfirmedGenerationChange: (value: boolean) => void;
  disabled: boolean;
  t: (key: string) => string;
}) {
  return <section className="approval-settings">
    <h3>{t("开发者选项")}</h3>
    <fieldset disabled={disabled}>
      <legend className="sr-only">{t("Agent B 构造范例")}</legend>
      <label className={`approval-choice ${!constructionExamples ? "selected" : ""}`}>
        <input
          type="radio"
          name="construction-examples"
          checked={!constructionExamples}
          onChange={() => onConstructionExamplesChange(false)}
        />
        <span>
          <strong>{t("关闭构造范例")}</strong>
        </span>
      </label>
      <label className={`approval-choice ${constructionExamples ? "selected" : ""}`}>
        <input
          type="radio"
          name="construction-examples"
          checked={constructionExamples}
          onChange={() => onConstructionExamplesChange(true)}
        />
        <span>
          <strong>{t("开启构造范例")}</strong>
          <small>FX3U · basic_construction/1</small>
        </span>
      </label>
    </fieldset>
    <fieldset disabled={disabled}>
      <legend className="sr-only">{t("A/B 重跑模式")}</legend>
      <label className={`approval-choice ${!freshConfirmedGeneration ? "selected" : ""}`}>
        <input
          type="radio"
          name="fresh-confirmed-generation"
          checked={!freshConfirmedGeneration}
          onChange={() => onFreshConfirmedGenerationChange(false)}
        />
        <span>
          <strong>{t("普通生成/编辑")}</strong>
        </span>
      </label>
      <label className={`approval-choice ${freshConfirmedGeneration ? "selected" : ""}`}>
        <input
          type="radio"
          name="fresh-confirmed-generation"
          checked={freshConfirmedGeneration}
          onChange={() => onFreshConfirmedGenerationChange(true)}
        />
        <span>
          <strong>{t("从确认规格重新生成（A/B 重跑）")}</strong>
        </span>
      </label>
    </fieldset>
  </section>;
}
