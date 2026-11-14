// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

// Device comment grouping for the program explorer. A generated program can list
// dozens of addresses, so the panel groups them by device family instead of one
// flat list; the viewer expands one family at a time.

/** Mitsubishi device families in the order an operator reads them. */
export const DEVICE_FAMILY_ORDER = [
  "X", "Y", "M", "S", "T", "C", "D", "R", "W", "B", "Z", "V", "SM", "SD",
] as const;

export const DEVICE_FAMILY_LABELS: Record<string, string> = {
  X: "输入继电器",
  Y: "输出继电器",
  M: "内部继电器",
  S: "状态继电器",
  T: "定时器",
  C: "计数器",
  D: "数据寄存器",
  R: "文件寄存器",
  W: "链接寄存器",
  B: "链接继电器",
  Z: "变址寄存器",
  V: "变址寄存器",
  SM: "特殊继电器",
  SD: "特殊寄存器",
};

export type DeviceFamilyGroup<T> = {
  family: string;
  label: string;
  count: number;
  entries: [string, T][];
};

/** The leading letters of an address, uppercased; empty when the address has none. */
export function deviceFamily(address: string): string {
  const match = /^[A-Za-z]+/.exec(String(address ?? ""));
  return match ? match[0].toUpperCase() : "";
}

function familyRank(family: string): number {
  const index = DEVICE_FAMILY_ORDER.indexOf(family as (typeof DEVICE_FAMILY_ORDER)[number]);
  return index < 0 ? DEVICE_FAMILY_ORDER.length : index;
}

/**
 * Group comment entries by device family. Known families keep the reading order;
 * any other prefix stays visible under its own name, sorted, never dropped.
 * The address order inside a family follows the caller's input order.
 */
export function groupDeviceComments<T>(entries: [string, T][]): DeviceFamilyGroup<T>[] {
  const groups = new Map<string, [string, T][]>();
  for (const entry of entries) {
    if (!Array.isArray(entry) || typeof entry[0] !== "string") continue;
    const family = deviceFamily(entry[0]);
    const bucket = groups.get(family);
    if (bucket) bucket.push(entry);
    else groups.set(family, [entry]);
  }
  return [...groups.entries()]
    .map(([family, list]) => ({
      family,
      label: DEVICE_FAMILY_LABELS[family] ?? (family || "—"),
      count: list.length,
      entries: list,
    }))
    .sort((left, right) =>
      familyRank(left.family) - familyRank(right.family) || left.family.localeCompare(right.family));
}
