"""Translate schemas/calls/results without implementing any PLC operations."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import FORBIDDEN_TOOL_NAMES, SAFE_TOOL_NAMES
from agent_runtime.runtime import ToolRuntime, public_tool_result_data

from .context_provider import ToolContextProvider


logger = logging.getLogger(__name__)


def to_mcp_result(result: ToolResult, *, context=None) -> CallToolResult:
    public = public_tool_result_data(result)
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
        "error": {"code": code, "message": message},
    }
    return to_mcp_result(ToolResult(call.id, call.name, "", data, is_error=True))


class MCPToolAdapter:
    def __init__(self, runtime: ToolRuntime, context_provider: ToolContextProvider):
        self.runtime = runtime
        self.context_provider = context_provider

    def list_tools(self) -> list[Tool]:
        tools = []
        for schema in self.runtime.list_tools():
            function = schema["function"]
            name = function["name"]
            if name not in SAFE_TOOL_NAMES or name in FORBIDDEN_TOOL_NAMES:
                continue
            tools.append(
                Tool(
                    name=name,
                    description=function.get("description", ""),
                    input_schema=copy.deepcopy(dict(function["parameters"])),
                )
            )
        return tools

    def call_tool(self, name: str, arguments: Any, call_id: str) -> CallToolResult:
        call = ToolCall(id=call_id, name=name, arguments=arguments)
        if name not in {tool.name for tool in self.list_tools()}:
            return _error(call, "UNKNOWN_TOOL", "Tool is not in the runtime's safe tool list.")
        try:
            context = self.context_provider.get_context()
        except Exception:
            logger.exception("Unable to load context for call_id=%s tool=%s", call_id, name)
            return _error(
                call, "CONTEXT_UNAVAILABLE",
                "Unable to load the configured project/version; check server logs.",
            )
        try:
            result = self.runtime.invoke(call, context)
            translated = to_mcp_result(result, context=context)
        except Exception:
            logger.exception("Runtime failed for call_id=%s tool=%s", call_id, name)
            return _error(call, "TOOL_FAILED", "Tool execution failed; check server logs.")
        logger.info(
            "call_id=%s tool=%s project_id=%s version_id=%s is_error=%s",
            call_id, name, context.project_id, context.version_id, result.is_error,
        )
        return translated
