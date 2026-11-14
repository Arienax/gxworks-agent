# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Tool registration, argument checks and public result envelopes."""
from __future__ import annotations
import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from application.instruction_research import CANDIDATE_TOOLS, REVIEW_SCHEMA, new_candidate, research_plan
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
    def result_schema(self):
        from application.read_delivery import READ_RESULT_SCHEMAS
        return READ_RESULT_SCHEMAS.get(self.name)

    @property
    def context_requirement(self):
        if self.name in DISCOVERY_TOOL_NAMES or self.name in {
                "list_projects", "create_project", "select_project", "import_gxw_project", "get_project_artifacts", "get_operation_status",
                "get_design_context", "propose_design_plan", "confirm_design_plan",
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
    public_read_validator: Any = field(default=None, repr=False)

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
            schema.setdefault('properties', {})['read_format'] = {
                'type': 'string', 'enum': ['resolved', 'paged'],
                'description': '默认 resolved 返回可读正文，超限继续正文分页；paged 使用兼容对象分页。'}
            schema['properties']['read_budget_tokens'] = {
                'type': 'integer', 'minimum': 1000, 'maximum': 128000,
                'description': 'resolved 单页交付预算，不限制总资料量或检索范围。默认6000；仅在宿主能完整输出时增大。宿主输出预算至少取此值的2倍。超大完整对象返回所需预算和原查询重试参数。'}
            schema.setdefault('properties', {})['cursor'] = {
                'type': 'string', 'minLength': 1, 'maxLength': 100,
                'description': '分页游标。使用 delivery.next 的完整参数继续，直到 delivery.complete=true；查询或工程/事实库变化后重新读取。'}
            definition = replace(definition, description=definition.description +
                ' 默认直接读取 data.document（context_schema_version=4）。仅在 delivery.next 存在时续读；'
                ' 兼容分页格式（版本3）须合并 objects 后解析 document。')
        if name in CANDIDATE_TOOLS:
            schema.setdefault('properties', {})['implementation_review'] = copy.deepcopy(REVIEW_SCHEMA)
            for option in schema.get('oneOf', ()):
                if option.get('properties', {}).get('operation', {}).get('enum') == ['generate']:
                    option['properties']['implementation_review'] = copy.deepcopy(REVIEW_SCHEMA)
        if name in SOURCE_TOOL_NAMES:
            schema.setdefault("properties", {})["source_ref"] = {
                "type": "string", "minLength": 1, "maxLength": 128,
                "description": "可选：inspect_gxw_project 或 read_gxworks2_snapshot 返回的当前会话只读来源引用。省略时读取保存版本。"}
        if name == "search_plc_manual":
            schema['properties']['check_cpu_version'] = {
                'type': 'boolean', 'description': '用户明确要求核对 CPU 固件版本时才设为 true；默认不交付版本条件。'}
            schema.setdefault("properties", {})["plc_model"] = {
                "type": "string", "minLength": 1, "maxLength": 100,
                "description": "未选中工程时传入实际 CPU；已选中工程时沿用工程型号。"}
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

    def call(self, name: str, arguments: Any, context: ToolContext, *, defer_read=False) -> Dict[str, Any]:
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
            if definition.name == 'create_st_candidate' and 'st_code' in decoded:
                if 'st_text' in decoded:
                    raise ValueError('Provide st_text (do not pass both st_text and st_code)')
                decoded['st_text'] = decoded.pop('st_code')
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
            project_context = context
            context = resolve_tool_context(definition, context, decoded)
            from application.read_delivery import PAGED_READ_TOOLS, ReadDeliveryStore, read_binding, manual_binding, read_review_binding
            if definition.name in PAGED_READ_TOOLS:
                if self._read_delivery is None:
                    self._read_delivery = ReadDeliveryStore()
                binding = (manual_binding(context, decoded.get('language')) if name == 'search_plc_manual'
                           else read_binding(context, durable=self._read_delivery.durable))
                if decoded.get('cursor'):
                    return self._read_delivery.resume(definition.name, decoded, binding)
            review = None
            patch = decoded['patch'] if name == 'patch_program' else {}
            needs_patch_context = (name == 'patch_program' and
                (decoded.get('generation_context_id') or (
                    not {'base_revision', 'base_ir_sha256'} <= patch.keys()
                    and any('expected' not in edit for edit in patch.get('instruction_edits', ())))))
            if new_candidate(name, decoded) or needs_patch_context:
                review = copy.deepcopy(decoded.pop('implementation_review', None))
                if self._read_delivery is not None or review is not None or decoded.get('generation_context_id'):
                    from plc.specification.fact_citations import referenced_fact_ids
                    ladder = decoded.get('ladder') or {}
                    selection = ladder.get('result', ladder) if isinstance(ladder, Mapping) else None
                    references = referenced_fact_ids(selection.get('decisions')) if isinstance(selection, Mapping) else ()
                    research = (self._read_delivery or ReadDeliveryStore()).check_review(
                        read_review_binding(context), review, decoded.get('generation_context_id'),
                        manual_scope=manual_binding(context), fact_owner=context.project_id, references=references)
                    if (research and research.get('generation_context_id')
                            and 'generation_context_id' in definition.parameters.get('properties', {})):
                        decoded['generation_context_id'] = research['generation_context_id']
                    if name == 'create_program_candidate' and research is not None:
                        decoded['_instruction_research'] = research
                if name == 'create_program_candidate' and review and review.get('assumptions'):
                    decoded['_implementation_assumptions'] = review['assumptions']
            data = dict(definition.handler(context, decoded) or {})
            if review is not None:
                data['implementation_review'] = review
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
            binding_diagnostic = getattr(error, 'binding_diagnostic', None)
            if isinstance(binding_diagnostic, dict):
                details['binding_diagnostic'] = copy.deepcopy(binding_diagnostic)
                opcode = binding_diagnostic.get('opcode')
                if opcode and binding_diagnostic.get('reason') == 'instruction_form_unresolved':
                    details['next_actions'] = [{'tool': 'search_plc_manual', 'arguments': {
                        'query': opcode, 'plc_model': context.plc_model, 'language': 'ladder',
                        'aspects': ['arity', 'order', 'roles', 'devices', 'constraints']}}]
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
                origins = data.pop('_fact_origins', {})
                try:
                    validate_schema(definition.result_schema, data, 'result')
                except ValueError as error:
                    raise ToolPreconditionError('INVALID_TOOL_RESULT', '工具业务结果结构无效。') from error
                public = _model_tool_value(definition.name, envelope)
                if (name == 'get_generation_context' and not context.version_id
                        and (decoded.get('user_requirement') or data.get('has_confirmed_spec'))
                        and data.get('output_contract', {}).get('tool') in CANDIDATE_TOOLS):
                    plan = research_plan(public['data'])
                    if decoded.get('instruction_details') or plan.get('manual_targets'):
                        from application.instruction_research import include_manual_facts
                        manual_context = resolve_tool_context(self._tools['search_plc_manual'], project_context, {})
                        supplied = (public['data'].get('generation_input') or {}).get('knowledge', [])
                        supplied = [*supplied, *public['data'].get('knowledge', [])]
                        plan = include_manual_facts(plan, manual_context, data['target_mode'],
                                                    supplied=supplied, explicit=bool(decoded.get('instruction_details')))
                        for key, rows in plan.pop('_fact_origins', {}).items():
                            existing = origins.setdefault(key, [])
                            existing.extend(row for row in rows if row not in existing)
                        if data.get('workflow_mode') == 'direct_generation' and not data.get('has_confirmed_spec'):
                            from application.instruction_research import direct_choice_input
                            payload = public['data']
                            payload['generation_input'] = direct_choice_input(payload['generation_input'], plan)
                    public['data']['instruction_research'] = _model_tool_value(
                        definition.name, {'instruction_research': plan}
                    )['instruction_research']
                from shared.cpu_version_projection import cpu_version_requested, project_cpu_version_knowledge
                from agent_runtime.tool_context import _generation_spec
                version_requested = (decoded.get('check_cpu_version') is True or cpu_version_requested(
                    decoded.get('user_requirement') or decoded.get('query') or '', _generation_spec(context)))
                public['data'] = project_cpu_version_knowledge(public['data'], requested=version_requested)
                if self.public_read_validator is not None:
                    self.public_read_validator(definition.name, public['data'])
                # Manual lookup normalizes CPU aliases; compare against the original project snapshot.
                review_binding = read_review_binding((project_context or context) if name == 'search_plc_manual' else context)
                return self._read_delivery.prepare(public, decoded, binding, review_binding=review_binding,
                    manual_scope=manual_binding(context, decoded.get('language')), origins=origins,
                    fact_owner=(project_context or context).project_id, defer_read=defer_read)
            except ToolPreconditionError as error:
                return {'ok': False, 'tool': definition.name, 'error': error.payload()}
        if definition.confirmation_required:
            envelope["status"] = "confirmation_required"
        return envelope
