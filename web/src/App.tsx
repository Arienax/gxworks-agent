import { X } from "lucide-react";
import type { CSSProperties } from "react";
import { lazy, Suspense, useRef } from "react";
import { AgentConversation, agentConversationFields } from "./workbench/AgentConversation";
import { AgentSidebar, agentSidebarFields } from "./workbench/AgentSidebar";
import { ComposerPanel, composerPanelFields } from "./workbench/ComposerPanel";
import { ConversationHistory, conversationHistoryFields } from "./workbench/ConversationHistory";
import { LoginScreen, loginScreenFields } from "./workbench/LoginScreen";
import { ProgramWorkspace, programWorkspaceFields } from "./workbench/ProgramWorkspace";
import { ProjectSidebar, projectSidebarFields } from "./workbench/ProjectSidebar";
import { ProposalList, proposalListFields } from "./workbench/ProposalList";
import { TaskDock, taskDockFields } from "./workbench/TaskDock";
import { WorkbenchHeader, workbenchHeaderFields } from "./workbench/WorkbenchHeader";
import { workbenchDialogsFields } from "./workbench/dialogFields";
import { useWorkbenchController } from "./workbench/useWorkbenchController";
import { pickView } from "./workbench/viewProps";

const WorkbenchDialogs = lazy(() => import("./workbench/WorkbenchDialogs").then((module) => ({ default: module.WorkbenchDialogs })));

export default function App() {
  const view = useWorkbenchController();
  const dialogsOpened = useRef(false);
  if (view.modal || view.gxSend) dialogsOpened.current = true;
  if (!view.session) return <LoginScreen {...pickView(view, loginScreenFields)} />;
  return (
    <div className={`workbench ${view.leftOpen ? "" : "sidebar-hidden"}`} style={{ "--inspector-width": `${view.rightWidth}px` } as CSSProperties}>
      <WorkbenchHeader {...pickView(view, workbenchHeaderFields)} />
      <ProjectSidebar {...pickView(view, projectSidebarFields)} />
      <ProgramWorkspace {...pickView(view, programWorkspaceFields)} />
      <div
        role="separator"
        aria-label={view.t("检查器")}
        aria-orientation="vertical"
        tabIndex={0}
        className="panel-resizer"
        onPointerDown={view.resizePanel}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") view.setRightWidth((v) => Math.min(650, v + 20));
          if (e.key === "ArrowRight") view.setRightWidth((v) => Math.max(300, v - 20));
        }}
      />
      <AgentSidebar {...pickView(view, agentSidebarFields)}
        conversation={<AgentConversation {...pickView(view, agentConversationFields)}
          history={<ConversationHistory {...pickView(view, conversationHistoryFields)} />}
          composer={<ComposerPanel {...pickView(view, composerPanelFields)} />} />}
        proposalsView={<ProposalList {...pickView(view, proposalListFields)} />} />
      <TaskDock {...pickView(view, taskDockFields)} />
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
