import { useEffect, useRef, useState } from "react";
import type { Job, Json, Proposal, Session } from "../api/client";
import { activeJob, api, key } from "../api/client";
import type { useGenerationResult } from "../features/GenerationResult";
import type { Navigation, Setter, Translate } from "./types";
import type { useJobFeed } from "./useJobFeed";
import type { ProjectWorkspace } from "./useProjectWorkspace";

type Options = Navigation & {
  workspace: ProjectWorkspace; feed: ReturnType<typeof useJobFeed>;
  generationResult: ReturnType<typeof useGenerationResult>; currentJob?: Job; session: Session | null;
  theme: string; specIsDirty: boolean; busy: boolean; guarded: (action: () => Promise<void>) => Promise<void>;
  setRefresh: Setter<number>; setOutputRetry: Setter<number>; setError: (message: string) => void;
  setNotice: (message: string) => void; t: Translate
};

export function useCandidateReview(options: Options) {
  const { workspace, feed, generationResult, currentJob, session, theme, specIsDirty, busy,
    guarded, setTab, setPanel, setModal, setRefresh, setOutputRetry, setError, setNotice, t } = options;
  const { pid, vid, version, loading, setVid, reloadProjectSilently } = workspace;
  const { jobs, jobId, setJobId, setJobs, setProposals, jobsPolling } = feed;
  const [selectedProposal, setSelectedProposal] = useState<Proposal | null>(null);
  const [preview, setPreview] = useState<Record<string, Json> | null>(null);
  const [diagnosticJobId, setDiagnosticJobId] = useState("");
  const [versionDrawing, setVersionDrawing] = useState<{ key: string; svg: string } | null>(null);
  const [refreshingDrawing, setRefreshingDrawing] = useState(false);
  const previewEpoch = useRef(0);
  const shownCandidates = useRef(new Set<string>());
  const failedPreviews = useRef(new Set<string>());
  const terminalJobs = useRef(new Set<string>());
  const resultKey = generationResult.id ? pid + ":" + generationResult.id : "";
  function clearPreview() {
    previewEpoch.current++; setPreview(null); setSelectedProposal(null); setDiagnosticJobId("");
  }
  function selectVersion(id: string) { clearPreview(); setVersionDrawing(null); setVid(id); }
  useEffect(() => {
    clearPreview(); setVersionDrawing(null); shownCandidates.current.clear();
    failedPreviews.current.clear(); terminalJobs.current.clear();
  }, [pid]);
  async function openSavedVersion(versionId: string, previewTheme = theme) {
    const epoch = workspace.capture();
    const fresh = await reloadProjectSilently(pid);
    if (!fresh || !workspace.isCurrent(epoch)) return;
    const saved = fresh.versions?.find((item) => item.id === versionId);
    if (!saved) throw new Error(t("已保存版本尚不可用，请刷新重试。"));
    clearPreview(); setVid(versionId);
    setTab(saved.target_mode || fresh.target_mode || "ladder"); setPanel("agent");
    if (saved.target_mode === "ladder") await redrawVersion(versionId, previewTheme);
  }

  useEffect(() => {
    const completed = jobs.filter(
      (job) =>
        job.project_id === pid &&
        !activeJob(job) &&
        !terminalJobs.current.has(`${job.id}:${job.status}`),
    );
    if (!completed.length) return;
    completed.forEach((job) => terminalJobs.current.add(`${job.id}:${job.status}`));
    setRefresh((n) => n + 1);
    const willOpenSaved =
      !busy &&
      !loading &&
      !specIsDirty &&
      completed.some(
        (job) =>
          job.id === jobs[0]?.id &&
          job.id === jobId &&
          job.status === "completed" &&
          typeof job.result?.version_id === "string",
      );
    if (!willOpenSaved)
      void reloadProjectSilently(pid).catch((e) => {
        if (workspace.isProject(pid)) setError(e.message);
      });
  }, [jobs, pid, jobId]);

  useEffect(() => {
    if (
      !session ||
      busy ||
      loading ||
      specIsDirty ||
      (currentJob?.kind === "generation" && typeof currentJob.result?.version_id === "string") ||
      !resultKey ||
      (!generationResult.versionId && !generationResult.proposalId && !generationResult.blocked) ||
      jobId !== jobs[0]?.id ||
      shownCandidates.current.has(resultKey) ||
      failedPreviews.current.has(resultKey)
    )
      return;
    void guarded(() => openGenerationResult());
  }, [
    resultKey,
    generationResult.versionId,
    generationResult.proposalId,
    generationResult.blocked,
    jobs,
    jobId,
    session,
    busy,
    loading,
  ]);

  useEffect(() => {
    const saved = currentJob?.result?.version_id;
    if (
      currentJob?.status !== "completed" ||
      typeof saved !== "string" ||
      !session ||
      busy ||
      loading ||
      specIsDirty ||
      jobId !== jobs[0]?.id
    )
      return;
    const key = `${pid}:${jobId}:saved`;
    if (shownCandidates.current.has(key) || failedPreviews.current.has(key)) return;
    void guarded(async () => {
      try {
        await openSavedVersion(saved);
        shownCandidates.current.add(key);
      } catch (e) {
        failedPreviews.current.add(key);
        throw e;
      }
    });
  }, [currentJob, session, busy, loading, jobs, jobId, pid]);

  async function openGenerationResult(previewTheme = theme, reread = false) {
    const epoch = workspace.capture();
    const key = resultKey;
    if (!key) return;
    try {
      // A manual retry reads the persisted result again rather than relying on
      // SSE, list polling, or an obsolete React state snapshot.
      const output = reread
        ? await api<Record<string, Json>>(`/jobs/${generationResult.id}/output`)
        : generationResult.value;
      if (epoch !== workspace.capture()) return;
      const blocked = output?.status === "contract_mismatch" || generationResult.blocked;
      const proposalId =
        typeof output?.proposal_id === "string" ? output.proposal_id : generationResult.proposalId;
      if (blocked) {
        const request = ++previewEpoch.current;
        const loaded = await api<Record<string, Json>>(
          `/jobs/${generationResult.id}/preview?theme=${previewTheme}`,
        );
        if (epoch !== workspace.capture() || request !== previewEpoch.current) return;
        setPreview(loaded);
        setSelectedProposal(null);
        setDiagnosticJobId(generationResult.id);
        setTab("ladder");
        setPanel("agent");
      } else if (typeof output?.version_id === "string" || generationResult.versionId) {
        await openSavedVersion(
          String(output?.version_id || generationResult.versionId),
          previewTheme,
        );
      } else if (proposalId) {
        const candidate = await api<Proposal>(`/proposals/${proposalId}`);
        if (epoch !== workspace.capture() || candidate.project_id !== pid) return;
        setProposals((old) => [candidate, ...old.filter((p) => p.id !== candidate.id)]);
        const acceptedVersion = candidate.result?.version_id;
        if (candidate.status === "accepted" && typeof acceptedVersion === "string") {
          await openSavedVersion(acceptedVersion, previewTheme);
        } else {
          await showProposal(candidate, previewTheme);
        }
      } else {
        throw new Error(t("任务已结束，但尚未取得可显示的候选结果。"));
      }
      if (epoch === workspace.capture()) {
        failedPreviews.current.delete(key);
        shownCandidates.current.add(key);
      }
    } catch (error) {
      if (epoch === workspace.capture()) failedPreviews.current.add(key);
      throw error;
    }
  }

  async function redrawVersion(versionId = vid, previewTheme = theme) {
    const epoch = workspace.capture();
    const request = ++previewEpoch.current;
    const drawing = await api<Record<string, Json>>(
      `/projects/${pid}/versions/${versionId}/preview?theme=${previewTheme}`,
    );
    if (epoch !== workspace.capture() || request !== previewEpoch.current) return;
    if (typeof drawing.svg !== "string" || !drawing.svg.includes("<svg"))
      throw new Error(t("未取得有效的梯形图预览，请检查诊断。"));
    setVersionDrawing({ key: `${pid}:${versionId}:${previewTheme}`, svg: drawing.svg });
    setTab("ladder");
  }

  async function refreshDrawing() {
    const epoch = workspace.capture();
    setRefreshingDrawing(true);
    setRefresh((n) => n + 1);
    try {
      if (selectedProposal) await showProposal(selectedProposal);
      else if (diagnosticJobId) await openGenerationResult(theme, true);
      else if (version?.target_mode === "ladder") await redrawVersion();
      else if (generationResult.id) await openGenerationResult(theme, true);
      else {
        setOutputRetry((n) => n + 1);
        void reloadProjectSilently(pid).catch((e) => {
          if (workspace.isProject(pid)) setError(e.message);
        });
        return;
      }
      if (epoch === workspace.capture()) {
        setOutputRetry((n) => n + 1);
        void reloadProjectSilently(pid).catch((e) => {
          if (workspace.isProject(pid)) setError(e.message);
        });
        setNotice(t("预览已刷新，未调用模型或修改程序。"));
      }
    } finally {
      setRefreshingDrawing(false);
    }
  }

  async function showProposal(value: Proposal, previewTheme = theme) {
    if (
      value.action === "accept_local" &&
      value.status === "accepted" &&
      typeof value.result?.version_id === "string"
    ) {
      await openSavedVersion(value.result.version_id, previewTheme);
      return;
    }
    if (value.execution_job_id) {
      const running = await api<Job>(`/jobs/${value.execution_job_id}`);
      if (!workspace.isProject(value.project_id)) return;
      setJobId(running.id);
      setJobs((old) => [running, ...old.filter((j) => j.id !== running.id)]);
      setPanel("agent");
      return;
    }
    const epoch = workspace.capture();
    const request = ++previewEpoch.current;
    const loaded = await api<Record<string, Json>>(
      `/proposals/${value.id}/preview?theme=${previewTheme}`,
    );
    if (
      !workspace.isProject(value.project_id) ||
      epoch !== workspace.capture() ||
      request !== previewEpoch.current
    )
      return;
    setDiagnosticJobId("");
    setPreview(loaded);
    setSelectedProposal(value);
    setPanel("proposals");
    setTab(
      loaded.target_mode === "fbd"
        ? "fbd"
        : loaded.target_mode === "sfc"
          ? "sfc"
          : loaded.target_mode === "st"
            ? "st"
            : "ladder",
    );
  }

  async function showFBDProposal(value: Proposal) {
    if (!workspace.isProject(value.project_id)) return;
    setProposals((old) => [value, ...old.filter((p) => p.id !== value.id)]);
    setModal("");
    await showProposal(value);
  }

  async function decide(value: Proposal, decision: "accept" | "reject") {
    const epoch = workspace.capture();
    const result = await api<{ proposal?: Proposal; job?: Job }>(
      `/proposals/${value.id}/decision`,
      "POST",
      { decision },
    );
    if (!workspace.isProject(value.project_id) || epoch !== workspace.capture()) return;
    jobsPolling.current?.refresh();
    if (result.proposal)
      setProposals((old) =>
        old.map((item) => (item.id === result.proposal!.id ? result.proposal! : item)),
      );
    setRefresh((n) => n + 1);
    if (result.job) {
      setJobId(result.job.id);
      setJobs((old) => [result.job!, ...old]);
    }
    if (result.proposal?.result?.version_id) setVid(String(result.proposal.result.version_id));
    setSelectedProposal(null);
    setPreview(null);
    setNotice(t("操作完成"));
    await reloadProjectSilently(pid);
  }

  async function proposeExecution(action: string, planId?: string, versionId = vid) {
    const epoch = workspace.capture();
    const value = await api<Proposal>("/proposals", "POST", {
      action,
      project_id: pid,
      version_id: versionId,
      plan_id: planId || null,
      request_id: key(),
    });
    if (!workspace.isProject(pid) || epoch !== workspace.capture()) return;
    jobsPolling.current?.refresh();
    setProposals((old) => [value, ...old.filter((item) => item.id !== value.id)]);
    await showProposal(value);
  }
  return {
    selectedProposal, preview, diagnosticJobId, versionDrawing, refreshingDrawing,
    clearPreview, selectVersion, openGenerationResult, openSavedVersion, redrawVersion, refreshDrawing,
    showProposal, showFBDProposal, decide, proposeExecution
  };
}
