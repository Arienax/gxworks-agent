import type { Job, JobKind } from "../api/client";
import { api, key } from "../api/client";
import type { clarificationSubmission } from "../features/clarificationAnswers";
import type { conversationRoute } from "../features/conversationRouting";
import type { Locale } from "../i18n";
import type { Setter, Translate } from "./types";
import type { useComposer } from "./useComposer";
import type { useJobFeed } from "./useJobFeed";
import type { ProjectWorkspace } from "./useProjectWorkspace";

type Options = {
  workspace: ProjectWorkspace; feed: ReturnType<typeof useJobFeed>;
  composer: ReturnType<typeof useComposer>; intent: JobKind; continuingDirect: boolean;
  directReply: ReturnType<typeof clarificationSubmission>; currentJob?: Job;
  composerRoute: ReturnType<typeof conversationRoute>; locale: Locale; constructionExamples: boolean;
  freshConfirmedGeneration: boolean; clearSpecDirty: () => void; setPanel: Setter<string>; t: Translate
  selectedGXWProgram?: string;
};
export function useJobCommands(options: Options) {
  const { workspace, feed, composer, intent, continuingDirect, directReply, currentJob,
    composerRoute, locale, constructionExamples, freshConfirmedGeneration, clearSpecDirty, setPanel, t,
    selectedGXWProgram } = options;
  const { project, pid, vid } = workspace;
  const { jobsPolling, setJobId, setJobs, setEvents } = feed;
  const { text, attachments, scopeEnabled, scopeNetworks, scopeAddresses, analysisMode,
    setAttachments, setText } = composer;
  async function submitJob(kind: JobKind = intent, extra: Record<string, unknown> = {}) {
    if (!project || project.id !== pid) return;
    if (kind === "direct_generation" && continuingDirect && !directReply.complete) return;
    const epoch = workspace.capture();
    const generationAction =
      kind === "generation"
        ? (extra.generation_action ??
          (kind === intent ? composerRoute.generationAction : undefined))
        : undefined;
    const job = await api<Job>(
      "/jobs",
      "POST",
      {
        kind,
        project_id: pid,
        version_id: vid || null,
        request_id: key(),
        text:
          kind === "generation"
            ? generationAction === "edit"
              ? text
              : t("请严格按照已确认规格生成候选程序。")
            : text,
        response_language: locale,
        attachment_ids: kind === "agent" ? [] : attachments.map((a) => a.attachment_id),
        ...(generationAction ? { generation_action: generationAction } : {}),
        ...(kind === "generation" && generationAction === "edit" && selectedGXWProgram
          ? { program: selectedGXWProgram } : {}),
        ...(kind === "direct_generation" && continuingDirect && currentJob
          ? { clarification_job_id: currentJob.id, clarification_answers: directReply.answers }
          : {}),
        ...((kind === "gx_read" || (kind === "generation" && generationAction === "edit")) &&
          scopeEnabled
          ? {
            change_scope: {
              ...(scopeNetworks.trim()
                ? { network_ids: scopeNetworks.trim().split(/[\s,，]+/) }
                : {}),
              ...(scopeAddresses.trim()
                ? {
                  addresses: scopeAddresses
                    .trim()
                    .toUpperCase()
                    .split(/[\s,，]+/),
                }
                : {}),
            },
          }
          : {}),
        ...extra,
        ...(kind === "analysis" ? { analysis_mode: analysisMode } : {}),
      },
      kind === "generation" || kind === "direct_generation"
        ? {
          headers: {
            "X-GX-Construction-Examples": constructionExamples ? "1" : "0",
            "X-GX-Fresh-Confirmed-Generation": generationAction
              ? generationAction === "regenerate"
                ? "1"
                : "0"
              : freshConfirmedGeneration
                ? "1"
                : "0",
          },
        }
        : {},
    );
    if (!workspace.isProject(pid) || epoch !== workspace.capture()) return;
    jobsPolling.current?.refresh();
    setJobId(job.id);
    if (kind === "analysis") clearSpecDirty();
    setJobs((old) => [job, ...old]);
    setPanel("agent");
    if (kind !== "agent") setAttachments([]);
    if (kind === "generation" || kind === "direct_generation") setText("");
  }

  async function repairFailedGeneration(job: Job) {
    const generation = job.kind === "generation" || job.kind === "direct_generation";
    const savedInvalid =
      generation && job.status === "completed" && job.result?.status === "saved_invalid";
    if (!generation || (job.error_code !== "generation_validation_failed" && !savedInvalid)) return;
    if (
      !window.confirm(
        t("只修复当前候选的局部格式/结构错误，不重新分析需求，也不重写完整程序。继续吗？"),
      )
    )
      return;
    const repaired = await api<Job>(`/jobs/${encodeURIComponent(job.id)}/repair`, "POST", {
      request_id: key(),
    });
    if (!workspace.isProject(pid)) return;
    jobsPolling.current?.refresh();
    setEvents([]);
    setJobId(repaired.id);
    setJobs((old) => [repaired, ...old.filter((item) => item.id !== repaired.id)]);
    setPanel("agent");
  }
  return { submitJob, repairFailedGeneration };
}
