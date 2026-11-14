// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import {
  Activity,
  ArrowLeft,
  ClipboardCheck,
  Code2,
  FileCheck2,
  FolderOpen,
  GitBranch,
  History,
  ListChecks,
  Network,
  PackageCheck,
  PanelRightClose,
  ScanSearch,
  ShieldCheck,
  Workflow
} from "lucide-react";
import type { ReactNode } from "react";
import { Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Proposal } from "../api/client";
import { activeJob, api, key } from "../api/client";
import { RetainedPanel } from "../components/RetainedPanel";
import { Button } from "../components/ui";
import { ProjectToolbar } from "../features/ProjectToolbar";
import { EditorContent } from "../workbench/EditorContent";
import type { WorkbenchModel } from "./useWorkbenchController";
import { VersionHistory } from "./VersionHistory";

const DOCUMENT_VIEWS = ["diagnostics", "reports", "simulation", "delivery"];

export const programWorkspaceFields = [
  "busy",
  "canWrite",
  "clearPreview",
  "currentJob",
  "deleteProject",
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
  "connection",
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
    deleteProject,
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
    connection,
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
  const nav = useRef<HTMLElement | null>(null);
  const activeTab = useRef<HTMLButtonElement | null>(null);
  const [slide, setSlide] = useState<{ left: number; width: number } | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [historyProjectId, setHistoryProjectId] = useState("");
  // The active pill slides inside its own group; groups never overlap, so a
  // single indicator measured against the active button is enough.
  const measure = useCallback(() => {
    const button = activeTab.current;
    const bar = nav.current;
    if (button) setSlide({ left: button.offsetLeft, width: button.offsetWidth });
    if (bar) setOverflowing(bar.scrollWidth > bar.clientWidth + 1);
  }, []);
  // Re-measure whenever the active tab changes or its label swaps in, so the
  // sliding indicator always lands on the button the operator just chose.
  useLayoutEffect(() => { measure(); }, [measure, view]);
  useEffect(() => {
    const bar = nav.current;
    if (!bar || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    return () => observer.disconnect();
  }, [measure]);
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
    ],
    [
      ["delivery", t("工程交付摘要"), <PackageCheck size={14} />],
      ["spec", t("需求"), <ListChecks size={14} />],
      ["proposals", t("审批记录"), <ClipboardCheck size={14} />],
      ["inspector", t("检查器"), <ScanSearch size={14} />],
    ],
  ];
  const activeGroup = groups.findIndex((group) => group.some(([id]) => id === view));

  return (
    <main id="engineering-canvas" className="engineering-area" aria-label={t("工程画布")}>
      <header className="canvas-header">
        <div className="canvas-title-row">
          <h1>{project?.name || t("选择工程")}</h1>
          <div className="canvas-meta">
            <span>{project?.plc_model || "MITSUBISHI FX"}</span>
            {project?.id === pid ? (
              <Button className="version-history-trigger" variant="ghost" aria-label={t("版本历史")}
                title={t("版本历史")} onClick={() => setHistoryProjectId(pid)}>
                <History size={14} aria-hidden="true" />
                {vid ? <span className="mono">{vid}</span> : t("暂无版本")}
              </Button>
            ) : (
              <span>{t("暂无版本")}</span>
            )}
            {version && status(displayedVersionStatus)}
          </div>
          <button className="icon-button" aria-label={t("收起画布")} title={t("收起画布")} onClick={() => {
            setCanvasOpen(false);
            requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(".canvas-toggle")?.focus());
          }}>
            <PanelRightClose size={17} />
          </button>
        </div>
        <ProjectToolbar
          pid={pid}
          vid={vid}
          artifacts={version?.artifacts || []}
          exportable={!!version && !preview}
          diagnosticJobId={currentJob && !activeJob(currentJob) ? currentJob.id : ""}
          canRead={canWrite && !!pid && !!operations.gx_inspect}
          canSend={canWrite && !preview && !jobs.some(activeJob) && !!operations.gx_import}
          canRefresh={!!connection && !!pid && !busy && !loading && !jobs.some(activeJob)}
          canDelete={canWrite && !!pid && !jobs.some(activeJob)}
          refreshing={refreshingDrawing}
          onRead={() => void guarded(() => submitJob("gx_read"))}
          onSend={requestGXSend}
          onRefresh={() => void guarded(refreshDrawing)}
          onDelete={() => guarded(deleteProject)}
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
            </>
          }
        />
      </header>
      {project?.id === pid && historyProjectId === pid && (
        <VersionHistory project={project} viewedId={vid} canActivate={canWrite && !jobs.some(activeJob)}
          locale={locale} t={t} onClose={() => setHistoryProjectId("")}
          onView={id => {
            selectVersion(id);
            setPanel("agent");
            setTab(project.versions?.find(item => item.id === id)?.target_mode || "ladder");
            setHistoryProjectId("");
          }}
          onActivate={id => void guarded(async () => {
            await api(`/projects/${pid}/active-version`, "POST", {
              version_id: id,
              expected_active_version_id: project.active_version_id,
            });
            await reloadProjectSilently(pid);
          })} />
      )}

      <nav className="canvas-tabs" aria-label={t("工程画布")} ref={nav} data-overflow={overflowing || undefined}>
        {groups.map((group, index) => (
          <div className="canvas-tab-group" key={index}>
            {index === activeGroup && (
              <span
                className="tab-indicator"
                aria-hidden="true"
                style={{
                  opacity: slide ? 1 : 0,
                  transform: `translateX(${slide?.left ?? 0}px)`,
                  width: slide?.width ?? 0,
                }}
              />
            )}
            {group.map(([id, title, icon]) => (
              <button
                key={id}
                ref={id === view ? activeTab : undefined}
                className={view === id ? "active" : ""}
                aria-current={view === id ? "page" : undefined}
                aria-label={title}
                title={title}
                onClick={() => open(id)}
              >
                {icon}
                {view === id && <span className="tab-label">{title}</span>}
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
