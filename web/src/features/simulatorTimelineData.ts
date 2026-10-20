import type { ApiSchema } from "../api/client";

export type Signal = ApiSchema["TimelineSignal"];
export type Sample = ApiSchema["TimelineSample"];
export type TraceEvent = ApiSchema["TimelineEvent"];
export type TraceState = ApiSchema["SimulationTimeline"];

export function mergeTrace(previous: TraceState | null, incoming: TraceState): TraceState {
  if (!previous || previous.view_id !== incoming.view_id) return incoming;
  const merge = <T extends { seq: number }>(prior: T[], next: T[]) => next.length ? [...new Map([...prior, ...next].map(row => [row.seq, row])).values()].sort((a, b) => a.seq - b.seq).slice(-6001) : prior;
  return { ...incoming, config: previous.config, samples: merge(previous.samples, incoming.samples), events: merge(previous.events, incoming.events) };
}

// The cursor uses the last complete sample at or before it, never a future value.
export function sampleAt(samples: Sample[], at: number): Sample | null {
  let low = 0, high = samples.length - 1, found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (samples[mid].at_ms <= at) { found = mid; low = mid + 1; } else high = mid - 1;
  }
  return found < 0 ? null : samples[found];
}

export function formatSignal(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "ON" : "OFF";
  if (typeof value !== "number") return Array.isArray(value) ? value.map(formatSignal).join(" … ") : String(value);
  return String(value);
}

export function signalPath(samples: Sample[], signal: Signal, duration: number, low: number, high: number): string {
  const y = (value: number) => 49 - (value - low) / (high - low || 1) * 38;
  let path = "", connected = false;
  for (const sample of samples) {
    const value = sample.values[signal.id];
    if (value == null) { connected = false; continue; }
    const x = sample.at_ms / duration * 1000;
    const pointY = y(Number(value));
    path += !connected ? `M${x},${pointY}` : signal.data_type === "BOOL" ? `H${x}V${pointY}` : `L${x},${pointY}`;
    connected = true;
  }
  return path;
}
