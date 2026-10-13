import type { Job, Proposal } from "../api/client";

export type GXSendSelection = {
  projectId: string; projectName: string; versionId: string;
  activeVersionId: string; epoch: number; requestId: string;
};
export type GXSendRequest = <T>(path: string, method?: string, body?: unknown) => Promise<T>;

/** A confirmed send uses the existing proposal/decision protocol exactly once. */
export async function submitGXSend(
  selection: GXSendSelection, isCurrent: () => boolean, request: GXSendRequest,
): Promise<{ proposal: Proposal; job?: Job }> {
  if (!isCurrent()) throw new Error("项目或版本已变化，请重新点击发送到 GX。");
  const proposal = await request<Proposal>("/proposals", "POST", {
    action: "gx_import", project_id: selection.projectId, version_id: selection.versionId,
    request_id: selection.requestId, manual_backup_acknowledged: true,
  });
  // In full mode the policy may already have queued the operation. Never approve twice.
  if (!isCurrent()) return { proposal };
  if (proposal.execution_job_id) {
    return { proposal, job: await request<Job>(`/jobs/${proposal.execution_job_id}`) };
  }
  if (proposal.status === "pending") {
    const result = await request<{ job?: Job }>(`/proposals/${proposal.id}/decision`, "POST", { decision: "accept" });
    return { proposal, job: result.job };
  }
  // Replays of an executing or finished proposal must not dispatch another operation.
  return { proposal };
}
