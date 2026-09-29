export function DeveloperSettingsPanel({
  constructionExamples,
  onConstructionExamplesChange,
  disabled,
  t,
}: {
  constructionExamples: boolean;
  onConstructionExamplesChange: (value: boolean) => void;
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
      <strong>{t("下一次 Web 生成的实际设置")}：{constructionExamples ? "ON" : "OFF"}</strong>
    </p>
    <p className="muted">
      {t("切换立即保存到当前浏览器，只影响之后提交的 generation job；已经提交或正在运行的任务保持其提交时快照。环境变量仍仅作为脚本或其他未显式传值入口的兼容后备。")}
    </p>
  </section>;
}
