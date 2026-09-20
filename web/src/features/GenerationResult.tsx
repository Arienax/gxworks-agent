import { useEffect, useState } from "react";
import type { Job, Json } from "../api/client";
import { api, freshGxCsvUrl } from "../api/client";
import { Button } from "../components/ui";
import { InteractionTraceExport } from "./InteractionTraceExport";

type Output = Record<string, Json>;

// Completion is a persisted job state, not a promise that a proposal exists.
// Fetch output after polling, reload and SSE completion alike. Do not depend on
// an SSE replay or the order in which project/proposal polling finishes.
export function useGenerationResult(job: Job | undefined, retry: number) {
  const [result, setResult] = useState<{
    id: string; value?: Output; error?: string;
  }>({ id: "" });
  const id = job?.kind === "generation" && job.status === "completed" ? job.id : "";
  useEffect(() => {
    let stopped = false;
    setResult({ id });
    if (id) void api<Output>(`/jobs/${encodeURIComponent(id)}/output`).then(
      (value) => { if (!stopped) setResult({ id, value }); },
      (error: Error) => { if (!stopped) setResult({ id, error: error.message }); },
    );
    return () => { stopped = true; };
  }, [id, retry]);
  const value = result.id === id ? result.value : undefined;
  const metadata = value?.generation && typeof value.generation === "object" && !Array.isArray(value.generation)
    ? value.generation as Output : undefined;
  const validation = metadata?.validation && typeof metadata.validation === "object" && !Array.isArray(metadata.validation)
    ? metadata.validation as Output : undefined;
  const blocked = job?.result?.status === "contract_mismatch" || value?.status === "contract_mismatch" || !!metadata?.contract_mismatch;
  const invalidCandidate = value?.status === "saved_invalid" || validation?.status === "invalid_candidate";
  const proposalId = typeof value?.proposal_id === "string" ? value.proposal_id
    : typeof job?.result?.proposal_id === "string" ? job.result.proposal_id : "";
  const versionId = typeof value?.version_id === "string" ? value.version_id
    : typeof job?.result?.version_id === "string" ? job.result.version_id : "";
  const loading = !!id && !versionId && !proposalId && !blocked && (!result.error || result.id !== id) && !value;
  return { id, projectId: job?.project_id || "", value, metadata, blocked, invalidCandidate, proposalId, versionId, loading,
    error: result.id === id ? result.error : undefined };
}

export type GenerationResultState = ReturnType<typeof useGenerationResult>;

export function GenerationResult({ result, busy, onOpen, onRetry, onRepair, onSpec, t }: {
  result: GenerationResultState;
  busy: boolean;
  onOpen: () => void;
  onRetry: () => void;
  onRepair: () => void;
  onSpec: () => void;
  t: (key: string) => string;
}) {
  if (!result.id) return null;
  const mismatch = result.metadata?.contract_mismatch;
  const detail = mismatch && typeof mismatch === "object" && !Array.isArray(mismatch)
    ? mismatch as Output : undefined;
  const freshCsv = result.projectId && result.versionId ? (
    <Button
      disabled={busy}
      onClick={() => window.location.assign(freshGxCsvUrl(result.projectId, result.versionId))}
      title={t("不调用模型，直接从已保存程序重新生成 CSV")}
    >
      {t("重新导出 GX Works2 CSV")}
    </Button>
  ) : null;
  return <section className="generation-result" aria-label={t("生成结果")}>
    {result.blocked ? <>
      <p className="error-text" role="status">{t("候选与确认方案冲突，未创建可接受的程序。")}</p>
      {typeof detail?.message === "string" && <p>{detail.message}</p>}
      {Array.isArray(detail?.issues) && <pre className="accepted-content">{detail.issues.map((v) => typeof v === "string" ? v : JSON.stringify(v)).join("\n")}</pre>}
      <p>{t("可以查看受阻候选及诊断；不能接受、导入或运行该候选。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看受阻候选")}</Button>{" "}
      <Button disabled={busy} onClick={onSpec}>{t("检查确认规格")}</Button>
    </> : result.versionId && result.invalidCandidate ? <>
      <p className="error-text" role="status">{t("候选存在校验错误，但梯形图和 CSV 已保留。")}</p>
      <p>{t("可以查看、导出并直接发送到 GX Works2；GX 中的黄色错误用于继续定位问题。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看错误候选")}</Button>{" "}
      {freshCsv}{" "}
      <Button disabled={busy} onClick={onRepair}>{t("局部修复")}</Button>
    </> : result.versionId ? <>
      <p>{t("程序已校验并自动保存，可直接导出文件。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看程序")}</Button>{" "}
      {freshCsv}
    </> : result.proposalId ? <>
      <p>{t("这是旧版生成的草稿，可打开后保存。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看旧草稿")}</Button>
    </> : result.loading ? <p role="status">{t("正在读取生成结果")}</p> : <>
      <p className="error-text" role="alert">{t("任务已结束，但尚未取得可显示的候选结果。")}</p>
      <p>{t("请重试读取结果；不要重复调用模型或重新建立工程。")}</p>
    </>}
    <Button disabled={busy} onClick={onRetry}>{t("重新读取结果")}</Button>
    <InteractionTraceExport jobId={result.id} t={t} />
  </section>;
}
