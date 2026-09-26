import { memo, useEffect, useMemo, useState } from "react";
import { activeJob } from "../api/client";
import type { Job, JobEvent } from "../api/client";

/** An indeterminate bar: real activity and elapsed time, never a guessed %. */
export const JobProgress = memo(function JobProgress({ job, events, t }: {
  job: Job; events: JobEvent[]; t: (key: string) => string;
}) {
  const running = activeJob(job);
  const modelDriven = !["execution", "gx_read", "gx_inspect"].includes(job.kind);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job.id, running]);
  const preview = useMemo(() => {
  let preview = { reasoning: "", content: "", discarded: false, truncated: false };
  for (const event of events) {
    if (event.event_type !== "model_preview") continue;
    const payload = event.payload;
    if (payload?.kind === "start" || payload?.kind === "discard")
      preview = { reasoning: "", content: "", discarded: payload.kind === "discard", truncated: false };
    if (payload?.kind === "delta") {
      preview.reasoning += String(payload.reasoning || "");
      preview.content += String(payload.content || "");
    }
    preview.truncated ||= !!payload?.truncated;
  }
  return preview;
  }, [events]);
  const latest = useMemo(() => [...events].reverse().find(event => ["model_progress", "progress"].includes(event.event_type)), [events]);
  const envelope = job.kind === "execution" && job.result ? job.result : null;
  const outcome = typeof envelope?.status === "string" ? envelope.status : "";
  const nested = envelope?.result;
  const executionDetails = nested && typeof nested === "object" && !Array.isArray(nested)
    ? (nested as Record<string, unknown>) : null;
  const executionFailed = !running && ["failed", "interrupted", "conflict"].includes(outcome);
  const executionMessage = typeof executionDetails?.message === "string" && executionDetails.message
    ? executionDetails.message
    : "GX 执行未完成，请检查 GX Works2 状态。";
  const hasModelPreview = !!(preview.reasoning || preview.content || preview.discarded);
  const showModelPreview = modelDriven || hasModelPreview;
  const executionCompleted = !running && outcome === "accepted" && !!executionDetails?.message;
  if (!running && !showModelPreview && !executionFailed && !executionCompleted) return null;
  const phaseNames: Record<string, string> = {
    waiting: "正在等待模型响应", thinking: "模型正在处理需求",
    receiving: "正在接收模型回复", validating: "正在检查回复格式",
  };
  const label = latest?.event_type === "model_progress"
    ? t(phaseNames[String(latest.payload?.phase)] || "正在处理")
    : t(String(latest?.payload?.message || "正在准备任务"));
  const elapsed = Math.max(0, Math.floor((now - Date.parse(job.created_at)) / 1000));
  return <section className="live-progress" aria-label={t("任务进度")}>
    {running && <>
      <div className="live-progress-label"><span role="status">{label}</span><small>{elapsed}{t("秒")}</small></div>
      <div role="progressbar" aria-label={label} aria-valuetext={label} className="live-progress-track"><span /></div>
    </>}
    {executionFailed && <p className="error-text" role="alert">{t(executionMessage)}</p>}
    {executionCompleted && <p role="status">{t(String(executionDetails?.message))}</p>}
    {showModelPreview && <details className="stream-preview">
      <summary>{t("查看实时输出")}</summary>
      <p className="muted">{t("生成过程预览，工程结果以最终校验为准。")}</p>
      {preview.discarded ? <p>{t("本次回复未通过检查，预览已清空。")}</p> : <>
        {preview.reasoning && <pre className="stream-reasoning">{preview.reasoning}</pre>}
        {preview.content && <pre>{preview.content}</pre>}
        {!preview.content && !preview.reasoning && <p>{t("正在等待模型响应")}</p>}
      </>}
      {preview.truncated && <p className="muted">{t("预览较长，完整内容请查看最终结果。")}</p>}
    </details>}
  </section>;
});
