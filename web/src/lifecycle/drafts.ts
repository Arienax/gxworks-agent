// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

/** Track immutable edits against a saved baseline without revisiting shared branches. */
const absent = Symbol("absent");
type Changes = ReadonlyMap<string, Changes | true>;
export type Draft<T> = { value: T; baseline: T; changes?: Changes | true };

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}
function sameShape(a: unknown, b: unknown): boolean {
  return object(a) && object(b) && Array.isArray(a) === Array.isArray(b);
}
function field(value: unknown, key: string): unknown {
  return object(value) && Object.hasOwn(value, key) ? value[key] : absent;
}
function differences(base: unknown, before: unknown, next: unknown, previous?: Changes | true): Changes | true | undefined {
  if (Object.is(base, next)) return undefined;
  if (Object.is(before, next)) return previous;
  if (!sameShape(base, next)) return true;
  const incremental = sameShape(before, next) && previous !== true;
  const changes = new Map<string, Changes | true>(incremental ? previous : undefined);
  const keys = new Set([...Object.keys(incremental ? before as object : base as object), ...Object.keys(next as object)]);
  if (Array.isArray(next)) keys.add("length");
  for (const key of keys) {
    const initial = field(base, key);
    const old = incremental ? field(before, key) : initial;
    const value = field(next, key);
    const change = differences(initial, old, value, incremental ? changes.get(key) : undefined);
    if (change) changes.set(key, change);
    else changes.delete(key);
  }
  return changes.size ? changes : undefined;
}
export function savedDraft<T>(value: T): Draft<T> {
  return { value, baseline: value };
}
export function editDraft<T>(draft: Draft<T>, value: T): Draft<T> {
  if (Object.is(draft.value, value)) return draft;
  return { ...draft, value, changes: differences(draft.baseline, draft.value, value, draft.changes) };
}
/** Adopting a server save preserves edits made after the submitted snapshot. */
export function acceptDraft<T>(draft: Draft<T>, submitted: T, saved: T): Draft<T> {
  return Object.is(draft.value, submitted) ? savedDraft(saved) : editDraft(savedDraft(saved), draft.value);
}
