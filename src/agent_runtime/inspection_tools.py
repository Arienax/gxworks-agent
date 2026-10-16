"""Shared schemas and handlers for source discovery and runtime observation."""
from __future__ import annotations

from agent_runtime.tool_errors import ToolPreconditionError


def register_inspection_tools(registry):
    from agent_runtime.plc_tools import ToolDefinition
    ref = {"type": "string", "minLength": 1, "maxLength": 128}
    signal = {"type": "object", "properties": {
        "id": {"type": "string", "maxLength": 96}, "label": {"type": "string", "maxLength": 160},
        "address": {"type": "string", "maxLength": 32}, "data_type": {"enum": ["BOOL", "INT", "UINT", "WORD", "DINT", "UDINT", "DWORD", "REAL", "RAW"]},
        "role": {"enum": ["input", "output", "state"]}, "unit": {"type": "string", "maxLength": 24}},
        "required": ["id", "address", "data_type"], "additionalProperties": False}
    config = {"type": "object", "properties": {
        "title": {"type": "string", "maxLength": 160}, "plc_model": {"type": "string", "maxLength": 64},
        "signals": {"type": "array", "minItems": 1, "maxItems": 256, "items": signal},
        "sample_ms": {"type": "integer", "minimum": 50, "maximum": 5000},
        "duration_ms": {"type": "integer", "minimum": 50, "maximum": 300000},
        "initial": {"type": "object"},
        "steps": {"type": "array", "maxItems": 500, "items": {"type": "object", "properties": {
            "at_ms": {"type": "integer", "minimum": 0}, "label": {"type": "string", "maxLength": 160},
            "set": {"type": "object"}, "expect": {"type": "object"}},
            "required": ["at_ms"], "additionalProperties": False}}},
        "required": ["signals"], "additionalProperties": False}
    definitions = (
        ("list_gxworks2_sessions", "发现全部 GX Works2 原生工程候选、软件版本和可读状态；不需要 Agent 工程，不自动选择。",
         {}, (), "list_gxworks2_sessions"),
        ("read_gxworks2_snapshot", "按发现返回的 session_id 读取指定程序及注释的只读分析快照；默认 MAIN。生成临时导出，不保存原工程或 Agent 版本；返回 source_ref、CPU、来源和解析覆盖。",
         {"session_id": ref, "program": {"type": "string", "minLength": 1, "maxLength": 64,
                                         "pattern": "[^\\x00-\\x1f\\x7f]{1,64}"}},
         ("session_id",), "read_gxworks2_snapshot"),
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
        ("prepare_simulator_scenario", "通用类型化仿真方案：填写 signals 的名称、地址、BOOL/INT/UINT/WORD/DINT/UDINT/DWORD/REAL 类型，以及 initial、steps 的命名数值。返回现有测试 DSL suite 和共用 Web 总览 URL；不执行写入。实际测试沿用版本绑定、审批和既有执行器。",
         {"config": config}, ("config",), "prepare_simulator_scenario"),
        ("start_simulator_trace", "按 target_ref 对 signals 进行有限时长只读采样，自动返回 Web 风格的全部信号时间轴。sample_ms 最小 50，duration_ms 最大 300000；所有类型转换由 Core 完成。config.steps 只显示为计划，不执行写入或断言。",
         {"target_ref": ref, "config": config}, ("target_ref", "config"), "start_simulator_trace"),
        ("get_simulator_trace", "读取本会话仿真视图的实际状态、最新数值、采样数量和 URL。",
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
        "get_current_project": {"selection_status": {"enum": ["none", "selected"]},
            "project": {"type": ["object", "null"]}, "next_actions": {"type": "array"}},
        "list_gxworks2_sessions": {"sessions": {"type": "array"},
            "selection_status": {"enum": ["none", "candidate", "ambiguous"]}},
        "read_gxworks2_snapshot": {"source_ref": {"type": "string"}, "cpu_menu": {"type": "string"},
            "plc_model": {"type": "string"}, "program_name": {"type": "string"},
            "coverage": {"type": "object"}, "editable": {"const": False}, "read_at": {"type": "string"}},
        "list_simulator_targets": {"targets": {"type": "array"}, "available": {"type": "boolean"}},
        "get_simulator_status": {"target_ref": {"type": "string"}, "values": {"type": "object"},
            "run_state": {"enum": ["run", "not_running"]}, "program_correspondence": {"const": "unverified"}},
        "read_simulator_devices": {"target_ref": {"type": "string"}, "values": {"type": "object"},
            "program_correspondence": {"const": "unverified"}},
    }
    properties = data_properties.get(name, {})
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
