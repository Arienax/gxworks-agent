import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Play, Plus, Save, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { usePanelVisible } from "../lifecycle/visibility";
import { useResource } from "../lifecycle/useResource";
import { Button } from "../components/ui";
import "./simulation.css";

type Value = number | string | boolean | null;
type Expectation = { address: string; operator: string; value: Value | Value[]; tolerance?: number };
type Step = { id: string; at_ms: number; set: Record<string, number>; expect: Expectation[]; wait_for: Expectation[]; timeout_ms?: number; poll_ms?: number };
type TestCase = { name: string; description?: string; plc_model: string; initial: Record<string, number>; steps: Step[]; sample_ms: number; timeout_ms: number; trace_devices: string[]; invariants?: unknown[]; fault_injections?: unknown[]; metadata?: Record<string, unknown> };
type Suite = { name: string; plc_model: string; tests: TestCase[] };
type Plan = { binding: { plan_id: string; version_id: string }; source: string; suite: Suite; requirement_links: Record<string, string[]>; issue_ids: string[] };
type RequirementRun = { run_id: string; test_name: string; created_at: string; backend_kind: string | null; status: string; recorded_status: string; verification: { status: string; category?: string } };
type Requirement = { id: string; text: string; devices: string[]; status: string; latest_run: RequirementRun | null; tests: { plan_id: string; test_name: string; latest_run: RequirementRun | null }[] };
type EditorMetadata = { empty_suite: Suite; default_expectation: Expectation; default_input_value: number;
  wait_timeout_ms: number; poll_ms: number; minimum_interval_ms: number; maximum_duration_ms: number;
  operators: { value: string; label: string; initial_value: Value; placeholder: string }[] };
type Workbench = { editor: EditorMetadata; version_id: string; ir_sha256: string; plc_model: string; devices: { address: string; label: string; writable: boolean }[]; plans: Plan[]; unavailable_plans: { plan_id: string; message: string }[]; unavailable_runs: string[]; requirements: Requirement[]; runs: { run_id: string; suite_name: string; status: string; created_at: string }[] };
type Observation = { at_ms: number; event: string; values: Record<string, Value> };
type ReplayCase = { name: string; status: string; backend_kind: string; observations: Observation[]; duration_ms: number; sample_ms: number; assertions: { passed?: boolean; at_ms?: number; step_id?: string; address?: string; actual?: Value; expected?: unknown; message?: string }[]; invariant_violations: unknown[]; requirement_ids: string[]; issue_ids: string[]; steps: Step[] };
type Replay = { run_id: string; version_id: string; status: string; verification: { status: string; category?: string; program_repair_allowed?: boolean }; cases: ReplayCase[] };
export type SimulationIssueContext = { id: string; title: string; addresses: string[] };
type Props = { pid: string; vid: string; readOnly: boolean; t: (s: string) => string; onExecute: (planId: string) => Promise<void>; onDebug?: (runId: string) => Promise<void>; onSaved?: () => void; issueContext?: SimulationIssueContext; initialPlanId?: string; refreshKey?: string | number };

const message = (e: unknown) => e instanceof Error ? e.message : String(e);
const statusLabel = (status: string) => ({ passed: "通过", failed: "失败", error: "执行出错", unavailable: "环境不可用", blocked: "证据未获验收" }[status] || status);
const encode = encodeURIComponent;

export function SimulationWorkbench({ pid, vid, readOnly, t, onExecute, onDebug, onSaved, issueContext, initialPlanId, refreshKey }: Props) {
  const path = `/projects/${encode(pid)}/versions/${encode(vid)}/simulation-workbench`;
  const [localRevision,setLocalRevision]=useState(0);
  const {value:data,error:readError}=useResource<Workbench>(path, `${refreshKey ?? ""}:${localRevision}`);
  const [suite, setSuite] = useState<Suite | null>(null);
  const [source, setSource] = useState(""), [testIndex, setTestIndex] = useState(0), [links, setLinks] = useState<Record<string, string[]>>({});
  const [issues, setIssues] = useState<string[]>([]), [savedDraft, setSavedDraft] = useState("");
  const [tab, setTab] = useState("editor"), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [runId, setRunId] = useState(""), [replay, setReplay] = useState<Replay | null>(null);
  const [runTest, setRunTest] = useState("");
  const request = useRef(0);
  const activeIssues = [...new Set([...issues, ...(issueContext ? [issueContext.id] : [])])];
  const currentDraft = JSON.stringify({ suite, links, issues: activeIssues });
  const dirty = currentDraft !== savedDraft;
  function loadPlan(plan: Plan) {
    setSuite(structuredClone(plan.suite)); setSource(plan.binding.plan_id); setLinks(structuredClone(plan.requirement_links));
    setIssues(plan.issue_ids); setTestIndex(0); setNotice("");
    setSavedDraft(JSON.stringify({ suite: plan.suite, links: plan.requirement_links, issues: plan.issue_ids }));
  }
  const initialized = useRef("");
  useEffect(() => {
    request.current++;
    initialized.current="";
    setBusy(false); setError(""); setNotice(""); setSuite(null); setReplay(null); setRunId(""); setSource(""); setIssues([]); setLinks({}); setSavedDraft("");
    return () => { request.current++; };
  }, [path, initialPlanId]);
  useEffect(() => {
    if (!data) return;
    const identity = `${path}:${initialPlanId || ""}`;
    if (initialized.current === identity) return;
    initialized.current = identity;
    const requested = initialPlanId ? data.plans.find(plan => plan.binding.plan_id === initialPlanId && plan.binding.version_id === vid) : data.plans[0];
    if (requested) { loadPlan(requested); if (initialPlanId) setTab("editor"); }
    else if (initialPlanId) setNotice(t("关联的方案不存在或已失效，请重新选择。"));
    else setSuite({ ...structuredClone(data.editor.empty_suite), name: t(data.editor.empty_suite.name) });
  }, [data, path, initialPlanId, vid]);
  useEffect(() => { if (readError) setError(readError); }, [readError]);
  useEffect(() => { if (issueContext) { setTab("editor"); setNotice(""); } }, [issueContext]);
  useEffect(() => {
    let active = true; setReplay(null);
    if (runId) api<Replay>(`${path}/runs/${encode(runId)}/replay`).then(value => { if (active) setReplay(value); }).catch(e => { if (active) setError(message(e)); });
    return () => { active = false; };
  }, [path, runId]);
  const disabled = readOnly || busy;
  const test = suite?.tests[testIndex];
  const addresses = data?.devices.map(d => d.address) || [];
  const writable = data?.devices.filter(d => d.writable).map(d => d.address) || [];
  function edit(change: (value: Suite) => void) { setSuite(old => { if (!old) return old; const next = structuredClone(old); change(next); return next; }); setNotice(""); }
  function editTest(change: (value: TestCase) => void) { edit(s => { if (s.tests[testIndex]) change(s.tests[testIndex]); }); }
  const draftPending = useRef(false);
  async function editCommand(command: Record<string, unknown>) {
    if (!suite || disabled || draftPending.current) return;
    const generation = request.current;
    draftPending.current = true; setBusy(true); setError("");
    try {
      const value = await api<{ suite: Suite }>(`${path}/draft`, "POST", { suite, command });
      if (request.current !== generation) return;
      setSuite(value.suite); setNotice("");
      if (command.action === "add_test") setTestIndex(value.suite.tests.length - 1);
    } catch (e) { if (request.current === generation) setError(message(e)); }
    finally { draftPending.current = false; if (request.current === generation) setBusy(false); }
  }
  function addTest() { void editCommand({ action: "add_test", name_prefix: t("测试") }); }
  function addStep() { void editCommand({ action: "add_step", test_index: testIndex }); }
  async function save() {
    if (!data || !suite || disabled || draftPending.current) return;
    draftPending.current = true;
    const generation = request.current; setBusy(true); setError("");
    try {
      const value = await api<Plan>(`${path}/plans`, "POST", { suite, requirement_links: links, issue_ids: activeIssues,
        source_plan_id: source || null, expected_ir_sha256: data.ir_sha256 });
      if (request.current !== generation) return;
      loadPlan(value); setNotice(t("已保存新方案。请检查保存后的步骤，执行遵循工作区审批设置。"));
      // One owner refreshes server evidence; saving never remounts this draft.
      if (onSaved) onSaved(); else setLocalRevision(n => n + 1);
    } catch (e) { if (request.current === generation) setError(message(e)); }
    finally { draftPending.current = false; if (request.current === generation) setBusy(false); }
  }
  async function execute() {
    if (disabled || draftPending.current) return;
    draftPending.current = true;
    setBusy(true); setError("");
    try { await onExecute(source); } catch (e) { setError(message(e)); } finally { draftPending.current = false; setBusy(false); }
  }
  async function debug() {
    if (!onDebug || !runId || disabled || draftPending.current) return;
    draftPending.current = true;
    setBusy(true); setError("");
    try { await onDebug(runId); } catch (e) { setError(message(e)); } finally { draftPending.current = false; setBusy(false); }
  }
  function openRun(run: RequirementRun) { setRunId(run.run_id); setRunTest(run.test_name); setTab("replay"); }
  function newPlan() { setSource(""); setSuite({ ...structuredClone(data!.editor.empty_suite), name: t(data!.editor.empty_suite.name) }); setLinks({}); setIssues([]); setSavedDraft(""); setNotice(""); }
  return <section className="simulation-workbench" aria-label={t("仿真工作台")}>
    <div className="sim-toolbar"><div><strong>{t("仿真工作台")}</strong><small>{t("编辑方案 → 确认执行 → 回放观测 → 对照需求")}</small></div>
      <div className="sim-tabs" role="tablist" aria-label={t("仿真工作台视图")}>{[["editor", "步骤编辑"], ["replay", "波形回放"], ["coverage", "需求覆盖"]].map(([key, name]) => <Button key={key} role="tab" aria-selected={tab === key} variant={tab === key ? "primary" : "ghost"} onClick={() => setTab(key)}>{t(name)}</Button>)}</div>
    </div>
    {error && <p role="alert" className="sim-error">{error}</p>}{notice && <p role="status" className="sim-notice">{notice}</p>}
    {!data && !error && <p>{t("正在读取版本绑定方案…")}</p>}
    {data && tab === "editor" && <>
      {issueContext && <aside className="sim-context"><strong>{t("复现问题")}：{issueContext.title}</strong><p>{issueContext.addresses.join(" · ")}</p><small>{t("保存时关联此问题。请依据证据填写预期行为。")}</small></aside>}
      <div className="sim-toolbar"><label>{t("已保存方案")}<select value={source} disabled={busy} onChange={e => { const p = data.plans.find(p => p.binding.plan_id === e.target.value); if (p) loadPlan(p); else newPlan(); }}><option value="">{t("新建方案")}</option>{data.plans.map(p => <option key={p.binding.plan_id} value={p.binding.plan_id}>{p.suite.name} · {p.binding.plan_id.slice(-8)}</option>)}</select></label>
        <Button disabled={disabled} onClick={newPlan}><Plus size={14} />{t("新建方案")}</Button></div>
      {data.unavailable_plans.map(p => <p className="muted" key={p.plan_id}>{p.plan_id}：{t(p.message)}</p>)}
      {suite && <><label>{t("方案名称")}<input disabled={disabled} value={suite.name} onChange={e => edit(s => { s.name = e.target.value; })} /></label>
        <div className="sim-editor"><aside className="sim-test-list">{suite.tests.map((item, index) => <Button key={index} variant={index === testIndex ? "primary" : "ghost"} onClick={() => setTestIndex(index)}>{index + 1}. {item.name}</Button>)}<Button disabled={disabled} onClick={addTest}><Plus size={14} />{t("添加测试")}</Button></aside>
        <div className="sim-test-detail">{!test ? <p>{t("添加一个测试，再依次设置初始输入、触发时刻与预期结果。")}</p> : <>
          <div className="sim-toolbar"><label>{t("测试名称")}<input disabled={disabled} value={test.name} onChange={e => { const name = e.target.value; setLinks(old => { const next = { ...old, [name]: old[test.name] || [] }; delete next[test.name]; return next; }); editTest(v => { v.name = name; }); }} /></label>
            <Button disabled={disabled} aria-label={t("删除测试")} onClick={() => { setLinks(old => { const next = { ...old }; delete next[test.name]; return next; }); edit(s => s.tests.splice(testIndex, 1)); setTestIndex(Math.max(0, testIndex - 1)); }}><Trash2 size={15} />{t("删除测试")}</Button></div>
          <label>{t("验证目的")}<textarea disabled={disabled} value={test.description || ""} onChange={e => editTest(v => { v.description = e.target.value; })} /></label>
          <div className="sim-timings"><label>{t("采样间隔 ms")}<input type="number" min={data.editor.minimum_interval_ms} disabled={disabled} value={test.sample_ms} onChange={e => editTest(v => { v.sample_ms = Number(e.target.value); })} /></label><label>{t("总超时 ms")}<input type="number" min={data.editor.minimum_interval_ms} max={data.editor.maximum_duration_ms} disabled={disabled} value={test.timeout_ms} onChange={e => editTest(v => { v.timeout_ms = Number(e.target.value); })} /></label></div>
          <fieldset disabled={disabled}><legend>{t("初始输入")}</legend><DeviceValues defaultValue={data.editor.default_input_value} values={test.initial} addresses={writable} onChange={values => editTest(v => { v.initial = values; })} t={t} /><small>{t("每个要改变的输入都必须在此定义初始值。")}</small></fieldset>
          <fieldset disabled={disabled}><legend>{t("记录软元件")}</legend><div className="sim-checks">{addresses.map(a => <label key={a}><input type="checkbox" checked={test.trace_devices.includes(a)} onChange={e => editTest(v => { v.trace_devices = e.target.checked ? [...v.trace_devices, a] : v.trace_devices.filter(d => d !== a); })} />{a}</label>)}</div><small>{t("输入和断言涉及的地址会自动加入记录。")}</small></fieldset>
          <fieldset disabled={disabled}><legend>{t("关联本版本需求")}</legend>{data.requirements.length ? <div className="sim-checks">{data.requirements.map(r => <label key={r.id}><input type="checkbox" checked={(links[test.name] || []).includes(r.id)} onChange={e => setLinks(old => ({ ...old, [test.name]: e.target.checked ? [...(old[test.name] || []), r.id] : (old[test.name] || []).filter(id => id !== r.id) }))} />{r.id} · {r.text}</label>)}</div> : <p>{t("此版本未记录结构化需求；可以先编辑并执行测试。")}</p>}</fieldset>
          {test.steps.map((step, index) => <fieldset disabled={disabled} className="sim-step" key={step.id}><legend>{t("步骤")} {index + 1} · {step.id}</legend>
            <div className="sim-toolbar"><label>{t("触发时刻 ms")}<input type="number" min={0} value={step.at_ms} onChange={e => editTest(v => { v.steps[index].at_ms = Number(e.target.value); })} /></label><div className="sim-actions"><Button disabled={disabled || index === 0} aria-label={t("上移步骤")} onClick={() => editTest(v => { [v.steps[index - 1], v.steps[index]] = [v.steps[index], v.steps[index - 1]]; })}><ArrowUp size={14} /></Button><Button disabled={disabled || index === test.steps.length - 1} aria-label={t("下移步骤")} onClick={() => editTest(v => { [v.steps[index + 1], v.steps[index]] = [v.steps[index], v.steps[index + 1]]; })}><ArrowDown size={14} /></Button><Button disabled={disabled} aria-label={t("删除步骤")} onClick={() => editTest(v => { v.steps.splice(index, 1); })}><Trash2 size={14} /></Button></div></div>
            <strong>{t("改变输入")}</strong><DeviceValues defaultValue={data.editor.default_input_value} addresses={writable} values={step.set} onChange={values => editTest(v => { v.steps[index].set = values; })} t={t} />
            <strong>{t("立即断言")}</strong><Expectations metadata={data.editor} values={step.expect} addresses={addresses} onChange={values => editTest(v => { v.steps[index].expect = values; })} t={t} />
            <strong>{t("等待条件满足")}</strong><Expectations metadata={data.editor} values={step.wait_for} addresses={addresses} onChange={values => editTest(v => { v.steps[index].wait_for = values; v.steps[index].timeout_ms ??= data.editor.wait_timeout_ms; v.steps[index].poll_ms ??= data.editor.poll_ms; })} t={t} />
            {step.wait_for.length > 0 && <div className="sim-timings"><label>{t("等待超时 ms")}<input type="number" min={data.editor.minimum_interval_ms} value={step.timeout_ms ?? data.editor.wait_timeout_ms} onChange={e => editTest(v => { v.steps[index].timeout_ms = Number(e.target.value); })} /></label><label>{t("轮询间隔 ms")}<input type="number" min={data.editor.minimum_interval_ms} value={step.poll_ms ?? data.editor.poll_ms} onChange={e => editTest(v => { v.steps[index].poll_ms = Number(e.target.value); })} /></label></div>}
          </fieldset>)}
          <Button disabled={disabled} onClick={addStep}><Plus size={14} />{t("添加步骤")}</Button><p className="muted">{t("时刻相对于测试起点；较晚时刻表示等待到该时刻。步骤须按时间排列。")}</p>
          {!!((test.invariants?.length || 0) + (test.fault_injections?.length || 0)) && <details><summary>{t("已有不变量与故障注入（保存时保留）")}</summary><pre>{JSON.stringify({ invariants: test.invariants, fault_injections: test.fault_injections }, null, 2)}</pre></details>}
        </>}</div></div>
        <footer className="sim-toolbar"><p>{t("保存会创建新方案，原方案及其审批记录保留。")}{dirty && <strong> · {t("有未保存修改")}</strong>}</p><div className="sim-actions"><Button variant="primary" disabled={disabled || !suite.tests.length} onClick={() => void save()}><Save size={14} />{t("保存为新方案")}</Button><Button disabled={disabled || !source || dirty} onClick={() => void execute()}><Play size={14} />{t("提交执行确认")}</Button></div></footer>
      </>}
    </>}
    {data && tab === "replay" && <><label>{t("本版本运行记录")}<select value={runId} onChange={e => { setRunId(e.target.value); setRunTest(""); }}><option value="">{t("选择运行记录")}</option>{data.runs.map(r => <option key={r.run_id} value={r.run_id}>{r.suite_name} · {t(statusLabel(r.status))} · {r.created_at}</option>)}</select></label>{!data.runs.length && <p>{t("尚无运行记录。保存方案并确认执行后，可在此回放。")}</p>}{onDebug && runId && <Button disabled={disabled || replay?.verification.program_repair_allowed !== true} onClick={() => void debug()}>{t("准备调试方案")}</Button>}{replay && <WaveformReplay replay={replay} initialTestName={runTest} t={t} />}</>}
    {data && tab === "coverage" && <>
      <p>{t("下表统计本版本需求与已保存测试的关联。关联和测试通过均不代表需求已被完整验证；运行结果请结合断言和采样证据检查。")}</p>
      {!!data.unavailable_runs?.length && <p className="sim-error" role="alert">{t("部分运行证据无法核验，以下仅显示可核验记录。")} {data.unavailable_runs.join(" · ")}</p>}
      <table><thead><tr><th>{t("需求")}</th><th>{t("已关联测试")}</th><th>{t("最近可核验运行")}</th></tr></thead><tbody>{data.requirements.map(r => <tr key={r.id}>
        <td><strong>{r.id}</strong><p>{r.text}</p><small>{r.devices.join(" · ")}</small><p>{t(r.status === "linked" ? "已关联，待核实验证充分性" : "未关联测试")}</p></td>
        <td>{r.tests.map(test => <div className="sim-coverage-test" key={`${test.plan_id}:${test.test_name}`}><Button variant="ghost" onClick={() => { const plan = data.plans.find(p => p.binding.plan_id === test.plan_id); if (plan) { loadPlan(plan); setTestIndex(plan.suite.tests.findIndex(v => v.name === test.test_name)); setTab("editor"); } }}>{test.test_name} · {test.plan_id.slice(-8)}</Button>
          {test.latest_run ? <><small>{t("已核验记录")} · {t(statusLabel(test.latest_run.status))} · {test.latest_run.backend_kind || t("未知")}</small><Button variant="ghost" title={t("打开关联运行波形")} onClick={() => openRun(test.latest_run!)}>{test.latest_run.run_id}</Button></> : <small>{t("未运行")}</small>}</div>)}</td>
        <td>{r.latest_run ? <><strong>{t(statusLabel(r.latest_run.status))}</strong><p>{r.latest_run.test_name} · {r.latest_run.backend_kind || t("未知")}</p><small>{r.latest_run.created_at}</small><Button onClick={() => openRun(r.latest_run!)}>{t("打开关联运行波形")}</Button><small>{r.latest_run.run_id}</small></> : t("未运行")}</td>
      </tr>)}</tbody></table>{!data.requirements.length && <p>{t("此版本未记录结构化需求。")}</p>}
    </>}
  </section>;
}

function DeviceValues({ values, addresses, onChange, defaultValue, t }: { defaultValue: number; values: Record<string, number>; addresses: string[]; onChange: (v: Record<string, number>) => void; t: (s: string) => string }) {
  const available = addresses.filter(a => !(a in values));
  return <div className="sim-value-editor">{Object.entries(values).map(([address, value]) => <div className="sim-value-row" key={address}><select aria-label={t("输入地址")} value={address} onChange={e => { const next = { ...values }; delete next[address]; next[e.target.value] = value; onChange(next); }}>{[address, ...available].map(a => <option key={a}>{a}</option>)}</select><input aria-label={`${address} ${t("输入值")}`} type="number" value={value} onChange={e => onChange({ ...values, [address]: Number(e.target.value) })} /><Button aria-label={`${t("删除输入")} ${address}`} onClick={() => { const next = { ...values }; delete next[address]; onChange(next); }}><Trash2 size={13} /></Button></div>)}<Button disabled={!available.length} onClick={() => onChange({ ...values, [available[0]]: defaultValue })}><Plus size={13} />{t("添加输入")}</Button></div>;
}

function Expectations({ values, addresses, onChange, metadata, t }: { metadata: EditorMetadata; values: Expectation[]; addresses: string[]; onChange: (v: Expectation[]) => void; t: (s: string) => string }) {
  function edit(index: number, field: keyof Expectation, value: unknown) { const next = structuredClone(values); Object.assign(next[index], { [field]: value }); onChange(next); }
  return <div className="sim-value-editor">{values.map((value, index) => <div className="sim-expect-row" key={index}><select aria-label={t("断言地址")} value={value.address} onChange={e => edit(index, "address", e.target.value)}><option value="">{t("选择地址")}</option>{addresses.map(a => <option key={a}>{a}</option>)}</select><select aria-label={t("比较方式")} value={value.operator} onChange={e => { const next = structuredClone(values); next[index].operator = e.target.value; next[index].value = metadata.operators.find(op => op.value === e.target.value)!.initial_value; onChange(next); }}>{metadata.operators.map(op => <option key={op.value} value={op.value}>{t(op.label)}</option>)}</select><input aria-label={t("期望值")} placeholder={t(metadata.operators.find(op => op.value === value.operator)?.placeholder || "")} value={Array.isArray(value.value) ? value.value.join(",") : String(value.value ?? "")} onChange={e => edit(index, "value", e.target.value)} />{value.tolerance !== undefined && <input aria-label={t("允许误差")} type="number" min={0} value={value.tolerance} onChange={e => edit(index, "tolerance", Number(e.target.value))} />}<Button aria-label={t("删除断言")} onClick={() => onChange(values.filter((_, i) => i !== index))}><Trash2 size={13} /></Button></div>)}<Button onClick={() => onChange([...values, structuredClone(metadata.default_expectation)])}><Plus size={13} />{t("添加条件")}</Button></div>;
}

function WaveformReplay({ replay, initialTestName, t }: { replay: Replay; initialTestName?: string; t: (s: string) => string }) {
  const visible=usePanelVisible();
  const [caseIndex, setCaseIndex] = useState(0), [cursor, setCursor] = useState(0), [playing, setPlaying] = useState(false);
  const current = replay.cases[caseIndex], samples = current?.observations || [];
  useEffect(() => { setCaseIndex(Math.max(0, replay.cases.findIndex(c => c.name === initialTestName))); setCursor(0); setPlaying(false); }, [replay.run_id, initialTestName]);
  useEffect(() => { setCursor(0); setPlaying(false); }, [caseIndex]);
  useEffect(() => { if (!playing || !visible) return; const timer = window.setInterval(() => setCursor(old => { if (old >= samples.length - 1) { setPlaying(false); return old; } return old + 1; }), 200); return () => window.clearInterval(timer); }, [playing, samples.length, visible]);
  const selected = samples[cursor], end = Math.max(samples.at(-1)?.at_ms || 1, 1);
  const graphs = useMemo(() => [...new Set(samples.flatMap(row => Object.keys(row.values)))].map(address => {
    const numeric = samples.map(s => s.values[address]).filter(v => typeof v === "number") as number[];
    const min = numeric.reduce((a, b) => Math.min(a, b), 0), max = numeric.reduce((a, b) => Math.max(a, b), 1), span = max - min || 1;
    let path = "", previous: number | null = null;
    samples.forEach(sample => { const value = sample.values[address], x = 10 + sample.at_ms / end * 770; if (typeof value !== "number" || !Number.isFinite(value)) { previous = null; return; } const y = 44 - (value - min) / span * 34; path += previous === null ? ` M ${x} ${y}` : ` H ${x} V ${y}`; previous = value; });
    return { address, min, max, path };
  }), [samples, end]);
  return <div className="sim-replay"><p>{t("验收状态")}：{t(statusLabel(replay.verification.status))}{replay.verification.category ? ` · ${replay.verification.category}` : ""}</p><label>{t("回放测试")}<select value={caseIndex} onChange={e => setCaseIndex(Number(e.target.value))}>{replay.cases.map((c, i) => <option key={c.name} value={i}>{c.name} · {t(statusLabel(c.status))}</option>)}</select></label>
    {current && <><p>{t("执行后端")}：{current.backend_kind} · {t("采样间隔 ms")}：{current.sample_ms}</p><p className="muted">{t("波形只展示记录中的读取值；连线表示相邻采样之间保持前值，不能证明采样间没有瞬态变化。播放按记录逐点前进。")}</p>
      {!samples.length ? <p>{t("此运行未记录可回放的观测数据。")}</p> : <><div className="sim-toolbar"><Button onClick={() => { if (cursor >= samples.length - 1) setCursor(0); setPlaying(!playing); }}>{t(playing ? "暂停" : "播放")}</Button><input aria-label={t("回放采样位置")} type="range" min={0} max={samples.length - 1} value={cursor} onChange={e => { setPlaying(false); setCursor(Number(e.target.value)); }} /><output>{selected?.at_ms} ms · {cursor + 1}/{samples.length}</output></div>
        <div className="sim-waveforms">{graphs.map(({address, min, max, path}) => {
          return <div className="sim-wave-row" key={address}><div><strong>{address}</strong><small>{selected && address in selected.values ? String(selected.values[address] ?? t("未知")) : t("未知")}</small></div><svg viewBox="0 0 790 54" role="img" aria-label={`${address} ${t("记录波形")}`}><line x1={10} y1={44} x2={780} y2={44} className="sim-grid" /><path d={path} className="sim-signal" /><line x1={10 + (selected?.at_ms || 0) / end * 770} y1={0} x2={10 + (selected?.at_ms || 0) / end * 770} y2={54} className="sim-cursor" /></svg><small>{min}…{max}</small></div>;
        })}</div></>}
      <p>{t("本测试关联需求")}：{current.requirement_ids.join(" · ") || t("未关联")}</p>
      <table><thead><tr><th>{t("断言时刻")}</th><th>{t("地址 / 步骤")}</th><th>{t("实际值")}</th><th>{t("期望")}</th><th>{t("结果")}</th></tr></thead><tbody>{current.assertions.map((a, i) => <tr key={i}><td><Button variant="ghost" onClick={() => { const index = samples.findIndex(s => s.at_ms >= (a.at_ms || 0)); if (index >= 0) setCursor(index); setPlaying(false); }}>{a.at_ms ?? "—"} ms</Button></td><td>{a.address || a.step_id || "—"}</td><td>{String(a.actual ?? "—")}</td><td>{typeof a.expected === "object" ? JSON.stringify(a.expected) : String(a.expected ?? "—")}</td><td>{t(a.passed ? "通过" : "失败")}</td></tr>)}</tbody></table>
      {!!current.invariant_violations.length && <details><summary>{t("运行不变量未满足")}</summary><pre>{JSON.stringify(current.invariant_violations, null, 2)}</pre></details>}
    </>}
  </div>;
}
