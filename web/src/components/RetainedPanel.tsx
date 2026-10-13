import { PanelVisibility, usePanelVisible } from "../lifecycle/visibility";
import { useState } from "react";
import type { ReactNode } from "react";

/** Lazy, bounded tab retention. The parent keys this by project/version/candidate.
 * Only visited tabs mount. Hidden tabs keep drafts/zoom without global caches;
 * changing the document or closing the modal disposes their effects and data.
 */
export function RetainedPanel({ active, children }: { active: boolean; children: () => ReactNode }) {
  const parentVisible = usePanelVisible();
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  if (!active && !visited) return null;
  return <div hidden={!active} style={{ display: active ? "contents" : "none" }}><PanelVisibility.Provider value={active && parentVisible}>{children()}</PanelVisibility.Provider></div>;
}
