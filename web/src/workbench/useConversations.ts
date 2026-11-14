// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useState } from "react";
import type { Job, WorkbenchConnection } from "../api/client";
import { api } from "../api/client";
import { usePolling } from "../lifecycle/usePolling";
import { reconcileByRevision } from "../lifecycle/requests";

/**
 * Jobs across the served workspace, newest first. The sidebar groups these
 * by conversation while the selected project retains the full task history.
 *
 * @param connection - local workbench connection; polling stops before it is available.
 * @param workspaceKey - identity of the served workspace; a switch reloads the list.
 */
export function useConversations(connection: WorkbenchConnection | null, paused: boolean) {
  const scope = connection?.selected ? connection.path || null : null;
  const [snapshot, setSnapshot] = useState<{ scope: string | null; jobs: Job[] }>({ scope: null, jobs: [] });
  usePolling(paused ? null : scope,
    signal => api<{ jobs: Job[] }>("/jobs", "GET", undefined, { signal }),
    value => setSnapshot(current => {
      const jobs = reconcileByRevision(current.scope === scope ? current.jobs : [], value.jobs);
      return current.scope === scope && jobs === current.jobs ? current : { scope, jobs };
    }),
    () => {}, 4000);
  return snapshot.scope === scope ? snapshot.jobs : [];
}
