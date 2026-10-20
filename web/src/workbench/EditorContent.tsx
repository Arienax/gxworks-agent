import {
  ArrowDownToLine,
  ChevronRight,
  FileCheck2,
  FileCode2,
  FolderOpen,
  GitBranch,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { lazy } from "react";
import type { Job, JobKind, Json, Project, Proposal, PreviewDocument, ProgramDocument } from "../api/client";
import { activeJob, api, artifactUrl, isFBDModel, isSFCModel, isFBDEditorView } from "../api/client";
import { Badge, Button } from "../components/ui";
import { CapabilityReview } from "../features/CapabilityReview";
import { DeliverySummary } from "../features/DeliverySummary";
import { FirstProjectGuide } from "../features/FirstProjectGuide";
import { useGenerationResult } from "../features/GenerationResult";
import type { IssueContext } from "../features/ProgramExplorer";
import { IssueCards, ProgramExplorer } from "../features/ProgramExplorer";
import type { CreationWorkflow, TaskPurpose } from "../features/conversationRouting";
import type { CreationCapabilities } from "./useCapabilities";

const SimulationWorkbench = lazy(() =>
  import("../features/SimulationWorkbench").then((module) => ({
    default: module.SimulationWorkbench,
  })),
);
const FBDPanel = lazy(() =>
  import("../features/FBDPanel").then((module) => ({ default: module.FBDPanel })),
);
const SFCPanel = lazy(() =>
  import("../features/SFCPanel").then((module) => ({ default: module.SFCPanel })),
);

export type EditorContentProps = {
  visibleTab: string;
  loading: boolean;
  project: Project | null;
  pid: string;
  t: (s: string) => string;
  canWrite: boolean;
  setModal: Dispatch<SetStateAction<string>>;
  vid: string;
  selectedProposal: Proposal | null;
  preview: PreviewDocument | null;
  version: NonNullable<Project["versions"]>[number] | undefined;
  visibleProgram: ProgramDocument | null | undefined;
  showFBDProposal: (value: Proposal) => Promise<void>;
  svg: string;
  generationResult: ReturnType<typeof useGenerationResult>;
  setPanel: Dispatch<SetStateAction<string>>;
  creation: CreationCapabilities | null;
  jobs: Job[];
  setDrafts: Dispatch<SetStateAction<Record<TaskPurpose, string>>>;
  setTask: Dispatch<SetStateAction<TaskPurpose>>;
  setWorkflow: Dispatch<SetStateAction<CreationWorkflow>>;
  setReviewRequested: Dispatch<SetStateAction<boolean>>;
  guarded: (action: () => Promise<void>) => Promise<void>;
  submitJob: (kind?: JobKind, extra?: Record<string, unknown>) => Promise<void>;
  refresh: number;
  diagnosticJobId: string;
  versionDrawing: { key: string; svg: string } | null;
  proposals: Proposal[];
  theme: "light" | "dark";
  network: Record<string, Json> | null;
  jumpVersion: string;
  jumpAddress: string;
  selectNetwork: (id: string) => void;
  st: string;
  operations: { [key: string]: boolean };
  setNetwork: Dispatch<SetStateAction<Record<string, Json> | null>>;
  networks: Record<string, Json>[];
  setJumpAddress: Dispatch<SetStateAction<string>>;
  setJumpVersion: Dispatch<SetStateAction<string>>;
  setTab: Dispatch<SetStateAction<string>>;
  setIssueContext: Dispatch<SetStateAction<(IssueContext & { versionId: string }) | undefined>>;
  setReport: Dispatch<SetStateAction<Json>>;
  status: (value?: string | null) => ReactNode;
  refreshAll: () => void;
  issueContext: (IssueContext & { versionId: string }) | undefined;
  proposeExecution: (action: string, planId?: string, versionId?: string) => Promise<void>;
};

export function EditorContent({
  visibleTab,
  loading,
  project,
  pid,
  t,
  canWrite,
  setModal,
  vid,
  selectedProposal,
  preview,
  version,
  visibleProgram,
  showFBDProposal,
  svg,
  generationResult,
  setPanel,
  creation,
  jobs,
  setDrafts,
  setTask,
  setWorkflow,
  setReviewRequested,
  guarded,
  submitJob,
  refresh,
  diagnosticJobId,
  versionDrawing,
  proposals,
  theme,
  network,
  jumpVersion,
  jumpAddress,
  selectNetwork,
  st,
  operations,
  setNetwork,
  networks,
  setJumpAddress,
  setJumpVersion,
  setTab,
  setIssueContext,
  setReport,
  status,
  refreshAll,
  issueContext,
  proposeExecution,
}: EditorContentProps) {
  return loading && project?.id !== pid ? (
    <div className="empty-state">
      <LoaderCircle size={28} className="spin" />
      <p>{t("正在读取工程")}</p>
    </div>
  ) : !project || project.id !== pid ? (
    <div className="empty-state">
      <FolderOpen size={42} />
      <h2>{t("打开本地工程")}</h2>
      <Button variant="primary" disabled={!canWrite} onClick={() => setModal("new")}>
        <Plus size={16} />
        {t("新建工程")}
      </Button>
    </div>
  ) : visibleTab === "sfc" ? (
    <SFCPanel
      key={`${pid}:${vid}:${selectedProposal?.id || "version"}`}
      value={
        preview?.target_mode === "sfc" || (!preview && version?.target_mode === "sfc")
          ? (isSFCModel(visibleProgram) ? visibleProgram : null)
          : null
      }
      pid={pid}
      vid={vid}
      readOnly={!canWrite}
      preview={!!preview}
      t={t}
      onProposal={showFBDProposal}
    />
  ) : visibleTab === "fbd" &&
    (preview?.target_mode === "fbd" ||
      (!preview &&
        (version?.target_mode === "fbd" || (!version && project.target_mode === "fbd")))) ? (
    <FBDPanel
      key={`${pid}:${vid}:${selectedProposal?.id || "version"}`}
      value={isFBDModel(visibleProgram) ? visibleProgram : null}
      svg={svg}
      pid={pid}
      vid={vid}
      readOnly={!canWrite}
      preview={!!preview}
      previewEditor={
        preview?.target_mode === "fbd" && isFBDEditorView(preview.editor) ? preview.editor : undefined
      }
      t={t}
      onProposal={showFBDProposal}
    />
  ) : visibleTab === "fbd" ? (
    <div className="empty-state">
      <GitBranch size={38} />
      <h2>{t("结构化梯形图/FBD")}</h2>
      <p>{t("导入 GXW 工程，或将当前梯形图转换为 FBD。也可以新建 FBD 工程直接生成。")}</p>
    </div>
  ) : !version && !preview ? (
    generationResult.id ? (
      <div className="empty-state">
        <Workflow size={44} />
        <h2>
          {t(
            generationResult.id
              ? generationResult.blocked
                ? "生成的程序与需求不一致"
                : generationResult.loading
                  ? "正在读取生成结果"
                  : generationResult.proposalId
                    ? "程序已保存，等待查看"
                    : "生成结果暂不可用"
              : "工程中还没有程序",
          )}
        </h2>
        {generationResult.id && (
          <Button onClick={() => setPanel("agent")}>{t("查看生成结果")}</Button>
        )}
        <Badge>
          {project.plc_model} · {project.target_mode.toUpperCase()}
        </Badge>
      </div>
    ) : (
      <FirstProjectGuide
        example={creation?.starter_requirement || ""}
        t={t}
        hasSpec={!!project.confirmed_spec}
        disabled={!canWrite || jobs.some(activeJob)}
        onExample={(value) => {
          setDrafts((before) => ({ ...before, create: value }));
          setTask("create");
          setWorkflow(project.target_mode === "ladder" ? "direct" : "review");
          setReviewRequested(false);
          setPanel("agent");
        }}
        onSpec={() => setPanel("spec")}
        onGenerate={() => void guarded(() => submitJob("generation"))}
      />
    )
  ) : visibleTab === "delivery" ? (
    <DeliverySummary
      key={`${pid}:${vid}`}
      pid={pid}
      vid={vid}
      t={t}
      refreshKey={refresh}
      readOnly={!canWrite}
    />
  ) : visibleTab === "ladder" ? (
    <ProgramExplorer
      key={`${pid}:${vid}:${selectedProposal?.id || diagnosticJobId || "version"}`}
      pid={pid}
      vid={vid}
      proposalId={preview ? selectedProposal?.id : undefined}
      jobId={preview && diagnosticJobId ? diagnosticJobId : undefined}
      refreshKey={`${refresh}:${versionDrawing?.key || ""}`}
      changeSummary={
        preview
          ? selectedProposal?.summary
          : proposals.find(
            (p) =>
              p.action === "accept_local" &&
              p.status === "accepted" &&
              p.result?.version_id === vid,
          )?.summary
      }
      theme={theme}
      selectedNetwork={String(network?.id || "")}
      initialAddress={jumpVersion === vid ? jumpAddress : ""}
      t={t}
      onNetwork={selectNetwork}
    />
  ) : visibleTab === "st" ? (
    <div className="code-view">
      <header>
        <FileCode2 size={15} />
        {preview
          ? "candidate.st"
          : version?.artifacts?.find((a) => a.id.includes("st"))?.filename || "ST"}
        <span className="spacer" />
        {!preview &&
          version?.artifacts
            ?.filter((a) => ["st", "st_from_ir"].includes(a.id) && a.available)
            .map((a) => (
              <a key={a.id} href={artifactUrl(pid, vid, a.id, true)}>
                <ArrowDownToLine size={14} />
                {t("下载")}
              </a>
            ))}
      </header>
      <pre>{String((preview ? preview.st : st) || t("此版本没有该产物"))}</pre>
    </div>
  ) : visibleTab === "diagnostics" ? (
    <div className="document-view">
      <div className="content-heading">
        <h2>{t("诊断")}</h2>
        <Button
          disabled={!canWrite || !operations.diagnose}
          onClick={() => void guarded(() => submitJob("review", { deep: false }))}
        >
          <ShieldCheck size={15} />
          {t("本地检查")}
        </Button>
      </div>
      <CapabilityReview metadata={version} t={t} />
      <IssueCards
        pid={pid}
        vid={vid}
        readOnly={!canWrite || !operations.simulation}
        t={t}
        refreshKey={refresh}
        onNetwork={(id, address) => {
          setNetwork({ ...networks.find((n) => n.id === id), id, version_id: vid });
          setJumpAddress(address || "");
          setJumpVersion(vid);
          setTab("ladder");
        }}
        onTest={(issue) => {
          setIssueContext({ ...issue, versionId: vid });
          setTab("simulation");
        }}
      />
    </div>
  ) : visibleTab === "reports" ? (
    <div className="document-view">
      <div className="content-heading">
        <h2>{t("检查报告")}</h2>
        <Button
          disabled={!canWrite || !operations.diagnose}
          onClick={() => void guarded(() => submitJob("review"))}
        >
          <FileCheck2 size={15} />
          {t("本地检查")} + AI
        </Button>
      </div>
      {project.reports?.length ? (
        project.reports.map((r) => (
          <button
            className="report-item"
            key={r.report_id}
            onClick={() =>
              void guarded(async () => {
                setReport(await api(`/projects/${pid}/reports/${r.report_id}`));
                setModal("report");
              })
            }
          >
            <FileCheck2 size={20} />
            <span>
              {r.summary || r.report_id}
              <small className="mono">
                {r.base_version_id} · {r.report_id}
              </small>
            </span>
            {status(r.status)}
            <ChevronRight size={16} />
          </button>
        ))
      ) : (
        <div className="panel-empty">{t("没有检查报告")}</div>
      )}
    </div>
  ) : (
    <div className="document-view">
      <div className="content-heading">
        <h2>{t("仿真记录")}</h2>
        <Button
          disabled={!canWrite || !!preview || !operations.simulation}
          onClick={() => void guarded(() => submitJob("test_plan"))}
        >
          <Plus size={15} />
          {t("生成测试方案")}
        </Button>
      </div>
      {operations.simulation ? (
        <SimulationWorkbench
          key={`${pid}:${vid}`}
          pid={pid}
          vid={vid}
          readOnly={!canWrite || !operations.simulation}
          t={t}
          refreshKey={refresh}
          onSaved={refreshAll}
          issueContext={issueContext}
          initialPlanId={issueContext?.planId}
          onExecute={(planId) => guarded(() => proposeExecution("simulation", planId))}
          onDebug={(runId) => guarded(() => submitJob("debug_plan", { run_id: runId }))}
        />
      ) : (
        <p>{t("此程序形式尚未接通仿真。")}</p>
      )}
    </div>
  );
}
