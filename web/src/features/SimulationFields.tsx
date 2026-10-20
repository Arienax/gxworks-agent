import { Plus, Trash2 } from "lucide-react";
import { Button } from "../components/ui";
import "./simulation.css";

import type { EditorMetadata, Expectation } from "./simulationTypes";

export function DeviceValues({
  values,
  addresses,
  onChange,
  defaultValue,
  t,
}: {
  defaultValue: number;
  values: Record<string, number>;
  addresses: string[];
  onChange: (v: Record<string, number>) => void;
  t: (s: string) => string;
}) {
  const available = addresses.filter((a) => !(a in values));
  return (
    <div className="sim-value-editor">
      {Object.entries(values).map(([address, value]) => (
        <div className="sim-value-row" key={address}>
          <select
            aria-label={t("输入地址")}
            value={address}
            onChange={(e) => {
              const next = { ...values };
              delete next[address];
              next[e.target.value] = value;
              onChange(next);
            }}
          >
            {[address, ...available].map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
          <input
            aria-label={`${address} ${t("输入值")}`}
            type="number"
            value={value}
            onChange={(e) => onChange({ ...values, [address]: Number(e.target.value) })}
          />
          <Button
            aria-label={`${t("删除输入")} ${address}`}
            onClick={() => {
              const next = { ...values };
              delete next[address];
              onChange(next);
            }}
          >
            <Trash2 size={13} />
          </Button>
        </div>
      ))}
      <Button
        disabled={!available.length}
        onClick={() => onChange({ ...values, [available[0]]: defaultValue })}
      >
        <Plus size={13} />
        {t("添加输入")}
      </Button>
    </div>
  );
}

export function Expectations({
  values,
  addresses,
  onChange,
  metadata,
  t,
}: {
  metadata: EditorMetadata;
  values: Expectation[];
  addresses: string[];
  onChange: (v: Expectation[]) => void;
  t: (s: string) => string;
}) {
  function edit(index: number, field: keyof Expectation, value: unknown) {
    onChange(values.map((item, i) => (i === index ? { ...item, [field]: value } : item)));
  }
  return (
    <div className="sim-value-editor">
      {values.map((value, index) => (
        <div className="sim-expect-row" key={index}>
          <select
            aria-label={t("检查地址")}
            value={value.address}
            onChange={(e) => edit(index, "address", e.target.value)}
          >
            <option value="">{t("选择地址")}</option>
            {addresses.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
          <select
            aria-label={t("比较方式")}
            value={value.operator}
            onChange={(e) => {
              const operator = e.target.value;
              const initial = metadata.operators.find((op) => op.value === operator)!.initial_value;
              onChange(values.map((item, i) => i === index
                ? { ...item, operator, value: structuredClone(initial) }
                : item));
            }}
          >
            {metadata.operators.map((op) => (
              <option key={op.value} value={op.value}>
                {t(op.label)}
              </option>
            ))}
          </select>
          <input
            aria-label={t("期望值")}
            placeholder={t(
              metadata.operators.find((op) => op.value === value.operator)?.placeholder || "",
            )}
            value={Array.isArray(value.value) ? value.value.join(",") : String(value.value ?? "")}
            onChange={(e) => edit(index, "value", e.target.value)}
          />
          {value.tolerance !== undefined && (
            <input
              aria-label={t("允许误差")}
              type="number"
              min={0}
              value={value.tolerance ?? ""}
              onChange={(e) => edit(index, "tolerance", Number(e.target.value))}
            />
          )}
          <Button
            aria-label={t("删除条件")}
            onClick={() => onChange(values.filter((_, i) => i !== index))}
          >
            <Trash2 size={13} />
          </Button>
        </div>
      ))}
      <Button onClick={() => onChange([...values, structuredClone(metadata.default_expectation)])}>
        <Plus size={13} />
        {t("添加条件")}
      </Button>
    </div>
  );
}
