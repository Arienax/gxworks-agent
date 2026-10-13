import { lazy, Suspense, useState } from "react";
import type { ReactNode } from "react";
import { useResource } from "../lifecycle/useResource";
import { Button } from "../components/ui";
import "./delivery.css";

const HardwarePanel = lazy(() => import("./HardwarePanel").then(module => ({ default: module.HardwarePanel })));

// The handoff writer emits headings, tables, lists and quoted evidence only.
// Render that bounded format as React text; embedded HTML/images never execute.
function DeliveryMarkdown({ text }: { text: string }) {
  const lines = text.split(/\r?\n/), blocks: ReactNode[] = [];
  const literal = (value: string) => value.replace(/\\([\\`*_{}\[\]()#+.!<>|~-])/g, "$1");
  const cells = (line: string) => line.split(/(?<!\\)\|/).slice(1, -1).map(cell => literal(cell.trim()));
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const heading = /^(#{1,3}) (.*)$/.exec(line);
    if (heading) {
      const title = literal(heading[2]);
      blocks.push(heading[1].length === 1 ? <h1 key={index}>{title}</h1>
        : heading[1].length === 2 ? <h2 key={index}>{title}</h2> : <h3 key={index}>{title}</h3>);
      index++; continue;
    }
    if (line.startsWith("| ") && /^\|[\s:|\-]+\|$/.test(lines[index + 1] || "")) {
      const key = index, headers = cells(line), rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].startsWith("| ")) rows.push(cells(lines[index++]));
      blocks.push(<div className="delivery-table" key={key}><table><thead><tr>{headers.map((cell, i) => <th key={i} scope="col">{cell}</th>)}</tr></thead>
        <tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (line.startsWith("- ")) {
      const key = index, items: string[] = [];
      while (index < lines.length && /^(?:- | {2})/.test(lines[index])) {
        const current = lines[index++];
        if (current.startsWith("- ")) items.push(literal(current.slice(2)));
        else items[items.length - 1] += "\n" + literal(current.trimStart());
      }
      blocks.push(<ul key={key}>{items.map((item, i) => <li key={i}>{item}</li>)}</ul>);
      continue;
    }
    if (line.startsWith("> ")) {
      const key = index, quote: string[] = [];
      while (index < lines.length && lines[index].startsWith("> ")) quote.push(literal(lines[index++].slice(2)));
      blocks.push(<blockquote key={key}>{quote.join("\n")}</blockquote>);
      continue;
    }
    const key = index, paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !/^(?:#{1,3} |\| |- |> )/.test(lines[index])) paragraph.push(literal(lines[index++]));
    if (!paragraph.length) paragraph.push(literal(lines[index++]));
    blocks.push(<p key={key}>{paragraph.join("\n")}</p>);
  }
  return <article className="delivery-report">{blocks}</article>;
}

export function DeliverySummary({pid,vid,t,refreshKey,readOnly}:{pid:string;vid:string;t:(s:string)=>string;refreshKey:number;readOnly:boolean}) {
  const {value,error}=useResource<{markdown:string}>(`/projects/${pid}/versions/${vid}/delivery`,refreshKey);
  const markdown=value?.markdown||"";
  const [showHardware,setShowHardware]=useState(false);
  return <div className="document-view"><div className="content-heading"><h2>{t("工程交付摘要")}</h2><Button disabled={!markdown} onClick={()=>{const url=URL.createObjectURL(new Blob([markdown],{type:"text/markdown;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download=`${pid}-${vid}-handoff.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>{t("下载摘要")}</Button></div>
    <details onToggle={event=>setShowHardware(event.currentTarget.open)} style={{marginBottom:20}}><summary>{t("高级维护：真实 PLC 只读接入")}</summary>{showHardware&&<Suspense fallback={<p role="status">{t("正在读取本地授权状态…")}</p>}><HardwarePanel pid={pid} vid={vid} readOnly={readOnly} t={t}/></Suspense>}</details>
    {error&&<p role="alert">{error}</p>}{markdown?<DeliveryMarkdown text={markdown}/>:<p role="status">{t("正在核对交付证据")}</p>}</div>;
}
