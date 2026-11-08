// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export type WorkspaceState = {
  selected: boolean;
  path: string | null;
  name: string | null;
  home: string | null;
};

type PickResult = WorkspaceState & { cancelled?: boolean };

/**
 * The engineering workspace the service currently serves.
 *
 * The page opens with whatever the service was started with and switches from the
 * sidebar through the operating system's own folder chooser. Binding another
 * workspace rebuilds the service, so a confirmed pick reloads the page instead of
 * keeping stale project ids.
 */
export function useWorkspaceBinding(session: unknown) {
  const [state, setState] = useState<WorkspaceState | null>(null);
  const [error, setError] = useState("");
  const [picking, setPicking] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const value = await api<WorkspaceState>("/workspace", "GET");
      setState(value);
      return value;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      return null;
    }
  }, []);

  useEffect(() => {
    if (session) void refresh();
  }, [session, refresh]);

  const pick = useCallback(async () => {
    setPicking(true);
    setError("");
    try {
      const value = await api<PickResult>("/workspace/pick", "POST", {});
      if (value?.cancelled) {
        setState(value);
        return;
      }
      window.location.reload();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setPicking(false);
    }
  }, []);

  return {
    state,
    error,
    picking,
    loading: !!session && state === null && !error,
    needsChoice: !!session && state !== null && !state.selected,
    refresh,
    pick,
  };
}
