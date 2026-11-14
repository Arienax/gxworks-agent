type Row = Record<string, unknown>;
type Change = { section: string; id?: string; value?: unknown; remove?: boolean };
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const keyedRows = (value: unknown): value is (Row & { id: string })[] => Array.isArray(value) && value.every((row) => row && typeof row === "object" && typeof row.id === "string" && row.id);

/** Compare only fields edited locally; unchanged sections are preserved by Core. */
export function specDocumentChanges(before: Record<string, unknown>, after: Record<string, unknown>): Change[] {
  const changes: Change[] = [];
  for (const section of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const old = before[section], next = after[section];
    if (same(old, next)) continue;
    if (keyedRows(old ?? []) && keyedRows(next)) {
      const previous = (old ?? []) as (Row & { id: string })[];
      for (const row of previous) if (!next.some((item) => item.id === row.id)) changes.push({ section, id: row.id, remove: true });
      for (const row of next) if (!same(previous.find((item) => item.id === row.id), row)) changes.push({ section, id: row.id, value: row });
    } else changes.push(next === undefined ? { section, remove: true } : { section, value: next });
  }
  return changes;
}
