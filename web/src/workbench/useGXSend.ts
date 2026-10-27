// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";
import { activeJob, api, key } from "../api/client";
import type { GXSendSelection } from "../features/gxSend";
import { submitGXSend } from "../features/gxSend";
import type { Setter } from "./types";
import type { useJobFeed } from "./useJobFeed";
import type { ProjectWorkspace } from "./useProjectWorkspace";
type Options = {
  workspace: ProjectWorkspace; feed: ReturnType<typeof useJobFeed>; canWrite: boolean;
  preview: unknown; operations: Record<string, boolean>; clearPreview: () => void; setPanel: Setter<string>;
  guarded: (action: () => Promise<void>) => Promise<void>;
  proposeExecution: (action: string) => Promise<void>
};
export function useGXSend(options: Options) {
  const { workspace, feed, canWrite, preview, operations, clearPreview, setPanel, guarded, proposeExecution } = options;
  const { pid, vid, project, version, reloadProjectSilently } = workspace;
  const { jobs, setProposals, setJobId, setJobs, jobsPolling } = feed;
  const [gxSend, setGXSend] = useState<GXSendSelection | null>(null);
  const gxSendSelection = useRef({ pid, vid, activeVersionId: "", epoch: 0 });
  gxSendSelection.current = { pid, vid, activeVersionId: project?.active_version_id || "", epoch: workspace.capture() };
  useEffect(() => { setGXSend(null); }, [pid, vid, project?.active_version_id]);
  function requestGXSend() {
    if (
      !canWrite ||
      preview ||
      jobs.some(activeJob) ||
      !operations.gx_import ||
      !project ||
      project.id !== pid ||
      !version
    )
      return;
    if (version.target_mode === "fbd") {
      void guarded(() => proposeExecution("gx_import"));
      return;
    }
    setGXSend({
      projectId: pid,
      projectName: project.name,
      versionId: vid,
      activeVersionId: project.active_version_id || "",
      epoch: workspace.capture(),
      requestId: key(),
    });
  }

  async function confirmGXSend() {
    const selection = gxSend;
    if (!selection) return;
    setGXSend(null);
    const isCurrent = () => {
      const current = gxSendSelection.current;
      return (
        current.pid === selection.projectId &&
        current.vid === selection.versionId &&
        current.activeVersionId === selection.activeVersionId &&
        current.epoch === selection.epoch
      );
    };
    const result = await submitGXSend(selection, isCurrent, api);
    if (!isCurrent()) return;
    jobsPolling.current?.refresh();
    setProposals((old) => [result.proposal, ...old.filter((p) => p.id !== result.proposal.id)]);
    clearPreview();
    if (result.job) {
      setJobId(result.job.id);
      setJobs((old) => [result.job!, ...old.filter((j) => j.id !== result.job!.id)]);
      setPanel("agent");
    }
    await reloadProjectSilently(selection.projectId);
  }
  return { gxSend, setGXSend, requestGXSend, confirmGXSend };
}
