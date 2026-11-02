// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

/** A disclosure menu that closes on outside pointer, item activation and Escape. */
export function Popover({ summary, children, className = "", summaryClassName = "", title, label, disabled }: {
  summary: ReactNode; children: ReactNode; className?: string; summaryClassName?: string;
  title?: string; label?: string; disabled?: boolean;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  const closing = useRef(0);
  /** Fade out first, then unmount: `details` has no exit animation of its own. */
  function close() {
    const menu = ref.current;
    if (!menu || !menu.hasAttribute("open") || menu.hasAttribute("data-closing")) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    menu.setAttribute("data-closing", "");
    window.clearTimeout(closing.current);
    closing.current = window.setTimeout(() => {
      menu.removeAttribute("open");
      menu.removeAttribute("data-closing");
    }, reduced ? 0 : 140);
  }
  useEffect(() => {
    function onPointer(e: PointerEvent) { if (!ref.current?.contains(e.target as Node)) close(); }
    /** An open menu owns Escape: a capture listener on `window` runs before the
     * document listener of a dialog, so only the menu closes. */
    function onKey(e: KeyboardEvent) {
      const menu = ref.current;
      if (e.key !== "Escape" || !menu?.hasAttribute("open")) return;
      e.stopImmediatePropagation();
      e.preventDefault();
      close();
      menu.querySelector("summary")?.focus();
    }
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey, true);
      window.clearTimeout(closing.current);
    };
  }, []);
  return <details ref={ref} className={`popover ${className}`} data-disabled={disabled || undefined}
    onToggle={(e) => { if (disabled) e.currentTarget.removeAttribute("open"); }}
    onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close();
    }}
    onKeyDown={(e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        /* Escape belongs to the open menu first: a dialog listening on the
         * document must not close together with it. */
        e.nativeEvent.stopImmediatePropagation();
        close();
        ref.current?.querySelector("summary")?.focus();
      }
    }}>
    <summary className={summaryClassName} title={title} aria-label={label}
      aria-disabled={disabled || undefined} tabIndex={disabled ? -1 : undefined}
      onClick={(e) => {
        if (disabled) {
          e.preventDefault();
          return;
        }
        // Reopening during the fade-out cancels the pending unmount.
        if (ref.current?.hasAttribute("data-closing")) {
          window.clearTimeout(closing.current);
          ref.current.removeAttribute("data-closing");
          ref.current.setAttribute("open", "");
          e.preventDefault();
        }
      }}>{summary}</summary>
    <div className="popover-content" onClick={(e) => {
      const item = (e.target as HTMLElement).closest("button,a");
      if (item && !item.hasAttribute("disabled") && !item.hasAttribute("data-keep-open")) close();
    }}>{children}</div>
  </details>;
}
