import { useEffect, useState } from "react";
import type { Job, Json } from "../api/client";
import { api, freshGxCsvUrl } from "../api/client";
import { Button } from "../components/ui";
import { CapabilityReview } from "./CapabilityReview";
import { DirectClarification } from "./DirectClarification";
import { clarificationQuestions } from "./clarificationAnswers";

type Output = Record<string, Json>;

// Completion is a persisted job state, not a promise that a proposal exists.
// Fetch output after polling, reload and SSE completion alike. Do not depend on
// an SSE replay or the order in which project/proposal polling finishes.
export function useGenerationResult(job: Job | undefined, retry: number) {
  const [result, setResult] = useState<{
    id: string; value?: Output; error?: string;
  }>({ id: "" });
  const id = (job?.kind === "generation" || job?.kind === "direct_generation") && job.status === "completed" ? job.id : "";
  useEffect(() => {
    const controller = new AbortController();
    setResult(old => old.id === id ? old : { id });
    if (id) void api<Output>(`/jobs/${encodeURIComponent(id)}/output`, "GET", undefined, { signal: controller.signal }).then(
      (value) => { if (!controller.signal.aborted) setResult({ id, value }); },
      (error: Error) => { if (!controller.signal.aborted) setResult({ id, error: error.message }); },
    );
    return () => controller.abort();
  }, [id, retry]);
  const value = result.id === id ? result.value : undefined;
  const metadata = value?.generation && typeof value.generation === "object" && !Array.isArray(value.generation)
    ? value.generation as Output : undefined;
  const validation = metadata?.validation && typeof metadata.validation === "object" && !Array.isArray(metadata.validation)
    ? metadata.validation as Output : undefined;
  const blocked = job?.result?.status === "contract_mismatch" || value?.status === "contract_mismatch" || !!metadata?.contract_mismatch;
  const needsInput = job?.kind === "direct_generation" && job.status === "completed"
    && (job.result?.status === "needs_input" || value?.status === "needs_input");
  const invalidCandidate = value?.status === "saved_invalid" || validation?.status === "invalid_candidate";
  const unverifiedCandidate = value?.status === "saved_unverified" || validation?.profile === "unavailable_target";
  const proposalId = typeof value?.proposal_id === "string" ? value.proposal_id
    : typeof job?.result?.proposal_id === "string" ? job.result.proposal_id : "";
  const versionId = typeof value?.version_id === "string" ? value.version_id
    : typeof job?.result?.version_id === "string" ? job.result.version_id : "";
  const loading = !!id && !versionId && !proposalId && !blocked && (!result.error || result.id !== id) && !value;
  const questions = clarificationQuestions(value?.missing_info ?? metadata?.missing_info);
  return { id, projectId: job?.project_id || "", value, metadata, questions, blocked, needsInput, invalidCandidate, unverifiedCandidate, proposalId, versionId, loading,
    error: result.id === id ? result.error : undefined };
}

export function STVerification({ report, t }: { report: Json | undefined; t: (key: string) => string }) {
  if (!report || typeof report !== 'object' || Array.isArray(report)) return null;
  const layers = [['syntax', '语法检查'], ['semantic', '语义检查'],
    ['native_compile', '原生编译'], ['behavior', '行为验证']] as const;
  const labels: Record<string, string> = { passed: '通过', failed: '失败', unverified: '未验证' };
  return <dl aria-label={t('ST 验证状态')}>
    {layers.map(([key, label]) => <div key={key}>
      <dt>{t(label)}</dt><dd>{t(labels[String(report[key])] || '未验证')}</dd>
    </div>)}
  </dl>;
}

export type GenerationResultState = ReturnType<typeof useGenerationResult>;

export function GenerationResult({ result, busy, onOpen, onRetry, onRepair, onSpec, clarification, t }: {
  result: GenerationResultState;
  busy: boolean;
  onOpen: () => void;
  onRetry: () => void;
  onRepair: () => void;
  onSpec: () => void;
  clarification?: { answers: Record<string, string>; onChange: (id: string, value: string) => void; disabled: boolean };
  t: (key: string) => string;
}) {
  if (!result.id) return null;
  const mismatch = result.metadata?.contract_mismatch;
  const detail = mismatch && typeof mismatch === "object" && !Array.isArray(mismatch)
    ? mismatch as Output : undefined;
  const notes = result.metadata?.generator_notes;
  const generatorNotes = notes && typeof notes === "object" && !Array.isArray(notes) && notes.source === "generator"
    ? notes as Output : undefined;
  const freshCsv = result.projectId && result.versionId ? (
    <Button
      disabled={busy}
      onClick={() => window.location.assign(freshGxCsvUrl(result.projectId, result.versionId))}
    >
      {t("重新导出 GX Works2 CSV")}
    </Button>
  ) : null;
  return <section className="generation-result" aria-label={t("生成结果")}>
    {result.needsInput ? <>
      {result.error ? <p role="alert">{t("暂时无法读取结果，请重试。")}</p>
        : !result.questions.length ? <p role="status">{t("正在读取生成结果")}</p>
        : clarification ? <DirectClarification questions={result.questions} {...clarification} t={t} />
        : result.questions.map(question => <p key={question.id}>{question.question}</p>)}
    </> : result.blocked ? <>
      <p className="error-text" role="status">{t("生成的程序与需求不一致，未保存。")}</p>
      {typeof detail?.message === "string" && <p>{detail.message}</p>}
      {Array.isArray(detail?.issues) && <pre className="accepted-content">{detail.issues.map((v) => typeof v === "string" ? v : JSON.stringify(v)).join("\n")}</pre>}
      <Button disabled={busy} onClick={onOpen}>{t("查看问题")}</Button>{" "}
      <Button disabled={busy} onClick={onSpec}>{t("检查需求")}</Button>
    </> : result.versionId && result.unverifiedCandidate ? <>
      <p role="status">{t("所选 CPU 缺少本地校验规则，已保留候选，程序正确性未验证。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看程序")}</Button>{" "}
      {freshCsv}
    </> : result.versionId && result.invalidCandidate ? <>
      <p className="error-text" role="status">{t("程序存在错误，请检查后再使用。")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看问题")}</Button>{" "}
      {freshCsv}{" "}
      <Button disabled={busy} onClick={onRepair}>{t("局部修复")}</Button>
    </> : result.versionId ? <>
      <p role="status">{t("程序已保存")}</p>
      <Button disabled={busy} onClick={onOpen}>{t("查看程序")}</Button>{" "}
      {freshCsv}
    </> : result.proposalId ? <>
      {result.value?.status === 'awaiting_native_verification' ? <>
        <p role="status">{t('工程方案已生成，等待原生验证。')}</p>
        <Button disabled={busy} onClick={onOpen}>{t('查看工程方案')}</Button>
      </> : <Button disabled={busy} onClick={onOpen}>{t("查看旧草稿")}</Button>}
    </> : result.loading ? <p role="status">{t("正在读取生成结果")}</p> : <>
      <p className="error-text" role="alert">{t("暂时无法读取结果，请重试。")}</p>
    </>}
    {result.versionId && !result.needsInput && generatorNotes && (
      [["decisions", "实现说明"], ["open_items", "可选调整"]] as const
    ).map(([key, label]) => {
      const items = generatorNotes[key];
      return Array.isArray(items) && items.length > 0 ? <details key={key}>
        <summary>{t(label)}</summary>
        <p>{t("来自生成器的说明")}</p>
        <ul>{items.filter((item): item is string => typeof item === "string").map((item, index) => <li key={index}>{item}</li>)}</ul>
      </details> : null;
    })}
    <STVerification report={result.metadata?.semantic_validation} t={t} />
    <CapabilityReview metadata={result.metadata} t={t} />
    <Button disabled={busy} onClick={onRetry}>{t("重新读取结果")}</Button>
  </section>;
}
