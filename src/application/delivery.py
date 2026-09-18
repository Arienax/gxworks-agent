"""Portable handoff summaries built from the selected version and stored evidence."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from application.projects import public


def delivery_summary(workbench, project_id, version_id):
    projects = workbench.projects
    project = projects.raw_project(project_id)
    version = projects.raw_version(project_id, version_id)
    program = projects.verified_program(project_id, version_id)
    artifacts = []
    for artifact in projects.artifacts(project_id, version_id):
        item = dict(artifact)
        if item["available"]:
            item["sha256"] = hashlib.sha256(projects.artifact(project_id, version_id, item["id"]).read_bytes()).hexdigest()
        artifacts.append(item)
    runs = []
    for entry in version.get("simulator_runs") or []:
        if not isinstance(entry, dict):
            runs.append({"run_id": None, "status": "evidence_unavailable"})
            continue
        try:
            evidence = projects.simulator_run(project_id, version_id, entry["run_id"])
            runs.append({"run_id": entry["run_id"], "status": evidence.get("verification", {}).get("status", "unverified"),
                         "verification": evidence.get("verification"),
                         "binding": evidence.get("binding"),
                         "backend_kinds": evidence.get("result", {}).get("backend_kinds", []),
                         "counts": evidence.get("result", {}).get("counts", {}),
                         "suite_name": evidence.get("suite", {}).get("name")})
        except (KeyError, ValueError):
            runs.append({"run_id": entry.get("run_id"), "status": "evidence_unavailable"})
    native = None
    if version.get("target_mode") == "fbd":
        from application.native_validation import NativeValidationService
        native = NativeValidationService(workbench).list(project_id, version_id)
    requirements = []
    if version.get("target_mode") == "ladder" and program:
        from application.simulation_workbench import SimulationWorkbenchService
        requirements = SimulationWorkbenchService(workbench).read(project_id, version_id)["requirements"]
    diff = None
    if version.get("target_mode") == "ladder" and version.get("parent_version_id"):
        from plc.core import PLCCore
        parent = projects.verified_program(project_id, version["parent_version_id"])
        if parent:
            detail = PLCCore().diff_programs(parent, program)
            diff = {k: detail[k] for k in ("added", "deleted", "modified", "device_comment_changes", "network_order_changed", "property_changes")}
    result = public({"project_id": project_id, "project_name": project["name"], "version_id": version_id,
        "is_active_version": project.get("active_version_id") == version_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "plc_model": version.get("plc_model"),
        "target_mode": version.get("target_mode"), "summary": version.get("summary"),
        "confirmed_spec": version.get("confirmed_spec_snapshot"), "confirmed_spec_hash": version.get("confirmed_spec_hash"),
        "ir_sha256": version.get("ir_sha256"), "artifacts": artifacts, "changes": diff,
        "validation_profile": version.get("validation_profile", "strict"),
        "static_validation": version.get("validation"), "simulation_runs": runs,
        "requirements": requirements, "native_validation": native,
        "reports": [r for r in projects.reports(project_id) if r.get("base_version_id") == version_id],
        "device_io": (program or {}).get("devices", {}),
        "fbd_objects": (program or {}).get("nodes", []) if version.get("target_mode") == "fbd" else [],
        "fbd_declarations": (program or {}).get("labels", {}) if version.get("target_mode") == "fbd" else {},
        "limitations": ["生成及保存本地版本不能证明原生编译或运行正确。",
                         "需求与测试的关联由设计者确认，不能据此推导全部需求已验证。",
                         "内存测试后端只验证软件流程；真实设备运行结果需单独核对。"]})
    result["markdown"] = render_delivery(result)
    return result


def render_delivery(value):
    def text(v):
        # Operator/model text is literal report content, not executable Markdown
        # (images, HTML and invented headings must not be introduced by evidence).
        return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", str(v if v is not None else "未记录"))

    def cell(v):
        return text(v).replace("\n", " ").replace("\r", " ")

    def readable(v):
        if v is None or v == "":
            return "待确认"
        if isinstance(v, bool):
            return "是" if v else "否"
        if isinstance(v, list):
            return "、".join(readable(item) for item in v)
        if isinstance(v, dict):
            for key in ("label", "name", "text", "value"):
                if key in v and not isinstance(v[key], (list, dict)):
                    return readable(v[key])
            return "已记录结构化配置，详见已确认规格"
        return str(v)
    lines = [f"# 工程交付摘要 · {cell(value['project_name'])}", "",
             f"版本：{value['version_id']} · {value['plc_model']} · {value['target_mode']}",
             f"导出时间：{value['created_at']}", "",
             f"程序指纹：{value.get('ir_sha256') or '未记录 / 使用产物指纹'}",
             f"规格指纹：{value.get('confirmed_spec_hash') or '未记录'}", "",
             text(value.get("summary") or "此版本没有工程说明。"), "", "## I/O 与引用", "",
             "| 地址 | 说明 | 读取网络 | 写入网络 |", "| --- | --- | --- | --- |"]
    for address, device in value["device_io"].items():
        lines.append(f"| {cell(address)} | {cell(device.get('comment', ''))} | {cell(', '.join(device.get('read_by', [])))} | {cell(', '.join(device.get('written_by', [])))} |")
    if not value["device_io"]:
        lines.append("| 未提供梯形图地址引用表 | FBD 对象及声明见下方 | — | — |")
    if value.get("fbd_objects"):
        lines += ["", "## FBD 对象与声明", "", "| 对象 | 类型 | 设备或实例符号 |", "| --- | --- | --- |"]
        for node in value["fbd_objects"]:
            lines.append(f"| {cell(node['id'])} | {cell(node['template'])} | {cell(node['symbol'])} |")
        for name, rows in value.get("fbd_declarations", {}).items():
            lines += ["", f"声明表：{cell(name)}", "", "| 名称 | 类型 | 设备 | 注释 |", "| --- | --- | --- | --- |"]
            for row in rows:
                lines.append(f"| {cell(row.get('name'))} | {cell(row.get('data_type'))} | {cell(row.get('device', ''))} | {cell(row.get('comment', ''))} |")
    lines += ["", "## 已确认规格", ""]
    spec = value.get("confirmed_spec")
    if isinstance(spec, dict) and spec:
        lines.append(text(spec.get("summary") or spec.get("text") or "已保存规格；需求细节见以下约定。"))
        approach = spec.get("selected_approach")
        if approach:
            lines += ["", "### 选定方案", ""]
            if isinstance(approach, dict):
                lines.append(text(approach.get("name") or approach.get("title") or "已选方案"))
                if approach.get("description"):
                    lines += ["", text(approach["description"])]
            else:
                lines.append(text(readable(approach)))
        io_rows = [row for row in (spec.get("io_table") or []) if isinstance(row, dict)]
        if io_rows:
            lines += ["", "### 约定 I/O", "", "| 地址 | 用途 | 类型 |", "| --- | --- | --- |"]
            for row in io_rows:
                lines.append(f"| {cell(readable(row.get('address')))} | {cell(readable(row.get('label') or row.get('comment')))} | {cell(readable(row.get('kind')))} |")
        parameters = [row for row in (spec.get("parameters") or []) if isinstance(row, dict)]
        if parameters:
            lines += ["", "### 参数与确认事项", "", "| 事项 | 已确认值 |", "| --- | --- |"]
            for row in parameters:
                lines.append(f"| {cell(readable(row.get('name') or row.get('label') or row.get('id')))} | {cell(readable(row.get('value')))} |")
        if spec.get("user_notes"):
            lines += ["", "### 工程备注", "", text(readable(spec["user_notes"]))]
    else:
        lines.append("此版本未保存已确认规格快照。")
    lines += ["", "## 结构与静态检查", ""]
    validation = value.get("static_validation") or {}
    if value.get("validation_profile") == "generation_structural":
        lines.append("保存时检查层级：生成结构校验；未将全部语义及策略检查作为保存门槛。请结合下方评审与运行证据复核。")
    lines.append(f"记录状态：{cell(validation.get('status'))}；此状态不代表原生编译通过。")
    lines.extend(f"- {cell(message)}" for message in validation.get("messages") or [])
    for report in value.get("reports") or []:
        lines.append(f"- 评审 {cell(report.get('report_id'))}：{cell(report.get('status'))}；{cell(report.get('summary'))}")
    lines += ["", "## 本版变更", ""]
    diff = value.get("changes")
    if diff:
        for label, key in (("新增网络", "added"), ("删除网络", "deleted"), ("修改网络", "modified")):
            lines.append(f"- {label}：{', '.join(diff[key]) or '无'}")
        for change in diff.get("device_comment_changes") or []:
            lines.append(f"- 地址注释 {cell(change['address'])}：{cell(change.get('before'))} → {cell(change.get('after'))}")
        if diff.get("network_order_changed"):
            lines.append("- 网络顺序已变化。")
        if diff.get("property_changes"):
            lines.append("- 其他程序属性变化：" + cell(", ".join(diff["property_changes"])))
    else:
        lines.append("此摘要未提供与父版本的梯形图比较。")
    lines += ["", "## 需求与测试关联", ""]
    for requirement in value["requirements"]:
        tests = ", ".join(f"{r['plan_id']}/{r['test_name']}" for r in requirement["tests"])
        lines.append(f"- {cell(requirement['id'])}：{cell(requirement['text'])}；关联测试：{cell(tests or '未关联')}")
    if not value["requirements"]:
        lines.append("没有可列出的结构化需求条款。")
    lines += ["", "## 仿真证据", ""]
    for run in value["simulation_runs"]:
        lines.append(f"- {cell(run['run_id'])}：{cell(run['status'])}；后端：{cell(', '.join(run.get('backend_kinds') or []) or '未提供后端证据')}")
        if run.get("binding"):
            lines.append(f"  程序 SHA256：{cell(run['binding'].get('ir_sha256'))}；测试 SHA256：{cell(run['binding'].get('suite_sha256'))}；结果 SHA256：{cell(run['binding'].get('result_sha256'))}")
        if (run.get("verification") or {}).get("category"):
            lines.append(f"  证据说明：{cell(run['verification']['category'])}；{cell(run['verification'].get('detail', ''))}")
    if not value["simulation_runs"]:
        lines.append("尚无保存的仿真执行证据。")
    lines += ["", "## 原生验证", ""]
    records = (value.get("native_validation") or {}).get("records", [])
    lines.append("自动原生验证：未提供通过证明。")
    for record in records:
        outcome = {"passed": "报告通过", "failed": "报告失败", "inconclusive": "报告待确认"}.get(record["outcome"], record["outcome"])
        lines.append(f"- 操作员记录 {cell(record['id'])}：{cell(outcome)}；绑定当前文件：{readable(record['binding_current'])}；未自动验证。")
        lines.append(f"  操作员：{cell(record['operator'])}；工具：{cell(record['tool_version'])}；源 GXW SHA256：{record['source_gxw_sha256']}")
        lines.extend("> " + text(line) for line in record["report"].splitlines())
        attachment = record.get("native_gxw")
        if attachment:
            lines.append(f"  原生附件：{cell(attachment['filename'])}；SHA256：{attachment['sha256']}；附件完整：{attachment['integrity_verified']}；与源文件字节一致：{attachment['matches_source_bytes']}；所选程序对象及声明一致：{attachment['selected_program_matches']}")
    lines += ["", "## 交付产物指纹", "", "| 文件 | 可用 | SHA256 |", "| --- | --- | --- |"]
    for artifact in value["artifacts"]:
        lines.append(f"| {cell(artifact.get('filename', artifact['id']))} | {'是' if artifact['available'] else '否'} | {artifact.get('sha256', '未提供')} |")
    lines += ["", "## 待核对边界", ""] + [f"- {text}" for text in value["limitations"]]
    return "\n".join(lines) + "\n"
