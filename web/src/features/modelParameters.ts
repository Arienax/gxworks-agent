/** Parameter controls are driven by endpoint evidence, never by model names. */
export type ParameterName = "reasoning_effort" | "temperature";
export type Descriptor = {
  status: "supported" | "accepted" | "unknown" | "unsupported" | "fixed";
  source: "probe" | "metadata" | "manual";
  values?: (string | number)[];
  minimum?: number;
  maximum?: number;
  step?: number;
  reasoning_effort?: string | null;
};
export type ParameterSupport = {
  scope?: { base_url: string; model: string; context: string };
  parameters?: Partial<Record<ParameterName, Descriptor>>;
};
export type Options = Record<string, unknown>;

export const ENDPOINT_PRESETS = [
  { name: "OpenAI", url: "https://api.openai.com/v1" },
  { name: "Qwen / 千问（北京）", url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { name: "Qwen / 千问（新加坡）", url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" },
  { name: "Kimi / Moonshot（中国）", url: "https://api.moonshot.cn/v1" },
  { name: "Kimi / Moonshot（国际）", url: "https://api.moonshot.ai/v1" },
  { name: "Claude / Anthropic", url: "https://api.anthropic.com/v1" },
  { name: "Gemini / Google", url: "https://generativelanguage.googleapis.com/v1beta/openai" },
] as const;

function body(options: Options): Options {
  const value = options.extra_body;
  return value && typeof value === "object" && !Array.isArray(value) ? value as Options : {};
}

export function effectiveValue(defaults: Options, overrides: Options, name: ParameterName): unknown {
  // Match profile deep-merge deletion, then SDK extra_body precedence.
  const value = name in overrides ? overrides[name] : defaults[name];
  const extra = overrides.extra_body === null ? {} : { ...body(defaults) };
  if (name in body(overrides)) {
    const override = body(overrides)[name];
    if (override === null) delete extra[name];
    else extra[name] = override;
  }
  return name in extra && extra[name] != null ? extra[name] : value;
}

export function changeParameter(defaults: Options, overrides: Options, name: ParameterName,
                                value: string | number | null): [Options, Options] {
  const clean = (input: Options): Options => {
    const result = { ...input };
    delete result[name];
    if (name === "reasoning_effort") delete result.temperature;
    if (input.extra_body) {
      const extra = { ...body(input) };
      delete extra[name];
      if (name === "reasoning_effort") delete extra.temperature;
      result.extra_body = extra;
    }
    return result;
  };
  const next = clean(defaults);
  if (value !== null) next[name] = value;
  return [next, clean(overrides)];
}

export function controlValues(descriptor?: Descriptor): (string | number)[] {
  if (!descriptor || !["supported", "fixed"].includes(descriptor.status)) return [];
  if (descriptor.values) return descriptor.values;
  const { minimum, maximum, step } = descriptor;
  if (typeof minimum !== "number" || typeof maximum !== "number" || typeof step !== "number" ||
      !Number.isFinite(minimum + maximum + step) || step <= 0 || maximum < minimum) return [];
  const count = Math.floor((maximum - minimum) / step + 1e-9);
  if (count > 2000) return [];
  return Array.from({ length: count + 1 }, (_, i) => Number((minimum + step * i).toFixed(6)));
}

export function temperatureModeMatches(descriptor: Descriptor, effort: unknown): boolean {
  return !("reasoning_effort" in descriptor) || descriptor.reasoning_effort === (effort ?? null);
}

export function reconcileParameters(defaults: Options, overrides: Options,
                                    support: ParameterSupport): [Options, Options] {
  let result: [Options, Options] = [defaults, overrides];
  for (const name of ["reasoning_effort", "temperature"] as const) {
    const descriptor = support.parameters?.[name];
    if (!descriptor) continue;
    const value = effectiveValue(...result, name);
    const values = controlValues(descriptor);
    if (["fixed", "unsupported"].includes(descriptor.status) ||
        (descriptor.status === "supported" && value != null && !values.includes(value as string | number))) {
      result = changeParameter(...result, name, null);
    }
  }
  return result;
}
