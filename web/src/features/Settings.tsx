import { useEffect, useRef, useState } from "react";
import { usePolling } from "../lifecycle/usePolling";
import { sameValue } from "../lifecycle/requests";
import { api } from "../api/client";
import type { Json, ModelSettings } from "../api/client";
import type { components } from "../api/generated";
import { Button, Badge, Modal } from "../components/ui";
import { ModelParameters } from "./ModelParameterControls";
import { ENDPOINT_PRESETS, adoptSelections, modifiedParameters } from "./modelParameters";
import { settingsTranslation } from "./modelSettingsLocale";
import type { Locale } from "../i18n";
import { RequestSummary } from "./ModelUsage";
import type { CapabilityContract, UserModelSettings, DiscoveryMode, Scalar } from "./modelParameters";

type Profile = NonNullable<ModelSettings["profiles"]>[number] & {
  deletable?: boolean;
  contract?: Record<string, Json>;
  user_settings?: Record<string, Json>;
  capability_overrides?: Record<string, Json>;
};

type Discovery = {
  kind?: "model_discovery_v1";
  mode?: DiscoveryMode;
  elapsed_ms?: number;
  budget_seconds?: number;
  budget_exhausted?: boolean;
  partial?: boolean;
  models?: string[];
  recommended_model?: string | null;
  selected_model_available?: boolean;
  contract?: CapabilityContract;
  note?: string;
};

type McpStatus = components["schemas"]["MCPIntegrationStatus"];
type McpResult = components["schemas"]["MCPIntegrationResult"];

const currentProjectId = () =>
  new URLSearchParams(window.location.search).get("project") || "";

function parseDiscovery(message: string): Discovery | null {
  try {
    const value = JSON.parse(message) as Discovery;
    return value?.kind === "model_discovery_v1" ? value : null;
  } catch {
    return null;
  }
}

function McpIntegrations({
  t,
  disabled,
}: {
  t: (s: string) => string;
  disabled: boolean;
}) {
  const projectId = currentProjectId();
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [serviceCheck, setServiceCheck] = useState<"unchecked" | "passed" | "failed">("unchecked");

  const commandPending = useRef(false);
  useEffect(() => {
    setStatus(null); setMessage(""); setError(""); setServiceCheck("unchecked");
  }, [projectId]);
  const polling = usePolling(projectId || null,
    signal => api<McpStatus>(`/integrations/mcp?project_id=${encodeURIComponent(projectId)}`, "GET", undefined, { signal }),
    value => setStatus(old => sameValue(old, value)),
    error => setError(error instanceof Error ? error.message : String(error)), 5000);

  const run = async (path: string) => {
    if (!projectId || commandPending.current || disabled) return;
    commandPending.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<McpResult>(path, "POST", { project_id: projectId });
      if (result.status === "failed") throw new Error(result.message || t("连接失败"));
      setServiceCheck("passed");
      setMessage(result.message || t("连接成功"));
    } catch (e) {
      if (path.endsWith("/test")) setServiceCheck("failed");
      setError((e as Error).message);
    } finally {
      polling.current?.refresh();
      commandPending.current = false;
      setBusy(false);
    }
  };

  const manualConfig = `[mcp_servers.gxworks]\ncommand = "gxworks-agent-mcp"\nstartup_timeout_sec = 30\ntool_timeout_sec = 120`;

  return (
    <div className="form settings-form">
      <div className="notice">
        <strong>{t("在 Codex 中使用当前工程")}</strong>
        <p>{t("连接后重启 Codex，并保持工作台运行。")}</p>
      </div>

      {status && <div className="notice">
        <p><strong>{t("连接配置")}</strong>{" · "}
          {t(status?.codex_configured ? "连接配置已写入" : "连接配置未写入")}
        </p>
        <p><strong>{t("MCP 服务检查")}</strong>{" · "}
          {t(serviceCheck === "passed" ? "本次服务检查通过" : serviceCheck === "failed" ? "本次服务检查失败" : "尚未执行服务检查")}
        </p>
        <p><strong>{t("客户端工具调用")}</strong>{" · "}
          <Badge tone={status?.client_observed ? "good" : "neutral"}>
            {t(status?.client_observed ? "客户端已调用" : "等待客户端调用")}
          </Badge>
        </p>
        {status?.last_tool && <p>{t("最后调用工具")}: <span className="mono">{status.last_tool}</span>
          {status.last_call_at && <> · <time dateTime={status.last_call_at}>{new Date(status.last_call_at).toLocaleString()}</time></>}
        </p>}
        {status?.client_observed && <p>{t(status.generation_context_observed ? "已读取生成上下文" : "尚未读取生成上下文")}</p>}
        {status?.candidate_proposal_id && <p>{t("待检查候选")}: <span className="mono">{status.candidate_proposal_id}</span></p>}
      </div>}

      <div className="context-chips">
        <Badge tone={status?.credential_ready ? "good" : "warn"}>
          {t(status?.credential_ready ? "工作台连接已就绪" : "工作台连接待初始化")}
        </Badge>
        <Badge tone={status?.launcher_ready ? "good" : "warn"}>
          {t(status?.launcher_ready ? "连接组件已就绪" : "连接组件不可用")}
        </Badge>
      </div>

      <label>
        {t("当前工程")}
        <input readOnly value={projectId || t("未选择工程")} />
      </label>
      {status?.bound_project_id && (
        <p className="muted">
          {t("已绑定工程")}: <span className="mono">{status.bound_project_id}</span>
        </p>
      )}

      <div className="form-actions">
        <Button
          variant="primary"
          disabled={!projectId || busy || disabled || !status?.credential_ready || !status?.launcher_ready}
          onClick={() => void run("/integrations/mcp/codex/connect")}
        >
          {t(busy ? "处理中…" : "连接 Codex")}
        </Button>
        <Button
          disabled={!projectId || busy || disabled || !status?.credential_ready || !status?.launcher_ready}
          onClick={() => void run("/integrations/mcp/test")}
        >
          {t("测试 MCP 连接")}
        </Button>
      </div>

      {!status?.credential_ready && (
        <p className="muted">
          {t("连接尚未就绪。请重新启动工作台，再尝试连接。")}
        </p>
      )}
      {message && <p role="status">{t(message)}</p>}
      {error && <p role="alert" className="error-text">{t(error)}</p>}

      <details>
        <summary>{t("高级 / 其他 MCP 客户端")}</summary>
        <label>
          Service URL
          <input readOnly value={status?.service_url || window.location.origin} />
        </label>
        <Badge tone={status?.codex_cli_available ? "good" : "neutral"}>
          {t(status?.codex_cli_available ? "Codex CLI 已检测到（可选）" : "Codex CLI 未检测到（可选）")}
        </Badge>
        <pre className="mono">{manualConfig}</pre>
        <Button onClick={() => void navigator.clipboard.writeText(manualConfig)}>
          {t("复制高级配置")}
        </Button>
      </details>
    </div>
  );
}

export function Settings({
  value,
  t: translateText,
  locale = "zh-CN",
  onChange,
  disabled,
}: {
  value: ModelSettings;
  locale?: Locale;
  t: (s: string) => string;
  onChange: (settings: ModelSettings) => void;
  disabled: boolean;
}) {
  const t = (text: string) => settingsTranslation(locale, text, translateText);
  const [page, setPage] = useState<"models" | "integrations">("models");
  const [selected, setSelected] = useState(value.active_profile_id || "");
  // One editor card at a time. A workspace with no saved profile starts with the
  // create card open, so the first run needs no extra click.
  const [cardOpen, setCardOpen] = useState(!value.profiles?.length);
  const [creating, setCreating] = useState(!value.profiles?.length);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState(""),
    [model, setModel] = useState(""),
    [baseUrl, setBaseUrl] = useState("");
  const [preset, setPreset] = useState("");
  const [mode, setMode] = useState<"catalog" | "custom">("catalog");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [debugMessage, setDebugMessage] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Profile | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [contract, setContract] = useState<CapabilityContract>({});
  const [userSettings, setUserSettings] = useState<UserModelSettings>({});
  const [listing, setListing] = useState(false);
  const [manualOverrides, setManualOverrides] = useState("{}");
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState("");
  const [resolveRevision, retryResolve] = useState(0);
  const [preview, setPreview] = useState<Record<string, Json> | null>(null);
  const [scenario, setScenario] = useState("text");
  const moreSettings = useRef<HTMLDetailsElement>(null);
  const draftRevision = useRef(0);
  const invalidate = (clearModels = false) => {
    draftRevision.current += 1;
    setContract({});
    setUserSettings({});
    setMessage("");
    setDebugMessage("");
    setPreview(null);
    if (clearModels) setDiscoveredModels([]);
  };
  const changeModel = (next: string) => {
    if (next !== model) { setManualOverrides("{}"); invalidate(); }
    setModel(next);
  };
  useEffect(() => () => { draftRevision.current += 1; }, []);
  const profile = value.profiles?.find((p) => p.id === selected) as
    | Profile
    | undefined;
  const presetFor = (url: string) =>
    ENDPOINT_PRESETS.find(item => item.url === url)?.url || "";
  const loadFields = (target: Profile | undefined) => {
    draftRevision.current += 1;
    setName(target?.name || "");
    setModel(target?.model || "");
    setBaseUrl(target?.base_url || "");
    const matched = presetFor(target?.base_url || "");
    setPreset(matched);
    // A saved address that is not a known service is a custom declaration.
    setMode(matched ? "catalog" : "custom");
    setSecret("");
    setContract((target?.contract || {}) as CapabilityContract);
    setUserSettings((target?.user_settings || {}) as UserModelSettings);
    setManualOverrides(JSON.stringify(target?.capability_overrides || {}, null, 2));
    setDiscoveredModels([]);
  };
  /**
   * Apply one endpoint preset. This only fills the address and a starting name:
   * presets never carry a model id or tuning values, and the chosen value stays
   * visible in the select instead of snapping back to the placeholder.
   */
  const applyPreset = (url: string) => {
    const found = ENDPOINT_PRESETS.find(item => item.url === url);
    if (!found) { setPreset(""); return; }
    setPreset(found.url);
    // A preset names the service; it never renames a profile the user already named.
    if (!name.trim()) setName(found.name);
    setBaseUrl(found.url);
    setManualOverrides("{}");
    invalidate(true);
  };
  useEffect(() => {
    // Background job refreshes must preserve in-progress fields and secrets.
    if (creating) { draftRevision.current += 1; return; }
    loadFields(profile);
  }, [selected, creating]);
  const parse = (text: string) => {
    const result = JSON.parse(text);
    if (!result || typeof result !== "object" || Array.isArray(result))
      throw new Error(t("配置数据必须是有效 JSON 对象。"));
    return result;
  };
  const command = () => {
    const invalid = document.querySelector<HTMLInputElement>(".model-parameters input:invalid");
    if (invalid || document.querySelector('.model-parameters [data-invalid="true"]')) {
      if (moreSettings.current) moreSettings.current.open = true;
      invalid?.reportValidity();
      throw new Error(t("请检查更多设置中的参数。"));
    }
    return ({
    ...(creating ? {} : { id: selected }),
    name,
    model,
    base_url: baseUrl,
    contract,
    user_settings: contract.scope ? userSettings : {},
    capability_overrides: parse(manualOverrides),
  });
  };
  const commandPending = useRef(false);
  const run = async (action: () => Promise<void>) => {
    if (commandPending.current || disabled) return;
    commandPending.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    setDebugMessage("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      commandPending.current = false;
      setBusy(false);
    }
  };
  const ready =
    !busy && !disabled && !resolving && !resolveError && !!contract.scope && !!name.trim() && !!model.trim() && !!baseUrl.trim();
  const discoverReady =
    !busy && !disabled && !!baseUrl.trim() && (!creating || !!secret.trim());
  const discover = () => void run(async () => {
    const revision = draftRevision.current;
    setListing(true);
    try {
      const draft = {
        ...(creating ? {} : { id: selected }),
        name: name.trim() || "Custom API",
        model: model.trim() || "__discover__",
        base_url: baseUrl,
        contract, user_settings: contract.scope ? userSettings : {}, capability_overrides: parse(manualOverrides),
        ...(secret ? { api_key: secret } : {}),
      };
      const result = await api<{ status: string; message: string; discovery?: Discovery }>(
        "/settings/detect", "POST", { profile: draft, mode: "list" },
      );
      if (revision !== draftRevision.current) return;
      if (!["connected", "resolved"].includes(result.status)) throw new Error(result.message || t("连接失败"));
      const discovery = result.discovery || parseDiscovery(result.message);
      if (!discovery) throw new Error(result.message || t("未获取到模型列表"));
      setDiscoveredModels(discovery.models || []);
      retryResolve(n=>n+1);
      setMessage(t(discovery.models?.length ? "模型列表已更新" : "未获取到模型列表"));
    } catch (e) {
      if (revision === draftRevision.current) throw e;
    } finally {
      setListing(false);
    }
  });
  const beginCreate = () => {
    draftRevision.current += 1;
    setCardOpen(true);
    setCreating(true);
    setSelected("");
    setName("");
    setModel("");
    setBaseUrl("");
    setPreset("");
    setMode("catalog");
    setSecret("");
    setContract({});
    setUserSettings({});
    setManualOverrides("{}");
    setDiscoveredModels([]);
    setError("");
    setMessage("");
    setDebugMessage("");
  };
  const openProfile = (target: Profile) => {
    setCardOpen(true);
    setCreating(false);
    setSelected(target.id);
    loadFields(target);
    setError("");
    setMessage("");
    setDebugMessage("");
  };
  const closeCard = () => {
    draftRevision.current += 1;
    setCardOpen(false);
    setCreating(false);
    setError("");
    setMessage("");
    setDebugMessage("");
    setListing(false);
  };

  useEffect(() => { setPreview(null); }, [userSettings, model, baseUrl, secret, manualOverrides, scenario]);
  useEffect(() => {
    if (!cardOpen || !model.trim() || !baseUrl.trim()) { setResolving(false); return; }
    const abort = new AbortController();
    const revision = draftRevision.current;
    setResolving(true);
    setResolveError("");
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const result = await api<{status: string; message: string; discovery?: Discovery}>("/settings/resolve", "POST", {
            profile: { ...(creating ? {} : {id:selected}), name:name.trim() || "Custom API", model:model.trim(),
              base_url:baseUrl, capability_overrides:parse(manualOverrides), ...(secret ? {api_key:secret} : {}) },
          }, {signal:abort.signal});
          if (abort.signal.aborted || revision !== draftRevision.current) return;
          if (!result.discovery?.contract) throw new Error(result.message);
          setContract(result.discovery.contract);
          setUserSettings(old => adoptSelections(result.discovery!.contract!, old, !!old.scope));
        } catch (e) {
          if (!abort.signal.aborted && revision === draftRevision.current) setResolveError((e as Error).message);
        } finally { if (!abort.signal.aborted) setResolving(false); }
      })();
    }, 300);
    return () => { window.clearTimeout(timer); abort.abort(); };
  }, [cardOpen, selected, creating, model, baseUrl, secret, manualOverrides, resolveRevision]);

  const save = (activate = false) => void run(async () => {
    const previous = new Set(value.profiles?.map(p=>p.id));
    let result = creating
      ? await api<ModelSettings>("/settings/profiles", "POST", {...command(), ...(secret ? {api_key:secret} : {})})
      : await api<ModelSettings>("/settings", "PUT", {profile:command(), ...(activate ? {active_profile_id:selected} : {}), ...(secret ? {api_key:secret} : {})});
    const savedId = creating ? result.profiles?.find(p=>!previous.has(p.id))?.id : selected;
    onChange(result);
    if (activate && savedId && result.active_profile_id !== savedId) {
      try {
        result = await api<ModelSettings>("/settings", "PUT", {active_profile_id:savedId});
        onChange(result);
      } catch (e) {
        setSelected(savedId); setCreating(false); setCardOpen(false); setSecret("");
        throw new Error(`${t("配置已保存，但切换失败。")} ${(e as Error).message}`);
      }
    }
    setSelected(savedId || "");
    setCreating(false);
    setCardOpen(false);
    setSecret("");
    setMessage(t(previous.size === 0 ? "首个模型已保存并设为当前使用" : activate ? "设置已保存，已切换当前模型" : "设置已保存"));
  });

  if (page === "integrations") {
    return (
      <div>
        <nav className="settings-tabs" aria-label={t("接入设置")}>
          <Button variant="ghost" onClick={() => setPage("models")}>{t("模型 API")}</Button>
          <Button variant="primary">Integrations / MCP</Button>
        </nav>
        <McpIntegrations t={t} disabled={disabled} />
      </div>
    );
  }

  const verify = (target: string, kind: "parameter" | "capability" | "chat", value?: Scalar) => {
    if (!window.confirm(t("将发送 1 次模型请求，可能计费；不自动重试。是否继续？") + `\n${target}${value === undefined ? "" : ` = ${value}`}`)) return;
    void run(async () => {
      const revision = draftRevision.current;
      const result = await api<{status: string; message: string; discovery?: Discovery}>("/settings/verify", "POST", {
        profile: {...command(), ...(secret ? {api_key:secret} : {})}, target, kind, value, consent:true,
      });
      if (revision !== draftRevision.current) return;
      if (result.status === "failed") throw new Error(result.message);
      if (result.discovery?.contract) {
        setContract(result.discovery.contract);
        setUserSettings(adoptSelections(result.discovery.contract, userSettings, !!userSettings.scope));
      }
      setDebugMessage(result.message);
      onChange(await api<ModelSettings>("/settings"));
    });
  };

  const profiles = (value.profiles || []) as Profile[];
  const customized = modifiedParameters(contract, userSettings).length;
  const connectionFields = <>
    <label>{t("配置名称")}<input value={name} onChange={e => setName(e.target.value)} /></label>
    <label>API URL
      <input value={baseUrl} placeholder="https://api.example.com/v1" onChange={e => {
        setBaseUrl(e.target.value);
        setManualOverrides("{}");
        invalidate(true);
      }} />
    </label>
  </>;
  const confirmDelete = () => {
    const target = deleteTarget;
    if (!target || confirming) return;
    setConfirming(true);
    void run(async () => {
      const result = await api<ModelSettings>(
        `/settings/profiles/${encodeURIComponent(target.id)}`,
        "DELETE",
      );
      onChange(result);
      setDeleteTarget(null);
      if (!result.profiles?.length) beginCreate();
      else setSelected(result.active_profile_id || result.profiles[0]?.id || "");
      setMessage(t("配置已删除"));
    }).finally(() => setConfirming(false));
  };

  return (
    <div>
      <nav className="settings-tabs" aria-label={t("接入设置")}>
        <Button variant="primary">{t("模型 API")}</Button>
        <Button variant="ghost" onClick={() => setPage("integrations")}>Integrations / MCP</Button>
      </nav>
      {!cardOpen && (
        <section className="provider-list" aria-label={t("模型与连接")}>
          <div className="editor-heading"><h3>{t("模型与连接")}</h3>
            <Button variant="primary" disabled={busy || disabled} onClick={beginCreate}>{t("添加模型")}</Button></div>
          {profiles.length > 0 && (
            <ul className="provider-rows">
              {[...profiles].sort((a,b)=>Number(b.id === value.active_profile_id)-Number(a.id === value.active_profile_id)).map((item) => (
                <li key={item.id} className="provider-row" data-active={item.id === value.active_profile_id}>
                  <div className="provider-identity">
                    <span className="provider-title"><strong className="provider-name">{item.name}</strong>
                      {item.id === value.active_profile_id && <Badge>{t("当前使用")}</Badge>}
                      {!item.configured && <Badge tone="warn">{t("未配置密钥")}</Badge>}
                    </span>
                    <code className="muted">{item.model}</code>
                  </div>
                  <span className="provider-actions">
                    {item.id !== value.active_profile_id && <Button variant="primary" disabled={busy || disabled} onClick={()=>void run(async()=>{
                      onChange(await api<ModelSettings>("/settings", "PUT", {active_profile_id:item.id}));
                      setMessage(t("已切换当前模型") + ` · ${item.name}`);
                    })}>{t("使用此模型")}</Button>}
                    <Button
                      disabled={busy || disabled}
                      onClick={() => openProfile(item)}
                    >
                      {t("编辑")}
                    </Button>
                    {item.deletable !== false && (
                      <details className="provider-more"><summary>{t("更多")} ⋯</summary><Button
                        variant="danger"
                        disabled={busy || disabled}
                        onClick={() => setDeleteTarget(item)}
                      >
                        {t("删除")}
                      </Button></details>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {error && <p role="alert" className="error-text">{error}</p>}
          {message && <p role="status">{message}</p>}
        </section>
      )}
      {cardOpen && (
      <fieldset className="form settings-form" disabled={busy || disabled}>
        <div className="editor-heading">
          <strong>{t(creating ? "添加模型" : "编辑模型")}</strong>
          <Button variant="ghost" disabled={busy} onClick={closeCard}>{t("取消")}</Button>
        </div>
        <div className="model-source-switch" role="group" aria-label={t("模型服务来源")}>
          <Button variant="ghost" aria-pressed={mode === "catalog"} onClick={() => setMode("catalog")}>
            {t("预设服务")}
          </Button>
          <Button variant="ghost" aria-pressed={mode === "custom"} onClick={() => setMode("custom")}>
            {t("自定义 API")}
          </Button>
        </div>
        {mode === "catalog" && <label>
          {t("服务")}
          <select value={preset} onChange={e => applyPreset(e.target.value)}>
            <option value="">{t("请选择服务")}</option>
            {ENDPOINT_PRESETS.map(item => <option key={item.url} value={item.url}>{item.name}</option>)}
          </select>
        </label>}
        {mode === "custom" && connectionFields}
        <label>
          API Key
          <input
            type="password"
            autoComplete="new-password"
            placeholder={!creating && profile?.configured ? t("保留现有密钥") : ""}
            value={secret}
            onChange={(e) => { setSecret(e.target.value); draftRevision.current += 1; setContract({}); }}
          />
        </label>
        <div className="model-field">
          <label htmlFor="model-id">{t("模型")}</label>
          <div className="model-picker">
            <input id="model-id" value={model} onChange={e => changeModel(e.target.value)}
              list="compatible-models" placeholder={t("输入模型 ID")} />
            <Button disabled={!discoverReady} onClick={discover}>{t(listing ? "获取中…" : "获取列表")}</Button>
          </div>
          <datalist id="compatible-models">
            {discoveredModels.map(item => <option value={item} key={item} />)}
          </datalist>
        </div>
        {resolveError && <p role="alert">{t("无法读取模型配置")} <Button onClick={()=>retryResolve(n=>n+1)}>{t("重试")}</Button></p>}
        <details className="more-settings" ref={moreSettings}>
          <summary>{t("更多设置")}{customized > 0 && <Badge>{customized}</Badge>}</summary>
          <div className="more-settings-body">
            {mode === "catalog" && connectionFields}
            <ModelParameters contract={contract} settings={userSettings}
              disabled={busy || disabled || resolving || !!resolveError} t={t} onChange={next => {
                draftRevision.current += 1;
                setUserSettings(next);
              }} />
          </div>
        </details>
        <details className="model-debug">
          <summary>debug</summary>
          {resolveError && <p role="alert" className="error-text">{resolveError}</p>}
          {!creating && profile && <dl className="debug-checks">
            <div><dt>{t("已保存模型")}</dt><dd><code>{profile.model}</code></dd></div>
            <div><dt>API Key</dt><dd>{t(profile.configured ? "密钥已保存" : "未配置密钥")}</dd></div>
            {(["connection", "generation"] as const).map(kind => !!profile.checks?.[kind] && <div key={kind}>
              <dt>{t(kind === "connection" ? "最近连接检查通过" : "最近生成验证通过")}</dt>
              <dd><time dateTime={new Date(Number(profile.checks[kind])*1000).toISOString()}>{new Date(Number(profile.checks[kind])*1000).toLocaleString()}</time></dd>
            </div>)}
          </dl>}
          <div className="form-actions">
            <Button disabled={!ready || creating} onClick={()=>void run(async()=>{
              const result = await api<{status:string;message:string}>(`/settings/profiles/${encodeURIComponent(selected)}/test`, "POST",
                {profile:command(), ...(secret ? {api_key:secret} : {})});
              if (["connected", "unverified"].includes(result.status)) setDebugMessage(result.message || t("连接成功"));
              else throw new Error(result.message || t("连接失败"));
              onChange(await api<ModelSettings>("/settings"));
            })}>{t("测试连接")}</Button>
            {!creating && profile?.configured && <Button variant="ghost" onClick={()=>void run(async()=>{
              onChange(await api<ModelSettings>(`/settings/profiles/${encodeURIComponent(selected)}/key`, "DELETE"));
              setSecret(""); invalidate(); retryResolve(n=>n+1); setDebugMessage(t("密钥已清除"));
            })}>{t("清除已存密钥")}</Button>}
          </div>
        <details className="request-preview"><summary>{t("查看请求预览")}</summary>
          <select aria-label={t("预览场景")} value={scenario} onChange={e=>setScenario(e.target.value)}>
            <option value="text">{t("纯文本 · 非流式")}</option><option value="tools">{t("工具任务 · 流式")}</option><option value="structured">{t("程序格式 · JSON")}</option>
          </select>
          <Button disabled={!ready} onClick={()=>void run(async()=>setPreview(await api("/settings/preview", "POST", {profile:{...command(), ...(secret ? {api_key:secret} : {})}, scenario})))}>{t("生成预览（无模型请求）")}</Button>
          {preview && <RequestSummary summary={preview} t={t} />}
        </details>
          <label>{t("手动能力覆盖 JSON（仅影响当前配置）")}
            <textarea className="mono" aria-label={t("手动能力覆盖 JSON")} value={manualOverrides}
              onChange={e=>{setManualOverrides(e.target.value);draftRevision.current+=1;}} />
          </label>
          <ModelParameters contract={contract} settings={userSettings} disabled={busy || disabled || resolving} t={t} onChange={setUserSettings} onVerify={verify} diagnostics />
          <Button disabled={busy || disabled || !model.trim() || !contract.scope}
            onClick={()=>verify("chat","chat")}>{t("验证模型可生成（1 次请求）")}</Button>
          {contract.scope && <details><summary>{t("查看能力合同")}</summary><pre className="mono">{JSON.stringify(contract, null, 2)}</pre></details>}
          {debugMessage && <p role="status">{debugMessage}</p>}
        </details>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {message && <p role="status">{message}</p>}
        <div className="form-actions">
          <Button variant="primary" disabled={!ready} onClick={()=>save()}>{t("保存")}</Button>
          {(creating ? profiles.length > 0 : selected !== value.active_profile_id) && <Button disabled={!ready} onClick={()=>save(true)}>{t("保存并使用")}</Button>}
        </div>
      </fieldset>
      )}
      <Modal
        open={deleteTarget !== null}
        onOpenChange={(next) => { if (!next && !confirming) setDeleteTarget(null); }}
        title={t("删除配置")}
        description={t("删除此配置及其已存密钥？")}
      >
        {deleteTarget && (
          <>
            <p className="mono">{deleteTarget.name} · {deleteTarget.model}</p>
            {deleteTarget.id === value.active_profile_id && <p>{profiles.find(p=>p.id !== deleteTarget.id)
              ? `${t("删除后将使用")}: ${profiles.find(p=>p.id !== deleteTarget.id)!.name} · ${profiles.find(p=>p.id !== deleteTarget.id)!.model}`
              : t("删除后没有可用模型，需要添加模型才能发起模型任务。")}</p>}
            {error && <p role="alert" className="error-text">{error}</p>}
            <div className="form-actions">
              <Button disabled={confirming} onClick={() => setDeleteTarget(null)}>
                {t("取消")}
              </Button>
              <Button variant="danger" disabled={confirming} onClick={confirmDelete}>
                {t(confirming ? "处理中…" : "确认删除")}
              </Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
