import { useEffect, useRef, useState } from "react";
import { usePolling } from "../lifecycle/usePolling";
import { sameValue } from "../lifecycle/requests";
import { api } from "../api/client";
import type { Json, ModelSettings } from "../api/client";
import type { components } from "../api/generated";
import { Button, Badge, Modal } from "../components/ui";
import { ModelParameters } from "./ModelParameterControls";
import { ENDPOINT_PRESETS, adoptSelections } from "./modelParameters";
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
        <p>
          {t("打开工程并点击“连接 Codex”，检查 MCP 服务并保存连接配置。使用期间请保持工作台运行。")}
        </p>
        <p>{t("支持本机 Codex App，无需安装 Codex CLI。配置完成后，请重启 Codex App 并创建新任务。")}</p>
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
        <p className="muted">{t("服务检查仅确认工作台可访问。这里的客户端记录来自成功的工程工具调用，不包含工具列表查询。")}</p>
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

      <div className="notice">
        <strong>{t("开始工程任务")}</strong>
        <p>{t("在 Codex 中输入任务，例如：“用 gxworks 为当前工程生成起保停程序：X0 启动，X1 停止，Y0 控制电机。”生成后在工作台检查程序、变更摘要和待审批操作。")}</p>
      </div>

      <details>
        <summary>{t("高级 / 其他 MCP 客户端")}</summary>
        <p className="muted">{t("若尚无工具调用记录，请检查 Codex 中的 gxworks MCP 连接，并在新任务中明确要求使用 gxworks 工具。")}</p>
        <label>
          Service URL
          <input readOnly value={status?.service_url || window.location.origin} />
        </label>
        <Badge tone={status?.codex_cli_available ? "good" : "neutral"}>
          {t(status?.codex_cli_available ? "Codex CLI 已检测到（可选）" : "Codex CLI 未检测到（可选）")}
        </Badge>
        <p className="muted">
          {t("将下方配置添加到 Claude、Cursor 等 MCP 客户端。点击“测试 MCP 连接”可绑定当前工程；配置中的 gxworks-agent-mcp 需可由客户端启动。")}
        </p>
        <pre className="mono">{manualConfig}</pre>
        <Button onClick={() => void navigator.clipboard.writeText(manualConfig)}>
          {t("复制高级配置")}
        </Button>
        <p className="muted">
          {t("CI 或无界面环境未启动 Web 服务时，使用：gxworks-agent-mcp --standalone --workspace <workspace> --project <project-id>。")}
        </p>
      </details>
    </div>
  );
}

export function Settings({
  value,
  t,
  onChange,
  disabled,
}: {
  value: ModelSettings;
  t: (s: string) => string;
  onChange: (settings: ModelSettings) => void;
  disabled: boolean;
}) {
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
  // How the address is chosen. DSH splits its add card the same way: adopt a
  // service the tool already knows, or declare a custom one.
  const [mode, setMode] = useState<"catalog" | "custom">("catalog");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Profile | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [contract, setContract] = useState<CapabilityContract>({});
  const [userSettings, setUserSettings] = useState<UserModelSettings>({});
  const [scanMode, setScanMode] = useState<DiscoveryMode | null>(null);
  const [scanSeconds, setScanSeconds] = useState(0);
  const [manualOverrides, setManualOverrides] = useState("{}");
  useEffect(() => {
    if (!scanMode) return;
    const started = Date.now();
    setScanSeconds(0);
    const timer = window.setInterval(() => setScanSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [scanMode]);
  const draftRevision = useRef(0);
  const invalidate = (clearModels = false) => {
    draftRevision.current += 1;
    setContract({});
    setUserSettings({});
    setMessage("");
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
  const presetSource = ENDPOINT_PRESETS.find(item => item.url === preset)?.source;
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
      invalid?.reportValidity();
      throw new Error(t("请先修正参数类型、范围或关联条件冲突。"));
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
    !busy && !disabled && !!name.trim() && !!model.trim() && !!baseUrl.trim();
  const discoverReady =
    !busy && !disabled && !!baseUrl.trim() && (!creating || !!secret.trim());
  const discover = (mode: DiscoveryMode) => void run(async () => {
    const revision = draftRevision.current;
    setScanMode(mode);
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
        mode === "list" ? "/settings/detect" : "/settings/resolve", "POST", { profile: draft, mode },
      );
      if (revision !== draftRevision.current) return;
      if (!["connected", "resolved"].includes(result.status)) throw new Error(result.message || t("连接失败"));
      const discovery = result.discovery || parseDiscovery(result.message);
      if (!discovery) { setMessage(result.message || t("连接成功")); return; }
      setDiscoveredModels(discovery.models || []);
      // Listing is read-only for the selected model and its current contract.
      if (discovery.contract && discovery.mode !== "list") {
        setContract(discovery.contract);
        setUserSettings(adoptSelections(discovery.contract, userSettings));
      }
      const timing = discovery.elapsed_ms == null ? "" : ` · ${t("耗时")} ${(discovery.elapsed_ms / 1000).toFixed(1)} s`;
      setMessage((discovery.note || t("能力配置已加载（无生成请求）")) + timing +
        (discovery.budget_exhausted ? ` · ${t("已达到检测预算，未完成项保留为未知或部分扫描。")}` : ""));
    } catch (e) {
      if (revision === draftRevision.current) throw e;
    } finally {
      setScanMode(null);
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
  };
  const openProfile = (target: Profile) => {
    setCardOpen(true);
    setCreating(false);
    setSelected(target.id);
    loadFields(target);
    setError("");
    setMessage("");
  };
  const closeCard = () => {
    draftRevision.current += 1;
    setCardOpen(false);
    setCreating(false);
    setError("");
    setMessage("");
    setScanMode(null);
  };

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
        setUserSettings(adoptSelections(result.discovery.contract, userSettings));
      }
      setMessage(result.message);
    });
  };

  const profiles = (value.profiles || []) as Profile[];
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
        <section className="provider-list" aria-label={t("模型服务")}>
          {profiles.length > 0 && (
            <ul className="provider-rows">
              {profiles.map((item) => (
                <li key={item.id} className="provider-row">
                  <span className="provider-identity">
                    <span className="provider-name">{item.name}</span>
                    <Badge tone={item.configured ? "good" : "warn"}>
                      {t(item.configured ? "已配置密钥" : "未配置密钥")}
                    </Badge>
                  </span>
                  <span className="provider-actions">
                    <Button
                      disabled={busy || disabled}
                      onClick={() => openProfile(item)}
                    >
                      {t("编辑")}
                    </Button>
                    {item.deletable !== false && (
                      <Button
                        variant="danger"
                        disabled={busy || disabled}
                        onClick={() => setDeleteTarget(item)}
                      >
                        {t("删除")}
                      </Button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {error && <p role="alert" className="error-text">{error}</p>}
          {message && <p role="status">{message}</p>}
          <Button
            className="provider-add"
            variant="primary"
            disabled={busy || disabled}
            onClick={beginCreate}
          >
            {t("新增配置")}
          </Button>
        </section>
      )}
      {cardOpen && (
      <fieldset className="form settings-form" disabled={busy || disabled}>
        <div className="editor-heading">
          <strong>{t(creating ? "新建配置" : "编辑配置")}</strong>
          <Button variant="ghost" disabled={busy} onClick={closeCard}>{t("取消")}</Button>
        </div>
        <nav className="mode-switch" aria-label={t("模型服务来源")}>
          <Button
            variant={mode === "catalog" ? "primary" : "ghost"}
            disabled={busy}
            onClick={() => setMode("catalog")}
          >
            {t("选择已知服务")}
          </Button>
          <Button
            variant={mode === "custom" ? "primary" : "ghost"}
            disabled={busy}
            onClick={() => setMode("custom")}
          >
            {t("自定义模型 API")}
          </Button>
        </nav>
        {mode === "catalog" ? (
          <>
            <label>
              {t("服务")}
              <select value={preset} onChange={event => applyPreset(event.target.value)}>
                <option value="">{t("请选择服务")}</option>
                {ENDPOINT_PRESETS.map(item =>
                  <option key={item.url} value={item.url}>{item.name}</option>)}
              </select>
            </label>
            <p className="muted">{t(
              presetSource === "local"
                ? "该端点有本地能力合同；加载能力配置后可直接使用已声明的参数。"
                : presetSource === "dsh"
                  ? "该端点使用通用模板；是否支持某项参数在验证前保持未确认。"
                  : "选择服务后自动填写地址。地址不绑定模型 ID 或调优值，可以在下方改成区域、工作区或网关地址。",
            )}</p>
          </>
        ) : (
          <p className="muted">{t("手动填写服务地址。未收录能力合同的地址使用通用模板，是否支持某项参数在验证前保持未确认。")}</p>
        )}
        <label>
          {t("配置名称")}
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          API URL
          <input
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setPreset(presetFor(e.target.value));
              setManualOverrides("{}");
              invalidate(true);
            }}
            placeholder="https://api.example.com/v1"
          />
        </label>
        <label>
          API Key{" "}
          <Badge tone={!creating && profile?.configured ? "good" : "warn"}>
            {t(!creating && profile?.configured ? "已配置密钥" : "未配置密钥")}
          </Badge>
          <input
            type="password"
            autoComplete="new-password"
            placeholder={t("保留现有密钥")}
            value={secret}
            onChange={(e) => { setSecret(e.target.value); invalidate(); }}
          />
        </label>
        <p className="muted">{t("API Key 用于连接模型服务；编辑已有配置时，留空可保留已保存的密钥。")}</p>
        <div className="form-actions">
          <Button disabled={!discoverReady} onClick={() => discover("list")}>
            {t("获取模型列表")}
          </Button>
          <Button disabled={busy || disabled || !baseUrl.trim() || !model.trim()} onClick={() => discover("resolve")}>
            {t("加载能力配置（零生成请求）")}
          </Button>
        </div>
        <p className="muted">{t("已知模型使用本地 JSON 预设；未知模型使用可编辑通用模板。加载配置、切换参数和保存都不调用模型。")}</p>
        {scanMode && <p role="status" aria-live="polite">
          {t(scanMode === "list" ? "正在获取模型列表…" : "正在解析本地配置…")}
          {` ${scanSeconds} s`}
        </p>}
        <label>
          {t("模型")}
          <input value={model} onChange={(e) => changeModel(e.target.value)}
            list="compatible-models" placeholder={t("可先获取模型列表，也可手动填写模型 ID")} />
          <datalist id="compatible-models">
            {discoveredModels.map(item => <option value={item} key={item} />)}
          </datalist>
        </label>
        <p className="muted">{t("仅“验证”按钮会发送固定测试文本并可能计费；不发送工程内容，不进行全档位扫描。")}</p>
        <ModelParameters contract={contract} settings={userSettings}
          disabled={busy || disabled} t={t} onVerify={verify} onChange={(next) => {
            draftRevision.current += 1;
            setUserSettings(next);
          }} />
        <details>
          <summary>{t("高级设置")}</summary>
          <label>{t("手动能力覆盖 JSON（仅影响当前配置）")}
            <textarea className="mono" aria-label={t("手动能力覆盖 JSON")} value={manualOverrides}
              onChange={e=>{setManualOverrides(e.target.value);draftRevision.current+=1;setContract({});}} />
          </label>
          <p className="muted">{t("格式：parameters / capabilities / constraints。修改后重新加载能力配置。不能覆盖消息、工具定义、密钥或服务地址。")}</p>
          <Button disabled={busy || disabled || !model.trim() || !contract.scope}
            onClick={()=>verify("chat","chat")}>{t("验证模型可生成（1 次请求）")}</Button>
          <p className="muted">
            {t("供应商特有的可调参数应声明在能力合同中；这里仅保留显式 capability override。")}
          </p>
          {contract.scope && <details><summary>{t("查看能力合同")}</summary><pre className="mono">{JSON.stringify(contract, null, 2)}</pre></details>}
        </details>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {message && <p role="status">{message}</p>}
        <div className="form-actions">
          <Button
            variant="primary"
            disabled={!ready}
            onClick={() =>
              void run(async () => {
                const previous = new Set(value.profiles?.map((p) => p.id));
                const result = creating
                  ? await api<ModelSettings>("/settings/profiles", "POST", {
                      ...command(),
                      ...(secret ? { api_key: secret } : {}),
                    })
                  : await api<ModelSettings>("/settings", "PUT", {
                      active_profile_id: selected,
                      profile: command(),
                      ...(secret ? { api_key: secret } : {}),
                    });
                onChange(result);
                const savedId = creating
                  ? result.profiles?.find((p) => !previous.has(p.id))?.id ||
                    result.active_profile_id ||
                    ""
                  : selected;
                setSelected(savedId);
                setCreating(false);
                setSecret("");
                const saved = result.profiles?.find((p) => p.id === savedId) as
                  | Profile
                  | undefined;
                loadFields(saved);
                setMessage(t("设置已保存"));
              })
            }
          >
            {t(creating ? "创建配置" : "保存并使用")}
          </Button>
          <Button
            disabled={!ready || creating}
            onClick={() =>
              void run(async () => {
                const result = await api<{ status: string; message: string }>(
                  `/settings/profiles/${encodeURIComponent(selected)}/test`,
                  "POST",
                  { profile: command(), ...(secret ? { api_key: secret } : {}) },
                );
                if (["connected", "unverified"].includes(result.status))
                  setMessage(result.message || t("连接成功"));
                else setError(result.message || t("连接失败"));
              })
            }
          >
            {t(busy ? "处理中…" : "测试连接")}
          </Button>
        </div>
        {!creating && profile?.configured && (
          <div className="form-actions">
            <Button
              variant="ghost"
              disabled={busy || disabled}
              onClick={() =>
                void run(async () => {
                  onChange(
                    await api<ModelSettings>(
                      `/settings/profiles/${encodeURIComponent(selected)}/key`,
                      "DELETE",
                    ),
                  );
                  setSecret("");
                  invalidate();
                  setMessage(t("密钥已清除"));
                })
              }
            >
              {t("清除已存密钥")}
            </Button>
          </div>
        )}
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
            <p className="mono">{deleteTarget.name}</p>
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
