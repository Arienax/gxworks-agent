// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import { useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, FileUp, Download } from "lucide-react";
import { api, artifactUrl, key } from "../api/client";
import type { Proposal } from "../api/client";
import { Button } from "../components/ui";
import "./fbd.css";

import type { ApiSchema } from "../api/client";
export type FBDModel = ApiSchema["FBDModel"];
type NativeEvidence = ApiSchema["NativeEvidence"];

type NativeCheck = ApiSchema["NativeCheck"];

const templateLabel = (key: string, t: (s: string) => string) => {
  const labels: Record<string, string> = {
    contact: "常开触点",
    contact_nc: "常闭触点",
    coil: "线圈",
    input: "输入值",
    output: "输出变量",
  };
  return labels[key]
    ? t(labels[key])
    : key.replace("function_block:", "FB · ").replace("function:", "Function · ");
};

export function NativeValidationPanel({
  pid,
  vid,
  program,
  disabled,
  t,
  onCell,
}: {
  pid: string;
  vid: string;
  program: string;
  disabled: boolean;
  t: (s: string) => string;
  onCell?: (id: string) => void;
}) {
  const route = `/projects/${pid}/versions/${vid}/native-validation`;
  const [evidence, setEvidence] = useState<NativeEvidence | null>(null),
    [error, setError] = useState("");
  const [operator, setOperator] = useState(""),
    [toolVersion, setToolVersion] = useState(""),
    [outcome, setOutcome] = useState("inconclusive");
  const [report, setReport] = useState(""),
    [attested, setAttested] = useState(false),
    [busy, setBusy] = useState(false);
  const [nativeGXW, setNativeGXW] = useState<{ filename: string; data_base64: string } | null>(
    null,
  );
  const [requestId, setRequestId] = useState(key);
  const [check, setCheck] = useState<NativeCheck | null>(null),
    [checking, setChecking] = useState(false);
  const checkRequest = useRef<string | null>(null);
  useEffect(() => {
    let active = true;
    api<NativeEvidence>(route)
      .then((value) => {
        if (active) setEvidence(value);
      })
      .catch((error) => {
        if (active) setError(error.message);
      });
    return () => {
      active = false;
    };
  }, [route]);
  function changed() {
    setRequestId(key());
    setAttested(false);
  }
  async function readNative(file?: File) {
    changed();
    setNativeGXW(null);
    setError("");
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      const data_base64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      setNativeGXW({ filename: file.name, data_base64 });
    } catch (error) {
      setError(String(error instanceof Error ? error.message : error));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!evidence) return;
    setBusy(true);
    setError("");
    try {
      await api(route, "POST", {
        request_id: requestId,
        source_gxw_sha256: evidence.source_gxw_sha256,
        operator,
        tool_version: toolVersion,
        outcome,
        report,
        attested,
        ...(nativeGXW ? { native_gxw: nativeGXW } : {}),
      });
      setEvidence(await api<NativeEvidence>(route));
      setRequestId(key());
      setAttested(false);
      setReport("");
      setNativeGXW(null);
    } catch (error) {
      setError(String(error instanceof Error ? error.message : error));
    } finally {
      setBusy(false);
    }
  }
  async function runNativeCheck() {
    setChecking(true);
    setCheck(null);
    setError("");
    checkRequest.current ??= key();
    try {
      const response = await api<ApiSchema["NativeValidationToolResult"]>(`${route}/check`, "POST", {
        request_id: checkRequest.current,
        program,
      });
      checkRequest.current = null;
      if (response.is_error || !response.data.data)
        throw new Error(response.data.error?.message || t("原生检查未返回完整结果。"));
      const result = response.data.data;
      if (
        result.snapshot.project_id !== pid ||
        result.snapshot.version_id !== vid ||
        result.snapshot.requested_program !== program
      )
        throw new Error(t("检查结果与所选版本或程序不一致。"));
      setCheck(result);
    } catch (error) {
      setError(String(error instanceof Error ? error.message : error));
    } finally {
      setChecking(false);
    }
  }
  const locked = disabled || busy || checking || !evidence;
  const statusText = (status: string) =>
    t(
      (
        {
          accepted: "接受",
          rejected: "被拒绝",
          incomplete: "不完整",
          completed: "完成",
          failed: "失败",
          passed: "通过",
          "completed-accepted": "完成并接受",
          "completed-rejected": "完成并拒绝",
          current: "当前版本一致",
          verified: "已核对",
          identity_mismatch: "对象身份不一致",
          conversion_incomplete: "诊断转换不完整",
          established: "已建立",
          not_established: "未建立",
          not_observed: "未采集",
          not_completed: "未完成",
          not_requested: "未请求",
          partial_or_unresolved: "部分或未解析",
          unresolved: "未解析",
          "source-resolved": "已定位源码",
          "instance-resolved": "已定位实例",
        } as Record<string, string>
      )[status] || status,
    );
  const outcomeText = (value: string) =>
    t(
      value === "passed"
        ? "操作员报告通过"
        : value === "failed"
          ? "操作员报告失败"
          : "操作员报告待确认",
    );
  const projectionCodes = check
    ? [
        ...new Set([
          ...check.diagnostic_projection.failures.flatMap((item) =>
            item.code == null ? [] : [item.code],
          ),
          ...(check.diagnostic_recheck?.trigger_code == null
            ? []
            : [check.diagnostic_recheck.trigger_code]),
        ]),
      ]
    : [];
  return (
    <div className="fbd-sheet fbd-native">
      <h3>{t("检查当前 GXW 版本")}</h3>
      <Button variant="primary" disabled={locked} onClick={() => void runNativeCheck()}>
        {t(checking ? "正在检查原生工程…" : "启动原生检查")}
      </Button>
      {check && (
        <div className="fbd-native-record">
          <p>
            {t("所选版本")}：{check.snapshot.version_id} · {check.snapshot.requested_program}
            <br />
            {check.cpu} · {check.native_version}
          </p>
          <dl className="fbd-native-results">
            {[
              [
                "原生编译",
                `${check.compilation.completed ? t("完成") : t("未完成")} · ${statusText(check.compilation.acceptance)}`,
              ],
              [
                "编译源码对应",
                statusText(check.compilation.source_context?.status || "not_observed"),
              ],
              [
                "编译声明对应",
                statusText(check.compilation.declaration_context?.status || "not_observed"),
              ],
              [
                "全局及结构声明对应",
                statusText(
                  check.compilation.declaration_context?.global_and_structure?.status ||
                    "not_observed",
                ),
              ],
              ["实际任务绑定", statusText(check.selection.status)],
              ["工作区发布", check.publication.completed ? t("完成") : t("未完成")],
              ["发布资源对应", statusText(check.published_resources.correspondence)],
              ["检查输入对应", statusText(check.owned_check_inputs.correspondence)],
              ["公开检查", statusText(check.public_check.status)],
              ["底层重检", statusText(check.raw_backend_check.status)],
              [
                "原生公开诊断转换",
                `${statusText(check.diagnostic_projection.status)} ${projectionCodes.map((code) => `0x${code.toString(16)}`).join(" · ")}`,
              ],
              [
                "完整诊断转换",
                `${statusText(check.public_diagnostic_conversion?.status || "not_observed")}${
                  check.public_diagnostic_conversion?.converted_report_count !== undefined
                    ? ` · ${check.public_diagnostic_conversion.converted_report_count}/${check.public_diagnostic_conversion.original_report_count}`
                    : ""
                }`,
              ],
              ["当前源码公开检查", statusText(check.current_source_check)],
              ["当前源码底层检查", statusText(check.current_raw_source_check)],
              ["源码位置", statusText(check.native_source_locations.status)],
              ["实例位置", statusText(check.native_instance_locations.status)],
              ["本次原生保存", check.save.completed ? t("完成") : t("未执行")],
              ["保存后重开", statusText(check.reopen.status)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{t(label)}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <h4>{t("实际检查任务与资源")}</h4>
          {check.selection.empty_task && (
            <p role="alert">{t("任务没有选中源码，不能证明当前程序完成检查。")}</p>
          )}
          <ul>
            {check.selection.tasks.map((task, index) => (
              <li key={index}>
                {task.resource} / {task.task}：{task.programs.join(" · ") || t("无源码")}
              </li>
            ))}
          </ul>
          <h4>{t("本次原生诊断")}</h4>
          {!check.compilation.source_diagnostics?.length &&
            check.diagnostic_projection.source_diagnostics.length === 0 && (
              <p>
                {t(
                  check.compilation.acceptance === "rejected"
                    ? "编译已拒绝，原始报告未提供可定位的诊断。"
                    : check.compilation.diagnostics_complete === true &&
                        check.compilation.diagnostics?.length === 0 &&
                        check.diagnostic_projection.empty_public_list_means_no_errors === true &&
                        check.current_source_check === "established"
                      ? "当前源码检查完成，未报告错误或警告。"
                      : "诊断尚不完整，不能确认当前源码没有错误。",
                )}
              </p>
            )}
          {(
            [
              ["编译诊断", check.compilation.source_diagnostics || []],
              ["检查诊断", check.diagnostic_projection.source_diagnostics],
            ] as const
          )
            .filter(([, diagnostics]) => diagnostics.length > 0)
            .map(([label, diagnostics]) => (
              <div key={label}>
                <h5>{t(label)}</h5>
                {diagnostics.map((diagnostic, index) => (
                  <article key={index} className="fbd-native-record">
                    <strong>
                      0x{diagnostic.original.code.toString(16)} ·{" "}
                      {diagnostic.original.arguments.map((item) => item.text).join(" · ")}
                    </strong>
                    <p>
                      {diagnostic.source_projection.source?.library &&
                        `${diagnostic.source_projection.source.library}/`}
                      {diagnostic.source_projection.source?.pou ||
                        diagnostic.source_projection.source?.declaration_owner ||
                        t("源码未解析")}
                      {" · "}
                      {statusText(diagnostic.source_projection.status)}
                      {diagnostic.source_projection.status === "source-resolved" &&
                        diagnostic.source_projection.source?.declaration && (
                          <>
                            <br />
                            {t("声明")}：{diagnostic.source_projection.source.declaration.name} ·{" "}
                            {diagnostic.source_projection.source.declaration.data_type}
                          </>
                        )}
                      {diagnostic.source_projection.status === "source-resolved" &&
                        diagnostic.source_projection.source?.zero_based_line != null && (
                          <>
                            <br />
                            {t("源码行")} {diagnostic.source_projection.source.zero_based_line + 1}
                            {diagnostic.source_projection.source.text && (
                              <>
                                <br />
                                <code>{diagnostic.source_projection.source.text}</code>
                              </>
                            )}
                          </>
                        )}
                      <br />
                      {diagnostic.source_projection.source?.child ? (
                        <>
                          {t(
                            diagnostic.source_projection.source.child.kind === "zoom"
                              ? "动作"
                              : "转换条件",
                          )}{" "}
                          {diagnostic.source_projection.source.child.kind === "zoom"
                            ? diagnostic.source_projection.source.child.registrations
                                .map((item) => item.action_number)
                                .join(" · ")
                            : diagnostic.source_projection.source.child.number}
                          {diagnostic.source_projection.instruction_position?.status ===
                            "source-resolved" &&
                            diagnostic.source_projection.instruction_position.position && (
                              <>
                                <br />
                                {t("代码步")}{" "}
                                {
                                  diagnostic.source_projection.instruction_position.position
                                    .native_step
                                }{" "}
                                ·{" "}
                                <code>
                                  {diagnostic.source_projection.instruction_position.position.op}{" "}
                                  {diagnostic.source_projection.instruction_position.position.args.join(
                                    " ",
                                  )}
                                </code>
                              </>
                            )}
                        </>
                      ) : diagnostic.source_projection.instance_resolution ===
                        "not_available_in_compile_report" ? (
                        t("编译报告未提供调用实例。")
                      ) : (
                        <>
                          {diagnostic.source_projection.instance_projection?.task} /{" "}
                          {diagnostic.source_projection.instance_projection?.instance ||
                            t("实例未解析")}
                        </>
                      )}
                      {diagnostic.graph_projection.object && (
                        <>
                          <br />
                          {t("网络")} {diagnostic.source_projection.source?.network} ·{" "}
                          {diagnostic.graph_projection.object.symbol}
                        </>
                      )}
                      {diagnostic.graph_projection.source?.instance &&
                        diagnostic.graph_projection.source.port_name && (
                          <>
                            <br />
                            {diagnostic.graph_projection.source.instance}.
                            {diagnostic.graph_projection.source.port_name} ·{" "}
                            {diagnostic.graph_projection.source.side}
                          </>
                        )}
                      {diagnostic.graph_projection.source?.pou &&
                        diagnostic.graph_projection.source.symbol && (
                          <>
                            <br />
                            {diagnostic.graph_projection.source.pou} · {t("网络")}{" "}
                            {diagnostic.graph_projection.source.network} ·{" "}
                            {diagnostic.graph_projection.source.symbol}
                          </>
                        )}
                      {diagnostic.graph_projection.caller && (
                        <>
                          <br />
                          {diagnostic.graph_projection.caller.pou} · {t("网络")}{" "}
                          {diagnostic.graph_projection.caller.network}
                          {diagnostic.graph_projection.caller.symbol && (
                            <> · {diagnostic.graph_projection.caller.symbol}</>
                          )}
                        </>
                      )}
                    </p>
                    {onCell && diagnostic.graph_projection.cell && (
                      <Button onClick={() => onCell(diagnostic.graph_projection.cell!.id)}>
                        {t("定位图形")}
                      </Button>
                    )}
                    {onCell && (diagnostic.graph_projection.candidates?.length || 0) > 1 && (
                      <p>
                        {t("多个图形位置，尚未确定唯一对象。")}
                        {diagnostic.graph_projection.candidates!.map((cell) => (
                          <Button key={cell.id} onClick={() => onCell(cell.id)}>
                            {t("查看候选位置")} ({cell.x},{cell.y})
                          </Button>
                        ))}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            ))}
          <details>
            <summary>{t("完整检查结果")}</summary>
            <pre className="fbd-native-json">{JSON.stringify(check, null, 2)}</pre>
          </details>
        </div>
      )}
      <h3>{t("记录 GX Works2 原生验证")}</h3>
      {evidence && (
        <p className="muted">
          {t("所选版本")}：{vid}
          <br />
          GXW SHA-256：<code className="fbd-hash">{evidence.source_gxw_sha256}</code>
        </p>
      )}
      {error && (
        <p className="fbd-error" role="alert">
          {error}
        </p>
      )}
      <div className="form">
        <div className="fbd-native-fields">
          <label>
            {t("操作员")}
            <input
              value={operator}
              disabled={locked}
              maxLength={160}
              onChange={(e) => {
                changed();
                setOperator(e.target.value);
              }}
            />
          </label>
          <label>
            {t("GX Works2 版本")}
            <input
              value={toolVersion}
              disabled={locked}
              maxLength={160}
              placeholder="GX Works2 …"
              onChange={(e) => {
                changed();
                setToolVersion(e.target.value);
              }}
            />
          </label>
          <label>
            {t("原生编译结果")}
            <select
              value={outcome}
              disabled={locked}
              onChange={(e) => {
                changed();
                setOutcome(e.target.value);
              }}
            >
              {["inconclusive", "passed", "failed"].map((item) => (
                <option key={item} value={item}>
                  {outcomeText(item)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          {t("编译报告与复核说明")}
          <textarea
            value={report}
            disabled={locked}
            maxLength={64000}
            rows={5}
            placeholder={t("粘贴 GX Works2 的编译结果、错误所在程序/对象以及复核说明。")}
            onChange={(e) => {
              changed();
              setReport(e.target.value);
            }}
          />
        </label>
        <label>
          {t("保存后的原生 GXW（可选）")}
          <input
            key={nativeGXW?.filename || requestId}
            type="file"
            accept=".gxw"
            disabled={locked}
            onChange={(e) => void readNative(e.target.files?.[0])}
          />
        </label>
        {nativeGXW && <p className="muted">{nativeGXW.filename}</p>}
        <label className="fbd-attestation">
          <input
            type="checkbox"
            checked={attested}
            disabled={locked}
            onChange={(e) => setAttested(e.target.checked)}
          />
          <span>
            {t("我已核对所选版本和源文件，报告如实描述我的原生验证；此记录不代表工作台自动验证。")}
          </span>
        </label>
        <Button
          variant="primary"
          disabled={
            locked || !attested || !operator.trim() || !toolVersion.trim() || !report.trim()
          }
          onClick={() => void save()}
        >
          {t(busy ? "正在保存…" : "保存原生验证记录")}
        </Button>
      </div>
      <h3>{t("已记录的原生证据")}</h3>
      {evidence?.records.length === 0 && <p className="muted">{t("此版本尚无原生验证记录。")}</p>}
      {evidence?.records.map((item) => (
        <article className="fbd-native-record" key={item.id}>
          <strong>
            {outcomeText(item.outcome)} · {t("未自动验证")}
          </strong>
          <p className="muted">
            {item.operator} · {item.tool_version} · {new Date(item.created_at).toLocaleString()}
          </p>
          {!item.binding_current && (
            <p role="alert" className="fbd-error">
              {t("源 GXW 文件已变化，此记录不适用于当前文件。")}
            </p>
          )}
          <pre>{item.report}</pre>
          {item.native_gxw && (
            <>
              <p>
                {item.native_gxw.integrity_verified ? t("附件完整性已校验") : t("附件缺失或已变化")}
                {" · "}
                {t(
                  item.native_gxw.matches_source_bytes
                    ? "附件与源文件完全一致"
                    : item.native_gxw.selected_program_matches
                      ? "附件字节已变化；所选程序对象与声明一致"
                      : "附件中的所选程序已变化，不能证明源版本编译通过",
                )}
              </p>
              <code className="fbd-hash">SHA-256：{item.native_gxw.sha256}</code>
              {item.native_gxw.integrity_verified && (
                <a className="button" href={`/api${route}/${item.id}/gxw`}>
                  <Download size={14} />
                  {t("下载原生证据 GXW")}
                </a>
              )}
            </>
          )}
        </article>
      ))}
    </div>
  );
}

export function FBDImport({
  pid,
  vid,
  disabled,
  onProposal,
  t,
}: {
  pid: string;
  vid: string;
  disabled: boolean;
  onProposal: (proposal: Proposal) => Promise<void>;
  t: (s: string) => string;
}) {
  const [upload, setUpload] = useState<{ filename: string; data_base64: string } | null>(null);
  const [programs, setPrograms] = useState<string[]>([]),
    [program, setProgram] = useState("");
  const [nativeSave, setNativeSave] = useState(false);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function read(file?: File) {
    if (!file) return;
    setError("");
    setUpload(null);
    setPrograms([]);
    setBusy(true);
    try {
      if (file.size > 30 * 1024 * 1024) throw new Error(t("GXW 文件不能超过 30 MiB。"));
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const value = { filename: file.name, data_base64: encoded };
      const info = await api<ApiSchema["FBDInspection"]>("/fbd/inspect", "POST", value);
      if (!info.programs.length) throw new Error(t("工程中没有可读取的 Program.pou。"));
      setUpload(value);
      setPrograms(info.programs);
      setProgram(info.programs[0]);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }
  async function propose() {
    if (!upload) return;
    setBusy(true);
    setError("");
    try {
      const proposal = await api<Proposal>("/fbd/proposals", "POST", {
        operation: "import",
        project_id: pid,
        version_id: vid || null,
        request_id: key(),
        data_base64: upload.data_base64,
        program,
        native_save: nativeSave,
      });
      await onProposal(proposal);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="form fbd-import">
      <label>
        {t("选择 GXW 工程")}
        <input
          type="file"
          accept=".gxw"
          disabled={disabled || busy}
          onChange={(e) => void read(e.target.files?.[0])}
        />
      </label>
      {upload && (
        <label>
          {t("程序")}
          <select value={program} onChange={(e) => setProgram(e.target.value)}>
            {programs.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
      )}
      <label>
        <input
          type="checkbox"
          checked={nativeSave}
          disabled={disabled || busy}
          onChange={(e) => setNativeSave(e.target.checked)}
        />
        {t("后续修改由 GX Works2 保存")}
      </label>
      {error && (
        <p role="alert" className="fbd-error">
          {error}
        </p>
      )}
      <Button
        disabled={disabled || busy || !upload}
        variant="primary"
        onClick={() => void propose()}
      >
        <FileUp size={15} />
        {t(busy ? "正在读取…" : "校验导入并保存")}
      </Button>
    </div>
  );
}

export type EditorView = ApiSchema["FBDEditorView"];

export function FBDPanel({
  value, svg, pid, vid, readOnly, preview, previewEditor, t,
}: {
  value: FBDModel | null;
  svg: string;
  pid: string;
  vid: string;
  readOnly: boolean;
  preview: boolean;
  previewEditor?: EditorView;
  t: (s: string) => string;
}) {
  const [loaded, setLoaded] = useState<{ identity: string; view: EditorView } | null>(null);
  const [section, setSection] = useState("diagram");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [table, setTable] = useState(""), [page, setPage] = useState(0);
  const [zoom, setZoom] = useState(1), [search, setSearch] = useState("");
  const generation = useRef(0), pending = useRef(false);
  const inputKey = useMemo(() => JSON.stringify(value), [value]);
  const identity = `${pid}/${vid}/${inputKey}`;
  const editor = preview ? previewEditor : loaded?.identity === identity ? loaded.view : null;

  useEffect(() => {
    const current = ++generation.current;
    pending.current = false;
    setLoaded(null);
    setBusy(false);
    setError("");
    setPage(0);
    setSearch("");
    setZoom(1);
    if (preview) {
      setTable(previewEditor?.presentation.tables[0] || "");
      return () => { generation.current++; };
    }
    api<EditorView>("/fbd/editor", "POST", {
      project_id: pid, version_id: vid || null, model: JSON.parse(inputKey),
    }).then((view) => {
      if (generation.current === current) {
        setLoaded({ identity, view });
        setTable(view.presentation.tables[0] || "");
      }
    }).catch((error) => {
      if (generation.current === current) setError(error.message);
    });
    return () => { generation.current++; };
  }, [identity, inputKey, pid, vid, preview, previewEditor]);

  async function selectProgram(program: string) {
    if (pending.current || preview) return;
    const current = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const view = await api<EditorView>("/fbd/editor", "POST", {
        project_id: pid, version_id: vid, program,
      });
      if (generation.current === current) {
        setLoaded({ identity, view });
        setTable(view.presentation.tables[0] || "");
        setPage(0);
        setSearch("");
        setZoom(1);
      }
    } catch (error) {
      if (generation.current === current) setError(error instanceof Error ? error.message : String(error));
    } finally {
      if (generation.current === current) {
        pending.current = false;
        setBusy(false);
      }
    }
  }

  if (!editor) return (
    <div className="fbd-panel">
      <p className="fbd-notice" role={error ? "alert" : "status"}>
        {error || t(preview ? "程序详情暂不可用。" : "正在读取…")}
      </p>
    </div>
  );

  const model = editor.model;
  const diagram = editor.source_svg
    ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(editor.source_svg)}` : svg;
  const blocks = editor.presentation.blocks || [];
  const rows = editor.presentation.rows[table] || [];
  const query = search.trim().toLocaleLowerCase();
  const components = model.nodes.filter((node) => node.template !== "comment" &&
    (!query || `${node.symbol} ${templateLabel(node.template, t)}`.toLocaleLowerCase().includes(query)));
  const nodeViews = new Map(editor.presentation.nodes.map((node) => [node.id, node]));
  const sections = [
    ["diagram", "程序图"], ["networks", "网络"], ["components", "元件与端口"], ["labels", "标签"],
    ...(!preview && vid ? [["native", "原生验证"]] : []),
  ];
  const visibleSection = sections.some(([id]) => id === section) ? section : "diagram";
  return (
    <div className="fbd-panel">
      <div className="fbd-toolbar">
        {vid && !preview && editor.programs.length > 1 ? (
          <select aria-label={t("所选 POU")} value={model.program} disabled={busy}
            onChange={(event) => void selectProgram(event.target.value)}>
            {editor.programs.map((name) => (
              <option key={name} value={name}>{name.replace(".Program.pou", "")}</option>
            ))}
          </select>
        ) : <strong>{model.program.replace(".Program.pou", "")} · FBD</strong>}
        <span className="muted">{model.cpu}{model.cpu && " · "}{blocks.length || 1} {t("网络")}</span>
        {vid && !preview && <div className="fbd-toolbar-actions">
          <a className="button" href={artifactUrl(pid, vid, "gxw", true)}>
            <Download size={14} />{t("下载 GXW")}
          </a>
        </div>}
      </div>
      <p className="fbd-notice">{t("在对话中描述修改需求；图形编辑可在 GX Works2 中完成。")}</p>
      <div className="fbd-sections" aria-label={t("FBD 视图")}>
        {sections.map(([id, label]) => (
          <button key={id} className={visibleSection === id ? "active" : ""}
            aria-pressed={visibleSection === id} onClick={() => setSection(id)}>{t(label)}</button>
        ))}
      </div>
      {error && <p className="fbd-error" role="alert">{error}</p>}
      {!!model.issues?.length && <details className="fbd-issues">
        <summary>{t("结构检查提示")} · {model.issues.length}</summary>
        <ul>{model.issues.map((issue, index) => <li key={index}>{issue.message || issue.code}</li>)}</ul>
      </details>}
      {visibleSection === "diagram" ? (
        <div className="fbd-diagram">
          {diagram && model.nodes.length ? <>
            <div className="fbd-zoom">
              <Button aria-label={t("缩小")} onClick={() => setZoom((value) => Math.max(0.25, value - 0.25))}>−</Button>
              <Button title={t("重置缩放")} onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</Button>
              <Button aria-label={t("放大")} onClick={() => setZoom((value) => Math.min(4, value + 0.25))}>+</Button>
            </div>
            <img alt={t("结构化梯形图/FBD")} src={diagram}
              style={{ width: `${zoom * 100}%`, maxWidth: "none" }} />
          </> : <div className="empty-state">
            <GitBranch size={38} /><h2>{t("暂无程序图")}</h2>
            <p>{t("在对话中描述控制需求，或导入已有 GXW 工程。")}</p>
          </div>}
        </div>
      ) : visibleSection === "networks" ? (
        <div className="fbd-sheet fbd-network-list">
          {blocks.map((block, index) => {
            const comments = model.nodes.filter((node) => (node.block ?? 0) === index && node.template === "comment");
            return <article className="fbd-network" key={index}>
              <header><span>{t("网络")} {index + 1}</span>{block.title && <code>{block.title}</code>}</header>
              {block.comment && <strong>{block.comment}</strong>}
              {comments.map((node) => <p key={node.id}>{node.symbol}</p>)}
              {!block.title && !block.comment && !comments.length && <p className="muted">{t("未填写网络说明")}</p>}
            </article>;
          })}
        </div>
      ) : visibleSection === "components" ? (
        <div className="fbd-sheet">
          <div className="fbd-inline">
            <input type="search" aria-label={t("搜索元件或实例")} placeholder={t("搜索元件或实例")}
              value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <div className="fbd-components">
            {components.map((node) => {
              const ports = nodeViews.get(node.id)?.ports || [];
              return <details className="fbd-component" key={node.id}>
                <summary>
                  <span className="fbd-component-name">{node.symbol || templateLabel(node.template, t)}</span>
                  <span className="muted">{templateLabel(node.template, t)}</span>
                  <span className="muted">{t("网络")} {(node.block ?? 0) + 1}</span>
                </summary>
                {ports.length ? <table>
                  <thead><tr>{["端口", "方向", "数据类型", "属性"].map((label) => <th key={label}>{t(label)}</th>)}</tr></thead>
                  <tbody>{ports.map((port, index) => <tr key={index}>
                    <td>{port.formal_name || port.name}</td>
                    <td>{t(port.side === "in" ? "输入" : port.side === "out" ? "输出" : "未解析")}</td>
                    <td>{port.data_type || t("未解析")}</td>
                    <td>{[port.class_code === 5 ? "IN_OUT" : "", port.negated === true ? t("取反") : ""].filter(Boolean).join(" · ") || "—"}</td>
                  </tr>)}</tbody>
                </table> : <p className="muted">{t("无端口信息")}</p>}
              </details>;
            })}
            {!components.length && <p className="muted">{t("没有匹配的元件")}</p>}
          </div>
        </div>
      ) : visibleSection === "native" && vid && !preview ? (
        <NativeValidationPanel key={`${pid}/${vid}/${model.program}`} pid={pid} vid={vid}
          program={model.program} disabled={readOnly || busy} t={t} />
      ) : (
        <div className="fbd-sheet">
          <div className="fbd-inline">
            <select aria-label={t("声明表")} value={table} onChange={(event) => { setTable(event.target.value); setPage(0); }}>
              {editor.presentation.tables.map((name) => <option key={name}>{name}</option>)}
            </select>
          </div>
          <table>
            <thead><tr>{["名称", "数据类型", "类别", "初始值", "软元件", "注释"].map((label) => <th key={label}>{t(label)}</th>)}</tr></thead>
            <tbody>{rows.slice(page * 50, (page + 1) * 50).map((row) => <tr key={row.name}>
              {(["name", "data_type", "class_name", "initial_value", "device", "comment"] as const).map((field) => (
                <td key={field}>{row[field] || "—"}</td>
              ))}
            </tr>)}</tbody>
          </table>
          {!rows.length && <p className="muted">{t("此表没有标签")}</p>}
          {rows.length > 50 && <div className="fbd-inline">
            <Button disabled={page === 0} onClick={() => setPage((value) => value - 1)}>{t("上一页")}</Button>
            <span>{page + 1} / {Math.ceil(rows.length / 50)} · {rows.length}</span>
            <Button disabled={(page + 1) * 50 >= rows.length} onClick={() => setPage((value) => value + 1)}>{t("下一页")}</Button>
          </div>}
        </div>
      )}
    </div>
  );
}
