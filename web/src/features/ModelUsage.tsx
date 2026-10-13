import { useRef, useState } from "react";
import { api, activeJob } from "../api/client";
import type { Json, ModelSettings, Job } from "../api/client";
import { Button } from "../components/ui";

type Summary = Record<string, unknown>;
const sources: Record<string,string> = {user_selection:"用户设置",user_omit:"服务默认",server_default:"服务默认",
  contract_omit:"接口约束",profile_default:"应用默认",workflow_hint:"任务要求",user_advanced:"用户设置"};

export function RequestSummary({summary, t}: {summary:Summary; t:(s:string)=>string}) {
  const rows = (Array.isArray(summary.parameters) ? summary.parameters : []) as {name:string; value:Json; source:string; sent:boolean; redacted:boolean}[];
  const features = (summary.features || {}) as Record<string,boolean>;
  const thinking = rows.find(row=>row.name === "reasoning_effort");
  return <div className="request-summary">
    <p>{String(summary.profile_name || "")} · <code>{String(summary.model || "")}</code></p>
    {thinking && <p>{t("思考强度")}: {thinking.sent ? thinking.redacted ? t("已指定（文本已隐藏）") : String(thinking.value) : t("服务默认")} · {t(sources[thinking.source] || thinking.source)}</p>}
    <p>{t("工具调用")}: {t(features.tools ? "本次请求已提供工具" : "本次请求未提供工具")} · {t(features.streaming ? "流式输出" : "非流式输出")}</p>
    <p>{t("程序格式输出")}: {t(features.structured_output ? "本次请求已指定格式" : "由工作流处理")} · {t("图片")}: {t(features.images ? "本次请求包含图片" : "本次请求没有图片")}</p>
    <details className="request-parameters"><summary>{t("请求参数与来源")}</summary><table><thead><tr><th>{t("参数")}</th><th>{t("请求值")}</th><th>{t("来源")}</th></tr></thead><tbody>
      {rows.map(row=><tr key={row.name}><td><code>{row.name}</code></td><td>{t(row.redacted ? "已指定（文本已隐藏）" : !row.sent ? "服务默认（未发送）" : String(row.value))}</td><td>{t(sources[row.source] || row.source)}</td></tr>)}
    </tbody></table></details>
  </div>;
}

export function ModelSwitcher({settings, job, disabled, onChange, onSettings, t}: {
  settings:ModelSettings | null; job?:Job; disabled:boolean; onChange:(s:ModelSettings)=>void;
  onSettings:()=>void; t:(s:string)=>string;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef(false);
  const frozen = job && activeJob(job) ? job.model_usage : null;
  return <div className="model-switcher">
    <div className="form-actions"><label className="sr-only">{t("新任务模型")}</label><select aria-label={t("当前模型（用于新任务）")} title={frozen?.model ? `${t("运行中")}: ${String(frozen.profile_name || frozen.model)} · ${String(frozen.model)}` : t("新任务模型")} value={settings?.active_profile_id || ""}
      disabled={disabled || busy || !settings?.profiles?.length} onChange={async e=>{
        if (pending.current) return;
        pending.current=true; setBusy(true); setError("");
        try { onChange(await api<ModelSettings>("/settings", "PUT", {active_profile_id:e.target.value})); }
        catch(e) { setError((e as Error).message); }
        finally { pending.current=false; setBusy(false); }
      }}>
      {!settings?.profiles?.length && <option value="">{t("添加模型")}</option>}
      {settings?.profiles?.map(p=><option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
    </select><Button variant="ghost" onClick={onSettings}>{t("模型设置")}</Button>
    {!!frozen?.model && frozen.profile_id !== settings?.active_profile_id && <small title={String(frozen.model)}>{t("运行中")}: {String(frozen.profile_name || frozen.model)}</small>}</div>
    {error && <p role="alert">{error}</p>}
  </div>;
}
