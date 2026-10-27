// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import {
  ArrowUp,
  ImagePlus,
  FileUp,
  Plus,
  ShieldCheck,
  Workflow,
  X
} from "lucide-react";
import { useRef, useState } from "react";
import { activeJob } from "../api/client";
import { Popover } from "../components/Popover";
import { Button } from "../components/ui";
import { approvalLabels } from "../features/ApprovalSettings";
import { ConversationControls } from "../features/ConversationControls";
import { ModelSwitcher } from "../features/ModelUsage";
import type { WorkbenchModel } from "./useWorkbenchController";


export const composerPanelFields = [
  "analysisMode",
  "answeringDirect",
  "approvalSettings",
  "attachments",
  "canSubmit",
  "canWrite",
  "composerRoute",
  "editAction",
  "guarded",
  "gxwImport",
  "gxwSource",
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
  "setSettings",
  "setSettingsTab",
  "setTask",
  "setText",
  "setWorkflow",
  "settings",
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
    approvalSettings,
    attachments,
    canSubmit,
    canWrite,
    composerRoute,
    editAction,
    guarded,
    gxwImport,
    gxwSource,
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
    setSettings,
    setSettingsTab,
    setTask,
    setText,
    setWorkflow,
    settings,
    submitJob,
    t,
    task,
    text,
    upload,
    version,
    workflow,
  } = props;
  const uploadRef = useRef<HTMLInputElement>(null);
  const gxwRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  return (
    <>
      {gxwSource.available && (
        <label className="gxw-source-picker">
          {t("编辑的程序")}
          <select aria-label={t("编辑的程序")} value={gxwSource.selected}
            disabled={!canWrite || gxwSource.loading || jobs.some(activeJob)}
            onChange={(event) => gxwSource.select(event.target.value)}>
            {(gxwSource.view?.programs || []).map((source) => (
              <option key={source.source_name} value={source.source_name}>
                {source.source_name} · {source.language?.toUpperCase()} · {source.unit_kind}
              </option>
            ))}
          </select>
          {gxwSource.loading && <small role="status">{t("正在读取…")}</small>}
        </label>
      )}
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
        className={`composer${dragging ? " gxw-dragging" : ""}`}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
            event.dataTransfer.dropEffect = canWrite ? "copy" : "none";
          }
        }}
        onDragEnter={(event) => {
          if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
            dragDepth.current++;
            if (canWrite) setDragging(true);
          }
        }}
        onDragLeave={(event) => {
          if (event.dataTransfer.types.includes("Files")) {
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (!dragDepth.current) setDragging(false);
          }
        }}
        onDrop={(event) => {
          event.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          if (canWrite) void gxwImport.open(event.dataTransfer.files);
        }}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSubmit) {
            event.preventDefault();
            void guarded(() => submitJob());
          }
        }}
      >
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
        {composerRoute.requiresText ? (
          <textarea
            rows={2}
            aria-label={t(answeringDirect ? "补充说明（选填）" : composerRoute.placeholder)}
            placeholder={t(answeringDirect ? "补充说明（选填）" : composerRoute.placeholder)}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <p className="composer-ready">{t(composerRoute.placeholder)}</p>
        )}
        <input ref={gxwRef} type="file" hidden accept=".gxw"
          aria-label={t("选择 GXW 工程")}
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            event.target.value = "";
            void gxwImport.open(files);
          }} />
        <input
          ref={uploadRef}
          type="file"
          hidden
          multiple
          accept="image/png,image/jpeg,image/gif,image/webp"
          onChange={(e) => void guarded(() => upload(e.target.files))}
        />
        <div className="composer-actions">
          <Popover className="composer-menu" summaryClassName="composer-icon" label={t("添加")} title={t("添加")}
            summary={<Plus size={17} />}>
            <Button variant="ghost" disabled={!canWrite} aria-label={t("导入 GXW")}
              onClick={() => gxwRef.current?.click()}>
              <FileUp size={15} />{t("导入 GXW")}
            </Button>
            <Button
              variant="ghost"
              aria-label={t("添加图片")}
              disabled={!canWrite || !pid || task === "question" || !composerRoute.requiresText}
              onClick={() => uploadRef.current?.click()}
            >
              <ImagePlus size={15} />{t("添加图片")}
            </Button>
            <Button variant="ghost" disabled={!canWrite || task === "question"} onClick={() => setModal("sfc")}>
              <Workflow size={15} />{t("流程步骤")}
            </Button>
          </Popover>
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
          <span className="spacer" />
          <ModelSwitcher
            compact
            settings={settings}
            job={jobs.find(activeJob)}
            disabled={!canWrite}
            onChange={setSettings}
            onSettings={() => {
              setSettingsTab("model");
              setModal("settings");
            }}
            t={t}
          />
          <button
            className={`approval-mode-indicator ${approvalSettings?.mode === "full" ? "full-access" : ""}`}
            aria-label={t(approvalSettings ? approvalLabels[approvalSettings.mode] : "审批设置")}
            title={`${t(approvalSettings ? approvalLabels[approvalSettings.mode] : "审批设置")} · ${t("在设置中调整审批模式")}`}
            onClick={() => {
              setSettingsTab("general");
              setModal("settings");
            }}
          >
            <ShieldCheck size={13} />
            <span>{t(approvalSettings ? approvalLabels[approvalSettings.mode] : "审批设置")}</span>
          </button>
          <Button
            variant="primary"
            className="composer-send"
            title={`${t(composerRoute.submitLabel)} · Ctrl ↵`}
            disabled={!canSubmit}
            onClick={() => void guarded(() => submitJob())}
          >
            <ArrowUp size={15} />
            <span>{t(composerRoute.submitLabel)}</span>
          </Button>
        </div>
      </div>
      <p className="gxw-drop-hint">
        {t(dragging ? "松开以检查 GXW 工程" : "可将 GXW 工程拖到这里，或选择文件导入。")}
        <span className="muted"> · Ctrl ↵</span>
      </p>
    </>
  );
}
