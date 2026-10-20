import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { DataView } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";

const SpecEditor = lazy(() => import("../features/SpecEditor").then((module) => ({ default: module.SpecEditor })));
export const agentSidebarFields = [
  "canWrite",
  "changeSpec",
  "guarded",
  "network",
  "panel",
  "pendingCount",
  "pid",
  "preview",
  "saveSpec",
  "setPanel",
  "spec",
  "specIssues",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof agentSidebarFields[number]> & { conversation: ReactNode; proposalsView: ReactNode };
export function AgentSidebar(props: Props) {
  const { conversation, proposalsView } = props;
  const {
    canWrite,
    changeSpec,
    guarded,
    network,
    panel,
    pendingCount,
    pid,
    preview,
    saveSpec,
    setPanel,
    spec,
    specIssues,
    t,
  } = props;

  return (
    <aside className="agent-sidebar">
      <nav className="panel-tabs">
        {[
          ["agent", t("对话")],
          ["spec", t("需求")],
          ["proposals", `${t("审批记录")}${pendingCount ? ` ${pendingCount}` : ""}`],
          ["inspector", t("检查器")],
        ].map(([id, label]) => (
          <button key={id} className={panel === id ? "active" : ""} onClick={() => setPanel(id)}>
            {label}
          </button>
        ))}
      </nav>
      {panel === "agent" ? (
        conversation
      ) : panel === "spec" ? (
        <Suspense fallback={<p role="status">{t("正在读取…")}</p>}><SpecEditor
          value={spec}
          issues={specIssues}
          onChange={changeSpec}
          t={t}
          disabled={!canWrite || !pid}
          onSave={(s) => void guarded(() => saveSpec(s))}
        /></Suspense>
      ) : panel === "proposals" ? (
        proposalsView
      ) : (
        <div className="inspector">
          <div className="section-label">{t("网络")}</div>
          {network ? (
            <>
              <h2 className="mono">{String(network.id)}</h2>
              <DataView value={network} />
            </>
          ) : (
            <p className="muted">{t("选择网络查看地址和引用。")}</p>
          )}
          {preview && !preview.program && <DataView value={preview} />}
        </div>
      )}
    </aside>
  );
}
