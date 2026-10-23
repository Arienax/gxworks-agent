import { X } from "lucide-react";
import type { CSSProperties } from "react";
import { lazy, Suspense, useRef } from "react";
import { RetainedPanel } from "./components/RetainedPanel";
import { AgentConversation, agentConversationFields } from "./workbench/AgentConversation";
import { CanvasPanels, canvasPanelFields } from "./workbench/CanvasPanels";
import { ComposerPanel, composerPanelFields } from "./workbench/ComposerPanel";
import { ConversationHistory, conversationHistoryFields } from "./workbench/ConversationHistory";
import { LoginScreen, loginScreenFields } from "./workbench/LoginScreen";
import { ProgramWorkspace, programWorkspaceFields } from "./workbench/ProgramWorkspace";
import { ProjectSidebar, projectSidebarFields } from "./workbench/ProjectSidebar";
import { ProposalList, proposalListFields } from "./workbench/ProposalList";
import { workbenchDialogsFields } from "./workbench/dialogFields";
import { clampCanvasWidth } from "./workbench/usePanelResize";
import { useWorkbenchController } from "./workbench/useWorkbenchController";
import { pickView } from "./workbench/viewProps";

const WorkbenchDialogs = lazy(() => import("./workbench/WorkbenchDialogs").then((module) => ({ default: module.WorkbenchDialogs })));

export default function App() {
  const view = useWorkbenchController();
  const dialogsOpened = useRef(false);
  if (view.modal || view.gxSend) dialogsOpened.current = true;
  if (!view.session) return <LoginScreen {...pickView(view, loginScreenFields)} />;
  const canvasVisible = view.canvasOpen && !!view.pid;
  return (
    <div
      className={`workbench ${view.leftOpen ? "" : "sidebar-hidden"} ${canvasVisible ? "canvas-open" : ""}`}
      style={{ "--canvas-width": `${view.rightWidth}px` } as CSSProperties}
      onKeyDown={(event) => {
        if (event.key === "Escape" && view.leftOpen && window.innerWidth <= 1100) {
          view.setLeftOpen(false);
          requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(".sidebar-toggle")?.focus());
        }
      }}
    >
      <ProjectSidebar {...pickView(view, projectSidebarFields)} />
      {view.leftOpen && <div className="sidebar-scrim" aria-hidden="true" onClick={() => view.setLeftOpen(false)} />}
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
        onPointerDown={view.resizePanel}
        onKeyDown={(e) => {
          if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
          e.preventDefault();
          if (e.key === "ArrowLeft") view.setRightWidth((v) => clampCanvasWidth(v + 20, view.leftOpen));
          if (e.key === "ArrowRight") view.setRightWidth((v) => clampCanvasWidth(v - 20, view.leftOpen));
        }}
      />
      <RetainedPanel active={canvasVisible}>
        {() => <ProgramWorkspace {...pickView(view, programWorkspaceFields)}
          contextView={<CanvasPanels {...pickView(view, canvasPanelFields)}
            proposalsView={<ProposalList {...pickView(view, proposalListFields)} />} />} />}
      </RetainedPanel>
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
