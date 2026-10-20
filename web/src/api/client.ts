export type Json =
  | null
  | boolean
  | number
  | string
  | Json[]
  | { [key: string]: Json };
import type { components } from "./generated";
export type Artifact = components["schemas"]["Artifact"];
export type Version = components["schemas"]["Version"];
export type Spec = components["schemas"]["Specification"];
export type Project = components["schemas"]["Project"];
export type Job = components["schemas"]["Job"];
export type JobEvent = components["schemas"]["JobEvent"];
export type Proposal = components["schemas"]["Proposal"];
export type ModelSettings = components["schemas"]["ModelSettings"];
export type Session = components["schemas"]["Session"];
export type JobKind = components["schemas"]["JobCreate"]["kind"];
export type FBDModel = components["schemas"]["FBDModel"];
export type SFCModel = components["schemas"]["SFCModel"];
export type ProgramDocument = components["schemas"]["PLCProgram"] | FBDModel | SFCModel | components["schemas"]["LadderDocument"];
export type FBDEditorView = components["schemas"]["FBDEditorView"];
export type ProposalPreview = components["schemas"]["LadderPreview"] | components["schemas"]["STPreview"] | components["schemas"]["FBDPreviewResource"] | components["schemas"]["SFCPreviewResource"] | components["schemas"]["ExecutionPreview"];
export type GenerationPreview = components["schemas"]["GenerationLadderPreview"] | components["schemas"]["GenerationFBDPreview"] | components["schemas"]["GenerationSFCPreview"] | components["schemas"]["GenerationSTPreview"];
export type ApiSchema = components["schemas"];
type UnionKeys<T> = T extends unknown ? keyof T : never;
type CompleteUnion<T, K extends PropertyKey = UnionKeys<T>> = T extends unknown ? T & Partial<Record<Exclude<K, keyof T>, never>> : never;
export type PreviewDocument = CompleteUnion<ProposalPreview | GenerationPreview>;

export function isFBDModel(value: unknown): value is FBDModel {
  return !!value && typeof value === "object" && "nodes" in value && Array.isArray(value.nodes)
    && "wires" in value && Array.isArray(value.wires) && "program" in value && typeof value.program === "string";
}
export function isSFCModel(value: unknown): value is SFCModel {
  return !!value && typeof value === "object" && "graph" in value && !!value.graph
    && "children" in value && Array.isArray(value.children) && "program" in value && typeof value.program === "string";
}
export function isFBDEditorView(value: unknown): value is FBDEditorView {
  return !!value && typeof value === "object" && "model" in value && isFBDModel(value.model)
    && "presentation" in value && !!value.presentation && "catalog" in value && Array.isArray(value.catalog);
}

let csrf = "";
export function setSession(value: Session) {
  csrf = value.csrf || "";
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  options: { signal?: AbortSignal; headers?: Record<string, string> } = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    signal: options.signal,
    credentials: "same-origin",
    headers: {
      ...options.headers,
      "Content-Type": "application/json",
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error?.message || `请求失败 (${response.status})`);
  return data as T;
}
export const activeJob = (job: Job) =>
  ["queued", "running", "cancelling"].includes(job.status);
export const artifactUrl = (
  pid: string,
  vid: string,
  aid: string,
  download = false,
) =>
  `/api/projects/${encodeURIComponent(pid)}/versions/${encodeURIComponent(vid)}/artifacts/${encodeURIComponent(aid)}${download ? "?download=true" : ""}`;
export const freshGxCsvUrl = (pid: string, vid: string) =>
  `/api/projects/${encodeURIComponent(pid)}/versions/${encodeURIComponent(vid)}/exports/gxworks2-csv`;
export const jobDiagnosticsUrl = (jobId: string) =>
  `/api/jobs/${encodeURIComponent(jobId)}/diagnostics`;
export const key = () => crypto.randomUUID();
