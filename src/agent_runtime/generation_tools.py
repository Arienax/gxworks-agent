"""Confirmed generation context and candidate preparation tools."""
from __future__ import annotations
from shared.privacy import credential_field, filesystem_field
import copy
import json
from application.generation_wire import expand_context_document
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_context import (
    ToolContext,
    _decision_receipt_id,
    _confirmed_spec,
)
from agent_runtime.native_tools import (
    _get_fbd_catalog,
    _read_fbd_project,
    _read_sfc_project,
)


def _source_knowledge(context, arguments, source, language, confirmed_spec):
    from application.generation_context import _build_knowledge_context
    from application.generation_support import _build_knowledge_query
    from knowledge.evidence import KnowledgeQuery
    from shared.model_context import technical_context_units, technical_knowledge_coverage
    query = _build_knowledge_query(arguments.get('user_requirement'), source)
    knowledge = _build_knowledge_context(KnowledgeQuery(query, precompiled=True,
        metadata={'target_mode': language, 'capability_requirement': str(query)}),
        plc_model=context.plc_model, task_type='edit', confirmed_context=confirmed_spec)
    return {'knowledge': technical_context_units(knowledge),
            'knowledge_coverage': technical_knowledge_coverage(knowledge)}


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
                'generation_request': str(arguments.get('user_requirement') or ''),
                'generation_instructions': 'Edit only the requested existing SFC graph or child source through '
                    'the returned capabilities. Keep other children, registrations and task bindings.',
                'output_contract': {'tool': 'create_sfc_candidate', 'operation': 'edit'},
                'limitations': ['Existing SFC POU only; internal step/action/condition records may change jointly; no new engineering POU or task.',
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
        if profile is None or profile != native_project_profile(snapshot['cpu']):
            raise ValueError('Saved ST generation context CPU differs from its selected PLC model')
        selected = [row for row in snapshot['pous'] if row['program'] == selected_name]
        if (len(selected) == 1 and selected[0]['kind'] in {'function_block', 'function'}
                and selected[0]['language'] == 'st' and selected[0]['source_status'] == 'available'):
            from application.native_st import model_project_context
            operation = 'edit_st_function' if selected[0]['kind'] == 'function' else 'edit_st_fb'
            return {'project_id': context.project_id, 'plc_model': context.plc_model,
                'target_mode': 'st', 'execution_mode': 'offline', 'current_version_id': context.version_id,
                'current_context': model_project_context(snapshot), 'selected_program': selected[0]['name'],
                **_source_knowledge(context, arguments, selected[0]['st_text'], 'st', confirmed_spec),
                'generation_request': str(arguments.get('user_requirement') or ''),
                'generation_instructions': 'Edit the selected existing ST callable with operation ' + operation + '. '
                    'Provide its complete st_text body. Omitted declarations retain the current table; '
                    'if changed, declarations is the complete ordered list. Keep callers and task registration. '
                    'Unsafe interface changes are rejected by the shared source checks.',
                'output_contract': {'tool': 'create_st_candidate' if selected[0]['kind'] == 'function' else 'create_fbd_candidate', 'operation': operation,
                    'source_name': selected_name, 'editable_fields': ['st_text', 'declarations']}}
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
                    and not str(key).lower().endswith(('_offset', '_handle', '_pointer')) and not filesystem_field(key)
                    and 'base64' not in str(key).lower()
                    and not credential_field(key, fragments=True, any_token=True)
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
            'confirmed_spec': public_spec, 'current_context': current,
            'selected_program_ref': '#/current_context/pous/' + str(current['pous'].index(program)),
            **_source_knowledge(context, arguments, program.get('st_text'), 'st', confirmed_spec),
            'generation_request': public_generation_value(str(arguments.get('user_requirement') or '')),
            'generation_instructions': 'Edit the selected ordinary ST Program in the current saved project. '
                'Keep its existing declarations, task binding and other project sources unless explicitly requested. '
                'Return create_st_candidate arguments using operation edit_st_program and the selected logical source_name. '
                'st_text replaces the complete body; omitted declarations retain the current declarations. '
                'When changing declarations, provide the complete current list with the requested changes; '
                'do not replace this project with a template.',
            'output_contract': {'tool': 'create_st_candidate', 'operation': 'edit_st_program',
                'source_name': selected_name, 'input_shape': 'arguments',
                'required_fields': ['operation', 'source_name'],
                'editable_fields': ['st_text', 'declarations'],
                'requires_any': ['st_text', 'declarations'],
                'declarations': 'complete list; omission preserves current rows'},
            'limitations': ['This context is read from the selected saved GXW version, not the unsaved active IDE.',
                'The candidate uses the shared proposal flow and offline source writer.',
                'Native compilation, current device allocation and PLC execution remain separate results.']}
    if target_mode == 'fbd':
        from application.fbd import build_fbd_generation_context
        scope = str(arguments.get('catalog_scope') or 'task')
        # The full table is only the selection *input* here: the shared builder
        # narrows it for this request, so the returned nodes stay task-scoped.
        catalog = _get_fbd_catalog(context, {'catalog_scope': 'full',
                                            'templates': arguments.get('templates'),
                                            'program': arguments.get('program')})
        from application.fbd_context import has_graph
        instances = _read_fbd_project(context, {})['model'] if context.fbd_baseline is not None else None
        previous = catalog.get('parameter_model')
        if not (has_graph(previous) or has_graph(instances)):
            # An empty saved graph preserves nothing, so it is generated as a new graph
            # instead of pretending to be an edit with a dependency closure.
            previous, instances = None, None
        from gxw.offline_project import read_offline_project
        comments, tables, _ = (read_offline_project(context.fbd_baseline).comments()
                               if context.fbd_baseline is not None else ({}, [], []))
        generation = build_fbd_generation_context(str(arguments.get('user_requirement') or ''),
            plc_model=context.plc_model, confirmed_spec=confirmed_spec, catalog=catalog, previous=previous,
            instances_model=instances,
            catalog_scope=scope, templates=arguments.get('templates'),
            device_comments=comments, comment_tables=tables,
            source_baseline=context.fbd_baseline)
        graph_contract = {
            'tool': 'create_fbd_candidate', 'schema_version': 2,
            'input_shape': 'parameters', 'model_schema_ref': '#/generation_input/parameter_schema',
            'operation': 'edit' if previous is not None else 'generate',
        }
        joint = generation['generation_input']['supported_paths']['joint_multi_source_transaction']
        output_contract = graph_contract
        if context.version_id and joint['supported']:
            output_contract = {
                'tool': 'edit_gxw_project', 'input_shape': 'arguments',
                'version_id': context.version_id, 'program': catalog['program'],
                'required_fields': ['version_id'],
                'requires_any': ['edits', 'declaration_edits', 'device_comments'],
                'editable_sources': joint['editable_sources'], 'save_policy': 'draft',
                'alternatives': [graph_contract],
                'instructions': 'Submit ST bodies, interfaces, declarations and all affected FBD callers '
                    'together using the existing language-specific commands in edits. '
                    'For a single graph without dependent source edits, the alternative contract is also available. '
                    'Use each tool\'s tools/list schema; generation_context_id belongs only to tools that accept it.',
            }
        data = {'project_id': context.project_id, 'plc_model': context.plc_model, 'cpu': catalog['cpu'],
                'target_mode': 'fbd',
                'workflow_mode': str(context.project.get('workflow_mode') or 'generate'),
                'has_confirmed_spec': isinstance(confirmed_spec, Mapping), 'confirmed_spec': public_spec,
                'current_version_id': context.version_id or None,
                'generation_instructions': generation['generation_instructions'],
                'generation_handoff': generation['generation_handoff'],
                'generation_request': str(arguments.get('user_requirement') or ''),
                'generation_input': generation['generation_input'],
                'output_contract': output_contract,
                'catalog_ref': '#/generation_input/catalog',
                'current_model_ref': '#/generation_input/previous',
                'catalog_scope': scope,
                'missing_templates': (generation['generation_handoff'].get('catalog_report') or {}).get('missing_templates') or [],
                }
        return data
    source = context.ladder
    if isinstance(context.program_ir, Mapping):
        from plc.ir import ir_to_ladder
        source = ir_to_ladder(context.program_ir)
    from plc.ordered_source import is_ordered_ladder
    if is_ordered_ladder(source) and context.native_snapshot is not None:
        from gxw.offline_project import read_offline_project, read_ordered_program
        from plc.ir import canonical_sha256
        project = read_offline_project(context.native_snapshot)
        current = read_ordered_program(project,
            context.program_ir['program_name'], revision=context.program_ir['revision'])
        return {'project_id': context.project_id, 'plc_model': context.plc_model,
            'target_mode': 'ladder', 'execution_mode': 'offline',
            'current_version_id': context.version_id, 'confirmed_spec': public_spec,
            **_source_knowledge(context, arguments, current['raw_networks'], 'ladder', confirmed_spec),
            'current_model': {'program': context.program_ir['program_name'],
                'networks': current['raw_networks'], 'device_comments': current['device_comments'],
                'comment_tables': current['comment_tables'], 'coverage': current['coverage'],
                'declaration_tables': project.declaration_tables()},
            'generation_request': public_generation_value(str(arguments.get('user_requirement') or '')),
            'generation_instructions': 'Edit the existing GXW with patch_program. The source instruction order is '
                'authoritative, including outputs before contacts or MPS/MRD/MPP. Do not reconstruct graphical rungs. '
                'instruction_edits uses original record_index and exact expected {op,args}; each index is edited once. '
                'replace and insert_before require instructions [{op,args}]; delete has no instructions. '
                'P/I source labels use op="P0" or "I0" and args=[]; bind an existing label using its text as expected.op. '
                'Preserve all unrelated instructions and comments. Do not edit END; insert before it if needed. '
                'device_comments maps device addresses to complete comment text, null deletes an existing row. '
                'declaration_edits maps existing logical table names to upserts, renames and remove; '
                'source and declaration changes are checked and saved together. Do not create a declaration table. '
                'Use only the requested edits. Compilation and execution remain separate checks.',
            'output_contract': {'tool': 'patch_program', 'input_shape': 'arguments',
                'base_revision': context.program_ir['revision'],
                'base_ir_sha256': canonical_sha256(context.program_ir),
                'editable_fields': ['instruction_edits', 'device_comments', 'comment_table', 'declaration_edits'],
                'requires_any': ['instruction_edits', 'device_comments', 'declaration_edits']},
            'limitations': ['Unchanged instruction records retain original bytes.',
                'New instruction forms require established encodings for this CPU.',
                'Source checks, compilation and PLC execution are separate results.']}
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
            knowledge_builder=_build_knowledge_context, model_profile={},
            wire_renderer=_compact_wire_renderer(context.plc_model, direct=True))
        generation_input = expand_context_document(json.loads(direct.wire_packet['messages'][1]['content']))
        return {"project_id": context.project_id, "plc_model": context.plc_model, "target_mode": target_mode,
                "workflow_mode": "direct_generation", "has_confirmed_spec": bool(confirmed_spec),
                "confirmed_spec_ref": '#/generation_input/confirmed_spec',
                "generation_input": generation_input,
                "current_version_id": None, "generation_request": raw,
                "generation_instructions": direct.wire_packet["messages"][0]["content"],
                "generation_handoff": direct.handoff,
                "output_contract": {"tool": "create_program_candidate", "protocol": PROTOCOL_VERSION,
                                    "schema_ref": '#/generation_input/output_schema', "needs_input": "向用户提出必要问题，解决前不得创建候选。"}}
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
        model_profile={},
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
        "confirmed_spec_ref": '#/generation_input/confirmed_spec',
        "generation_input": (expand_context_document(json.loads(instructions.wire_packet['messages'][1]['content']))
                             if hasattr(instructions, 'wire_packet') else {}),
        "output_contract": {**{key: value for key, value in generation_output_contract(
            allow_partial=is_edit_mode, plc_model=context.plc_model, target_mode=target_mode).items() if key != 'schema'},
            'schema_ref': '#/generation_input/output_schema'},
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


class GenerationToolHandlers:
    """One registry's discovery receipts and explicitly bound candidate builders."""

    def __init__(self, context_builder, candidate_builder, fbd_builder, patch_builder):
        from application.generation_receipts import GenerationReceiptCache
        self.receipts = GenerationReceiptCache()
        self.context_builder = context_builder
        self.candidate_builder = candidate_builder
        self.fbd_builder = fbd_builder
        self.patch_builder = patch_builder

    @staticmethod
    def _binding(context):
        from application.confirmed_generation_context import project_confirmed_specification
        from application.generation_support import public_generation_ladder
        from plc.ir import canonical_sha256, ir_to_ladder
        program = ir_to_ladder(context.program_ir) if isinstance(context.program_ir, Mapping) else context.ladder
        return canonical_sha256({
            "project_id": context.project_id, "version_id": context.version_id,
            "plc_model": context.plc_model,
            "target_mode": str(context.project.get("target_mode") or "ladder"),
            "confirmed_spec": project_confirmed_specification(_confirmed_spec(context)),
            "program": public_generation_ladder(program),
        })

    def context(self, context, arguments):
        result = self.context_builder(context, arguments)
        if result.get("generation_handoff"):
            result["generation_context_id"] = self.receipts.remember(
                self._binding(context), result["generation_handoff"])
        return result

    def _handoff(self, context, arguments):
        receipt_id = arguments.get("generation_context_id")
        handoff = self.receipts.resolve(receipt_id, self._binding(context)) if receipt_id else None
        if handoff is not None:
            handoff["external_context_id"] = receipt_id
        return handoff

    def candidate(self, context, arguments):
        return self.candidate_builder(context, arguments, generation_handoff=self._handoff(context, arguments))

    def fbd(self, context, arguments):
        handoff = self._handoff(context, arguments)
        if handoff is not None:
            handoff["external_model_use"] = "not_observed"
        return self.fbd_builder(context, arguments, generation_handoff=handoff)

    def patch(self, context, arguments):
        return self.patch_builder(context, arguments, generation_handoff=self._handoff(context, arguments))
