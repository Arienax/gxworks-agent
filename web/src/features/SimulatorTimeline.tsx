// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, CirclePause, Download, Moon, Play, Radio, Sun } from "lucide-react";
import { Badge, Button } from "../components/ui";
import { formatSignal, mergeTrace, sampleAt, signalPath } from "./simulatorTimelineData";
import type { Signal, TraceState } from "./simulatorTimelineData";
import "./simulator-timeline.css";

type Props = { state: TraceState; disconnected?: string; standalone?: boolean };
const timeText = (ms: number) => `${(ms / 1000).toFixed(ms % 1000 ? 2 : 0)} s`;
const statusText: Record<string, string> = { prepared: "等待执行", recording: "正在采样", completed: "采样结束", passed: "测试通过", failed: "测试失败", stopped: "采样已停止", error: "采样失败", unavailable: "环境不可用" };
const roleText: Record<string, string> = { input: "输入", output: "输出", state: "状态" };

export function SimulatorTimelineFeed({ endpoint, request, standalone = false }: { endpoint: string; request?: (path: string) => Promise<TraceState | null>; standalone?: boolean }) {
  const [state, setState] = useState<TraceState | null>(null), [error, setError] = useState("");
  useEffect(() => {
    let active = true, sequence = 0, viewId = "", timer: ReturnType<typeof setTimeout>;
    setState(null); setError("");
    async function poll() {
      try {
        const path = `${endpoint}?after=${sequence}&view_id=${encodeURIComponent(viewId)}`;
        const result = request ? await request(path) : await fetch(path, { cache: "no-store" }).then(async response => {
          if (!response.ok) throw new Error(`连接失败 (${response.status})`);
          return await response.json() as TraceState;
        });
        if (!active) return;
        if (result) { sequence = result.sequence; viewId = result.view_id; setState(old => mergeTrace(old, result)); }
        setError("");
      } catch (e) { if (active) setError(e instanceof Error ? e.message : String(e)); }
      if (active) timer = setTimeout(poll, 500);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [endpoint, request]);
  if (!state) return <section className="simulator-timeline sim-timeline-empty" role="status">{error || "等待测试开始。采样后，全部信号会出现在同一时间轴上。"}</section>;
  return <SimulatorTimeline state={state} disconnected={error} standalone={standalone} />;
}

export function SimulatorTimeline({ state, disconnected, standalone }: Props) {
  const [cursor, setCursor] = useState<number | null>(null), [playing, setPlaying] = useState(false);
  const [filter, setFilter] = useState(""), [role, setRole] = useState("all"), [compact, setCompact] = useState(true);
  const [speed, setSpeed] = useState("samples");
  const [light, setLight] = useState(document.documentElement.dataset.theme === "light");
  const generation = useRef(state.view_id);
  useEffect(() => { if (generation.current !== state.view_id) { generation.current = state.view_id; setCursor(null); setPlaying(false); } }, [state.view_id]);
  const last = state.samples.at(-1)?.at_ms || 0;
  const active = ["prepared", "preparing", "recording"].includes(state.status);
  const duration = Math.max(1, active || !last ? Math.max(state.config.duration_ms, last) : last);
  const at = cursor === null ? last : Math.min(cursor, last || duration);
  const current = sampleAt(state.samples, at);
  const assertions = state.events.filter(event => event.kind === "assertion");
  const failed = assertions.filter(event => !event.passed).length;
  const completed = assertions.length - failed;
  const invalid = Object.keys(current?.errors || {}).length;
  const visible = state.config.signals.filter(signal => (role === "all" || signal.role === role)
    && `${signal.label} ${signal.id} ${signal.address}`.toLowerCase().includes(filter.toLowerCase()));
  const actualEvents = state.events.filter(event => ["write", "initial_state", "initial_write", "assertion", "case", "error", "runner_error"].includes(event.kind));
  const activeEvents = actualEvents.filter(event => Math.abs(event.at_ms - at) < Math.max(1, state.config.sample_ms));
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setCursor(old => {
      const next = speed === "samples" ? state.samples.find(sample => sample.at_ms > (old ?? 0))?.at_ms ?? last
        : (old ?? 0) + 100 * Number(speed);
      if (next >= last) { setPlaying(false); return last; }
      return next;
    }), 100);
    return () => clearInterval(id);
  }, [playing, last, state.samples, speed]);
  function theme() {
    const next = !light; setLight(next);
    document.documentElement.dataset.theme = next ? "light" : "dark";
    localStorage.setItem("gx.theme", next ? "light" : "dark");
  }
  function download() {
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }));
    link.href = url; link.download = "simulator-trace.json"; link.click(); URL.revokeObjectURL(url);
  }
  return <section className={`simulator-timeline ${compact ? "compact" : ""} ${standalone ? "standalone" : ""}`} aria-label="仿真信号总览">
    <header className="timeline-header"><div className="timeline-brand"><Activity size={20} /><span>GXWorks Agent<small>仿真总览</small></span></div>
      <div className="timeline-actions"><Badge tone={disconnected || state.error || failed || invalid ? "bad" : state.status === "recording" ? "good" : "neutral"}>{disconnected ? "连接中断" : invalid ? `${invalid} 项读数缺失或无效` : statusText[state.status] || state.status}</Badge>
        <Button variant="ghost" onClick={download}><Download size={14} />导出记录</Button>{standalone && <Button variant="ghost" onClick={theme} aria-label="切换主题">{light ? <Moon size={16} /> : <Sun size={16} />}</Button>}</div></header>
    <div className="timeline-intro"><div><h2>{state.config.title}</h2><p>{state.config.plc_model} · {state.source.route || state.source.backend_kind || "测试执行记录"} · {state.source.kind.startsWith("recorded_") ? "记录中的采样" : `请求间隔 ${state.config.sample_ms} ms`}{state.binding.version_id ? ` · 版本 ${state.binding.version_id}` : ""}</p>
      {state.source.program_correspondence === "unverified" && <p>程序对应关系未验证</p>}</div>
      <div className="timeline-metrics"><span><strong>{state.config.signals.length}</strong>信号</span><span><strong>{state.sample_count}</strong>采样</span><span><strong>{timeText(last)}</strong>已记录</span><span><strong className={failed ? "timeline-bad" : ""}>{completed} / {assertions.length}</strong>检查通过</span></div></div>
    {(disconnected || state.error) && <p role="alert" className="timeline-error">{disconnected || state.error}。保留最后记录，当前数值不再更新。</p>}
    {state.dropped_samples > 0 && <p className="timeline-notice">已保留最近 6001 次采样，较早的 {state.dropped_samples} 次采样已移出当前视图。</p>}
    <div className="timeline-controls"><Button onClick={() => { if (playing) setPlaying(false); else { if (cursor === null || at >= last) setCursor(0); setPlaying(true); } }} disabled={state.samples.length < 2}>{playing ? <CirclePause size={14} /> : <Play size={14} />}{playing ? "暂停" : "回放"}</Button>
      <select className="timeline-speed" aria-label="回放速度" value={speed} onChange={event => setSpeed(event.target.value)}><option value="samples">按采样回放</option><option value="1">1× 实际时间</option><option value="5">5× 实际时间</option><option value="20">20× 实际时间</option></select>
      <Button variant={cursor === null ? "primary" : "secondary"} onClick={() => { setPlaying(false); setCursor(null); }}><Radio size={14} />{state.status === "recording" ? "跟随实时" : "最新采样"}</Button>
      <output>{timeText(at)} <span>/ {timeText(duration)}</span></output><input aria-label="全部信号的时间游标" type="range" min={0} max={last || duration} step={1} value={at} onChange={event => { setPlaying(false); setCursor(Number(event.target.value)); }} />
      <Button variant="ghost" onClick={() => setCompact(value => !value)}>{compact ? "展开行高" : "紧凑显示"}</Button></div>
    <div className="timeline-filter"><input aria-label="查找信号" placeholder="查找变量、名称或地址" value={filter} onChange={event => setFilter(event.target.value)} />
      <select aria-label="信号类别" value={role} onChange={event => setRole(event.target.value)}><option value="all">全部信号</option><option value="input">输入</option><option value="output">输出</option><option value="state">状态</option></select>
      <span>{visible.length} / {state.config.signals.length} 可见 · {current ? `读数 ${timeText(current.at_ms)}` : "尚无采样"}</span></div>
    <div className="timeline-scroll"><div className="timeline-table" role="table" aria-label="全部信号同步趋势">
      <div className="timeline-row timeline-columns" role="row"><span role="columnheader">变量 / 地址</span><span role="columnheader">采样值</span><span role="columnheader">请求 / 期望</span><div role="columnheader" className="timeline-axis">{[0, 1, 2, 3, 4].map(n => <span key={n} style={{ left: `${n * 25}%` }}>{timeText(duration * n / 4)}</span>)}</div></div>
      <div className="timeline-row timeline-event-band"><span>输入与检查<small>实线：实测 · 虚线：计划</small></span><span /><span /><div className="timeline-events">{state.config.events?.map((event, index) => <i className="planned" key={`p${index}`} style={{ left: `${event.at_ms / duration * 100}%` }} title={`计划 ${timeText(event.at_ms)} · ${event.label}`} />)}
        {actualEvents.map(event => <button className={event.kind === "assertion" ? event.passed ? "passed" : "failed" : "written"} key={event.seq} style={{ left: `${event.at_ms / duration * 100}%` }} title={`${timeText(event.at_ms)} · ${event.label} · ${event.kind}`} aria-label={`${timeText(event.at_ms)} ${event.label}`} onClick={() => { setCursor(event.at_ms); setPlaying(false); }} />)}</div></div>
      {visible.map(signal => <SignalRow key={signal.id} signal={signal} state={state} at={at} duration={duration} onCursor={value => { setCursor(value); setPlaying(false); }} />)}
      {!visible.length && <p className="muted">没有匹配的信号。</p>}
    </div></div>
    <footer className="timeline-footer"><div>{activeEvents.length ? activeEvents.slice(-4).map(event => <Badge key={event.seq} tone={event.passed === false ? "bad" : "neutral"}>{timeText(event.at_ms)} · {event.label}{event.kind === "assertion" ? `：${formatSignal(event.actual)} / ${formatSignal(event.expected)}` : ""}</Badge>) : <span>拖动一个时间游标，即可同时查看所有变量。缺失读数显示为 —，折线缺口不补值。</span>}</div>
      <small>{state.source.kind === "prepared_test_plan" ? "当前仅为测试计划，尚未执行输入和检查。" : state.source.kind === "runtime_device_observation" ? "只读观察；计划标记不代表已执行输入或通过检查。" : state.source.kind === "recorded_native_test" ? "已有原生测试记录回放；当前没有运行测试。" : "实线来自实际读取；检查结果来自执行器断言。"} 虚线仅表示写入请求，期望旁的时间是最近检查时刻。采样可能遗漏两次读取间的短暂变化。{state.source.program_correspondence === "unverified" ? "程序对应关系未验证。" : ""}{state.source.note}</small></footer>
  </section>;
}

function SignalRow({ signal, state, at, duration, onCursor }: { signal: Signal; state: TraceState; at: number; duration: number; onCursor: (at: number) => void }) {
  const current = sampleAt(state.samples, at);
  const actual = current?.values[signal.id];
  const assertion = [...state.events].reverse().find(event => event.kind === "assertion" && event.signal === signal.id && event.at_ms <= at);
  const requested = state.events.filter(event => ["write", "initial_write"].includes(event.kind) && event.values?.[signal.id] != null);
  const command = [...requested].reverse().find(event => event.at_ms <= at);
  const numbers = useMemo(() => state.samples.flatMap(sample => sample.values[signal.id] == null ? [] : [Number(sample.values[signal.id])]), [state.samples, signal.id]);
  const requestedNumbers = requested.map(event => Number(event.values![signal.id]));
  const combined = [...numbers, ...requestedNumbers];
  const low = signal.data_type === "BOOL" ? 0 : combined.length ? Math.min(...combined) : 0;
  const high = signal.data_type === "BOOL" ? 1 : combined.length ? Math.max(...combined) : 1;
  const padding = low === high ? Math.max(1, Math.abs(low) * .05) : 0;
  const path = useMemo(() => signalPath(state.samples, signal, duration, low - padding, high + padding), [state.samples, signal, duration, low, high, padding]);
  const requestedPath = signalPath(requested.map(event => ({ seq: event.seq, at_ms: event.at_ms, values: event.values!, errors: {} })), { ...signal, data_type: "BOOL" }, duration, low - padding, high + padding);
  return <div className={`timeline-row timeline-signal ${assertion?.passed === false ? "has-failure" : ""}`} role="row">
    <div className="timeline-signal-name" role="cell"><strong title={signal.id}>{signal.label}</strong><small>{signal.address} · {signal.data_type} · {roleText[signal.role]}</small></div>
    <div className={`timeline-value ${actual === true ? "is-on" : actual == null ? "is-missing" : ""}`} role="cell" title={current?.errors[signal.id] || formatSignal(actual)}>{formatSignal(actual)}{actual != null && signal.unit && <small>{signal.unit}</small>}</div>
    <div className={assertion?.passed === false ? "timeline-bad" : "timeline-expected"} role="cell">{signal.role === "input" && command ? <>{formatSignal(command.values![signal.id])}<small>请求 {timeText(command.at_ms)}</small></> : assertion ? <>{formatSignal(assertion.expected)}<small>{assertion.passed ? "✓" : "×"} {timeText(assertion.at_ms)}</small></> : "—"}</div>
    <div className="timeline-plot" role="cell"><svg viewBox="0 0 1000 60" preserveAspectRatio="none" aria-label={`${signal.label}趋势`} onPointerDown={event => { const rect = event.currentTarget.getBoundingClientRect(); onCursor(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * duration); }}>
      {[0, 250, 500, 750, 1000].map(x => <line key={x} className="timeline-grid" x1={x} x2={x} y1={0} y2={60} />)}
      <path className={`timeline-path ${signal.role}`} d={path} />
      {requested.length > 0 && <path className="timeline-request-path" d={requestedPath} />}
      {state.events.filter(event => event.kind === "assertion" && event.signal === signal.id).map(event => <circle className={event.passed ? "timeline-dot-pass" : "timeline-dot-fail"} key={event.seq} cx={event.at_ms / duration * 1000} cy={typeof event.actual === "number" || typeof event.actual === "boolean" ? 49 - (Number(event.actual) - low + padding) / (high - low + padding * 2 || 1) * 38 : 30} r={4} />)}
      {state.samples.filter((sample, index, samples) => sample.values[signal.id] != null
        && (index === 0 || samples[index - 1].values[signal.id] == null)
        && (index === samples.length - 1 || samples[index + 1].values[signal.id] == null)).map(sample => <circle key={sample.seq} className="timeline-value-dot" cx={sample.at_ms / duration * 1000} cy={49 - (Number(sample.values[signal.id]) - low + padding) / (high - low + padding * 2 || 1) * 38} r={3} />)}
      <line className="timeline-cursor" x1={at / duration * 1000} x2={at / duration * 1000} y1={0} y2={60} />
    </svg><small>{numbers.length ? `${formatSignal(low)} … ${formatSignal(high)}` : requestedNumbers.length ? `仅请求 ${formatSignal(low)} … ${formatSignal(high)}` : "无读数"}{signal.unit ? ` ${signal.unit}` : ""}</small></div>
  </div>;
}
