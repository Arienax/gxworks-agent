import { useCallback, useEffect, useMemo, useState } from "react";
import type { Json, Spec } from "../api/client";
import { activeJob, artifactUrl } from "../api/client";
import { Badge } from "../components/ui";
import { useGenerationResult } from "../features/GenerationResult";
import { clarificationSubmission } from "../features/clarificationAnswers";
import { conversationRoute } from "../features/conversationRouting";
import { statusText, statusTone } from "../i18n";
import { useCapabilities } from "../workbench/useCapabilities";
import { useComposer } from "../workbench/useComposer";
import { useJobFeed } from "../workbench/useJobFeed";
import { usePanelResize } from "../workbench/usePanelResize";
import { usePreferences } from "../workbench/usePreferences";
import { useProgramDocument } from "../workbench/useProgramDocument";
import { useSession } from "../workbench/useSession";
import { useSettings } from "../workbench/useSettings";

import { useCandidateReview } from "./useCandidateReview";
import { useFeedback, useGuardedAction } from "./useFeedback";
import { useGXSend } from "./useGXSend";
import { useInspectionSelection } from "./useInspectionSelection";
import { useJobCommands } from "./useJobCommands";
import { useProjectWorkspace } from "./useProjectWorkspace";
import { useSpecificationDraft } from "./useSpecificationDraft";

export function useWorkbenchController() {
  const {
    locale,
    setLocale,
    t,
    theme,
    setTheme,
    constructionExamples,
    setConstructionExamples,
    freshConfirmedGeneration,
    setFreshConfirmedGeneration,
  } = usePreferences();

  const { session, connecting, token, setToken, login } = useSession();

  const { error, setError, notice, setNotice } = useFeedback();

  const workspace = useProjectWorkspace(session, setError);

  const { projects, project, pid, setPid, vid, setVid, version, loading, hasSavedVersions, setProjectListRevision, reloadProjectSilently } = workspace;

  const { busy, guarded } = useGuardedAction(workspace, setError);

  const [refresh, setRefresh] = useState(0);

  const [tab, setTab] = useState("ladder"),
    [panel, setPanel] = useState("agent"),
    [leftOpen, setLeftOpen] = useState(window.innerWidth > 900);

  const feed = useJobFeed(pid, session, setError);

  const { jobs, setJobs, jobId, setJobId, events, proposals, jobsPolling } = feed;

  const { rightWidth, setRightWidth, resizePanel } = usePanelResize();

  const composer = useComposer(pid, vid);

  const {
    scopeEnabled,
    setScopeEnabled,
    scopeNetworks,
    setScopeNetworks,
    scopeAddresses,
    setScopeAddresses,
    task,
    setTask,
    workflow,
    setWorkflow,
    editAction,
    setEditAction,
    reviewRequested,
    setReviewRequested,
    setDrafts,
    text,
    setText,
    analysisMode,
    setAnalysisMode,
    attachments,
    setAttachments,
    upload,
    syncComposerRoute,
    directAnswers, setDirectAnswers, ignoredClarification, setIgnoredClarification, } = composer;

  const [modal, setModal] = useState("");

  const {
    approvalSettings,
    setApprovalSettings,
    settingsTab,
    setSettingsTab,
    settingsError,
    setSettingsRetry,
    settings,
    setSettings,
    environment,
  } = useSettings(session, modal, setError);

  const { creation, defaultOperations } = useCapabilities(session, setError);

  const [outputRetry, setOutputRetry] = useState(0);

  const { program, st } = useProgramDocument(pid, vid, version, session, setError);

  const displayedVersionStatus =
    version?.validation?.status === "candidate_ready"
      ? "saved"
      : version?.validation?.status ||
      (version?.lifecycle_status === "accepted" ? "saved" : version?.target_mode);

  const currentJob = jobs.find((j) => j.id === jobId && j.project_id === pid);

  const generationResult = useGenerationResult(currentJob, outputRetry);

  const specification = useSpecificationDraft({ workspace, currentJob, jobId, jobs, session, outputRetry, setPanel, setError, setNotice, t });

  const { spec, specIssues, specIsDirty, analysisOutput, changeSpec, clearSpecDirty, openAnalysisDraft, persistSpec } = specification;

  const inspection = useInspectionSelection(pid, vid);

  const { report, setReport, network, setNetwork, jumpAddress, setJumpAddress, jumpVersion, setJumpVersion, issueContext, setIssueContext } = inspection;

  const continuingDirect =
    currentJob?.kind === "direct_generation" &&
    generationResult.needsInput &&
    currentJob.id !== ignoredClarification;

  const composerRoute = conversationRoute({
    task,
    workflow,
    editAction,
    targetMode: project?.target_mode || "ladder",
    hasVersions: !!hasSavedVersions,
    hasSpec: !!project?.confirmed_spec,
    reviewRequested,
    continuingDirect,
  });

  const intent = composerRoute.kind;

  const answeringDirect = intent === "direct_generation" && continuingDirect;

  const directReply = clarificationSubmission(
    generationResult.questions,
    directAnswers[generationResult.id] || {},
  );

  const generationSaved =
    !!generationResult.versionId ||
    proposals.some(
      (proposal) =>
        proposal.id === generationResult.proposalId &&
        proposal.action === "accept_local" &&
        proposal.status === "accepted",
    );

  const displayedJobStatus =
    currentJob?.kind === "execution" &&
      currentJob.status === "completed" &&
      ["failed", "interrupted", "conflict"].includes(String(currentJob.result?.status || ""))
      ? "failed"
      : (currentJob?.kind === "generation" || currentJob?.kind === "direct_generation") &&
        currentJob.status === "completed"
        ? generationResult.needsInput
          ? "needs_input"
          : generationResult.blocked
            ? "contract_mismatch"
            : generationSaved
              ? "saved"
              : generationResult.proposalId
                ? "candidate_ready"
                : generationResult.loading
                  ? "loading_result"
                  : "result_unavailable"
        : currentJob?.status;

  const pendingCount = proposals.filter((p) => p.status === "pending").length;

  const canWrite = !!session && !session.read_only && !busy && !loading;

  const canGenerate =
    !!project &&
    project.id === pid &&
    (!!project.confirmed_spec || hasSavedVersions) &&
    !specIsDirty &&
    !jobs.some(activeJob);

  const canSubmit =
    canWrite &&
    !!pid &&
    project?.id === pid &&
    !jobs.some(activeJob) &&
    composerRoute.available &&
    (task !== "edit" || !!version) &&
    (intent !== "generation" || canGenerate) &&
    (answeringDirect
      ? directReply.complete && (!!Object.keys(directReply.answers).length || !!text.trim())
      : !composerRoute.requiresText || !!text.trim());

  const operations = version?.capabilities?.operations || defaultOperations;

  const navigation = { setTab, setPanel, setModal };

  const candidates = useCandidateReview({ workspace, feed, generationResult, currentJob, session, theme, specIsDirty, busy, guarded, setRefresh, setOutputRetry, setError, setNotice, t, ...navigation });

  const {
    selectedProposal,
    preview,
    diagnosticJobId,
    versionDrawing,
    refreshingDrawing,
    clearPreview,
    selectVersion,
    openGenerationResult,
    redrawVersion,
    refreshDrawing,
    showProposal,
    showFBDProposal,
    decide,
    proposeExecution,
  } = candidates;

  const { submitJob, repairFailedGeneration } = useJobCommands({ workspace, feed, composer, intent, continuingDirect, directReply, currentJob, composerRoute, locale, constructionExamples, freshConfirmedGeneration, clearSpecDirty, setPanel, t });

  const { gxSend, setGXSend, requestGXSend, confirmGXSend } = useGXSend({ workspace, feed, canWrite, preview, operations, clearPreview, setPanel, guarded, proposeExecution });

  const refreshAll = () => {
    setRefresh((n) => n + 1);
    jobsPolling.current?.refresh();
    void reloadProjectSilently(pid).catch((e) => {
      if (workspace.isProject(pid)) setError(e.message);
    });
  };

  useEffect(() => { if (project?.id === pid) syncComposerRoute(project); }, [project, pid]);

  useEffect(() => {
    if (version?.target_mode === "fbd") setTab("fbd");
    else if (version?.target_mode === "sfc") setTab("sfc");
    else if (project?.id === pid && !project.versions?.length && project.target_mode === "fbd") setTab("fbd");
  }, [version?.id, version?.target_mode, project?.id, project?.target_mode]);

  function retryOutput() {
    setOutputRetry((value) => value + 1);
    void reloadProjectSilently(pid).catch((error: Error) => { if (workspace.isProject(pid)) setError(error.message); });
  }

  async function saveSpec(value: Spec) {
    const result = await persistSpec(value);
    if (!result) return;
    setTask(hasSavedVersions ? "edit" : "create"); setWorkflow("review");
    setEditAction(result.regenerateAfterSave ? "regenerate" : "edit"); setReviewRequested(false); setPanel("agent");
    if (result.generateAfterSave) await submitJob("generation", result.regenerateAfterSave ? { generation_action: "regenerate" } : {});
    else await reloadProjectSilently(pid);
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    if (selectedProposal) void guarded(() => showProposal(selectedProposal, next));
    else if (diagnosticJobId && diagnosticJobId === generationResult.id)
      void guarded(() => openGenerationResult(next));
    else if (versionDrawing && version?.target_mode === "ladder")
      void guarded(() => redrawVersion(vid, next));
  }

  const visibleProgram = preview ? (preview.program as Record<string, Json> | undefined) : program;

  const networks = Array.isArray(visibleProgram?.networks)
    ? (visibleProgram.networks as Record<string, Json>[])
    : [];

  const freshVersionSvg =
    versionDrawing?.key === `${pid}:${vid}:${theme}` ? versionDrawing.svg : "";

  const svg = useMemo(
    () =>
      preview?.svg
        ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(String(preview.svg))}`
        : !preview && freshVersionSvg
          ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(freshVersionSvg)}`
          : !preview && version?.artifacts?.find((a) => a.id === "svg" && a.available)
            ? artifactUrl(pid, vid, "svg") + `?theme=${theme}`
            : "",
    [preview, freshVersionSvg, version?.id, pid, vid, theme],
  );

  const selectNetwork = useCallback(
    (id: string) => {
      setNetwork({ ...networks.find((n) => n.id === id), id, version_id: vid });
    },
    [visibleProgram, vid],
  );

  const status = (value?: string | null) => (
    <Badge tone={statusTone(value || "unknown")}>{statusText(locale, value || "unknown")}</Badge>
  );

  const latestMessage = useMemo(
    () => events.filter((e) => e.event_type === "progress").at(-1)?.payload,
    [events],
  );

  const eventText = (kind: string) =>
    events
      .filter((e) => e.event_type === kind)
      .map((e) => e.payload?.text || e.payload?.message || "")
      .join("");

  const editorProps = {
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
  };
  return {
    analysisMode,
    analysisOutput,
    answeringDirect,
    approvalSettings,
    attachments,
    busy,
    canSubmit,
    canWrite,
    changeSpec,
    clearPreview,
    composerRoute,
    confirmGXSend,
    connecting,
    constructionExamples,
    continuingDirect,
    creation,
    currentJob,
    decide,
    diagnosticJobId,
    directAnswers,
    displayedJobStatus,
    displayedVersionStatus,
    editAction,
    editorProps,
    environment,
    error,
    eventText,
    events,
    freshConfirmedGeneration,
    generationResult,
    guarded,
    gxSend,
    hasSavedVersions,
    intent,
    jobId,
    jobs,
    jobsPolling,
    latestMessage,
    leftOpen,
    loading,
    locale,
    login,
    modal,
    network,
    notice,
    openAnalysisDraft,
    openGenerationResult,
    operations,
    panel,
    pendingCount,
    pid,
    preview,
    project,
    projects,
    proposals,
    proposeExecution,
    refreshDrawing,
    refreshingDrawing,
    reloadProjectSilently,
    repairFailedGeneration,
    report,
    requestGXSend,
    resizePanel,
    retryOutput,
    reviewRequested,
    rightWidth,
    saveSpec,
    scopeAddresses,
    scopeEnabled,
    scopeNetworks,
    selectVersion,
    selectedProposal,
    session,
    setAnalysisMode,
    setApprovalSettings,
    setAttachments,
    setConstructionExamples,
    setDirectAnswers,
    setDrafts,
    setEditAction,
    setError,
    setFreshConfirmedGeneration,
    setGXSend,
    setIgnoredClarification,
    setIssueContext,
    setJobId,
    setJobs,
    setJumpAddress,
    setJumpVersion,
    setLeftOpen,
    setLocale,
    setModal,
    setNetwork,
    setNotice,
    setPanel,
    setPid,
    setProjectListRevision,
    setReviewRequested,
    setRightWidth,
    setScopeAddresses,
    setScopeEnabled,
    setScopeNetworks,
    setSettings,
    setSettingsRetry,
    setSettingsTab,
    setTab,
    setTask,
    setText,
    setToken,
    setVid,
    setWorkflow,
    settings,
    settingsError,
    settingsTab,
    showFBDProposal,
    showProposal,
    spec,
    specIsDirty,
    specIssues,
    status,
    submitJob,
    t,
    tab,
    task,
    text,
    theme,
    toggleTheme,
    token,
    upload,
    version,
    vid,
    workflow,
  };
}
export type WorkbenchModel = ReturnType<typeof useWorkbenchController>;
