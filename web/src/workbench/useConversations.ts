// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useState } from "react";
import type { Job, Session } from "../api/client";
import { api } from "../api/client";

/**
 * Every conversation in the served workspace, newest first.
 *
 * A conversation is one job: the workbench runs one per request, so the sidebar
 * lists them like a harness lists sessions. The list is served unfiltered by
 * project, because the operator picks a conversation, not a project.
 *
 * @param session - open operator session; polling stops without one.
 * @param workspaceKey - identity of the served workspace; a switch reloads the list.
 */
export function useConversations(session: Session | null, workspaceKey: string | null) {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    if (!session) {
      setJobs([]);
      return;
    }
    let stopped = false;
    const load = async () => {
      try {
        const value = await api<{ jobs: Job[] }>("/jobs", "GET");
        if (stopped) return;
        setJobs([...value.jobs].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)));
      } catch {
        // Keep the previous list; the poll retries on the next tick.
      }
    };
    void load();
    const timer = setInterval(load, 4000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [session, workspaceKey]);

  return jobs;
}
