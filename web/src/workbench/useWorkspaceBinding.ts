// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

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
 * running service. Activating another one rebuilds it, so the page reloads.
 */
export function useWorkspaceBinding(session: unknown) {
  const [index, setIndex] = useState<WorkspaceIndex | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const value = await api<WorkspaceIndex>("/workspaces", "GET");
      setIndex(value);
      return value;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      return null;
    }
  }, []);

  useEffect(() => {
    if (session) void refresh();
  }, [session, refresh]);

  const addViaPicker = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const value = await api<WorkspaceIndex>("/workspaces/pick", "POST", {});
      if (value?.cancelled) return;
      window.location.reload();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }, []);

  const activate = useCallback(async (identifier: string) => {
    setBusy(true);
    setError("");
    try {
      await api<WorkspaceIndex>(`/workspaces/${encodeURIComponent(identifier)}/activate`, "POST", {});
      window.location.reload();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }, []);

  const forget = useCallback(async (identifier: string) => {
    setBusy(true);
    setError("");
    try {
      const value = await api<WorkspaceIndex>(`/workspaces/${encodeURIComponent(identifier)}`, "DELETE");
      setIndex(value);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }, []);

  return {
    index,
    error,
    busy,
    loading: !!session && index === null && !error,
    workspaces: index?.workspaces ?? [],
    activeId: index?.active ?? null,
    refresh,
    addViaPicker,
    activate,
    forget,
  };
}
