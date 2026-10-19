"""Deterministic, allow-listed tools exposed to the PLC chat agent.

The language model never receives filesystem, mouse, keyboard, PLC write, or
device-force primitives.  Every tool operates on an immutable snapshot of the
currently selected project/version.  Candidate patches and GX synchronization
only create confirmation requests; application services own commit points.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS, SOURCE_ACTIONS


SAFE_TOOL_NAMES = (
    "get_current_project",
    "get_operation_status",
    "get_native_engineering_session",
    "pause_native_engineering_session",
    "get_generation_context",
    "create_program_candidate",
    "get_current_program_info",
    "read_network",
    "search_plc_manual",
    "get_diagnostics",
    "validate_project",
    "compile_project",
    "patch_program",
    "validate_current_program",
    "import_current_program_to_gxworks2",
    "execute_native_engineering",
    "get_fbd_catalog",
    "read_fbd_project",
    "read_fbd_libraries",
    "read_fbd_definition",
    "create_fbd_candidate",
    "read_st_project",
    "create_st_candidate",
    "read_sfc_project",
    "create_sfc_candidate",
    "list_gxworks2_sessions",
    "read_gxworks2_snapshot",
    "list_simulator_targets",
    "get_simulator_status",
    "read_simulator_devices",
    "connect_simulator",
    "prepare_simulator_scenario",
    "start_simulator_trace",
    "get_simulator_trace",
    "stop_simulator_trace",
    "read_native_device_assignments",
    "compare_native_compiled_code",
    "locate_native_code_steps",
)

# Engineering questions expose only these operations. New tools require an
# explicit decision before they become available to the read-only assistant.
READ_ONLY_TOOL_NAMES = frozenset({
    "get_current_project", "get_operation_status", "get_native_engineering_session", "get_current_program_info", "read_network",
    "search_plc_manual", "get_diagnostics", "validate_project",
    "read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps",
    "compile_project", "validate_current_program", "get_fbd_catalog",
    "read_fbd_project",
    "read_fbd_libraries",
    "read_fbd_definition",
    "read_sfc_project",
    "read_st_project",
    "list_gxworks2_sessions", "read_gxworks2_snapshot",
    "list_simulator_targets", "get_simulator_status", "read_simulator_devices",
    "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
    "get_simulator_trace", "stop_simulator_trace",
})

SOURCE_TOOL_NAMES = frozenset({"get_current_program_info", "read_network", "get_diagnostics",
                              "validate_current_program", "validate_project"})
DISCOVERY_TOOL_NAMES = frozenset({"get_current_project", "list_gxworks2_sessions",
                                "read_gxworks2_snapshot", "list_simulator_targets",
                                "get_simulator_status", "read_simulator_devices",
                                "connect_simulator", "prepare_simulator_scenario", "start_simulator_trace",
                                "get_simulator_trace", "stop_simulator_trace"})
NATIVE_INSPECTION_TOOL_NAMES = frozenset({"read_native_device_assignments", "compare_native_compiled_code", "locate_native_code_steps"})

FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "mouse_click",
        "keyboard_input",
        "delete_file",
        "write_plc",
        "force_device",
    }
)

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolContext:
    """Read-only project snapshot used for one agent turn."""

    project: Mapping[str, Any]
    version: Optional[Mapping[str, Any]] = None
    ladder: Optional[Mapping[str, Any]] = None
    program_ir: Optional[Mapping[str, Any]] = None
    fbd_baseline: Optional[bytes] = None
    native_snapshot: Optional[bytes] = None
    st_text: Optional[str] = None
    sfc_graph: Optional[Mapping[str, Any]] = None
    inspection: Any = None
    analysis_source: Optional[Mapping[str, Any]] = None
    operation_status: Any = None
    native_session_status: Any = None
    native_session_pause: Any = None

    @property
    def project_id(self) -> str:
        return str(self.project.get("id") or "")

    @property
    def version_id(self) -> str:
        return str((self.version or {}).get("id") or "")

    @property
    def plc_model(self) -> str:
        return str(
            (self.version or {}).get("plc_model")
            or self.project.get("plc_model")
            or "FX3U"
        ).upper()


def build_tool_context(
    project: Mapping[str, Any],
    *,
    version: Optional[Mapping[str, Any]] = None,
    ladder: Optional[Mapping[str, Any]] = None,
    program_ir: Optional[Mapping[str, Any]] = None,
    fbd_baseline: Optional[bytes] = None,
    native_snapshot: Optional[bytes] = None,
    st_text: Optional[str] = None,
    sfc_graph: Optional[Mapping[str, Any]] = None,
    inspection=None,
    analysis_source=None,
    operation_status=None,
    native_session_status=None,
    native_session_pause=None,
) -> ToolContext:
    """Copy mutable UI/session state before handing it to a worker thread."""

    if not isinstance(project, Mapping):
        raise TypeError("project must be an object")
    return ToolContext(
        project=copy.deepcopy(dict(project)),
        version=copy.deepcopy(dict(version)) if isinstance(version, Mapping) else None,
        ladder=copy.deepcopy(dict(ladder)) if isinstance(ladder, Mapping) else None,
        program_ir=(
            copy.deepcopy(dict(program_ir))
            if isinstance(program_ir, Mapping)
            else None
        ),
        fbd_baseline=bytes(fbd_baseline) if fbd_baseline is not None else None,
        native_snapshot=bytes(native_snapshot) if native_snapshot is not None else None,
        st_text=st_text,
        sfc_graph=copy.deepcopy(dict(sfc_graph)) if isinstance(sfc_graph, Mapping) else None,
        inspection=inspection,
        analysis_source=copy.deepcopy(analysis_source),
        operation_status=operation_status,
        native_session_status=native_session_status,
        native_session_pause=native_session_pause,
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
                "list_projects", "create_project", "select_project", "get_project_artifacts", "get_operation_status",
                "get_native_engineering_session", "pause_native_engineering_session"}:
            return "none"
        if self.name == "search_plc_manual":
            return "model"
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
        if name in SOURCE_TOOL_NAMES:
            schema.setdefault("properties", {})["source_ref"] = {
                "type": "string", "minLength": 1, "maxLength": 128,
                "description": "可选：read_gxworks2_snapshot 返回的当前会话只读快照引用。省略时读取保存版本。"}
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
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        missing = sorted(required.difference(arguments))
        if missing:
            raise ValueError("missing required argument(s): " + ", ".join(missing))
        if schema.get("additionalProperties") is False:
            extras = sorted(set(arguments).difference(properties))
            if extras:
                raise ValueError("unknown argument(s): " + ", ".join(extras))

        for name, value in arguments.items():
            rule = properties.get(name)
            if not isinstance(rule, Mapping):
                continue
            expected = rule.get("type")
            valid = True
            if expected == "string":
                valid = isinstance(value, str)
            elif expected == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif expected == "number":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif expected == "boolean":
                valid = isinstance(value, bool)
            elif expected == "array":
                valid = isinstance(value, list)
            elif expected == "object":
                valid = isinstance(value, Mapping)
            if not valid:
                raise ValueError(f"argument {name!r} must be {expected}")
            if isinstance(value, str):
                if "minLength" in rule and len(value) < int(rule["minLength"]):
                    raise ValueError(f"argument {name!r} is too short")
                if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                    raise ValueError(f"argument {name!r} is too long")
                if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
                    raise ValueError(f"argument {name!r} has invalid syntax")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in rule and value < rule["minimum"]:
                    raise ValueError(f"argument {name!r} is below minimum")
                if "maximum" in rule and value > rule["maximum"]:
                    raise ValueError(f"argument {name!r} is above maximum")
            if "enum" in rule and value not in rule["enum"]:
                raise ValueError(f"argument {name!r} is not an allowed value")

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
            return {
                "ok": False,
                "tool": definition.name,
                "error": {
                    "code": "TOOL_FAILED",
                    "message": str(error)[:1000],
                    "next_actions": [],
                },
            }
        envelope = {"ok": True, "tool": definition.name, "data": data}
        if definition.confirmation_required:
            envelope["status"] = "confirmation_required"
        return envelope


def resolve_tool_context(definition, context, arguments):
    """Resolve only the context this operation needs, never infer a project."""
    from dataclasses import replace
    context = context or build_tool_context({})
    if arguments.get("source_ref"):
        if definition.name not in SOURCE_TOOL_NAMES or arguments.get("native"):
            raise ToolPreconditionError("PERMISSION_DENIED", "分析快照仅允许程序读取和静态检查。")
        if context.inspection is None:
            raise ToolPreconditionError("STALE_SOURCE_REF", "分析快照不属于当前会话。", next_actions=PROJECT_ACTIONS)
        context = context.inspection.source_context(arguments["source_ref"])
    requirement = definition.context_requirement
    if (definition.name == "validate_project" and arguments.get("native")) or (
            definition.name in {"create_fbd_candidate", "create_sfc_candidate"}
            and arguments.get("operation") in {"edit", "compile", "create_fb", "create_program", "import_library", "create_structure", "create_st_fb", "edit_st_fb", "edit_global_labels", "create_global_labels", "edit_pou_labels", "configure_project"}):
        requirement = "version"
    if definition.name == 'create_st_candidate' and arguments.get('operation') in {'create_st_program', 'edit_st_program'}:
        requirement = 'version'
    if requirement == "none":
        return context
    if requirement == "model":
        supplied = arguments.get("plc_model")
        actual = (context.version or {}).get("plc_model") or context.project.get("plc_model")
        if supplied and actual and supplied.upper() != actual.upper():
            raise ToolPreconditionError("MODEL_MISMATCH", "检索型号与当前工程不一致。")
        if not (supplied or actual):
            raise ToolPreconditionError("PLC_MODEL_REQUIRED", "手册检索需要明确 CPU 型号。",
                next_actions=({"tool": "search_plc_manual", "required_arguments": ["query", "plc_model"]},))
        if supplied and not actual:
            from plc.project_capabilities import normalize_project_selection
            selection = normalize_project_selection(supplied, "ladder", None, None)
            context = replace(context, project={"plc_model": selection["plc_model"]})
        return context
    if not context.project_id and context.analysis_source is None:
        raise ToolPreconditionError("NO_PROJECT_SELECTED", "未选中 Agent 工程；可先发现原生工程并读取分析快照。",
                                    next_actions=PROJECT_ACTIONS)
    if requirement == "version" and not context.version_id:
        raise ToolPreconditionError("NO_PROGRAM_SOURCE", "此操作需要已保存的工程版本。", next_actions=SOURCE_ACTIONS)
    if requirement == "program" and not any((context.program_ir, context.ladder, context.native_snapshot,
                                             context.fbd_baseline, context.st_text, context.analysis_source)):
        # Program-info queries report absence as state, rather than an error.
        if definition.name != "get_current_program_info":
            raise ToolPreconditionError("NO_PROGRAM_SOURCE", "当前工程没有可分析的程序来源。", next_actions=SOURCE_ACTIONS)
    if context.analysis_source and context.program_ir is None and definition.name not in {"get_current_program_info", "read_network"}:
        raise ToolPreconditionError("SOURCE_NOT_PARSEABLE", "快照保留原始指令，但不能进行完整静态检查。",
            next_actions=({"tool": "get_current_program_info", "arguments": {"source_ref": arguments.get("source_ref")}},),
            details={"analysis_source": {key: copy.deepcopy(value) for key, value in context.analysis_source.items()
                                         if key != "raw_networks"}})
    return context


_EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _engineering_object(properties, required=(), **bounds):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False, **bounds}


def _engineering_text(*, empty=False, maximum=None):
    return {'type': 'string', 'pattern': r'^[^\x00]*$' if empty else r'^(?=[\s\S]*\S)[^\x00]+$',
            **({} if empty else {'minLength': 1}), **({'maxLength': maximum} if maximum is not None else {})}


def _engineering_declarations(scope, *, maximum=4096, nonempty=False):
    text = _engineering_text(empty=True)
    fields = {'name': _engineering_text(maximum=255),
              'data_type': _engineering_text(), 'initial_value': text, 'comment': text}
    if scope != 'structure':
        classes = (['VAR_GLOBAL', 'VAR_GLOBAL_CONSTANT'] if scope == 'global' else
                   ['VAR', 'VAR_CONSTANT'] if scope == 'program' else
                   ['VAR', 'VAR_CONSTANT', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT'] +
                   (['VAR_INPUT_CONSTANT', 'VAR_IN_EXT'] if scope == 'pou' else []))
        fields.update(class_name={'type': 'string', 'enum': classes},
                      kind={'type': 'string', 'enum': ['variable', 'structure', 'function_block']})
    if scope == 'global':
        pair = _engineering_object({'device': text, 'iec_address': text}, ['device'])
        fields.update(device=text, iec_address=text, member_devices={'type': 'object',
            'propertyNames': _engineering_text(), 'additionalProperties': {'oneOf': [text, pair]}})
    return {'type': 'array', 'maxItems': maximum, 'minItems': 1 if nonempty else 0,
            'items': _engineering_object(fields, ['name', 'data_type'])}


def _engineering_configuration(*, visible, live=False):
    pair = {'type': 'array', 'minItems': 2, 'maxItems': 2,
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}}
    if live:
        ranges = _engineering_object({key: pair for key in ('D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10')},
                                     minProperties=1)
    elif visible:
        ranges = _engineering_object({'D': {**pair, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 7999}},
                                      'M': pair}, minProperties=1)
    else:
        pair = {**pair, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}}
        ranges = {'type': 'object', 'propertyNames': _engineering_text(),
                  'additionalProperties': {'oneOf': [pair, {'type': 'null'}]}}
    fields = {'system_variable_ranges': ranges}
    if not visible:
        fields['program_capacity'] = {'type': 'integer', 'enum': [16000, 64000]}
    return _engineering_object(fields, ['system_variable_ranges'] if visible else (), minProperties=1)


def _engineering_graph_actions(*, live=False):
    text = _engineering_text()
    coordinate = {'type': 'integer', 'minimum': 0, 'maximum': 4096}
    point = {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': coordinate}
    node_endpoint = _engineering_object({'node': text, 'port': text}, ['node', 'port'])
    endpoint = node_endpoint if live else {'oneOf': [node_endpoint,
                          _engineering_object({'wire': text, 'point': point}, ['wire', 'point'])]}
    variants = {
        'rebind_terminal': {'node': text, 'symbol': text}, 'delete_terminal': {'node': text},
        'move_node': {'node': text, 'x': coordinate, 'y': coordinate}, 'delete_wire': {'wire': text},
        'add_wire': {'id': {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'},
                     'from': endpoint, 'to': endpoint,
                     'via': {'type': 'array', 'maxItems': 128, 'items': point}}}
    if live:
        variants['add_terminal'] = {'template': {'type': 'string', 'enum': ['input']},
            'symbol': text, 'x': coordinate, 'y': coordinate}
        variants = {name: {'network': ({'type': 'integer', 'enum': [0]}
                                      if name in ('move_node', 'add_wire', 'add_terminal') else
                                      {'type': 'integer', 'minimum': 0, 'maximum': 511}), **fields}
                    for name, fields in variants.items()}
    return {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {'oneOf': [
        _engineering_object({'action': {'type': 'string', 'enum': [name]}, **fields}, ['action', *fields])
        for name, fields in variants.items()]}}


def _engineering_authoring_commands(*, visible):
    identity = _engineering_text(maximum=255)
    body = _engineering_text(empty=not visible, maximum=4 * 1024 * 1024)
    declarations = lambda scope: _engineering_declarations(scope, maximum=4096 if visible else 8192)
    commands = {}
    for name in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels'):
        # Global tables also accept their exact logical source with .gh.
        source = identity if name == 'edit_pou_labels' else _engineering_text(maximum=512)
        commands[name] = _engineering_object({'source_name': source,
            'declarations': declarations('pou' if name == 'edit_pou_labels' else 'global')},
            ['source_name', 'declarations'])
        if visible and name != 'edit_pou_labels':
            row = commands[name]['properties']['declarations']['items']['properties']
            row.pop('member_devices')
            row['kind']['enum'] = ['variable', 'function_block']
    commands['create_structure'] = _engineering_object({'source_name': identity,
        'members': _engineering_declarations('structure', maximum=4096 if visible else 8192, nonempty=True)},
        ['source_name', 'members'])
    for name in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'):
        fields = {'source_name': identity, 'st_text': body,
                  'declarations': declarations('program' if name.endswith('program') else 'fb')}
        required = ['source_name', 'st_text'] + (['declarations'] if name.startswith('create_') else [])
        if name == 'create_st_program':
            fields.update(resource=identity, task=identity, scope_mode={'type': 'string', 'enum': ['existing_task']})
            required += ['resource', 'task', 'scope_mode']
        commands[name] = _engineering_object(fields, required)
    commands['configure_project'] = _engineering_object({'source_name': _engineering_text(maximum=512),
        'options': _engineering_configuration(visible=visible)}, ['source_name', 'options'])
    return commands


def _engineering_native_schema():
    commands = _engineering_authoring_commands(visible=True)
    text = _engineering_text(maximum=512)
    commands.update(create_fbd_call=_engineering_object({'caller': text, 'instance': text, 'definition': text,
        'arguments': {'type': 'object', 'minProperties': 1, 'maxProperties': 256,
                      'propertyNames': _engineering_text(), 'additionalProperties': _engineering_text()}},
        ['caller', 'instance', 'definition', 'arguments']),
        edit_fbd_graph=_engineering_object({'caller': text, 'actions': _engineering_graph_actions()}, ['caller', 'actions']),
        show_assignments=_engineering_object({'queries': {'type': 'array', 'minItems': 1, 'maxItems': 32,
            'items': _engineering_object({'scope': {'type': 'string', 'enum': ['global']}, 'source': text, 'name': text},
                                        ['scope', 'source', 'name'])}}, ['queries']))
    commands.update({name: _engineering_object({}) for name in ('compile', 'save', 'reopen')})
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]}, **rule['properties']},
                 ['operation', *rule['required']]) for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    # Keep discoverable common properties while oneOf enforces operation-local
    # required fields and rejects arguments belonging to a different operation.
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    common['declarations'] = _engineering_declarations('global')
    common['declarations']['items']['properties']['class_name']['enum'] += [
        'VAR', 'VAR_CONSTANT', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT', 'VAR_INPUT_CONSTANT', 'VAR_IN_EXT']
    command = _engineering_object(common, ['operation'], oneOf=variants)
    batch = _engineering_object({
        'schema_version': {'type': 'integer', 'enum': [1]},
        'commands': {'type': 'array', 'minItems': 1, 'maxItems': 256, 'items': command}},
        ['schema_version', 'commands'])
    live_command = copy.deepcopy(command)
    live_command['oneOf'] = [variant for variant in live_command['oneOf']
                            if variant['properties']['operation']['enum'][0] != 'create_structure']
    live_command['properties']['operation']['enum'].remove('create_structure')
    for variant in live_command['oneOf']:
        operation = variant['properties']['operation']['enum'][0]
        if operation == 'edit_fbd_graph':
            variant['properties']['actions'] = _engineering_graph_actions(live=True)
        elif operation == 'show_assignments':
            variant['properties']['queries']['items']['properties']['scope']['enum'] = ['global', 'local', 'instance']
        elif operation == 'configure_project':
            variant['properties']['options'] = _engineering_configuration(visible=True, live=True)
    delete_fields = {'scope': {'type': 'string', 'enum': ['program']},
                     'source_name': _engineering_text(maximum=255), 'name': _engineering_text(maximum=255)}
    live_command['oneOf'].append(_engineering_object({
        'operation': {'type': 'string', 'enum': ['delete_label']}, **delete_fields},
        ['operation', *delete_fields]))
    live_command['properties']['operation']['enum'].append('delete_label')
    live_command['properties'].update(delete_fields)
    live_command['properties']['actions'] = _engineering_graph_actions(live=True)
    live_command['properties']['options'] = _engineering_configuration(visible=True, live=True)
    live_command['properties']['queries']['items']['properties']['scope']['enum'] = ['global', 'local', 'instance']
    controls = _engineering_object({
        'dwell_ms': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
        'max_steps': {'type': 'integer', 'minimum': 1, 'maximum': 256},
        'expected_revision': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647},
        'step_mode': {'type': 'boolean'}, 'resume_pending': {'type': 'boolean'}})
    live = _engineering_object({'schema_version': {'type': 'integer', 'enum': [1]},
        'mode': {'type': 'string', 'enum': ['live']},
        'session_action': {'type': 'string', 'enum': ['start', 'continue', 'end', 'rebind']},
        'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'},
        'controls': controls,
        'commands': {'type': 'array', 'minItems': 0, 'maxItems': 256, 'items': live_command}},
        ['schema_version', 'mode', 'commands'])
    discoverable = copy.deepcopy(common)
    discoverable['operation']['enum'].append('delete_label')
    discoverable.update(delete_fields)
    discoverable['actions'] = {'oneOf': [_engineering_graph_actions(), _engineering_graph_actions(live=True)]}
    discoverable['options'] = _engineering_configuration(visible=True, live=True)
    discoverable['queries']['items']['properties']['scope']['enum'] = ['global', 'local', 'instance']
    properties = {**batch['properties'], **live['properties'],
        'commands': {'type': 'array', 'maxItems': 256, 'items': _engineering_object(discoverable, ['operation'])}}
    return _engineering_object({'plan': _engineering_object(properties, ['schema_version', 'commands'],
        oneOf=[batch, live])}, ['plan'])


def _engineering_st_schema():
    commands = _engineering_authoring_commands(visible=False)
    commands = {key: commands[key] for key in ('create_st_program', 'edit_st_program')}
    commands = {'import': _engineering_object({'data_base64': _engineering_text(),
        'program': _engineering_text(maximum=512)}, ['data_base64', 'program']), **commands}
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]},
        **rule['properties'], 'summary': _engineering_text(empty=True)}, ['operation', *rule['required']])
        for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    return _engineering_object(common, ['operation'], oneOf=variants)


def _engineering_fbd_schema():
    commands = {name: rule for name, rule in _engineering_authoring_commands(visible=False).items()
                if name not in {'create_st_program', 'edit_st_program'}}
    text = _engineering_text(maximum=255)
    commands.update({name: _engineering_object({'model': {'type': 'object'}}, ['model'])
                     for name in ('generate', 'edit')})
    commands.update(import_library=_engineering_object({'data_base64': _engineering_text(), 'library_name': text},
                                                       ['data_base64', 'library_name']),
        create_fb=_engineering_object({'source_name': text}, ['source_name']),
        create_program=_engineering_object({'source_name': text, 'resource': text, 'task': text,
            'scope_mode': {'type': 'string', 'enum': ['existing_task', 'new_task', 'new_resource']}},
            ['source_name', 'resource', 'task', 'scope_mode']),
        remove_empty_program=_engineering_object({'source_name': _engineering_text(maximum=512)}, ['source_name']),
        compile=_engineering_object({}))
    commands['import'] = _engineering_object({'data_base64': _engineering_text(), 'program': _engineering_text(),
                                             'native_save': {'type': 'boolean'}}, ['data_base64'])
    extras = {'summary': _engineering_text(empty=True),
              'generation_context_id': _engineering_text(maximum=128)}
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]},
        **rule['properties'], **extras}, ['operation', *rule['required']]) for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    common['declarations'] = _engineering_declarations('global', maximum=8192)
    common['declarations']['items']['properties']['class_name']['enum'] += [
        'VAR', 'VAR_CONSTANT', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT', 'VAR_INPUT_CONSTANT', 'VAR_IN_EXT']
    return _engineering_object(common, ['operation'], oneOf=variants)


def _validate_engineering_schema(rule, value, path='arguments'):
    """Validate the engineering schema subset without project or native I/O."""
    if not rule:
        return
    if 'oneOf' in rule:
        matches = 0
        for option in rule['oneOf']:
            try:
                _validate_engineering_schema(option, value, path)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(path + ' must match one operation or value structure')
    types = {'object': lambda x: isinstance(x, Mapping), 'array': lambda x: isinstance(x, list),
             'string': lambda x: isinstance(x, str), 'integer': lambda x: type(x) is int,
             'boolean': lambda x: type(x) is bool, 'null': lambda x: x is None}
    if rule.get('type') in types and not types[rule['type']](value):
        raise ValueError(path + ' must be ' + rule['type'])
    if 'enum' in rule and value not in rule['enum']:
        raise ValueError(path + ' is not an allowed value')
    if isinstance(value, Mapping):
        fields = rule.get('properties', {})
        if not set(rule.get('required', ())) <= value.keys():
            raise ValueError(path + ' is missing required fields')
        if not rule.get('minProperties', 0) <= len(value) <= rule.get('maxProperties', float('inf')):
            raise ValueError(path + ' has an invalid field count')
        for key, item in value.items():
            if 'propertyNames' in rule:
                _validate_engineering_schema(rule['propertyNames'], key, path + '.key')
            additional = rule.get('additionalProperties', True)
            if key not in fields and additional is False:
                raise ValueError(path + ' has an unknown field: ' + str(key))
            child = fields.get(key, additional if isinstance(additional, dict) else {})
            _validate_engineering_schema(child, item, path + '.' + str(key))
    elif isinstance(value, list):
        if not rule.get('minItems', 0) <= len(value) <= rule.get('maxItems', float('inf')):
            raise ValueError(path + ' has an invalid item count')
        if 'items' in rule:
            for index, item in enumerate(value):
                _validate_engineering_schema(rule['items'], item, path + '[' + str(index) + ']')
    elif isinstance(value, str):
        if not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', float('inf')):
            raise ValueError(path + ' has an invalid text length')
        if 'pattern' in rule and not re.fullmatch(rule['pattern'], value):
            raise ValueError(path + ' has invalid text syntax')
    elif type(value) is int and not rule.get('minimum', -float('inf')) <= value <= rule.get('maximum', float('inf')):
        raise ValueError(path + ' has an out-of-range integer')


def _validate_engineering_input(name, schema, arguments):
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Engineering commands must be valid JSON text') from error
    if name == 'execute_native_engineering' and len(encoded) > 4 * 1024 * 1024 + 32:
        raise ValueError('Native engineering plan is too large')
    _validate_engineering_schema(schema, arguments)
    if name == 'execute_native_engineering':
        if arguments['plan'].get('mode') == 'live':
            from application.live_engineering import normalize_live_plan
            normalize_live_plan(arguments['plan'])
        else:
            from application.native_engineering import normalize_native_plan
            normalize_native_plan(arguments['plan'])
    commands = arguments.get('plan', {}).get('commands', []) if name == 'execute_native_engineering' else [arguments]
    for command in commands:
        rows = command.get('members', command.get('declarations', []))
        if rows:
            from gxw.fb_authoring import _name
            from gxw.declarations import _type_parts
            seen = set()
            for row in rows:
                _name(row['name'], 65001)
                if row['name'].casefold() in seen:
                    raise ValueError('Duplicate engineering declaration name')
                seen.add(row['name'].casefold())
                _, bounds = _type_parts(row['data_type'])
                if len(bounds) > 1 or any(not -2**31 <= low <= high < 2**31 for low, high in bounds):
                    raise ValueError('Engineering declarations require scalar or rank-one types')
        if command.get('operation') in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'):
            from gxw.fb_authoring import _st_implementation
            _st_implementation(command['st_text'])
        if command.get('operation') == 'configure_project':
            from gxw.fb_authoring import validate_configuration_ranges
            ranges = command['options'].get('system_variable_ranges', {})
            validate_configuration_ranges(ranges, cpu='', known_devices=ranges)
    if name in NATIVE_INSPECTION_TOOL_NAMES:
        queries = arguments.get('queries', [])
        for query in queries:
            fields = ('source', 'name') if name == 'read_native_device_assignments' else ('resource',)
            if any(any(ord(char) < 32 or ord(char) == 127 for char in query[field]) for field in fields):
                raise ValueError('Native inspection names cannot contain control characters')
        identities = [(q.get('source'), q.get('name'), q.get('scope', 'local'))
                      if name == 'read_native_device_assignments' else (q.get('resource'), q.get('step')) for q in queries]
        if len(set(identities)) != len(identities):
            raise ValueError('Duplicate native inspection query')


def _get_operation_status(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id:
        raise ToolPreconditionError('NO_PROJECT_SELECTED', '请先明确选择需要查询执行回执的工程。',
                                    next_actions=PROJECT_ACTIONS)
    if sum(key in arguments for key in ('proposal_id', 'execution_job_id')) != 1:
        raise ToolPreconditionError('INVALID_ARGUMENTS', '请提供 proposal_id 或 execution_job_id 中的一项。')
    if not callable(context.operation_status):
        raise ToolPreconditionError('OPERATION_STATUS_UNAVAILABLE', '当前接入方式没有绑定应用执行回执查询。')
    return context.operation_status(context.project_id, **arguments)


def _get_native_engineering_session(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id:
        raise ToolPreconditionError('NO_PROJECT_SELECTED', 'Select the intended engineering project first.',
                                    next_actions=PROJECT_ACTIONS)
    if not callable(context.native_session_status):
        return {'mode': 'background', 'status': 'not_started', 'project_id': context.project_id}
    return context.native_session_status(context.project_id, arguments.get('session_id'))


def _pause_native_engineering_session(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if not context.project_id or not callable(context.native_session_pause):
        raise ValueError('No native engineering session is available in this selected project')
    return context.native_session_pause(context.project_id, arguments['session_id'])


def _get_current_project(context: ToolContext, _arguments: Mapping[str, Any]) -> Dict[str, Any]:
    project = context.project
    if not context.project_id:
        return {"selection_status": "none", "project": None, "next_actions": list(PROJECT_ACTIONS)}
    versions = [item for item in project.get("versions", []) or [] if isinstance(item, Mapping)]
    result = {
        "project_id": context.project_id,
        "name": str(project.get("name") or "未命名项目"),
        "plc_model": context.plc_model,
        "target_mode": str((context.version or {}).get('target_mode') or project.get("target_mode") or "ladder"),
        "workflow_mode": str(project.get("workflow_mode") or "generate"),
        "selected_version_id": context.version_id or None,
        "active_version_id": project.get("active_version_id"),
        "version_count": len(versions),
        "has_confirmed_spec": isinstance(project.get("confirmed_spec"), Mapping),
        "has_pending_review": bool(project.get("pending_review")),
    }
    return {**result, "selection_status": "selected", "project": dict(result), "next_actions": []}


def _device_summary(program_ir: Mapping[str, Any]) -> Dict[str, Any]:
    devices = program_ir.get("devices") or {}
    if not isinstance(devices, Mapping):
        return {"count": 0, "by_type": {}, "addresses": []}
    by_type: Dict[str, int] = {}
    for address, data in devices.items():
        kind = ""
        if isinstance(data, Mapping):
            kind = str(data.get("kind") or data.get("type") or "").upper()
        if not kind:
            match = re.match(r"^[A-Z]+", str(address).upper())
            kind = match.group(0) if match else "OTHER"
        by_type[kind] = by_type.get(kind, 0) + 1
    addresses = [str(value) for value in devices.keys()]
    return {
        "count": len(addresses),
        "by_type": dict(sorted(by_type.items())),
        "addresses": addresses[:120],
        "truncated": len(addresses) > 120,
    }


def _get_generation_context(
    context: ToolContext, arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    # A saved version is authoritative after changing a project's language.
    target_mode = str((context.version or {}).get('target_mode')
                      or context.project.get('target_mode') or 'ladder')
    selection = arguments.get('execution_mode')
    if context.project_id and selection != 'background' and callable(context.native_session_status):
        native = context.native_session_status(context.project_id, None)
        if native.get('mode') == 'live' and native.get('status') not in ('ended', 'closed'):
            contract = {'tool': 'execute_native_engineering', 'mode': 'live',
                'session_action': 'continue', 'session_id': native['session_id'],
                'expected_revision': native['revision'], 'schema_version': 1,
                'controls': {'expected_revision': native['revision']}}
            state = native.get('status')
            if state in ('running', 'busy', 'pause_requested'):
                contract = {'tool': 'get_native_engineering_session', 'session_id': native['session_id']}
            elif state in ('unconfirmed', 'failed'):
                contract.update(session_action='rebind', commands=[])
            elif state == 'paused' and native.get('pending_commands', 0):
                contract['controls']['resume_pending'] = True
                contract['commands'] = []
            return {'project_id': context.project_id, 'plc_model': context.plc_model,
                'execution_mode': 'live', 'target_mode': target_mode,
                'current_version_id': context.version_id or None, 'native_session': native,
                'current_context': native.get('current_context'),
                'generation_request': str(arguments.get('user_requirement') or ''),
                'output_contract': contract,
                'limitations': ['Current context is the last confirmed native source revision; an in-progress operation remains separate.',
                    'Query a running session; reconcile unconfirmed or failed operations without replay; resume an existing paused sequence before submitting new commands.',
                    'Save is an explicit stage boundary. Compilation and actual variable addresses have separate revision bindings.']}
    if selection == 'live':
        raise ValueError('Start a native engineering session before requesting its live generation context')
    from plc.generation_contract import generation_output_contract
    from application.generation_context import (
        _build_knowledge_context, build_generation_instructions, generation_user_input,
        public_generation_ladder, public_generation_specification, public_generation_value,
    )
    confirmed_spec = _confirmed_spec(context)
    from application.confirmed_generation_context import (
        CONFIRMED_GENERATION_REQUEST, project_confirmed_specification,
    )
    public_spec = public_generation_specification(confirmed_spec)
    if target_mode == 'sfc':
        snapshot = _read_sfc_project(context, {})
        return {'project_id': context.project_id, 'plc_model': snapshot['model']['cpu'], 'target_mode': 'sfc',
                'current_version_id': context.version_id, 'current_model': snapshot['model'],
                'output_contract': {'tool': 'create_sfc_candidate', 'operation': 'edit'},
                'limitations': ['Existing source records only; no SFC compiler or graph generator.',
                                'Native compilation, checking and PLC execution are separate results.']}
    if target_mode == 'st' and context.native_snapshot is not None:
        from gxw.declarations import CLASS_CODES
        from gxw.st_program import project_context_snapshot
        from gxw.visible_engineering import normalized_st
        from gxworks2.project_profiles import native_project_profile
        from shared.public_results import public_result_field

        selected_name = (context.version or {}).get('program_name')
        if not isinstance(selected_name, str) or not selected_name:
            raise ValueError('Saved ST generation context requires its exact current logical Program')
        snapshot = project_context_snapshot(context.native_snapshot, selected_name)
        profile = native_project_profile(context.plc_model)
        if profile is None or profile.cpu != snapshot['cpu']:
            raise ValueError('Saved ST generation context CPU differs from its selected PLC model')
        selected = [row for row in snapshot['pous'] if row['program'] == selected_name]
        if (len(selected) != 1 or selected[0]['kind'] != 'program' or selected[0]['language'] != 'st'
                or selected[0]['source_status'] != 'available' or len(selected[0]['task_bindings']) != 1
                or not isinstance(context.st_text, str)
                or normalized_st(selected[0]['st_text']) != normalized_st(context.st_text)):
            raise ValueError('Saved ST generation context differs from its current ordinary Program, text or task')

        # Core owns the source interpretation. This projection removes native
        # storage/layout evidence before either raw or model tool results exist.
        private_fields = {'storage', 'raw', 'vendor', 'bytes', 'source_stream', 'source_offset',
            'definition_offset', 'node_offset', 'unknown_record_count', 'codepage', 'type_code',
            'record_id', 'record_ids', 'offset', 'class_code', 'record', 'records',
            'snapshot', 'observation', 'data_id', 'control_id', 'hwnd', 'pointer', 'ptr'}
        class_names = {value: key for key, value in CLASS_CODES.items()}
        def engineering_value(value):
            if isinstance(value, Mapping):
                result = {key: engineering_value(item) for key, item in value.items()
                    if public_result_field(key) and str(key).lower() not in private_fields
                    and not str(key).lower().startswith(('raw_', 'vendor_', 'native_'))
                    and not str(key).lower().endswith(('_offset', '_path', '_dir', '_file', '_handle', '_pointer'))
                    and not any(token in str(key).lower() for token in
                        ('base64', 'password', 'credential', 'api_key', 'token', 'secret'))
                    and not isinstance(item, (bytes, bytearray, memoryview))}
                if value.get('class_code') in class_names and 'class_name' not in result:
                    result['class_name'] = class_names[value['class_code']]
                return result
            if isinstance(value, (list, tuple)):
                return [engineering_value(item) for item in value if not isinstance(item, (bytes, bytearray, memoryview))]
            return public_generation_value(value)

        current = public_generation_value(engineering_value({key: snapshot[key] for key in (
            'status', 'origin', 'cpu', 'selected_source', 'globals', 'pous', 'structures',
            'configuration', 'resources', 'tasks', 'issues', 'compilation', 'execution')}))
        program, = [row for row in current['pous'] if row['program'] == selected_name]
        return {'project_id': context.project_id, 'plc_model': context.plc_model,
            'target_mode': 'st', 'execution_mode': 'background',
            'workflow_mode': str(context.project.get('workflow_mode') or 'generate'),
            'current_version_id': context.version_id, 'has_confirmed_spec': isinstance(confirmed_spec, Mapping),
            'confirmed_spec': public_spec, 'current_context': current, 'selected_program': program,
            'generation_request': public_generation_value(str(arguments.get('user_requirement') or '')),
            'generation_instructions': 'Edit the selected ordinary ST Program in the current saved project. '
                'Keep its existing declarations, task binding and other project sources unless explicitly requested. '
                'Return create_st_candidate arguments using operation edit_st_program and the selected logical source_name. '
                'st_text replaces the complete body; omitted declarations retain the current declarations. '
                'Include declaration upserts only when requested; do not replace this project with a template.',
            'output_contract': {'tool': 'create_st_candidate', 'operation': 'edit_st_program',
                'source_name': selected_name, 'input_shape': 'arguments',
                'required_fields': ['operation', 'source_name'],
                'editable_fields': ['st_text', 'declarations'],
                'requires_any': ['st_text', 'declarations'],
                'declarations': 'upserts; omitted declarations preserve current rows'},
            'limitations': ['This context is read from the selected saved GXW version, not the unsaved active IDE.',
                'The candidate uses the shared approval and isolated native source-save path.',
                'Native compilation, current device allocation and PLC execution remain separate results.']}
    if target_mode == 'fbd':
        from application.fbd import build_fbd_generation_context
        catalog = _get_fbd_catalog(context, {})
        previous = _read_fbd_project(context, {})['model'] if context.fbd_baseline is not None else None
        generation = build_fbd_generation_context(str(arguments.get('user_requirement') or ''),
            plc_model=context.plc_model, confirmed_spec=confirmed_spec, catalog=catalog, previous=previous)
        return {'project_id': context.project_id, 'plc_model': context.plc_model, 'cpu': catalog['cpu'], 'target_mode': 'fbd',
                'workflow_mode': str(context.project.get('workflow_mode') or 'generate'),
                'has_confirmed_spec': isinstance(confirmed_spec, Mapping), 'confirmed_spec': public_spec,
                'current_version_id': context.version_id or None,
                'generation_instructions': generation['generation_instructions'],
                'generation_handoff': generation['generation_handoff'],
                'generation_request': str(arguments.get('user_requirement') or ''),
                'generation_input': generation['generation_input'],
                'output_contract': {'tool': 'create_fbd_candidate', 'schema_version': 2,
                                    'input_shape': 'parameters', 'model_schema': generation['generation_input']['parameter_schema'],
                                    'operation': 'edit' if previous is not None else 'generate'},
                'catalog': {**catalog, 'nodes': generation['generation_input']['catalog'],
                            'empty_model': catalog.get('empty_parameter_model', dict(schema_version=2, program=catalog['program'], networks=[]))},
                'current_model': generation['generation_input']['previous']}
    source = context.ladder
    if isinstance(context.program_ir, Mapping):
        from plc.ir import ir_to_ladder
        source = ir_to_ladder(context.program_ir)
    current_ladder = public_generation_ladder(source)
    user_requirement = public_generation_value(str(arguments.get("user_requirement") or ""))
    is_edit_mode = target_mode == "ladder" and current_ladder is not None
    if (target_mode == "ladder" and not is_edit_mode
            and arguments.get("workflow") != "detailed"
            and (not confirmed_spec or arguments.get("workflow") == "direct_generation")
            and str(arguments.get("user_requirement") or "").strip()):
        from application.confirmed_generation_context import build_direct_generation_context
        from application.generation_agent import _compact_wire_renderer, direct_response_schema
        from application.compact_protocol import PROTOCOL_VERSION
        raw = str(arguments["user_requirement"])
        direct = build_direct_generation_context(raw, context.plc_model, confirmed_spec=confirmed_spec,
            knowledge_builder=_build_knowledge_context, wire_renderer=_compact_wire_renderer(context.plc_model, direct=True))
        return {"project_id": context.project_id, "plc_model": context.plc_model, "target_mode": target_mode,
                "workflow_mode": "direct_generation", "has_confirmed_spec": bool(confirmed_spec),
                "confirmed_spec": public_spec, "user_facts": direct.confirmed_spec,
                "current_version_id": None, "generation_request": raw,
                "generation_instructions": direct.wire_packet["messages"][0]["content"],
                "generation_handoff": direct.handoff,
                "output_contract": {"tool": "create_program_candidate", "protocol": PROTOCOL_VERSION,
                                    "schema": direct_response_schema(), "needs_input": "向用户提出必要问题，解决前不得创建候选。"}}
    if target_mode == "ladder" and isinstance(confirmed_spec, Mapping) and confirmed_spec:
        public_spec = project_confirmed_specification(confirmed_spec)
        if not is_edit_mode:
            user_requirement = CONFIRMED_GENERATION_REQUEST
    model_request = generation_user_input(
        user_requirement, is_edit_mode=is_edit_mode, target_mode=target_mode,
    )
    generation_handoff = {}
    def capture_context(value):
        generation_handoff.update(value)
    instructions = build_generation_instructions(
        model_request,
        plc_model=context.plc_model,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
        confirmed_context=public_spec,
        current_version_json=current_ladder,
        # Clean retrieved text before assembly; generic path matching must
        # never rewrite application-owned schema patterns in the prompt.
        knowledge_builder=_build_knowledge_context,
        on_context=capture_context,
    )
    receipt_id = _decision_receipt_id(context)
    if receipt_id:
        generation_handoff["decision_receipt_id"] = receipt_id
    return {
        "project_id": context.project_id,
        "plc_model": context.plc_model,
        "target_mode": target_mode,
        "workflow_mode": str(context.project.get("workflow_mode") or "generate"),
        "has_confirmed_spec": isinstance(confirmed_spec, Mapping),
        "confirmed_spec": public_spec,
        "output_contract": generation_output_contract(allow_partial=is_edit_mode, plc_model=context.plc_model, target_mode=target_mode),
        "current_version_id": context.version_id or None,
        "generation_instructions": instructions,
        "generation_handoff": public_generation_value(generation_handoff),
        "generation_request": model_request,
    }


def _create_program_candidate(
    context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None,
) -> Dict[str, Any]:
    from plc.core import PLCCore
    from plc.ir import canonical_sha256

    if str(context.project.get("target_mode") or "ladder") != "ladder":
        raise ValueError("只有 ladder 目标模式可以创建新程序候选。")
    core = PLCCore()
    confirmed_spec = copy.deepcopy(_confirmed_spec(context))
    candidate_spec = confirmed_spec
    ladder = arguments["ladder"]
    if isinstance(generation_handoff, dict) and generation_handoff.get("mode") == "direct_generation":
        from application.confirmed_generation_context import project_direct_user_facts
        from application.generation_agent import prepare_model_candidate
        candidate_spec = project_direct_user_facts(generation_handoff["raw_requirement"], context.plc_model, confirmed_spec)
        if isinstance(ladder, Mapping) and ("r" in ladder or "result" in ladder):
            ladder = prepare_model_candidate(ladder, candidate_spec, context.plc_model)["ladder"]
    candidate = core.create_program_candidate(
        ladder,
        plc_model=context.plc_model,
        program_name=arguments.get("program_name", (context.program_ir or {}).get("program_name", "MAIN")),
        confirmed_spec=candidate_spec,
        previous_program=context.program_ir,
    )
    compiled = core.compile_project(candidate["candidate_ir"], validation_profile=candidate["validation_profile"])
    if not isinstance(generation_handoff, dict):
        from application.confirmed_generation_context import project_confirmed_specification
        from plc.specification.provenance import handoff_snapshot
        generation_handoff = handoff_snapshot(
            project_confirmed_specification(confirmed_spec), stage="external_generate",
            decision_receipt_id=_decision_receipt_id(context),
            evidence={"stage": "external_generate", "status": "not_recorded", "records": [],
                      "reason": "external_context_not_correlated"},
        )
    generation_handoff = copy.deepcopy(generation_handoff)
    generation_handoff["confirmed_spec_sha256"] = (
        canonical_sha256(confirmed_spec) if confirmed_spec is not None else None)
    generation_handoff["external_model_use"] = "not_observed"
    action = {
        "type": "accept_generated_program",
        "project_id": context.project_id,
        "project_name": str(context.project.get("name") or "未命名项目"),
        "program_name": candidate["candidate_ir"]["program_name"],
        "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"],
        "candidate_ir_sha256": candidate["candidate_ir_sha256"],
        "ladder_sha256": candidate["ladder_sha256"],
        "confirmed_spec_hash": (
            canonical_sha256(confirmed_spec) if confirmed_spec is not None else None
        ),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "summary": copy.deepcopy(candidate["summary"]),
        "artifact_hashes": copy.deepcopy(compiled.get("hashes") or {}),
        "_candidate_ir": copy.deepcopy(candidate["candidate_ir"]),
        "_confirmed_spec": confirmed_spec,
        "_validation_profile": candidate["validation_profile"],
        "normalization": copy.deepcopy(candidate["normalization"]),
        "_generation_handoff": generation_handoff,
    }
    if context.program_ir is not None:
        action.update(base_version_id=context.version_id, base_ir_sha256=canonical_sha256(context.program_ir))
    return {
        "requires_confirmation": True,
        "message": "程序候选已通过结构检查和临时编译，等待工程确认；行为与原生验证尚未执行。",
        "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"],
        "summary": copy.deepcopy(candidate["summary"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "validation_profile": candidate["validation_profile"],
        "normalization": copy.deepcopy(candidate["normalization"]),
        "generation_handoff": copy.deepcopy(generation_handoff),
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "verification": {
            "structural_checks_passed": True,
            "deterministic_checks_passed": True,
            "behavior_verified": False,
            "native_verified": False,
            "scope": "API structural acceptance, IR consistency and temporary artifact compilation only. Static findings are diagnostics. "
                     "Input sequences, timing and requirement coverage still need independent verification.",
        },
        "pending_action": action,
    }


def _get_current_program_info(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.analysis_source is not None:
        source = context.analysis_source
        raw = source["raw_networks"]
        return {"available": True, "version_id": None, "editable": False,
                "source_ref": source["source_ref"], "plc_model": source["plc_model"],
                "program_name": source["program_name"], "coverage": source["coverage"],
                "network_count": len(raw), "networks_truncated": len(raw) > 100,
                "networks": [{key: value for key, value in row.items() if key != "raw_instructions"}
                             | {"instruction_count": len(row["raw_instructions"])} for row in raw[:100]],
                "devices": _device_summary(context.program_ir) if context.program_ir else {},
                "artifacts": {}, "runtime_verified": False}
    version = context.version
    if not isinstance(version, Mapping):
        return {"available": False, "reason": "NO_PROGRAM_SOURCE", "next_actions": list(SOURCE_ACTIONS),
                "message": "当前没有选中的已生成版本。"}

    artifacts = version.get("artifacts") or {}
    artifact_flags = {
        name: bool(value)
        for name, value in artifacts.items()
        if name in {"json", "ir", "program_csv", "comment_csv", "svg", "st", "st_from_ir", "fbd", "gxw", "write_report"}
    } if isinstance(artifacts, Mapping) else {}
    result: Dict[str, Any] = {
        "available": True,
        "version_id": context.version_id,
        "target_mode": str(version.get("target_mode") or context.project.get("target_mode") or "ladder"),
        "plc_model": context.plc_model,
        "program_name": str(version.get("program_name") or "MAIN"),
        "revision": version.get("revision"),
        "ir_schema_version": version.get("ir_schema_version"),
        "ir_sha256": version.get("ir_sha256"),
        "ladder_sha256": version.get("ladder_sha256"),
        "artifacts": artifact_flags,
    }
    program_ir = context.program_ir
    if isinstance(program_ir, Mapping):
        networks = [
            {
                "id": str(network.get("id") or ""),
                "comment": str(network.get("comment") or "")[:200],
                "instruction_count": len(network.get("instructions") or []),
                "reads": list(network.get("reads") or [])[:40],
                "writes": list(network.get("writes") or [])[:40],
            }
            for network in (program_ir.get("networks") or [])[:100]
            if isinstance(network, Mapping)
        ]
        result.update(
            {
                "network_count": len(program_ir.get("networks") or []),
                "networks": networks,
                "networks_truncated": len(program_ir.get("networks") or []) > 100,
                "devices": _device_summary(program_ir),
                "timing": copy.deepcopy(program_ir.get("timing") or {}),
                "static_analysis": {
                    "counts": copy.deepcopy(
                        (program_ir.get("analysis") or {}).get("counts") or {}
                    ),
                    "rules_checked": list(
                        (program_ir.get("analysis") or {}).get("rules_checked") or []
                    ),
                    "dependency_node_count": len(
                        (program_ir.get("analysis") or {})
                        .get("dependency_graph", {})
                        .get("nodes", [])
                    ),
                    "dependency_edge_count": len(
                        (program_ir.get("analysis") or {})
                        .get("dependency_graph", {})
                        .get("device_edges", [])
                    ),
                    "findings": copy.deepcopy(
                        ((program_ir.get("analysis") or {}).get("findings") or [])[:30]
                    ),
                },
            }
        )
    return result


def _search_plc_manual(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.evidence import evidence_record

    query = str(arguments.get("query") or "").strip()
    top_k = int(arguments.get("top_k", 5))
    rows = retrieve_fact_aware_knowledge(
        query,
        plc_model=context.plc_model,
        task_type="analysis",
        top_k=top_k,
    )
    results = []
    for row in rows[:top_k]:
        if not isinstance(row, Mapping):
            continue
        results.append(
            {
                "id": str(row.get("id") or ""),
                "manual_id": str(row.get("manual_id") or ""),
                "source": str(row.get("source") or ""),
                "page": row.get("page"),
                "pdf_page": row.get("pdf_page"),
                "section": str(row.get("section") or ""),
                "chunk_type": str(row.get("chunk_type") or ""),
                "role": evidence_record(row)['role'],
                "instruction_opcode": str(row.get("instruction_opcode") or ""),
                # The shared retriever delivers complete evidence. Cutting a block can detach a CPU row
                # from its footnote or cut structured operand JSON in half.
                "text": str(row.get("text") or ""),
                **{key: copy.deepcopy(row[key]) for key in (
                    "manual_number", "revision", "manual_type", "page_end", "pdf_page_end",
                    "plc_models", "source_applicability", "source_evidence", "source_chunk_id", "source_section_id",
                    "source_address_locations",
                    "fact_kind", "fact_target", "fact_dimensions", "device_lookup_basis",
                    "structured_fact_requested_target",
                    "device_definition_status", "device_definition", "evidence_scope",
                    "table_binding_status", "content_status",
                    "content_origin", "distribution_status", "technical_status",
                ) if key in row},
            }
        )
    if not arguments.get('debug', False):
        from shared.model_context import technical_knowledge_text
        results = [{**{key: copy.deepcopy(row[key]) for key in
                    ('fact_kind', 'fact_target', 'plc_models', 'instruction_opcode') if row.get(key)},
                    'text': technical_knowledge_text(row)} for row in results]
        return {'query': query, 'plc_model': context.plc_model, 'count': len(results), 'results': results}
    return {
        "query": query,
        "plc_model": context.plc_model,
        "count": len(results),
        "results": results,
        "evidence_delivery": {
            "policy": "relevance_complete_units",
            "top_k": top_k,
            "omitted_evidence_is_not_unsupported": True,
        },
    }


def _validate_current_program(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.st_text is not None:
        from plc.st_validation import inspect_st_program
        return {**inspect_st_program(context.st_text, plc_model=context.plc_model,
                    hardware_context=(_confirmed_spec(context) or {}).get('hardware_profile'),
                    confirmed_spec=_confirmed_spec(context)),
                "version_id": context.version_id}
    if not isinstance(context.ladder, Mapping):
        return {
            "available": False,
            "valid": False,
            "message": "当前没有可校验的梯形图版本。",
        }

    from inspection.engine import run_local_inspection

    report = run_local_inspection(
        context.ladder,
        report_type="program_review",
        request={"review_focus": "Agent 请求校验当前程序"},
        confirmed_spec=(context.version or {}).get("confirmed_spec_snapshot")
        or context.project.get("confirmed_spec"),
        plc_model=context.plc_model,
        base_version_id=context.version_id or None,
        trigger="agent_tool",
        depth="basic",
    )
    findings = []
    for finding in (report.get("findings") or [])[:30]:
        if not isinstance(finding, Mapping):
            continue
        findings.append(
            {
                key: copy.deepcopy(finding.get(key))
                for key in (
                    "finding_id",
                    "id",
                    "code",
                    "severity",
                    "title",
                    "message",
                    "suggestion",
                    "rung_ids",
                    "devices",
                )
                if finding.get(key) not in (None, "", [])
            }
        )
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in report.get("findings") or []:
        severity = str((finding or {}).get("severity") or "").lower()
        if severity in counts:
            counts[severity] += 1
    from plc.validation import SUPPORTED_PLC_MODELS
    profile_available = context.plc_model in SUPPORTED_PLC_MODELS
    conditions_unverified = any(
        item.get('category') == 'device_applicability_unverified' for item in report.get('findings', []))
    available = report.get("status") != "unsupported"
    status = ('failed' if counts['error'] else 'unverified'
              if not profile_available or conditions_unverified or not available else 'passed')
    return {
        "available": available,
        "valid": status == 'passed',
        "status": status,
        "version_id": context.version_id,
        "plc_model": context.plc_model,
        "summary": str(report.get("summary") or ""),
        "counts": counts,
        "finding_count": len(report.get("findings") or []),
        "findings": findings,
        "findings_truncated": len(report.get("findings") or []) > len(findings),
        "base_json_hash": report.get("base_json_hash")
        or (report.get("base") or {}).get("json_sha256"),
    }


def _require_program_ir(context: ToolContext) -> Mapping[str, Any]:
    if not isinstance(context.program_ir, Mapping):
        raise ValueError("当前没有可供 PLC Core 读取的程序 IR。")
    return context.program_ir


def _decision_receipt_id(context: ToolContext):
    # The same binding as _confirmed_spec: a historical version must never
    # acquire the current project's newer decision merely because it lacks one.
    if (context.version or {}).get("confirmed_spec_snapshot"):
        handoff = (context.version or {}).get("generation_handoff") or {}
        return handoff.get("decision_receipt_id") if isinstance(handoff, Mapping) else None
    return context.project.get("confirmed_decision_receipt_id")


def _confirmed_spec(context: ToolContext):
    return (context.version or {}).get("confirmed_spec_snapshot") or context.project.get(
        "confirmed_spec"
    )


def _read_network(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from plc.core import PLCCore
    if context.analysis_source is not None:
        raw = next((row for row in context.analysis_source["raw_networks"]
                    if row["id"] == arguments["network_id"]), None)
        if raw is None:
            raise ToolPreconditionError("NETWORK_NOT_FOUND", "快照中没有此 Network。",
                next_actions=({"tool": "get_current_program_info", "arguments": {"source_ref": arguments["source_ref"]}},))
        if context.program_ir is None:
            return {"network": copy.deepcopy(raw), "coverage": "raw_instructions_only"}
        network = PLCCore().read_network(context.program_ir, arguments["network_id"])
        return {"network": network, "raw_instructions": copy.deepcopy(raw["raw_instructions"]),
                "coverage": copy.deepcopy(context.analysis_source["coverage"])}
    network = PLCCore().read_network(
        _require_program_ir(context),
        str(arguments.get("network_id") or ""),
    )
    return {"network": network}


def _get_diagnostics(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if context.st_text is not None:
        return _validate_current_program(context, {})
    from plc.core import PLCCore

    return {**dict(PLCCore().validate_project(_require_program_ir(context), _confirmed_spec(context))),
            "evidence_kind": "static_program_check", "runtime_verified": False}


def _validate_project(
    context: ToolContext, arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if arguments.get('native') is True:
        from application.native_validation import validate_native_snapshot
        snapshot = context.native_snapshot if context.native_snapshot is not None else context.fbd_baseline
        if snapshot is None or not context.version_id:
            raise ValueError('原生检查需要绑定当前版本已保存的 GXW 快照；仅有源码或 CSV 不足以启动原生检查。')
        return validate_native_snapshot(snapshot,
            project_id=context.project_id, version_id=context.version_id,
            program=arguments.get('program') or (context.version or {}).get('program_name'),
            expected_cpu=context.plc_model if (context.version or {}).get('target_mode') in {'ladder','st','sfc'} else None,
            target_mode=(context.version or {}).get('target_mode'), st_text=context.st_text,
            program_ir=context.program_ir, sfc_graph=context.sfc_graph)
    if 'program' in arguments:
        raise ValueError('program 仅用于 native=true 的原生检查上下文。')
    if (context.version or {}).get('target_mode') == 'sfc':
        snapshot = _read_sfc_project(context, {})
        return {'status': 'source_preserved', 'gx_compile': 'not_run',
                'issues': snapshot['model']['issues'], 'position_resolution': 'not-established'}
    if context.st_text is not None:
        return _validate_current_program(context, {})
    from plc.core import PLCCore

    return dict(
        PLCCore().validate_project(
            _require_program_ir(context),
            _confirmed_spec(context),
        )
    )


def _compile_project(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    if (context.version or {}).get('target_mode') == 'sfc':
        return {'compiled': False, 'status': 'not_run',
                'message': 'SFC 子程序使用原生 validate_project(native=true) 编译与检查。'}
    if context.st_text is not None:
        return {"compiled": False, "status": "unverified", "artifacts": ["st"],
                "message": "ST 源码已保存；本地静态检查不能替代 GX Works2 编译。",
                "validation": _validate_current_program(context, {})}
    from plc.core import PLCCore
    from plc.validation import ValidationProfileUnavailableError

    core = PLCCore()
    program = _require_program_ir(context)
    try:
        compiled = core.compile_project(program)
    except ValidationProfileUnavailableError:
        return {"compiled": False, "status": "unverified", "artifacts": [],
                "message": "当前型号缺少本地校验规则，未执行本地编译；需要独立的原生工程检查。",
                "validation": core.validate_project(program, _confirmed_spec(context))}
    return {
        "compiled": True,
        "artifacts": sorted(compiled.get("artifacts") or {}),
        "hashes": copy.deepcopy(compiled.get("hashes") or {}),
    }


def _patch_program(context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None) -> Dict[str, Any]:
    from plc.core import PLCCore
    from plc.ir import canonical_sha256

    program = _require_program_ir(context)
    if not isinstance(context.version, Mapping):
        raise ValueError("当前没有绑定候选补丁的基础版本。")
    patch = copy.deepcopy(arguments.get("patch"))
    if not isinstance(patch, Mapping):
        raise ValueError("patch 必须是 JSON 对象。")
    patch = dict(patch)
    patch.setdefault("base_revision", program.get("revision"))
    patch.setdefault("base_ir_sha256", canonical_sha256(program))
    patch.setdefault("target_revision", int(program.get("revision") or 0) + 1)
    core = PLCCore()
    confirmed_spec = copy.deepcopy(_confirmed_spec(context))
    candidate = dict(core.patch_program(program, patch, confirmed_spec))
    compiled = core.compile_project(candidate["candidate_ir"])
    action = {
        "type": "accept_candidate_patch",
        "project_id": context.project_id,
        "project_name": str(context.project.get("name") or "未命名项目"),
        "base_version_id": context.version_id,
        "candidate_id": candidate["candidate_id"],
        "base_revision": candidate["base_revision"],
        "base_ir_sha256": candidate["base_ir_sha256"],
        "target_revision": candidate["target_revision"],
        "candidate_ir_sha256": candidate["candidate_ir_sha256"],
        "confirmed_spec_hash": (
            canonical_sha256(confirmed_spec) if confirmed_spec is not None else None
        ),
        "diff": copy.deepcopy(candidate["diff"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "artifact_hashes": copy.deepcopy(compiled.get("hashes") or {}),
        "_candidate_ir": copy.deepcopy(candidate["candidate_ir"]),
        "_confirmed_spec": confirmed_spec,
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "_generation_handoff": copy.deepcopy(generation_handoff),
    }
    return {
        "requires_confirmation": True,
        "message": "候选补丁已通过确定性校验和临时编译，等待用户查看差异。",
        "maintainability_review": copy.deepcopy(candidate.get("maintainability_review")),
        "generation_handoff": copy.deepcopy(generation_handoff),
        "candidate_id": candidate["candidate_id"],
        "diff": copy.deepcopy(candidate["diff"]),
        "diagnostics": copy.deepcopy(candidate["diagnostics"]),
        "pending_action": action,
    }


def _request_native_engineering(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.native_engineering import normalize_native_plan, validate_native_baseline
    if not isinstance(context.version, Mapping) or context.version.get('target_mode') not in {'fbd', 'st'}:
        raise ValueError('可见原生工程执行需要已选择的结构化 FBD 或 ST 保存版本。')
    baseline = context.native_snapshot or context.fbd_baseline
    if not baseline:
        raise ValueError('可见原生工程执行需要当前版本保存的 GXW 工程。')
    selected_cpu = context.version.get('plc_model') or context.project.get('plc_model')
    if not isinstance(selected_cpu, str) or not selected_cpu.strip():
        raise ToolPreconditionError('PLC_MODEL_REQUIRED', 'Native engineering requires the selected saved project CPU model.')
    try:
        if arguments.get('plan', {}).get('mode') == 'live':
            from application.live_engineering import normalize_live_plan
            plan = normalize_live_plan(arguments['plan'], plc_model=selected_cpu)
        else:
            plan = normalize_native_plan(arguments.get('plan'), plc_model=selected_cpu)
    except ValueError as error:
        raise ToolPreconditionError('INVALID_ARGUMENTS', str(error)) from error
    validate_native_baseline(baseline, plc_model=selected_cpu, live=plan.get('mode') == 'live')
    if context.version['target_mode'] == 'st':
        from gxw.fb_authoring import st_program_snapshot
        from gxw.visible_engineering import normalized_st
        selected = [row for row in st_program_snapshot(baseline)['programs']
                    if row['program'] == context.version.get('program_name')]
        if (len(selected) != 1 or len(selected[0]['task_bindings']) != 1
                or not isinstance(context.st_text, str)
                or normalized_st(selected[0]['st_text']) != normalized_st(context.st_text)):
            raise ValueError('ST 版本正文、当前 Program 及唯一任务绑定不一致。')
    return {'requires_confirmation': True,
        'message': ('工程步骤已冻结，获准后沿同一原生会话执行；保存步骤单独交付版本。'
                    if plan.get('mode') == 'live' else
                    '工程方案已冻结。获准后会在独立 GX Works2 工程副本中执行，核对所请求的验证层后保存新版本。'),
        'pending_action': {'type': 'execute_native_engineering', 'project_id': context.project_id,
                           'version_id': context.version_id, 'plan': plan}}


def _request_gxworks2_import(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    version = context.version
    if not isinstance(version, Mapping):
        raise ValueError("当前没有选中的已生成版本。")
    if str(version.get("target_mode") or "") != "ladder":
        raise ValueError("只有梯形图版本可导入 GX Works2。")
    artifacts = version.get("artifacts") or {}
    missing = [
        label
        for key, label in (
            ("program_csv", "程序 CSV"),
            ("comment_csv", "软元件注释 CSV"),
        )
        if not isinstance(artifacts, Mapping) or not artifacts.get(key)
    ]
    if missing:
        raise ValueError("当前版本缺少" + "、".join(missing) + "。")
    return {
        "requires_confirmation": True,
        "message": "需要用户确认后，才能调用现有的一键导入服务。",
        "pending_action": {
            "type": "import_current_program_to_gxworks2",
            "project_id": context.project_id,
            "project_name": str(context.project.get("name") or "未命名项目"),
            "version_id": context.version_id,
            "revision": version.get("revision"),
            "program_name": str(version.get("program_name") or "MAIN"),
            "plc_model": context.plc_model,
        },
    }


def _get_fbd_catalog(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import _target_baseline, catalog_snapshot

    raw = _target_baseline(context.fbd_baseline, context.plc_model)
    return catalog_snapshot(raw, arguments.get('program') or (context.version or {}).get('program_name')) | {
        'limitations': ['Fresh projects use CPU-matched blank templates for 56 enabled Q/L/FX menu identities; FX1 is unsupported by the current FBD reproduction workflow; parameter-only targets have no FBD Program.',
                        'Callable ports are bound to this project CPU and source declarations.',
                        'GX Works2 compilation is a separate validation step.']}


def _read_fbd_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload, inspect_upload, read_snapshot

    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.fbd_baseline
    if raw is None:
        raise ValueError('当前没有绑定的 FBD 工程；请提供 GXW 文件的 data_base64。')
    if uploaded is not None and arguments.get('program') is None:
        names = inspect_upload(uploaded)['programs']
        if len(names) != 1:
            return {'available': False, 'programs': names, 'message': '请指定要读取的 Program.pou。'}
    return {'available': True, 'version_id': context.version_id or None,
            **read_snapshot(raw, arguments.get('program') or ((context.version or {}).get('program_name') if uploaded is None else None))}


def _read_fbd_libraries(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload, libraries_snapshot
    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.fbd_baseline
    if raw is None:
        raise ValueError('请提供来源 GXW 的 data_base64 或选择已保存的 FBD 工程。')
    return libraries_snapshot(raw, arguments.get('library_name'), include_source=arguments.get('include_source') is True)


def _read_fbd_definition(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from gxw.fb_authoring import definition_snapshot
    if context.fbd_baseline is None:
        raise ValueError('请选择已保存的 FBD 工程版本。')
    return {'version_id': context.version_id, **definition_snapshot(context.fbd_baseline, arguments.get('source_name'))}


def _create_fbd_candidate(context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None) -> Dict[str, Any]:
    from pathlib import Path
    import tempfile
    from application.fbd import (AUTHORING_OPERATIONS, authoring_command, decode_upload, pack_candidate,
                                 prepare_candidate, version_requires_native_save)

    if context.project.get('target_mode') != 'fbd' and (context.version or {}).get('target_mode') != 'fbd':
        raise ValueError('FBD 候选需要绑定 FBD 目标工程。')
    operation = arguments['operation']
    if arguments.get('native_save') is not None and (operation != 'import' or type(arguments['native_save']) is not bool):
        raise ValueError('native_save 只用于 FBD 导入时选择后续原生保存。')
    if operation not in ('create_fb', 'create_program', *AUTHORING_OPERATIONS) and 'source_name' in arguments:
        raise ValueError('source_name 只用于新建 FBD POU。')
    if operation != 'import_library' and 'library_name' in arguments:
        raise ValueError('library_name 只用于导入用户库。')
    if operation != 'create_program' and any(key in arguments for key in ('resource', 'task', 'scope_mode')):
        raise ValueError('资源和任务选择只用于新建普通 FBD Program。')
    if operation not in AUTHORING_OPERATIONS and any(key in arguments for key in ('members', 'declarations', 'st_text', 'options')):
        raise ValueError('members、declarations、st_text 和 options 只用于编写项目定义或配置。')
    if operation in AUTHORING_OPERATIONS:
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('编写项目定义需要已保存的 FBD 基础版本。')
        kwargs = {'baseline': context.fbd_baseline, 'authoring': authoring_command(arguments),
                  'program_name': context.version.get('program_name')}
    elif operation == 'compile':
        if not context.version_id or context.fbd_baseline is None or any(key in arguments for key in ('model','data_base64','program')):
            raise ValueError('编译保存需要当前 FBD 基础版本，不接受源码修改。')
        kwargs = {'baseline':context.fbd_baseline,'compile_save':True,'program_name':context.version.get('program_name')}
    elif operation == 'generate':
        if context.version is not None or context.fbd_baseline is not None:
            raise ValueError('已有版本的 FBD 工程请使用 edit 并绑定基础版本。')
        if 'data_base64' in arguments or 'program' in arguments:
            raise ValueError('generate 只接受 model 和 summary。')
        kwargs = {'model': arguments.get('model')}
    elif operation == 'edit':
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('edit 需要绑定已保存的 FBD 基础版本。')
        if 'data_base64' in arguments or 'program' in arguments:
            raise ValueError('edit 的 Program.pou 由 model.program 指定。')
        kwargs = {'model': arguments.get('model'), 'baseline': context.fbd_baseline,
                  'native_save': version_requires_native_save(context.version)}
    elif operation == 'import_library':
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('库导入需要绑定已保存的 FBD 基础版本。')
        if not isinstance(arguments.get('library_name'), str) or any(key in arguments for key in ('model', 'program', 'source_name')):
            raise ValueError('库导入只接受来源 GXW 的 data_base64、library_name 和 summary。')
        kwargs = {'baseline': context.fbd_baseline, 'library_source': decode_upload(arguments.get('data_base64')),
                  'library_name': arguments['library_name'], 'program_name': context.version.get('program_name')}
    elif operation in ('create_fb', 'create_program'):
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('新建 FBD POU 需要绑定已保存的 FBD 基础版本。')
        if any(key in arguments for key in ('model', 'data_base64', 'program')):
            raise ValueError('新建 FBD POU 只接受名称、目标任务和 summary。')
        if not isinstance(arguments.get('source_name'), str):
            raise ValueError('新建 FBD POU 需要 source_name。')
        kwargs = {'baseline': context.fbd_baseline, 'source_name': arguments['source_name']}
        if operation == 'create_program':
            kwargs.update(pou_kind='program', **{key: arguments.get(key) for key in ('resource', 'task', 'scope_mode')})
    elif operation == 'import':
        if 'model' in arguments:
            raise ValueError('import 使用原始 GXW，不接受 model。')
        kwargs = {'imported': decode_upload(arguments.get('data_base64')),
                  'program_name': arguments.get('program'), 'native_save': arguments.get('native_save') is True}
    else:
        raise ValueError('Unsupported FBD operation')
    with tempfile.TemporaryDirectory(prefix='gxw-fbd-candidate-') as directory:
        payload = prepare_candidate(directory, **kwargs, plc_model=context.plc_model,
                                    summary=arguments.get('summary') or 'Agent 提出的 FBD 候选')
        if generation_handoff is not None:
            payload['metadata']['generation_handoff'] = copy.deepcopy(generation_handoff)
        elif operation in {'generate', 'edit'}:
            payload['metadata']['generation_handoff'] = {'stage': operation,
                'external_model_use': 'not_observed', 'trace_gaps': ['generation_context_not_observed']}
        graph = json.loads((Path(directory) / 'fbd.json').read_bytes())
        svg = (Path(directory) / 'fbd.svg').read_text(encoding='utf-8')
        authored_definition = json.loads((Path(directory) / 'definition.json').read_bytes()) if 'definition' in payload['artifacts'] else None
        snapshot = pack_candidate(payload, directory)
    action = {'type': 'accept_fbd_candidate', 'project_id': context.project_id,
              'base_version_id': context.version_id or None, 'target_mode': 'fbd',
              'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
              '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_fbd_candidate': snapshot}
    return {'requires_confirmation': True, 'model': graph, 'svg': svg,
            **({'definition': authored_definition} if authored_definition is not None else {}),
            **({'library_import': copy.deepcopy(payload['metadata']['library_import'])}
               if 'library_import' in payload['metadata'] else {}),
            'generation_handoff': copy.deepcopy(payload['metadata'].get('generation_handoff')),
            'maintainability_review': copy.deepcopy(payload['metadata'].get('maintainability_review')),
            'verification': {'structural_checks_passed': True,
                             'native_verified': operation=='compile' and payload['metadata']['validation'].get('gui_configuration') == 'matched',
                             'gx_compile': payload['metadata']['validation']['gx_compile'],
                             **({key: payload['metadata']['validation'][key] for key in
                                 ('gui_configuration', 'saved_code', 'public_check', 'cold_reopen')
                                 if key in payload['metadata']['validation']} if operation == 'compile' else {}),
                             **({key: copy.deepcopy(payload['metadata']['validation'][key])
                                 for key in ('library_definitions', 'native_initial_value_changes')}
                                if 'library_import' in payload['metadata'] else {})},
            'message': ('FBD 编译保存候选已完成独立冷读和代码对应检查，等待工程确认。'
                        + ('GUI 自动分配配置尚未完整验证，不能视为可见原生会话通过。'
                           if payload['metadata']['validation'].get('gui_configuration') != 'matched' else ''))
                        if operation=='compile' else 'FBD 候选已完成结构、声明与预览校验，等待工程确认；GX Works2 编译尚未执行。',
            'pending_action': action}


def _read_sfc_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload
    from application.sfc import read_snapshot

    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.native_snapshot
    if raw is None:
        raise ValueError('请选择带 GXW 快照的版本或提供已有 SFC 工程。')
    program = arguments.get('program') or ((context.version or {}).get('program_name') if uploaded is None else None)
    graph = context.sfc_graph if uploaded is None else None
    if arguments.get('native') and read_snapshot(raw, program, native_graph=graph)['model']['graph']['position_resolution'] != 'native-source-bound':
        from application.fbd import _native_workspace
        with _native_workspace() as workspace:
            graph = workspace.read_sfc_graph(raw, program, timeout=None)
    return {'available': True, 'version_id': context.version_id or None,
            **read_snapshot(raw, program, native_graph=graph)}


def _create_sfc_candidate(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from pathlib import Path
    import tempfile
    from application.fbd import decode_upload, pack_candidate
    from application.sfc import prepare_candidate

    operation = arguments['operation']
    if operation == 'import':
        if 'child_edits' in arguments or 'graph_edits' in arguments:
            raise ValueError('SFC 导入不接受子程序编辑。')
        keywords = {'imported': decode_upload(arguments.get('data_base64', ''))}
    elif operation == 'edit':
        if not context.version_id or context.native_snapshot is None or 'data_base64' in arguments:
            raise ValueError('SFC 编辑需要当前版本的 GXW 快照，不接受导入字节。')
        keywords = {'baseline': context.native_snapshot, 'child_edits': arguments.get('child_edits'),
                    'graph_edits': arguments.get('graph_edits'), 'native_graph': context.sfc_graph}
    else:
        raise ValueError('SFC 目前支持已有工程的导入及子程序编辑。')
    with tempfile.TemporaryDirectory(prefix='gxw-sfc-candidate-') as directory:
        payload = prepare_candidate(directory, **keywords,
            program_name=arguments.get('program') or ((context.version or {}).get('program_name') if operation == 'edit' else None),
            summary=arguments.get('summary') or 'Agent 提出的 SFC 子程序候选')
        model = json.loads((Path(directory) / 'sfc.json').read_bytes())
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'model': model,
            'verification': {'structural_checks_passed': True, 'gx_compile': 'not_run',
                             'native_save': payload['metadata']['validation']['native_save']},
            'pending_action': {'type': 'accept_sfc_candidate', 'project_id': context.project_id,
                'base_version_id': context.version_id or None, 'target_mode': 'sfc',
                'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
                '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_sfc_candidate': snapshot}}


def _read_st_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload
    from gxw.fb_authoring import st_program_snapshot
    raw = decode_upload(arguments['data_base64']) if 'data_base64' in arguments else context.native_snapshot
    if raw is None:
        raise ValueError('请选择带 GXW 快照的版本或提供已有 ST 工程。')
    snapshot = st_program_snapshot(raw)
    if 'program' in arguments:
        snapshot['programs'] = [row for row in snapshot['programs'] if row['program'] == arguments['program']]
        if len(snapshot['programs']) != 1:
            raise ValueError('没有唯一匹配的普通 ST Program。')
    return {'available': True, 'version_id': context.version_id or None, **snapshot}


def _create_st_candidate(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    import tempfile
    from application.fbd import _native_candidate, decode_upload, pack_candidate
    from application.native_st import prepare_candidate
    operation = arguments['operation']
    authored = None
    if operation == 'import':
        raw, selected = decode_upload(arguments['data_base64']), arguments['program']
    else:
        from gxw.st_program import background_st_program_plan
        baseline = context.native_snapshot or context.fbd_baseline
        if baseline is None:
            raise ValueError('普通 ST Program 后台创建和编辑需要当前版本的完整 GXW 工程。')
        command = {key: value for key, value in arguments.items() if key != 'summary'}
        background_st_program_plan(baseline, command)
        authored = _native_candidate('author', baseline, command)
        raw = authored.data
        selected = authored.report['definition_after']['program']
    with tempfile.TemporaryDirectory(prefix='gxw-st-candidate-') as directory:
        payload = prepare_candidate(directory, imported=raw, program_name=selected,
            plc_model=context.plc_model, summary=arguments.get('summary') or '普通 ST Program 工程候选')
        if authored is not None:
            payload['metadata']['validation'].update(native_save='passed', definition_readback='matched')
            payload['metadata']['validation']['messages'] = [
                '普通 ST Program 已在隔离工作区写入并核对源码、声明和任务；编译及活动 IDE 编辑另行验证。']
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'verification': payload['metadata']['validation'],
        'pending_action': {'type': 'accept_st_candidate', 'project_id': context.project_id,
            'base_version_id': context.version_id or None, 'target_mode': 'st',
            'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
            '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_st_candidate': snapshot}}


def build_default_tool_registry() -> ToolRegistry:
    from application.generation_receipts import GenerationReceiptCache
    from plc.ir import canonical_sha256

    receipts = GenerationReceiptCache()

    def receipt_binding(context):
        from application.confirmed_generation_context import project_confirmed_specification
        from application.generation_support import public_generation_ladder
        from plc.ir import ir_to_ladder

        # Bind the engineering snapshot that was actually exposed, not arbitrary
        # UI/provider objects retained by a host in its private ToolContext.
        # Private metadata must neither leak nor break this optional observation.
        program = (ir_to_ladder(context.program_ir) if isinstance(context.program_ir, Mapping)
                   else context.ladder)
        return canonical_sha256({
            "project_id": context.project_id, "version_id": context.version_id,
            "plc_model": context.plc_model,
            "target_mode": str(context.project.get("target_mode") or "ladder"),
            "confirmed_spec": project_confirmed_specification(_confirmed_spec(context)),
            "program": public_generation_ladder(program),
        })

    def generation_context(context, arguments):
        result = _get_generation_context(context, arguments)
        if result.get("generation_handoff"):
            result["generation_context_id"] = receipts.remember(
                receipt_binding(context), result["generation_handoff"],
            )
        return result

    def program_candidate(context, arguments):
        receipt_id = arguments.get("generation_context_id")
        handoff = receipts.resolve(receipt_id, receipt_binding(context)) if receipt_id else None
        if handoff is not None:
            handoff["external_context_id"] = receipt_id
        return _create_program_candidate(context, arguments, generation_handoff=handoff)

    def fbd_candidate(context, arguments):
        receipt_id = arguments.get('generation_context_id')
        handoff = receipts.resolve(receipt_id, receipt_binding(context)) if receipt_id else None
        if handoff is not None:
            handoff['external_context_id'] = receipt_id
            handoff['external_model_use'] = 'not_observed'
        return _create_fbd_candidate(context, arguments, generation_handoff=handoff)

    def program_patch(context, arguments):
        receipt_id = arguments.get('generation_context_id')
        handoff = receipts.resolve(receipt_id, receipt_binding(context)) if receipt_id else None
        return _patch_program(context, arguments, generation_handoff=handoff)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "get_current_project",
            "查询 Agent 工程选择状态；未选中时正常返回 selection_status=none 和发现入口，不代表 GX Works2 没有打开工程。",
            _EMPTY_OBJECT_SCHEMA,
            _get_current_project,
        )
    )
    registry.register(ToolDefinition('get_operation_status',
        '查询当前选定工程中真实的提案或执行任务回执。提供 proposal_id 或 execution_job_id 中的一项；返回当前状态、已有执行结果及其保存版本标识，不批准或重试操作。',
        {'type': 'object', 'properties': {
            'proposal_id': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                            'pattern': r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'},
            'execution_job_id': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                                 'pattern': r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'}},
            'oneOf': [{'required': ['proposal_id']}, {'required': ['execution_job_id']}],
            'additionalProperties': False}, _get_operation_status))
    registry.register(ToolDefinition('get_native_engineering_session',
        '读取选定工程的持续原生会话、当前确认修订、未保存状态和真实执行回执；省略 session_id 时查询该工程唯一的活动会话，不操作原生页面。',
        _engineering_object({'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'}}),
        _get_native_engineering_session))
    registry.register(ToolDefinition('pause_native_engineering_session',
        '请求持续原生会话在当前操作完成并核对后暂停；不打断已发出的原生修改。恢复和单步通过既有 execute_native_engineering 的已审批 continue 请求完成。',
        _engineering_object({'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'}}, ['session_id']),
        _pause_native_engineering_session))
    registry.register(
        ToolDefinition(
            "get_generation_context",
            "读取与内置 API 共用的生成提示、当前程序、确认规格、PLC 型号和本地手册上下文；可传本次需求改善检索。无版本时也可用，不调用模型，不返回路径或模型配置。",
            {
                "type": "object",
                "properties": {
                    "user_requirement": {
                        "type": "string", "maxLength": 64000,
                        "description": "本次生成或修改需求；省略时仍返回当前工程上下文。",
                    },
                    "workflow": {"type": "string", "enum": ["direct_generation", "detailed"],
                                 "description": "新 Ladder 有原始需求时默认 Direct；已确认规格可显式选 Direct，编辑仍沿用原入口。"},
                    "execution_mode": {"type": "string", "enum": ["background", "live"],
                        "description": "选择保存版本的后台上下文或持续原生会话当前确认的源码。省略时优先使用已启动的原生会话。"},
                },
                "additionalProperties": False,
            },
            generation_context,
        )
    )
    registry.register(
        ToolDefinition(
            "create_program_candidate",
            "提交完整或局部 ladder JSON，复用 API 的兼容整理、结构检查和临时编译，返回待确认候选；device_comments 只写最多16字符的软元件用途短名称，不写动作过程；不再次调用模型或导入 GX Works2。",
            {
                "type": "object",
                "properties": {
                    "program_name": {
                        "type": "string", "default": "MAIN",
                        "minLength": 1, "maxLength": 64,
                    },
                    "ladder": {
                        "type": "object",
                        "description": "Full or partial ladder JSON from get_generation_context; the shared API parser validates and normalizes compatible encodings.",
                    },
                    "generation_context_id": {
                        "type": "string", "maxLength": 128,
                        "description": "Copy the ID returned by get_generation_context to preserve source lineage. Optional; absence or expiry is recorded as a trace gap, not a validation failure.",
                    },
                },
                "required": ["ladder"],
                "additionalProperties": False,
            },
            program_candidate,
            confirmation_required=True,
        )
    )
    registry.register(
        ToolDefinition(
            "get_current_program_info",
            "读取保存版本或 source_ref 分析快照的程序摘要、Network、覆盖和来源；不读取实时值，不把快照认作可编辑版本。",
            _EMPTY_OBJECT_SCHEMA,
            _get_current_program_info,
        )
    )
    registry.register(
        ToolDefinition(
            "read_network",
            "按稳定 Network ID 读取当前程序中的一个完整网络。",
            {
                "type": "object",
                "properties": {
                    "network_id": {
                        "type": "string",
                        "description": "例如 N0001。",
                        "minLength": 2,
                        "maxLength": 32,
                    }
                },
                "required": ["network_id"],
                "additionalProperties": False,
            },
            _read_network,
        )
    )
    registry.register(
        ToolDefinition(
            "search_plc_manual",
            "检索当前 PLC 型号的技术语义、约束和原创案例；来源与核验记录仅供显式 debug 查询。",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的 PLC 指令、软元件、错误码或问题。",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，1 到 8。",
                        "minimum": 1,
                        "maximum": 8,
                    },
                    "debug": {
                        "type": "boolean", "default": False,
                        "description": "仅显式调试时设为 true，返回来源、证据和核验记录。普通生成与分析保持 false。",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            _search_plc_manual,
        )
    )
    registry.register(
        ToolDefinition(
            "get_diagnostics",
            "执行确定性程序静态检查；不读取 RUN 或实时软元件。运行问题需另调用 get_simulator_status/read_simulator_devices。",
            _EMPTY_OBJECT_SCHEMA,
            _get_diagnostics,
        )
    )
    registry.register(
        ToolDefinition(
            "validate_project",
            "默认校验当前程序；native=true 在隔离工作区检查当前版本已绑定的 GXW 快照，分别返回编译、公开检查、底层重检、诊断和当前源码对应状态。仅有源码或 CSV 不能启动原生检查；简单 Ladder 必须有明确原生 CPU，并完整回读源码词法、与当前候选的指令逐项匹配，覆盖外保留为未验证。program 仅标识请求的 POU 上下文，实际检查范围以 selection 和 targets 为准。不会操作 GX 界面、PLC 或写入项目版本。",
            {"type": "object", "properties": {
                "native": {"type": "boolean", "description": "显式启动已保存 GXW 的离线原生检查；默认 false。"},
                "program": {"type": "string", "minLength": 1, "maxLength": 256,
                            "description": "native=true 时的请求 POU；默认使用版本所选 Program。"},
            }, "additionalProperties": False},
            _validate_project,
        )
    )
    registry.register(
        ToolDefinition(
            "compile_project",
            "从当前 PLC IR 临时生成并校验 JSON、SVG、ST、程序 CSV 和注释 CSV；不会写入项目版本或操作 GX Works2。",
            _EMPTY_OBJECT_SCHEMA,
            _compile_project,
        )
    )
    registry.register(
        ToolDefinition(
            "patch_program",
            "在当前 IR 副本上增删改 Network，完成校验与临时编译后生成待用户确认的候选补丁。不会直接保存或同步 GX Works2。",
            {
                "type": "object",
                "properties": {
                    "generation_context_id": {
                        "type": "string", "maxLength": 128,
                        "description": "Optional context receipt from get_generation_context; preserves edit discovery evidence without adding a model call.",
                    },
                    "patch": {
                        "type": "object",
                        "description": "基于当前 revision/sha256 的 Network 级候选补丁。",
                        "properties": {
                            "base_revision": {"type": "integer"},
                            "base_ir_sha256": {"type": "string"},
                            "target_revision": {"type": "integer"},
                            "operations": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "operation": {
                                            "type": "string",
                                            "enum": [
                                                "add_network",
                                                "modify_network",
                                                "replace_network",
                                                "delete_network",
                                            ],
                                        },
                                        "network": {"type": "string"},
                                        "after": {"type": "string"},
                                        "ladder": {
                                            "type": "object",
                                            "description": "新增或替换 Network 的完整 ladder rung 对象。",
                                        },
                                    },
                                    "required": ["operation", "network"],
                                },
                            },
                            "device_comments": {
                                "type": "object",
                                "description": "软元件地址到注释；null 表示删除注释。",
                            },
                        },
                        "required": ["operations"],
                    }
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
            program_patch,
            confirmation_required=True,
        )
    )
    registry.register(
        ToolDefinition(
            "validate_current_program",
            "对当前所选梯形图运行确定性的本地结构校验与评审规则，不调用 AI 修改程序。",
            _EMPTY_OBJECT_SCHEMA,
            _validate_current_program,
        )
    )
    registry.register(
        ToolDefinition(
            "import_current_program_to_gxworks2",
            "请求把当前所选梯形图版本导入已打开的 GX Works2 工程。该工具只生成确认请求；用户明确确认后才会由既有高层导入服务执行。",
            _EMPTY_OBJECT_SCHEMA,
            _request_gxworks2_import,
            confirmation_required=True,
        )
    )
    registry.register(ToolDefinition('execute_native_engineering',
        '请求在独立 GX Works2 进程和托管工程副本中执行显式工程方案。持续会话接受原生 FX/Q/L 可编程 CPU 菜单，实际操作按具体 CPU、软件版本和中英日界面的已验证能力执行；仅参数型号不能执行程序操作。后台批次仍限已验证 FX3U。标签、ST FB、普通 ST Program、FBD 调用与改接、分配、编译、当前地址、保存和重开共用现有审批；新 Program 需明确已有资源和任务。图形动作使用当前源码节点和连线标识，连接显式给出端口及正交路径。不接受路径、PID 或原生驱动参数。',
        _engineering_native_schema(),
        _request_native_engineering, confirmation_required=True))
    registry.register(ToolDefinition('get_fbd_catalog',
        '读取当前 CPU 的空白 FBD 模板、工程源码引脚和声明表；启用 56 个 Q/L/FX 原生菜单，当前 FBD 复现流程不支持 FX1，按合并菜单身份选择模板。',
        {'type': 'object', 'properties': {'program': {'type': 'string'}}, 'additionalProperties': False},
        _get_fbd_catalog))
    registry.register(ToolDefinition('read_fbd_project',
        '读取当前已保存 GXW 或上传 GXW 的 FBD 图形、标签、具名引脚和 SVG；不运行 GX Works2。',
        {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}},
         'additionalProperties': False}, _read_fbd_project))
    registry.register(ToolDefinition('read_fbd_libraries',
        '只读列出当前或上传 GXW 的用户库、FB/FUNCTION 定义、IN_OUT 两侧形参、结构和库全局表；include_source=true 返回已支持的 ST 实现。不会导入来源工程。',
        {'type': 'object', 'properties': {'data_base64': {'type': 'string'},
         'library_name': {'type': 'string', 'minLength': 1, 'maxLength': 255}, 'include_source': {'type': 'boolean'}},
         'additionalProperties': False}, _read_fbd_libraries))
    registry.register(ToolDefinition('read_fbd_definition',
        '读取当前版本自主创建的项目结构和 FB 定义；返回声明、具名接口及 ST 正文或 FBD 图形。source_name 省略时列出全部项目定义。',
        {'type': 'object', 'properties': {'source_name': {'type': 'string', 'minLength': 1, 'maxLength': 255}},
         'additionalProperties': False}, _read_fbd_definition))
    registry.register(ToolDefinition('create_fbd_candidate',
        '在当前工程副本上编写 FBD。generate/edit 的 model 可只填 networks：calls 用 id/type/instance/arguments，组合逻辑用 nodes 和 from/to 端口连接。Core 从当前定义生成接口、IN_OUT、分块和布线，不需要坐标；参数 schema 见 get_fbd_catalog.parameter_schema。原有 v2 对象编辑仍支持。create_structure 用 source_name/members 新建类型；create_st_fb 用 source_name/declarations/st_text 编写完整 ST FB；edit_st_fb 省略 declarations 时保留接口。create_global_labels/edit_global_labels 用表名和 declarations 新建或更新全局表，结构设备映射用 member_devices；edit_pou_labels 用 POU 名和 declarations 更新局部表。configure_project 用目录 project_configuration 的 source_name 和 options 修改 system_variable_ranges，program_capacity 当前仅支持已验证 FX3U/FX3UC 的 16000/64000。create_fb 新建空 FBD FB 后用 edit 编写图形。定义和配置操作需基础版本。import_library 单独导入现有库；create_program 需资源和任务。compile 在当前基础版本上原生编译、完整检查、保存并冷重开比较三个代码通道，不接受源码修改参数；运行验证另行执行。',
        _engineering_fbd_schema(),
        fbd_candidate, confirmation_required=True))
    registry.register(ToolDefinition('read_st_project',
        '读取保存 GXW 中普通 ST Program 的正文、局部声明、资源及任务成员；program 使用返回的逻辑 POU 名称。只读，不根据正文生成工程。',
        {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}},
         'additionalProperties': False}, _read_st_project))
    registry.register(ToolDefinition('create_st_candidate',
        '在当前保存 GXW 的隔离副本中创建或编辑普通 ST Program，或 import 已保存工程。create_st_program 使用 source_name/declarations/st_text，并显式指定已有 resource/task/scope_mode=existing_task；edit_st_program 用 source_name/st_text，可选 declarations。import 使用 read_st_project 返回的逻辑 program 和 data_base64。写入复用原生后台适配器及 Core 回读，不修改活动 IDE；候选经现有事务保存版本，编译和运行验证分别执行。',
        _engineering_st_schema(),
        _create_st_candidate, confirmation_required=True))
    registry.register(ToolDefinition('read_sfc_project',
        '读取当前 GXW 或上传工程中 SFC 的 CPU、声明、图形引用和子程序。native=true 在隔离工作区只读取得原生格点；已有对应证据时复用，不推断执行语义。',
        {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}, 'native': {'type': 'boolean'}},
         'additionalProperties': False}, _read_sfc_project))
    registry.register(ToolDefinition('create_sfc_candidate',
        '导入已有 SFC 工程，按 kind/name 替换子程序 records_hex；FX3U/FX3UC 可修改独立动作引用或跳转目标，已实测 Q/L 可用 kind=rebuild 和完整 symbols 重建现有图形，或在当前原生格点上用 kind=swap_steps 交换两个普通步骤。图形修改调用原生转换器，当前代码编译与检查另行执行；不新建 SFC。',
        {'type': 'object', 'properties': {'operation': {'type': 'string', 'enum': ['import', 'edit']},
         'data_base64': {'type': 'string'}, 'program': {'type': 'string'}, 'summary': {'type': 'string'},
         'child_edits': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': {'type': 'object',
             'properties': {'kind': {'type': 'string', 'enum': ['zoom', 'transition']},
                            'name': {'type': 'string', 'minLength': 1, 'maxLength': 255},
                            'records_hex': {'type': 'string', 'maxLength': 16 * 1024 * 1024}},
             'required': ['kind', 'name', 'records_hex'], 'additionalProperties': False}},
         'graph_edits': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': {'oneOf': [{'type': 'object',
             'properties': {'step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                            'action_number': {'type': 'integer', 'minimum': 0, 'maximum': 65535}},
             'required': ['step_reference', 'action_number'], 'additionalProperties': False},
             {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['jump']},
                 'x': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                 'y': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                 'before_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                 'target_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535}},
              'required': ['kind', 'x', 'y', 'before_step_reference', 'target_step_reference'],
              'additionalProperties': False},
             {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['rebuild']},
                 'symbols': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': {'type': 'object',
                     'properties': {'kind': {'type': 'string', 'enum': ['initial_step', 'step', 'transition', 'end', 'line',
                         'parallel_branch', 'parallel_coupling', 'selective_branch', 'selective_coupling']},
                         **{key: {'type': 'integer', 'minimum': 0, 'maximum': 32767}
                            for key in ('x', 'y', 'number', 'start', 'end')}},
                     'required': ['kind'], 'additionalProperties': False}}},
              'required': ['kind', 'symbols'], 'additionalProperties': False},
             {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['swap_steps']},
                 'left_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 32767},
                 'right_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 32767}},
              'required': ['kind', 'left_step_reference', 'right_step_reference'], 'additionalProperties': False}]}}},
         'required': ['operation'], 'additionalProperties': False}, _create_sfc_candidate, confirmation_required=True))
    from agent_runtime.inspection_tools import register_inspection_tools
    register_inspection_tools(registry)
    if tuple(registry.names) != SAFE_TOOL_NAMES:
        raise RuntimeError("default PLC tool registry does not match its allow-list")
    return registry


__all__ = [
    "FORBIDDEN_TOOL_NAMES",
    "READ_ONLY_TOOL_NAMES",
    "SAFE_TOOL_NAMES",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "build_default_tool_registry",
    "build_tool_context",
]
