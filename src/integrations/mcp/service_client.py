# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Explicit, agent-only bridge to the running loopback application service.

The standalone adapter remains unchanged. This client knows only the two safe
tool routes: it cannot approve proposals, execute jobs, or read credentials.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from agent_runtime.plc_tools import FORBIDDEN_TOOL_NAMES, NATIVE_INSPECTION_TOOL_NAMES, SAFE_TOOL_NAMES
from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.runtime import public_tool_result_data


class ServiceConnectionError(RuntimeError):
    pass


def validate_service_url(value):
    parsed = urllib.parse.urlsplit(str(value or ""))
    hostname = parsed.hostname or ""
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if (
        parsed.scheme != "http" or not loopback or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
    ):
        raise ValueError("--service-url must be a loopback HTTP origin with no credentials or path.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid local service port.") from error
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ServiceConnectionError("The local service must not redirect agent requests.")


class ApplicationServiceClient:
    MAX_RESPONSE_BYTES = 16 * 1024 * 1024

    def __init__(self, service_url, token, *, timeout=60.0, opener=None):
        self.service_url = validate_service_url(service_url)
        if not isinstance(token, str) or not token.strip() or "\n" in token or "\r" in token:
            raise ValueError("The service agent token must be configured in the named environment variable.")
        self._token = token.strip()
        self.timeout = float(timeout)
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        from application.read_delivery import ReadDeliveryReceiver
        self._read_receiver = ReadDeliveryReceiver()

    @classmethod
    def from_environment(cls, service_url, variable, **kwargs):
        if not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise ValueError("--service-token-env must name the environment variable holding the agent token.")
        return cls(service_url, os.environ.get(variable, ""), **kwargs)

    def _request(self, path, payload=None, *, native_validation=False, defer_read=False):
        # Keeping routes internal prevents an adapter user from turning a tool
        # call into an arbitrary authenticated operator HTTP request.
        if path not in {"/api/agent/tools", "/api/agent/tools/call", "/api/agent/read-delivery"}:
            raise ValueError("Unsupported agent service route.")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.service_url + path, data=body,
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        if defer_read:
            request.add_header('X-GXWorks-Deferred-Read', '1')
        try:
            # A native task owns its temporary workspace until natural exit.
            # Do not turn an arbitrary transport deadline into a retryable
            # native failure; retries keep the existing application's call id.
            with self._opener.open(request, timeout=None if native_validation else self.timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise ServiceConnectionError("Local agent service rejected the request (HTTP %d)." % error.code) from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ServiceConnectionError("Local agent service is unavailable; check its address and agent token.") from None
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ServiceConnectionError("Local agent service response is too large.")
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as error:
            raise ServiceConnectionError("Local agent service returned invalid JSON.") from None
        if not isinstance(result, dict):
            raise ServiceConnectionError("Local agent service returned an invalid object.")
        return result

    def list_tools(self):
        response = self._request("/api/agent/tools")
        schemas = response.get("tools")
        if not isinstance(schemas, list):
            raise ServiceConnectionError("Local agent service returned invalid tool schemas.")
        safe = []
        for schema in schemas:
            function = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("parameters"), dict):
                raise ServiceConnectionError("Local agent service returned an invalid tool definition.")
            name = function.get("name")
            if name in SAFE_TOOL_NAMES and name not in FORBIDDEN_TOOL_NAMES:
                safe.append(copy.deepcopy(schema))
        return safe

    def finish_host_read(self, tool, document_id, accepted, *, project_id):
        response = self._request("/api/agent/read-delivery", {
            "tool": tool, "document_id": document_id, "accepted": accepted, "project_id": project_id})
        return response.get('ok') is True

    def call_tool(self, name, arguments, call_id, *, project_id, version_id=None, defer_read=False):
        if name not in SAFE_TOOL_NAMES or name in FORBIDDEN_TOOL_NAMES:
            raise ValueError("Tool is not in the shared safe tool list.")
        payload = {"project_id": project_id, "name": name, "arguments": arguments, "call_id": call_id}
        if version_id is not None:
            payload["version_id"] = version_id
        response = self._request("/api/agent/tools/call", payload,
            native_validation=name in NATIVE_INSPECTION_TOOL_NAMES or (
                name == 'validate_project' and isinstance(arguments, dict) and arguments.get('native') is True),
            defer_read=defer_read)
        data = response.get("data")
        if not isinstance(data, dict):
            raise ServiceConnectionError("Local agent service returned an invalid tool result.")
        from application.read_delivery import PAGED_READ_TOOLS
        if name in PAGED_READ_TOOLS and not response.get('is_error'):
            try:
                if response.get('name') != name or response.get('call_id') != call_id:
                    raise ValueError('Read response belongs to another call')
                if json.loads(response.get('content') or '') != data:
                    raise ValueError('Read response content differs from its data')
                data = self._read_receiver.receive(name, data,
                    binding=(name, project_id, None if name == 'search_plc_manual' else version_id),
                    query_arguments={key: value for key, value in arguments.items() if key != 'cursor'},
                    cursor=arguments.get('cursor'))
            except (ValueError, TypeError) as error:
                raise ServiceConnectionError('Local agent service returned an invalid paged read result.') from error
        result = ToolResult(call_id, name, str(response.get("content") or ""), data, is_error=bool(response.get("is_error")))
        # Defense in depth: never trust a transport response with private IR.
        public = public_tool_result_data(result)
        for key in ('proposal_id', 'execution_job_id', 'version_id'):
            value = response.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
                raise ServiceConnectionError('Local agent service returned an invalid operation identifier.')
            if key in public and public[key] != value:
                raise ServiceConnectionError('Local agent service returned inconsistent operation identifiers.')
            public[key] = value
        return ToolResult(call_id, name, json.dumps(public, ensure_ascii=False), public, is_error=result.is_error)


class ServiceMCPToolAdapter:
    """Translate the existing runtime schema and results; no PLC operations."""

    def __init__(self, client, project_id, version_id=None, *, read_budget_tokens=None):
        from shared.records import record_id
        from .context_provider import ContextUnavailableError

        from application.context_units import ContextUnits
        self.read_units = ContextUnits()
        self.read_budget_tokens = read_budget_tokens
        self.client = client
        self.project_id = record_id(project_id, message="Invalid project ID.", error_type=ContextUnavailableError)
        self.version_id = record_id(version_id, message="Invalid version ID.", error_type=ContextUnavailableError) if version_id is not None else None
        self._session_id = uuid.uuid4().hex
        self._call_ids = {}

    def list_tools(self, *, read_budget_tokens=None):
        from .tool_adapter import mcp_tool
        from application.context_units import restore_tool_schema
        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        return [mcp_tool(schema, read_budget_tokens=budget)
                for schema in [*self.client.list_tools(), restore_tool_schema()]]

    def call_tool(self, name: str, arguments: Any, call_id: str, *, read_budget_tokens=None):
        from .tool_adapter import _error, to_mcp_result, restore_context_units, invoke_host_read
        from application.context_units import RESTORE_TOOL

        budget = self.read_budget_tokens if read_budget_tokens is None else read_budget_tokens
        call = ToolCall(call_id, name, arguments)
        if name == RESTORE_TOOL:
            return restore_context_units(self.read_units, call, budget)
        if name not in SAFE_TOOL_NAMES or name in FORBIDDEN_TOOL_NAMES:
            return _error(call, "UNKNOWN_TOOL", "Tool is not in the shared safe tool list.")
        try:
            # MCP JSON-RPC ids often restart at 1 when the process restarts.
            # Give each logical call a stable UUID within this server session;
            # a failed response retried with the same id keeps its backend key.
            logical_id = self._call_ids.setdefault(str(call_id), self._session_id + "_" + uuid.uuid4().hex)
            result = invoke_host_read(ToolCall(logical_id, name, arguments),
                lambda request: self.client.call_tool(request.name, request.arguments, request.id,
                    project_id=self.project_id, version_id=self.version_id,
                    **({'defer_read': True} if request.defer_read else {})), budget,
                read_units=self.read_units, finish_read=lambda tool, document_id, accepted:
                    self.client.finish_host_read(tool, document_id, accepted, project_id=self.project_id))
            return to_mcp_result(ToolResult(call_id, result.name, result.content, result.data, result.is_error),
                                 read_units=self.read_units)
        except (ServiceConnectionError, ValueError):
            return _error(call, "SERVICE_UNAVAILABLE", "Local application service call failed; inspect the existing operation receipt before submitting a new action.")
