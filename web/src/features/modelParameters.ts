/** Model-neutral schema and deterministic selection helpers. No model-name tests. */
export type Scalar = string | number | boolean;
export type Options = Record<string, unknown>;
export type Conditions = { requires?: Record<string, (Scalar | null)[]>; conflicts_with?: string[] };
export type Scope = { endpoint: string; model: string; context: string; binding: string };
export type Domain = { values?: Scalar[]; minimum?: number; maximum?: number; exclusive_minimum?: number; exclusive_maximum?: number; step?: number; multiple_of?: number; source?: string; enforcement?: "hard" | "hint" };
export type Descriptor = Conditions & {
  type: "enum" | "number" | "integer" | "boolean" | "string";
  status: "supported" | "conditional" | "accepted" | "unknown" | "unsupported" | "fixed";
  source: "probe" | "metadata" | "manual" | "legacy" | "catalog" | "generic" | "observation";
  domain?: Domain; evidence?: { accepted_values?: Scalar[]; rejected_values?: Scalar[]; observations?: {outcome: string; value: Scalar; context: string; at: number}[] };
  ui_hint?: {minimum?: number; maximum?: number; step?: number; suggestions?: Scalar[]; advanced?: boolean};
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
export type Selection = { mode: "omit" | "inherit" } | { mode: "value"; value: Scalar };
export type UserModelSettings = { scope?: Scope; parameters?: Record<string, Selection> };

/**
 * Endpoint presets fill a base URL only; they never carry model names or tuning
 * defaults. `source` says where the endpoint came from: `local` is an endpoint a
 * bundled capability contract already matches, `dsh` is one whose vendor also
 * appears in the DeepSeek Harness provider catalog.
 *
 * The one admission rule is the protocol this project implements: the endpoint
 * must serve a Chat Completions–compatible API reachable with a stored API key.
 * A vendor's *native* protocol does not disqualify it — Claude and Gemini are
 * listed through their OpenAI compatibility layers, which is exactly what the
 * bundled `anthropic-compat` contract describes. Vendors that authenticate by
 * request signing or OAuth (Bedrock, Vertex), or whose base URL embeds the
 * caller's own resource id (Azure OpenAI, Cloudflare), cannot be a fixed preset
 * and must be typed by the user.
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
  for (const name of Object.keys(contract.parameters || {})) {
    const selection = settings.parameters?.[name];
    result[name] = selection?.mode === "value" ? selection.value : null;
  }
  return result;
}
export function conditionsMatch(condition: Conditions | undefined, values: Options): boolean {
  return Object.entries(condition?.requires || {}).every(([name, allowed]) => allowed.some(v => v === (values[name] ?? null))) &&
    !(condition?.conflicts_with || []).some(name => values[name] != null);
}
export function parameterEnabled(name: string, contract: CapabilityContract, values: Options): boolean {
  const descriptor = contract.parameters?.[name];
  if (!descriptor) return false;
  // Conditions involving request flags are checked again at runtime. An editor
  // cannot know whether a future workflow is streaming or includes tools.
  const known = (rule?: Conditions): Conditions => ({
    requires: Object.fromEntries(Object.entries(rule?.requires || {}).filter(([key]) => key in (contract.parameters || {}))),
    conflicts_with: (rule?.conflicts_with || []).filter(key => key in (contract.parameters || {})),
  });
  return conditionsMatch(known(descriptor), values) && conditionsMatch(known(contract.constraints?.[name]), values);
}
export function adoptSelections(contract: CapabilityContract, previous: UserModelSettings): UserModelSettings {
  const parameters: Record<string, Selection> = {};
  for (const [name, desc] of Object.entries(contract.parameters || {})) {
    const old = previous.parameters?.[name];
    if (old) parameters[name] = old;
    else if (["fixed", "unsupported"].includes(desc.status)) parameters[name] = { mode: "omit" };
    else parameters[name] = { mode: desc.default_mode || "omit" };
  }
  return reconcileSelections(contract, { scope: contract.scope, parameters });
}
export function reconcileSelections(contract: CapabilityContract, settings: UserModelSettings): UserModelSettings {
  // Keep an explicit invalid value visible. Save/request validation must explain
  // the conflict, not silently remove the user's settings after metadata changes.
  return { scope: contract.scope, parameters: Object.fromEntries(Object.entries(settings.parameters || {})
    .filter(([name]) => !!contract.parameters?.[name])) };
}
export function changeSelection(contract: CapabilityContract, settings: UserModelSettings, name: string,
                                selection: Selection): UserModelSettings {
  const desc = contract.parameters?.[name];
  if (!desc || selection.mode === "value" && !validValue(desc, selection.value)) throw new Error("Invalid parameter selection");
  return { scope: contract.scope, parameters: { ...settings.parameters, [name]: selection } };
}

// Both normal actions are zero-generation; verification has a separate API.
export type DiscoveryMode = "list" | "resolve";
