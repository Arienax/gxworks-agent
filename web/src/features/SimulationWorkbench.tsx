import { ArrowDown, ArrowUp, Play, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SetStateAction } from "react";
import { acceptDraft, editDraft, savedDraft as baselineDraft } from "../lifecycle/drafts";
import { api } from "../api/client";
import { Button } from "../components/ui";
import { equalValue } from "../lifecycle/requests";
import { useResource } from "../lifecycle/useResource";
import "./simulation.css";
import { reorderStep, updateStep, updateTest } from "./simulationDraft";
import { DeviceValues, Expectations } from "./SimulationFields";
import { WaveformReplay } from "./SimulationReplay";
import type { RequirementRun } from "./simulationTypes";
import { SimulatorTimeline, SimulatorTimelineFeed } from "./SimulatorTimeline";
import type { TraceState } from "./simulatorTimelineData";

import type {
  Plan,
  Replay,
  SimulationIssueContext,
  Step,
  Suite,
  TestCase,
  Workbench,
} from "./simulationTypes";
export type { SimulationIssueContext } from "./simulationTypes";
type Props = {
  pid: string;
  vid: string;
  readOnly: boolean;
  t: (s: string) => string;
  onExecute: (planId: string) => Promise<void>;
  onDebug?: (runId: string) => Promise<void>;
  onSaved?: () => void;
  issueContext?: SimulationIssueContext;
  initialPlanId?: string;
  refreshKey?: string | number;
};

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const statusLabel = (status: string) =>
  ({
    passed: "通过",
    failed: "失败",
    error: "执行出错",
    unavailable: "环境不可用",
    blocked: "证据未获验收",
  })[status] || status;
const encode = encodeURIComponent;
const requestTrace = (path: string) => api<TraceState | null>(path);

export function SimulationWorkbench({
  pid,
  vid,
  readOnly,
  t,
  onExecute,
  onDebug,
  onSaved,
  issueContext,
  initialPlanId,
  refreshKey,
}: Props) {
  const path = `/projects/${encode(pid)}/versions/${encode(vid)}/simulation-workbench`;
  const [localRevision, setLocalRevision] = useState(0);
  const { value: data, error: readError } = useResource<Workbench>(
    path,
    `${refreshKey ?? ""}:${localRevision}`,
  );
  const [suiteDraft, setSuiteDraft] = useState(() => baselineDraft<Suite | null>(null));
  const suite = suiteDraft.value;
  function setSuite(value: SetStateAction<Suite | null>) {
    setSuiteDraft((old) => editDraft(old, typeof value === "function" ? value(old.value) : value));
  }
  const [source, setSource] = useState(""),
    [testIndex, setTestIndex] = useState(0),
    [links, setLinks] = useState<Record<string, string[]>>({});
  const [issues, setIssues] = useState<string[]>([]),
    [savedDraft, setSavedDraft] = useState<{
      links: Record<string, string[]>;
      issues: string[];
    } | null>(null);
  const [tab, setTab] = useState("editor"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [runId, setRunId] = useState(""),
    [replay, setReplay] = useState<Replay | null>(null);
  const [runTest, setRunTest] = useState("");
  const request = useRef(0);
  const activeIssues = useMemo(
    () => [...new Set([...issues, ...(issueContext ? [issueContext.id] : [])])],
    [issues, issueContext?.id],
  );
  const dirty = useMemo(
    () =>
      !savedDraft ||
      !!suiteDraft.changes ||
      !equalValue(links, savedDraft.links) ||
      !equalValue(activeIssues, savedDraft.issues),
    [suiteDraft.changes, links, activeIssues, savedDraft],
  );
  function loadPlan(plan: Plan) {
    setSuiteDraft(baselineDraft(plan.suite));
    setSource(plan.binding.plan_id);
    setLinks(plan.requirement_links);
    setIssues(plan.issue_ids);
    setTestIndex(0);
    setNotice("");
    setSavedDraft({ links: plan.requirement_links, issues: plan.issue_ids });
  }
  const initialized = useRef("");
  useEffect(() => {
    request.current++;
    initialized.current = "";
    setBusy(false);
    setError("");
    setNotice("");
    setSuite(null);
    setReplay(null);
    setRunId("");
    setSource("");
    setIssues([]);
    setLinks({});
    setSavedDraft(null);
    return () => {
      request.current++;
    };
  }, [path, initialPlanId]);
  useEffect(() => {
    if (!data) return;
    const identity = `${path}:${initialPlanId || ""}`;
    if (initialized.current === identity) return;
    initialized.current = identity;
    const requested = initialPlanId
      ? data.plans.find(
          (plan) => plan.binding.plan_id === initialPlanId && plan.binding.version_id === vid,
        )
      : data.plans[0];
    if (requested) {
      loadPlan(requested);
      if (initialPlanId) setTab("editor");
    } else if (initialPlanId) setNotice(t("关联的方案不存在或已失效，请重新选择。"));
    else
      setSuite({
        ...structuredClone(data.editor.empty_suite),
        name: t(data.editor.empty_suite.name),
      });
  }, [data, path, initialPlanId, vid]);
  useEffect(() => {
    if (readError) setError(readError);
  }, [readError]);
  useEffect(() => {
    if (issueContext) {
      setTab("editor");
      setNotice("");
    }
  }, [issueContext]);
  useEffect(() => {
    let active = true;
    setReplay(null);
    if (runId)
      api<Replay>(`${path}/runs/${encode(runId)}/replay`)
        .then((value) => {
          if (active) setReplay(value);
        })
        .catch((e) => {
          if (active) setError(message(e));
        });
    return () => {
      active = false;
    };
  }, [path, runId]);
  const disabled = readOnly || busy;
  const test = suite?.tests[testIndex];
  const preservedConditions = useMemo(
    () => JSON.stringify({ invariants: test?.invariants, fault_injections: test?.fault_injections }, null, 2),
    [test?.invariants, test?.fault_injections],
  );
  const addresses = data?.devices.map((d) => d.address) || [];
  const writable = data?.devices.filter((d) => d.writable).map((d) => d.address) || [];
  function edit(change: (value: Suite) => Suite) {
    setSuite((old) => (old ? change(old) : old));
    setNotice("");
  }
  function editTest(change: (value: TestCase) => TestCase) {
    edit((suite) => updateTest(suite, testIndex, change));
  }
  function editStep(index: number, change: (value: Step) => Step) {
    editTest((test) => updateStep(test, index, change));
  }
  function moveStep(index: number, offset: number) {
    editTest((test) => reorderStep(test, index, offset));
  }
  const draftPending = useRef(false);
  async function editCommand(command: Record<string, unknown>) {
    if (!suite || disabled || draftPending.current) return;
    const generation = request.current;
    draftPending.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await api<{ suite: Suite }>(`${path}/draft`, "POST", { suite, command });
      if (request.current !== generation) return;
      setSuite(value.suite);
      setNotice("");
      if (command.action === "add_test") setTestIndex(value.suite.tests.length - 1);
    } catch (e) {
      if (request.current === generation) setError(message(e));
    } finally {
      draftPending.current = false;
      if (request.current === generation) setBusy(false);
    }
  }
  function addTest() {
    void editCommand({ action: "add_test", name_prefix: t("测试") });
  }
  function addStep() {
    void editCommand({ action: "add_step", test_index: testIndex });
  }
  async function save() {
    if (!data || !suite || disabled || draftPending.current) return;
    draftPending.current = true;
    const generation = request.current;
    setBusy(true);
    setError("");
    try {
      const value = await api<Plan>(`${path}/plans`, "POST", {
        suite,
        requirement_links: links,
        issue_ids: activeIssues,
        source_plan_id: source || null,
        expected_ir_sha256: data.ir_sha256,
      });
      if (request.current !== generation) return;
      setSuiteDraft((draft) => acceptDraft(draft, suite, value.suite));
      setLinks((current) => current === links ? value.requirement_links : current);
      setIssues((current) => current === issues ? value.issue_ids : current);
      setSource(value.binding.plan_id);
      setSavedDraft({ links: value.requirement_links, issues: value.issue_ids });
      setNotice(t("已保存新方案。请检查保存后的步骤，执行遵循工作区审批设置。"));
      // One owner refreshes server evidence; saving never remounts this draft.
      if (onSaved) onSaved();
      else setLocalRevision((n) => n + 1);
    } catch (e) {
      if (request.current === generation) setError(message(e));
    } finally {
      draftPending.current = false;
      if (request.current === generation) setBusy(false);
    }
  }
  async function execute() {
    if (disabled || draftPending.current) return;
    draftPending.current = true;
    setBusy(true);
    setError("");
    try {
      setTab("live");
      await onExecute(source);
    } catch (e) {
      setError(message(e));
    } finally {
      draftPending.current = false;
      setBusy(false);
    }
  }
  async function debug() {
    if (!onDebug || !runId || disabled || draftPending.current) return;
    draftPending.current = true;
    setBusy(true);
    setError("");
    try {
      await onDebug(runId);
    } catch (e) {
      setError(message(e));
    } finally {
      draftPending.current = false;
      setBusy(false);
    }
  }
  function openRun(run: RequirementRun) {
    setRunId(run.run_id);
    setRunTest(run.test_name);
    setTab("replay");
  }
  function newPlan() {
    setSource("");
    setSuite({
      ...structuredClone(data!.editor.empty_suite),
      name: t(data!.editor.empty_suite.name),
    });
    setLinks({});
    setIssues([]);
    setSavedDraft(null);
    setNotice("");
  }
  return (
    <section className="simulation-workbench" aria-label={t("仿真工作台")}>
      <div className="sim-toolbar">
        <div>
          <strong>{t("仿真工作台")}</strong>
        </div>
        <div className="sim-tabs" role="tablist" aria-label={t("仿真工作台视图")}>
          {[
            ["editor", "步骤编辑"],
            ["live", "实时总览"],
            ["replay", "波形回放"],
            ["coverage", "需求覆盖"],
          ].map(([key, name]) => (
            <Button
              key={key}
              role="tab"
              aria-selected={tab === key}
              variant={tab === key ? "primary" : "ghost"}
              onClick={() => setTab(key)}
            >
              {t(name)}
            </Button>
          ))}
        </div>
      </div>
      {error && (
        <p role="alert" className="sim-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="sim-notice">
          {notice}
        </p>
      )}
      {!data && !error && <p>{t("正在读取…")}</p>}
      {tab === "live" && <SimulatorTimelineFeed endpoint={`${path}/live`} request={requestTrace} />}
      {data && tab === "editor" && (
        <>
          {issueContext && (
            <aside className="sim-context">
              <strong>
                {t("复现问题")}：{issueContext.title}
              </strong>
              <p>{issueContext.addresses.join(" · ")}</p>
            </aside>
          )}
          <div className="sim-toolbar">
            <label>
              {t("已保存方案")}
              <select
                value={source}
                disabled={busy}
                onChange={(e) => {
                  const p = data.plans.find((p) => p.binding.plan_id === e.target.value);
                  if (p) loadPlan(p);
                  else newPlan();
                }}
              >
                <option value="">{t("新建方案")}</option>
                {data.plans.map((p) => (
                  <option key={p.binding.plan_id} value={p.binding.plan_id}>
                    {p.suite.name} · {p.binding.plan_id.slice(-8)}
                  </option>
                ))}
              </select>
            </label>
            <Button disabled={disabled} onClick={newPlan}>
              <Plus size={14} />
              {t("新建方案")}
            </Button>
          </div>
          {data.unavailable_plans.map((p) => (
            <p className="muted" key={p.plan_id}>
              {p.plan_id}：{t(p.message)}
            </p>
          ))}
          {suite && (
            <>
              <label>
                {t("方案名称")}
                <input
                  disabled={disabled}
                  value={suite.name}
                  onChange={(e) => edit((s) => ({ ...s, name: e.target.value }))}
                />
              </label>
              <div className="sim-editor">
                <aside className="sim-test-list">
                  {suite.tests.map((item, index) => (
                    <Button
                      key={index}
                      variant={index === testIndex ? "primary" : "ghost"}
                      onClick={() => setTestIndex(index)}
                    >
                      {index + 1}. {item.name}
                    </Button>
                  ))}
                  <Button disabled={disabled} onClick={addTest}>
                    <Plus size={14} />
                    {t("添加测试")}
                  </Button>
                </aside>
                <div className="sim-test-detail">
                  {!test ? (
                    <p>{t("暂无测试")}</p>
                  ) : (
                    <>
                      <div className="sim-toolbar">
                        <label>
                          {t("测试名称")}
                          <input
                            disabled={disabled}
                            value={test.name}
                            onChange={(e) => {
                              const name = e.target.value;
                              setLinks((old) => {
                                const next = { ...old, [name]: old[test.name] || [] };
                                delete next[test.name];
                                return next;
                              });
                              editTest((v) => ({ ...v, name }));
                            }}
                          />
                        </label>
                        <Button
                          disabled={disabled}
                          aria-label={t("删除测试")}
                          onClick={() => {
                            setLinks((old) => {
                              const next = { ...old };
                              delete next[test.name];
                              return next;
                            });
                            edit((s) => ({
                              ...s,
                              tests: s.tests.filter((_, i) => i !== testIndex),
                            }));
                            setTestIndex(Math.max(0, testIndex - 1));
                          }}
                        >
                          <Trash2 size={15} />
                          {t("删除测试")}
                        </Button>
                      </div>
                      <label>
                        {t("验证目的")}
                        <textarea
                          disabled={disabled}
                          value={test.description || ""}
                          onChange={(e) => editTest((v) => ({ ...v, description: e.target.value }))}
                        />
                      </label>
                      <div className="sim-timings">
                        <label>
                          {t("采样间隔 ms")}
                          <input
                            type="number"
                            min={data.editor.minimum_interval_ms}
                            disabled={disabled}
                            value={test.sample_ms}
                            onChange={(e) =>
                              editTest((v) => ({ ...v, sample_ms: Number(e.target.value) }))
                            }
                          />
                        </label>
                        <label>
                          {t("总超时 ms")}
                          <input
                            type="number"
                            min={data.editor.minimum_interval_ms}
                            max={data.editor.maximum_duration_ms}
                            disabled={disabled}
                            value={test.timeout_ms}
                            onChange={(e) =>
                              editTest((v) => ({ ...v, timeout_ms: Number(e.target.value) }))
                            }
                          />
                        </label>
                      </div>
                      <fieldset disabled={disabled}>
                        <legend>{t("初始输入")}</legend>
                        <DeviceValues
                          defaultValue={data.editor.default_input_value}
                          values={test.initial}
                          addresses={writable}
                          onChange={(values) => editTest((v) => ({ ...v, initial: values }))}
                          t={t}
                        />
                      </fieldset>
                      <fieldset disabled={disabled}>
                        <legend>{t("记录软元件")}</legend>
                        <div className="sim-checks">
                          {addresses.map((a) => (
                            <label key={a}>
                              <input
                                type="checkbox"
                                checked={test.trace_devices.includes(a)}
                                onChange={(e) =>
                                  editTest((v) => ({
                                    ...v,
                                    trace_devices: e.target.checked
                                      ? [...v.trace_devices, a]
                                      : v.trace_devices.filter((d) => d !== a),
                                  }))
                                }
                              />
                              {a}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      <fieldset disabled={disabled}>
                        <legend>{t("关联本版本需求")}</legend>
                        {data.requirements.length ? (
                          <div className="sim-checks">
                            {data.requirements.map((r) => (
                              <label key={r.id}>
                                <input
                                  type="checkbox"
                                  checked={(links[test.name] || []).includes(r.id)}
                                  onChange={(e) =>
                                    setLinks((old) => ({
                                      ...old,
                                      [test.name]: e.target.checked
                                        ? [...(old[test.name] || []), r.id]
                                        : (old[test.name] || []).filter((id) => id !== r.id),
                                    }))
                                  }
                                />
                                {r.id} · {r.text}
                              </label>
                            ))}
                          </div>
                        ) : (
                          <p>{t("此版本未记录需求")}</p>
                        )}
                      </fieldset>
                      {test.steps.map((step, index) => (
                        <fieldset disabled={disabled} className="sim-step" key={step.id}>
                          <legend>
                            {t("步骤")} {index + 1}
                          </legend>
                          <div className="sim-toolbar">
                            <label>
                              {t("测试开始后 ms")}
                              <input
                                type="number"
                                min={0}
                                value={step.at_ms}
                                onChange={(e) =>
                                  editStep(index, (v) => ({ ...v, at_ms: Number(e.target.value) }))
                                }
                              />
                            </label>
                            <div className="sim-actions">
                              <Button
                                disabled={disabled || index === 0}
                                aria-label={t("上移步骤")}
                                onClick={() => moveStep(index, -1)}
                              >
                                <ArrowUp size={14} />
                              </Button>
                              <Button
                                disabled={disabled || index === test.steps.length - 1}
                                aria-label={t("下移步骤")}
                                onClick={() => moveStep(index, 1)}
                              >
                                <ArrowDown size={14} />
                              </Button>
                              <Button
                                disabled={disabled}
                                aria-label={t("删除步骤")}
                                onClick={() =>
                                  editTest((v) => ({
                                    ...v,
                                    steps: v.steps.filter((_, i) => i !== index),
                                  }))
                                }
                              >
                                <Trash2 size={14} />
                              </Button>
                            </div>
                          </div>
                          <strong>{t("改变输入")}</strong>
                          <DeviceValues
                            defaultValue={data.editor.default_input_value}
                            addresses={writable}
                            values={step.set}
                            onChange={(values) => editStep(index, (v) => ({ ...v, set: values }))}
                            t={t}
                          />
                          <strong>{t("立即检查")}</strong>
                          <Expectations
                            metadata={data.editor}
                            values={step.expect}
                            addresses={addresses}
                            onChange={(values) =>
                              editStep(index, (v) => ({ ...v, expect: values }))
                            }
                            t={t}
                          />
                          <strong>{t("等待条件满足")}</strong>
                          <Expectations
                            metadata={data.editor}
                            values={step.wait_for}
                            addresses={addresses}
                            onChange={(values) =>
                              editStep(index, (v) => ({
                                ...v,
                                wait_for: values,
                                timeout_ms: v.timeout_ms ?? data.editor.wait_timeout_ms,
                                poll_ms: v.poll_ms ?? data.editor.poll_ms,
                              }))
                            }
                            t={t}
                          />
                          {step.wait_for.length > 0 && (
                            <div className="sim-timings">
                              <label>
                                {t("等待超时 ms")}
                                <input
                                  type="number"
                                  min={data.editor.minimum_interval_ms}
                                  value={step.timeout_ms ?? data.editor.wait_timeout_ms}
                                  onChange={(e) =>
                                    editStep(index, (v) => ({
                                      ...v,
                                      timeout_ms: Number(e.target.value),
                                    }))
                                  }
                                />
                              </label>
                              <label>
                                {t("轮询间隔 ms")}
                                <input
                                  type="number"
                                  min={data.editor.minimum_interval_ms}
                                  value={step.poll_ms ?? data.editor.poll_ms}
                                  onChange={(e) =>
                                    editStep(index, (v) => ({
                                      ...v,
                                      poll_ms: Number(e.target.value),
                                    }))
                                  }
                                />
                              </label>
                            </div>
                          )}
                        </fieldset>
                      ))}
                      <Button disabled={disabled} onClick={addStep}>
                        <Plus size={14} />
                        {t("添加步骤")}
                      </Button>
                      {!!(
                        (test.invariants?.length || 0) + (test.fault_injections?.length || 0)
                      ) && (
                        <details>
                          <summary>{t("已有不变量与故障注入（保存时保留）")}</summary>
                          <pre>{preservedConditions}</pre>
                        </details>
                      )}
                    </>
                  )}
                </div>
              </div>
              <footer className="sim-toolbar">
                <p>{dirty && <strong>{t("有未保存修改")}</strong>}</p>
                <div className="sim-actions">
                  <Button
                    variant="primary"
                    disabled={disabled || !suite.tests.length}
                    onClick={() => void save()}
                  >
                    <Save size={14} />
                    {t("保存为新方案")}
                  </Button>
                  <Button disabled={disabled || !source || dirty} onClick={() => void execute()}>
                    <Play size={14} />
                    {t("提交执行确认")}
                  </Button>
                </div>
              </footer>
            </>
          )}
        </>
      )}
      {data && tab === "replay" && (
        <>
          <label>
            {t("本版本运行记录")}
            <select
              value={runId}
              onChange={(e) => {
                setRunId(e.target.value);
                setRunTest("");
              }}
            >
              <option value="">{t("选择运行记录")}</option>
              {data.runs.map((r) => (
                <option key={r.run_id} value={r.run_id ?? ""}>
                  {r.suite_name} · {t(statusLabel(r.status ?? "unknown"))} · {r.created_at}
                </option>
              ))}
            </select>
          </label>
          {!data.runs.length && <p>{t("暂无运行记录")}</p>}
          {onDebug && runId && (
            <Button
              disabled={disabled || replay?.verification.program_repair_allowed !== true}
              onClick={() => void debug()}
            >
              {t("准备调试方案")}
            </Button>
          )}
          {replay?.timeline && <SimulatorTimeline state={replay.timeline} />}
          {replay && (
            <details open={Boolean(runTest)}>
              <summary>{t("按测试查看详细记录")}</summary>
              <WaveformReplay replay={replay} initialTestName={runTest} t={t} />
            </details>
          )}
        </>
      )}
      {data && tab === "coverage" && (
        <>
          {!!data.unavailable_runs?.length && (
            <p className="sim-error" role="alert">
              {t("部分运行证据无法核验，以下仅显示可核验记录。")}{" "}
              {data.unavailable_runs.join(" · ")}
            </p>
          )}
          <table>
            <thead>
              <tr>
                <th>{t("需求")}</th>
                <th>{t("已关联测试")}</th>
                <th>{t("最近可核验运行")}</th>
              </tr>
            </thead>
            <tbody>
              {data.requirements.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.id}</strong>
                    <p>{r.text}</p>
                    <small>{r.devices.join(" · ")}</small>
                    <p>{t(r.status === "linked" ? "已关联测试" : "未关联测试")}</p>
                  </td>
                  <td>
                    {r.tests.map((test) => (
                      <div className="sim-coverage-test" key={`${test.plan_id}:${test.test_name}`}>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            const plan = data.plans.find((p) => p.binding.plan_id === test.plan_id);
                            if (plan) {
                              loadPlan(plan);
                              setTestIndex(
                                plan.suite.tests.findIndex((v) => v.name === test.test_name),
                              );
                              setTab("editor");
                            }
                          }}
                        >
                          {test.test_name} · {test.plan_id.slice(-8)}
                        </Button>
                        {test.latest_run ? (
                          <>
                            <small>
                              {t("已核验记录")} · {t(statusLabel(test.latest_run.status))} ·{" "}
                              {test.latest_run.backend_kind || t("未知")}
                            </small>
                            <Button
                              variant="ghost"
                              title={t("打开关联运行波形")}
                              onClick={() => openRun(test.latest_run!)}
                            >
                              {test.latest_run.run_id}
                            </Button>
                          </>
                        ) : (
                          <small>{t("未运行")}</small>
                        )}
                      </div>
                    ))}
                  </td>
                  <td>
                    {r.latest_run ? (
                      <>
                        <strong>{t(statusLabel(r.latest_run.status))}</strong>
                        <p>
                          {r.latest_run.test_name} · {r.latest_run.backend_kind || t("未知")}
                        </p>
                        <small>{r.latest_run.created_at}</small>
                        <Button onClick={() => openRun(r.latest_run!)}>
                          {t("打开关联运行波形")}
                        </Button>
                        <small>{r.latest_run.run_id}</small>
                      </>
                    ) : (
                      t("未运行")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.requirements.length && <p>{t("此版本未记录需求")}</p>}
        </>
      )}
    </section>
  );
}
