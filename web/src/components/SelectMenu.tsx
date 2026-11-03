// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { Popover } from "./Popover";

export type SelectMenuOption<T extends string> = {
  value: T;
  label: ReactNode;
  disabled?: boolean;
};

/**
 * A pill that picks exactly one value. It reuses the popover shell so its menu
 * is the same rounded card as the composer's `+` menu instead of the native
 * dropdown, which cannot follow the workbench theme.
 */
export function SelectMenu<T extends string>({ value, options, onChange, label, title, disabled,
  className = "", triggerClassName = "", mono = false }: {
  value: T;
  options: SelectMenuOption<T>[];
  onChange: (value: T) => void;
  label: string;
  title?: string;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  mono?: boolean;
}) {
  const current = options.find((option) => option.value === value);
  return <Popover
    className={["select-menu", className].filter(Boolean).join(" ")}
    summaryClassName={["select-trigger", triggerClassName].filter(Boolean).join(" ")}
    label={label}
    title={title ?? label}
    disabled={disabled}
    summary={<span className="select-value">{current?.label ?? value}</span>}
  >
    {options.map((option) => {
      const selected = option.value === value;
      return <button
        key={option.value}
        type="button"
        aria-disabled={option.disabled || undefined}
        /* An unavailable choice stays focusable so it is still announced and
         * the menu does not close, unlike a native disabled button. */
        data-keep-open={option.disabled ? "" : undefined}
        aria-pressed={selected}
        className={`select-option${selected ? " selected" : ""}${mono ? " mono" : ""}`}
        onClick={() => {
          if (!option.disabled) onChange(option.value);
        }}
      >
        <span>{option.label}</span>
        {selected && <Check size={13} aria-hidden="true" />}
      </button>;
    })}
  </Popover>;
}
