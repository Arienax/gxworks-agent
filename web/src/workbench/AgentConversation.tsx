// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { PanelLeftOpen, PanelRightClose, PanelRightOpen, Square } from "lucide-react";
import type { ReactNode } from "react";
import type { Job } from "../api/client";
import { activeJob, api } from "../api/client";
import { Button } from "../components/ui";
import { jobKindLabel } from "./jobLabels";
import type { WorkbenchModel } from "./useWorkbenchController";

export const agentConversationFields = [
  "canvasOpen",
  "currentJob",
  "displayedJobStatus",
  "guarded",
  "jobId",
  "jobsPolling",
  "leftOpen",
  "pid",
  "project",
  "setCanvasOpen",
  "setJobs",
  "setLeftOpen",
  "status",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof agentConversationFields[number]> & { history: ReactNode; composer: ReactNode };
/** The central conversation column: task header, message flow and composer. */
export function AgentConversation(props: Props) {
  const { history, composer } = props;
  const {
    canvasOpen,
    currentJob,
    displayedJobStatus,
    guarded,
    jobId,
    jobsPolling,
    leftOpen,
    pid,
    project,
    setCanvasOpen,
    setJobs,
    setLeftOpen,
    status,
    t,
  } = props;

  return (
    <section className="agent-pane" aria-label={t("助手")}>
      <header className="pane-header">
        {!leftOpen && (
          <button className="icon-button sidebar-toggle" aria-label={t("显示侧栏")} title={t("显示侧栏")} onClick={() => setLeftOpen(true)}>
            <PanelLeftOpen size={17} />
          </button>
        )}
        <div className="pane-title">
          {project?.id === pid && project && <span className="pane-crumb">{project.name}</span>}
          <strong>{currentJob ? t(jobKindLabel(currentJob.kind)) : t("新任务")}</strong>
          {currentJob && status(displayedJobStatus)}
        </div>
        {currentJob && activeJob(currentJob) && (
          <Button
            variant="ghost"
            title={t("安全检查点取消")}
            onClick={() =>
              void guarded(async () => {
                const cancelled = await api<Job>(`/jobs/${jobId}/cancel`, "POST");
                jobsPolling.current?.refresh();
                setJobs((old) => old.map((job) => (job.id === cancelled.id ? cancelled : job)));
              })
            }
          >
            <Square size={11} />
            {t("取消请求")}
          </Button>
        )}
        {!!pid && (
          <button
            className={`canvas-toggle ${canvasOpen ? "active" : ""}`}
            aria-pressed={canvasOpen}
            aria-controls="engineering-canvas"
            title={t(canvasOpen ? "收起画布" : "打开画布")}
            onClick={() => setCanvasOpen((value) => !value)}
          >
            {canvasOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
            <span>{t("工程画布")}</span>
          </button>
        )}
      </header>
      {history}
      <div className="composer-dock">{composer}</div>
    </section>
  );
}
