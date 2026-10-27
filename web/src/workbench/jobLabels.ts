// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { Job } from "../api/client";

/** Display names for job kinds; the kinds themselves come from the job API. */
export const jobKindLabel = (kind: string) =>
  (
    ({
      analysis: "整理需求",
      generation: "生成程序",
      direct_generation: "生成程序",
      agent: "工程问答",
      review: "检查程序",
      test_plan: "设计测试",
      debug_plan: "排查问题",
      gx_read: "从 GX 读取",
      gx_inspect: "检查 GX 工程",
      execution: "执行操作",
    }) as Record<string, string>
  )[kind] || "任务";

/** Status shown for a job in lists; the selected job uses the controller's derived status. */
export function listedJobStatus(job: Job, selectedId: string, selectedStatus?: string | null) {
  if (job.id === selectedId && selectedStatus) return selectedStatus;
  const result = job.result?.status;
  if (job.status !== "completed") return job.status;
  if (job.kind === "execution" && ["failed", "interrupted", "conflict"].includes(String(result))) return "failed";
  if (job.kind === "generation" || job.kind === "direct_generation") {
    if (result === "needs_input" || result === "contract_mismatch") return result;
    if (job.result?.version_id) return "saved";
    if (job.result?.proposal_id) return "candidate_ready";
  }
  return job.status;
}
