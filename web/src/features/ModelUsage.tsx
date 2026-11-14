// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { ChartNoAxesColumn } from "lucide-react";
import { useRef, useState } from "react";
import { api, activeJob } from "../api/client";
import type { Json, ModelSettings, Job } from "../api/client";
import { Popover } from "../components/Popover";
import { Button } from "../components/ui";
import { SelectMenu } from "../components/SelectMenu";
import type { Locale } from "../i18n";
import { conversationTokenUsage } from "./conversationUsage";
import { reasoningChoices } from "./modelParameters";
import type { CapabilityContract, UserModelSettings } from "./modelParameters";

type Summary = Record<string, unknown>;
const sources: Record<string,string> = {user_selection:"用户设置",user_omit:"服务默认",server_default:"服务默认",
  contract_omit:"接口约束",profile_default:"应用默认",workflow_hint:"任务要求",user_advanced:"用户设置"};
const effortLabels: Record<string,string> = {"":"服务默认", none:"关闭推理", off:"关闭推理", enabled:"开启推理",
  minimal:"最低", low:"低", medium:"中", high:"高", xhigh:"极高", max:"最高"};

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

export function ComposerTokenUsage({ jobs, projectId, conversationId, locale, t }: {
  jobs: readonly Job[];
  projectId: string;
  conversationId: string;
  locale: Locale;
  t: (key: string) => string;
}) {
  const usage = conversationTokenUsage(jobs, projectId, conversationId);
  const number = new Intl.NumberFormat(locale);
  const percent = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 2 });
  let cacheRate = "—";
  if (usage && usage.input > 0 && usage.cached !== undefined) {
    cacheRate = percent.format(usage.cached / usage.input);
    if (usage.cached < usage.input && cacheRate === percent.format(1)) cacheRate = `<${cacheRate}`;
  }
  const label = t("会话用量");
  return <Popover key={`${projectId}:${conversationId}`} className="composer-token-usage" summaryClassName="composer-pill"
    label={label} summary={<><ChartNoAxesColumn size={13} aria-hidden />{label}</>}>
    <dl className="composer-token-usage-values" aria-label={label}>
      <dt>{t("输入 token")}</dt><dd>{usage ? number.format(usage.input) : "—"}</dd>
      <dt>{t("输出 token")}</dt><dd>{usage ? number.format(usage.output) : "—"}</dd>
      <dt>{t("命中 token")}</dt><dd>{usage?.cached !== undefined ? number.format(usage.cached) : "—"}</dd>
      <dt>{t("未命中 token")}</dt><dd>{usage?.cached !== undefined ? number.format(usage.input - usage.cached) : "—"}</dd>
      <dt>{t("缓存命中率")}</dt><dd>{cacheRate}</dd>
    </dl>
  </Popover>;
}

export function ModelSwitcher({settings, job, disabled, onChange, onSettings, t, compact = false}: {
  settings:ModelSettings | null; job?:Job; disabled:boolean; onChange:(s:ModelSettings)=>void;
  onSettings:()=>void; t:(s:string)=>string; compact?: boolean;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef(false);
  const frozen = job && activeJob(job) ? job.model_usage : null;
  const profiles = settings?.profiles || [];
  const profile = profiles.find(item => item.id === settings?.active_profile_id);
  const contract = (profile?.contract || {}) as CapabilityContract;
  const effort = reasoningChoices(contract, (profile?.user_settings || {}) as UserModelSettings);
  async function save(change: object) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    try { onChange(await api<ModelSettings>("/settings", "PUT", change)); }
    catch (e) { setError((e as Error).message); }
    finally { pending.current = false; setBusy(false); }
  }
  return <div className={`model-switcher${compact ? " compact" : ""}`}>
    <div className="form-actions">
      <SelectMenu className="model-picker-pill" label={t("当前模型（用于新任务）")}
        title={frozen?.model ? `${t("运行中")}: ${String(frozen.profile_name || frozen.model)} · ${String(frozen.model)}` : t("新任务模型")}
        value={settings?.active_profile_id || ""}
        disabled={disabled || busy || !profiles.length}
        onChange={(id) => void save({ active_profile_id: id })}
        options={profiles.length
          ? profiles.map((p) => ({ value: p.id, label: `${p.name} · ${p.model}` }))
          : [{ value: "", label: t("添加模型"), disabled: true }]} />
      {profile && effort && <SelectMenu className="model-picker-pill reasoning-picker-pill"
        label={t("思考强度")} value={effort.current} disabled={disabled || busy}
        options={effort.options.map(option => ({
          value: option.value, label: t(effortLabels[option.value] ?? option.value), disabled: option.disabled,
        }))}
        onChange={value => {
          const choice = effort.options.find(option => option.value === value);
          if (choice) void save({ profile: { id: profile.id, contract, user_settings: choice.settings } });
        }} />}
      {(!compact || !profiles.length) && <Button variant="ghost" onClick={onSettings}>{t("模型设置")}</Button>}
      {!!frozen?.model && frozen.profile_id !== settings?.active_profile_id && <small title={String(frozen.model)}>{t("运行中")}: {String(frozen.profile_name || frozen.model)}</small>}</div>
    {error && <p role="alert">{error}</p>}
  </div>;
}
