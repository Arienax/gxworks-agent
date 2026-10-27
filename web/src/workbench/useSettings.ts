// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import type { Json, ModelSettings, Session } from "../api/client";
import { api } from "../api/client";
import type { ApprovalSettings } from "../features/ApprovalSettings";
import { LatestRead, sameValue } from "../lifecycle/requests";
import { usePolling } from "../lifecycle/usePolling";

export function useSettings(
  session: Session | null,
  modal: string,
  setError: (value: string) => void,
) {
  const [approvalSettings, setApprovalSettings] = useState<ApprovalSettings | null>(null);
  const [settingsTab, setSettingsTab] = useState<"general" | "model" | "developer">("general");
  const [settingsError, setSettingsError] = useState("");
  const [settingsRetry, setSettingsRetry] = useState(0);
  const [settings, setSettings] = useState<ModelSettings | null>(null),
    [environment, setEnvironment] = useState<Record<string, Json>>({});

  const settingsRead = useRef(new LatestRead());
  const settingsOpen = modal === "settings",
    environmentOpen = modal === "environment";
  useEffect(() => {
    if (!session || (!settingsOpen && settings)) return;
    const read = settingsRead.current.begin();
    void api<ModelSettings>("/settings", "GET", undefined, { signal: read.signal })
      .then((value) => {
        if (read.current()) setSettings((old) => sameValue(old, value));
      })
      .catch((e) => {
        if (read.current()) setError(e.message);
      });
    return () => read.cancel();
  }, [session, settingsOpen]);
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    void api<Record<string, Json>>("/environment", "GET", undefined, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setEnvironment((old) => sameValue(old, value));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [session, environmentOpen]);
  usePolling(
    session ? `approval:${settingsRetry}` : null,
    (signal) => api<ApprovalSettings>("/settings/approval", "GET", undefined, { signal }),
    (value) => {
      setApprovalSettings((old) =>
        old && old.revision > value.revision ? old : sameValue(old, value),
      );
      setSettingsError("");
    },
    (e) => setSettingsError(String(e instanceof Error ? e.message : e)),
    5000,
  );

  function acceptSettings(value: ModelSettings) {
    settingsRead.current.cancel();
    setSettings(value);
  }
  return {
    approvalSettings,
    setApprovalSettings,
    settingsTab,
    setSettingsTab,
    settingsError,
    setSettingsRetry,
    settings,
    setSettings: acceptSettings,
    environment,
  };
}
