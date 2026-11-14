# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Translate schemas/calls/results without implementing any PLC operations."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import replace
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import FORBIDDEN_TOOL_NAMES, SAFE_TOOL_NAMES
from agent_runtime.runtime import ToolRuntime, public_tool_result_data
from application.context_units import ContextUnits, RESTORE_TOOL, restore_tool_schema

from .context_provider import ToolContextProvider
from agent_runtime.inspection_tools import output_schema


def mcp_tool(schema, *, read_budget_tokens=None):
    function = schema["function"]
    name = function["name"]
    description = function.get("description", "")
    parameters = copy.deepcopy(dict(function["parameters"]))
    from application.read_delivery import PAGED_READ_TOOLS
    if read_budget_tokens is not None and name in {*PAGED_READ_TOOLS, RESTORE_TOOL}:
        parameters["properties"].pop("read_budget_tokens", None)
        description = description.split(" 默认直接读取 data.document")[0]
        description += " 宿主负责交付预算和正文组装。默认直接读取完整结果。"
    return Tool(name=name, description=description,
                input_schema=parameters,
                output_schema=(None if function["name"] in {"search_plc_manual", "get_generation_context", "get_fbd_catalog", "get_design_context", RESTORE_TOOL}
                               else output_schema(function["name"])),
                annotations={"readOnlyHint": function["name"] in {RESTORE_TOOL,
                    "get_current_project", "get_project_spec", "get_current_program_info", "read_network", "search_plc_manual",
                    "get_diagnostics", "validate_current_program", "list_gxworks2_sessions", "list_simulator_targets",
                    "inspect_gxworks2_csv_files", "get_generation_context", "get_fbd_catalog",
                    "read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps",
                    "read_fbd_libraries",
                    "read_fbd_definition",
                    "get_simulator_status", "read_simulator_devices", "list_projects", "get_operation_status",
                    "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
                    "get_simulator_trace", "stop_simulator_trace"},
                    "destructiveHint": function["name"] in {
                        "import_current_program_to_gxworks2", "execute_native_engineering"}})


logger = logging.getLogger(__name__)


def _read_result(public, *, is_error=False):
    from shared.project_info import MCP_SERVER_INFO
    return CallToolResult(
        content=[TextContent(type='text', text=json.dumps(public, ensure_ascii=False, separators=(',', ':')))],
        is_error=is_error, meta={'io.modelcontextprotocol/serverInfo': copy.deepcopy(MCP_SERVER_INFO)})


def _read_result_fits(public, response=None):
    from application.read_delivery import read_limits, serialized_size
    wire = (response if response is not None else _read_result(public)).model_dump(by_alias=True, exclude_none=True)
    size = serialized_size(wire)
    tokens, byte_limit = read_limits(public)
    return size['estimated_tokens'] <= tokens and size['utf8_bytes'] <= byte_limit


def to_mcp_result(result: ToolResult, *, context=None, available_tools=None, read_units=None) -> CallToolResult:
    public = public_tool_result_data(result)
    if available_tools is not None:
        def filter_actions(value):
            if isinstance(value, dict):
                return {key: [action for action in item if isinstance(action, dict) and action.get("tool") in available_tools]
                        if key == "next_actions" and isinstance(item, list) else filter_actions(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [filter_actions(item) for item in value]
            return value
        public = filter_actions(public)
    from application.read_delivery import PAGED_READ_TOOLS
    if (read_units is not None and result.name in PAGED_READ_TOOLS and not result.is_error
            and 'conversation_unit_ids' not in (public.get('data') or {})):
        public = read_units.project(public, fits=_read_result_fits)
    if result.name in PAGED_READ_TOOLS or result.name == RESTORE_TOOL:
        # Model hosts consume one JSON text body. Measure the actual serialized
        # MCP wrapper, including escaping, before returning the complete body.
        response = _read_result(public, is_error=result.is_error)
        if not _read_result_fits(public, response):
            failure = {'ok': False, 'tool': result.name, 'error': {
                'code': 'READ_OBJECT_TOO_LARGE', 'message': '最终 MCP 返回超过读取上限；未截断对象。'}}
            return CallToolResult(content=[TextContent(type='text', text=json.dumps(failure, ensure_ascii=False))], is_error=True)
        return response
    text = (
        json.dumps(public, ensure_ascii=False, separators=(",", ":"))
        if result.data
        else result.content
    )
    audit = {"call_id": result.call_id, "tool": result.name}
    if context is not None:
        audit.update(project_id=context.project_id, version_id=context.version_id)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=public if result.data else None,
        is_error=result.is_error,
        meta={"gxworks": audit},
    )


def _error(call: ToolCall, code: str, message: str) -> CallToolResult:
    data = {
        "ok": False,
        "tool": call.name,
        "error": {"code": code, "message": message, "next_actions": []},
    }
    return to_mcp_result(ToolResult(call.id, call.name, "", data, is_error=True))


def restore_context_units(units, call, read_budget_tokens=None):
    from agent_runtime.tool_errors import ToolPreconditionError
    from agent_runtime.tool_registry import ToolRegistry
    try:
        args = ToolRegistry._decode_arguments(call.arguments)
        if read_budget_tokens is not None:
            args = {**args, 'read_budget_tokens': read_budget_tokens}
        data = units.restore(args, hosted=read_budget_tokens is not None)
        if read_budget_tokens is not None and not _read_result_fits(data):
            return _error(call, 'HOST_CONTEXT_TOO_LARGE', '完整资料超过宿主交付容量。可按具体缺项查询。')
    except ToolPreconditionError as error:
        data = {'ok': False, 'tool': call.name, 'error': error.payload()}
    except ValueError as error:
        return _error(call, 'INVALID_ARGUMENTS', str(error))
    return to_mcp_result(ToolResult(call.id, call.name, '', data, is_error=not data['ok']))


def invoke_host_read(call, invoke, read_budget_tokens, *, read_units=None, finish_read=None):
    """Consume a read sequence on behalf of a host with a configured budget."""
    from application.read_delivery import (MAX_RESOLVED_BYTES, MAX_RESOLVED_TOKENS, PAGED_READ_TOOLS,
                                           REVIEWED_READ_TOOLS, collect_host_read)
    args = {} if call.arguments in (None, "") else call.arguments
    if (read_budget_tokens is None or call.name not in PAGED_READ_TOOLS or not isinstance(args, dict)
            or args.get("cursor") or args.get("read_format") == "paged"):
        return invoke(call)
    page_budget = max(read_budget_tokens, MAX_RESOLVED_TOKENS)
    request = {**args, "read_format": "resolved", "read_budget_tokens": page_budget}
    deferred = finish_read is not None and call.name in REVIEWED_READ_TOOLS
    envelope = collect_host_read(call.name, request, call.id,
        lambda read_id, args: invoke(ToolCall(read_id, call.name, args, defer_read=deferred)).data)
    if envelope.get("ok"):
        document_id = envelope['data']['delivery']['document_id']
        envelope['data']['delivery']['limits'] = {'estimated_tokens': read_budget_tokens,
            'utf8_bytes': max(MAX_RESOLVED_BYTES, read_budget_tokens * 8)}

        def accept(value):
            if not _read_result_fits(value):
                return False
            if deferred and not finish_read(call.name, document_id, True):
                raise ValueError('Host could not register complete read delivery.')
            return True

        projected = read_units.project(envelope, fits=accept) if read_units is not None else envelope
        if projected is not envelope or accept(envelope):
            return ToolResult(call.id, call.name, "", projected)
        if deferred:
            finish_read(call.name, document_id, False)
    elif (envelope.get("error") or {}).get("code") not in {"READ_BUDGET_TOO_SMALL", "READ_OBJECT_TOO_LARGE"}:
        return ToolResult(call.id, call.name, "", envelope, is_error=True)
    return ToolResult(call.id, call.name, "", {"ok": False, "tool": call.name, "error": {
        "code": "HOST_CONTEXT_TOO_LARGE",
        "message": "完整资料超过宿主交付容量。可按具体缺项查询；完整交付需宿主提供足够容量。",
    }}, is_error=True)


class MCPToolAdapter:
    def __init__(self, runtime: ToolRuntime, context_provider: ToolContextProvider, *, read_budget_tokens=None):
        self.runtime = runtime
        self.context_provider = context_provider
        self.read_units = ContextUnits()
        self.read_budget_tokens = read_budget_tokens
        from application.program_inspection import ProgramInspectionService
        self.inspection = ProgramInspectionService()

    def list_tools(self, *, read_budget_tokens=None) -> list[Tool]:
        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        tools = []
        for schema in self.runtime.list_tools():
            function = schema["function"]
            name = function["name"]
            if name not in SAFE_TOOL_NAMES or name in FORBIDDEN_TOOL_NAMES:
                continue
            tools.append(mcp_tool(schema, read_budget_tokens=budget))
        return [*tools, mcp_tool(restore_tool_schema(), read_budget_tokens=budget)]

    def call_tool(self, name: str, arguments: Any, call_id: str, *, read_budget_tokens=None) -> CallToolResult:
        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        call = ToolCall(id=call_id, name=name, arguments=arguments)
        if name == RESTORE_TOOL:
            return restore_context_units(self.read_units, call, budget)
        if name not in {tool.name for tool in self.list_tools()}:
            return _error(call, "UNKNOWN_TOOL", "Tool is not in the runtime's safe tool list.")
        from agent_runtime.plc_tools import DISCOVERY_TOOL_NAMES, ToolRegistry, build_tool_context
        try:
            decoded = ToolRegistry._decode_arguments(arguments)
        except ValueError as error:
            return _error(call, "INVALID_ARGUMENTS", str(error))
        try:
            independent = name in DISCOVERY_TOOL_NAMES - {"get_current_project"} or bool(decoded.get("source_ref"))
            definition = self.runtime.registry._tools[name]
            get_context = (getattr(self.context_provider, 'get_model_context', self.context_provider.get_context)
                           if definition.context_requirement == 'model' else self.context_provider.get_context)
            context = build_tool_context({}) if independent else get_context()
            context = replace(context, inspection=self.inspection)
        except Exception:
            logger.exception("Unable to load context for call_id=%s tool=%s", call_id, name)
            return _error(
                call, "CONTEXT_UNAVAILABLE",
                "Unable to load the configured project/version; check server logs.",
            )
        try:
            def finish_read(tool, document_id, accepted):
                return self.runtime.registry._read_delivery.finish_host_read(
                    tool, document_id, context.project_id, accepted=accepted)

            result = invoke_host_read(ToolCall(call_id, name, decoded),
                lambda request: self.runtime.invoke(request, context), budget,
                read_units=self.read_units, finish_read=finish_read)
            translated = to_mcp_result(result, context=context, read_units=self.read_units,
                                       available_tools={tool.name for tool in self.list_tools()})
        except Exception:
            logger.exception("Runtime failed for call_id=%s tool=%s", call_id, name)
            return _error(call, "TOOL_FAILED", "Tool execution failed; check server logs.")
        logger.info(
            "call_id=%s tool=%s project_id=%s version_id=%s is_error=%s",
            call_id, name, context.project_id, context.version_id, result.is_error,
        )
        return translated
