import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/generated";
import { Button } from "../components/ui";

export type ApprovalSettings = components["schemas"]["ApprovalSettings"];
export const approvalLabels = { ask: "逐项审批", auto: "替我审批", full: "完全访问" } as const;
const descriptions = {
  ask: "本地程序自动保存；发送到 GX Works2、仿真和调试前逐项确认。",
  auto: "本地程序自动保存；按规则自动批准绑定版本的仿真和调试，发送到 GX Works2 仍需确认。",
  full: "自动批准工作台已支持的 GX、仿真和调试操作，不再逐项询问。",
};

export function ApprovalSettingsPanel({ value, onChange, disabled, t }: {
  value: ApprovalSettings; onChange: (value: ApprovalSettings) => void;
  disabled: boolean; t: (key: string) => string;
}) {
  const [mode, setMode] = useState(value.mode);
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { setMode(value.mode); setConsent(false); }, [value.mode, value.revision]);
  const needsConsent = mode === "full" && value.mode !== "full";
  const pending = useRef(false);
  async function save() {
    if (pending.current || disabled) return;
    pending.current = true;
    setSaving(true); setError(""); setMessage("");
    try {
      const next = await api<ApprovalSettings>("/settings/approval", "PUT", {
        mode, expected_revision: value.revision, confirm_full_access: consent,
      });
      onChange(next); setMessage(t("审批模式已保存，仅影响后续请求。"));
    } catch (error) { setError((error as Error).message); }
    finally { pending.current = false; setSaving(false); }
  }
  return <section className="approval-settings">
    <h3>{t("操作审批")}</h3>
    <p className="muted">{t("程序通过校验后自动保存并显示；历史版本可随时切回，不再二次确认本地保存。")}</p>
    <fieldset disabled={disabled || saving}>
      <legend className="sr-only">{t("审批模式")}</legend>
      {(Object.keys(approvalLabels) as (keyof typeof approvalLabels)[]).map((id) => (
        <label className={`approval-choice ${mode === id ? "selected" : ""}`} key={id}>
          <input type="radio" name="approval-mode" value={id} checked={mode === id}
            onChange={() => { setMode(id); setConsent(false); }} />
          <span><strong>{t(approvalLabels[id])}</strong><small>{t(descriptions[id])}</small></span>
        </label>
      ))}
    </fieldset>
    {needsConsent && <label className="approval-consent">
      <input type="checkbox" checked={consent} disabled={disabled || saving} onChange={(e) => setConsent(e.target.checked)} />
      {t("我允许工作台自动执行已支持的 GX、仿真和调试操作。")}
    </label>}
    <p className="muted">{t("审批模式不改变 PLC 校验、文件完整性检查或工具范围，也不是 Windows 权限隔离。")}</p>
    <Button variant="primary" disabled={disabled || saving || mode === value.mode || (needsConsent && !consent)} onClick={() => void save()}>
      {t(saving ? "正在保存" : "保存审批模式")}
    </Button>
    {message && <p role="status">{message}</p>}{error && <p className="error-text" role="alert">{error}</p>}
  </section>;
}
