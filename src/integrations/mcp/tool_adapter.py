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

from .context_provider import ToolContextProvider
from agent_runtime.inspection_tools import output_schema


def mcp_tool(schema):
    function = schema["function"]
    return Tool(name=function["name"], description=function.get("description", ""),
                input_schema=copy.deepcopy(dict(function["parameters"])),
                output_schema=output_schema(function["name"]),
                annotations={"readOnlyHint": function["name"] in {
                    "get_current_project", "get_current_program_info", "read_network", "search_plc_manual",
                    "get_diagnostics", "validate_current_program", "list_gxworks2_sessions", "list_simulator_targets",
                    "read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps",
                    "read_fbd_libraries",
                    "read_fbd_definition",
                    "get_simulator_status", "read_simulator_devices", "list_projects", "get_operation_status",
                    "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
                    "get_simulator_trace", "stop_simulator_trace"},
                    "destructiveHint": function["name"] in {
                        "import_current_program_to_gxworks2", "execute_native_engineering"}})


logger = logging.getLogger(__name__)


def to_mcp_result(result: ToolResult, *, context=None, available_tools=None) -> CallToolResult:
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


class MCPToolAdapter:
    def __init__(self, runtime: ToolRuntime, context_provider: ToolContextProvider):
        self.runtime = runtime
        self.context_provider = context_provider
        from application.program_inspection import ProgramInspectionService
        self.inspection = ProgramInspectionService()

    def list_tools(self) -> list[Tool]:
        tools = []
        for schema in self.runtime.list_tools():
            function = schema["function"]
            name = function["name"]
            if name not in SAFE_TOOL_NAMES or name in FORBIDDEN_TOOL_NAMES:
                continue
            tools.append(mcp_tool(schema))
        return tools

    def call_tool(self, name: str, arguments: Any, call_id: str) -> CallToolResult:
        call = ToolCall(id=call_id, name=name, arguments=arguments)
        if name not in {tool.name for tool in self.list_tools()}:
            return _error(call, "UNKNOWN_TOOL", "Tool is not in the runtime's safe tool list.")
        from agent_runtime.plc_tools import DISCOVERY_TOOL_NAMES, ToolRegistry, build_tool_context
        try:
            decoded = ToolRegistry._decode_arguments(arguments)
        except ValueError as error:
            return _error(call, "INVALID_ARGUMENTS", str(error))
        try:
            independent = name in DISCOVERY_TOOL_NAMES - {"get_current_project"} or bool(decoded.get("source_ref"))
            context = build_tool_context({}) if independent else self.context_provider.get_context()
            context = replace(context, inspection=self.inspection)
        except Exception:
            logger.exception("Unable to load context for call_id=%s tool=%s", call_id, name)
            return _error(
                call, "CONTEXT_UNAVAILABLE",
                "Unable to load the configured project/version; check server logs.",
            )
        try:
            result = self.runtime.invoke(call, context)
            translated = to_mcp_result(result, context=context, available_tools={tool.name for tool in self.list_tools()})
        except Exception:
            logger.exception("Runtime failed for call_id=%s tool=%s", call_id, name)
            return _error(call, "TOOL_FAILED", "Tool execution failed; check server logs.")
        logger.info(
            "call_id=%s tool=%s project_id=%s version_id=%s is_error=%s",
            call_id, name, context.project_id, context.version_id, result.is_error,
        )
        return translated
