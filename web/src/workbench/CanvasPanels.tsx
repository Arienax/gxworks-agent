import type { ReactNode } from "react";
import { lazy, Suspense } from "react";
import { DataView } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";

const SpecEditor = lazy(() => import("../features/SpecEditor").then((module) => ({ default: module.SpecEditor })));

/** Canvas views that are not program documents: requirement, approvals and inspector. */
export const canvasPanelFields = [
  "canWrite",
  "changeSpec",
  "guarded",
  "network",
  "panel",
  "pid",
  "preview",
  "saveSpec",
  "spec",
  "specIssues",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof canvasPanelFields[number]> & { proposalsView: ReactNode };
export function CanvasPanels(props: Props) {
  const {
    canWrite,
    changeSpec,
    guarded,
    network,
    panel,
    pid,
    preview,
    proposalsView,
    saveSpec,
    spec,
    specIssues,
    t,
  } = props;

  if (panel === "spec")
    return (
      <div className="canvas-document">
        <Suspense fallback={<p role="status">{t("正在读取…")}</p>}><SpecEditor
          value={spec}
          issues={specIssues}
          onChange={changeSpec}
          t={t}
          disabled={!canWrite || !pid}
          onSave={(s) => void guarded(() => saveSpec(s))}
        /></Suspense>
      </div>
    );
  if (panel === "proposals") return <div className="canvas-document">{proposalsView}</div>;
  return (
    <div className="canvas-document">
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
    </div>
  );
}
