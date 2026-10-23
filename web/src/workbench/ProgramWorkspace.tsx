import {
  Activity,
  ArrowLeft,
  ClipboardCheck,
  Code2,
  FileCheck2,
  FolderOpen,
  GitBranch,
  ListChecks,
  Network,
  PackageCheck,
  PanelRightClose,
  ScanSearch,
  ShieldCheck,
  Workflow
} from "lucide-react";
import type { ReactNode } from "react";
import { Suspense } from "react";
import type { Proposal } from "../api/client";
import { activeJob, api, key } from "../api/client";
import { RetainedPanel } from "../components/RetainedPanel";
import { Button } from "../components/ui";
import { ProjectToolbar } from "../features/ProjectToolbar";
import { EditorContent } from "../workbench/EditorContent";
import type { WorkbenchModel } from "./useWorkbenchController";

const DOCUMENT_VIEWS = ["diagnostics", "reports", "simulation", "delivery"];

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
  "locale",
  "operations",
  "panel",
  "pendingCount",
  "pid",
  "preview",
  "project",
  "refreshDrawing",
  "refreshingDrawing",
  "reloadProjectSilently",
  "requestGXSend",
  "selectVersion",
  "selectedProposal",
  "session",
  "setCanvasOpen",
  "setModal",
  "setPanel",
  "setTab",
  "showFBDProposal",
  "status",
  "submitJob",
  "t",
  "tab",
  "version",
  "vid",
] as const;
type Props = Pick<WorkbenchModel, typeof programWorkspaceFields[number]> & { contextView: ReactNode };
/** The engineering canvas: program documents, verification records and context views. */
export function ProgramWorkspace(props: Props) {
  const {
    busy,
    canWrite,
    clearPreview,
    contextView,
    currentJob,
    diagnosticJobId,
    displayedVersionStatus,
    editorProps,
    guarded,
    jobs,
    loading,
    locale,
    operations,
    panel,
    pendingCount,
    pid,
    preview,
    project,
    refreshDrawing,
    refreshingDrawing,
    reloadProjectSilently,
    requestGXSend,
    selectVersion,
    selectedProposal,
    session,
    setCanvasOpen,
    setModal,
    setPanel,
    setTab,
    showFBDProposal,
    status,
    submitJob,
    t,
    tab,
    version,
    vid,
  } = props;
  // The selected GXW program and candidate may use a different language from
  // the project's original target. Present the document that is actually open.
  const programView = preview?.target_mode || editorProps.version?.target_mode || project?.target_mode || "ladder";
  const programViews = [programView, ...DOCUMENT_VIEWS];
  const view = panel !== "agent" ? panel : DOCUMENT_VIEWS.includes(tab) ? tab : programView;
  const programLabels: Record<string, [string, ReactNode]> = {
    ladder: [t("梯形图"), <Workflow size={14} />],
    fbd: ["FBD", <GitBranch size={14} />],
    sfc: ["SFC", <Network size={14} />],
    st: ["ST", <Code2 size={14} />],
  };
  const [programTitle, programIcon] = programLabels[programView] || [programView.toUpperCase(), <Code2 size={14} />];
  const versions = project?.id === pid ? project.versions?.slice().reverse() || [] : [];
  const open = (id: string) => {
    if (programViews.includes(id)) {
      setPanel("agent");
      setTab(id);
      if (DOCUMENT_VIEWS.includes(id)) clearPreview();
    } else setPanel(id);
  };
  const groups: [string, string, ReactNode][][] = [
    [[programView, programTitle, programIcon]],
    [
      ["diagnostics", t("诊断"), <ShieldCheck size={14} />],
      ["reports", t("检查报告"), <FileCheck2 size={14} />],
      ["simulation", t("仿真记录"), <Activity size={14} />],
      ["delivery", t("工程交付摘要"), <PackageCheck size={14} />],
    ],
    [
      ["spec", t("需求"), <ListChecks size={14} />],
      ["proposals", t("审批记录"), <ClipboardCheck size={14} />],
      ["inspector", t("检查器"), <ScanSearch size={14} />],
    ],
  ];

  return (
    <main id="engineering-canvas" className="engineering-area" aria-label={t("工程画布")}>
      <header className="canvas-header">
        <div className="canvas-title">
          <h1>{project?.name || t("选择工程")}</h1>
          <div className="canvas-meta">
            <span>{project?.plc_model || "MITSUBISHI FX"}</span>
            {versions.length ? (
              <select
                className="version-select mono"
                aria-label={t("版本历史")}
                title={t("版本历史")}
                value={vid}
                disabled={busy}
                onChange={(e) => selectVersion(e.target.value)}
              >
                {!versions.some((v) => v.id === vid) && <option value={vid}>{vid}</option>}
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id} ·{" "}
                    {new Date(v.created_at || "").toLocaleString(locale, {
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {v.id === project?.active_version_id ? ` · ${t("当前版本")}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <span>{t("暂无版本")}</span>
            )}
            {version && status(displayedVersionStatus)}
          </div>
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
        <button className="icon-button" aria-label={t("收起画布")} title={t("收起画布")} onClick={() => {
          setCanvasOpen(false);
          requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(".canvas-toggle")?.focus());
        }}>
          <PanelRightClose size={17} />
        </button>
      </header>

      <nav className="canvas-tabs" aria-label={t("工程画布")}>
        {groups.map((group, index) => (
          <div className="canvas-tab-group" key={index}>
            {group.map(([id, title, icon]) => (
              <button
                key={id}
                className={view === id ? "active" : ""}
                aria-current={view === id ? "page" : undefined}
                aria-label={title}
                title={title}
                onClick={() => open(id)}
              >
                {icon}
                <span>{title}</span>
                {id === "proposals" && !!pendingCount && <span className="tab-count">{pendingCount}</span>}
              </button>
            ))}
          </div>
        ))}
      </nav>
      {diagnosticJobId && (
        <div className="preview-banner" role="status">
          <ShieldCheck size={16} />
          <strong>{t("受阻候选预览（不可接受）")}</strong>
          <span>{t("未满足确认方案；不提供接受或导入操作。")}</span>
          <Button variant="ghost" onClick={() => clearPreview()}>
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
          <Button variant="ghost" onClick={() => clearPreview()}>
            <ArrowLeft size={14} />
            {t("返回版本")}
          </Button>
        </div>
      )}
      <div className="editor-content">
        {programViews.map((id) => (
          <RetainedPanel
            key={`${pid}:${vid}:${selectedProposal?.id || diagnosticJobId || "version"}:${id}`}
            active={view === id}
          >
            {() => (
              <Suspense fallback={<p role="status">{t("正在读取…")}</p>}>
                {<EditorContent visibleTab={id} {...editorProps} />}
              </Suspense>
            )}
          </RetainedPanel>
        ))}
        {panel !== "agent" && contextView}
      </div>
    </main>
  );
}
