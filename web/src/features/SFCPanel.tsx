import { useEffect, useId, useRef, useState } from "react";
import { api, artifactUrl, key } from "../api/client";
import type { Proposal } from "../api/client";
import { Button } from "../components/ui";
import { NativeValidationPanel } from "./FBDPanel";
import "./sfc.css";

type Binding = { status: string; step_reference?: number; action_number?: number; transition_number?: number;
  child?: string; registrations?: { qualifier: number; zoom: string }[] };
type Cell = { id: string; x: number; y: number; type: number; result: number; role: string; binding: Binding };
type Symbol = { cell: string; kind: string; ports: ("down" | "up" | "right" | "left")[]; reference?: number };
type Topology = { status: string; symbols: Symbol[];
  segments: { source: string; target: string; direction: string; style: string }[];
  jumps: { source: string; step_reference: number; targets: string[]; status: string }[];
  issues: { cell?: string; reason: string }[] };
type Child = { kind: string; name: string; number: number | null;
  records: { kind: string; op?: string; args?: string[]; raw_hex: string }[] };
export type SFCModel = { program: string; cpu: string; codepage: number;
  graph: { position_resolution: string; edit_profile?: string | null; width?: number; height?: number; cells?: Cell[]; topology?: Topology;
    write_symbols?: { kind: string; number?: number; x?: number; y?: number; start?: number; end?: number }[] | null;
    records: { kind: string; references: number[] }[] };
  actions: { name: string; number: number; registrations: { zoom: string; qualifier: number }[] }[];
  children: Child[]; issues: (string | { message?: string; code?: string })[] };
type ActionEdit = { step_reference: number; action_number: number };
type JumpEdit = { kind: "jump"; x: number; y: number; before_step_reference: number; target_step_reference: number };
type SwapEdit = { kind: "swap_steps"; left_step_reference: number; right_step_reference: number };
type GraphEdit = ActionEdit | JumpEdit | SwapEdit;
const isActionEdit = (edit: GraphEdit): edit is ActionEdit => !("kind" in edit);
const isJumpEdit = (edit: GraphEdit): edit is JumpEdit => "kind" in edit && edit.kind === "jump";
type Preview = { program: SFCModel; validation: { native_save: string }; diff: { has_changes: boolean } };

export function SFCPanel({ value, pid, vid, readOnly, preview, onProposal, t }: {
  value: SFCModel | null; pid: string; vid: string; readOnly: boolean; preview: boolean;
  onProposal: (proposal: Proposal) => Promise<void>; t: (s: string) => string;
}) {
  const [source, setSource] = useState<SFCModel | null>(value), [candidate, setCandidate] = useState<Preview | null>(null);
  const [edits, setEdits] = useState<GraphEdit[]>([]), [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  const jumpArrow = useId();
  useEffect(() => { epoch.current++; setSource(value); setCandidate(null); setEdits([]); setSelected(""); setError(""); setBusy(false); }, [value, pid, vid]);
  useEffect(() => () => { epoch.current++; }, []);
  const model = candidate?.program || source;
  const cells = model?.graph.cells || [];
  const topology = model?.graph.topology;
  const symbols = new Map((topology?.symbols || []).map(symbol => [symbol.cell, symbol]));
  const positions = new Map(cells.map(cell => [cell.id, [(cell.x + 1) * 128, (cell.y + 1) * 64]]));
  const picked = cells.find(cell => cell.id === selected);
  const sourceJump = source?.graph.topology?.jumps.find(jump => jump.source === selected);
  const sourceJumpCell = source?.graph.cells?.find(cell => cell.id === selected);
  const targetSteps = (source?.graph.cells || []).filter(cell => cell.role === "step" && cell.binding.status === "source-resolved");
  const names = picked?.role === "step" ? picked.binding.registrations?.map(item => item.zoom) || []
    : picked?.binding.child ? [picked.binding.child] : [];
  const children = (model?.children || []).filter(child => names.includes(child.name));
  const locked = readOnly || preview || busy;
  const editable = model?.graph.edit_profile === "fx-references";
  const swapSteps = (source?.graph.write_symbols || []).filter(symbol => symbol.kind === "step" && symbol.number != null);
  const selectedStep = source?.graph.cells?.find(cell => cell.id === selected && cell.role === "step")?.binding.step_reference;
  async function loadGeometry() {
    if (!source || preview || busy) return;
    const current = ++epoch.current; setBusy(true); setError("");
    try {
      const snapshot = await api<{ model: SFCModel }>(`/sfc/source?project_id=${encodeURIComponent(pid)}&version_id=${encodeURIComponent(vid)}&program=${encodeURIComponent(source.program)}&native=true`);
      if (current === epoch.current) { setSource(snapshot.model); setCandidate(null); }
    } catch (e) { if (current === epoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current === epoch.current) setBusy(false); }
  }
  function change(step: number, action: number, original: number) {
    setCandidate(null);
    setEdits(before => [...before.filter(edit => !isActionEdit(edit) || edit.step_reference !== step), ...(action === original ? [] : [{ step_reference: step, action_number: action }])]);
  }
  function changeJump(target: number) {
    if (!sourceJump || !sourceJumpCell) return;
    const cell = sourceJumpCell;
    setCandidate(null);
    setEdits(before => [...before.filter(edit => !isJumpEdit(edit) || edit.x !== cell.x || edit.y !== cell.y),
      ...(target === sourceJump.step_reference ? [] : [{ kind: "jump" as const, x: cell.x, y: cell.y,
        before_step_reference: sourceJump.step_reference, target_step_reference: target }])]);
  }
  async function prepare() {
    if (!source || locked || !edits.length) return;
    const current = ++epoch.current; setBusy(true); setError("");
    try {
      const result = await api<Preview>("/sfc/preview", "POST", { project_id: pid, version_id: vid, program: source.program, graph_edits: edits });
      if (current === epoch.current) setCandidate(result);
    } catch (e) { if (current === epoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current === epoch.current) setBusy(false); }
  }
  async function save() {
    if (!source || locked || !candidate || !edits.length) return;
    const current = ++epoch.current; setBusy(true); setError("");
    try {
      const proposal = await api<Proposal>("/sfc/proposals", "POST", { operation: "edit", project_id: pid, version_id: vid,
        program: source.program, request_id: key(), graph_edits: edits });
      if (current === epoch.current) await onProposal(proposal);
    } catch (e) { if (current === epoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current === epoch.current) setBusy(false); }
  }
  if (!model) return <div className="empty-state"><h2>SFC</h2><p>{t("导入已有 SFC 工程以查看步骤、动作和转换条件。")}</p>
    <SFCImport pid={pid} vid={vid} disabled={readOnly} onProposal={onProposal} t={t}/></div>;
  const maxX = Math.max(1, ...cells.map(cell => cell.x + 1)), maxY = Math.max(1, ...cells.map(cell => cell.y + 1));
  return <div className="sfc-panel">
    <header className="sfc-toolbar"><strong>{model.program} · {model.cpu}</strong>
      {!preview && model.graph.position_resolution !== "native-source-bound" && <Button disabled={busy} onClick={() => void loadGeometry()}>{t(busy ? "正在读取…" : "读取原生图形")}</Button>}
      <Button disabled={locked || !(editable || swapSteps.length > 1) || !edits.length} onClick={() => void prepare()}>{t("预览修改")}</Button>
      <Button variant="primary" disabled={locked || !candidate || !candidate.diff.has_changes} onClick={() => void save()}>{t("保存候选")}</Button>
      {vid && !preview && <a className="button" href={artifactUrl(pid, vid, "gxw")}>{t("下载 GXW")}</a>}
    </header>
    {error && <p className="sfc-error" role="alert">{error}</p>}
    {candidate && <p className="muted">{t("已通过原生保存回读；当前代码检查需对保存后的版本执行。")}</p>}
    <div className="sfc-layout">
      <section className="sfc-canvas" aria-label={t("SFC 图形")}>
        {cells.length ? <svg role="img" aria-label={t("当前源码的原生格点")} viewBox={`0 0 ${(maxX + 1) * 128} ${(maxY + 1) * 64}`}>
          <defs><marker id={jumpArrow} viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L6 3 L0 6" fill="context-stroke"/></marker></defs>
          <g className="sfc-connections" aria-hidden="true">{(topology?.segments || []).map(segment => {
            const from = positions.get(segment.source), to = positions.get(segment.target);
            if (!from || !to) return null;
            return <g key={`${segment.source}/${segment.target}`}>{(segment.style === "parallel" ? [-3, 3] : [0]).map(offset =>
              <line key={offset} x1={from[0]} y1={from[1] + offset} x2={to[0]} y2={to[1] + offset}/>)}</g>;
          })}</g>
          <g className="sfc-jump-connections" aria-hidden="true">{(topology?.jumps || []).map(jump => {
            if (jump.status !== "uniquely-correlated" || jump.targets.length !== 1) return null;
            const from = positions.get(jump.source), to = positions.get(jump.targets[0]);
            if (!from || !to) return null;
            return <path key={jump.source} d={`M${from[0] - 32} ${from[1]} H${Math.min(from[0], to[0]) - 80} V${to[1]} H${to[0] - 49}`} markerEnd={`url(#${jumpArrow})`}/>;
          })}</g>
          {cells.map(cell => {
            const symbol = symbols.get(cell.id);
            const terminal = symbol?.kind === "jump" || symbol?.kind === "end";
            const connector = cell.role === "connector" && symbol?.kind !== "opaque" && !!symbol?.ports.length;
            return <g key={cell.id} transform={`translate(${(cell.x + 1) * 128},${(cell.y + 1) * 64})`}
            className={selected === cell.id ? "sfc-cell selected" : "sfc-cell"} role="button" tabIndex={0}
            aria-label={cell.role === "step" ? `S${cell.binding.step_reference} · ${t("动作")} ${cell.binding.action_number}`
              : cell.role === "transition" ? `${t("转换条件")} ${cell.binding.transition_number}`
              : terminal ? symbol.kind === "jump" ? `${t("跳转")} S${symbol.reference}` : t("结束") : t("原生连接符")}
            onClick={() => setSelected(cell.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(cell.id); } }}>
            {cell.role === "step" ? <><rect x="-45" y="-21" width="90" height="42" rx="3"/>
              {(cell.type === 6 || cell.type === 10 || cell.type === 26) && <rect x="-40" y="-16" width="80" height="32" rx="2"/>}
              <text textAnchor="middle" y="-2">S{cell.binding.step_reference}</text>
              <text className="sfc-action" textAnchor="middle" y="14">{t("动作")} {cell.binding.action_number}</text></>
              : cell.role === "transition" ? <><rect x="-22" y="-4" width="44" height="8"/><text x="29" y="5">T{cell.binding.transition_number}</text></>
              : terminal ? <><rect x="-32" y="-11" width="64" height="22" rx="3"/><text textAnchor="middle" y="5">{symbol.kind === "jump" ? `↗ S${symbol.reference}` : t("结束")}</text></>
              : connector ? <><rect x="-22" y="-16" width="44" height="32" className="sfc-connector-hit"/>
                {symbol.ports.map(port => {
                  const delta = { down: [0, 12], up: [0, -12], right: [22, 0], left: [-22, 0] }[port];
                  return (symbol.kind.startsWith("parallel-") && (port === "right" || port === "left") ? [-3, 3] : [0]).map(offset =>
                    <line key={`${port}/${offset}`} x1="0" y1={offset} x2={delta[0]} y2={delta[1] + offset}/>);
                })}</>
              : <><circle r="5"/><text className="sfc-action" textAnchor="middle" y="20">{t("未解析连接符")}</text></>}
          </g>; })}
        </svg> : <p className="muted">{t("尚未取得当前图形的原生位置。源码引用如下。")}</p>}
        {!!topology?.issues.length && <p className="muted">{t("部分连接尚未解析。")}</p>}
      </section>
      <aside className="sfc-details">
        <h3>{t("步骤与动作")}</h3>
        {(source?.graph.records || []).filter(row => row.kind === "step-action-reference").map(row => {
          const [step] = row.references;
          const action = source?.graph.cells?.find(cell => cell.binding.step_reference === step)?.binding.action_number ?? row.references[1];
          return <label key={step}>S{step}<select aria-label={`S${step} ${t("动作")}`} disabled={locked || !editable}
            value={edits.filter(isActionEdit).find(edit => edit.step_reference === step)?.action_number ?? action}
            onChange={e => change(step, Number(e.target.value), action)}>
            {source?.actions.map(item => <option key={item.name} value={item.number}>{t("动作")} {item.number}</option>)}
          </select></label>;
        })}
        {selectedStep != null && swapSteps.some(symbol => symbol.number === selectedStep) && <label>{t("交换步骤")}
          <select aria-label={t("交换步骤")} disabled={locked}
            value={edits.find((edit): edit is SwapEdit => "kind" in edit && edit.kind === "swap_steps" && edit.left_step_reference === selectedStep)?.right_step_reference ?? ""}
            onChange={e => { setCandidate(null); setError(""); setEdits(e.target.value === "" ? [] : [{ kind: "swap_steps",
              left_step_reference: selectedStep, right_step_reference: Number(e.target.value) }]); }}>
            <option value="">{t("选择步骤")}</option>
            {swapSteps.filter(symbol => symbol.number !== selectedStep).map(symbol =>
              <option key={symbol.number} value={symbol.number}>S{symbol.number}</option>)}
          </select></label>}
        {sourceJump && sourceJumpCell && <label>{t("跳转目标")}<select aria-label={t("跳转目标")}
          disabled={locked || !editable}
          value={edits.filter(isJumpEdit).find(edit => edit.x === sourceJumpCell.x && edit.y === sourceJumpCell.y)?.target_step_reference ?? sourceJump.step_reference}
          onChange={e => changeJump(Number(e.target.value))}>
          {!targetSteps.some(cell => cell.binding.step_reference === sourceJump.step_reference) &&
            <option value={sourceJump.step_reference} disabled>S{sourceJump.step_reference} · {t("未解析")}</option>}
          {targetSteps.map(cell => <option key={cell.id} value={cell.binding.step_reference}>S{cell.binding.step_reference}</option>)}
        </select></label>}
        <h3>{t("所选子程序")}</h3>
        {!children.length && <p className="muted">{t("选择步骤或转换条件查看源码。")}</p>}
        {children.map(child => <article key={child.name}><strong>{t(child.kind === "transition" ? "转换条件" : "动作")}</strong>
          <pre>{child.records.map(row => row.op ? `${row.op} ${(row.args || []).join(" ")}` : t("未解析记录，已保留")).join("\n")}</pre>
        </article>)}
      </aside>
    </div>
    {model.issues.length > 0 && <details><summary>{t("未解析内容")}</summary><ul>{model.issues.map((item, i) => <li key={i}>{typeof item === "string" ? item : item.message || item.code}</li>)}</ul></details>}
    {vid && !preview && <NativeValidationPanel pid={pid} vid={vid} program={model.program} disabled={readOnly || busy || !!candidate || !!edits.length} t={t} onCell={setSelected}/>}
  </div>;
}

function SFCImport({ pid, vid, disabled, onProposal, t }: {
  pid: string; vid: string; disabled: boolean; onProposal: (proposal: Proposal) => Promise<void>; t: (s: string) => string;
}) {
  const [upload, setUpload] = useState<string>(""), [programs, setPrograms] = useState<string[]>([]), [program, setProgram] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const epoch = useRef(0);
  useEffect(() => () => { epoch.current++; }, []);
  async function read(file?: File) {
    if (!file || disabled || busy) return;
    const current = ++epoch.current; setBusy(true); setError(""); setUpload("");
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
      });
      const result = await api<{ programs: string[] }>("/fbd/inspect", "POST", { data_base64: encoded });
      if (current === epoch.current) { setUpload(encoded); setPrograms(result.programs); setProgram(result.programs[0] || ""); }
    } catch (e) { if (current === epoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current === epoch.current) setBusy(false); }
  }
  async function save() {
    if (!upload || !program || busy || disabled) return;
    const current = ++epoch.current; setBusy(true); setError("");
    try {
      const result = await api<Proposal>("/sfc/proposals", "POST", { operation: "import", project_id: pid,
        version_id: vid || undefined, request_id: key(), data_base64: upload, program });
      if (current === epoch.current) await onProposal(result);
    } catch (e) { if (current === epoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (current === epoch.current) setBusy(false); }
  }
  return <div className="form"><label>{t("已有 GXW 工程")}<input type="file" accept=".gxw" disabled={disabled || busy} onChange={e => void read(e.target.files?.[0])}/></label>
    {programs.length > 0 && <label>{t("程序")}<select value={program} disabled={disabled || busy} onChange={e => setProgram(e.target.value)}>
      {programs.map(name => <option key={name} value={name}>{name}</option>)}</select></label>}
    <Button variant="primary" disabled={disabled || busy || !upload || !program} onClick={() => void save()}>{t("导入 SFC 工程")}</Button>
    {error && <p className="sfc-error" role="alert">{error}</p>}
  </div>;
}
