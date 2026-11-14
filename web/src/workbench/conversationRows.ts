// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

export type ConversationLike = {
  id: string;
  kind: string;
  created_at?: string | null;
  project_id?: string | null;
  conversation_id?: string | null;
};

/** Jobs arrive newest first. The latest job represents each conversation. */
export function conversationRows<T extends ConversationLike>(jobs: readonly T[]): T[] {
  const rows = new Map<string, T>();
  for (const job of jobs) {
    const id = job.conversation_id;
    if (!id || rows.has(id)) continue;
    rows.set(id, job);
  }
  return [...rows.values()];
}

type SidebarProject = { id: string; name: string; created_at?: string | null };

/** Show saved projects until a conversation represents them in the sidebar. */
export function workspaceRows<T extends ConversationLike>(jobs: readonly T[], projects: readonly SidebarProject[]) {
  const projectIds = new Set(projects.map((project) => project.id));
  const conversations = conversationRows(jobs.filter((job) => job.project_id && projectIds.has(job.project_id)));
  const represented = new Set(conversations.map((job) => job.project_id));
  const projectsWithoutConversations = projects
    .filter((project) => !represented.has(project.id))
    .map(({ id, name, created_at }) => ({ id, name, created_at, project_id: id }));
  return [...projectsWithoutConversations, ...conversations]
    .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
}

/** Label each conversation with its project's saved name. */
export function conversationTitle(
  job: ConversationLike,
  projectNames: ReadonlyMap<string, string>,
  labelled: (kind: string) => string,
): string {
  return projectNames.get(job.project_id ?? "") || labelled(job.kind);
}

/** Keep the conversations whose title contains the query, ignoring case. */
export function filterConversations<T>(
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
export function orderByProject<T extends Pick<ConversationLike, "project_id" | "created_at">>(jobs: readonly T[]): T[] {
  const groups = new Map<string, T[]>();
  for (const job of jobs) {
    const key = String(job.project_id || "");
    groups.set(key, [...(groups.get(key) ?? []), job]);
  }
  return [...groups.values()].flatMap((rows) =>
    [...rows].sort((a, b) => (String(a.created_at || "") < String(b.created_at || "") ? 1 : -1)));
}
