import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Json, ModelSettings } from "../api/client";
import type { components } from "../api/generated";
import { Button, Badge } from "../components/ui";
import { ModelParameters } from "./ModelParameters";
import { ENDPOINT_PRESETS, adoptSelections, clearKnown } from "./modelParameters";
import type { CapabilityContract, UserModelSettings } from "./modelParameters";

type Profile = NonNullable<ModelSettings["profiles"]>[number] & {
  deletable?: boolean;
  generation_defaults?: Record<string, Json>;
  request_overrides?: Record<string, Json>;
  contract?: Record<string, Json>;
  user_settings?: Record<string, Json>;
};

type Discovery = {
  kind: "model_discovery_v1";
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

  const refresh = async () => {
    if (!projectId) {
      setStatus(null);
      return;
    }
    const value = await api<McpStatus>(
      `/integrations/mcp?project_id=${encodeURIComponent(projectId)}`,
    );
    setStatus(value);
  };

  useEffect(() => {
    let stopped = false;
    setStatus(null);
    setMessage("");
    setError("");
    setServiceCheck("unchecked");
    if (!projectId) return;
    const poll = () => {
      if (document.visibilityState === "hidden") return;
      api<McpStatus>(`/integrations/mcp?project_id=${encodeURIComponent(projectId)}`)
        .then((value) => !stopped && setStatus(value))
        .catch((e) => !stopped && setError((e as Error).message));
    };
    poll();
    const timer = window.setInterval(poll, 5000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [projectId]);

  const run = async (path: string) => {
    if (!projectId || busy || disabled) return;
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
      try { await refresh(); } catch (e) { setError((e as Error).message); }
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
  const [creating, setCreating] = useState(!value.profiles?.length),
    [busy, setBusy] = useState(false);
  const [name, setName] = useState(""),
    [model, setModel] = useState(""),
    [baseUrl, setBaseUrl] = useState("");
  const [secret, setSecret] = useState(""),
    [capabilities, setCapabilities] = useState<Record<string, boolean>>({});
  const [defaults, setDefaults] = useState("{}"),
    [overrides, setOverrides] = useState("{}");
  const [compatibilityText, setCompatibilityText] = useState("{}");
  const [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [contract, setContract] = useState<CapabilityContract>({});
  const [userSettings, setUserSettings] = useState<UserModelSettings>({});
  const draftRevision = useRef(0);
  const invalidate = (clearModels = false) => {
    draftRevision.current += 1;
    setContract({});
    setUserSettings({});
    if (clearModels) setDiscoveredModels([]);
  };
  const clearParameterValues = () => {
    try {
      const [nextDefaults, nextOverrides] = clearKnown(JSON.parse(defaults), JSON.parse(overrides), contract);
      setDefaults(JSON.stringify(nextDefaults, null, 2));
      setOverrides(JSON.stringify(nextOverrides, null, 2));
    } catch { /* Keep invalid advanced JSON for the user to repair. */ }
  };
  const changeModel = (next: string) => {
    if (next !== model) { invalidate(); setCapabilities({}); setCompatibilityText("{}"); clearParameterValues(); }
    setModel(next);
  };
  useEffect(() => () => { draftRevision.current += 1; }, []);
  const profile = value.profiles?.find((p) => p.id === selected) as
    | Profile
    | undefined;
  useEffect(() => {
    draftRevision.current += 1;
    if (creating) return;
    setName(profile?.name || "");
    setModel(profile?.model || "");
    setBaseUrl(profile?.base_url || "");
    setSecret("");
    setCapabilities(profile?.capabilities || {});
    setCompatibilityText(JSON.stringify(profile?.capabilities || {}, null, 2));
    setContract((profile?.contract || {}) as CapabilityContract);
    setUserSettings((profile?.user_settings || {}) as UserModelSettings);
    setDefaults(JSON.stringify(profile?.generation_defaults || {}, null, 2));
    setOverrides(JSON.stringify(profile?.request_overrides || {}, null, 2));
    setDeleting(false);
    setDiscoveredModels([]);
    // Background job refreshes must preserve in-progress fields and secrets.
  }, [selected, creating]);
  const parse = (text: string) => {
    const result = JSON.parse(text);
    if (!result || typeof result !== "object" || Array.isArray(result))
      throw new Error(t("配置数据必须是有效 JSON 对象。"));
    return result;
  };
  const command = () => ({
    ...(creating ? {} : { id: selected }),
    name,
    model,
    base_url: baseUrl,
    capabilities: parse(compatibilityText),
    generation_defaults: parse(defaults),
    request_overrides: parse(overrides),
    contract,
    user_settings: userSettings,
  });
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const ready =
    !busy && !disabled && !!name.trim() && !!model.trim() && !!baseUrl.trim();
  const discoverReady =
    !busy && !disabled && !!baseUrl.trim() && (!creating || !!secret.trim());
  const beginCreate = () => {
    setCreating(true);
    setName("");
    setModel("");
    setBaseUrl("");
    setSecret("");
    setCapabilities({}); setCompatibilityText("{}");
    invalidate(true);
    setDefaults("{}");
    setOverrides("{}");
    setDiscoveredModels([]);
    setError("");
    setMessage("");
    setDeleting(false);
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

  return (
    <div>
      <nav className="settings-tabs" aria-label={t("接入设置")}>
        <Button variant="primary">{t("模型 API")}</Button>
        <Button variant="ghost" onClick={() => setPage("integrations")}>Integrations / MCP</Button>
      </nav>
      <fieldset className="form settings-form" disabled={busy || disabled}>
        <div className="form-actions">
          <label>
            {t("选择模型配置")}
            <select
              disabled={busy}
              value={creating ? "" : selected}
              onChange={(e) => {
                setCreating(false);
                setSelected(e.target.value);
                setError("");
                setMessage("");
              }}
            >
              {creating && <option value="">{t("新建配置")}</option>}
              {value.profiles?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            disabled={busy || disabled}
            onClick={beginCreate}
          >
            {t("新建配置")}
          </Button>
        </div>
        <label>
          {t("兼容服务预设")}
          <select value="" onChange={event => {
            const preset = ENDPOINT_PRESETS.find(item => item.url === event.target.value);
            if (preset) { beginCreate(); setName(preset.name); setBaseUrl(preset.url); }
          }}>
            <option value="">{t("自定义 OpenAI-compatible 服务")}</option>
            {ENDPOINT_PRESETS.map(item => <option key={item.url} value={item.url}>{item.name}</option>)}
          </select>
        </label>
        <p className="muted">{t("预设只填写地址，不绑定模型或参数。可修改为区域、工作区或网关提供的兼容地址。Claude 原生 Messages 功能不等同于兼容接口功能。")}</p>
        <label>
          {t("配置名称")}
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          API URL
          <input
            value={baseUrl}
            onChange={(e) => { setBaseUrl(e.target.value); invalidate(true); setCapabilities({}); setCompatibilityText("{}"); clearParameterValues(); }}
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
          <Button
            disabled={!discoverReady}
            onClick={() =>
              void run(async () => {
                const revision = draftRevision.current;
                const draft = {
                  ...(creating ? {} : { id: selected }),
                  name: name.trim() || "Custom API",
                  model: model.trim() || "__discover__",
                  base_url: baseUrl,
                  capabilities,
                  generation_defaults: parse(defaults),
                  request_overrides: parse(overrides),
                  // Re-detection must not validate against a stale contract.
                  contract,
                  user_settings: userSettings,
                  ...(secret ? { api_key: secret } : {}),
                };
                const result = await api<{ status: string; message: string; discovery?: Discovery }>(
                  "/settings/detect", "POST", { profile: draft },
                );
                if (revision !== draftRevision.current) return;
                if (result.status !== "connected") throw new Error(result.message || t("连接失败"));
                const discovery = result.discovery || parseDiscovery(result.message);
                if (!discovery) { setMessage(result.message || t("连接成功")); return; }
                setDiscoveredModels(discovery.models || []);
                if (!model && discovery.recommended_model) setModel(discovery.recommended_model);
                const nextContract = discovery.contract || {};
                setContract(nextContract);
                setUserSettings(adoptSelections(nextContract, userSettings, parse(defaults), parse(overrides)));
                setMessage(discovery.note || t("模型列表与能力检测完成"));
              })
            }
          >
            {t(busy ? "检测中…" : "自动获取模型 + 检测能力")}
          </Button>
        </div>
        <label>
          {t("模型")}
          <input value={model} onChange={(e) => changeModel(e.target.value)}
            list="compatible-models" placeholder={t("可先自动获取模型")} />
          <datalist id="compatible-models">
            {discoveredModels.map(item => <option value={item} key={item} />)}
          </datalist>
        </label>
        <p className="muted">{t("检测会发送少量测试请求，可能产生 API 费用；仅使用固定测试文本，不发送工程内容。")}</p>
        <ModelParameters contract={contract} settings={userSettings} defaults={defaults} overrides={overrides}
          disabled={busy || disabled} t={t} onChange={(next) => {
            draftRevision.current += 1;
            setUserSettings(next);
          }} />
        <details>
          <summary>{t("高级设置")}</summary>
          <p className="muted">
            {t("如供应商要求特定参数，可在此调整模型能力、生成参数和请求覆盖参数。")}
          </p>
          <label>
            {t("旧版协议兼容选项")}
            <textarea className="mono" aria-label={t("旧版协议兼容选项")}
              value={compatibilityText}
              onChange={e => { setCompatibilityText(e.target.value); invalidate();
                try { const next = parse(e.target.value);
                  if (Object.values(next).every(v => typeof v === "boolean")) setCapabilities(next);
                } catch { /* Keep invalid JSON editable; the save command validates it. */ }
              }} />
          </label>
          {contract.scope && <details><summary>{t("查看能力合同")}</summary><pre className="mono">{JSON.stringify(contract, null, 2)}</pre></details>}
          <label>
            {t("生成默认参数")}
            <textarea
              className="mono"
              aria-label={t("生成默认参数")}
              value={defaults}
              onChange={(e) => { setDefaults(e.target.value); invalidate(); }}
            />
          </label>
          <p className="muted">temperature · top_p · max_tokens</p>
          <label>
            {t("请求覆盖参数")}
            <textarea
              className="mono"
              aria-label={t("请求覆盖参数")}
              value={overrides}
              onChange={(e) => { setOverrides(e.target.value); invalidate(); }}
            />
          </label>
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
                setSelected(
                  creating
                    ? result.profiles?.find((p) => !previous.has(p.id))?.id ||
                        result.active_profile_id ||
                        ""
                    : selected,
                );
                setCreating(false);
                setSecret("");
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
                if (result.status === "connected") {
                  const discovery = parseDiscovery(result.message);
                  setMessage(discovery?.note || t("连接成功"));
                } else setError(result.message || t("连接失败"));
              })
            }
          >
            {t(busy ? "处理中…" : "测试连接")}
          </Button>
        </div>
        {!creating && (
          <div className="settings-danger">
            <Button
              variant="ghost"
              disabled={busy || disabled || !profile?.configured}
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
            {profile?.deletable && (
              <Button
                variant="ghost"
                disabled={busy || disabled}
                onClick={() => setDeleting((v) => !v)}
              >
                {t("删除配置")}
              </Button>
            )}
            {deleting && (
              <div className="notice">
                <p>
                  {t("删除此配置及其已存密钥？")} {profile?.name}
                </p>
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const result = await api<ModelSettings>(
                        `/settings/profiles/${encodeURIComponent(selected)}`,
                        "DELETE",
                      );
                      onChange(result);
                      setSelected(
                        result.active_profile_id ||
                          result.profiles?.[0]?.id ||
                          "",
                      );
                      if (!result.profiles?.length) beginCreate();
                      setDeleting(false);
                      setMessage(t("配置已删除"));
                    })
                  }
                >
                  {t("确认删除")}
                </Button>
              </div>
            )}
          </div>
        )}
      </fieldset>
    </div>
  );
}
