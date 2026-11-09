"""Shared deterministic tool boundary for desktop and external agents."""

from __future__ import annotations

import copy
import json
from typing import Any, List, Mapping, Protocol

from agent_runtime.messages import ToolCall, ToolResult
from shared.i18n import tr
from shared.public_results import public_result_field


class ToolRuntime(Protocol):
    def list_tools(self, context: Any = None) -> List[Mapping[str, Any]]: ...

    def invoke(self, tool_call: ToolCall, context: Any) -> ToolResult: ...


def _public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get('mode') == 'live':
            from application.live_engineering import is_live_engineering_receipt, live_engineering_summary
            if is_live_engineering_receipt(value):
                return live_engineering_summary(value)
        from application.capability_context import public_discovery
        return {
            key: public_discovery(item) if key == "capability_discovery" else _public_value(item)
            for key, item in value.items()
            if public_result_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return copy.deepcopy(value)


_DEBUG_TOOLS = frozenset({
    'get_diagnostics', 'validate_current_program', 'validate_project',
    'get_operation_status',
    'compare_native_compiled_code', 'get_simulator_trace',
    'read_native_device_assignments', 'locate_native_code_steps',
})


def _model_tool_value(name, value):
    from application.read_delivery import PAGED_READ_TOOLS, DELIVERY_VERSION
    if (name in PAGED_READ_TOOLS and isinstance(value, Mapping)
            and (value.get('data', {}).get('delivery') or {}).get('version') == DELIVERY_VERSION):
        # Produced only by the registry after the ordinary public projection.
        # A second generic projection would corrupt engineering map keys in
        # the object table and invalidate the measured final envelope.
        return copy.deepcopy(value)
    public = _public_value(value)
    # Selection belongs to the registered tool owner, never to a payload flag.
    document = {"get_current_program_info": "program", "read_network": "program",
                "get_diagnostics": "diagnostics", "read_sfc_project": "source_snapshot",
                "read_fbd_project": "source_snapshot", "read_fbd_definition": "source_snapshot",
                "create_fbd_candidate": "source_snapshot", "create_sfc_candidate": "source_snapshot",
                "get_generation_context": "generation_context",
                "get_fbd_catalog": "catalog"}.get(name)
    enveloped = isinstance(value, Mapping) and value.get("tool") == name and "data" in value
    if document:
        from shared.engineering_documents import project_document
        source = value["data"] if enveloped else value
        projected_document = project_document(source, kind=document, field_allowed=public_result_field)
        if enveloped:
            public["data"] = projected_document
        else:
            public = projected_document
    # Search owns its explicit debug switch. Deterministic debug tools retain
    # their reports, while ordinary engineering tools omit attached audits.
    if name in _DEBUG_TOOLS or name == 'search_plc_manual':
        return public
    from shared.model_context import technical_value
    projected = technical_value(public)
    if document:
        from shared.engineering_documents import restore_document_data
        if enveloped:
            projected["data"] = restore_document_data(projected["data"], public["data"],
                kind=document, project_metadata=technical_value)
        else:
            projected = restore_document_data(projected, public, kind=document, project_metadata=technical_value)
    def live_receipts(original, current):
        if isinstance(original, Mapping) and original.get('mode') == 'live':
            # Live step verification and native diagnostics are operational
            # receipts, already bounded by their application projection.
            from application.live_engineering import is_live_engineering_receipt, live_engineering_summary
            if is_live_engineering_receipt(original):
                return live_engineering_summary(original)
        if isinstance(current, Mapping) and isinstance(original, Mapping):
            return {key: live_receipts(original[key], item) if key in original else item
                    for key, item in current.items()}
        if isinstance(current, list) and isinstance(original, (list, tuple)):
            return [live_receipts(before, after) for before, after in zip(original, current)]
        return current
    return live_receipts(public, projected)


def public_tool_result_data(result: ToolResult) -> Mapping[str, Any]:
    """Return the untruncated public projection, never UI-only candidate data.

    ``ToolResult.data`` deliberately retains private fields for the desktop's
    confirmation flow. Transport adapters must use this projection instead.
    """

    return _model_tool_value(result.name, result.data)


class InProcessToolRuntime:
    """Adapt the existing deterministic registry to the ToolRuntime contract."""

    def __init__(self, registry: Any):
        self.registry = registry

    def list_tools(self, context: Any = None) -> List[Mapping[str, Any]]:
        del context
        return self.registry.schemas()

    def invoke(self, tool_call: ToolCall, context: Any) -> ToolResult:
        envelope = self.registry.call(
            tool_call.name,
            tool_call.arguments,
            context,
        )
        public = _model_tool_value(tool_call.name, envelope)
        return ToolResult(
            call_id=tool_call.id,
            name=tool_call.name,
            content=json.dumps(
                public,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            data=envelope,
            is_error=not bool(envelope.get("ok")),
        )


class ReadOnlyToolRuntime:
    """Enforce the question tool set during discovery and invocation."""

    def __init__(self, runtime: ToolRuntime):
        from agent_runtime.plc_tools import READ_ONLY_TOOL_NAMES
        self.runtime = runtime
        self.allowed_names = READ_ONLY_TOOL_NAMES

    def list_tools(self, context: Any = None) -> List[Mapping[str, Any]]:
        schemas = [copy.deepcopy(schema) for schema in self.runtime.list_tools(context)
                   if schema.get("function", {}).get("name") in self.allowed_names]
        for schema in schemas:
            if schema['function']['name'] == 'validate_project':
                schema['function']['description'] = '通过 PLC Core 校验当前 PLC IR 与梯形图。'
                schema['function']['parameters'] = {
                    'type': 'object', 'properties': {key: value for key, value in
                        schema['function']['parameters'].get('properties', {}).items() if key == 'source_ref'},
                    'additionalProperties': False}
        return schemas

    def invoke(self, tool_call: ToolCall, context: Any) -> ToolResult:
        from agent_runtime.plc_tools import ToolRegistry
        native_check = False
        if tool_call.name == 'validate_project':
            try:
                arguments = ToolRegistry._decode_arguments(tool_call.arguments)
                native_check = arguments.get('native', False) is not False or 'program' in arguments
            except ValueError:
                native_check = True
        if tool_call.name in self.allowed_names and not native_check:
            result = self.runtime.invoke(tool_call, context)
            # A read-only tool must not smuggle an engineering proposal through
            # a custom runtime, even when its name is permitted.
            if not (result.data.get("data") or {}).get("pending_action"):
                return result
        envelope = {"ok": False, "tool": tool_call.name, "error": {
            "code": "READ_ONLY_TOOL",
            "message": str(tr("工程问答只允许查询；请切换到创建程序或修改程序。")),
        }}
        return ToolResult(tool_call.id, tool_call.name,
                          json.dumps(envelope, ensure_ascii=False), envelope, is_error=True)


def build_default_tool_runtime() -> InProcessToolRuntime:
    from agent_runtime.plc_tools import build_default_tool_registry

    return InProcessToolRuntime(build_default_tool_registry())


__all__ = [
    "InProcessToolRuntime",
    "ReadOnlyToolRuntime",
    "ToolRuntime",
    "build_default_tool_runtime",
    "public_tool_result_data",
]
