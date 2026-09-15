import { ConditionNormalization } from "./features/ConditionNormalization";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  Code2,
  FileCheck2,
  FileCode2,
  FolderOpen,
  GitBranch,
  History,
  LoaderCircle,
  Moon,
  PanelLeftClose,
  Paperclip,
  Play,
  Plus,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  Sun,
  Workflow,
  X,
} from "lucide-react";
import { api, activeJob, artifactUrl, key, setSession } from "./api/client";
import type {
  Artifact,
  Job,
  JobEvent,
  JobKind,
  Json,
  ModelSettings,
  Project,
  Proposal,
  Session,
  Spec,
} from "./api/client";
import { Badge, Button, Modal } from "./components/ui";
import { statusText, statusTone, translate } from "./i18n";
import type { Locale } from "./i18n";
import { SpecEditor } from "./features/SpecEditor";
import { JobFailure } from "./features/JobFailure";
import { GenerationResult, useGenerationResult } from "./features/GenerationResult";
import { JobProgress } from "./features/JobProgress";
import { Settings } from "./features/Settings";
import { ApprovalSettingsPanel, approvalLabels } from "./features/ApprovalSettings";
import type { ApprovalSettings } from "./features/ApprovalSettings";
import { ProjectToolbar, artifactLabel } from "./features/ProjectToolbar";
import { submitGXSend } from "./features/gxSend";
import type { GXSendSelection } from "./features/gxSend";
import { FBDPanel, FBDImport, emptyFBD } from "./features/FBDPanel";
import type { FBDModel } from "./features/FBDPanel";
import { ProgramExplorer, IssueCards } from "./features/ProgramExplorer";
import type { IssueContext } from "./features/ProgramExplorer";
import { SimulationWorkbench } from "./features/SimulationWorkbench";
import { FirstProjectGuide } from "./features/FirstProjectGuide";
import { DeliverySummary } from "./features/DeliverySummary";

let bootstrapToken =
  new URLSearchParams(location.hash.slice(1)).get("token") || "";
if (bootstrapToken)
  history.replaceState(null, "", location.pathname + location.search);
const stringify = (value: unknown) => JSON.stringify(value, null, 2);

export default function App() {
  const [locale, setLocale] = useState<Locale>(
    () => (localStorage.getItem("gx.locale") as Locale) || "zh-CN",
  );
  const t = useCallback((s: string) => translate(locale, s), [locale]);
  const [session, updateSession] = useState<Session | null>(null),
    [connecting, setConnecting] = useState(true);
  const [token, setToken] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState<Project | null>(null);
  const [pid, setPid] = useState(
      new URLSearchParams(location.search).get("project") || "",
    ),
    [vid, setVid] = useState("");
  const [refresh, setRefresh] = useState(0),
    [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("ladder"),
    [panel, setPanel] = useState("agent"),
    [leftOpen, setLeftOpen] = useState(window.innerWidth > 900);
  const [approvalSettings, setApprovalSettings] = useState<ApprovalSettings | null>(null);
  const [settingsTab, setSettingsTab] = useState<"general" | "model">("general");
  const [settingsError, setSettingsError] = useState("");
  const [settingsRetry, setSettingsRetry] = useState(0);
  const [settings, setSettings] = useState<ModelSettings | null>(null),
    [environment, setEnvironment] = useState<Record<string, Json>>({});
  const [jobs, setJobs] = useState<Job[]>([]),
    [jobId, setJobId] = useState(""),
    [events, setEvents] = useState<JobEvent[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]),
    [selectedProposal, setSelectedProposal] = useState<Proposal | null>(null);
  const [preview, setPreview] = useState<Record<string, Json> | null>(null),
    [spec, setSpec] = useState<Spec | null>(null);
  const [analysisOutput, setAnalysisOutput] = useState<Record<
    string,
    Json
  > | null>(null);
  const [program, setProgram] = useState<Record<string, Json> | null>(null),
    [report, setReport] = useState<Json>(null);
  const [network, setNetwork] = useState<Record<string, Json> | null>(null),
    [st, setSt] = useState("");
  const [rightWidth, setRightWidth] = useState(370);
  const [jumpAddress, setJumpAddress] = useState("");
  const [jumpVersion, setJumpVersion] = useState("");
  const [issueContext, setIssueContext] = useState<IssueContext & {versionId:string}>();
  const [scopeEnabled, setScopeEnabled] = useState(false);
  const [scopeNetworks, setScopeNetworks] = useState("");
  const [scopeAddresses, setScopeAddresses] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    localStorage.getItem("gx.theme") === "light" ? "light" : "dark",
  );
  const [text, setText] = useState(""),
    [intent, setIntent] = useState<"analysis" | "generation" | "agent">(
      "analysis",
    );
  const [attachments, setAttachments] = useState<
      { attachment_id: string; filename: string }[]
    >([]),
    [busy, setBusy] = useState(false);
  const [modal, setModal] = useState(""),
    [newName, setNewName] = useState(""),
    [newMode, setNewMode] = useState<"ladder" | "st" | "fbd">("ladder");
  const [steps, setSteps] = useState([
    { name: "", action: "", transition: "" },
  ]);
  const [specIssues, setSpecIssues] = useState<{ path: string; message: string }[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const consumedDrafts = useRef(new Set<string>());
  const shownCandidates = useRef(new Set<string>());
  const failedPreviews = useRef(new Set<string>());
  const previewEpoch = useRef(0);
  const [diagnosticJobId, setDiagnosticJobId] = useState("");
  const [outputRetry, setOutputRetry] = useState(0);
  const [versionDrawing, setVersionDrawing] = useState<{ key: string; svg: string } | null>(null);
  const [refreshingDrawing, setRefreshingDrawing] = useState(false);
  const specBinding = useRef("");
  const specDirty = useRef(false);
  const openedDrafts = useRef(new Set<string>());
  const activeProjectRef = useRef(pid);
  const projectEpoch = useRef(0);
  const [gxSend, setGXSend] = useState<GXSendSelection | null>(null);
  const gxSendSelection = useRef({ pid, vid, activeVersionId: "", epoch: 0 });
  gxSendSelection.current = { pid, vid, activeVersionId: project?.active_version_id || "", epoch: projectEpoch.current };
  useEffect(() => { setGXSend(null); }, [pid, vid, project?.active_version_id]);
  const composerProjectRef = useRef("");
  function syncComposerRoute(value: Project) {
    const editRegenerate = !!value.confirmed_spec &&
      ((value.version_count || 0) > 0 || (value.versions?.length || 0) > 0);
    const routeKey = `${value.id}:${editRegenerate ? "edit-regenerate" : "new-requirement"}`;
    if (composerProjectRef.current === routeKey) return;
    composerProjectRef.current = routeKey;
    setIntent(editRegenerate ? "generation" : "analysis");
  }
  useEffect(() => {
    projectEpoch.current += 1;
    previewEpoch.current += 1;
    setPreview(null);
    setSelectedProposal(null);
    setDiagnosticJobId("");
    setVersionDrawing(null);
    shownCandidates.current.clear();
    failedPreviews.current.clear();
    activeProjectRef.current = pid;
    setAttachments([]);
    setVid("");
    setProgram(null);
    setSt("");
    setJobs([]);
    setJobId("");
    setEvents([]);
    setAnalysisOutput(null);
    setProposals([]);
    setSpec(null);
    setSpecIssues([]);
    specDirty.current = false;
    setNetwork(null);
  }, [pid]);
  const version = project?.versions?.find((v) => v.id === vid);
  const hasSavedVersions = project?.id === pid &&
    ((project.version_count || 0) > 0 || !!project.versions?.length);
  // Shape validation can remain candidate_ready after its local version is saved.
  const displayedVersionStatus = version?.validation?.status === "candidate_ready"
    ? "saved" : version?.validation?.status ||
      (version?.lifecycle_status === "accepted" ? "saved" : version?.target_mode);
  const currentJob = jobs.find((j) => j.id === jobId && j.project_id === pid);
  const generationResult = useGenerationResult(currentJob, outputRetry);
  const resultKey = generationResult.id ? `${pid}:${generationResult.id}` : "";
  const generationSaved = !!generationResult.versionId || proposals.some((proposal) =>
    proposal.id === generationResult.proposalId && proposal.action === "accept_local" &&
    proposal.status === "accepted");
  const displayedJobStatus = currentJob?.kind === "execution" && currentJob.status === "completed" &&
    ["failed", "interrupted", "conflict"].includes(String(currentJob.result?.status || ""))
    ? "failed"
    : currentJob?.kind === "generation" && currentJob.status === "completed"
      ? generationResult.blocked ? "contract_mismatch"
        : generationSaved ? "saved"
        : generationResult.proposalId ? "candidate_ready"
        : generationResult.loading ? "loading_result" : "result_unavailable"
      : currentJob?.status;
  const pendingCount = proposals.filter((p) => p.status === "pending").length;
  const canWrite = !!session && !session.read_only && !busy && !loading;
  const canGenerate = !!project && project.id === pid &&
    !!project.confirmed_spec && !specDirty.current && !jobs.some(activeJob);
  const canSubmit = canWrite && !!pid && project?.id === pid &&
    (intent === "generation" ? canGenerate : !!text.trim());
  const operations = version?.capabilities?.operations || {};
  const refreshAll = () => setRefresh((n) => n + 1);
  const guarded = async (action: () => Promise<void>) => {
    // State updates are asynchronous: hold a synchronous submission lock too.
    if (busyRef.current) return;
    busyRef.current = true;
    const epoch = projectEpoch.current;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      if (epoch === projectEpoch.current)
        setError(String((e as Error).message));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  useEffect(() => {
    localStorage.setItem("gx.locale", locale);
    document.documentElement.lang = locale;
  }, [locale]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gx.theme", theme);
  }, [theme]);
  useEffect(() => {
    let stopped = false;
    const credential = bootstrapToken;
    bootstrapToken = "";
    (async () => {
      try {
        const value = credential
          ? await api<Session>("/session", "POST", { token: credential })
          : await api<Session>("/session");
        if (!stopped) {
          setSession(value);
          updateSession(value);
        }
      } catch {
        /* login form handles absent/expired session */
      } finally {
        if (!stopped) setConnecting(false);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);
  useEffect(() => {
    if (!session) return;
    let stopped = false;
    api<{ projects: Project[] }>("/projects")
      .then((result) => {
        if (stopped) return;
        setProjects(result.projects);
        setPid((selected) =>
          selected && result.projects.some((p) => p.id === selected)
            ? selected
            : result.projects[0]?.id || "",
        );
      })
      .catch((e) => setError(e.message));
    api<ModelSettings>("/settings")
      .then((v) => {
        if (!stopped) setSettings(v);
      })
      .catch((e) => setError(e.message));
    api<Record<string, Json>>("/environment")
      .then((v) => {
        if (!stopped) setEnvironment(v);
      })
      .catch((e) => setError(e.message));
    return () => {
      stopped = true;
    };
  }, [session, refresh]);
  useEffect(() => {
    if (!session) return;
    let stopped = false;
    const read = () => api<ApprovalSettings>("/settings/approval").then((value) => {
      if (!stopped) { setApprovalSettings(value); setSettingsError(""); }
    }).catch((e) => { if (!stopped) setSettingsError(e.message); });
    void read();
    const interval = setInterval(() => void read(), 5000);
    return () => { stopped = true; clearInterval(interval); };
  }, [session, settingsRetry]);
  useEffect(() => {
    if (!pid || !session) {
      setProject(null);
      return;
    }
    let stopped = false;
    setLoading(true);
    history.replaceState(null, "", `?project=${encodeURIComponent(pid)}`);
    api<Project>(`/projects/${pid}`)
      .then((value) => {
        if (stopped) return;
        setProject(value);
        syncComposerRoute(value);
        if (!value.versions?.length && value.target_mode === "fbd") setTab("fbd");
        const binding = value.id + ":" + (value.confirmed_spec_hash || "");
        if (specBinding.current !== binding) {
          specBinding.current = binding;
          setSpec((value.confirmed_spec as Spec) || null);
        }
        setVid((old) =>
          value.versions?.some((v) => v.id === old)
            ? old
            : value.active_version_id || value.versions?.[0]?.id || "",
        );
      })
      .catch((e) => setError(e.message))
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, [pid, session, refresh]);
  useEffect(() => {
    setScopeEnabled(false);
    setScopeNetworks("");
    setScopeAddresses("");
    setNetwork(old => old?.version_id === vid ? old : null);
    setIssueContext(old => old?.versionId === vid ? old : undefined);
    setReport(null);
  }, [pid, vid]);
  useEffect(() => {
    if (!pid || !vid || !session) {
      setProgram(null);
      setSt("");
      return;
    }
    let stopped = false;
    const path = `/projects/${pid}/versions/${vid}`;
    api<Record<string, Json>>(path + "/program")
      .then((v) => {
        if (!stopped) setProgram(v);
      })
      .catch((e) => setError(e.message));
    const artifact = version?.artifacts?.find(
      (a) => ["st_from_ir", "st"].includes(a.id) && a.available,
    );
    if (artifact)
      fetch(artifactUrl(pid, vid, artifact.id))
        .then(async (r) => {
          if (!r.ok) throw new Error(t("此版本没有该产物"));
          return r.text();
        })
        .then((v) => {
          if (!stopped) setSt(v);
        })
        .catch((e) => setError(e.message));
    else setSt("");
    return () => {
      stopped = true;
    };
  }, [pid, vid, version, session, t]);
  useEffect(() => {
    if (version?.target_mode === "fbd") setTab("fbd");
  }, [version?.id, version?.target_mode]);
  useEffect(() => {
    if (!session) return;
    let stopped = false;
    async function poll() {
      try {
        const [j, p] = await Promise.all([
          api<{ jobs: Job[] }>(`/jobs${pid ? `?project_id=${pid}` : ""}`),
          api<{ proposals: Proposal[] }>(
            `/proposals${pid ? `?project_id=${pid}` : ""}`,
          ),
        ]);
        if (!stopped) {
          setJobs(j.jobs);
          setProposals(p.proposals);
          setJobId((old) =>
            j.jobs.some((item) => item.id === old) ? old : j.jobs[0]?.id || "",
          );
        }
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    }
    void poll();
    const interval = setInterval(poll, 2500);
    return () => {
      stopped = true;
      clearInterval(interval);
    };
  }, [session, pid, refresh]);
  useEffect(() => {
    setAnalysisOutput(null);
    if (!jobId || !session) {
      setEvents([]);
      return;
    }
    setEvents([]);
    let sequence = 0;
    let stopped = false;
    const stream = new EventSource(`/api/jobs/${jobId}/events`);
    stream.onmessage = (event) => {
      const value = JSON.parse(event.data) as JobEvent;
      if (value.sequence <= sequence) return;
      sequence = value.sequence;
      setEvents((old) => [...old, value]);
      if (["running", "completed", "failed", "cancelled", "interrupted"].includes(value.event_type)) {
        setJobs((old) => old.map((job) => job.id === value.job_id
          ? { ...job, status: value.event_type,
              ...(value.event_type === "failed" ? {error_code: value.payload?.error_code as Job["error_code"],
                error_details: value.payload?.error_details as Job["error_details"]} : {}),
              ...(value.payload?.result ? { result: value.payload.result as Record<string, Json> } : {}) }
          : job));
      }
      if (
        ["completed", "failed", "cancelled", "interrupted"].includes(
          value.event_type,
        )
      ) {
        stream.close();
        if (value.event_type === "completed")
          api<Record<string, Json>>(`/jobs/${jobId}/output`)
            .then((output) => {
              if (stopped || activeProjectRef.current !== value.project_id)
                return;
              if (output.spec_draft && !consumedDrafts.current.has(jobId)) {
                setAnalysisOutput(output);
              }
            })
            .catch(() => {});
        const eventResult = value.payload?.result;
        const savedVersionId = value.event_type === "completed" &&
          !!eventResult && typeof eventResult === "object" && !Array.isArray(eventResult) &&
          typeof (eventResult as Record<string, Json>).version_id === "string"
            ? String((eventResult as Record<string, Json>).version_id)
            : "";
        if (savedVersionId) {
          void openSavedVersion(savedVersionId).catch((error: Error) => {
            if (!stopped && activeProjectRef.current === value.project_id)
              setError(error.message);
          });
        } else {
          void reloadProjectSilently(value.project_id);
        }
      }
    };
    return () => {
      stopped = true;
      stream.close();
    };
  }, [jobId, session]);
  useEffect(() => {
    if (!session || currentJob?.kind !== "analysis" || currentJob.status !== "completed" ||
        consumedDrafts.current.has(currentJob.id)) return;
    let stopped = false;
    const id = currentJob.id;
    // Polling and reload must recover analysis output even without SSE.
    void api<Record<string, Json>>(`/jobs/${id}/output`).then((output) => {
      if (!stopped && activeProjectRef.current === pid &&
          !consumedDrafts.current.has(id) && output.spec_draft)
        setAnalysisOutput(output);
    }).catch((error: Error) => {
      if (!stopped && activeProjectRef.current === pid) setError(error.message);
    });
    return () => { stopped = true; };
  }, [currentJob?.id, currentJob?.kind, currentJob?.status, pid, session, outputRetry]);
  useEffect(() => {
    if (!analysisOutput?.spec_draft || !project || project.id !== pid ||
        jobId !== jobs[0]?.id || openedDrafts.current.has(jobId) || specDirty.current ||
        (analysisOutput.spec_base_hash ?? null) !== (project.confirmed_spec_hash ?? null) ||
        (analysisOutput.base_version_id ?? null) !== (vid || null)) return;
    openedDrafts.current.add(jobId);
    setSpec(analysisOutput.spec_draft as Spec);
    setSpecIssues([]);
    setPanel("spec");
  }, [analysisOutput, project, pid, vid, jobId, jobs]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!session || busy || loading || specDirty.current ||
        (currentJob?.kind === "generation" && typeof currentJob.result?.version_id === "string") ||
        !resultKey ||
        (!generationResult.versionId && !generationResult.proposalId && !generationResult.blocked) ||
        jobId !== jobs[0]?.id || shownCandidates.current.has(resultKey) ||
        failedPreviews.current.has(resultKey)) return;
    void guarded(() => openGenerationResult());
  }, [resultKey, generationResult.versionId, generationResult.proposalId, generationResult.blocked, jobs, jobId, session, busy, loading]);

  useEffect(() => {
    const saved = currentJob?.result?.version_id;
    if (currentJob?.status !== "completed" || typeof saved !== "string" ||
        !session || busy || loading || specDirty.current || jobId !== jobs[0]?.id) return;
    const key = `${pid}:${jobId}:saved`;
    if (shownCandidates.current.has(key) || failedPreviews.current.has(key)) return;
    void guarded(async () => {
      try { await openSavedVersion(saved); shownCandidates.current.add(key); }
      catch (e) { failedPreviews.current.add(key); throw e; }
    });
  }, [currentJob, session, busy, loading, jobs, jobId, pid]);

  async function openGenerationResult(previewTheme = theme, reread = false) {
    const epoch = projectEpoch.current;
    const key = resultKey;
    if (!key) return;
    try {
      // A manual retry reads the persisted result again rather than relying on
      // SSE, list polling, or an obsolete React state snapshot.
      const output = reread
        ? await api<Record<string, Json>>(`/jobs/${generationResult.id}/output`)
        : generationResult.value;
      if (epoch !== projectEpoch.current) return;
      const blocked = output?.status === "contract_mismatch" || generationResult.blocked;
      const proposalId = typeof output?.proposal_id === "string" ? output.proposal_id : generationResult.proposalId;
      if (blocked) {
        const request = ++previewEpoch.current;
        const loaded = await api<Record<string, Json>>(`/jobs/${generationResult.id}/preview?theme=${previewTheme}`);
        if (epoch !== projectEpoch.current || request !== previewEpoch.current) return;
        setPreview(loaded);
        setSelectedProposal(null);
        setDiagnosticJobId(generationResult.id);
        setTab("ladder");
        setPanel("agent");
      } else if (typeof output?.version_id === "string" || generationResult.versionId) {
        await openSavedVersion(String(output?.version_id || generationResult.versionId), previewTheme);
      } else if (proposalId) {
        const candidate = await api<Proposal>(`/proposals/${proposalId}`);
        if (epoch !== projectEpoch.current || candidate.project_id !== pid) return;
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
      if (epoch === projectEpoch.current) {
        failedPreviews.current.delete(key);
        shownCandidates.current.add(key);
      }
    } catch (error) {
      if (epoch === projectEpoch.current) failedPreviews.current.add(key);
      throw error;
    }
  }

  async function openSavedVersion(versionId: string, previewTheme = theme) {
    const epoch = projectEpoch.current;
    const fresh = await api<Project>(`/projects/${pid}`);
    if (epoch !== projectEpoch.current || fresh.id !== pid) return;
    const saved = fresh.versions?.find((v) => v.id === versionId);
    if (!saved) throw new Error(t("已保存版本尚不可用，请刷新重试。"));
    previewEpoch.current += 1;
    syncComposerRoute(fresh);
    setProject(fresh); setVid(versionId); setPreview(null); setSelectedProposal(null);
    setDiagnosticJobId(""); setTab(saved.target_mode || fresh.target_mode || "ladder"); setPanel("agent");
    if (saved.target_mode === "ladder") await redrawVersion(versionId, previewTheme);
  }

  async function redrawVersion(versionId = vid, previewTheme = theme) {
    const epoch = projectEpoch.current;
    const request = ++previewEpoch.current;
    const drawing = await api<Record<string, Json>>(`/projects/${pid}/versions/${versionId}/preview?theme=${previewTheme}`);
    if (epoch !== projectEpoch.current || request !== previewEpoch.current) return;
    if (typeof drawing.svg !== "string" || !drawing.svg.includes("<svg"))
      throw new Error(t("未取得有效的梯形图预览，请检查诊断。"));
    setVersionDrawing({ key: `${pid}:${versionId}:${previewTheme}`, svg: drawing.svg });
    setTab("ladder");
  }

  async function reloadProjectSilently(targetPid: string | null | undefined = pid) {
    const epoch = projectEpoch.current;
    if (!targetPid || activeProjectRef.current !== targetPid) return;
    const fresh = await api<Project>(`/projects/${targetPid}`);
    if (epoch !== projectEpoch.current ||
        activeProjectRef.current !== targetPid || fresh.id !== targetPid) return;
    syncComposerRoute(fresh);
    setProject(fresh);
    setProjects((old) => old.map((item) => item.id === fresh.id ? fresh : item));
    setVid((old) => fresh.versions?.some((item) => item.id === old)
      ? old
      : fresh.active_version_id || fresh.versions?.[0]?.id || "");
    const binding = fresh.id + ":" + (fresh.confirmed_spec_hash || "");
    if (!specDirty.current && specBinding.current !== binding) {
      specBinding.current = binding;
      setSpec((fresh.confirmed_spec as Spec) || null);
    }
  }

  async function refreshDrawing() {
    const epoch = projectEpoch.current;
    setRefreshingDrawing(true);
    try {
      if (selectedProposal) await showProposal(selectedProposal);
      else if (diagnosticJobId) await openGenerationResult(theme, true);
      else if (version?.target_mode === "ladder") await redrawVersion();
      else if (generationResult.id) await openGenerationResult(theme, true);
      else { setOutputRetry((n) => n + 1); void reloadProjectSilently(pid); return; }
      if (epoch === projectEpoch.current) {
        setOutputRetry((n) => n + 1);
        void reloadProjectSilently(pid);
        setNotice(t("预览已刷新，未调用模型或修改程序。"));
      }
    } finally {
      setRefreshingDrawing(false);
    }
  }

  async function submitJob(
    kind: JobKind = intent,
    extra: Record<string, unknown> = {},
  ) {
    if (!project || project.id !== pid) return;
    const epoch = projectEpoch.current;
    const job = await api<Job>("/jobs", "POST", {
      kind,
      project_id: pid,
      version_id: vid || null,
      request_id: key(),
      text: kind === "generation"
        ? text.trim() || t("请严格按照已确认规格生成候选程序。")
        : text,
      response_language: locale,
      attachment_ids: attachments.map((a) => a.attachment_id),
      ...(["generation", "agent", "gx_read"].includes(kind) && scopeEnabled ? {change_scope: {
        ...(scopeNetworks.trim() ? {network_ids: scopeNetworks.trim().split(/[\s,，]+/)} : {}),
        ...(scopeAddresses.trim() ? {addresses: scopeAddresses.trim().toUpperCase().split(/[\s,，]+/)} : {}),
      }} : {}),
      ...extra,
    });
    if (activeProjectRef.current !== pid || epoch !== projectEpoch.current)
      return;
    setJobId(job.id);
    if (kind === "analysis") specDirty.current = false;
    setJobs((old) => [job, ...old]);
    setPanel("agent");
    setAttachments([]);
    if (kind === "generation") setText("");
  }
  async function repairFailedGeneration(job: Job) {
    const savedInvalid = job.kind === "generation" && job.status === "completed" && job.result?.status === "saved_invalid";
    if ((job.kind !== "generation" || job.error_code !== "generation_validation_failed") && !savedInvalid) return;
    if (!window.confirm(t("只修复当前候选的局部格式/结构错误，不重新分析需求，也不重写完整程序。继续吗？"))) return;
    const repaired = await api<Job>(`/jobs/${encodeURIComponent(job.id)}/repair`, "POST", { request_id: key() });
    if (activeProjectRef.current !== pid) return;
    setEvents([]);
    setJobId(repaired.id);
    setJobs((old) => [repaired, ...old.filter((item) => item.id !== repaired.id)]);
    setPanel("agent");
  }

  async function saveSpec(value: Spec) {
    const epoch = projectEpoch.current;
    const generateAfterSave = currentJob?.kind === "analysis" &&
      currentJob.status === "completed" && !!analysisOutput?.spec_draft &&
      jobId === currentJob.id;
    const result = await api<{ valid: boolean; spec?: Spec; hash?: string; issues?: { errors?: { path: string; message: string }[] } }>(
      `/projects/${pid}/spec`,
      "PUT",
      { spec: value, expected_hash: project?.confirmed_spec_hash ?? null },
    );
    if (activeProjectRef.current !== pid || epoch !== projectEpoch.current)
      return;
    if (!result.valid) {
      setSpecIssues(result.issues?.errors || []);
      return;
    }
    if (!result.spec || !result.hash)
      throw new Error(t("确认规格响应不完整，请刷新后重试。"));
    const savedSpec = result.spec;
    const savedHash = result.hash;
    setSpecIssues([]);
    specDirty.current = false;
    setSpec(savedSpec);
    // Adopt the persisted spec/hash together, before the background refresh.
    specBinding.current = pid + ":" + savedHash;
    setProject((current) => current?.id === pid
      ? { ...current, confirmed_spec: savedSpec, confirmed_spec_hash: savedHash }
      : current);
    if (currentJob?.kind === "analysis") consumedDrafts.current.add(jobId);
    setAnalysisOutput(null);
    setNotice(t("规格已确认"));
    setIntent("generation");
    setPanel("agent");
    if (generateAfterSave) await submitJob("generation");
    else void reloadProjectSilently(pid);
  }
  async function showProposal(value: Proposal, previewTheme = theme) {
    if (value.action === "accept_local" && value.status === "accepted" && typeof value.result?.version_id === "string") {
      await openSavedVersion(value.result.version_id, previewTheme); return;
    }
    if (value.execution_job_id) {
      const running = await api<Job>(`/jobs/${value.execution_job_id}`);
      if (activeProjectRef.current !== value.project_id) return;
      setJobId(running.id); setJobs((old) => [running, ...old.filter((j) => j.id !== running.id)]);
      setPanel("agent"); return;
    }
    const epoch = projectEpoch.current;
    const request = ++previewEpoch.current;
    const loaded = await api<Record<string, Json>>(
      `/proposals/${value.id}/preview?theme=${previewTheme}`,
    );
    if (
      activeProjectRef.current !== value.project_id ||
      epoch !== projectEpoch.current || request !== previewEpoch.current
    )
      return;
    setDiagnosticJobId("");
    setPreview(loaded);
    setSelectedProposal(value);
    setPanel("proposals");
    setTab(loaded.target_mode === "fbd" ? "fbd" : loaded.target_mode === "st" ? "st" : "ladder");
  }
  async function showFBDProposal(value: Proposal) {
    if (activeProjectRef.current !== value.project_id) return;
    setProposals(old => [value, ...old.filter(p => p.id !== value.id)]);
    setModal("");
    await showProposal(value);
  }
  async function decide(value: Proposal, decision: "accept" | "reject") {
    const epoch = projectEpoch.current;
    const result = await api<{ proposal?: Proposal; job?: Job }>(
      `/proposals/${value.id}/decision`,
      "POST",
      { decision },
    );
    if (
      activeProjectRef.current !== value.project_id ||
      epoch !== projectEpoch.current
    )
      return;
    if (result.job) {
      setJobId(result.job.id);
      setJobs((old) => [result.job!, ...old]);
    }
    if (result.proposal?.result?.version_id)
      setVid(String(result.proposal.result.version_id));
    setSelectedProposal(null);
    setPreview(null);
    setNotice(t("操作完成"));
    await reloadProjectSilently(pid);
  }
  async function proposeExecution(
    action: string,
    planId?: string,
    versionId = vid,
  ) {
    const epoch = projectEpoch.current;
    const value = await api<Proposal>("/proposals", "POST", {
      action,
      project_id: pid,
      version_id: versionId,
      plan_id: planId || null,
      request_id: key(),
    });
    if (activeProjectRef.current !== pid || epoch !== projectEpoch.current)
      return;
    setProposals((old) => [value, ...old]);
    await showProposal(value);
  }
  function requestGXSend() {
    if (!canWrite || preview || jobs.some(activeJob) || !operations.gx_import || !project || project.id !== pid || !version) return;
    if (version.target_mode === "fbd") {
      void guarded(() => proposeExecution("gx_import"));
      return;
    }
    setGXSend({ projectId: pid, projectName: project.name, versionId: vid,
      activeVersionId: project.active_version_id || "",
      epoch: projectEpoch.current, requestId: key() });
  }
  async function confirmGXSend() {
    const selection = gxSend;
    if (!selection) return;
    setGXSend(null);
    const isCurrent = () => {
      const current = gxSendSelection.current;
      return current.pid === selection.projectId && current.vid === selection.versionId &&
        current.activeVersionId === selection.activeVersionId && current.epoch === selection.epoch;
    };
    const result = await submitGXSend(selection, isCurrent, api);
    if (!isCurrent()) return;
    setProposals(old => [result.proposal, ...old.filter(p => p.id !== result.proposal.id)]);
    setSelectedProposal(null);
    setPreview(null);
    if (result.job) {
      setJobId(result.job.id);
      setJobs(old => [result.job!, ...old.filter(j => j.id !== result.job!.id)]);
      setPanel("agent");
    }
    await reloadProjectSilently(selection.projectId);
  }
  async function upload(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const uploadProject = pid;
      const added = await api<{ attachment_id: string; filename: string }>(
        `/projects/${pid}/attachments`,
        "POST",
        { filename: file.name, data_base64: encoded },
      );
      if (activeProjectRef.current === uploadProject)
        setAttachments((old) => [...old, added]);
    }
  }
  function resizePanel(event: React.PointerEvent) {
    const start = event.clientX,
      width = rightWidth;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (e: PointerEvent) =>
      setRightWidth(Math.max(300, Math.min(650, width + start - e.clientX)));
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
  }
  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    if (selectedProposal)
      void guarded(() => showProposal(selectedProposal, next));
    else if (diagnosticJobId && diagnosticJobId === generationResult.id)
      void guarded(() => openGenerationResult(next));
    else if (versionDrawing && version?.target_mode === "ladder")
      void guarded(() => redrawVersion(vid, next));
  }
  const model = settings?.profiles?.find(
    (p) => p.id === settings.active_profile_id,
  );
  const visibleProgram = preview
    ? (preview.program as Record<string, Json> | undefined)
    : program;
  const networks = Array.isArray(visibleProgram?.networks)
    ? (visibleProgram.networks as Record<string, Json>[])
    : [];
  const freshVersionSvg = versionDrawing?.key === `${pid}:${vid}:${theme}` ? versionDrawing.svg : "";
  const svg = preview?.svg
    ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(String(preview.svg))}`
    : !preview && freshVersionSvg
      ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(freshVersionSvg)}`
    : !preview && version?.artifacts?.find((a) => a.id === "svg" && a.available)
      ? artifactUrl(pid, vid, "svg") + `?theme=${theme}`
      : "";
  const status = (value?: string | null) => (
    <Badge tone={statusTone(value || "unknown")}>
      {statusText(locale, value || "unknown")}
    </Badge>
  );
  const latestMessage = events
    .filter((e) => e.event_type === "progress")
    .at(-1)?.payload;
  const eventText = (kind: string) =>
    events
      .filter((e) => e.event_type === kind)
      .map((e) => e.payload?.text || e.payload?.message || "")
      .join("");

  if (!session)
    return (
      <div className="login-shell">
        <div className="login-card">
          <CircuitBoard size={38} />
          <h1>
            GXWorks <span>Agent</span>
          </h1>
          <p className="eyebrow">{t("工程工作台")}</p>
          {connecting ? (
            <p>
              <LoaderCircle className="spin" size={16} />{" "}
              {t("正在连接本地服务")}
            </p>
          ) : (
            <form
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                void guarded(async () => {
                  const value = await api<Session>("/session", "POST", {
                    token,
                  });
                  setSession(value);
                  updateSession(value);
                  setToken("");
                });
              }}
            >
              <h2>{t("操作员登录")}</h2>
              <p className="muted">
                {t("输入本地服务启动时提供的操作员令牌。")}
              </p>
              <input
                type="password"
                autoComplete="off"
                required
                aria-label="Operator token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
              <Button variant="primary" disabled={busy}>
                {t("连接")}
              </Button>
            </form>
          )}
          {error && (
            <p role="alert" className="error-text">
              {error}
            </p>
          )}
        </div>
      </div>
    );

  return (
    <div
      className={`workbench ${leftOpen ? "" : "sidebar-hidden"}`}
      style={{ "--inspector-width": `${rightWidth}px` } as CSSProperties}
    >
      <header className="app-header">
        <div className="brand">
          <CircuitBoard size={24} />
          <strong>
            GXWorks <span>Agent</span>
          </strong>
          <span className="edition">WORKBENCH</span>
        </div>
        <div className="header-center">
          <span className="connection-dot" />
          {t("本地服务")}
          <span className="separator">/</span>
          {t("工程工作台")}
        </div>
        <div className="header-actions">
          <button
            className="icon-button"
            disabled={busy}
            aria-label={t(theme === "dark" ? "切换浅色主题" : "切换深色主题")}
            title={t(theme === "dark" ? "切换浅色主题" : "切换深色主题")}
            onClick={toggleTheme}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className={`approval-mode-indicator ${approvalSettings?.mode === "full" ? "full-access" : ""}`}
            title={t("在设置中调整审批模式")} onClick={() => { setSettingsTab("general"); setModal("settings"); }}>
            <ShieldCheck size={14}/>{t(approvalSettings ? approvalLabels[approvalSettings.mode] : "审批设置")}
          </button>
          {session.read_only && <Badge tone="warn">{t("只读恢复")}</Badge>}
          <select
            aria-label={t("响应语言")}
            className="language-select"
            value={locale}
            onChange={(e) => setLocale(e.target.value as Locale)}
          >
            <option value="zh-CN">简体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
          <button
            className="icon-button"
            aria-label={t("设置")}
            title={t("设置")}
            onClick={() => setModal("settings")}
          >
            <Settings2 size={18} />
          </button>
        </div>
      </header>
      <aside className="project-sidebar">
        <div className="sidebar-heading">
          <span>{t("工程")}</span>
          <Button
            variant="ghost"
            aria-label={t("新建工程")}
            disabled={!canWrite}
            onClick={() => setModal("new")}
          >
            <Plus size={16} />
          </Button>
        </div>
        <div className="project-list">
          {projects.map((p) => (
            <button
              key={p.id}
              disabled={busy}
              className={`project-item ${pid === p.id ? "selected" : ""}`}
              onClick={() => {
                if (p.id === pid) return;
                setPid(p.id);
              }}
            >
              <FolderOpen size={17} />
              <span>
                {p.name}
                <small>
                  {p.plc_model} · {p.target_mode.toUpperCase()}
                </small>
              </span>
              {pid === p.id && <ChevronRight size={14} />}
            </button>
          ))}
        </div>
        <div className="sidebar-heading versions-title">
          <span>{t("版本历史")}</span>
          <History size={15} />
        </div>
        <div className="version-list">
          {project?.versions
            ?.slice()
            .reverse()
            .map((v) => (
              <button
                className={`version-item ${v.id === vid ? "selected" : ""}`}
                key={v.id}
                disabled={busy}
                onClick={() => {
                  previewEpoch.current += 1;
                  setDiagnosticJobId("");
                  setVersionDrawing(null);
                  setVid(v.id);
                  setPreview(null);
                  setSelectedProposal(null);
                }}
              >
                <GitBranch size={15} />
                <span>
                  <strong className="mono">{v.id}</strong>
                  <small>
                    {new Date(v.created_at || "").toLocaleString(locale, {
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </small>
                </span>
                {v.id === project.active_version_id && (
                  <span className="active-version" title={t("当前版本")} />
                )}
              </button>
            ))}
          {!project?.versions?.length && (
            <p className="sidebar-empty">{t("暂无版本")}</p>
          )}
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setModal("environment")}>
            <Activity size={16} />
            <span>GX Works2</span>
            <span className="muted">
              {String(environment.status || t("未运行"))}
            </span>
          </button>
          <button onClick={() => setModal("settings")}>
            <Bot size={16} />
            <span>{model?.name || t("模型设置")}</span>
            <span
              className={`small-dot ${model?.configured ? "good" : "warn"}`}
            />
          </button>
        </div>
      </aside>
      <main className="engineering-area">
        <div className="project-bar">
          <button
            className="icon-button"
            title={t("工程")}
            onClick={() => setLeftOpen((v) => !v)}
          >
            <PanelLeftClose size={17} />
          </button>
          <div className="project-title">
            <h1>{project?.name || t("选择工程")}</h1>
            <span>
              {project?.plc_model || "MITSUBISHI FX"}
              {vid && (
                <>
                  {" "}
                  <span className="separator">/</span>{" "}
                  <span className="mono">{vid}</span>
                </>
              )}
            </span>
          </div>
          {version && status(displayedVersionStatus)}
        </div>
        <ProjectToolbar pid={pid} vid={vid} artifacts={version?.artifacts || []}
          exportable={!!version && !preview} canRead={canWrite && !!pid && version?.target_mode !== "fbd"}
          canSend={canWrite && !preview && !jobs.some(activeJob) && !!operations.gx_import}
          canRefresh={!!session && !!pid && !busy && !loading && !jobs.some(activeJob)}
          refreshing={refreshingDrawing} onRead={() => void guarded(() => submitJob("gx_read"))}
          onSend={requestGXSend} onRefresh={() => void guarded(refreshDrawing)} t={t}
          more={<>
            <Button disabled={!canWrite || !pid} onClick={() => setModal("fbd-import")}><FolderOpen size={15}/>{t("导入 GXW")}</Button>
            <Button disabled={!canWrite || !pid || version?.target_mode === "fbd"} onClick={() => void guarded(() => submitJob("gx_inspect"))}>
              <GitBranch size={15}/>{t("检查同步")}
            </Button>
            {operations.fbd_convert && <Button disabled={!canWrite || !!preview} onClick={() => void guarded(async () => {
              const result = await api<Proposal>("/fbd/proposals", "POST", {operation:"convert",project_id:pid,version_id:vid,request_id:key()});
              await showFBDProposal(result);
            })}>{t("转换为 FBD")}</Button>}
            {version && vid !== project?.active_version_id && <Button disabled={!canWrite} onClick={() => void guarded(async () => {
              await api(`/projects/${pid}/active-version`, "POST", {version_id:vid,expected_active_version_id:project?.active_version_id});
              await reloadProjectSilently(pid);
            })}>{t("设为当前版本")}</Button>}
          </>}/>

        <nav className="editor-tabs">
          {(
            [
              ["ladder", t("梯形图"), <Workflow size={15} />],
              ["fbd", "FBD", <GitBranch size={15} />],
              ["st", "ST", <Code2 size={15} />],
              ["diagnostics", t("诊断"), <ShieldCheck size={15} />],
              ["reports", t("检查报告"), <FileCheck2 size={15} />],
              ["simulation", t("仿真记录"), <Activity size={15} />],
              ["delivery", t("工程交付摘要"), <FileCheck2 size={15} />],
            ] as [string, string, ReactNode][]
          ).map(([id, title, icon]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => {setTab(id); if (["diagnostics","reports","simulation","delivery"].includes(id)) {previewEpoch.current += 1;setPreview(null);setSelectedProposal(null);setDiagnosticJobId("");}}}
            >
              {icon}
              {title}
            </button>
          ))}
        </nav>
        {diagnosticJobId && (
          <div className="preview-banner" role="status">
            <ShieldCheck size={16} />
            <strong>{t("受阻候选预览（不可接受）")}</strong>
            <span>{t("未满足确认方案；不提供接受或导入操作。")}</span>
            <Button variant="ghost" onClick={() => { previewEpoch.current += 1; setDiagnosticJobId(""); setPreview(null); }}>{t("返回版本")}</Button>
          </div>
        )}
        {selectedProposal && (
          <div className="preview-banner">
            <GitBranch size={16} />
            <strong>{t("候选预览")}</strong>
            <span>
              {t("基础版本")}{" "}
              {selectedProposal.base_version_id || t("首次生成")}
            </span>
            {status(selectedProposal.status)}
            <Button
              variant="ghost"
              onClick={() => {
                previewEpoch.current += 1;
                setPreview(null);
                setSelectedProposal(null);
              }}
            >
              <ArrowLeft size={14} />
              {t("返回版本")}
            </Button>
          </div>
        )}
        <div className="editor-content">
          {loading ? (
            <div className="empty-state">
              <LoaderCircle size={28} className="spin" />
              <p>{t("正在读取工程")}</p>
            </div>
          ) : !project ? (
            <div className="empty-state">
              <FolderOpen size={42} />
              <h2>{t("打开本地工程")}</h2>
              <p>{t("选择已有工程，或创建一个新工程开始。")}</p>
              <Button
                variant="primary"
                disabled={!canWrite}
                onClick={() => setModal("new")}
              >
                <Plus size={16} />
                {t("新建工程")}
              </Button>
            </div>
          ) : tab === "fbd" && (preview?.target_mode === "fbd" || (!preview && (version?.target_mode === "fbd" || (!version && project.target_mode === "fbd")))) ? (
            <FBDPanel key={`${pid}:${vid}:${selectedProposal?.id || "version"}`} value={Array.isArray(visibleProgram?.nodes) ? visibleProgram as unknown as FBDModel : emptyFBD()}
              svg={svg} pid={pid} vid={vid} readOnly={!canWrite} preview={!!preview} t={t} onProposal={showFBDProposal} />
          ) : tab === "fbd" ? (
            <div className="empty-state"><GitBranch size={38}/><h2>{t("结构化梯形图/FBD")}</h2><p>{t("导入 GXW 工程，或将当前梯形图转换为 FBD。也可以新建 FBD 工程直接生成。")}</p></div>
          ) : !version && !preview ? (
            generationResult.id ? (<div className="empty-state">
              <Workflow size={44} />
              <h2>{t(generationResult.id
                ? generationResult.blocked ? "候选与确认方案冲突"
                  : generationResult.loading ? "正在读取生成结果"
                  : generationResult.proposalId ? "程序已保存，等待查看"
                  : "生成结果暂不可用"
                : "工程中还没有程序")}</h2>
              <p>{t(generationResult.id
                ? "请在 Agent 面板查看生成结果及具体诊断。"
                : "描述控制需求，确认规格后生成第一个程序。")}</p>
              {generationResult.id && <Button onClick={() => setPanel("agent")}>{t("查看生成结果")}</Button>}
              <Badge>
                {project.plc_model} · {project.target_mode.toUpperCase()}
              </Badge>
            </div>) : (<FirstProjectGuide t={t} hasSpec={!!project.confirmed_spec} disabled={!canWrite || jobs.some(activeJob)}
              onExample={value=>{setText(value);setIntent("analysis");setPanel("agent");}}
              onSpec={()=>setPanel("spec")} onGenerate={()=>void guarded(()=>submitJob("generation"))} />)
          ) : tab === "delivery" ? (
            <DeliverySummary key={`${pid}:${vid}`} pid={pid} vid={vid} t={t} refreshKey={refresh} readOnly={!canWrite}/>
          ) : tab === "ladder" ? (
            <ProgramExplorer key={`${pid}:${vid}:${selectedProposal?.id || diagnosticJobId || "version"}`} pid={pid} vid={vid} proposalId={preview ? selectedProposal?.id : undefined}
              jobId={preview && diagnosticJobId ? diagnosticJobId : undefined} refreshKey={versionDrawing}
              changeSummary={preview ? selectedProposal?.summary : proposals.find(p=>p.action === "accept_local" && p.status === "accepted" && p.result?.version_id === vid)?.summary}
              theme={theme} selectedNetwork={String(network?.id || "")} initialAddress={jumpVersion === vid ? jumpAddress : ""} t={t}
              onNetwork={id => setNetwork({...networks.find(n => n.id === id), id, version_id:vid})} />
          ) : tab === "st" ? (
            <div className="code-view">
              <header>
                <FileCode2 size={15} />
                {preview
                  ? "candidate.st"
                  : version?.artifacts?.find((a) => a.id.includes("st"))
                      ?.filename || "ST"}
                <span className="spacer" />
                {!preview &&
                  version?.artifacts
                    ?.filter(
                      (a) => ["st", "st_from_ir"].includes(a.id) && a.available,
                    )
                    .map((a) => (
                      <a key={a.id} href={artifactUrl(pid, vid, a.id, true)}>
                        <ArrowDownToLine size={14} />
                        {t("下载")}
                      </a>
                    ))}
              </header>
              <pre>
                {String((preview ? preview.st : st) || t("此版本没有该产物"))}
              </pre>
            </div>
          ) : tab === "diagnostics" ? (
            <div className="document-view">
              <div className="content-heading">
                <h2>{t("诊断")}</h2>
                <Button
                  disabled={!canWrite || !operations.diagnose}
                  onClick={() =>
                    void guarded(() => submitJob("review", { deep: false }))
                  }
                >
                  <ShieldCheck size={15} />
                  {t("本地检查")}
                </Button>
              </div>
              <IssueCards pid={pid} vid={vid} readOnly={!canWrite || !operations.simulation} t={t} refreshKey={refresh}
                onNetwork={(id,address)=>{setNetwork({...networks.find(n=>n.id===id),id,version_id:vid});setJumpAddress(address||"");setJumpVersion(vid);setTab("ladder");}}
                onTest={issue=>{setIssueContext({...issue,versionId:vid});setTab("simulation");}}/>
            </div>
          ) : tab === "reports" ? (
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
                        setReport(
                          await api(`/projects/${pid}/reports/${r.report_id}`),
                        );
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
              {version?.target_mode === "ladder" ? <SimulationWorkbench key={`${pid}:${vid}`} pid={pid} vid={vid} readOnly={!canWrite || !operations.simulation}
                t={t} refreshKey={`${refresh}:${jobs.filter(job => !activeJob(job)).map(job => `${job.id}:${job.status}`).join("|")}`} onSaved={refreshAll} issueContext={issueContext} initialPlanId={issueContext?.planId}
                onExecute={planId=>guarded(()=>proposeExecution("simulation",planId))}
                onDebug={runId=>guarded(()=>submitJob("debug_plan",{run_id:runId}))}/> : <p>{t("此程序形式尚未接通仿真。")}</p>}
            </div>
          )}
        </div>
        {version && !preview && (
          <footer className="artifact-footer">
            <span>
              <FileCode2 size={13} />
              {t("产物来自后端工程核心")}
            </span>
            <div>
              {(version.artifacts || [])
                .filter(
                  (a) =>
                    a.available && !["svg", "st", "st_from_ir"].includes(a.id),
                )
                .map((a: Artifact) => (
                  <a href={artifactUrl(pid, vid, a.id, true)} key={a.id}>
                    {t(artifactLabel(a.id))} <ArrowDownToLine size={12} />
                  </a>
                ))}
            </div>
          </footer>
        )}
      </main>
      <div
        role="separator"
        aria-label={t("检查器")}
        aria-orientation="vertical"
        tabIndex={0}
        className="panel-resizer"
        onPointerDown={resizePanel}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft")
            setRightWidth((v) => Math.min(650, v + 20));
          if (e.key === "ArrowRight")
            setRightWidth((v) => Math.max(300, v - 20));
        }}
      />
      <aside className="agent-sidebar">
        <nav className="panel-tabs">
          {[
            ["agent", "Agent"],
            ["spec", t("规格")],
            [
              "proposals",
              `${t("审批记录")}${pendingCount ? ` ${pendingCount}` : ""}`,
            ],
            ["inspector", t("检查器")],
          ].map(([id, label]) => (
            <button
              key={id}
              className={panel === id ? "active" : ""}
              onClick={() => setPanel(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        {panel === "agent" ? (
          <>
            <div className="agent-heading">
              <Bot size={20} />
              <div>
                <strong>Engineering Agent</strong>
                <small>{model?.model || t("未配置密钥")}</small>
              </div>
              <Badge>{locale}</Badge>
            </div>
            <div className="conversation">
              <div className="agent-intro">
                <span className="agent-mark">
                  <CircuitBoard size={22} />
                </span>
                <h2>{t("工程工作台")}</h2>
                <p>{t(hasSavedVersions ? "描述希望修改的行为，或查看当前程序与验证结果。" : "描述控制需求，确认规格后生成第一个程序。")}</p>
                <div className="context-chips">
                  <Badge>{project?.plc_model || "FX3U"}</Badge>
                  {vid && <Badge>{vid}</Badge>}
                  {project?.confirmed_spec && (
                    <Badge tone="good">
                      <Check size={12} />
                      {t("规格已确认")}
                    </Badge>
                  )}
                </div>
              </div>
              {project?.id === pid && !hasSavedVersions && !!project.confirmed_spec && (
                <div className="candidate-diff">
                  <p>{t("规格已确认。下一步生成程序，无需重新输入需求。")}</p>
                  <Button
                    variant="primary"
                    disabled={!canWrite || !canGenerate}
                    onClick={() => void guarded(() => submitJob("generation"))}
                  >
                    <Play size={14} />
                    {t("按已确认规格生成程序")}
                  </Button>
                  {specDirty.current && <p className="muted">{t("规格有未确认修改，请先确认后再生成。")}</p>}
                </div>
              )}
              {currentJob && (
                <div className="job-conversation">
                  <div className="job-line">
                    <span>{currentJob.kind}</span>
                    {status(displayedJobStatus)}
                  </div>
                  <JobProgress job={currentJob} events={events} t={t} />
                  {events.some((e) =>
                    e.event_type === "progress",
                  ) && (
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
                            {t(String(
                              e.payload?.message ||
                                e.payload?.text ||
                                e.payload?.stage ||
                                "",
                            ))}
                          </p>
                        ))}
                    </details>
                  )}
                  {currentJob.kind !== "analysis" && currentJob.kind !== "generation" && currentJob.status === "completed" && eventText("content") && (
                    <AcceptedMessage
                      kind={currentJob.kind}
                      text={eventText("content")}
                      t={t}
                    />
                  )}
                  <GenerationResult result={generationResult} busy={busy || loading}
                    onOpen={() => void guarded(() => openGenerationResult())}
                    onRetry={() => { setOutputRetry((n) => n + 1); void reloadProjectSilently(pid); }}
                    onRepair={() => { if (currentJob) void guarded(() => repairFailedGeneration(currentJob)); }}
                    onSpec={() => setPanel("spec")} t={t} />
                  <JobFailure job={currentJob} busy={!canWrite}
                    onRepair={() => void guarded(() => repairFailedGeneration(currentJob))} t={t} />
                  {!!analysisOutput?.spec_draft && (
                    <div>
                      <Button
                        disabled={
                          !canWrite ||
                          (analysisOutput.spec_base_hash ?? null) !==
                            (project?.confirmed_spec_hash ?? null) ||
                          (analysisOutput.base_version_id ?? null) !==
                            (vid || null)
                        }
                        onClick={() => {
                          setSpec(analysisOutput.spec_draft as Spec);
                          specDirty.current = false;
                          setSpecIssues([]);
                          setPanel("spec");
                        }}
                      >
                        {t("编辑并确认规格")}
                      </Button>
                      {(analysisOutput.spec_base_hash ?? null) !==
                        (project?.confirmed_spec_hash ?? null) && (
                        <p className="muted">
                          {t(project?.confirmed_spec
                            ? "该分析草稿早于当前确认规格，可直接使用当前规格生成。"
                            : "规格已变化，请重新分析后再应用草稿。")}
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
                            currentJob.kind === "debug_plan"
                              ? "debug"
                              : "simulation",
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
              <p className="acceptance-note">
                <ShieldCheck size={13} />
                {t("程序校验通过后自动保存；可在版本历史中查看或回退。")}
              </p>
            </div>
            {version?.target_mode === "ladder" && <details className="scope-controls" open={scopeEnabled}>
              <summary>{t("修改范围")}{scopeEnabled ? ` · ${t("局部约束已启用")}` : ` · ${t("整个程序")}`}</summary>
              <label><input type="checkbox" style={{width:"auto"}} checked={scopeEnabled} onChange={e=>setScopeEnabled(e.target.checked)}/> {t("只允许修改指定范围")}</label>
              {scopeEnabled && <><label>{t("允许修改的网络")}<input value={scopeNetworks} onChange={e=>setScopeNetworks(e.target.value)} placeholder="N0001, N0002"/></label>
                <Button disabled={!network} onClick={()=>setScopeNetworks(String(network?.id||""))}>{t("使用选中网络")}</Button>
                <label>{t("允许涉及的地址")}<input value={scopeAddresses} onChange={e=>setScopeAddresses(e.target.value)} placeholder="X0, Y0, M0"/></label>
                <small>{t("至少填写一项；同时填写时两项都必须满足。地址范围包含变更网络修改前后的所有读写地址。")}</small></>}
            </details>}
            <div className="composer">
              <div className="composer-mode">
                <select
                  aria-label={t("输入方式")}
                  value={intent}
                  onChange={(e) => setIntent(e.target.value as typeof intent)}
                >
                  <option value="analysis">{t("分析需求")}</option>
                  <option value="generation">{t("生成程序")}</option>
                  <option value="agent">{t("询问 Agent")}</option>
                </select>
                <button
                  className="text-button"
                  disabled={!canWrite}
                  onClick={() => setModal("sfc")}
                >
                  SFC <Workflow size={13} />
                </button>
              </div>
              <textarea
                aria-label={t("描述你的控制需求…")}
                placeholder={t(intent === "generation"
                  ? "可补充生成要求；留空则按已确认规格生成。"
                  : "描述你的控制需求…")}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    (e.ctrlKey || e.metaKey) &&
                    e.key === "Enter" &&
                    canSubmit
                  ) {
                    e.preventDefault();
                    void guarded(() => submitJob());
                  }
                }}
              />
              {attachments.length > 0 && (
                <div className="attachment-chips">
                  {attachments.map((a) => (
                    <button
                      key={a.attachment_id}
                      onClick={() =>
                        setAttachments((items) =>
                          items.filter(
                            (v) => v.attachment_id !== a.attachment_id,
                          ),
                        )
                      }
                    >
                      {a.filename}
                      <X size={12} />
                    </button>
                  ))}
                </div>
              )}
              <div className="composer-actions">
                <input
                  ref={uploadRef}
                  type="file"
                  hidden
                  multiple
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  onChange={(e) => void guarded(() => upload(e.target.files))}
                />
                <Button
                  variant="ghost"
                  aria-label={t("添加图片")}
                  disabled={!canWrite || !pid}
                  onClick={() => uploadRef.current?.click()}
                >
                  <Paperclip size={16} />
                </Button>
                <span className="muted">Ctrl ↵</span>
                <Button
                  variant="primary"
                  disabled={!canSubmit}
                  onClick={() => void guarded(() => submitJob())}
                >
                  <Send size={14} />
                  {t(intent === "generation" ? "生成程序" : "发送")}
                </Button>
              </div>
            </div>
          </>
        ) : panel === "spec" ? (
          <SpecEditor
            value={spec}
            issues={specIssues}
            onChange={(value) => { specDirty.current = true; setSpec(value); setSpecIssues([]); }}
            t={t}
            disabled={!canWrite || !pid}
            onSave={(s) => void guarded(() => saveSpec(s))}
          />
        ) : panel === "proposals" ? (
          <div className="proposal-list">
            {proposals.some((p) => p.action !== "accept_local" || p.status === "pending") ? (
              proposals.filter((p) => p.action !== "accept_local" || p.status === "pending").map((p) => (
                <section
                  className={`proposal-card ${selectedProposal?.id === p.id ? "selected" : ""}`}
                  key={p.id}
                >
                  <div className="job-line">
                    <GitBranch size={15} />
                    <strong>
                      {p.action === "accept_local"
                        ? t("保存候选")
                        : p.action}
                    </strong>
                    {status(p.status)}
                  </div>
                  <p>{String(p.summary?.summary || "")}</p>
                  <div className="proposal-meta">
                    {t("基础版本")}{" "}
                    <span className="mono">
                      {p.base_version_id || t("首次生成")}
                    </span>
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
                  {!!p.summary?.impact && <div className="candidate-diff"><strong>{t("变更影响")}</strong><DataView value={p.summary.impact}/>
                    {!!p.summary.change_scope && <><strong>{t("修改范围")}</strong><DataView value={p.summary.change_scope}/></>}</div>}
                  {selectedProposal?.id === p.id && !!preview?.diff && (
                    <DiffView value={preview.diff} t={t} />
                  )}
                  {selectedProposal?.id === p.id && !!preview?.action && (
                    <div className="candidate-diff">
                      <p>
                        {t("审批绑定版本")}{" "}
                        <strong>{String(preview.version_id || "")}</strong>
                      </p>
                      {!!preview.plan && (
                        <>
                          <h3>{t("指定执行方案")}</h3>
                          <DataView value={preview.plan} />
                        </>
                      )}
                    </div>
                  )}
                  <ConditionNormalization value={p.summary?.normalization} t={t}/>
                  {!!p.summary?.validation && (
                    <DataView value={p.summary.validation} />
                  )}
                  <div className="proposal-actions">
                    {p.status === "pending" && (
                      <>
                        <Button
                          variant="ghost"
                          disabled={!canWrite}
                          onClick={() =>
                            void guarded(() => decide(p, "reject"))
                          }
                        >
                          {t("拒绝")}
                        </Button>
                        <Button
                          variant="primary"
                          disabled={!canWrite || selectedProposal?.id !== p.id}
                          title={t("预览与差异")}
                          onClick={() =>
                            void guarded(() => decide(p, "accept"))
                          }
                        >
                          <Check size={14} />
                          {t(
                            p.action === "accept_local"
                              ? "保存候选"
                              : "批准执行",
                          )}
                        </Button>
                      </>
                    )}
                  </div>
                  {p.result && (
                    <div className="execution-result">
                      {t("执行结果")}{" "}
                      {status(String(p.result.status || p.status))}
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
            <p className="muted footnote">
              {t("本地版本不代表 GX 编译或仿真通过。")}
            </p>
          </div>
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
      <footer className="task-dock">
        <div>
          <Activity size={15} />
          <strong>{t("任务")}</strong>
          <select
            aria-label={t("任务记录")}
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
          >
            <option value="">{t("暂无任务")}</option>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.kind} · {statusText(locale, j.id === jobId ? displayedJobStatus || j.status : j.result?.status === "contract_mismatch" ? "contract_mismatch" : j.status)} · {j.id.slice(-6)}
              </option>
            ))}
          </select>
          {currentJob && status(displayedJobStatus)}
          {currentJob && activeJob(currentJob) && (
            <Button
              variant="ghost"
              title={t("安全检查点取消")}
              onClick={() =>
                void guarded(async () => {
                  await api(`/jobs/${jobId}/cancel`, "POST");
                  void reloadProjectSilently(pid);
                })
              }
            >
              <Square size={11} />
              {t("取消请求")}
            </Button>
          )}
        </div>
        <span className="dock-progress">
          {String(
            latestMessage?.message ||
              latestMessage?.text ||
              t("后端任务会在关闭或刷新页面后继续。"),
          )}
        </span>
        <span className="local-indicator">
          <span className="connection-dot" />
          {t("已连接")}
        </span>
      </footer>
      {(error || notice) && (
        <div
          className={`toast ${error ? "toast-error" : ""}`}
          role={error ? "alert" : "status"}
        >
          <span>{error || notice}</span>
          <button
            className="icon-button"
            aria-label={t("关闭")}
            onClick={() => {
              setError("");
              setNotice("");
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}
      <Modal open={!!gxSend} onOpenChange={open => !open && setGXSend(null)} title={t("发送前请备份")}
        description={t("发送将覆盖 GX Works2 当前 MAIN 和相关软元件注释。请先自行备份目标工程；本次不会自动备份或检查外部修改。")}
      >
        <p>{t("本地项目")}：<strong>{gxSend?.projectName}</strong></p>
        <p>{t("待发送版本")}：<strong>{gxSend?.versionId}</strong></p>
        <div className="proposal-actions">
          <Button onClick={() => setGXSend(null)}>{t("取消")}</Button>
          <Button variant="primary" disabled={!canWrite} onClick={() => void guarded(confirmGXSend)}>{t("继续发送")}</Button>
        </div>
      </Modal>
      <Modal
        open={modal === "new"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("新建工程")}
      >
        <form
          className="form"
          onSubmit={(e) => {
            e.preventDefault();
            void guarded(async () => {
              const p = await api<Project>("/projects", "POST", {
                name: newName,
                target_mode: newMode,
                plc_model: "FX3U",
              });
              setPid(p.id);
              setVid("");
              setModal("");
              setNewName("");
              refreshAll();
            });
          }}
        >
          <label>
            {t("项目名称")}
            <input
              autoFocus
              required
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </label>
          <label>
            {t("程序形式")}
            <select
              value={newMode}
              onChange={(e) => setNewMode(e.target.value as typeof newMode)}
            >
              <option value="ladder">{t("梯形图")} · FX3U</option>
              <option value="st">ST · FX3U</option>
              <option value="fbd">FBD / {t("结构化梯形图")} · FX3U</option>
            </select>
          </label>
          <Button variant="primary" disabled={busy}>
            <Plus size={15} />
            {t("创建")}
          </Button>
        </form>
      </Modal>
      <Modal open={modal === "fbd-import"} onOpenChange={v => !v && setModal("")} title={t("导入 GXW 工程")}>
        <FBDImport key={`${pid}:${vid}`} pid={pid} vid={vid} disabled={!canWrite} onProposal={showFBDProposal} t={t}/>
      </Modal>
      <Modal
        open={modal === "settings"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("设置")}
      >
        <nav className="settings-tabs" aria-label={t("设置分类")}>
          <Button variant={settingsTab === "general" ? "primary" : "ghost"} onClick={() => setSettingsTab("general")}>{t("通用与审批")}</Button>
          <Button variant={settingsTab === "model" ? "primary" : "ghost"} onClick={() => setSettingsTab("model")}>{t("模型")}</Button>
        </nav>
        {settingsTab === "general" && (approvalSettings ? <ApprovalSettingsPanel value={approvalSettings}
          disabled={!!session.read_only} onChange={setApprovalSettings} t={t}/> : <p>{t("正在读取设置")}</p>)}
        {settingsTab === "general" && settingsError && <p role="alert" className="error-text">{settingsError}
          <Button onClick={() => setSettingsRetry((n) => n + 1)}>{t("重试")}</Button></p>}
        {settingsTab === "model" && settings && (
          <Settings
            value={settings}
            t={t}
            disabled={!session || !!session.read_only}
            onChange={setSettings}
          />
        )}
      </Modal>
      <Modal
        open={modal === "environment"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("环境详情")}
      >
        <DataView value={environment} />
      </Modal>
      <Modal
        open={modal === "report"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("检查报告详情")}
      >
        {modal === "report" && report && typeof report === "object" && !Array.isArray(report) && Array.isArray(report.findings) && report.report_id && report.base_version_id ?
          <IssueCards pid={pid} vid={String(report.base_version_id)} reportId={String(report.report_id)} readOnly={!canWrite} t={t}
            onNetwork={(id,address)=>{setPreview(null);setSelectedProposal(null);setVid(String(report.base_version_id));setNetwork({id,version_id:String(report.base_version_id)});setJumpAddress(address||"");setJumpVersion(String(report.base_version_id));setTab("ladder");setModal("");}}
            onTest={issue=>{setPreview(null);setSelectedProposal(null);setVid(String(report.base_version_id));setIssueContext({...issue,versionId:String(report.base_version_id)});setTab("simulation");setModal("");}}/> : <DataView value={report}/>}
      </Modal>
      <Modal
        open={modal === "sfc"}
        onOpenChange={(v) => !v && setModal("")}
        title={t("SFC 需求输入")}
        description={t("此输入生成需求文本，不编译 GX SFC 工程。")}
      >
        <div className="sfc-steps">
          {steps.map((s, i) => (
            <div className="sfc-step" key={i}>
              <span className="step-number">{i + 1}</span>
              <div>
                {(["name", "action", "transition"] as const).map((field, n) => (
                  <input
                    key={field}
                    aria-label={t(["步骤名称", "动作", "转移条件"][n])}
                    placeholder={t(["步骤名称", "动作", "转移条件"][n])}
                    value={s[field]}
                    onChange={(e) =>
                      setSteps((old) =>
                        old.map((item, j) =>
                          i === j ? { ...item, [field]: e.target.value } : item,
                        ),
                      )
                    }
                  />
                ))}
              </div>
              <Button
                variant="ghost"
                aria-label={t("清除")}
                disabled={steps.length <= 1}
                onClick={() => setSteps((old) => old.filter((_, j) => i !== j))}
              >
                <X size={14} />
              </Button>
            </div>
          ))}
        </div>
        <div className="form-actions">
          <Button
            onClick={() =>
              setSteps((old) => [
                ...old,
                { name: "", action: "", transition: "" },
              ])
            }
          >
            <Plus size={14} />
            {t("添加步骤")}
          </Button>
          <Button
            variant="primary"
            disabled={steps.some((s) => !s.name.trim())}
            onClick={() =>
              void guarded(async () => {
                const result = await api<{ text: string }>(
                  "/sfc/requirement",
                  "POST",
                  { steps },
                );
                setText((old) => `${old}\n${result.text}`.trim());
                setModal("");
                setPanel("agent");
              })
            }
          >
            {t("应用到需求")}
          </Button>
        </div>
      </Modal>
    </div>
  );
}

function AcceptedMessage({
  kind,
  text,
}: {
  kind: string;
  text: string;
  t: (key: string) => string;
}) {
  let structured: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      structured = parsed;
  } catch {
    /* Accepted prose is rendered verbatim. */
  }
  if (kind === "analysis" && structured)
    return (
      <div className="accepted-content">
        <p>{String(structured.summary || "")}</p>
      </div>
    );
  return <pre className="accepted-content">{text}</pre>;
}

function DiffView({ value, t }: { value: Json; t: (key: string) => string }) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return <DataView value={value} />;
  const diff = value as Record<string, Json>;
  const changes = Array.isArray(diff.changes)
    ? (diff.changes as Record<string, Json>[])
    : [];
  const comments = Array.isArray(diff.device_comment_changes)
    ? (diff.device_comment_changes as Record<string, Json>[])
    : [];
  return (
    <div className="candidate-diff">
      <h3>{t("差异数据")}</h3>
      {diff.kind === "fbd" ? <>
        <p>{t("对象")} {String(diff.before_object_count)} → {String(diff.after_object_count)} · {t("导线")} {String(diff.before_wire_count)} → {String(diff.after_wire_count)}</p>
        <p>{t(diff.declarations_changed ? "声明表有变化" : "声明表无变化")}</p>
        {diff.has_changes === false ? <p>{t("程序内容无变化")}</p> : <details><summary>{t("查看详细对象差异")}</summary><pre>{String(diff.unified_diff || "")}</pre></details>}
      </> : <>
      {diff.has_changes === false ? (
        <p>{t("程序内容无变化")}</p>
      ) : typeof diff.unified_diff === "string" ? (
        <pre>{diff.unified_diff}</pre>
      ) : (
        <>
          <p>
            {t("网络")} {String(diff.before_network_count ?? 0)} →{" "}
            {String(diff.after_network_count ?? 0)}
          </p>
          {changes.map((change, i) => (
            <details key={i} open={changes.length === 1}>
              <summary>
                <span className="mono">
                  {String(change.marker)} {String(change.network)}
                </span>{" "}
                {String(change.comment || "")}
              </summary>
              <div className="diff-columns">
                <div>
                  <strong>{t("变更前")}</strong>
                  <DataView value={change.before} />
                </div>
                <div>
                  <strong>{t("变更后")}</strong>
                  <DataView value={change.after} />
                </div>
              </div>
            </details>
          ))}
          {comments.length > 0 && (
            <details open>
              <summary>{t("软元件注释变化")}</summary>
              <table>
                <thead>
                  <tr>
                    <th>{t("地址")}</th>
                    <th>{t("变更前")}</th>
                    <th>{t("变更后")}</th>
                  </tr>
                </thead>
                <tbody>
                  {comments.map((change, i) => (
                    <tr key={i}>
                      <td className="mono">{String(change.address)}</td>
                      <td>{String(change.before ?? "—")}</td>
                      <td>{String(change.after ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {diff.network_order_changed === true && (
            <DataView
              value={{
                before: diff.before_network_order,
                after: diff.after_network_order,
              }}
            />
          )}
          {!!diff.property_changes && (
            <DataView value={diff.property_changes} />
          )}
        </>
      )}
      </>}
    </div>
  );
}

function DataView({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="muted">—</span>;
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return <span className="data-value">{String(value)}</span>;
  if (Array.isArray(value))
    return (
      <div className="data-list">
        {value.length ? (
          value.map((v, i) => (
            <div key={i}>
              <DataView value={v} />
            </div>
          ))
        ) : (
          <span className="muted">—</span>
        )}
      </div>
    );
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 20)
    return <pre className="data-json">{stringify(value)}</pre>;
  return (
    <dl className="data-fields">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{fieldLabel(key)}</dt>
          <dd>
            <DataView value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function fieldLabel(key: string) {
  const labels: Record<string, [string, string, string]> = {
    summary: ["摘要", "Summary", "概要"],
    status: ["状态", "Status", "状態"],
    report_type: ["检查类型", "Review type", "検査種類"],
    report_id: ["报告编号", "Report ID", "レポートID"],
    base_version_id: ["基础版本", "Base version", "基準バージョン"],
    version_id: ["版本", "Version", "バージョン"],
    findings: ["检查发现", "Findings", "検出事項"],
    messages: ["校验说明", "Validation notes", "検証結果"],
    created_at: ["创建时间", "Created", "作成日時"],
    updated_at: ["更新时间", "Updated", "更新日時"],
    schema_version: ["格式版本", "Format version", "形式バージョン"],
    added: ["新增网络", "Added networks", "追加ネットワーク"],
    deleted: ["删除网络", "Removed networks", "削除ネットワーク"],
    modified: ["修改网络", "Changed networks", "変更ネットワーク"],
    changes: ["变更内容", "Changes", "変更内容"],
    before: ["变更前", "Before", "変更前"],
    after: ["变更后", "After", "変更後"],
    before_network_count: [
      "原网络数",
      "Original networks",
      "変更前ネットワーク数",
    ],
    after_network_count: [
      "候选网络数",
      "Candidate networks",
      "候補ネットワーク数",
    ],
    device_comments_changed: [
      "软元件注释变化",
      "Device comments changed",
      "デバイスコメント変更",
    ],
    severity: ["严重程度", "Severity", "重要度"],
    description: ["说明", "Description", "説明"],
    network: ["网络", "Network", "ネットワーク"],
    instruction_count: ["指令数", "Instructions", "命令数"],
    comment: ["注释", "Comment", "コメント"],
    passed: ["验证通过", "Passed", "検証合格"],
    valid: ["校验有效", "Valid", "有効"],
  };
  const index =
    document.documentElement.lang === "en"
      ? 1
      : document.documentElement.lang === "ja"
        ? 2
        : 0;
  return labels[key]?.[index] || key.replaceAll("_", " ");
}
