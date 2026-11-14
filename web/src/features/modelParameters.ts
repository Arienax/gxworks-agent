// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import type { Json } from "../api/client";

/** Model-neutral schema and deterministic selection helpers. No model-name tests. */
export type Scalar = string | number | boolean;
export const REASONING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
export type ReasoningLevel = typeof REASONING_LEVELS[number];
export type ReasoningLevels = Partial<Record<ReasoningLevel, string | null>>;
export const DEFAULT_REASONING_LEVELS: ReasoningLevels = { off: null, low: "low", medium: "medium", high: "high" };
export type Options = Record<string, unknown>;
export type Conditions = { requires?: Record<string, (Scalar | null)[]>; conflicts_with?: string[] };
export type Scope = { endpoint: string; model: string; context: string; binding: string };
export type Domain = { values?: Scalar[]; minimum?: number; maximum?: number; exclusive_minimum?: number; exclusive_maximum?: number; step?: number; multiple_of?: number; source?: string; enforcement?: "hard" | "hint" };
export type Descriptor = Conditions & {
  type: "enum" | "number" | "integer" | "boolean" | "string";
  status: "supported" | "conditional" | "accepted" | "unknown" | "unsupported" | "fixed";
  source: "probe" | "metadata" | "manual" | "legacy" | "catalog" | "generic" | "observation";
  domain?: Domain; evidence?: { accepted_values?: Scalar[]; rejected_values?: Scalar[]; observations?: {outcome: string; value: Scalar; context: string; at: number}[] };
  ui_hint?: {minimum?: number; maximum?: number; step?: number; suggestions?: Scalar[]; advanced?: boolean; reasoning_levels?: ReasoningLevels};
  values?: Scalar[];
  minimum?: number; maximum?: number; step?: number;
  wire_location?: "body" | "extra_body"; wire_path?: string[];
  default_mode?: "omit" | "inherit"; label?: string;
  scan?: "partial" | "complete";
};
export type Capability = Conditions & {
  status: "supported" | "unsupported" | "unknown" | "conditional";
  source: Descriptor["source"]; modes?: string[]; value?: Scalar; evidence?: Descriptor["evidence"];
};
export type CapabilityContract = {
  schema_version?: 2 | 3; scope?: Scope;
  parameters?: Record<string, Descriptor>;
  capabilities?: Record<string, Capability>;
  constraints?: Record<string, Conditions>;
};
export type Selection = ({ mode: "omit" | "inherit" } | { mode: "value"; value: Scalar }) & { level?: ReasoningLevel };
export type UserModelSettings = { scope?: Scope; parameters?: Record<string, Selection>; request_parameters?: Record<string, Json> };
export type RequestParameterRow = { name: string; value: string };

/**
 * Endpoint presets fill a base URL only; they never carry model names or tuning
 * defaults. `source` says where the endpoint came from: `local` is an endpoint a
 * bundled capability contract already matches, `dsh` is one whose vendor also
 * appears in the DeepSeek Harness provider catalog.
 *
 * These are address shortcuts. Generation requires a Responses-compatible
 * endpoint reachable with the saved API key. Endpoints with a caller-specific
 * resource ID are configured under Custom API.
 */
export const ENDPOINT_PRESETS = [
  { name: "OpenAI", url: "https://api.openai.com/v1", source: "local" },
  { name: "DeepSeek", url: "https://api.deepseek.com", source: "local" },
  { name: "智谱 GLM（开放平台）", url: "https://open.bigmodel.cn/api/paas/v4", source: "local" },
  { name: "通义千问（北京）", url: "https://dashscope.aliyuncs.com/compatible-mode/v1", source: "local" },
  { name: "通义千问（新加坡）", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", source: "local" },
  { name: "Kimi / Moonshot（国际）", url: "https://api.moonshot.ai/v1", source: "local" },
  { name: "Kimi / Moonshot（中国）", url: "https://api.moonshot.cn/v1", source: "local" },
  { name: "Claude / Anthropic（OpenAI 兼容层）", url: "https://api.anthropic.com/v1", source: "local" },
  { name: "Gemini（OpenAI 兼容端点）", url: "https://generativelanguage.googleapis.com/v1beta/openai", source: "local" },
  { name: "OpenRouter", url: "https://openrouter.ai/api/v1", source: "dsh" },
  { name: "Groq", url: "https://api.groq.com/openai/v1", source: "dsh" },
  { name: "Together", url: "https://api.together.ai/v1", source: "dsh" },
  { name: "Fireworks", url: "https://api.fireworks.ai/inference/v1", source: "dsh" },
  { name: "Cerebras", url: "https://api.cerebras.ai/v1", source: "dsh" },
  { name: "Baseten", url: "https://inference.baseten.co/v1", source: "dsh" },
  { name: "NVIDIA NIM", url: "https://integrate.api.nvidia.com/v1", source: "dsh" },
  { name: "Hugging Face Router", url: "https://router.huggingface.co/v1", source: "dsh" },
  { name: "Ant Ling", url: "https://api.ant-ling.com/v1", source: "dsh" },
  { name: "Mistral", url: "https://api.mistral.ai/v1", source: "dsh" },
  { name: "MiniMax（国际）", url: "https://api.minimax.io/v1", source: "dsh" },
  { name: "MiniMax（中国）", url: "https://api.minimaxi.com/v1", source: "dsh" },
  { name: "Vercel AI Gateway", url: "https://ai-gateway.vercel.sh/v1", source: "dsh" },
  { name: "OpenCode Zen", url: "https://opencode.ai/zen/v1", source: "dsh" },
  { name: "小米 MiMo", url: "https://api.xiaomimimo.com/v1", source: "dsh" },
  { name: "Z.AI Coding", url: "https://api.z.ai/api/coding/paas/v4", source: "dsh" },
  { name: "Z.AI Coding（中国）", url: "https://open.bigmodel.cn/api/coding/paas/v4", source: "dsh" },
  { name: "通义千问 Token Plan（新加坡）", url: "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", source: "dsh" },
  { name: "通义千问 Token Plan（北京）", url: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", source: "dsh" },
  { name: "小米 Token Plan（AMS）", url: "https://token-plan-ams.xiaomimimo.com/v1", source: "dsh" },
  { name: "小米 Token Plan（中国）", url: "https://token-plan-cn.xiaomimimo.com/v1", source: "dsh" },
  { name: "小米 Token Plan（新加坡）", url: "https://token-plan-sgp.xiaomimimo.com/v1", source: "dsh" },
] as const;

/** Legacy probe samples are evidence, never a domain, even before migration. */
export function hardDomain(desc: Descriptor): Domain {
  if (desc.domain) return desc.domain.enforcement === "hard" ? desc.domain : {};
  return ["probe", "generic", "observation"].includes(desc.source) ? {} : desc;
}
export function controlValues(desc?: Descriptor): Scalar[] {
  if (!desc) return [];
  const domain = hardDomain(desc);
  if (domain.values) return domain.values;
  if (desc.type === "boolean") return [false, true];
  return desc.type === "enum" ? desc.ui_hint?.suggestions || [] : [];
}
export function sliderRange(desc: Descriptor): {minimum: number; maximum: number; step: number} | null {
  if (!["number", "integer"].includes(desc.type) || hardDomain(desc).values) return null;
  const domain = hardDomain(desc), hint = desc.ui_hint || {};
  const step = domain.multiple_of ?? domain.step ?? hint.step ?? (desc.type === "integer" ? 1 : .01);
  const anchor = domain.multiple_of != null ? 0 : domain.minimum ?? 0;
  let minimum = domain.minimum ?? hint.minimum ?? domain.exclusive_minimum;
  let maximum = domain.maximum ?? hint.maximum ?? domain.exclusive_maximum;
  if (minimum == null || maximum == null || !Number.isFinite(minimum + maximum + step) || step <= 0) return null;
  // HTML range grids start at min. Align that min with the declared wire grid,
  // and never let a presentation hint reintroduce an excluded endpoint.
  minimum = anchor + Math.ceil((minimum-anchor)/step - 1e-9)*step;
  maximum = anchor + Math.floor((maximum-anchor)/step + 1e-9)*step;
  if (domain.exclusive_minimum != null)
    minimum = Math.max(minimum, anchor + (Math.floor((domain.exclusive_minimum-anchor)/step + 1e-9)+1)*step);
  if (domain.exclusive_maximum != null)
    maximum = Math.min(maximum, anchor + (Math.ceil((domain.exclusive_maximum-anchor)/step - 1e-9)-1)*step);
  minimum = Number(minimum.toPrecision(12)); maximum = Number(maximum.toPrecision(12));
  return maximum > minimum ? {minimum, maximum, step} : null;
}
export function validValue(desc: Descriptor, value: unknown): value is Scalar {
  if (value == null || !["string", "number", "boolean"].includes(typeof value)) return false;
  if (typeof value === "number" && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) return false;
  if (typeof value === "string" && value.length > 256) return false;
  const domain = hardDomain(desc);
  if (domain.values && !domain.values.some(v => v === value)) return false;
  if (desc.type === "boolean") return typeof value === "boolean";
  if (desc.type === "string" || desc.type === "enum") return typeof value === "string" || desc.type === "enum" && !!domain.values;
  if (desc.type === "number" || desc.type === "integer") {
    if (typeof value !== "number" || (desc.type === "integer" && !Number.isSafeInteger(value))) return false;
    if (domain.minimum != null && value < domain.minimum || domain.maximum != null && value > domain.maximum) return false;
    if (domain.exclusive_minimum != null && value <= domain.exclusive_minimum || domain.exclusive_maximum != null && value >= domain.exclusive_maximum) return false;
    const step = domain.multiple_of ?? domain.step;
    if (step != null) {
      const units = (value - (domain.multiple_of != null ? 0 : domain.minimum || 0)) / step;
      if (Math.abs(units - Math.round(units)) > 1e-7) return false;
    }
  }
  return true;
}
export function selectedValues(contract: CapabilityContract, settings: UserModelSettings): Options {
  const result: Options = {};
  for (const [name, descriptor] of Object.entries(contract.parameters || {})) {
    const selection = settings.parameters?.[name];
    const custom = customParameterValue(settings, name, descriptor);
    result[name] = custom !== undefined ? custom : selection?.mode === "value" ? selection.value : null;
  }
  return result;
}

function customParameterValue(settings: UserModelSettings, name: string, desc: Descriptor): Json | undefined {
  const parameters = settings.request_parameters;
  let value: Json | undefined = parameters;
  for (const key of desc.wire_path ?? [name]) {
    if (value === null) return null;
    value = value && typeof value === "object" && !Array.isArray(value) && Object.hasOwn(value, key) ? value[key] : undefined;
  }
  return value !== undefined ? value : parameters && Object.hasOwn(parameters, name) ? parameters[name] : undefined;
}
export function conditionsMatch(condition: Conditions | undefined, values: Options): boolean {
  return Object.entries(condition?.requires || {}).every(([name, allowed]) => allowed.some(v => v === (values[name] ?? null))) &&
    !(condition?.conflicts_with || []).some(name => values[name] != null);
}
export function parameterEnabled(name: string, contract: CapabilityContract, values: Options, settings?: UserModelSettings): boolean {
  const descriptor = contract.parameters?.[name];
  if (!descriptor) return false;
  // Conditions involving request flags are checked again at runtime. An editor
  // cannot know whether a future workflow is streaming or includes tools.
  const explicit = (key: string) => {
    const dependency = contract.parameters?.[key];
    return !!dependency && (!settings || (settings.parameters?.[key]?.mode || "inherit") !== "inherit" ||
      customParameterValue(settings, key, dependency) !== undefined);
  };
  const known = (rule?: Conditions): Conditions => ({
    requires: Object.fromEntries(Object.entries(rule?.requires || {}).filter(([key]) => explicit(key))),
    conflicts_with: (rule?.conflicts_with || []).filter(explicit),
  });
  return conditionsMatch(known(descriptor), values) && conditionsMatch(known(contract.constraints?.[name]), values);
}
export function adoptSelections(contract: CapabilityContract, previous: UserModelSettings, preserveInherited = false): UserModelSettings {
  const parameters: Record<string, Selection> = {};
  for (const [name, desc] of Object.entries(contract.parameters || {})) {
    const old = previous.parameters?.[name];
    if (old) parameters[name] = old;
    // A saved omission of the selection inherits at runtime. Merely opening
    // the editor must not materialize a new explicit omit over that inheritance.
    else if (preserveInherited) continue;
    else if (["fixed", "unsupported"].includes(desc.status)) parameters[name] = { mode: "omit" };
    else parameters[name] = { mode: desc.default_mode || "omit" };
  }
  return reconcileSelections(contract, { ...previous, parameters });
}
export function reconcileSelections(contract: CapabilityContract, settings: UserModelSettings): UserModelSettings {
  // Keep an explicit invalid value visible. Save/request validation must explain
  // the conflict, not silently remove the user's settings after metadata changes.
  return { ...settings, scope: contract.scope, parameters: Object.fromEntries(Object.entries(settings.parameters || {})
    .filter(([name]) => !!contract.parameters?.[name])) };
}
export function changeSelection(contract: CapabilityContract, settings: UserModelSettings, name: string,
                                selection: Selection): UserModelSettings {
  const desc = contract.parameters?.[name];
  if (!desc || selection.mode === "value" && !validValue(desc, selection.value)) throw new Error("Invalid parameter selection");
  return { ...settings, scope: contract.scope, parameters: { ...settings.parameters, [name]: selection } };
}

// Both normal actions are zero-generation; verification has a separate API.
export type DiscoveryMode = "list" | "resolve";

/** Presentation only: no inference of effective request values. */
export function parameterTitle(name: string, desc: Descriptor): string {
  return ({reasoning_effort:"思考强度", temperature:"输出随机性", top_p:"采样范围",
    max_tokens:"生成长度限制", max_completion_tokens:"生成长度限制",
    frequency_penalty:"重复频率调整", presence_penalty:"话题重复调整"} as Record<string,string>)[name] || desc.label || name;
}
export function selectionFor(name: string, settings: UserModelSettings): Selection {
  return settings.parameters?.[name] || {mode:"inherit"};
}

function declaredChoices(desc?: Descriptor): Scalar[] {
  if (!desc || !["supported", "conditional"].includes(desc.status)) return [];
  return hardDomain(desc).values ?? (desc.type === "boolean" ? [true, false] : []);
}

export function isReasoningControl(name: string): boolean {
  return name === "reasoning_effort" || name === "thinking_mode" || name === "enable_thinking";
}

export function requestParameterRows(settings: UserModelSettings): RequestParameterRow[] {
  const values = { ...Object.fromEntries(Object.entries(settings.parameters || {}).flatMap(([name, choice]) =>
    !isReasoningControl(name) && choice.mode === "value" ? [[name, choice.value]] : [])), ...settings.request_parameters };
  return Object.entries(values).map(([name, value]) => ({ name, value: JSON.stringify(value) }));
}

export function parseRequestParameterRows(rows: readonly RequestParameterRow[], contract?: CapabilityContract): {
  parameters?: Record<string, Json>; error?: string; field?: string;
} {
  const entries: [string, Json][] = [];
  const used = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim(), text = row.value.trim();
    if (!name && !text) continue;
    if (!/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(name)) return { error: "请填写有效的参数名。" };
    if (isReasoningControl(name)) return { error: "请在模型名旁选择推理档位。" };
    if (used.has(name)) return { error: "请求参数名不能重复。" };
    if (!text) return { error: "请填写参数值。" };
    let value: Json = text;
    try { value = JSON.parse(text) as Json; }
    catch { if (/^[\[{"]/.test(text)) return { error: "请检查参数值的 JSON 格式。" }; }
    try {
      JSON.stringify(value, (_key, item: unknown) => {
        if (typeof item === "number" && !Number.isFinite(item)) throw new Error("Non-finite JSON number");
        return item;
      });
    } catch { return { error: "请检查参数值的 JSON 格式。" }; }
    used.add(name);
    entries.push([name, value]);
  }
  const parameters = Object.fromEntries(entries);
  for (const [name, desc] of Object.entries(contract?.parameters || {})) {
    const value = customParameterValue({ request_parameters: parameters }, name, desc);
    if (value === undefined) continue;
    if (isReasoningControl(name)) return { error: "请在模型名旁选择推理档位。", field: name };
    if (value !== null && (["fixed", "unsupported"].includes(desc.status) || !validValue(desc, value)))
      return { error: "参数值不符合当前模型的要求。", field: name };
  }
  return { parameters };
}

export function withRequestParameters(settings: UserModelSettings, requestParameters: Record<string, Json>): UserModelSettings {
  const parameters = Object.fromEntries(Object.entries(settings.parameters || {}).map(([name, choice]) =>
    [name, !isReasoningControl(name) && choice.mode === "value" ? { mode: "omit" as const } : choice]));
  return { ...settings, parameters, request_parameters: requestParameters };
}

export function editableReasoningLevels(desc?: Descriptor): ReasoningLevels | undefined {
  if (desc?.ui_hint?.reasoning_levels) return desc.ui_hint.reasoning_levels;
  const levels: ReasoningLevels = {};
  for (const wire of declaredChoices(desc)) {
    const level = wire === "none" ? "off" : REASONING_LEVELS.find(candidate => candidate === wire);
    if (!level || typeof wire !== "string" || levels[level] !== undefined) return undefined;
    levels[level] = wire;
  }
  return Object.keys(levels).some(level => level !== "off") ? levels : undefined;
}

function pickerValues(desc: Descriptor | undefined, saved: Selection): Scalar[] {
  const values = declaredChoices(desc);
  // A profile configured before the level editor may have one explicit value
  // without a declared domain. Keep that value and let the user clear it.
  if (desc && saved.mode === "value" && !["unsupported", "fixed"].includes(desc.status) &&
      !values.includes(saved.value) && validValue(desc, saved.value)) return [...values, saved.value];
  return values;
}

function mappedReasoningSelection(level: ReasoningLevel, wire: string | null): Selection {
  return wire === null ? { mode: "omit", level } : { mode: "value", value: wire, level };
}

export function reasoningOverride(previous: Descriptor | undefined, levels?: ReasoningLevels): Descriptor {
  const shared = { wire_location: previous?.wire_location, wire_path: previous?.wire_path,
    requires: previous?.requires, conflicts_with: previous?.conflicts_with };
  if (!levels) return { ...shared, type: "enum", status: "unsupported", source: "manual" };
  const values = [...new Set(Object.values(levels).filter((value): value is string => typeof value === "string"))];
  return { ...shared, type: "enum", status: previous?.status === "conditional" ? "conditional" : "supported", source: "manual",
    domain: { source: "manual", enforcement: "hard", values }, ui_hint: { reasoning_levels: levels },
  };
}

export function rebindReasoningSelection(selection: Selection, levels?: ReasoningLevels): Selection {
  const wire = selection.level && levels?.[selection.level];
  if (selection.level && wire !== undefined) return mappedReasoningSelection(selection.level, wire);
  if (!selection.level && levels && (selection.mode !== "value" ||
      typeof selection.value === "string" && Object.values(levels).includes(selection.value))) return selection;
  return { mode: "omit" };
}

export function reasoningChoices(contract: CapabilityContract, settings: UserModelSettings) {
  const effortName = "reasoning_effort";
  const descriptor = contract.parameters?.[effortName];
  const effort = selectionFor(effortName, settings);
  const named = descriptor?.ui_hint?.reasoning_levels;
  const levels: string[] = named ? REASONING_LEVELS.filter(level => named[level] !== undefined)
    : pickerValues(descriptor, effort).filter((value): value is string => typeof value === "string" && !!value);
  const modeName = contract.parameters?.thinking_mode ? "thinking_mode" : "enable_thinking";
  const mode = selectionFor(modeName, settings);
  const modes = pickerValues(contract.parameters?.[modeName], mode);
  const on = modes.find(value => value === true || value === "enabled" || value === "adaptive");
  const off = modes.find(value => value === false || value === "disabled");
  const savedMode = mode.mode === "value" && (mode.value === on || mode.value === off);
  if (!contract.scope || (!levels.length && !savedMode && (on === undefined || off === undefined))) return null;

  const offLevel = levels.find(value => value === "none" || value === "off");
  const offChoice = offLevel ?? "none";
  const choices = ["", ...levels];
  if (!named && off !== undefined && offLevel === undefined) choices.splice(1, 0, offChoice);
  if (!levels.length && on !== undefined) choices.push("enabled");

  let current = effort.level && levels.includes(effort.level) ? effort.level : "";
  if (!current && effort.mode === "value") {
    current = levels.find(level => (named ? named[level as ReasoningLevel] : level) === effort.value) ?? "";
  }
  if (choices.includes(offChoice) && mode.mode === "value" && mode.value === off) current = offChoice;
  if (!levels.length && mode.mode === "value" && mode.value === on) current = "enabled";

  const options = choices.map(value => {
    const omit: Selection = { mode: "omit" };
    let next = settings;
    if (levels.length) {
      const level = value as ReasoningLevel;
      const wire = named?.[level];
      const selection = wire !== undefined ? mappedReasoningSelection(level, wire)
        : levels.includes(value) ? { mode: "value" as const, value } : omit;
      next = changeSelection(contract, next, effortName, selection);
    }
    if (on !== undefined || off !== undefined) {
      const modeValue = value === offChoice ? off : on;
      next = changeSelection(contract, next, modeName, value && modeValue !== undefined ? { mode: "value", value: modeValue } : omit);
    }
    const values = selectedValues(contract, next);
    const disabled = Object.keys(contract.parameters || {}).some(name =>
      values[name] != null && !parameterEnabled(name, contract, values, next));
    return { value, settings: next, disabled };
  });
  return { current, options };
}

export function modifiedParameters(contract: CapabilityContract, settings: UserModelSettings): string[] {
  return Object.entries(contract.parameters || {}).filter(([name, desc]) => {
    if (!settings.parameters?.[name]) return false;
    const s = selectionFor(name, settings);
    return s.mode === "value" || s.mode !== (desc.default_mode || "inherit");
  }).map(([name]) => name);
}
export function serviceName(url: string): string {
  return ENDPOINT_PRESETS.find(p => p.url.replace(/\/$/, "") === url.replace(/\/$/, ""))?.name || url;
}
