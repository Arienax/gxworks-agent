// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import type { Job } from "../api/client";

/** Each saved job contributes its metered requests once, even if validation failed. */
export function conversationTokenUsage(
  jobs: readonly Pick<Job, "project_id" | "conversation_id" | "model_usage">[],
  projectId: string,
  conversationId: string,
) {
  if (!projectId || !conversationId) return null;
  let input = 0;
  let output = 0;
  let cached: number | undefined = 0;
  for (const job of jobs) {
    if (job.project_id !== projectId || job.conversation_id !== conversationId) continue;
    const tokens = job.model_usage?.tokens;
    if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) continue;
    const { input_tokens, output_tokens, cached_input_tokens } = tokens as Record<string, unknown>;
    if (typeof input_tokens !== "number" || typeof output_tokens !== "number") continue;
    input += input_tokens;
    output += output_tokens;
    if (cached !== undefined && input_tokens > 0) {
      cached = typeof cached_input_tokens === "number" ? cached + cached_input_tokens : undefined;
    }
  }
  return input || output ? { input, output, cached } : null;
}
