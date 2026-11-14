// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import type { ApiSchema, Job, Json, WorkbenchConnection, Spec } from "../api/client";
import { api } from "../api/client";
import { specificationContinuation, type AnalysisRequest } from "./specificationContinuation";
import { specDocumentChanges } from "./specDocumentChanges";
import type { Setter, Translate } from "./types";
import type { ProjectWorkspace } from "./useProjectWorkspace";

type Options = {
  regenerationRequested: boolean; workspace: ProjectWorkspace; currentJob?: Job; jobId: string; jobs: Job[];
  connection: WorkbenchConnection | null; outputRetry: number; setPanel: Setter<string>;
  setError: (message: string) => void; setNotice: (message: string) => void; t: Translate
};

export function useSpecificationDraft(options: Options) {
  const { regenerationRequested, workspace, currentJob, jobId, jobs, connection, outputRetry, setPanel, setError, setNotice, t } = options;
  const { project, pid, vid } = workspace;
  const [documentVersion, setDocumentVersion] = useState(vid);
  const [specDocument, setSpecDocument] = useState<ApiSchema["SpecDocument"] | null>(null);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [specIssues, setSpecIssues] = useState<{ path: string; message: string }[]>([]);
  const [analysisOutput, setAnalysisOutput] = useState<Record<string, Json> | null>(null);
  const [specIsDirty, setDirty] = useState(false);
  const dirty = useRef(false);
  const analysisRequests = useRef(new Map<string, AnalysisRequest>());
  const consumed = useRef(new Set<string>());
  const opened = useRef(new Set<string>());
  function clearSpecDirty() { dirty.current = false; setDirty(false); }
  function changeSpec(value: Spec) {
    dirty.current = true; setDirty(true); setSpec(value); setSpecIssues([]);
  }
  useEffect(() => {
    clearSpecDirty(); setSpec(null); setSpecDocument(null); setSpecIssues([]);
    consumed.current.clear(); opened.current.clear(); analysisRequests.current.clear();
  }, [pid, connection?.path]);
  useEffect(() => { setAnalysisOutput(null); }, [jobId, pid]);
  async function refreshSpecDocument(signal?: AbortSignal) {
    if (!pid || !connection) return;
    const epoch = workspace.capture();
    const value = await api<ApiSchema["SpecDocument"]>("/projects/" + pid + "/spec-document" + (vid ? "?version_id=" + encodeURIComponent(vid) : ""), "GET", undefined, { signal });
    if (signal?.aborted || !workspace.isCurrent(epoch)) return;
    if (dirty.current) {
      setDocumentVersion(vid);
      setSpecDocument(current => current?.project_id === pid ? { ...current, version_spec: value.version_spec } : current);
      return value;
    }
    setDocumentVersion(vid); setSpecDocument(value); setSpecIssues(documentErrors(value));
    setDirty(!!value.differences?.length || (!!value.draft && !value.confirmed_spec));
    setSpec(value.draft || value.confirmed_spec || null);
    return value;
  }
  useEffect(() => {
    const controller = new AbortController();
    void refreshSpecDocument(controller.signal).catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [pid, vid, connection, project?.confirmed_spec_hash, currentJob?.status, outputRetry]);
  useEffect(() => {
    if (!connection || currentJob?.kind !== "analysis" || currentJob.status !== "completed"
      || currentJob.result?.status === "needs_input" || consumed.current.has(currentJob.id)) return;
    const controller = new AbortController();
    const id = currentJob.id;
    void api<Record<string, Json>>("/jobs/" + id + "/output", "GET", undefined, { signal: controller.signal })
      .then((output) => { if (!controller.signal.aborted && workspace.isProject(pid) && !consumed.current.has(id) && output.spec_draft) setAnalysisOutput(output); })
      .catch((error: Error) => { if (!controller.signal.aborted && workspace.isProject(pid)) setError(error.message); });
    return () => controller.abort();
  }, [currentJob?.id, currentJob?.kind, currentJob?.status, currentJob?.result?.status, pid, connection, outputRetry]);
  function openAnalysisDraft() {
    if (!analysisOutput?.spec_draft) return;
    void refreshSpecDocument().catch((error: Error) => setError(error.message)); setPanel("spec");
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
    const captured = analysisRequests.current.get(jobId);
    const baseVersion = typeof analysisOutput?.base_version_id === "string" ? analysisOutput.base_version_id : currentJob?.version_id || captured?.versionId || null;
    const request: AnalysisRequest = {
      versionId: baseVersion,
      conversationId: currentJob?.conversation_id,
      program: typeof analysisOutput?.base_program === "string" ? analysisOutput.base_program : captured?.program,
      text: typeof analysisOutput?.edit_text === "string" ? analysisOutput.edit_text : captured?.text || "",
      generationAction: captured?.generationAction || (regenerationRequested ? "regenerate" : "edit"),
    };
    const generateAfterSave = currentJob?.kind === "analysis" && currentJob.status === "completed"
      && !!analysisOutput?.spec_draft && jobId === currentJob.id;
    const regenerateAfterSave = generateAfterSave && request.generationAction === "regenerate" && !!baseVersion;
    const continuation = specificationContinuation(request);
    const updated = await saveSpecDraft(value);
    if (!updated || !workspace.isCurrent(epoch)) return;
    if (documentErrors(updated).length) return;
    const result = await api<ApiSchema["SpecDocument"]>("/projects/" + pid + "/spec-document/confirm", "POST", { expected_revision: updated.draft_revision, version_id: baseVersion || vid || null });
    if (!workspace.isCurrent(epoch)) return;
    setSpecDocument(result); setSpecIssues(documentErrors(result)); clearSpecDirty(); setSpec(result.draft || result.confirmed_spec || null);
    await workspace.reloadProjectSilently();
    if (currentJob?.kind === "analysis") consumed.current.add(jobId);
    setAnalysisOutput(null);
    setNotice(t("需求已确认"));
    return { generateAfterSave, regenerateAfterSave, continuation };
  }
  function documentErrors(value: ApiSchema["SpecDocument"]): { path: string; message: string }[] {
    return (value.issues?.errors || []).map(issue => ({ path: issue.path || "$", message: issue.message }));
  }
  async function saveSpecDraft(value: Spec) {
    if (!specDocument) throw new Error(t("请先读取工程规格。"));
    const epoch = workspace.capture();
    const changes = specDocumentChanges(specDocument.draft || {}, value);
    if (!changes.length) return specDocument;
    const result = await api<ApiSchema["SpecDocument"]>("/projects/" + pid + "/spec-document", "PATCH", {
      changes, expected_revision: specDocument.draft_revision,
    });
    if (!workspace.isCurrent(epoch)) return;
    setSpecDocument({ ...result, version_spec: specDocument.version_spec }); setSpec(result.draft || null); setSpecIssues(documentErrors(result));
    dirty.current = false; setDirty(!!result.differences?.length || !result.confirmed_spec);
    setNotice(t("规格草稿已保存"));
    return result;
  }
  function recordAnalysisRequest(id: string, request: AnalysisRequest, parentId?: string) {
    const parent = parentId ? analysisRequests.current.get(parentId) : undefined;
    analysisRequests.current.set(id, parent ?? request);
  }
  async function reloadSpecDocument() { clearSpecDirty(); return refreshSpecDocument(); }
  const boundDocument = specDocument?.project_id === pid && documentVersion === vid ? specDocument : null;
  return { spec: boundDocument ? spec : null, specDocument: boundDocument, refreshSpecDocument: reloadSpecDocument, saveSpecDraft, recordAnalysisRequest, specIssues, specIsDirty, analysisOutput, changeSpec, clearSpecDirty, openAnalysisDraft, persistSpec };
}
