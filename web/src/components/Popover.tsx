import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

/** A disclosure menu that closes on outside pointer, item activation and Escape. */
export function Popover({ summary, children, className = "", summaryClassName = "", title, label, disabled }: {
  summary: ReactNode; children: ReactNode; className?: string; summaryClassName?: string;
  title?: string; label?: string; disabled?: boolean;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function close(e: PointerEvent) { if (!ref.current?.contains(e.target as Node)) ref.current?.removeAttribute("open"); }
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  return <details ref={ref} className={`popover ${className}`} data-disabled={disabled || undefined}
    onToggle={(e) => { if (disabled) e.currentTarget.removeAttribute("open"); }}
    onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) e.currentTarget.removeAttribute("open");
    }}
    onKeyDown={(e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        ref.current?.removeAttribute("open");
        ref.current?.querySelector("summary")?.focus();
      }
    }}>
    <summary className={summaryClassName} title={title} aria-label={label}
      aria-disabled={disabled || undefined} tabIndex={disabled ? -1 : undefined}
      onClick={(e) => { if (disabled) e.preventDefault(); }}>{summary}</summary>
    <div className="popover-content" onClick={(e) => {
      const item = (e.target as HTMLElement).closest("button,a");
      if (item && !item.hasAttribute("disabled") && !item.hasAttribute("data-keep-open")) ref.current?.removeAttribute("open");
    }}>{children}</div>
  </details>;
}
