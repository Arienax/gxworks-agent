// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { SelectMenu } from "../components/SelectMenu";
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
    <SelectMenu className="composer-select" label={t("任务")} value={task} disabled={disabled}
      onChange={(value) => onTask(value)} options={[
        { value: "create", label: t("创建程序"), disabled: hasVersions },
        { value: "edit", label: t("修改程序"), disabled: !hasVersions },
        { value: "question", label: t("工程问答") },
      ]} />
    {task === "create" && targetMode === "ladder" && <SelectMenu className="composer-select"
      label={t("创建方式")} value={workflow} disabled={disabled}
      onChange={(value) => onWorkflow(value)} options={[
        { value: "direct", label: t("直接生成") },
        { value: "review", label: t("先确认需求") },
      ]} />}
    {task === "edit" && <SelectMenu className="composer-select" label={t("修改方式")}
      value={editAction} disabled={disabled} onChange={(value) => onEditAction(value)}
      options={[
        { value: "edit", label: t("修改当前版本") },
        ...(targetMode === "ladder"
          ? [{ value: "regenerate" as GenerationAction, label: t("按需求重新生成") }]
          : []),
      ]} />}
    {task === "edit" && editAction === "edit" && <SelectMenu className="composer-select"
      label={t("需求确认")} value={workflow} disabled={disabled}
      onChange={(value) => onWorkflow(value)} options={[
        { value: "review", label: t("先确认变更") },
        { value: "direct", label: t("直接修改程序") },
      ]} />}
    {analyzing && <AnalysisModePicker compact locale={locale} value={analysisMode} onChange={onAnalysisMode} disabled={disabled} />}
    {confirmed && task !== "question" && (task === "create" || editAction === "regenerate") &&
      <button className="text-button" disabled={disabled} onClick={onReview}>{t("重新确认需求")}</button>}
  </div>;
}
