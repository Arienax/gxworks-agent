// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { ProgramDocument, Project, Session } from "../api/client";
import { api, artifactUrl } from "../api/client";

export function useProgramDocument(
  pid: string,
  vid: string,
  version: NonNullable<Project["versions"]>[number] | undefined,
  session: Session | null,
  setError: (message: string) => void,
) {
  const [program, setProgram] = useState<ProgramDocument | null>(null);
  const [st, setSt] = useState("");
  const stArtifact = version?.artifacts?.find(
    (a) => ["st_from_ir", "st"].includes(a.id) && a.available,
  )?.id;
  useEffect(() => {
    setProgram(null);
    setSt("");
    if (!pid || !vid || !session) return;
    const controller = new AbortController();
    void api<ProgramDocument | null>(`/projects/${pid}/versions/${vid}/program`, "GET", undefined, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setProgram(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    if (stArtifact)
      void fetch(artifactUrl(pid, vid, stArtifact), { signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error(`ST artifact (${response.status})`);
          return response.text();
        })
        .then((value) => {
          if (!controller.signal.aborted) setSt(value);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        });
    return () => controller.abort();
  }, [pid, vid, stArtifact, session]);

  return { program, st };
}
