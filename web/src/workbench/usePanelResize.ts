import type { PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useRef, useState } from "react";

export function usePanelResize() {
  const [rightWidth, setRightWidth] = useState(370);
  const panelDragCleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => panelDragCleanup.current?.(), []);
  function resizePanel(event: ReactPointerEvent) {
    panelDragCleanup.current?.();
    const start = event.clientX,
      width = rightWidth;
    const target = event.currentTarget,
      pointerId = event.pointerId;
    target.setPointerCapture(pointerId);
    let frame = 0,
      nextWidth = width;
    const apply = () => {
      frame = 0;
      setRightWidth(nextWidth);
    };
    const move = (e: PointerEvent) => {
      if (e.pointerId !== pointerId) return;
      nextWidth = Math.max(300, Math.min(650, width + start - e.clientX));
      if (!frame) frame = requestAnimationFrame(apply);
    };
    const cleanup = () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("blur", end);
      target.removeEventListener("lostpointercapture", end);
      panelDragCleanup.current = null;
    };
    const end = () => {
      apply();
      cleanup();
    };
    panelDragCleanup.current = cleanup;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("blur", end);
    target.addEventListener("lostpointercapture", end);
  }

  return { rightWidth, setRightWidth, resizePanel };
}
