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
export interface Spec {
  summary?: string;
  io_table?: Record<string, Json>[];
  parameters?: Record<string, Json>[];
  approaches?: Record<string, Json>[];
  selected_approach?: Record<string, Json>;
  user_notes?: string;
  [key: string]: Json | undefined;
}
export type Project = components["schemas"]["Project"];
export type Job = components["schemas"]["Job"];
export type JobEvent = components["schemas"]["JobEvent"];
export type Proposal = components["schemas"]["Proposal"];
export type ModelSettings = components["schemas"]["ModelSettings"];
export type Session = components["schemas"]["Session"];
export type JobKind = components["schemas"]["JobCreate"]["kind"];

let csrf = "";
export function setSession(value: Session) {
  csrf = value.csrf || "";
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
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
