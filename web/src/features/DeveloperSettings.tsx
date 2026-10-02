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
    <p className="muted">
      {t("这些选项用于开发与对照试验。Web 会把选择显式绑定到新提交的生成任务，不依赖后端进程环境变量。")}
    </p>
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
          <small>{t("Agent B 只接收当前确认规格、型号事实和本题检索证据。")}</small>
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
          <small>{t("Agent B 额外接收 basic_construction/1 的固定完整构造范例；当前仅适用于 FX3U。")}</small>
        </span>
      </label>
    </fieldset>
    <p role="status">
      <strong>{t("下一次 Web 生成的构造范例")}：{constructionExamples ? "ON" : "OFF"}</strong>
    </p>
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
          <small>{t("已有版本时沿用正常编辑路径。")}</small>
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
          <small>{t("忽略当前程序作为生成基线，不重新运行 Agent A；用现有确认规格再次走 fresh Agent B，并把结果保存为新版本。")}</small>
        </span>
      </label>
    </fieldset>
    <p role="status">
      <strong>{t("下一次 Web 生成的路径")}：{freshConfirmedGeneration ? "FRESH CONFIRMED" : "NORMAL"}</strong>
    </p>
    <p className="muted">
      {t("两个开发者选项都会在提交 generation job 时冻结。之后再切换不会改变已经提交或正在运行的任务；环境变量仍仅作为脚本或其他未显式传值入口的兼容后备。")}
    </p>
  </section>;
}
