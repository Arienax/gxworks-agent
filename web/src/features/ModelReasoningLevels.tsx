// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "../components/ui";
import { DEFAULT_REASONING_LEVELS, REASONING_LEVELS } from "./modelParameters";
import type { ReasoningLevel, ReasoningLevels } from "./modelParameters";

const defaultWire = (level: ReasoningLevel) => level === "off" ? null : level;

export function ModelReasoningLevels({ levels, disabled, onChange, t }: {
  levels?: ReasoningLevels;
  disabled: boolean;
  onChange: (levels?: ReasoningLevels) => void;
  t: (key: string) => string;
}) {
  const [editing, setEditing] = useState<{ level: ReasoningLevel; text: string } | null>(null);
  const rows = Object.entries(levels || {}) as [ReasoningLevel, string | null][];
  const available = REASONING_LEVELS.filter(level => levels?.[level] === undefined);
  const highest = Math.max(-1, ...rows.map(([level]) => REASONING_LEVELS.indexOf(level)));
  const next = available.find(level => REASONING_LEVELS.indexOf(level) > highest) ?? available[0];
  const onlyThinkingLevel = rows.filter(([level]) => level !== "off").length === 1;

  return <section className="model-reasoning-levels" aria-label={t("推理档位")}>
    <label className="model-reasoning-toggle">
      <input type="checkbox" role="switch" checked={!!levels} disabled={disabled}
        onChange={event => onChange(event.target.checked ? DEFAULT_REASONING_LEVELS : undefined)} />
      {t("推理档位")}
    </label>
    {levels && <>
      <p className="muted">{t("保存后在模型名旁选档。Off 留空时省略推理参数。")}</p>
      {rows.map(([level, wire], index) => <div className="model-reasoning-row" key={index}>
        <select aria-label={`${t("档位")} ${index + 1}`} value={level} disabled={disabled}
          onChange={event => {
            const replacement = event.target.value as ReasoningLevel;
            const mapped = wire && wire !== level && level !== "off" && replacement !== "off" ? wire : defaultWire(replacement);
            onChange(Object.fromEntries(rows.map(row => row[0] === level ? [replacement, mapped] : row)));
          }}>
          {REASONING_LEVELS.filter(choice => choice === level || (available.includes(choice) &&
            !(choice === "off" && onlyThinkingLevel))).map(choice =>
            <option key={choice} value={choice}>{choice === "off" ? "Off" : choice}</option>)}
        </select>
        <input aria-label={`${level} ${t("API 参数值")}`} placeholder={level === "off" ? t("留空时省略") : level}
          value={editing?.level === level ? editing.text : wire ?? ""} maxLength={256}
          autoComplete="off" spellCheck={false} disabled={disabled}
          onChange={event => {
            const text = event.target.value;
            setEditing({ level, text });
            const value = text.trim() || defaultWire(level);
            if (value !== wire) onChange({ ...levels, [level]: value });
          }} onBlur={() => setEditing(null)} />
        <Button variant="ghost" aria-label={`${t("删除档位")} ${level}`} title={t("删除档位")}
          disabled={disabled || (level !== "off" && onlyThinkingLevel)}
          onClick={() => onChange(Object.fromEntries(rows.filter(([key]) => key !== level)))}>
          <Trash2 size={14} aria-hidden />
        </Button>
      </div>)}
      {next && <Button variant="ghost" disabled={disabled}
        onClick={() => onChange({ ...levels, [next]: defaultWire(next) })}>
        <Plus size={14} aria-hidden />{t("添加档位")}
      </Button>}
    </>}
  </section>;
}
