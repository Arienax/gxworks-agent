// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { ProgramDocument, Project, WorkbenchConnection } from "../api/client";
import { api, artifactUrl } from "../api/client";

export function useProgramDocument(
  pid: string,
  vid: string,
  version: NonNullable<Project["versions"]>[number] | undefined,
  connection: WorkbenchConnection | null,
  setError: (message: string) => void,
) {
  const [program, setProgram] = useState<{ scope: string; value: ProgramDocument | null } | null>(null);
  const [st, setSt] = useState<{ scope: string; value: string } | null>(null);
  const scope = connection?.selected && pid && vid ? JSON.stringify([connection.path, pid, vid]) : "";
  const stArtifact = version?.artifacts?.find(
    (a) => a.id === "st" && a.available,
  )?.id;
  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    void api<ProgramDocument | null>(`/projects/${pid}/versions/${vid}/program`, "GET", undefined, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setProgram({ scope, value });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [scope, pid, vid, setError]);
  useEffect(() => {
    if (!scope || !stArtifact) return;
    const controller = new AbortController();
    void fetch(artifactUrl(pid, vid, stArtifact), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`ST artifact (${response.status})`);
        return response.text();
      })
      .then((value) => {
        if (!controller.signal.aborted) setSt({ scope, value });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [scope, pid, vid, stArtifact, setError]);

  return { program: program?.scope === scope ? program.value : null,
    st: stArtifact && st?.scope === scope ? st.value : "" };
}
