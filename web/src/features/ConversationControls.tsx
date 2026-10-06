import { AnalysisModePicker } from "./AnalysisModePicker";
import type { AnalysisMode } from "./AnalysisModePicker";
import type { CreationWorkflow, GenerationAction, TaskPurpose } from "./conversationRouting";
import type { Locale } from "../i18n";

export function ConversationControls({ task, onTask, workflow, onWorkflow, editAction, onEditAction,
  analysisMode, onAnalysisMode, hasVersions, targetMode, analyzing, confirmed, onReview, disabled, locale, t }: {
  task: TaskPurpose; onTask: (value: TaskPurpose) => void;
  workflow: CreationWorkflow; onWorkflow: (value: CreationWorkflow) => void;
  editAction: GenerationAction; onEditAction: (value: GenerationAction) => void;
  analysisMode: AnalysisMode; onAnalysisMode: (value: AnalysisMode) => void;
  hasVersions: boolean; targetMode: string; analyzing: boolean; confirmed: boolean;
  onReview: () => void; disabled: boolean; locale: Locale; t: (value: string) => string;
}) {
  return <div className="conversation-controls">
    <label className="composer-select">{t("任务目的")}
      <select aria-label={t("任务目的")} value={task} disabled={disabled}
        onChange={event => onTask(event.target.value as TaskPurpose)}>
        <option value="create" disabled={hasVersions}>{t("创建程序")}</option>
        <option value="edit" disabled={!hasVersions}>{t("修改程序")}</option>
        <option value="question">{t("工程问答")}</option>
      </select>
    </label>
    {task === "create" && targetMode === "ladder" && <label className="composer-select">{t("创建流程")}
      <select aria-label={t("创建流程")} value={workflow} disabled={disabled}
        onChange={event => onWorkflow(event.target.value as CreationWorkflow)}>
        <option value="direct">{t("直接生成")}</option>
        <option value="review">{t("先确认规格")}</option>
      </select>
    </label>}
    {task === "edit" && <label className="composer-select">{t("修改方式")}
      <select aria-label={t("修改方式")} value={editAction} disabled={disabled}
        onChange={event => onEditAction(event.target.value as GenerationAction)}>
        <option value="edit">{t("修改当前版本")}</option>
        {targetMode === "ladder" && <option value="regenerate">{t("按规格重新生成")}</option>}
      </select>
    </label>}
    {analyzing && <AnalysisModePicker locale={locale} value={analysisMode} onChange={onAnalysisMode} disabled={disabled} />}
    {confirmed && task !== "question" && (task === "create" || editAction === "regenerate") &&
      <button className="text-button" disabled={disabled} onClick={onReview}>{t("重新核对规格")}</button>}
  </div>;
}
