// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

/** Latest-read ownership. Mutations are never cancelled or automatically retried. */
export class LatestRead {
  private controller: AbortController | undefined;
  begin() {
    this.cancel();
    const controller = new AbortController();
    this.controller = controller;
    return {
      signal: controller.signal,
      cancel: () => controller.abort(),
      current: () => this.controller === controller && !controller.signal.aborted,
    };
  }
  cancel() {
    this.controller?.abort();
    this.controller = undefined;
  }
}

/** Compare JSON data without serializing or allocating whole-record strings. */
export function equalValue(previous: unknown, next: unknown): boolean {
  if (Object.is(previous, next)) return true;
  if (
    previous === null ||
    next === null ||
    typeof previous !== "object" ||
    typeof next !== "object"
  )
    return false;
  if (Array.isArray(previous)) {
    return (
      Array.isArray(next) &&
      previous.length === next.length &&
      previous.every((value, index) => equalValue(value, next[index]))
    );
  }
  if (Array.isArray(next)) return false;
  const oldRecord = previous as Record<string, unknown>;
  const newRecord = next as Record<string, unknown>;
  const keys = Object.keys(oldRecord);
  return (
    keys.length === Object.keys(newRecord).length &&
    keys.every((key) => Object.hasOwn(newRecord, key) && equalValue(oldRecord[key], newRecord[key]))
  );
}

/** Preserve unchanged snapshots; comparisons stop at the first changed field. */
export function sameValue<T>(previous: T, next: T): T {
  return equalValue(previous, next) ? previous : next;
}

export function reconcileById<T extends { id: string }>(previous: T[], next: T[]): T[] {
  const records = new Map(previous.map((item) => [item.id, item]));
  const shared = next.map((item) => {
    const old = records.get(item.id);
    return old ? sameValue(old, item) : item;
  });
  return shared.length === previous.length && shared.every((item, i) => item === previous[i])
    ? previous
    : shared;
}


/** Server revisions cover every persisted update. Legacy or optimistic rows
 * are adopted directly; their payload is never recursively compared. */
export function reconcileByRevision<T extends { id: string; revision?: number | null;
  status?: string; execution_job_id?: string | null }>(previous: T[], next: T[]): T[] {
  const records = new Map(previous.map((item) => [item.id, item]));
  const shared = next.map((item) => {
    const old = records.get(item.id);
    return old && typeof item.revision === "number" && item.revision === old.revision
      && item.status === old.status && item.execution_job_id === old.execution_job_id ? old : item;
  });
  return shared.length === previous.length && shared.every((item, index) => item === previous[index])
    ? previous : shared;
}

export type Poller = {
  refresh: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
};

/** Completion-based polling: at most one read, even across manual invalidations.
 * An invalidation suppresses the in-flight snapshot and queues one fresh read.
 */
export function startPolling<T>(options: {
  read: (signal: AbortSignal) => Promise<T>;
  apply: (value: T) => void;
  error: (error: unknown) => void;
  interval: number;
  paused?: boolean;
}): Poller {
  let stopped = false,
    paused = options.paused ?? false,
    running = false,
    queued = false,
    revision = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const controller = new AbortController();
  const clear = () => {
    clearTimeout(timer);
    timer = undefined;
  };
  const schedule = (delay: number) => {
    clear();
    if (!stopped && !paused) timer = setTimeout(() => void run(), delay);
  };
  async function run() {
    if (stopped || paused || running) return;
    running = true;
    queued = false;
    const started = revision;
    try {
      const value = await options.read(controller.signal);
      if (!stopped && !paused && started === revision) options.apply(value);
    } catch (error) {
      if (!stopped && !paused && started === revision) options.error(error);
    } finally {
      running = false;
      schedule(queued ? 0 : options.interval);
    }
  }
  const refresh = () => {
    revision++;
    queued = true;
    clear();
    if (!running) void run();
  };
  void run();
  return {
    refresh,
    pause() {
      paused = true;
      revision++;
      clear();
    },
    resume() {
      if (paused) {
        paused = false;
        refresh();
      }
    },
    stop() {
      stopped = true;
      revision++;
      clear();
      controller.abort();
    },
  };
}
