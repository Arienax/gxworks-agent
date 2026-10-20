import {
  Check,
  ChevronDown,
  Workflow
} from "lucide-react";
import { activeJob } from "../api/client";
import { Button } from "../components/ui";
import { GenerationResult } from "../features/GenerationResult";
import { JobFailure } from "../features/JobFailure";
import { JobProgress } from "../features/JobProgress";
import { AcceptedMessage } from "../workbench/ResultViews";
import type { WorkbenchModel } from "./useWorkbenchController";

const jobKindLabel = (kind: string) =>
  (
    ({
      analysis: "整理需求",
      generation: "生成程序",
      direct_generation: "生成程序",
      agent: "工程问答",
      review: "检查程序",
      test_plan: "设计测试",
      debug_plan: "排查问题",
      gx_read: "从 GX 读取",
      gx_inspect: "检查 GX 工程",
      execution: "执行操作",
    }) as Record<string, string>
  )[kind] || "任务";
export const conversationHistoryFields = [
  "analysisOutput",
  "busy",
  "canWrite",
  "continuingDirect",
  "currentJob",
  "directAnswers",
  "displayedJobStatus",
  "eventText",
  "events",
  "generationResult",
  "guarded",
  "hasSavedVersions",
  "jobs",
  "loading",
  "openAnalysisDraft",
  "openGenerationResult",
  "pid",
  "project",
  "proposeExecution",
  "repairFailedGeneration",
  "retryOutput",
  "setDirectAnswers",
  "setDrafts",
  "setIgnoredClarification",
  "setPanel",
  "setTask",
  "setWorkflow",
  "specIsDirty",
  "status",
  "t",
  "task",
  "vid",
] as const;
type Props = Pick<WorkbenchModel, typeof conversationHistoryFields[number]>;
export function ConversationHistory(props: Props) {
  const {
    analysisOutput,
    busy,
    canWrite,
    continuingDirect,
    currentJob,
    directAnswers,
    displayedJobStatus,
    eventText,
    events,
    generationResult,
    guarded,
    hasSavedVersions,
    jobs,
    loading,
    openAnalysisDraft,
    openGenerationResult,
    pid,
    project,
    proposeExecution,
    repairFailedGeneration,
    retryOutput,
    setDirectAnswers,
    setDrafts,
    setIgnoredClarification,
    setPanel,
    setTask,
    setWorkflow,
    specIsDirty,
    status,
    t,
    task,
    vid,
  } = props;

  return (
    <div className="conversation">
      {task === "create" &&
        project?.id === pid &&
        !hasSavedVersions &&
        !!project.confirmed_spec &&
        specIsDirty && <p role="alert">{t("请先确认修改后的需求。")}</p>}
      {currentJob && (
        <div className="job-conversation">
          <div className="job-line">
            <span>{t(jobKindLabel(currentJob.kind))}</span>
            {status(displayedJobStatus)}
          </div>
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
      )}
    </div>
  );
}
