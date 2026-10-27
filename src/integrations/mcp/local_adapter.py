# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""MCP projection for the independent application session; no PLC operations."""
from __future__ import annotations

import logging
import uuid

from mcp.types import Tool

from agent_runtime.messages import ToolCall, ToolResult
from .tool_adapter import _error, to_mcp_result, mcp_tool


logger = logging.getLogger(__name__)


class LocalMCPToolAdapter:
    def __init__(self, session):
        self.session = session
        self._session_id = uuid.uuid4().hex
        self._call_ids = {}

    def list_tools(self):
        return [mcp_tool(schema) for schema in self.session.list_tools()]

    def call_tool(self, name, arguments, call_id):
        call = ToolCall(call_id, name, arguments)
        try:
            logical_id = self._call_ids.setdefault(str(call_id), self._session_id + "_" + uuid.uuid4().hex)
            result = self.session.invoke(ToolCall(logical_id, name, arguments))
            return to_mcp_result(ToolResult(call_id, name, result.content, result.data, result.is_error),
                                 available_tools={tool.name for tool in self.list_tools()})
        except Exception:
            logger.exception("Independent MCP call failed: tool=%s call_id=%s", name, call_id)
            return _error(call, "TOOL_FAILED", "Engineering operation failed; check the MCP server logs and saved receipt before retrying.")
