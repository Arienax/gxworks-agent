import {
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  CircuitBoard,
  ClipboardCheck,
  FlaskConical,
  MessageSquare,
  Plus,
  Workflow
} from "lucide-react";
import { activeJob } from "../api/client";
import { Badge, Button } from "../components/ui";
import { FirstProjectGuide } from "../features/FirstProjectGuide";
import { GenerationResult } from "../features/GenerationResult";
import { JobFailure } from "../features/JobFailure";
import { JobProgress } from "../features/JobProgress";
import { AcceptedMessage } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";

export const conversationHistoryFields = [
  "analysisOutput",
  "busy",
  "canWrite",
  "continuingDirect",
  "creation",
  "currentJob",
  "directAnswers",
  "eventText",
  "events",
  "generationResult",
  "guarded",
  "hasSavedVersions",
  "jobs",
  "loading",
  "openAnalysisDraft",
  "openGenerationResult",
  "operations",
  "pid",
  "preview",
  "project",
  "proposeExecution",
  "repairFailedGeneration",
  "retryOutput",
  "setDirectAnswers",
  "setDrafts",
  "setIgnoredClarification",
  "setModal",
  "setPanel",
  "setReviewRequested",
  "setTask",
  "setWorkflow",
  "specIsDirty",
  "submitJob",
  "t",
  "task",
  "version",
  "vid",
] as const;
type Props = Pick<WorkbenchModel, typeof conversationHistoryFields[number]>;
export function ConversationHistory(props: Props) {
  const {
    analysisOutput,
    busy,
    canWrite,
    continuingDirect,
    creation,
    currentJob,
    directAnswers,
    eventText,
    events,
    generationResult,
    guarded,
    hasSavedVersions,
    jobs,
    loading,
    openAnalysisDraft,
    openGenerationResult,
    operations,
    pid,
    preview,
    project,
    proposeExecution,
    repairFailedGeneration,
    retryOutput,
    setDirectAnswers,
    setDrafts,
    setIgnoredClarification,
    setModal,
    setPanel,
    setReviewRequested,
    setTask,
    setWorkflow,
    specIsDirty,
    submitJob,
    t,
    task,
    version,
    vid,
  } = props;
  const loaded = !!project && project.id === pid;
  const idle = canWrite && !jobs.some(activeJob);

  return (
    <div className="conversation">
      <div className="conversation-inner">
      {task === "create" &&
        loaded &&
        !hasSavedVersions &&
        !!project.confirmed_spec &&
        specIsDirty && <p role="alert">{t("请先确认修改后的需求。")}</p>}
      {!currentJob && (
        <div className="welcome">
          <div className="welcome-mark"><CircuitBoard size={26} /></div>
          <h2>{loaded ? t("有什么需要处理的？") : t("开始一个 PLC 工程任务")}</h2>
          {loaded ? (
            <p className="welcome-context">
              <Badge>{project.plc_model}</Badge>
              <Badge>{project.target_mode.toUpperCase()}</Badge>
              <span className="mono">{vid || t("暂无版本")}</span>
            </p>
          ) : (
            <p className="muted">{t("新建工程，或将 GXW 工程拖到对话输入框导入。")}</p>
          )}
          {!pid && !loading && (
            <div className="welcome-actions">
              <Button variant="primary" disabled={!canWrite} onClick={() => setModal("new")}>
                <Plus size={15} />{t("新建工程")}
              </Button>
            </div>
          )}
          {loaded && !hasSavedVersions && (
            <FirstProjectGuide
              example={creation?.starter_requirement || ""}
              t={t}
              hasSpec={!!project.confirmed_spec}
              disabled={!idle}
              onExample={(value) => {
                setDrafts((before) => ({ ...before, create: value }));
                setTask("create");
                setWorkflow(project.target_mode === "ladder" ? "direct" : "review");
                setReviewRequested(false);
              }}
              onSpec={() => setPanel("spec")}
              onGenerate={() => void guarded(() => submitJob("generation"))}
            />
          )}
          {loaded && !!hasSavedVersions && (
            <div className="quick-actions">
              <button disabled={!idle || !version || !operations.diagnose}
                onClick={() => void guarded(() => submitJob("review", { deep: false }))}>
                <ClipboardCheck size={16} /><span>{t("本地检查")}</span>
              </button>
              <button disabled={!idle || !version || !!preview || !operations.simulation}
                onClick={() => void guarded(() => submitJob("test_plan"))}>
                <FlaskConical size={16} /><span>{t("生成测试方案")}</span>
              </button>
              <button disabled={!idle || !operations.gx_inspect}
                onClick={() => void guarded(() => submitJob("gx_read"))}>
                <ArrowLeft size={16} /><span>{t("从 GX 读取")}</span>
              </button>
              <button disabled={!canWrite} className={task === "question" ? "active" : ""}
                onClick={() => setTask("question")}>
                <MessageSquare size={16} /><span>{t("工程问答")}</span>
              </button>
            </div>
          )}
        </div>
      )}
      {currentJob && (
        <div className="job-conversation assistant-turn">
          <div className="assistant-avatar" aria-hidden="true"><Bot size={16} /></div>
          <div className="assistant-body">
          <JobProgress job={currentJob} events={events} t={t} />
          {events.some((e) => e.event_type === "progress") && (
            <details className="reasoning">
              <summary>
                <Workflow size={14} />
                {t("任务记录")}
                <ChevronDown size={14} />
              </summary>
              {events
                .filter((e) => e.event_type === "progress")
                .map((e) => (
                  <p key={e.sequence}>
                    <Check size={11} />
                    {t(
                      String(
                        e.payload?.message || e.payload?.text || e.payload?.stage || "",
                      ),
                    )}
                  </p>
                ))}
            </details>
          )}
          {currentJob.kind !== "analysis" &&
            currentJob.kind !== "generation" &&
            currentJob.kind !== "direct_generation" &&
            currentJob.status === "completed" &&
            eventText("content") && (
              <AcceptedMessage kind={currentJob.kind} text={eventText("content")} t={t} />
            )}
          <GenerationResult
            result={generationResult}
            busy={busy || loading}
            clarification={
              continuingDirect
                ? {
                  answers: directAnswers[generationResult.id] || {},
                  onChange: (id, value) =>
                    setDirectAnswers((before) => ({
                      ...before,
                      [generationResult.id]: {
                        ...before[generationResult.id],
                        [id]: value,
                      },
                    })),
                  disabled: !canWrite || jobs.some(activeJob),
                }
                : undefined
            }
            onOpen={() => void guarded(() => openGenerationResult())}
            onRetry={retryOutput}
            onRepair={() => {
              if (currentJob) void guarded(() => repairFailedGeneration(currentJob));
            }}
            onSpec={() => setPanel("spec")}
            t={t}
          />
          {continuingDirect && (
            <Button
              disabled={!canWrite}
              onClick={() => {
                setIgnoredClarification(currentJob.id);
                setTask("create");
                setWorkflow("direct");
                setDrafts((before) => ({ ...before, create: "" }));
              }}
            >
              {t("开始新需求")}
            </Button>
          )}
          <JobFailure
            job={currentJob}
            busy={!canWrite}
            onRepair={() => void guarded(() => repairFailedGeneration(currentJob))}
            t={t}
          />
          {!!analysisOutput?.spec_draft && (
            <div>
              <Button
                disabled={
                  !canWrite ||
                  (analysisOutput.spec_base_hash ?? null) !==
                  (project?.confirmed_spec_hash ?? null) ||
                  (analysisOutput.base_version_id ?? null) !== (vid || null)
                }
                onClick={openAnalysisDraft}
              >
                {t("查看并确认需求")}
              </Button>
              {(analysisOutput.spec_base_hash ?? null) !==
                (project?.confirmed_spec_hash ?? null) && (
                  <p className="muted">
                    {t(
                      project?.confirmed_spec
                        ? "需求已更新，请使用最新需求。"
                        : "需求已变化，请重新整理。",
                    )}
                  </p>
                )}
            </div>
          )}
          {!!currentJob.result?.plan_id && (
            <Button
              disabled={!canWrite}
              onClick={() =>
                void guarded(() =>
                  proposeExecution(
                    currentJob.kind === "debug_plan" ? "debug" : "simulation",
                    String(currentJob.result?.plan_id),
                    currentJob.version_id || vid,
                  ),
                )
              }
            >
              {t("运行指定方案")}
            </Button>
          )}
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
