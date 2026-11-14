// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { Button } from "../components/ui";
import { hardDomain, parameterEnabled, parameterTitle, selectedValues, selectionFor, validValue } from "./modelParameters";
import type { CapabilityContract, Scalar, UserModelSettings } from "./modelParameters";

const sources: Record<string, string> = { catalog: "本地预设", metadata: "服务元数据", manual: "手动声明",
  generic: "通用参考模板", probe: "历史验证", observation: "使用记录", legacy: "旧配置" };
const labels = { supported: "已声明支持", accepted: "仅确认接口接受，是否生效未知", unknown: "未验证，可手动设置",
  unsupported: "声明不支持", fixed: "固定值", conditional: "条件支持" };

export function ModelParameterDiagnostics({ contract, settings, disabled, t, onVerify }: {
  contract: CapabilityContract;
  settings: UserModelSettings;
  disabled: boolean;
  t: (key: string) => string;
  onVerify: (target: string, kind: "parameter" | "capability", value?: Scalar) => void;
}) {
  if (!contract.scope) return null;
  const values = selectedValues(contract, settings);
  return <section className="model-diagnostics" aria-label={t("兼容性详情")}>
    <div className="capability-grid" aria-label={t("模型能力合同")}>
      {Object.entries(contract.capabilities || {}).map(([name, cap]) => <div key={name}>
        <code>{name}</code>: {cap.status} · {t(sources[cap.source] || cap.source)}{cap.modes?.length ? ` · ${cap.modes.join(" / ")}` : ""}
        {["tools", "structured_output"].includes(name) && cap.status !== "unsupported" &&
          (name !== "structured_output" || !cap.modes?.length || cap.modes.includes("json_object")) &&
          <Button disabled={disabled} onClick={() => onVerify(name, "capability", name === "structured_output" ? "json_object" : undefined)}>
            {t("单项验证（1 次请求）")}
          </Button>}
      </div>)}
    </div>
    {Object.entries(contract.parameters || {}).map(([name, desc]) => {
      const value = values[name];
      const chosen = value != null;
      const enabled = parameterEnabled(name, contract, values, settings);
      const valid = validValue(desc, value) && enabled && !["fixed", "unsupported"].includes(desc.status);
      return <div className="parameter-control" key={name} data-parameter={name}>
        <div className="parameter-heading">
          <span><strong>{t(parameterTitle(name, desc))}</strong><small className="muted mono">{name}</small></span>
          <output>{chosen ? String(value) : t(selectionFor(name, settings).mode === "omit" ? "服务默认" : "跟随应用设置 · 默认")}</output>
        </div>
        <p className="muted">{t(labels[desc.status])} · {t(sources[desc.source] || desc.source)}</p>
        {chosen && valid && <Button disabled={disabled} onClick={() => onVerify(name, "parameter", value)}>{t("验证当前值（1 次请求）")}</Button>}
        {!!desc.evidence?.accepted_values?.length && <p className="muted">{t("历史接受样本（不是可选值限制）")}: {desc.evidence.accepted_values.join(", ")}</p>}
        {!!desc.evidence?.observations?.length && <p className="muted">{t("最近使用记录（仅对应当时请求组合）")}: {desc.evidence.observations.slice(0, 3).map(row => `${row.value}: ${row.outcome}`).join("; ")}</p>}
        {chosen && !valid && <p role="alert">{t("当前值不适用于此模型，请调整或恢复默认。")}</p>}
        {desc.status === "fixed" && <p>{t("固定值")}: {String(hardDomain(desc).values?.[0])}</p>}
        {(desc.requires || desc.conflicts_with) && <p className="muted mono">{JSON.stringify({ requires: desc.requires, conflicts_with: desc.conflicts_with })}</p>}
      </div>;
    })}
  </section>;
}
