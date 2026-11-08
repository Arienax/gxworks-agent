// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

// Pure helpers for the in-app workspace picker. The dialog renders; these decide
// what it shows and which directory a confirmation adopts.

export type WorkspaceEntry = { name: string; path: string; hidden?: boolean };
export type WorkspaceBreadcrumb = { name: string; path: string };
export type WorkspaceListing = {
  path: string;
  home: string;
  crumbs: WorkspaceBreadcrumb[];
  entries: WorkspaceEntry[];
  truncated?: boolean;
};

/**
 * Rows the dialog shows: hidden entries stay listed by the service, so the footer
 * switch decides visibility here. The filter matches the trailing column only.
 */
export function visibleEntries(
  listing: WorkspaceListing | undefined,
  options: { query?: string; showHidden?: boolean } = {},
): WorkspaceEntry[] {
  const entries = listing?.entries ?? [];
  const query = (options.query ?? "").trim().toLowerCase();
  return entries.filter((entry) => {
    if (entry.hidden && !options.showHidden) return false;
    if (!query) return true;
    return String(entry.name).toLowerCase().startsWith(query);
  });
}

/**
 * The directory `Open` adopts: the selected row, else the listed level itself.
 * A selection only counts while it still belongs to the listed level.
 */
export function pickTarget(
  listing: WorkspaceListing | undefined,
  selected: string | null | undefined,
): string {
  if (!listing) return "";
  const path = selected && listing.entries.some((entry) => entry.path === selected)
    ? selected
    : listing.path;
  return path ?? "";
}

/** The crumb that names the root: no separator left in its label. */
export function isRootCrumb(crumb: WorkspaceBreadcrumb): boolean {
  return crumb.name === crumb.path;
}

/**
 * Accept a typed path as a jump target. Trailing separators are dropped so
 * `C:\work\` and `C:\work` address the same level; an empty draft means home.
 */
export function jumpTarget(draft: string): string | undefined {
  const text = String(draft ?? "").trim();
  if (!text) return undefined;
  const trimmed = text.replace(/[\\/]+$/, "");
  return trimmed || text;
}

/** True when the text addresses a folder directly inside the listed level. */
export function childOf(listing: WorkspaceListing | undefined, path: string): boolean {
  return (listing?.entries ?? []).some((entry) => entry.path === path);
}
