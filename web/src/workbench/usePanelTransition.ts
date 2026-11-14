// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useEffect, useRef, useState } from "react";

export type PanelPhase = "opening" | "open" | "closing" | "closed";

/**
 * Four-phase visibility for a collapsible panel.
 *
 * The panel stays mounted and is hidden by width or transform, so its state
 * survives. `closing` keeps it interactive until the exit motion ends and only
 * then reports `closed`, which is when the caller hides it from the tab order
 * and the accessibility tree. A timeout drives the transition, so a dropped
 * `transitionend` (reduced motion, interrupted animation, throttled tab) can
 * never leave the panel stuck between phases.
 */
export function usePanelTransition(visible: boolean, duration = 220): PanelPhase {
  const [phase, setPhase] = useState<PanelPhase>(visible ? "open" : "closed");
  const settled = useRef(false);
  useEffect(() => {
    if (!settled.current) {
      settled.current = true;
      setPhase(visible ? "open" : "closed");
      return;
    }
    // Reversing mid-flight is left to the CSS transition: it restarts from the
    // current computed width or offset instead of jumping to a keyframe.
    setPhase(visible ? "opening" : "closing");
    const timer = window.setTimeout(() => setPhase(visible ? "open" : "closed"), duration);
    return () => window.clearTimeout(timer);
  }, [visible, duration]);
  return phase;
}
