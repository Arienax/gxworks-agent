// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import type { Job, Json, Session, Spec } from "../api/client";
import { api } from "../api/client";
import type { Setter, Translate } from "./types";
import type { ProjectWorkspace } from "./useProjectWorkspace";

type Options = {
  workspace: ProjectWorkspace; currentJob?: Job; jobId: string; jobs: Job[];
  session: Session | null; outputRetry: number; setPanel: Setter<string>;
  setError: (message: string) => void; setNotice: (message: string) => void; t: Translate
};

export function useSpecificationDraft(options: Options) {
  const { workspace, currentJob, jobId, jobs, session, outputRetry, setPanel, setError, setNotice, t } = options;
  const { project, pid, vid } = workspace;
  const [spec, setSpec] = useState<Spec | null>(null);
  const [specIssues, setSpecIssues] = useState<{ path: string; message: string }[]>([]);
  const [analysisOutput, setAnalysisOutput] = useState<Record<string, Json> | null>(null);
  const [specIsDirty, setDirty] = useState(false);
  const dirty = useRef(false);
  const binding = useRef("");
  const consumed = useRef(new Set<string>());
  const opened = useRef(new Set<string>());
  function clearSpecDirty() { dirty.current = false; setDirty(false); }
  function changeSpec(value: Spec) {
    dirty.current = true; setDirty(true); setSpec(value); setSpecIssues([]);
  }
  useEffect(() => {
    clearSpecDirty(); setSpec(null); setSpecIssues([]); binding.current = "";
    consumed.current.clear(); opened.current.clear();
  }, [pid]);
  useEffect(() => { setAnalysisOutput(null); }, [jobId, pid]);
  useEffect(() => {
    if (!project || project.id !== pid || dirty.current) return;
    const next = pid + ":" + (project.confirmed_spec_hash || "");
    if (binding.current === next) return;
    binding.current = next;
    setSpec((project.confirmed_spec as Spec) || null);
  }, [project, pid]);
  useEffect(() => {
    if (!session || currentJob?.kind !== "analysis" || currentJob.status !== "completed" || consumed.current.has(currentJob.id)) return;
    const controller = new AbortController();
    const id = currentJob.id;
    void api<Record<string, Json>>("/jobs/" + id + "/output", "GET", undefined, { signal: controller.signal })
      .then((output) => { if (!controller.signal.aborted && workspace.isProject(pid) && !consumed.current.has(id) && output.spec_draft) setAnalysisOutput(output); })
      .catch((error: Error) => { if (!controller.signal.aborted && workspace.isProject(pid)) setError(error.message); });
    return () => controller.abort();
  }, [currentJob?.id, currentJob?.kind, currentJob?.status, pid, session, outputRetry]);
  function openAnalysisDraft() {
    if (!analysisOutput?.spec_draft) return;
    setSpec(analysisOutput.spec_draft as Spec); clearSpecDirty(); setSpecIssues([]); setPanel("spec");
  }
  useEffect(() => {
    if (!analysisOutput?.spec_draft || !project || project.id !== pid || jobId !== jobs[0]?.id
      || opened.current.has(jobId) || dirty.current
      || (analysisOutput.spec_base_hash ?? null) !== (project.confirmed_spec_hash ?? null)
      || (analysisOutput.base_version_id ?? null) !== (vid || null)) return;
    opened.current.add(jobId); openAnalysisDraft();
  }, [analysisOutput, project, pid, vid, jobId, jobs]);
  async function persistSpec(value: Spec) {
    const epoch = workspace.capture();
    const generateAfterSave = currentJob?.kind === "analysis" && currentJob.status === "completed"
      && !!analysisOutput?.spec_draft && jobId === currentJob.id;
    const regenerateAfterSave = generateAfterSave && workspace.hasSavedVersions && project?.target_mode === "ladder";
    const result = await api<{
      valid: boolean; spec?: Spec; hash?: string;
      issues?: { errors?: { path: string; message: string }[]; warnings?: { message: string }[] }
    }>(
      "/projects/" + pid + "/spec", "PUT", { spec: value, expected_hash: project?.confirmed_spec_hash ?? null });
    if (!workspace.isCurrent(epoch)) return;
    if (!result.valid) { setSpecIssues(result.issues?.errors || []); return; }
    if (!result.spec || !result.hash) throw new Error(t("确认规格响应不完整，请刷新后重试。"));
    workspace.cancelRead();
    const savedSpec = result.spec, savedHash = result.hash;
    setSpecIssues([]); clearSpecDirty(); setSpec(savedSpec); binding.current = pid + ":" + savedHash;
    workspace.setProject((current) => current?.id === pid ? { ...current, confirmed_spec: savedSpec, confirmed_spec_hash: savedHash } : current);
    if (currentJob?.kind === "analysis") consumed.current.add(jobId);
    setAnalysisOutput(null);
    setNotice([t("需求已确认"), ...(result.issues?.warnings || []).map((issue) => issue.message)].join("；"));
    return { generateAfterSave, regenerateAfterSave };
  }
  return { spec, specIssues, specIsDirty, analysisOutput, changeSpec, clearSpecDirty, openAnalysisDraft, persistSpec };
}
