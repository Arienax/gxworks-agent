export function InteractionTraceExport({ jobId, t }: {
  jobId: string;
  t: (key: string) => string;
}) {
  if (!jobId) return null;
  return <div className="job-diagnostic-export">
    <a
      className="button secondary"
      href={`/api/jobs/${encodeURIComponent(jobId)}/diagnostics`}
      download
    >
      {t("导出交互记录")}
    </a>
    <p className="muted">{t("任务编号")}：<code>{jobId}</code></p>
    <p className="muted">
      {t("导出内容包含本任务实际发送给模型的消息、模型思考与回复、工具调用、token 使用和任务事件；API Key、Authorization 与图片/二进制正文不会导出，也不会自动上传。")}
    </p>
  </div>;
}
