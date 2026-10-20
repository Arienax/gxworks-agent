import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  Code2,
  FileCheck2,
  FileCode2,
  FolderOpen,
  GitBranch,
  PanelLeftClose,
  ShieldCheck,
  Workflow
} from "lucide-react";
import type { ReactNode } from "react";
import { Suspense } from "react";
import type { Artifact, Proposal } from "../api/client";
import { activeJob, api, artifactUrl, key } from "../api/client";
import { RetainedPanel } from "../components/RetainedPanel";
import { Button } from "../components/ui";
import { ProjectToolbar, artifactLabel } from "../features/ProjectToolbar";
import { EditorContent } from "../workbench/EditorContent";
import type { WorkbenchModel } from "./useWorkbenchController";


export const programWorkspaceFields = [
  "busy",
  "canWrite",
  "clearPreview",
  "currentJob",
  "diagnosticJobId",
  "displayedVersionStatus",
  "editorProps",
  "guarded",
  "jobs",
  "loading",
  "operations",
  "pid",
  "preview",
  "project",
  "refreshDrawing",
  "refreshingDrawing",
  "reloadProjectSilently",
  "requestGXSend",
  "selectedProposal",
  "session",
  "setLeftOpen",
  "setModal",
  "setTab",
  "showFBDProposal",
  "status",
  "submitJob",
  "t",
  "tab",
  "version",
  "vid",
] as const;
type Props = Pick<WorkbenchModel, typeof programWorkspaceFields[number]>;
export function ProgramWorkspace(props: Props) {
  const {
    busy,
    canWrite,
    clearPreview,
    currentJob,
    diagnosticJobId,
    displayedVersionStatus,
    editorProps,
    guarded,
    jobs,
    loading,
    operations,
    pid,
    preview,
    project,
    refreshDrawing,
    refreshingDrawing,
    reloadProjectSilently,
    requestGXSend,
    selectedProposal,
    session,
    setLeftOpen,
    setModal,
    setTab,
    showFBDProposal,
    status,
    submitJob,
    t,
    tab,
    version,
    vid,
  } = props;

  return (
    <main className="engineering-area">
      <div className="project-bar">
        <button className="icon-button" title={t("工程")} onClick={() => setLeftOpen((v) => !v)}>
          <PanelLeftClose size={17} />
        </button>
        <div className="project-title">
          <h1>{project?.name || t("选择工程")}</h1>
          <span>
            {project?.plc_model || "MITSUBISHI FX"}
            {vid && (
              <>
                {" "}
                <span className="separator">/</span> <span className="mono">{vid}</span>
              </>
            )}
          </span>
        </div>
        {version && status(displayedVersionStatus)}
      </div>
      <ProjectToolbar
        pid={pid}
        vid={vid}
        artifacts={version?.artifacts || []}
        exportable={!!version && !preview}
        diagnosticJobId={currentJob && !activeJob(currentJob) ? currentJob.id : ""}
        canRead={canWrite && !!pid && !!operations.gx_inspect}
        canSend={canWrite && !preview && !jobs.some(activeJob) && !!operations.gx_import}
        canRefresh={!!session && !!pid && !busy && !loading && !jobs.some(activeJob)}
        refreshing={refreshingDrawing}
        onRead={() => void guarded(() => submitJob("gx_read"))}
        onSend={requestGXSend}
        onRefresh={() => void guarded(refreshDrawing)}
        t={t}
        more={
          <>
            <Button disabled={!canWrite || !pid} onClick={() => setModal("fbd-import")}>
              <FolderOpen size={15} />
              {t("导入 GXW")}
            </Button>
            <Button
              disabled={!canWrite || !pid || !operations.gx_inspect}
              onClick={() => void guarded(() => submitJob("gx_inspect"))}
            >
              <GitBranch size={15} />
              {t("检查同步")}
            </Button>
            {operations.fbd_convert && (
              <Button
                disabled={!canWrite || !!preview}
                onClick={() =>
                  void guarded(async () => {
                    const result = await api<Proposal>("/fbd/proposals", "POST", {
                      operation: "convert",
                      project_id: pid,
                      version_id: vid,
                      request_id: key(),
                    });
                    await showFBDProposal(result);
                  })
                }
              >
                {t("转换为 FBD")}
              </Button>
            )}
            {version && vid !== project?.active_version_id && (
              <Button
                disabled={!canWrite}
                onClick={() =>
                  void guarded(async () => {
                    await api(`/projects/${pid}/active-version`, "POST", {
                      version_id: vid,
                      expected_active_version_id: project?.active_version_id,
                    });
                    await reloadProjectSilently(pid);
                  })
                }
              >
                {t("设为当前版本")}
              </Button>
            )}
          </>
        }
      />

      <nav className="editor-tabs">
        {(
          [
            ["ladder", t("梯形图"), <Workflow size={15} />],
            ["fbd", "FBD", <GitBranch size={15} />],
            ["sfc", "SFC", <GitBranch size={15} />],
            ["st", "ST", <Code2 size={15} />],
            ["diagnostics", t("诊断"), <ShieldCheck size={15} />],
            ["reports", t("检查报告"), <FileCheck2 size={15} />],
            ["simulation", t("仿真记录"), <Activity size={15} />],
            ["delivery", t("工程交付摘要"), <FileCheck2 size={15} />],
          ] as [string, string, ReactNode][]
        ).map(([id, title, icon]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              if (["diagnostics", "reports", "simulation", "delivery"].includes(id)) {
                clearPreview();

              }
            }}
          >
            {icon}
            {title}
          </button>
        ))}
      </nav>
      {diagnosticJobId && (
        <div className="preview-banner" role="status">
          <ShieldCheck size={16} />
          <strong>{t("受阻候选预览（不可接受）")}</strong>
          <span>{t("未满足确认方案；不提供接受或导入操作。")}</span>
          <Button
            variant="ghost"
            onClick={() => {
              clearPreview();


            }}
          >
            {t("返回版本")}
          </Button>
        </div>
      )}
      {selectedProposal && (
        <div className="preview-banner">
          <GitBranch size={16} />
          <strong>{t("候选预览")}</strong>
          <span>
            {t("基础版本")} {selectedProposal.base_version_id || t("首次生成")}
          </span>
          {status(selectedProposal.status)}
          <Button
            variant="ghost"
            onClick={() => {
              clearPreview();


            }}
          >
            <ArrowLeft size={14} />
            {t("返回版本")}
          </Button>
        </div>
      )}
      <div className="editor-content">
        {["ladder", "fbd", "sfc", "st", "diagnostics", "reports", "simulation", "delivery"].map(
          (id) => (
            <RetainedPanel
              key={`${pid}:${vid}:${selectedProposal?.id || diagnosticJobId || "version"}:${id}`}
              active={tab === id}
            >
              {() => (
                <Suspense fallback={<p role="status">{t("正在读取…")}</p>}>
                  {<EditorContent visibleTab={id} {...editorProps} />}
                </Suspense>
              )}
            </RetainedPanel>
          ),
        )}
      </div>
      {version && !preview && (
        <footer className="artifact-footer">
          <span>
            <FileCode2 size={13} />
            {t("产物来自后端工程核心")}
          </span>
          <div>
            {(version.artifacts || [])
              .filter((a) => a.available && !["svg", "st", "st_from_ir"].includes(a.id))
              .map((a: Artifact) => (
                <a href={artifactUrl(pid, vid, a.id, true)} key={a.id}>
                  {t(artifactLabel(a.id))} <ArrowDownToLine size={12} />
                </a>
              ))}
          </div>
        </footer>
      )}
    </main>
  );
}
