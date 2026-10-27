// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { Version } from "../api/client";
import { readGXWSource } from "../api/gxw";
import type { GXWSourceView } from "../api/gxw";

export function useGXWSource(pid: string, vid: string, version: Version | undefined,
  enabled: boolean, setError: (message: string) => void) {
  const key = `${pid}:${vid}`;
  const available = !!version?.artifacts?.some((artifact) => artifact.id === "gxw" && artifact.available);
  const [selection, setSelection] = useState({ key: "", program: "" });
  const [result, setResult] = useState<{ key: string; view: GXWSourceView } | null>(null);
  const [loading, setLoading] = useState(false);
  const program = selection.key === key ? selection.program : version?.program_name || "";
  useEffect(() => {
    setResult((previous) => previous?.key === key ? previous : null);
    if (!enabled || !available || !pid || !vid) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    void readGXWSource(pid, vid, program || undefined, controller.signal)
      .then((view) => { if (!controller.signal.aborted) setResult({ key, view }); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [pid, vid, key, program, enabled, available, setError]);
  return { available, loading, view: result?.key === key ? result.view : null,
    selected: program, select: (name: string) => setSelection({ key, program: name }) };
}
