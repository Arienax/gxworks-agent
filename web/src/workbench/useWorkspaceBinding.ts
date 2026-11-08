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

/**
 * The engineering workspace the service currently serves.
 *
 * The service can start without one, so the workbench asks before it shows any
 * project: `selected` false means the picker owns the screen. Binding another
 * workspace rebuilds the service, so callers reload rather than keep stale ids.
 */
export function useWorkspaceBinding(session: unknown) {
  const [state, setState] = useState<WorkspaceState | null>(null);
  const [error, setError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const value = await api<WorkspaceState>("/workspace", "GET");
      setState(value);
      setError("");
      return value;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      return null;
    }
  }, []);

  useEffect(() => {
    if (session) void refresh();
  }, [session, refresh]);

  return {
    state,
    error,
    /** True until the first read completes: the shell must not flash empty. */
    loading: !!session && state === null && !error,
    needsChoice: !!session && state !== null && !state.selected,
    pickerOpen,
    openPicker: () => setPickerOpen(true),
    closePicker: () => setPickerOpen(false),
    refresh,
  };
}
