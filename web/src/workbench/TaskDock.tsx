import {
  Activity,
  Square
} from "lucide-react";
import type { Job } from "../api/client";
import { activeJob, api } from "../api/client";
import { Button } from "../components/ui";
import { statusText } from "../i18n";
import type { WorkbenchModel } from "./useWorkbenchController";

const jobKindLabel = (kind: string) =>
  (
    ({
      analysis: "整理需求",
      generation: "生成程序",
      direct_generation: "生成程序",
      agent: "工程问答",
      review: "检查程序",
      test_plan: "设计测试",
      debug_plan: "排查问题",
      gx_read: "从 GX 读取",
      gx_inspect: "检查 GX 工程",
      execution: "执行操作",
    }) as Record<string, string>
  )[kind] || "任务";
export const taskDockFields = [
  "currentJob",
  "displayedJobStatus",
  "guarded",
  "jobId",
  "jobs",
  "jobsPolling",
  "latestMessage",
  "locale",
  "setJobId",
  "setJobs",
  "status",
  "t",
] as const;
type Props = Pick<WorkbenchModel, typeof taskDockFields[number]>;
export function TaskDock(props: Props) {
  const {
    currentJob,
    displayedJobStatus,
    guarded,
    jobId,
    jobs,
    jobsPolling,
    latestMessage,
    locale,
    setJobId,
    setJobs,
    status,
    t,
  } = props;

  return (
    <footer className="task-dock">
      <div>
        <Activity size={15} />
        <strong>{t("任务")}</strong>
        <select
          aria-label={t("任务记录")}
          value={jobId}
          onChange={(e) => setJobId(e.target.value)}
        >
          <option value="">{t("暂无任务")}</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {t(jobKindLabel(j.kind))} ·{" "}
              {statusText(
                locale,
                j.id === jobId
                  ? displayedJobStatus || j.status
                  : j.result?.status === "needs_input"
                    ? "needs_input"
                    : j.result?.status === "contract_mismatch"
                      ? "contract_mismatch"
                      : j.status,
              )}{" "}
              · {j.id.slice(-6)}
            </option>
          ))}
        </select>
        {currentJob && status(displayedJobStatus)}
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
      </div>
      <span className="dock-progress">
        {String(latestMessage?.message || latestMessage?.text || "")}
      </span>
      <span className="local-indicator">
        <span className="connection-dot" />
        {t("已连接")}
      </span>
    </footer>
  );
}
