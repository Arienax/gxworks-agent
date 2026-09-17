/** Model-neutral schema and deterministic selection helpers. No model-name tests. */
export type Scalar = string | number | boolean;
export type Options = Record<string, unknown>;
export type Conditions = { requires?: Record<string, (Scalar | null)[]>; conflicts_with?: string[] };
export type Scope = { endpoint: string; model: string; context: string; binding: string };
export type Descriptor = Conditions & {
  type: "enum" | "number" | "integer" | "boolean" | "string";
  status: "supported" | "conditional" | "accepted" | "unknown" | "unsupported" | "fixed";
  source: "probe" | "metadata" | "manual" | "legacy";
  values?: Scalar[];
  minimum?: number; maximum?: number; step?: number;
  wire_location?: "body" | "extra_body"; wire_path?: string[];
  default_mode?: "omit" | "inherit"; label?: string;
};
export type Capability = Conditions & {
  status: "supported" | "unsupported" | "unknown" | "conditional";
  source: Descriptor["source"]; modes?: string[]; value?: Scalar;
};
export type CapabilityContract = {
  schema_version?: 2; scope?: Scope;
  parameters?: Record<string, Descriptor>;
  capabilities?: Record<string, Capability>;
  constraints?: Record<string, Conditions>;
};
export type Selection = { mode: "omit" | "inherit" } | { mode: "value"; value: Scalar };
export type UserModelSettings = { scope?: Scope; parameters?: Record<string, Selection> };

export const ENDPOINT_PRESETS = [
  { name: "OpenAI", url: "https://api.openai.com/v1" },
  { name: "Qwen / 千问（北京）", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { name: "Qwen / 千问（新加坡）", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
  { name: "Kimi / Moonshot（中国）", url: "https://api.moonshot.cn/v1" },
  { name: "Kimi / Moonshot（国际）", url: "https://api.moonshot.ai/v1" },
  { name: "Claude / Anthropic", url: "https://api.anthropic.com/v1" },
  { name: "Gemini / Google", url: "https://generativelanguage.googleapis.com/v1beta/openai" },
] as const;

const own = (object: Options, key: string) => Object.hasOwn(object, key);
function object(value: unknown): Options {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Options : {};
}
function read(input: Options, path: string[]): unknown {
  let current: unknown = input;
  for (const key of path) {
    if (!own(object(current), key)) return undefined;
    current = object(current)[key];
  }
  return current;
}
function remove(input: Options, path: string[]): void {
  if (!path.length || !own(input, path[0])) return;
  if (path.length === 1) delete input[path[0]];
  else {
    const child = object(input[path[0]]);
    remove(child, path.slice(1));
    if (!Object.keys(child).length) delete input[path[0]];
  }
}
export function clearKnown(defaults: Options, overrides: Options, contract: CapabilityContract): [Options, Options] {
  return [defaults, overrides].map(group => {
    const next = structuredClone(group);
    for (const [name, desc] of Object.entries(contract.parameters || {})) {
      const path = desc.wire_path || [name];
      for (const alias of [path, ["extra_body", ...path], [name], ["extra_body", name]]) remove(next, alias);
    }
    return next;
  }) as [Options, Options];
}
function merged(base: Options, overlay: Options): Options {
  const result = structuredClone(base);
  for (const [key, value] of Object.entries(overlay)) {
    if (["__proto__", "prototype", "constructor"].includes(key)) continue;
    if (value === null) delete result[key];
    else if (value && typeof value === "object" && !Array.isArray(value)) result[key] = merged(object(result[key]), object(value));
    else result[key] = value;
  }
  return result;
}
export function effectiveValue(defaults: Options, overrides: Options, name: string, desc: Descriptor): unknown {
  const options = merged(defaults, overrides), path = desc.wire_path || [name];
  const extra = read(options, ["extra_body", ...path]);
  return extra !== undefined ? extra : read(options, path) ?? read(options, ["extra_body", name]) ?? options[name];
}
export function controlValues(desc?: Descriptor): Scalar[] {
  if (!desc || !["supported", "conditional", "fixed"].includes(desc.status)) return [];
  if (desc.values) return desc.values;
  if (desc.type === "boolean") return [false, true];
  const { minimum, maximum, step } = desc;
  // Undeclared bounds/steps get a numeric input, never a guessed range.
  if (typeof minimum !== "number" || typeof maximum !== "number" || typeof step !== "number" ||
      !Number.isFinite(minimum + maximum + step) || step <= 0 || maximum < minimum) return [];
  const count = Math.floor((maximum - minimum) / step + 1e-9);
  if (count > 2000) return [];
  return Array.from({ length: count + 1 }, (_, i) => Number((minimum + step * i).toPrecision(12)));
}
export function validValue(desc: Descriptor, value: unknown): value is Scalar {
  if (value == null || !["string", "number", "boolean"].includes(typeof value)) return false;
  if (typeof value === "number" && (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) return false;
  if (typeof value === "string" && value.length > 256) return false;
  if (desc.values && !desc.values.some(v => v === value)) return false;
  if (desc.type === "boolean") return typeof value === "boolean";
  if (desc.type === "string") return typeof value === "string";
  if (desc.type === "number" || desc.type === "integer") {
    if (typeof value !== "number" || (desc.type === "integer" && !Number.isSafeInteger(value))) return false;
    if (desc.minimum != null && value < desc.minimum || desc.maximum != null && value > desc.maximum) return false;
    if (desc.step != null) {
      const units = (value - (desc.minimum || 0)) / desc.step;
      if (Math.abs(units - Math.round(units)) > 1e-7) return false;
    }
  }
  return true;
}
export function selectedValues(contract: CapabilityContract, settings: UserModelSettings,
                               defaults: Options = {}, overrides: Options = {}): Options {
  const result: Options = {};
  for (const [name, desc] of Object.entries(contract.parameters || {})) {
    const selection = settings.parameters?.[name];
    result[name] = selection?.mode === "value" ? selection.value : selection?.mode === "omit" ? null :
      effectiveValue(defaults, overrides, name, desc) ?? null;
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
export function adoptSelections(contract: CapabilityContract, previous: UserModelSettings,
                                defaults: Options, overrides: Options): UserModelSettings {
  const parameters: Record<string, Selection> = {};
  for (const [name, desc] of Object.entries(contract.parameters || {})) {
    const old = previous.parameters?.[name];
    const value = old?.mode === "value" ? old.value : effectiveValue(defaults, overrides, name, desc);
    if (["fixed", "unsupported"].includes(desc.status)) parameters[name] = { mode: "omit" };
    else if (old && old.mode !== "value") parameters[name] = old;
    else if (validValue(desc, value)) parameters[name] = { mode: "value", value };
    else parameters[name] = { mode: desc.default_mode || "omit" };
  }
  return reconcileSelections(contract, { scope: contract.scope, parameters }, defaults, overrides);
}
export function reconcileSelections(contract: CapabilityContract, settings: UserModelSettings,
                                    defaults: Options = {}, overrides: Options = {}): UserModelSettings {
  const parameters = { ...settings.parameters };
  // Evaluate one immutable snapshot so conflicts cannot resolve according to
  // dictionary iteration order. Clear every invalid dependent selection.
  const values = selectedValues(contract, settings, defaults, overrides);
  for (const [name, selection] of Object.entries(parameters)) {
    const desc = contract.parameters?.[name];
    if (!desc) { delete parameters[name]; continue; }
    if (selection.mode === "value" && (!validValue(desc, selection.value) || !parameterEnabled(name, contract, values)))
      parameters[name] = { mode: "omit" };
  }
  return { scope: contract.scope, parameters };
}
export function changeSelection(contract: CapabilityContract, settings: UserModelSettings, name: string,
                                selection: Selection, defaults: Options = {}, overrides: Options = {}): UserModelSettings {
  const desc = contract.parameters?.[name];
  if (!desc || selection.mode === "value" && !validValue(desc, selection.value)) throw new Error("Invalid parameter selection");
  const next = { scope: contract.scope, parameters: { ...settings.parameters, [name]: selection } };
  // First remove dependents of the changed setting. The requested choice itself
  // is retained so a conflict is visible instead of silently undoing a click.
  const result = reconcileSelections(contract, next, defaults, overrides);
  if (result.parameters) result.parameters[name] = selection;
  return result;
}
