import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Trash2, GitBranch, FileUp, Download } from "lucide-react";
import { api, artifactUrl, key } from "../api/client";
import type { Proposal } from "../api/client";
import { Button } from "../components/ui";
import "./fbd.css";

type Port = { name: string; x: number; y: number };
type Node = { id: string; source_offset?: number; template: string; symbol: string; x: number; y: number; width?: number; height?: number; ports?: Port[] };
type Wire = { source_offset?: number; start?: number[]; end?: number[]; from?: string; to?: string; via?: number[][] };
type Label = { name: string; data_type?: string; kind?: string; class_name?: string; initial_value?: string; device?: string; iec_address?: string; comment?: string };
type DeclarationEdit = { upserts?: Label[]; renames?: Record<string, string>; remove?: string[] };
export type FBDModel = { schema_version: number; program: string; canvas_height?: number; nodes: Node[]; wires: Wire[];
  labels?: Record<string, Label[]>; declaration_edits?: Record<string, DeclarationEdit>; unknown_record_count?: number; issues?: { code: string; message: string }[] };
type CatalogNode = { template: string; kind: string; symbol: string; width: number; height: number; ports: Port[] };
type DraftPreview = { svg: string; model: FBDModel; gx_compile: string };
type NativeRecord = { id: string; created_at: string; operator: string; tool_version: string; outcome: string; report: string; source_gxw_sha256: string; binding_current: boolean;
  native_gxw?: { filename: string; sha256: string; integrity_verified: boolean; matches_source_bytes: boolean; selected_program_matches: boolean } };
type NativeEvidence = { source_gxw_sha256: string; automatic_verification: string; records: NativeRecord[] };

const templateLabel = (key: string, t: (s: string) => string) => {
  const labels: Record<string, string> = { contact: "常开触点", contact_nc: "常闭触点", coil: "线圈", input: "输入值", output: "输出变量" };
  return labels[key] ? t(labels[key]) : key.replace("function_block:", "FB · ").replace("function:", "Function · ");
};

function NativeValidationPanel({ pid, vid, disabled, t }: { pid: string; vid: string; disabled: boolean; t: (s: string) => string }) {
  const route = `/projects/${pid}/versions/${vid}/native-validation`;
  const [evidence, setEvidence] = useState<NativeEvidence | null>(null), [error, setError] = useState("");
  const [operator, setOperator] = useState(""), [toolVersion, setToolVersion] = useState(""), [outcome, setOutcome] = useState("inconclusive");
  const [report, setReport] = useState(""), [attested, setAttested] = useState(false), [busy, setBusy] = useState(false);
  const [nativeGXW, setNativeGXW] = useState<{ filename: string; data_base64: string } | null>(null);
  const [requestId, setRequestId] = useState(key);
  useEffect(() => { let active = true; api<NativeEvidence>(route).then(value => { if (active) setEvidence(value); }).catch(error => { if (active) setError(error.message); }); return () => { active = false; }; }, [route]);
  function changed() { setRequestId(key()); setAttested(false); }
  async function readNative(file?: File) {
    changed(); setNativeGXW(null); setError("");
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      const data_base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
      });
      setNativeGXW({ filename: file.name, data_base64 });
    } catch (error) { setError(String(error instanceof Error ? error.message : error)); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!evidence) return;
    setBusy(true); setError("");
    try {
      await api(route, "POST", { request_id: requestId, source_gxw_sha256: evidence.source_gxw_sha256,
        operator, tool_version: toolVersion, outcome, report, attested, ...(nativeGXW ? { native_gxw: nativeGXW } : {}) });
      setEvidence(await api<NativeEvidence>(route)); setRequestId(key()); setAttested(false); setReport(""); setNativeGXW(null);
    } catch (error) { setError(String(error instanceof Error ? error.message : error)); }
    finally { setBusy(false); }
  }
  const locked = disabled || busy || !evidence;
  const outcomeText = (value: string) => t(value === "passed" ? "操作员报告通过" : value === "failed" ? "操作员报告失败" : "操作员报告待确认");
  return <div className="fbd-sheet fbd-native">
    <h3>{t("记录 GX Works2 原生验证")}</h3>
    <p>{t("下载并在 GX Works2 中编译所选版本，将编译结果、错误信息与保存后的 GXW 文件回填到这里。每条记录绑定当前已保存版本；草稿需先保存为版本。")}</p>
    <p className="fbd-native-status">{t("自动原生编译：未执行。以下结果由操作员报告，生成、导入或文件保存成功不代表编译通过。")}</p>
    {evidence && <p className="muted">{t("所选版本")}：{vid}<br/>GXW SHA-256：<code className="fbd-hash">{evidence.source_gxw_sha256}</code></p>}
    {error && <p className="fbd-error" role="alert">{error}</p>}
    <div className="form">
      <div className="fbd-native-fields"><label>{t("操作员")}<input value={operator} disabled={locked} maxLength={160} onChange={e => { changed(); setOperator(e.target.value); }}/></label>
        <label>{t("GX Works2 版本")}<input value={toolVersion} disabled={locked} maxLength={160} placeholder="GX Works2 …" onChange={e => { changed(); setToolVersion(e.target.value); }}/></label>
        <label>{t("原生编译结果")}<select value={outcome} disabled={locked} onChange={e => { changed(); setOutcome(e.target.value); }}>{["inconclusive", "passed", "failed"].map(item => <option key={item} value={item}>{outcomeText(item)}</option>)}</select></label></div>
      <label>{t("编译报告与复核说明")}<textarea value={report} disabled={locked} maxLength={64000} rows={5} placeholder={t("粘贴 GX Works2 的编译结果、错误所在程序/对象以及复核说明。")} onChange={e => { changed(); setReport(e.target.value); }}/></label>
      <label>{t("保存后的原生 GXW（可选）")}<input key={nativeGXW?.filename || requestId} type="file" accept=".gxw" disabled={locked} onChange={e => void readNative(e.target.files?.[0])}/></label>
      {nativeGXW && <p className="muted">{nativeGXW.filename} · {t("将保留为原生证据附件")}</p>}
      <label className="fbd-attestation"><input type="checkbox" checked={attested} disabled={locked} onChange={e => setAttested(e.target.checked)}/><span>{t("我已核对所选版本和源文件，报告如实描述我的原生验证；此记录不代表工作台自动验证。")}</span></label>
      <Button variant="primary" disabled={locked || !attested || !operator.trim() || !toolVersion.trim() || !report.trim()} onClick={() => void save()}>{t(busy ? "正在保存…" : "保存原生验证记录")}</Button>
    </div>
    <h3>{t("已记录的原生证据")}</h3>
    {evidence?.records.length === 0 && <p className="muted">{t("此版本尚无原生验证记录。")}</p>}
    {evidence?.records.map(item => <article className="fbd-native-record" key={item.id}>
      <strong>{outcomeText(item.outcome)} · {t("未自动验证")}</strong>
      <p className="muted">{item.operator} · {item.tool_version} · {new Date(item.created_at).toLocaleString()}</p>
      {!item.binding_current && <p role="alert" className="fbd-error">{t("源 GXW 文件已变化，此记录不适用于当前文件。")}</p>}
      <pre>{item.report}</pre>
      {item.native_gxw && <><p>{item.native_gxw.integrity_verified ? t("附件完整性已校验") : t("附件缺失或已变化")}
        {" · "}{t(item.native_gxw.matches_source_bytes ? "附件与源文件完全一致" : item.native_gxw.selected_program_matches ? "附件字节已变化；所选程序对象与声明一致" : "附件中的所选程序已变化，不能证明源版本编译通过")}</p>
        <code className="fbd-hash">SHA-256：{item.native_gxw.sha256}</code>
        {item.native_gxw.integrity_verified && <a className="button" href={`/api${route}/${item.id}/gxw`}><Download size={14}/>{t("下载原生证据 GXW")}</a>}</>}
    </article>)}
  </div>;
}

export function FBDImport({ pid, vid, disabled, onProposal, t }: {
  pid: string; vid: string; disabled: boolean; onProposal: (proposal: Proposal) => Promise<void>; t: (s: string) => string;
}) {
  const [upload, setUpload] = useState<{ filename: string; data_base64: string } | null>(null);
  const [programs, setPrograms] = useState<string[]>([]), [program, setProgram] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function read(file?: File) {
    if (!file) return;
    setError(""); setUpload(null); setPrograms([]); setBusy(true);
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
      });
      const value = { filename: file.name, data_base64: encoded };
      const info = await api<{ programs: string[] }>("/fbd/inspect", "POST", value);
      if (!info.programs.length) throw new Error(t("工程中没有可读取的 Program.pou。"));
      setUpload(value); setPrograms(info.programs); setProgram(info.programs[0]);
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  async function propose() {
    if (!upload) return;
    setBusy(true); setError("");
    try {
      const proposal = await api<Proposal>("/fbd/proposals", "POST", { operation: "import", project_id: pid,
        version_id: vid || null, request_id: key(), data_base64: upload.data_base64, program });
      await onProposal(proposal);
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }
  return <div className="form fbd-import">
    <p>{t("读取 GXW 文件并校验结构化梯形图/FBD；校验通过后保存为本地版本。")}</p>
    <label>{t("选择 GXW 工程")}<input type="file" accept=".gxw" disabled={disabled || busy} onChange={e => void read(e.target.files?.[0])} /></label>
    {upload && <label>{t("程序")}<select value={program} onChange={e => setProgram(e.target.value)}>{programs.map(p => <option key={p}>{p}</option>)}</select></label>}
    {error && <p role="alert" className="fbd-error">{error}</p>}
    <Button disabled={disabled || busy || !upload} variant="primary" onClick={() => void propose()}><FileUp size={15} />{t(busy ? "正在读取…" : "校验导入并保存")}</Button>
  </div>;
}

type EditorView = { model: FBDModel; presentation: { tables: string[]; rows: Record<string, Label[]>;
  nodes: { id: string; symbol_editable: boolean; ports: Port[] }[];
  endpoints: { value: string; label: string; point: number[] }[] }; gx_compile: string };

export function FBDPanel({ value, svg, pid, vid, readOnly, preview, onProposal, t }: {
  value: FBDModel | null; svg: string; pid: string; vid: string; readOnly: boolean; preview: boolean;
  onProposal: (proposal: Proposal) => Promise<void>; t: (s: string) => string;
}) {
  const [editor, setEditor] = useState<EditorView | null>(null), [sourceKey, setSourceKey] = useState("");
  const [section, setSection] = useState("diagram"), [catalog, setCatalog] = useState<CatalogNode[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [from, setFrom] = useState(""), [to, setTo] = useState(""), [table, setTable] = useState("");
  const [zoom, setZoom] = useState(1), [page, setPage] = useState(0);
  const [rendered, setRendered] = useState<{ identity: string; value?: DraftPreview; error?: string } | null>(null);
  const generation = useRef(0), pending = useRef(false);
  const inputKey = useMemo(() => JSON.stringify(value), [value]);
  useEffect(() => {
    const current = ++generation.current;
    pending.current = false; setBusy(false); setEditor(null); setError(""); setRendered(null);
    setPage(0); setFrom(""); setTo("");
    api<EditorView>("/fbd/editor", "POST", { project_id: pid, version_id: vid || null, model: JSON.parse(inputKey) })
      .then(result => { if (generation.current === current) { setEditor(result); setSourceKey(JSON.stringify(result.model)); setTable(result.presentation.tables[0] || ""); } })
      .catch(e => { if (generation.current === current) setError(e.message); });
    return () => { generation.current++; };
  }, [inputKey, pid, vid]);
  useEffect(() => { let active = true;
    api<{ nodes: CatalogNode[] }>("/fbd/catalog").then(v => { if (active) { setCatalog(v.nodes); setSelectedTemplate(v.nodes[0]?.template || ""); } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const draft = editor?.model;
  const draftKey = useMemo(() => JSON.stringify(draft), [draft]), dirty = !!draft && draftKey !== sourceKey;
  const previewIdentity = `${pid}/${vid}/${draftKey}`;
  const disabled = readOnly || preview || busy;
  useEffect(() => {
    if (!dirty || preview) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api<DraftPreview>("/fbd/preview", "POST", { project_id: pid, version_id: vid || null, model: JSON.parse(draftKey) })
        .then(value => { if (active) setRendered({ identity: previewIdentity, value }); })
        .catch(error => { if (active) setRendered({ identity: previewIdentity, error: String(error instanceof Error ? error.message : error) }); });
    }, 350);
    return () => { active = false; window.clearTimeout(timer); };
  }, [dirty, preview, pid, vid, draftKey, previewIdentity]);
  async function edit(command: Record<string, unknown>) {
    if (!editor || disabled || pending.current) return;
    const current = generation.current;
    pending.current = true; setBusy(true); setError("");
    try {
      const result = await api<EditorView>("/fbd/editor", "POST", { project_id: pid, version_id: vid || null, model: editor.model, command });
      if (generation.current === current) setEditor(result);
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (generation.current === current) { pending.current = false; setBusy(false); } }
  }
  async function discard() {
    if (pending.current) return;
    const current = generation.current;
    pending.current = true; setBusy(true);
    try {
      const result = await api<EditorView>("/fbd/editor", "POST", { project_id: pid, version_id: vid || null, model: JSON.parse(sourceKey) });
      if (generation.current === current) { setEditor(result); setError(""); }
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (generation.current === current) { pending.current = false; setBusy(false); } }
  }
  async function propose() {
    if (!draft || pending.current || error) return;
    const current = generation.current;
    pending.current = true; setBusy(true); setError("");
    try {
      const proposal = await api<Proposal>("/fbd/proposals", "POST", { operation: vid ? "edit" : "generate",
        project_id: pid, version_id: vid || null, request_id: key(), model: draft });
      if (generation.current === current) await onProposal(proposal);
    } catch (e) { if (generation.current === current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (generation.current === current) { pending.current = false; setBusy(false); } }
  }
  if (!editor || !draft) return <div className="fbd-panel"><p role={error ? "alert" : "status"}>{error || t("正在读取…")}</p></div>;
  const currentRender = rendered?.identity === previewIdentity ? rendered : null;
  const diagram = dirty && !preview ? (currentRender?.value ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(currentRender.value.svg)}` : "") : svg;
  const diagramIssues = currentRender?.value?.model.issues || (!dirty ? draft.issues : []) || [];
  const rows = editor.presentation.rows[table] || [], endpoints = editor.presentation.endpoints;
  const number = (text: string) => text.trim() === "" ? text : Number(text);
  return <div className="fbd-panel">
    <div className="fbd-toolbar"><strong>{draft.program.replace(".Program.pou", "")} · FBD</strong><span className="muted">{draft.nodes.length} {t("对象")} · {draft.wires.length} {t("导线")}</span>
      <div className="fbd-toolbar-actions">{vid && !preview && <a className="button" href={artifactUrl(pid, vid, "gxw", true)}><Download size={14} />GXW</a>}
      {!preview && <Button disabled={disabled || !!error || !draft.nodes.length || (!!vid && !dirty)} variant="primary" onClick={() => void propose()}><GitBranch size={14} />{t("校验并保存版本")}</Button>}</div>
    </div>
    <div className="fbd-sections">{[["diagram","图形"],["objects","对象"],["wires","连接"],["labels","声明"], ...(!preview && vid ? [["native", "原生验证"]] : [])].map(([id,label]) => <button className={section === id ? "active" : ""} key={id} onClick={() => setSection(id)}>{t(label)}</button>)}
      {dirty && <><span role="status">{t(currentRender?.error ? "草稿有错误，请检查后重试。" : currentRender?.value ? "图形已与草稿同步；尚未保存、尚未原生编译。" : "正在校验并更新草稿图形…")}</span><Button disabled={disabled} onClick={() => void discard()}>{t("撤销草稿")}</Button></>}
    </div>
    {error && <p role="alert" className="fbd-error">{error}</p>}
    {dirty && currentRender?.error && <p role="alert" className="fbd-error">{currentRender.error}</p>}
    {!!diagramIssues.length && <details className="fbd-issues"><summary>{t("结构检查提示")} · {diagramIssues.length}</summary><ul>{diagramIssues.map((issue, i) => <li key={i}><code>{issue.code}</code> · {issue.message}</li>)}</ul></details>}
    {section === "diagram" ? <div className="fbd-diagram"><div className="fbd-zoom"><Button onClick={() => setZoom(v => Math.max(.25, v-.25))}>−</Button><Button onClick={() => setZoom(1)} title={t("适应画布")}>{Math.round(zoom*100)}%</Button><Button onClick={() => setZoom(v => Math.min(4, v+.25))}>+</Button></div>
      {diagram ? <img alt={t(dirty ? "当前草稿结构化梯形图/FBD" : "结构化梯形图/FBD")} src={diagram} style={{ width: `${zoom*100}%`, maxWidth: "none" }} /> : dirty ? <div className="empty-state" role="status"><p>{t(currentRender?.error ? "修正对象、导线或声明后，图形会自动更新。" : "正在生成当前草稿图形…")}</p></div> : <div className="empty-state"><GitBranch size={38} /><h2>{t("生成 FBD 工程")}</h2><p>{t("在右侧描述需求并选择“生成程序”，或在对象和连接页签中创建程序。")}</p><Button onClick={() => setSection("objects")}>{t("添加对象")}</Button></div>}
    </div> : section === "objects" ? <div className="fbd-sheet">
      <div className="fbd-inline"><select aria-label={t("对象类型")} value={selectedTemplate} onChange={e => setSelectedTemplate(e.target.value)}>{catalog.map(c => <option key={c.template} value={c.template}>{templateLabel(c.template, t)}</option>)}</select>
        <Button disabled={disabled || !selectedTemplate} onClick={() => void edit({ action: "add_node", template: selectedTemplate })}><Plus size={14}/>{t("添加对象")}</Button></div>
      <table><thead><tr>{["类型", "设备或实例名", "列", "行", "端口", ""].map((v,i) => <th key={i}>{t(v)}</th>)}</tr></thead><tbody>{draft.nodes.map((node, i) => {
        const view = editor.presentation.nodes.find(n => n.id === node.id);
        return <tr key={node.id}>
        <td><select disabled={disabled} value={node.template} onChange={e => void edit({ action: "update_node", id: node.id, field: "template", value: e.target.value })}>{!catalog.some(c => c.template === node.template) && <option>{node.template}</option>}{catalog.map(c => <option key={c.template} value={c.template}>{templateLabel(c.template, t)}</option>)}</select></td>
        <td><input key={`${node.id}:symbol:${node.symbol}`} aria-label={`${t("设备或实例名")} ${i+1}`} disabled={disabled || !view?.symbol_editable} defaultValue={node.symbol} onBlur={e => { if (e.target.value !== node.symbol) void edit({ action: "update_node", id: node.id, field: "symbol", value: e.target.value }); }}/></td>
        {(["x","y"] as const).map(coord => <td key={coord}><input key={`${node.id}:${coord}:${node[coord]}`} aria-label={`${node.id} ${coord}`} className="fbd-number" type="number" disabled={disabled} defaultValue={node[coord]} onBlur={e => { if (e.target.value !== String(node[coord])) void edit({ action: "update_node", id: node.id, field: coord, value: number(e.target.value) }); }}/></td>)}
        <td className="mono">{view?.ports.map(p => p.name).join(" · ")}</td>
        <td><Button aria-label={`${t("删除对象")} ${i+1}`} disabled={disabled} onClick={() => void edit({ action: "delete_node", id: node.id })}><Trash2 size={14}/></Button></td></tr>;
      })}</tbody></table>
      <p className="muted">{t("移动对象后请在连接页签检查导线坐标。更换 FB 实例名会创建相应声明；旧声明可在声明页签中重命名或删除。")}</p>
    </div> : section === "wires" ? <div className="fbd-sheet"><div className="fbd-inline">
      <select aria-label={t("起点端口")} value={from} onChange={e => setFrom(e.target.value)}><option value="">{t("起点端口")}</option>{endpoints.map(e => <option key={e.value} value={e.value}>{e.label}</option>)}</select><span>→</span>
      <select aria-label={t("终点端口")} value={to} onChange={e => setTo(e.target.value)}><option value="">{t("终点端口")}</option>{endpoints.map(e => <option key={e.value} value={e.value}>{e.label}</option>)}</select>
      <Button disabled={disabled || !from || !to} onClick={() => void edit({ action: "add_wire", from, to })}><Plus size={14}/>{t("连接端口")}</Button>
      <Button disabled={disabled} onClick={() => void edit({ action: "add_bus" })}>{t("添加左母线")}</Button></div>
      <table><thead><tr><th>{t("起点")}</th><th>{t("终点")}</th><th>{t("折点")}</th><th/></tr></thead><tbody>{draft.wires.map((w,i) => <tr key={i}>{(["start","end"] as const).map((field,side) => <td key={field}>{w[field] ? <div className="fbd-inline">{[0,1].map(coord => <input key={`${coord}:${w[field]![coord]}`} className="fbd-number" type="number" aria-label={`${t("导线")} ${i+1} ${field} ${coord}`} disabled={disabled} defaultValue={w[field]![coord]} onBlur={e => { const value = [...w[field]!]; const next = number(e.target.value); if (next !== value[coord]) void edit({ action: "update_wire", index: i, field, value: value.map((v,j) => j === coord ? next : v) }); }}/>)}</div> : <span className="mono">{side ? w.to : w.from}</span>}</td>)}
      <td className="mono">{w.from ? <input key={`${i}:${JSON.stringify(w.via)}`} aria-label={`${t("折点")} ${i+1}`} disabled={disabled} defaultValue={w.via?.map(p => p.join(",")).join("; ") || ""} placeholder="x,y; x,y" onBlur={e => { if (e.target.value !== (w.via?.map(p => p.join(",")).join("; ") || "")) void edit({ action: "update_wire", index: i, field: "via", value: e.target.value }); }}/> : "—"}</td><td><Button disabled={disabled} aria-label={`${t("删除导线")} ${i+1}`} onClick={() => void edit({ action: "delete_wire", index: i })}><Trash2 size={14}/></Button></td></tr>)}</tbody></table><p className="muted">{t("端口连线随对象位置重新计算起终点；移动后可编辑折点，保持每段导线水平或垂直。导入的坐标导线保留原始坐标，请检查移动后的连接。")}</p></div>
      : section === "native" && vid && !preview ? <NativeValidationPanel key={`${pid}/${vid}`} pid={pid} vid={vid} disabled={readOnly || busy || dirty} t={t}/>
      : <div className="fbd-sheet"><div className="fbd-inline"><select aria-label={t("声明表")} value={table} onChange={e => { setTable(e.target.value); setPage(0); }}>{editor.presentation.tables.map(n => <option key={n}>{n}</option>)}</select><Button disabled={disabled || !table} onClick={() => void edit({ action: "add_label", table })}><Plus size={14}/>{t("添加声明")}</Button></div>
      <table><thead><tr>{["名称","数据类型","类别","初始值","软元件","注释", ""].map((v,i) => <th key={i}>{t(v)}</th>)}</tr></thead><tbody>{rows.slice(page*50,(page+1)*50).map(row => <tr key={`${table}:${row.name}`}>{(["name","data_type","class_name","initial_value","device","comment"] as const).map(field => <td key={field}><input key={`${row.name}:${field}:${row[field]}`} disabled={disabled} aria-label={`${row.name} ${field}`} defaultValue={row[field] || ""} onBlur={e => { if (e.target.value !== (row[field] || "")) void edit({ action: "update_label", table, name: row.name, field, value: e.target.value }); }}/></td>)}<td><Button disabled={disabled} aria-label={`${t("删除声明")} ${row.name}`} onClick={() => void edit({ action: "delete_label", table, name: row.name })}><Trash2 size={14}/></Button></td></tr>)}</tbody></table>
      <div className="fbd-inline"><Button disabled={page===0} onClick={() => setPage(p=>p-1)}>{t("上一页")}</Button><span>{page+1} / {Math.max(1,Math.ceil(rows.length/50))} · {rows.length}</span><Button disabled={(page+1)*50>=rows.length} onClick={() => setPage(p=>p+1)}>{t("下一页")}</Button></div>
      <p className="muted">{t("FB 调用的实例与类型自动同步到声明表。未知声明字段和未知对象会保留；不支持的编辑会被后端拒绝。")}</p></div>}
  </div>;
}
