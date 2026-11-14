# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""MCP projection for the independent application session; no PLC operations."""
from __future__ import annotations

import logging
import uuid

from mcp.types import Tool

from agent_runtime.messages import ToolCall, ToolResult
from .tool_adapter import _error, to_mcp_result, mcp_tool, restore_context_units, invoke_host_read
from application.context_units import ContextUnits, RESTORE_TOOL, restore_tool_schema


logger = logging.getLogger(__name__)


class LocalMCPToolAdapter:
    def __init__(self, session, *, read_budget_tokens=None):
        self.session = session
        self.read_units = ContextUnits()
        self.read_budget_tokens = read_budget_tokens
        self._session_id = uuid.uuid4().hex
        self._call_ids = {}

    def list_tools(self, *, read_budget_tokens=None):
        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        return [mcp_tool(schema, read_budget_tokens=budget)
                for schema in [*self.session.list_tools(), restore_tool_schema()]]

    def call_tool(self, name, arguments, call_id, *, read_budget_tokens=None):
        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        call = ToolCall(call_id, name, arguments)
        if name == RESTORE_TOOL:
            return restore_context_units(self.read_units, call, budget)
        try:
            logical_id = self._call_ids.setdefault(str(call_id), self._session_id + "_" + uuid.uuid4().hex)
            result = invoke_host_read(ToolCall(logical_id, name, arguments), self.session.invoke,
                                      budget, read_units=self.read_units,
                                      finish_read=getattr(self.session, 'finish_host_read', None))
            return to_mcp_result(ToolResult(call_id, name, result.content, result.data, result.is_error),
                                 read_units=self.read_units, available_tools={tool.name for tool in self.list_tools()})
        except Exception:
            logger.exception("Independent MCP call failed: tool=%s call_id=%s", name, call_id)
            return _error(call, "TOOL_FAILED", "Engineering operation failed; check the MCP server logs and saved receipt before retrying.")
