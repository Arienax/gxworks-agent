// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

export type ConversationLike = {
  id: string;
  kind: string;
  title?: string | null;
  created_at?: string | null;
  project_id?: string | null;
};

/**
 * The row label for one conversation.
 *
 * The operator's own sentence is the title; a job that carries no text (an
 * inspection or an execution) falls back to the action it performed.
 *
 * @param job - job projection carrying the request text.
 * @param labelled - localizes one job kind, used only as the fallback.
 */
export function conversationTitle(
  job: ConversationLike,
  labelled: (kind: string) => string,
): string {
  const title = (job.title || "").trim();
  return title || labelled(job.kind);
}

/** Keep the conversations whose title contains the query, ignoring case. */
export function filterConversations<T extends ConversationLike>(
  jobs: readonly T[],
  query: string,
  titleOf: (job: T) => string,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...jobs];
  return jobs.filter((job) => titleOf(job).toLowerCase().includes(needle));
}

/**
 * Group key for ordering by project: conversations stay together per project,
 * newest first inside each group, and projects keep their first-seen order.
 */
export function orderByProject<T extends ConversationLike>(jobs: readonly T[]): T[] {
  const groups = new Map<string, T[]>();
  for (const job of jobs) {
    const key = String(job.project_id || "");
    groups.set(key, [...(groups.get(key) ?? []), job]);
  }
  return [...groups.values()].flatMap((rows) =>
    [...rows].sort((a, b) => (String(a.created_at || "") < String(b.created_at || "") ? 1 : -1)));
}
