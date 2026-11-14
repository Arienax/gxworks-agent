// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import { Plus, Trash2 } from "lucide-react";
import { Badge, Button } from "../components/ui";
import type { RequestParameterRow } from "./modelParameters";

export function ModelRequestParameters({ rows, error, field, onChange, t }: {
  rows: RequestParameterRow[];
  error?: string;
  field?: string;
  onChange: (rows: RequestParameterRow[]) => void;
  t: (key: string) => string;
}) {
  const changeRow = (index: number, patch: Partial<RequestParameterRow>) =>
    onChange(rows.map((row, at) => at === index ? { ...row, ...patch } : row));

  return <section className="model-request-parameters" aria-label={t("自定义请求参数")}>
    <div className="request-parameters-heading">
      <strong>{t("自定义请求参数")}</strong>
      {rows.length > 0 && <Badge>{rows.length}</Badge>}
    </div>
    {rows.map((row, index) => <div className="model-request-parameter-row" key={index}>
      <input className="mono" aria-label={`${t("参数名")} ${index + 1}`} placeholder="top_k"
        autoComplete="off" spellCheck={false} maxLength={64} value={row.name}
        onChange={event => changeRow(index, { name: event.target.value })} />
      <input className="mono" aria-label={`${t("参数值")} ${index + 1}`} placeholder="30"
        autoComplete="off" spellCheck={false} value={row.value}
        onChange={event => changeRow(index, { value: event.target.value })} />
      <Button variant="ghost" aria-label={`${t("删除参数")} ${index + 1}`} title={t("删除参数")}
        onClick={() => onChange(rows.filter((_, at) => at !== index))}>
        <Trash2 size={14} aria-hidden />
      </Button>
    </div>)}
    {rows.length > 0 && <p className="muted">{t("参数值支持 JSON，普通文本会作为字符串发送。")}</p>}
    {error && <p role="alert" className="error-text">{t(error)}{field && <> <code>{field}</code></>}</p>}
    <Button variant="ghost" disabled={rows.length >= 128}
      onClick={() => onChange([...rows, { name: "", value: "" }])}>
      <Plus size={14} aria-hidden />{t("添加参数")}
    </Button>
  </section>;
}
