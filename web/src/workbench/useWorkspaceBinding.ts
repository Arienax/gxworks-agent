// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkbenchConnection } from "../api/client";
import { api } from "../api/client";
import { LatestRead } from "../lifecycle/requests";

export type WorkspaceProjectRef = {
  id?: string | null;
  name?: string | null;
  plc_model?: string | null;
  target_mode?: string | null;
};

export type WorkspaceSummary = {
  id: string;
  path: string;
  label: string;
  active: boolean;
  projects: WorkspaceProjectRef[];
};

export type WorkspaceIndex = {
  active: string | null;
  workspaces: WorkspaceSummary[];
  cancelled?: boolean;
};

/**
 * The workspaces this workbench serves and the projects inside each one.
 *
 * A workspace is one row in a list, not the process binding: the sidebar shows
 * every registered folder with its projects, and only the active workspace has a
 * running service. Apply a new binding in place after it is ready.
 */
export function useWorkspaceBinding(connection: WorkbenchConnection | null, onBindingChanged: (projectId?: string) => Promise<void>) {
  const [index, setIndex] = useState<WorkspaceIndex | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const changing = useRef(false);
  const indexRead = useRef(new LatestRead());
  const connected = !!connection;

  const refresh = useCallback(async () => {
    if (changing.current) return null;
    const read = indexRead.current.begin();
    try {
      const value = await api<WorkspaceIndex>("/workspaces", "GET", undefined, { signal: read.signal });
      if (read.current()) setIndex(value);
      return value;
    } catch (failure) {
      if (read.current()) setError(failure instanceof Error ? failure.message : String(failure));
      return null;
    }
  }, []);

  useEffect(() => {
    if (connected) void refresh();
    return () => indexRead.current.cancel();
  }, [connected, refresh]);

  async function changeBinding(command: () => Promise<WorkspaceIndex>, projectId?: string) {
    if (changing.current) return;
    changing.current = true;
    indexRead.current.cancel();
    setBusy(true);
    setError("");
    try {
      const value = await command();
      if (value.cancelled) return;
      const path = value.workspaces.find(item => item.id === value.active)?.path || null;
      try {
        if (path !== (connection?.path || null) || projectId) await onBindingChanged(projectId);
      } finally {
        setIndex(value);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      changing.current = false;
      setBusy(false);
    }
  }

  return {
    index,
    error,
    busy,
    loading: !!connection && index === null && !error,
    workspaces: index?.workspaces ?? [],
    activeId: index?.active ?? null,
    refresh,
    addViaPicker: () => changeBinding(() => api<WorkspaceIndex>("/workspaces/pick", "POST", {})),
    activate: (identifier: string, projectId?: string) => changeBinding(
      () => api<WorkspaceIndex>(`/workspaces/${encodeURIComponent(identifier)}/activate`, "POST", {}), projectId),
    forget: (identifier: string) => changeBinding(
      () => api<WorkspaceIndex>(`/workspaces/${encodeURIComponent(identifier)}`, "DELETE")),
  };
}
