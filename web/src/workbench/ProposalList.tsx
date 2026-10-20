import {
  Check,
  ChevronRight,
  GitBranch,
  ShieldCheck
} from "lucide-react";
import { Suspense, lazy } from "react";
import { Button } from "../components/ui";
import { ConditionNormalization } from "../features/ConditionNormalization";
import { DataView, DiffView } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";

const NativeEngineeringPlan = lazy(() =>
  import("../features/NativeEngineeringPlan").then((module) => ({
    default: module.NativeEngineeringPlan,
  })),
);
export const proposalListFields = [
  "canWrite",
  "decide",
  "guarded",
  "preview",
  "proposals",
  "selectedProposal",
  "showProposal",
  "status",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof proposalListFields[number]>;
export function ProposalList(props: Props) {
  const {
    canWrite,
    decide,
    guarded,
    preview,
    proposals,
    selectedProposal,
    showProposal,
    status,
    t,
  } = props;

  return (
    <div className="proposal-list">
      {proposals.some((p) => p.action !== "accept_local" || p.status === "pending") ? (
        proposals
          .filter((p) => p.action !== "accept_local" || p.status === "pending")
          .map((p) => (
            <section
              className={`proposal-card ${selectedProposal?.id === p.id ? "selected" : ""}`}
              key={p.id}
            >
              <div className="job-line">
                <GitBranch size={15} />
                <strong>
                  {p.action === "accept_local"
                    ? t("保存候选")
                    : p.action === "native_engineering"
                      ? t("在 GX Works2 中编辑工程")
                      : p.action}
                </strong>
                {status(p.status)}
              </div>
              <p>{String(p.summary?.summary || "")}</p>
              <div className="proposal-meta">
                {t("基础版本")}{" "}
                <span className="mono">{p.base_version_id || t("首次生成")}</span>
              </div>
              <Button onClick={() => void guarded(() => showProposal(p))}>
                {t("预览与差异")}
                <ChevronRight size={14} />
              </Button>
              {!!p.summary?.diff && (
                <details>
                  <summary>{t("差异数据")}</summary>
                  <DataView value={p.summary.diff} />
                </details>
              )}
              {!!p.summary?.impact && (
                <div className="candidate-diff">
                  <strong>{t("变更影响")}</strong>
                  <DataView value={p.summary.impact} />
                  {!!p.summary.change_scope && (
                    <>
                      <strong>{t("修改范围")}</strong>
                      <DataView value={p.summary.change_scope} />
                    </>
                  )}
                </div>
              )}
              {selectedProposal?.id === p.id && !!preview?.diff && (
                <DiffView value={preview.diff} t={t} />
              )}
              {selectedProposal?.id === p.id && !!preview?.action && (
                <div className="candidate-diff">
                  <p>
                    {t("审批绑定版本")} <strong>{String(preview.version_id || "")}</strong>
                  </p>
                  {!!preview.plan && (
                    <>
                      <h3>{t("指定执行方案")}</h3>
                      {preview.action === "native_engineering" ? (
                        <Suspense fallback={<p role="status">{t("正在读取…")}</p>}>
                          <NativeEngineeringPlan value={preview.plan} t={t} />
                        </Suspense>
                      ) : (
                        <DataView value={preview.plan} />
                      )}
                    </>
                  )}
                </div>
              )}
              <ConditionNormalization value={p.summary?.normalization} t={t} />
              {!!p.summary?.validation && <DataView value={p.summary.validation} />}
              <div className="proposal-actions">
                {p.status === "pending" && (
                  <>
                    <Button
                      variant="ghost"
                      disabled={!canWrite}
                      onClick={() => void guarded(() => decide(p, "reject"))}
                    >
                      {t("拒绝")}
                    </Button>
                    <Button
                      variant="primary"
                      disabled={!canWrite || selectedProposal?.id !== p.id}
                      title={t("预览与差异")}
                      onClick={() => void guarded(() => decide(p, "accept"))}
                    >
                      <Check size={14} />
                      {t(p.action === "accept_local" ? "保存候选" : "批准执行")}
                    </Button>
                  </>
                )}
              </div>
              {p.result && (
                <div className="execution-result">
                  {t("执行结果")} {status(String(p.result.status || p.status))}
                  <DataView value={p.result} />
                </div>
              )}
            </section>
          ))
      ) : (
        <div className="panel-empty">
          <ShieldCheck size={28} />
          <p>{t("暂无待审批提案")}</p>
        </div>
      )}
    </div>
  );
}
