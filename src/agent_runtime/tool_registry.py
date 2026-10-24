"""Tool registration, argument checks and public result envelopes."""
from __future__ import annotations
import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_schema import validate_schema
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS, SOURCE_ACTIONS
from agent_runtime.tool_policy import (
    SOURCE_TOOL_NAMES,
    DISCOVERY_TOOL_NAMES,
    NATIVE_INSPECTION_TOOL_NAMES,
    FORBIDDEN_TOOL_NAMES,
    _TOOL_NAME_RE,
)
from agent_runtime.tool_context import (
    ToolContext,
    resolve_tool_context,
)
from agent_runtime.engineering_schema import (
    _validate_engineering_input,
)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[[ToolContext, Mapping[str, Any]], Mapping[str, Any]]
    confirmation_required: bool = False

    @property
    def context_requirement(self):
        if self.name in DISCOVERY_TOOL_NAMES or self.name in {
                "list_projects", "create_project", "select_project", "import_gxw_project", "get_project_artifacts", "get_operation_status",
                "get_native_engineering_session", "pause_native_engineering_session"}:
            return "none"
        if self.name == "search_plc_manual":
            return "model"
        if self.name in {'read_fbd_project', 'read_st_project', 'read_sfc_project'}:
            return 'project'
        if self.name in {'read_fbd_definition', 'read_gxw_source', 'edit_gxw_project'}:
            return 'version'
        if self.name in SOURCE_TOOL_NAMES or self.name == "compile_project":
            return "program"
        if self.name in {"patch_program", "import_current_program_to_gxworks2", "execute_native_engineering", "read_fbd_definition"} | NATIVE_INSPECTION_TOOL_NAMES:
            return "version"
        return "project"

    def api_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(dict(self.parameters)),
            },
        }


@dataclass
class ToolRegistry:
    """Name-to-handler registry with a small JSON-schema validation subset."""

    _tools: Dict[str, ToolDefinition] = field(default_factory=dict)
    _read_delivery: Any = field(default=None, init=False, repr=False)

    def register(self, definition: ToolDefinition) -> None:
        name = str(definition.name or "")
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid tool name: {name!r}")
        if name in FORBIDDEN_TOOL_NAMES:
            raise ValueError(f"forbidden tool cannot be registered: {name}")
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        from dataclasses import replace
        schema = copy.deepcopy(dict(definition.parameters or {}))
        from application.read_delivery import PAGED_READ_TOOLS
        if name in PAGED_READ_TOOLS:
            schema.setdefault('properties', {})['cursor'] = {
                'type': 'string', 'minLength': 1, 'maxLength': 100,
                'description': '分页游标。使用 delivery.next 的完整参数继续，直到 delivery.complete=true；查询或工程/事实库变化后重新读取。'}
            definition = replace(definition, description=definition.description +
                ' 返回 context_schema_version=3：document 引用 objects；按 delivery.next 读取全部页并合并 objects。'
                ' 单次最多 6000 估算 token / 32 KiB，未完成交付不能当作知识缺失或改用网页搜索。')
        if name in SOURCE_TOOL_NAMES:
            schema.setdefault("properties", {})["source_ref"] = {
                "type": "string", "minLength": 1, "maxLength": 128,
                "description": "可选：inspect_gxw_project 或 read_gxworks2_snapshot 返回的当前会话只读来源引用。省略时读取保存版本。"}
        if name == "search_plc_manual":
            schema.setdefault("properties", {})["plc_model"] = {
                "type": "string", "minLength": 1, "maxLength": 100,
                "description": "无选中工程时必须明确 CPU；有工程时不得覆盖工程型号。"}
        definition = replace(definition, parameters=schema)
        if schema.get("type") != "object":
            raise ValueError(f"tool {name} parameters must be an object schema")
        self._tools[name] = definition

    @property
    def names(self) -> Sequence[str]:
        return tuple(self._tools)

    def schemas(self) -> List[Dict[str, Any]]:
        return [definition.api_schema() for definition in self._tools.values()]

    @staticmethod
    def _decode_arguments(arguments: Any) -> Dict[str, Any]:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise ValueError("arguments must be valid JSON") from error
        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be a JSON object")
        return dict(arguments)

    @staticmethod
    def _validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
        validate_schema(schema, arguments)

    def call(self, name: str, arguments: Any, context: ToolContext) -> Dict[str, Any]:
        definition = self._tools.get(str(name or ""))
        if definition is None:
            return {
                "ok": False,
                "tool": str(name or ""),
                "error": {
                    "code": "UNKNOWN_TOOL",
                    "message": "该工具未在白名单中。",
                    "next_actions": [],
                },
            }
        try:
            decoded = self._decode_arguments(arguments)
            self._validate_arguments(definition.parameters, decoded)
            if definition.name in {'execute_native_engineering', 'create_fbd_candidate', 'create_st_candidate'} | NATIVE_INSPECTION_TOOL_NAMES:
                _validate_engineering_input(definition.name, definition.parameters, decoded)
        except ValueError as error:
            return {
                "ok": False,
                "tool": definition.name,
                "error": {
                    "code": "INVALID_ARGUMENTS",
                    "message": str(error),
                    "next_actions": [],
                },
            }
        try:
            context = resolve_tool_context(definition, context, decoded)
            from application.read_delivery import PAGED_READ_TOOLS, ReadDeliveryStore, read_binding
            if definition.name in PAGED_READ_TOOLS:
                if self._read_delivery is None:
                    self._read_delivery = ReadDeliveryStore()
                binding = read_binding(context)
                if decoded.get('cursor'):
                    return self._read_delivery.resume(definition.name, decoded, binding)
            data = dict(definition.handler(context, decoded) or {})
            if context.analysis_source is not None:
                data["analysis_source"] = {key: copy.deepcopy(value) for key, value in context.analysis_source.items()
                                           if key != "raw_networks"}
                data["runtime_verified"] = False
                coverage = context.analysis_source["coverage"]
                if definition.name in {"get_diagnostics", "validate_project", "validate_current_program"} and (
                        coverage["parse_status"] != "complete" or coverage["unknown_instructions"]):
                    data.update(valid=False, status="unverified", reason="source_coverage_incomplete")
        except ToolPreconditionError as error:
            return {"ok": False, "tool": definition.name, "error": error.payload()}
        except PermissionError as error:
            return {"ok": False, "tool": definition.name, "error": {
                "code": "PERMISSION_DENIED", "message": str(error), "next_actions": []}}
        except Exception as error:  # tool errors are data, not agent-loop crashes
            from gxw.models import GXWFormatError
            details = {}
            if isinstance(error, GXWFormatError) and isinstance(getattr(error, 'findings', None), list):
                # Keep source-local static errors actionable through MCP, not
                # just a shortened aggregate message. The shared public/model
                # projections still remove internal metadata from these rows.
                details['findings'] = copy.deepcopy(error.findings)
            return {
                "ok": False,
                "tool": definition.name,
                "error": {
                    "code": "TOOL_FAILED",
                    "message": str(error)[:1000],
                    "next_actions": [],
                    **details,
                },
            }
        envelope = {"ok": True, "tool": definition.name, "data": data}
        if definition.name in PAGED_READ_TOOLS:
            from agent_runtime.runtime import _model_tool_value
            try:
                return self._read_delivery.prepare(_model_tool_value(definition.name, envelope), decoded, binding)
            except ToolPreconditionError as error:
                return {'ok': False, 'tool': definition.name, 'error': error.payload()}
        if definition.confirmation_required:
            envelope["status"] = "confirmation_required"
        return envelope
