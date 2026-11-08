// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

export type WorkspaceProjectRow = {
  id?: string | null;
  name?: string | null;
  plc_model?: string | null;
  target_mode?: string | null;
};

export type WorkspaceGroupRow = {
  id: string;
  path: string;
  label: string;
  active: boolean;
  projects: WorkspaceProjectRow[];
};

export type FlatProjectRow = {
  group: WorkspaceGroupRow;
  project: WorkspaceProjectRow;
  /** Stable key: a project id is unique inside its workspace, not across them. */
  key: string;
  /** True only for the project the page currently shows. */
  current: boolean;
};

/** Whether a project name contains the typed query (case-insensitive, trimmed). */
export function matchesProject(project: WorkspaceProjectRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return String(project.name || "").toLowerCase().includes(needle);
}

/**
 * Every project of every workspace as one list, for the flat view and for search.
 *
 * The result keeps the workspace on each row, because opening a row that belongs to
 * another workspace has to switch that workspace first.
 *
 * @param groups - workspaces with the projects they own, in display order.
 * @param query - project-name filter; empty keeps every project.
 * @param currentProjectId - the project the page shows, marked `current`.
 */
export function flattenProjects(
  groups: readonly WorkspaceGroupRow[],
  query = "",
  currentProjectId = "",
): FlatProjectRow[] {
  const rows: FlatProjectRow[] = [];
  for (const group of groups) {
    for (const project of group.projects) {
      if (!matchesProject(project, query)) continue;
      rows.push({
        group,
        project,
        key: `${group.id}:${project.id ?? project.name ?? ""}`,
        current: !!group.active && !!project.id && project.id === currentProjectId,
      });
    }
  }
  return rows;
}

/** Other workspaces fold in place; the active one always shows its projects. */
export function workspaceExpanded(
  group: WorkspaceGroupRow,
  expanded: Readonly<Record<string, boolean>>,
): boolean {
  return group.active ? true : !!expanded[group.id];
}

/** Workspaces listed above the active one, which owns the project tree below. */
export function otherWorkspaces(groups: readonly WorkspaceGroupRow[]): WorkspaceGroupRow[] {
  return groups.filter((group) => !group.active);
}
