// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { X } from "lucide-react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { RetainedPanel } from "./components/RetainedPanel";
import { AgentConversation, agentConversationFields } from "./workbench/AgentConversation";
import { CanvasPanels, canvasPanelFields } from "./workbench/CanvasPanels";
import { ComposerPanel, composerPanelFields } from "./workbench/ComposerPanel";
import { ConversationHistory, conversationHistoryFields } from "./workbench/ConversationHistory";
import { ProgramWorkspace, programWorkspaceFields } from "./workbench/ProgramWorkspace";
import { ProjectSidebar, projectSidebarFields } from "./workbench/ProjectSidebar";
import { ProposalList, proposalListFields } from "./workbench/ProposalList";
import { workbenchDialogsFields } from "./workbench/dialogFields";
import { clampCanvasWidth } from "./workbench/usePanelResize";
import { usePanelTransition } from "./workbench/usePanelTransition";
import { useWorkbenchController } from "./workbench/useWorkbenchController";
import type { Job } from "./api/client";
import { useConversations } from "./workbench/useConversations";
import { pickView } from "./workbench/viewProps";

const WorkbenchDialogs = lazy(() => import("./workbench/WorkbenchDialogs").then((module) => ({ default: module.WorkbenchDialogs })));

/** The sidebar column is a layout constant shared with the canvas clamp. */
const SIDEBAR_WIDTH = 268;
const MIN_CONVERSATION = 360;

export default function App() {
  const view = useWorkbenchController();
  // The service may start without a workspace; the picker then owns the screen.
  const workspace = view.workspaceBinding;
  const conversations = useConversations(view.connection, workspace.busy);
  const pendingJob = useRef<Job | null>(null);
  const openConversation = (job: Job) => {
    if (view.busy) return;
    if (job.project_id && job.project_id !== view.pid) {
      const projectId = job.project_id;
      void view.guarded(async () => {
        pendingJob.current = job;
        let selected = false;
        try { selected = await view.selectProject(projectId); }
        finally { if (!selected && pendingJob.current === job) pendingJob.current = null; }
      });
      return;
    }
    view.selectConversation(job);
  };
  useEffect(() => {
    const pending = pendingJob.current;
    if (pending && pending.project_id === view.pid) {
      pendingJob.current = null;
      view.selectConversation(pending);
    }
  }, [view.pid, view.selectConversation]);
  const dialogsOpened = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const [resizing, setResizing] = useState(false);
  const [nudging, setNudging] = useState(false);
  const nudgeTimer = useRef(0);
  const [overlay, setOverlay] = useState(false);
  const [noMotion, setNoMotion] = useState(true);
  const canvasVisible = view.canvasOpen && !!view.pid;
  const sidebarPhase = usePanelTransition(view.leftOpen);
  const canvasPhase = usePanelTransition(canvasVisible);
  const wasLeftOpen = useRef(view.leftOpen);

  // The first paint must not animate a restored layout.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setNoMotion(false));
    return () => cancelAnimationFrame(frame);
  }, []);

  // The canvas turns into an overlay before it squeezes the conversation.
  useEffect(() => {
    const measure = () => {
      const sidebar = view.leftOpen && window.innerWidth > 1100 ? SIDEBAR_WIDTH : 0;
      const splitter = canvasVisible ? 5 : 0;
      const conversation = window.innerWidth - sidebar - splitter - view.rightWidth;
      setOverlay(window.innerWidth <= 860 || (canvasVisible && conversation < MIN_CONVERSATION));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [view.leftOpen, view.rightWidth, canvasVisible]);

  // A collapsed sidebar leaves the tab order; send focus to its trigger instead.
  useEffect(() => {
    if (wasLeftOpen.current === view.leftOpen) return;
    wasLeftOpen.current = view.leftOpen;
    const frame = requestAnimationFrame(() => {
      const selector = view.leftOpen ? ".project-sidebar .sidebar-collapse" : ".sidebar-toggle";
      document.querySelector<HTMLElement>(selector)?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [view.leftOpen]);

  if (!view.connection) {
    return (
      <p className="panel-empty" role={view.connecting ? "status" : "alert"}>
        {view.t(view.connecting ? "正在连接本地服务" : "无法连接本地服务，请确认工作台仍在运行。")}
      </p>
    );
  }
  if (view.modal || view.gxSend) dialogsOpened.current = true;
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    setResizing(true);
    const stop = () => {
      setResizing(false);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    view.resizePanel(event);
  };
  return (
    <div
      ref={root}
      className={[
        "workbench",
        view.leftOpen ? "" : "sidebar-hidden",
        canvasVisible ? "canvas-open" : "",
        overlay ? "canvas-overlay" : "",
        resizing ? "is-resizing" : "",
        nudging ? "is-nudging" : "",
        noMotion ? "no-motion" : "",
      ].filter(Boolean).join(" ")}
      data-sidebar={sidebarPhase}
      data-canvas={canvasPhase}
      style={{ "--sidebar-width": `${SIDEBAR_WIDTH}px`, "--canvas-width": `${view.rightWidth}px` } as CSSProperties}
      onKeyDown={(event) => {
        if (event.key === "Escape" && view.leftOpen && window.innerWidth <= 1100) view.setLeftOpen(false);
      }}
    >
      <div className="sidebar-host" inert={sidebarPhase === "closed" || undefined}>
        <ProjectSidebar {...pickView(view, projectSidebarFields)}
          conversations={conversations} onOpenConversation={openConversation}
          onOpenProject={(id) => void view.guarded(async () => { await view.selectProject(id); })}
          workspaces={workspace.workspaces}
          workspaceError={workspace.error} workspacesBusy={view.busy}
          onAddWorkspace={workspace.addViaPicker}
          onActivateWorkspace={workspace.activate}
          onForgetWorkspace={workspace.forget} />
      </div>
      <div className="sidebar-scrim" aria-hidden="true" onClick={() => view.setLeftOpen(false)} />
      <AgentConversation {...pickView(view, agentConversationFields)}
        history={<ConversationHistory {...pickView(view, conversationHistoryFields)} />}
        composer={<ComposerPanel {...pickView(view, composerPanelFields)} />} />
      <div
        role="separator"
        aria-label={view.t("工程画布")}
        aria-orientation="vertical"
        aria-valuemin={clampCanvasWidth(0, view.leftOpen)}
        aria-valuemax={clampCanvasWidth(window.innerWidth, view.leftOpen)}
        aria-valuenow={view.rightWidth}
        aria-controls="engineering-canvas"
        tabIndex={canvasVisible ? 0 : -1}
        className="panel-resizer"
        onPointerDown={startResize}
        onKeyDown={(e) => {
          if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
          e.preventDefault();
          // Keyboard nudges use the quick duration; pointer drags use none.
          setNudging(true);
          window.clearTimeout(nudgeTimer.current);
          nudgeTimer.current = window.setTimeout(() => setNudging(false), 240);
          if (e.key === "ArrowLeft") view.setRightWidth((v) => clampCanvasWidth(v + 20, view.leftOpen));
          if (e.key === "ArrowRight") view.setRightWidth((v) => clampCanvasWidth(v - 20, view.leftOpen));
        }}
      />
      <div className="canvas-host" inert={canvasPhase === "closed" || undefined}>
        <RetainedPanel active={canvasVisible || canvasPhase !== "closed"}>
          {() => <ProgramWorkspace {...pickView(view, programWorkspaceFields)}
            contextView={<CanvasPanels {...pickView(view, canvasPanelFields)}
              proposalsView={<ProposalList {...pickView(view, proposalListFields)} />} />} />}
        </RetainedPanel>
      </div>
      {(view.error || view.notice) && (
        <div className={`toast ${view.error ? "toast-error" : ""}`} role={view.error ? "alert" : "status"}>
          <span>{view.error || view.notice}</span>
          <button
            className="icon-button"
            aria-label={view.t("关闭")}
            onClick={() => {
              view.setError("");
              view.setNotice("");
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {dialogsOpened.current && (
        <Suspense fallback={null}>
          <WorkbenchDialogs {...pickView(view, workbenchDialogsFields)} />
        </Suspense>
      )}
    </div>
  );
}
