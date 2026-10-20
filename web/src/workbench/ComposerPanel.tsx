import {
  Paperclip,
  Send,
  Workflow,
  X
} from "lucide-react";
import { useRef } from "react";
import { activeJob } from "../api/client";
import { Button } from "../components/ui";
import { ConversationControls } from "../features/ConversationControls";
import type { WorkbenchModel } from "./useWorkbenchController";


export const composerPanelFields = [
  "analysisMode",
  "answeringDirect",
  "attachments",
  "canSubmit",
  "canWrite",
  "composerRoute",
  "editAction",
  "guarded",
  "hasSavedVersions",
  "intent",
  "jobs",
  "locale",
  "network",
  "pid",
  "project",
  "reviewRequested",
  "scopeAddresses",
  "scopeEnabled",
  "scopeNetworks",
  "setAnalysisMode",
  "setAttachments",
  "setEditAction",
  "setModal",
  "setReviewRequested",
  "setScopeAddresses",
  "setScopeEnabled",
  "setScopeNetworks",
  "setTask",
  "setText",
  "setWorkflow",
  "submitJob",
  "t",
  "task",
  "text",
  "upload",
  "version",
  "workflow",
] as const;
type Props = Pick<WorkbenchModel, typeof composerPanelFields[number]>;
export function ComposerPanel(props: Props) {
  const {
    analysisMode,
    answeringDirect,
    attachments,
    canSubmit,
    canWrite,
    composerRoute,
    editAction,
    guarded,
    hasSavedVersions,
    intent,
    jobs,
    locale,
    network,
    pid,
    project,
    reviewRequested,
    scopeAddresses,
    scopeEnabled,
    scopeNetworks,
    setAnalysisMode,
    setAttachments,
    setEditAction,
    setModal,
    setReviewRequested,
    setScopeAddresses,
    setScopeEnabled,
    setScopeNetworks,
    setTask,
    setText,
    setWorkflow,
    submitJob,
    t,
    task,
    text,
    upload,
    version,
    workflow,
  } = props;
  const uploadRef = useRef<HTMLInputElement>(null);
  return (
    <>
      {version?.target_mode === "ladder" && task === "edit" && editAction === "edit" && (
        <details className="scope-controls" open={scopeEnabled}>
          <summary>
            {t("修改范围")}
            {scopeEnabled ? ` · ${t("局部约束已启用")}` : ` · ${t("整个程序")}`}
          </summary>
          <label>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={scopeEnabled}
              onChange={(e) => setScopeEnabled(e.target.checked)}
            />{" "}
            {t("只允许修改指定范围")}
          </label>
          {scopeEnabled && (
            <>
              <label>
                {t("允许修改的网络")}
                <input
                  value={scopeNetworks}
                  onChange={(e) => setScopeNetworks(e.target.value)}
                  placeholder="N0001, N0002"
                />
              </label>
              <Button
                disabled={!network}
                onClick={() => setScopeNetworks(String(network?.id || ""))}
              >
                {t("使用选中网络")}
              </Button>
              <label>
                {t("允许涉及的地址")}
                <input
                  value={scopeAddresses}
                  onChange={(e) => setScopeAddresses(e.target.value)}
                  placeholder="X0, Y0, M0"
                />
              </label>
              <small>{t("网络和地址至少填写一项，同时填写时须同时满足。")}</small>
            </>
          )}
        </details>
      )}
      <div
        className="composer"
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSubmit) {
            event.preventDefault();
            void guarded(() => submitJob());
          }
        }}
      >
        <div className="composer-mode">
          <strong>{t("工程任务")}</strong>
          <button
            className="text-button"
            disabled={!canWrite || task === "question"}
            onClick={() => setModal("sfc")}
          >
            {t("流程步骤")} <Workflow size={13} />
          </button>
        </div>
        <ConversationControls
          task={task}
          onTask={setTask}
          workflow={workflow}
          onWorkflow={setWorkflow}
          editAction={editAction}
          onEditAction={setEditAction}
          analysisMode={analysisMode}
          onAnalysisMode={setAnalysisMode}
          hasVersions={!!hasSavedVersions}
          targetMode={project?.target_mode || "ladder"}
          analyzing={intent === "analysis"}
          confirmed={!!project?.confirmed_spec && !reviewRequested}
          onReview={() => {
            setWorkflow("review");
            setReviewRequested(true);
          }}
          disabled={!canWrite || jobs.some(activeJob)}
          locale={locale}
          t={t}
        />
        {composerRoute.requiresText && (
          <textarea
            aria-label={t(answeringDirect ? "补充说明（选填）" : composerRoute.placeholder)}
            placeholder={t(answeringDirect ? "补充说明（选填）" : composerRoute.placeholder)}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        )}
        {task !== "question" && attachments.length > 0 && (
          <div className="attachment-chips">
            {attachments.map((a) => (
              <button
                key={a.attachment_id}
                onClick={() =>
                  setAttachments((items) =>
                    items.filter((v) => v.attachment_id !== a.attachment_id),
                  )
                }
              >
                {a.filename}
                <X size={12} />
              </button>
            ))}
          </div>
        )}
        <div className="composer-actions">
          <input
            ref={uploadRef}
            type="file"
            hidden
            multiple
            accept="image/png,image/jpeg,image/gif,image/webp"
            onChange={(e) => void guarded(() => upload(e.target.files))}
          />
          <Button
            variant="ghost"
            aria-label={t("添加图片")}
            disabled={!canWrite || !pid || task === "question" || !composerRoute.requiresText}
            onClick={() => uploadRef.current?.click()}
          >
            <Paperclip size={16} />
          </Button>
          <span className="muted">Ctrl ↵</span>
          <Button
            variant="primary"
            disabled={!canSubmit}
            onClick={() => void guarded(() => submitJob())}
          >
            <Send size={14} />
            {t(composerRoute.submitLabel)}
          </Button>
        </div>
      </div>
    </>
  );
}
