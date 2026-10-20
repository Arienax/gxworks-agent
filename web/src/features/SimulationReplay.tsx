import { useEffect, useMemo, useState } from "react";
import { Button } from "../components/ui";
import { usePanelVisible } from "../lifecycle/visibility";
import "./simulation.css";

import type { Replay } from "./simulationTypes";

const statusLabel = (status: string) =>
  ({
    passed: "通过",
    failed: "失败",
    error: "执行出错",
    unavailable: "环境不可用",
    blocked: "证据未获验收",
  })[status] || status;

export function WaveformReplay({
  replay,
  initialTestName,
  t,
}: {
  replay: Replay;
  initialTestName?: string;
  t: (s: string) => string;
}) {
  const visible = usePanelVisible();
  const [caseIndex, setCaseIndex] = useState(0),
    [cursor, setCursor] = useState(0),
    [playing, setPlaying] = useState(false);
  const current = replay.cases[caseIndex],
    samples = current?.observations || [];
  useEffect(() => {
    setCaseIndex(
      Math.max(
        0,
        replay.cases.findIndex((c) => c.name === initialTestName),
      ),
    );
    setCursor(0);
    setPlaying(false);
  }, [replay.run_id, initialTestName]);
  useEffect(() => {
    setCursor(0);
    setPlaying(false);
  }, [caseIndex]);
  useEffect(() => {
    if (!playing || !visible) return;
    const timer = window.setInterval(
      () =>
        setCursor((old) => {
          if (old >= samples.length - 1) {
            setPlaying(false);
            return old;
          }
          return old + 1;
        }),
      200,
    );
    return () => window.clearInterval(timer);
  }, [playing, samples.length, visible]);
  const selected = samples[cursor],
    end = Math.max(samples.at(-1)?.at_ms || 1, 1);
  const graphs = useMemo(
    () =>
      [...new Set(samples.flatMap((row) => Object.keys(row.values)))].map((address) => {
        const numeric = samples
          .map((s) => s.values[address])
          .filter((v) => typeof v === "number") as number[];
        const min = numeric.reduce((a, b) => Math.min(a, b), 0),
          max = numeric.reduce((a, b) => Math.max(a, b), 1),
          span = max - min || 1;
        let path = "",
          previous: number | null = null;
        samples.forEach((sample) => {
          const value = sample.values[address],
            x = 10 + (sample.at_ms / end) * 770;
          if (typeof value !== "number" || !Number.isFinite(value)) {
            previous = null;
            return;
          }
          const y = 44 - ((value - min) / span) * 34;
          path += previous === null ? ` M ${x} ${y}` : ` H ${x} V ${y}`;
          previous = value;
        });
        return { address, min, max, path };
      }),
    [samples, end],
  );
  return (
    <div className="sim-replay">
      <p>
        {t("验收状态")}：{t(statusLabel(replay.verification.status))}
        {replay.verification.category ? ` · ${replay.verification.category}` : ""}
      </p>
      <label>
        {t("回放测试")}
        <select value={caseIndex} onChange={(e) => setCaseIndex(Number(e.target.value))}>
          {replay.cases.map((c, i) => (
            <option key={c.name} value={i}>
              {c.name} · {t(statusLabel(c.status))}
            </option>
          ))}
        </select>
      </label>
      {current && (
        <>
          <p>
            {t("执行后端")}：{current.backend_kind} · {t("采样间隔 ms")}：{current.sample_ms}
          </p>
          {!samples.length ? (
            <p>{t("此运行未记录可回放的观测数据。")}</p>
          ) : (
            <>
              <div className="sim-toolbar">
                <Button
                  onClick={() => {
                    if (cursor >= samples.length - 1) setCursor(0);
                    setPlaying(!playing);
                  }}
                >
                  {t(playing ? "暂停" : "播放")}
                </Button>
                <input
                  aria-label={t("回放采样位置")}
                  type="range"
                  min={0}
                  max={samples.length - 1}
                  value={cursor}
                  onChange={(e) => {
                    setPlaying(false);
                    setCursor(Number(e.target.value));
                  }}
                />
                <output>
                  {selected?.at_ms} ms · {cursor + 1}/{samples.length}
                </output>
              </div>
              <div className="sim-waveforms">
                {graphs.map(({ address, min, max, path }) => {
                  return (
                    <div className="sim-wave-row" key={address}>
                      <div>
                        <strong>{address}</strong>
                        <small>
                          {selected && address in selected.values
                            ? String(selected.values[address] ?? t("未知"))
                            : t("未知")}
                        </small>
                      </div>
                      <svg
                        viewBox="0 0 790 54"
                        role="img"
                        aria-label={`${address} ${t("记录波形")}`}
                      >
                        <line x1={10} y1={44} x2={780} y2={44} className="sim-grid" />
                        <path d={path} className="sim-signal" />
                        <line
                          x1={10 + ((selected?.at_ms || 0) / end) * 770}
                          y1={0}
                          x2={10 + ((selected?.at_ms || 0) / end) * 770}
                          y2={54}
                          className="sim-cursor"
                        />
                      </svg>
                      <small>
                        {min}…{max}
                      </small>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          <p>
            {t("本测试关联需求")}：{current.requirement_ids.join(" · ") || t("未关联")}
          </p>
          <table>
            <thead>
              <tr>
                <th>{t("检查时刻")}</th>
                <th>{t("地址 / 步骤")}</th>
                <th>{t("实际值")}</th>
                <th>{t("期望")}</th>
                <th>{t("结果")}</th>
              </tr>
            </thead>
            <tbody>
              {current.assertions.map((a, i) => (
                <tr key={i}>
                  <td>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        const index = samples.findIndex((s) => s.at_ms >= (a.at_ms || 0));
                        if (index >= 0) setCursor(index);
                        setPlaying(false);
                      }}
                    >
                      {a.at_ms ?? "—"} ms
                    </Button>
                  </td>
                  <td>{a.address || a.step_id || "—"}</td>
                  <td>{String(a.actual ?? "—")}</td>
                  <td>
                    {typeof a.expected === "object"
                      ? JSON.stringify(a.expected)
                      : String(a.expected ?? "—")}
                  </td>
                  <td>{t(a.passed ? "通过" : "失败")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!!current.invariant_violations.length && (
            <details>
              <summary>{t("运行不变量未满足")}</summary>
              <pre>{JSON.stringify(current.invariant_violations, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}
