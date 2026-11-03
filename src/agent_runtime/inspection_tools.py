# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Shared schemas and handlers for source discovery and runtime observation."""
from __future__ import annotations

from agent_runtime.tool_errors import ToolPreconditionError


def register_inspection_tools(registry):
    from agent_runtime.plc_tools import ToolDefinition
    from simulator.models import MAX_TRACE_DEVICES
    from simulator.signals import SIGNAL_TYPES
    ref = {"type": "string", "minLength": 1, "maxLength": 128}
    value = {"type": ["boolean", "number"]}
    inputs = {"type": "object", "additionalProperties": value,
              "description": "按 signals.id 填写 role=input 的信号值；BOOL 用 true/false 或 0/1，数值信号按声明类型填写。"}
    signal = {"type": "object", "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 96, "pattern": "^[A-Za-z_][A-Za-z0-9_.-]{0,95}$",
               "description": "唯一信号名，供 initial/set/expect 及返回值引用。"},
        "label": {"type": "string", "maxLength": 160, "description": "显示名称，默认使用 id。"},
        "address": {"type": "string", "minLength": 1, "maxLength": 32,
                    "description": "BOOL 使用位软元件；数值类型使用 D 起始地址，32 位类型自动读取连续两个字，低字在前。"},
        "data_type": {"type": "string", "enum": list(SIGNAL_TYPES),
                      "description": "软元件存储值的解码类型；RAW 保留原始整数，仅用于观察。"},
        "role": {"type": "string", "enum": ["input", "output", "state"], "default": "state"},
        "unit": {"type": "string", "maxLength": 24}},
        "required": ["id", "address", "data_type"], "additionalProperties": False}
    config = {"type": "object", "properties": {
        "title": {"type": "string", "maxLength": 160, "default": "仿真观察"},
        "plc_model": {"type": "string", "maxLength": 64, "default": "FX3U",
                      "description": "方案按此型号校验地址；实时 Simulator2 采样当前使用 FX3U 路由。"},
        "signals": {"type": "array", "minItems": 1, "maxItems": MAX_TRACE_DEVICES, "items": signal,
                    "description": f"最多 {MAX_TRACE_DEVICES} 个信号，展开并去重后的物理地址也最多 {MAX_TRACE_DEVICES} 个。"},
        "sample_ms": {"type": "integer", "minimum": 50, "maximum": 5000, "default": 100},
        "duration_ms": {"type": "integer", "minimum": 50, "maximum": 300000, "default": 10000},
        "initial": {**inputs, "description": inputs["description"] + "覆盖 steps.set 中的全部输入信号。"},
        "steps": {"type": "array", "maxItems": 500, "items": {"type": "object", "properties": {
            "at_ms": {"type": "integer", "minimum": 0,
                      "description": "相对开始的毫秒数，按非递减顺序排列且在 duration_ms 内。"},
            "label": {"type": "string", "maxLength": 160}, "set": inputs,
            "expect": {"type": "object", "description": "按 signals.id 填写期望值或 {value, tolerance}；tolerance 为非负绝对误差，默认 0。",
                       "additionalProperties": {"oneOf": [value, {"type": "object", "properties": {
                           "value": value, "tolerance": {"type": "number", "minimum": 0}},
                           "required": ["value"], "additionalProperties": False}]}}},
            "required": ["at_ms"], "additionalProperties": False}}},
        "required": ["signals"], "additionalProperties": False}
    definitions = (
        ('inspect_gxw_project', '离线读取 Ladder/ST/FBD/SFC GXW：提供 data_base64 或本机 path（二选一），无需打开 GX Works2 或选择 Agent 工程。返回实际 CPU、Program/FB/FUNCTION 清单、声明、注释和各源码读写能力；可分析的所选源码返回 source_ref，多程序用 program 选择。原文件保持不变。',
         {'data_base64': {'type': 'string', 'minLength': 1, 'maxLength': 41943040},
          'path': {'type': 'string', 'minLength': 1, 'maxLength': 32767},
          'program': {'type': 'string', 'minLength': 1, 'maxLength': 512}}, (), 'inspect_gxw_project'),
        ("list_gxworks2_sessions", "发现全部 GX Works2 原生工程候选、软件版本和可读状态；不需要 Agent 工程，不自动选择。",
         {}, (), "list_gxworks2_sessions"),
        ("read_gxworks2_snapshot", "按发现返回的 session_id 读取指定程序及注释的只读分析快照；默认 MAIN。生成临时导出，不保存原工程或 Agent 版本；返回 source_ref、CPU、来源和解析覆盖。",
         {"session_id": ref, "program": {"type": "string", "minLength": 1, "maxLength": 64,
                                         "pattern": "[^\\x00-\\x1f\\x7f]{1,64}"}},
         ("session_id",), "read_gxworks2_snapshot"),
        ("inspect_gxworks2_csv_files", "直接分析已有的 GX Works2 程序 CSV 与软元件注释 CSV：各自提供内容 data_base64 或本机 path（二选一），并显式指定 plc_model。无需打开 GX Works2 或选择 Agent 工程；返回只读 source_ref、指令读取/有序 IR/图形预览/语义核验分项状态，以及可按网络读取的 CSV 行列来源。",
         {"program_data_base64": {'type': 'string', 'minLength': 1, 'maxLength': 41943040},
          "program_path": {'type': 'string', 'minLength': 1, 'maxLength': 32767},
          "comment_data_base64": {'type': 'string', 'minLength': 1, 'maxLength': 41943040},
          "comment_path": {'type': 'string', 'minLength': 1, 'maxLength': 32767},
          "plc_model": {"type": "string", "minLength": 1, "maxLength": 64},
          "program_name": {"type": "string", "minLength": 1, "maxLength": 512}},
         ("plc_model",), "inspect_gxworks2_csv_files"),
        ("list_simulator_targets", "发现已连接 Simulator2 网关的只读目标；不需要工程，不启动、连接、停止或下载模拟程序。程序对应关系保留未验证。",
         {}, (), "list_simulator_targets"),
        ("get_simulator_status", "读取明确 target_ref 的型号专用 RUN 监控值；只观察当前模拟器，不推断程序对应关系或真实 PLC 状态。",
         {"target_ref": ref}, ("target_ref",), "get_simulator_status"),
        ("read_simulator_devices", "读取明确 target_ref 的 1 至 64 个软元件；复用 Core 地址策略，只读已连接 Simulator2，不强制值、不复位、不访问真实 PLC。",
         {"target_ref": ref, "addresses": {"type": "array", "minItems": 1, "maxItems": 64,
                                           "items": {"type": "string"}}},
         ("target_ref", "addresses"), "read_simulator_devices"),
        ("connect_simulator", "连接现有 GX Simulator2，必要时启动本机专用网关并返回 target_ref；不启动、下载、编译、修改或停止 PLC 程序。当前原生路由只支持 FX3U。",
         {}, (), "connect_simulator"),
        ("prepare_simulator_scenario", "按 signals 定义类型化仿真方案，initial/steps 使用信号 id 和类型化数值，expect 支持绝对容差。返回测试 DSL suite、execution_started=false、view_ref 和 Web 时间轴 URL；实际执行通过既有版本绑定测试服务。RAW 信号用于观察。",
         {"config": config}, ("config",), "prepare_simulator_scenario"),
        ("start_simulator_trace", "对已连接的 FX3U Simulator2 target_ref 进行有限时长只读采样；按 data_type 解码位值或 D 字值，返回 view_ref、最新数值和 Web 时间轴 URL。sample_ms 默认100，duration_ms 默认10000。initial/steps 表达计划，采样期间不执行写入或断言；无需选择 Agent 工程。",
         {"target_ref": ref, "config": config}, ("target_ref", "config"), "start_simulator_trace"),
        ("get_simulator_trace", "读取本会话视图的 status、latest.values/errors、sample_count、dropped_samples、elapsed_ms 和 URL；完整采样序列在 Web 时间轴查看，工具返回摘要（samples 为空数组）。",
         {"view_ref": ref}, ("view_ref",), "get_simulator_trace"),
        ("stop_simulator_trace", "停止指定视图的只读采样并保留回放；不停止模拟器或 PLC 程序。",
         {"view_ref": ref}, ("view_ref",), "stop_simulator_trace"),
    )
    for name, description, properties, required, method in definitions:
        def handler(context, arguments, method=method):
            if context.inspection is None:
                raise ToolPreconditionError("INSPECTION_UNAVAILABLE", "当前主机未提供原生发现和快照服务。")
            return getattr(context.inspection, method)(**arguments)
        registry.register(ToolDefinition(name, description, {
            "type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}, handler))
    text = {"type": "string", "minLength": 1, "maxLength": 512}
    program = {"type": "string", "minLength": 1, "maxLength": 512}
    native_tools = (
        ("read_native_device_assignments", "在隔离工作区编译当前已保存 GXW，查询声明或 FB 实例的原生软元件分配。queries 默认使用局部 POU 名称及声明名；全局变量指定 scope=global、source=全局声明表名。最多 128 项。编译、检查、诊断和分配分别返回。",
         {"queries": {"type": "array", "minItems": 1, "maxItems": 128, "items": {
             "type": "object", "properties": {"source": text, "name": text, "scope": {"enum": ["local", "global"]}}, "required": ["source", "name"], "additionalProperties": False}},
          "program": program, "compare_saved_code": {"type": "boolean"}}, ("queries",)),
        ("compare_native_compiled_code", "比较当前 GXW 已保存的三个原生代码通道与本次独立编译输出；分别返回资源和通道的差异、源码绑定及检查状态。差异是观测结果，不阻止保存；相同也不证明模拟器或 PLC 执行了该代码。",
         {"program": program}, ()),
        ("locate_native_code_steps", "将明确资源和非负代码步号定位到当前 GXW 的源码与实例候选。最多 64 项，共用本次隔离编译的源码、声明和引用；映射不唯一时保留未解析及候选。步号可来自运行错误监控，但本工具不读取设备或推断执行对应关系。",
         {"queries": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
             "type": "object", "properties": {"resource": text, "step": {"type": "integer", "minimum": 0, "maximum": 2147483647}},
             "required": ["resource", "step"], "additionalProperties": False}},
          "program": program, "compare_saved_code": {"type": "boolean"}}, ("queries",)),
    )
    for name, description, properties, required in native_tools:
        def handler(context, arguments, name=name):
            from application.native_validation import validate_native_snapshot
            snapshot = context.native_snapshot if context.native_snapshot is not None else context.fbd_baseline
            if snapshot is None:
                raise ToolPreconditionError("NO_PROGRAM_SOURCE", "原生查询需要当前已保存版本的 GXW 快照。")
            query_key = 'assignments' if name == 'read_native_device_assignments' else 'locations'
            queries = arguments.get('queries', [])
            bound = 128 if query_key == 'assignments' else 64
            if not isinstance(queries, list) or (name != 'compare_native_compiled_code' and not 1 <= len(queries) <= bound):
                raise ToolPreconditionError("INVALID_ARGUMENTS", '原生查询数量超出本次调用范围。')
            inspection = {'assignments': [], 'locations': [],
                          'saved_code': name == 'compare_native_compiled_code' or arguments.get('compare_saved_code', False)}
            if queries:
                inspection[query_key] = queries
            from gxw.native_diagnostics import native_inspection_queries
            from gxw.models import GXWFormatError
            try:
                inspection = native_inspection_queries(snapshot, inspection)
            except GXWFormatError as error:
                raise ToolPreconditionError("INVALID_ARGUMENTS", str(error)) from error
            return validate_native_snapshot(snapshot, project_id=context.project_id, version_id=context.version_id,
                program=arguments.get('program') or (context.version or {}).get('program_name'), expected_cpu=context.plc_model,
                target_mode=(context.version or {}).get('target_mode'), st_text=context.st_text, program_ir=context.program_ir,
                sfc_graph=context.sfc_graph, inspection=inspection)
        registry.register(ToolDefinition(name, description, {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}, handler))


def output_schema(name):
    """MCP output contracts; provider function schemas remain unchanged."""
    data_properties = {
        'inspect_gxw_project': {'source_ref': {'type': ['string', 'null']},
            'cpu_menu': {'type': 'string'}, 'programs': {'type': 'array'},
            'write_backend': {'const': 'offline'}, 'native_required': {'const': False}},
        "get_current_project": {"selection_status": {"enum": ["none", "selected"]},
            "project": {"type": ["object", "null"]}, "next_actions": {"type": "array"}},
        "list_gxworks2_sessions": {"sessions": {"type": "array"},
            "selection_status": {"enum": ["none", "candidate", "ambiguous"]}},
        "read_gxworks2_snapshot": {"source_ref": {"type": "string"}, "cpu_menu": {"type": "string"},
            "plc_model": {"type": "string"}, "program_name": {"type": "string"},
            "coverage": {"type": "object"}, "step_width": {"type": ["object", "null"]},
            "editable": {"const": False}, "read_at": {"type": "string"}},
        "inspect_gxworks2_csv_files": {"source_ref": {"type": "string"}, "kind": {"const": "csv_file_snapshot"},
            "plc_model": {"type": "string"}, "program_name": {"type": "string"},
            "origin": {"type": "object"}, "coverage": {"type": "object"},
            "step_width": {"type": ["object", "null"]},
            "editable": {"const": False}, "read_at": {"type": "string"}},
        "list_simulator_targets": {"targets": {"type": "array"}, "available": {"type": "boolean"}},
        "connect_simulator": {"targets": {"type": "array"}, "available": {"type": "boolean"}},
        "get_simulator_status": {"target_ref": {"type": "string"}, "values": {"type": "object"},
            "run_state": {"enum": ["run", "not_running"]}, "program_correspondence": {"const": "unverified"}},
        "read_simulator_devices": {"target_ref": {"type": "string"}, "values": {"type": "object"},
            "program_correspondence": {"const": "unverified"}},
    }
    properties = data_properties.get(name, {})
    if name in {"prepare_simulator_scenario", "start_simulator_trace", "get_simulator_trace", "stop_simulator_trace"}:
        properties = {
            "view_ref": {"type": "string"}, "url": {"type": "string"},
            "schema_version": {"const": 1}, "view_id": {"type": "string"},
            "config": {"type": "object", "description": "规范化的信号、展开地址、采样周期及计划事件。"},
            "source": {"type": "object", "description": "观察来源、目标及程序对应关系。"},
            "binding": {"type": "object"}, "status": {"type": "string"}, "error": {"type": "string"},
            "revision": {"type": "integer"}, "sequence": {"type": "integer"},
            "sample_count": {"type": "integer", "description": "累计采样数，包含已移出的旧样本。"},
            "dropped_samples": {"type": "integer"}, "elapsed_ms": {"type": "integer"},
            "latest": {"type": ["object", "null"], "description": "最近一次采样；尚无采样时为 null。",
                       "properties": {"seq": {"type": "integer"}, "at_ms": {"type": "integer"},
                           "read_at": {"type": "string"}, "values": {"type": "object"},
                           "errors": {"type": "object"}, "case": {"type": "string"}},
                       "required": ["seq", "at_ms", "read_at", "values", "errors", "case"]},
            "samples": {"type": "array", "maxItems": 0, "description": "摘要中为空；完整序列由 URL 对应的视图提供。"},
        }
        if name == "prepare_simulator_scenario":
            properties.update(suite={"type": "object"}, execution_started={"const": False}, next_step={"type": "string"})
    if name in {"read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps"}:
        properties = {"snapshot": {"type": "object"}, "compilation": {"type": "object"},
                      "public_check": {"type": "object"}, "diagnostic_projection": {"type": "object"},
                      "inspection": {"type": "object", "required": ["source_context", "execution_correspondence", "assignments", "locations", "saved_code"]}}
    return {"type": "object", "required": ["ok", "tool"], "properties": {
        "ok": {"type": "boolean"}, "tool": {"type": "string"},
        "data": {"type": "object", "properties": properties},
        "error": {"type": "object", "required": ["code", "message"], "properties": {
            "code": {"type": "string"}, "message": {"type": "string"}, "next_actions": {"type": "array"}}}},
        "allOf": [{"if": {"properties": {"ok": {"const": True}}},
                   "then": {"required": ["data"], "properties": {"data": {"required": list(properties)}}},
                   "else": {"required": ["error"]}}]}
