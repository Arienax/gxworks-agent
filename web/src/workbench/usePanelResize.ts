// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useRef, useState } from "react";

const MIN_CANVAS = 380;
const MIN_CONVERSATION = 400;

/** The engineering canvas keeps room for the conversation and the task sidebar. */
export function clampCanvasWidth(width: number, sidebarOpen = true) {
  const sidebarWidth = sidebarOpen && window.innerWidth > 1100 ? 264 : 0;
  const max = Math.max(MIN_CANVAS, window.innerWidth - sidebarWidth - MIN_CONVERSATION - 5);
  return Math.round(Math.max(MIN_CANVAS, Math.min(max, width)));
}

export function usePanelResize(sidebarOpen: boolean) {
  const [rightWidth, setRightWidth] = useState(() =>
    clampCanvasWidth(Math.min(920, (window.innerWidth - 264) * 0.48), sidebarOpen),
  );
  const panelDragCleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => panelDragCleanup.current?.(), []);
  useEffect(() => {
    const fit = () => setRightWidth((value) => clampCanvasWidth(value, sidebarOpen));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [sidebarOpen]);
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
      nextWidth = clampCanvasWidth(width + start - e.clientX, sidebarOpen);
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
