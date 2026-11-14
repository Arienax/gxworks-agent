// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import type { AnalysisRequest } from "./specificationContinuation";
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
  recordAnalysisRequest: (id: string, request: AnalysisRequest, parentId?: string) => void;
};
export function useJobCommands(options: Options) {
  const { workspace, feed, composer, intent, continuingDirect, directReply, currentJob,
    composerRoute, locale, constructionExamples, freshConfirmedGeneration, clearSpecDirty, setPanel, t,
    selectedGXWProgram, recordAnalysisRequest } = options;
  const { project, pid, vid } = workspace;
  const { conversationId, jobsPolling, selectConversation, setJobId, setJobs, setEvents } = feed;
  const { text, attachments, scopeEnabled, scopeNetworks, scopeAddresses, analysisMode,
    setAttachments, setText } = composer;
  async function submitJob(kind: JobKind, extra: Record<string, unknown> = {}, clearSubmittedText = false) {
    if (!project || project.id !== pid) return;
    const epoch = workspace.capture();
    const parentId = typeof extra.clarification_job_id === "string" ? extra.clarification_job_id : undefined;
    const generationAction =
      !parentId && kind === "generation"
        ? (extra.generation_action ??
          (kind === intent ? composerRoute.generationAction : undefined))
        : undefined;
    const job = await api<Job>(
      "/jobs",
      "POST",
      {
        kind,
        project_id: pid,
        ...(!parentId ? { version_id: vid || null } : {}),
        request_id: key(),
        text: kind === "generation" && !parentId && generationAction !== "edit"
          ? t("请严格按照已确认规格生成候选程序。") : text,
        response_language: locale,
        attachment_ids: kind === "agent" ? [] : attachments.map((a) => a.attachment_id),
        ...(generationAction ? { generation_action: generationAction } : {}),
        ...(!parentId && (kind === "analysis" || kind === "generation" && generationAction === "edit") && selectedGXWProgram
          ? { program: selectedGXWProgram } : {}),
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
        ...(kind === "analysis" && !parentId ? { analysis_mode: analysisMode } : {}),
      },
      !parentId && (kind === "generation" || kind === "direct_generation")
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
    if (job.conversation_id) selectConversation(job);
    else setJobId(job.id);
    if (kind === "analysis") {
      if (parentId) recordAnalysisRequest(job.id, { versionId: job.version_id || null, text }, parentId);
      else recordAnalysisRequest(job.id, { versionId: vid || null, program: selectedGXWProgram, text,
        generationAction: composer.task === "edit" ? composer.editAction : undefined });
      clearSpecDirty();
    }
    setJobs((old) => [job, ...old]);
    setPanel("agent");
    if (kind !== "agent") setAttachments([]);
    if (clearSubmittedText) setText((draft) => draft === text ? "" : draft);
  }

  async function submitMessage() {
    if (continuingDirect && currentJob) {
      const { kind } = currentJob;
      if ((kind !== "analysis" && kind !== "generation" && kind !== "direct_generation") || !directReply.complete) return;
      if (!Object.keys(directReply.answers).length && !text.trim()) return;
      return submitJob(kind, { clarification_job_id: currentJob.id, clarification_answers: directReply.answers }, true);
    }
    const attachment = conversationId
      ? { conversation_id: conversationId }
      : { start_conversation: true };
    return submitJob(intent, attachment, composerRoute.requiresText);
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
    if (repaired.conversation_id) selectConversation(repaired);
    else setJobId(repaired.id);
    setJobs((old) => [repaired, ...old.filter((item) => item.id !== repaired.id)]);
    setPanel("agent");
  }
  return { submitJob, submitMessage, repairFailedGeneration };
}
