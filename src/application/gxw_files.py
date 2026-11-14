# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Shared GXW file import and offline-source candidates for Web and MCP."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from gxw.models import GXWFormatError
from gxw.offline_project import read_offline_project, inspect_offline_project, read_ordered_program
from .fbd import decode_upload, json_bytes, pack_candidate


def read_file_input(*, data_base64=None, path=None):
    if (data_base64 is None) == (path is None):
        raise ValueError('Provide exactly one GXW data_base64 or local path')
    if data_base64 is not None:
        return decode_upload(data_base64)
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != '.gxw' or not source.is_file() or not 0 < source.stat().st_size <= 30 * 1024 * 1024:
        raise ValueError('Select an existing GXW file containing 1 byte to 30 MiB')
    raw = source.read_bytes()
    if len(raw) > 30 * 1024 * 1024:
        raise ValueError('GXW file exceeds 30 MiB')
    return raw


def read_source(raw, program=None):
    """Read an explicitly selected owner without changing the saved version."""
    project = read_offline_project(raw)
    selected = project.select(program)
    language, logical = selected['language'], selected['source_name']
    source_records = None
    if language == 'ladder':
        ordered = read_ordered_program(project, logical)
        model, source_records = ordered['program_ir'], ordered['source_records']
        from plc.ir import ir_to_ladder
        from rendering.ladder_svg import AdvancedSVGLadder
        svg = AdvancedSVGLadder().generate_ladder(json.dumps(ir_to_ladder(model), ensure_ascii=False))
    elif language == 'st':
        from gxw.text_pou import parse_st_pou
        model = {'program': logical, 'st_text': parse_st_pou(project.streams[logical]).text}
        svg = None
    elif language == 'fbd':
        from gxw.object_model import read_project_context
        context = read_project_context(raw, logical)
        model, svg = context.object_model(), context.svg()
    elif language == 'sfc':
        from .sfc import read_snapshot
        model = read_snapshot(raw, logical)['model']
        svg = None
    else:
        raise GXWFormatError('selected source has no editing representation')
    from gxw.project_metadata import existing_source_groups
    groups, _ = existing_source_groups(project.outer['projectdatalist.xml'], project.streams)
    tables = project.declaration_tables()
    result = {'source_name': logical, 'language': language, 'unit_kind': selected['unit_kind'],
            'model': model, 'svg': svg, 'programs': project.sources(),
            'declaration_tables': [row for row in tables if row['source_name'] == groups[logical]['labels']
                                   or row.get('scope') == 'global'],
            'capabilities': selected['capabilities'], 'write_backend': 'offline', 'native_required': False}
    if source_records is not None:
        result['source_records'] = source_records
    return result


def source_context(context, program):
    from dataclasses import replace
    view = read_source(context.native_snapshot, program)
    mode, model = view['language'], view['model']
    version = {**dict(context.version or {}), 'target_mode': mode, 'program_name': view['source_name']}
    ir = model if mode == 'ladder' else None
    if ir is not None:
        from plc.ir import ir_to_ladder
        ladder = ir_to_ladder(ir)
    else:
        ladder = None
    return replace(context, version=version, program_ir=ir, ladder=ladder,
                   fbd_baseline=context.native_snapshot if mode == 'fbd' else None,
                   st_text=model['st_text'] if mode == 'st' else None, sfc_graph=None)


def read_source_tool(context, arguments):
    if not context.version_id or context.native_snapshot is None:
        raise GXWFormatError('select a saved GXW version before reading an editable source')
    return {'project_id': context.project_id, 'version_id': context.version_id,
            **read_source(context.native_snapshot, arguments.get('program') or
                          (context.version or {}).get('program_name'))}


def project_source_changes(before, after):
    """Compare every changed owner using the same language projections as editing."""
    from difflib import unified_diff
    from gxw.source_header import source_payload_offset
    if before is None:
        return []
    original, current = read_offline_project(before), read_offline_project(after)
    changes = []
    for source in current.sources():
        name, language = source['source_name'], source['language']
        previous, saved = original.streams.get(name), current.streams[name]
        if previous is None or previous[source_payload_offset(previous):] == saved[source_payload_offset(saved):]:
            continue
        if language == 'st':
            from gxw.text_pou import parse_st_pou
            left, right = parse_st_pou(previous).text, parse_st_pou(saved).text
            lines = unified_diff(left.splitlines(True), right.splitlines(True),
                                 fromfile='base/' + name, tofile='candidate/' + name)
            difference = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                                 for line in lines)
        elif language == 'ladder':
            def records(project):
                return [{key: value for key, value in row.items()
                         if key not in {'record_index', 'source_offset', 'step', 'operand_details'}}
                        for row in read_ordered_program(project, name)['source_records']]
            text = lambda value: json.dumps(value, ensure_ascii=False, indent=2).splitlines(True)
            difference = ''.join(unified_diff(text(records(original)), text(records(current)),
                fromfile='base/' + name, tofile='candidate/' + name))
        elif language == 'fbd':
            from gxw.object_model import read_project_context
            from .fbd import graph_diff
            difference = graph_diff(read_project_context(before, name).object_model(),
                                    read_project_context(after, name).object_model())['unified_diff']
        elif language == 'sfc':
            from .sfc import read_snapshot, source_diff
            difference = source_diff(read_snapshot(before, name)['model'],
                                     read_snapshot(after, name)['model'])['unified_diff']
        else:
            raise GXWFormatError('changed source has no review projection: ' + name)
        changes.append({key: source[key] for key in ('source_name', 'language', 'unit_kind')} |
                       {'unified_diff': difference})
    return changes


def validate_ladder_candidate(artifacts):
    from plc.ir import validate_plc_ir, canonical_sha256
    if not {'gxw', 'ir', 'json', 'svg', 'write_report'} <= artifacts.keys():
        raise ValueError('GXW Ladder candidate is missing its bound source artifacts')
    program = json.loads(artifacts['ir'])
    validate_plc_ir(program, validate_ladder=False)
    current = read_ordered_program(read_offline_project(artifacts['gxw']), program['program_name'],
                                   revision=program['revision'])
    if current['program_ir'] != program:
        raise ValueError('GXW instructions/comments differ from the candidate IR')
    from plc.ir import ir_to_ladder
    if json.loads(artifacts['json']) != ir_to_ladder(program):
        raise ValueError('GXW source document differs from the candidate IR')
    return current


def prepare_candidate(output, *, raw, program=None, summary='导入 GXW 工程', revision=1, write_report=None):
    """Freeze the original whole project and a selected source projection."""
    if not isinstance(raw, (bytes, bytearray)) or not 0 < len(raw) <= 30 * 1024 * 1024:
        raise GXWFormatError('GXW source candidate must contain 1 byte to 30 MiB')
    project = read_offline_project(raw)
    selected = project.select(program)
    mode, logical = selected['language'], selected['source_name']
    if mode == 'fbd':
        from .fbd import prepare_candidate as prepare_fbd
        payload = prepare_fbd(output, imported=raw, program_name=logical,
                           plc_model=project.context['cpu'], summary=summary)
    elif mode == 'st':
        from .native_st import prepare_candidate as prepare_st
        payload = prepare_st(output, imported=raw, program_name=logical,
                             plc_model=project.context['cpu'], summary=summary)
    elif mode == 'sfc':
        from .sfc import prepare_candidate as prepare_sfc
        payload = prepare_sfc(output, imported=raw, program_name=logical, summary=summary)
    elif mode == 'ladder':
        from plc.ir import canonical_sha256, ir_to_ladder
        from plc.core import PLCCore
        from rendering.ladder_svg import AdvancedSVGLadder
        current = read_ordered_program(project, logical, revision=revision)
        ir = current['program_ir']
        ladder = ir_to_ladder(ir)
        report = {'schema_version': 1, 'operation': 'gxw_file_import', 'write_backend': 'offline',
            'source_records': current['source_records'], 'coverage': current['coverage'],
            'validation': {'native_compile': 'not_run', 'runtime': 'not_run'}}
        if write_report is not None:
            report.update(copy.deepcopy(write_report))
        validation = dict(PLCCore().validate_project(ir))
        validation.update(offline_write='passed' if write_report is not None else 'source_preserved',
            write_backend='offline', source_format=ir['source']['format'], st_translation='unavailable',
            source_readback='matched', gx_compile='not_run', native_save='not_run',
            compile_state='stale' if write_report is not None else 'unverified',
            messages=['GXW 源码、注释与有序指令已回读；原生编译及运行分别验证。'])
        if write_report is not None:
            validation['source_checks'] = copy.deepcopy(write_report.get('source_checks', []))
            validation['save_policy'] = write_report.get('save_policy', 'checked')
            validation['artifact_state'] = write_report.get('artifact_state', 'source_checked')
            if write_report.get('invalidated_results'):
                validation['invalidated_results'] = copy.deepcopy(write_report['invalidated_results'])
        files = {'gxw': ('program.gxw', bytes(raw)), 'ir': ('program.ir.json', json_bytes(ir)),
            'json': ('ladder.json', json_bytes(ladder)), 'write_report': ('write_report.json', json_bytes(report)),
            'svg': ('ladder.svg', AdvancedSVGLadder().generate_ladder(json.dumps(ladder, ensure_ascii=False)).encode('utf-8'))}
        validate_ladder_candidate({key: data for key, (_name, data) in files.items()})
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for key, (name, data) in files.items():
            (output / name).write_bytes(data)
            artifacts[key] = {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
        from .session import ProjectSession
        metadata = ProjectSession._ir_metadata(ir)
        metadata.update(target_mode='ladder', plc_model=current['plc_model'], program_name=logical,
                        summary=summary, validation=validation, program_size=current['program_size'])
        return {'target_mode': 'ladder', 'plc_model': current['plc_model'], 'staging_dir': str(output),
                'artifacts': artifacts, 'metadata': metadata}
    else:
        raise GXWFormatError('selected GXW source has no supported editing representation')
    if write_report is not None:
        (Path(output) / 'offline_write_report.json').write_bytes(json_bytes(write_report))
        payload['artifacts']['offline_write_report'] = {'path': 'offline_write_report.json',
            'sha256': hashlib.sha256(json_bytes(write_report)).hexdigest()}
        payload['metadata']['validation'].update(offline_write='passed', compile_state='stale',
            native_save='not_run', native_source_save_required=False, write_backend='offline')
        if write_report.get('invalidated_results'):
            payload['metadata']['validation']['invalidated_results'] = copy.deepcopy(write_report['invalidated_results'])
        payload['metadata']['validation'].update(
            save_policy=write_report.get('save_policy', 'checked'),
            artifact_state=write_report.get('artifact_state', 'source_checked'),
            source_checks=copy.deepcopy(write_report.get('source_checks', [])))
    return payload


def _check_ladder_edit(payload, directory, context, spec):
    from application.spec_document import project_acceptance_cases, project_behavior_cpu
    from plc.ir import ir_to_ladder
    from simulator.io_example import check_independent_acceptance

    ir = json.loads((Path(directory) / payload['artifacts']['ir']['path']).read_bytes())
    behavior = check_independent_acceptance(ir_to_ladder(ir), project_behavior_cpu(context.project, context.plc_model),
        project_id=context.project_id, confirmed_spec=spec,
        loaded_cases=project_acceptance_cases(context.project, context.plc_model))
    payload['metadata']['behavior_result'] = behavior
    if behavior['status'] == 'mismatch':
        payload['metadata']['activation_blocked'] = True
    return ir


def transaction_candidate(context, arguments):
    """Shared Web/MCP proposal from one version and several existing sources."""
    from gxw.offline_edit import edit_offline_project
    if not context.version_id or arguments.get('version_id', context.version_id) != context.version_id:
        raise GXWFormatError('offline edit requires its explicit current base version')
    raw = context.native_snapshot if context.native_snapshot is not None else context.fbd_baseline
    if raw is None:
        raise GXWFormatError('offline edit requires a saved GXW artifact')
    result = edit_offline_project(raw, arguments.get('edits', []),
        declaration_edits=arguments.get('declaration_edits'),
        device_comments=arguments.get('device_comments'), comment_table=arguments.get('comment_table'),
        save_policy=arguments.get('save_policy', 'draft'))
    program = arguments.get('program') or (context.version or {}).get('program_name')
    from agent_runtime.tool_context import _generation_spec
    spec = _generation_spec(context)
    validation = {}
    if any(row.get('language') == 'fbd' for row in (spec or {}).get('implementation_items', [])):
        from application.fbd import attach_plan_check
        from gxw.object_model import read_project_context
        sources = read_offline_project(result.data).sources()
        models = {row['source_name']: read_project_context(result.data, row['source_name']).object_model()
                  for row in sources if row.get('language') == 'fbd' and any(
                      item.get('location', '').casefold().removesuffix('.pou') ==
                      row['source_name'].casefold().removesuffix('.pou')
                      for item in spec['implementation_items'] if item.get('language') == 'fbd')}
        attach_plan_check(validation, spec, models)
    with tempfile.TemporaryDirectory(prefix='gxw-edit-candidate-') as directory:
        payload = prepare_candidate(directory, raw=result.data, program=program,
            revision=(context.program_ir or {}).get('revision', 0) + 1,
            summary=arguments.get('summary') or '编辑 GXW 工程', write_report=result.report)
        payload['metadata']['validation'].update(validation)
        if payload['target_mode'] == 'ladder':
            _check_ladder_edit(payload, directory, context, spec)
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'validation': payload['metadata']['validation'],
        'edited_sources': result.report['edited_sources'],
        'pending_action': {'type': 'accept_gxw_candidate', 'project_id': context.project_id,
            'base_version_id': context.version_id, 'target_mode': payload['target_mode'],
            'summary': payload['metadata']['summary'], '_confirmed_spec': copy.deepcopy(_generation_spec(context)),
            '_gxw_candidate': snapshot}}


def ladder_patch_candidate(context, patch, *, generation_handoff=None):
    from agent_runtime.tool_errors import ToolPreconditionError
    from agent_runtime.tool_context import _generation_spec, _ladder_user_facts
    from application.compact_bindings import CompactBindings
    from application.confirmed_generation_context import direct_history_fields
    from gxw.offline_project import edit_ladder_project
    from plc.ir import canonical_sha256
    if context.native_snapshot is None or not context.version_id:
        raise ValueError('instruction edits require a saved GXW version')
    revision = context.program_ir['revision']
    if patch.get('base_revision') not in (None, revision):
        raise ValueError('stale source revision')
    source_hash = patch.get('base_ir_sha256')
    if source_hash is not None and source_hash != canonical_sha256(context.program_ir):
        raise ValueError('stale source IR binding')
    if patch.get('target_revision') not in (None, revision + 1):
        raise ValueError('instruction patch target_revision must be the next source revision')
    if patch.get('operations'):
        if patch.get('instruction_edits'):
            raise ValueError(
                "ordered instruction edits cannot be mixed with graphical network replacements; "
                "omit 'operations' and edit by original record_index in 'instruction_edits'")
        raise ValueError(
            "saved GXW ladder versions use ordered source instructions (cannot be mixed with graphical network "
            "replacements in 'operations'); call get_generation_context first to read raw_networks record_index "
            "values, then submit patch.instruction_edits")
    spec = _generation_spec(context)
    bindings = CompactBindings(context.plc_model, _ladder_user_facts(context))
    edits = copy.deepcopy(patch.get('instruction_edits', []))
    source_bound = bool((generation_handoff or {}).get('external_context_id')
                        or (patch.get('base_revision') == revision and source_hash is not None))
    if not source_bound and any('expected' not in edit for edit in edits):
        raise ToolPreconditionError('PATCH_SOURCE_REQUIRED',
            '本地没有与本次修改匹配的源码版本，请选择原工程版本后继续。')
    for index, edit in enumerate(edits):
        if 'instructions' in edit:
            edit['instructions'] = [bindings.ordered_record(record, f'instruction_edits[{index}].instructions[{position}]')
                                    for position, record in enumerate(edit['instructions'])]
    result = edit_ladder_project(context.native_snapshot, context.program_ir['program_name'],
        edits, device_comments=patch.get('device_comments'),
        comment_table=patch.get('comment_table'), declaration_edits=patch.get('declaration_edits'),
        save_policy=patch.get('save_policy', 'checked'), source_bound=source_bound)
    with tempfile.TemporaryDirectory(prefix='gxw-offline-candidate-') as directory:
        payload = prepare_candidate(directory, raw=result.data, program=context.program_ir['program_name'],
            revision=context.program_ir['revision'] + 1, summary='离线修改 GXW 指令、声明或注释', write_report=result.report)
        after = _check_ladder_edit(payload, directory, context, spec)
        handoff = direct_history_fields((context.version or {}).get('generation_handoff'))
        handoff.update(copy.deepcopy(generation_handoff or {}))
        if handoff:
            payload['metadata']['generation_handoff'] = handoff
        snapshot = pack_candidate(payload, directory)
    from plc.core import PLCCore
    diff = PLCCore().diff_programs(context.program_ir, after)
    return {'requires_confirmation': True, 'diff': diff, 'validation': payload['metadata']['validation'],
        'behavior_result': payload['metadata']['behavior_result'],
        'program_size': payload['metadata']['program_size'],
        'pending_action': {'type': 'accept_gxw_candidate', 'project_id': context.project_id,
            'base_version_id': context.version_id, 'target_mode': 'ladder', 'summary': payload['metadata']['summary'],
            '_confirmed_spec': copy.deepcopy(spec),
            '_gxw_candidate': snapshot, 'diff': diff}}


def definition_candidate(context, command, *, device_comments=None, comment_table=None, save_policy='checked'):
    """Keep the selected source view while editing an existing table or FB."""
    from agent_runtime.tool_context import _generation_spec
    from gxw.offline_authoring import write_existing_definition
    if not context.version_id or context.native_snapshot is None:
        raise ValueError('existing definition edits require a saved GXW version')
    result = write_existing_definition(context.native_snapshot, command,
        device_comments=device_comments, comment_table=comment_table, save_policy=save_policy)
    with tempfile.TemporaryDirectory(prefix='gxw-definition-') as directory:
        payload = prepare_candidate(directory, raw=result.data,
            program=context.version['program_name'], revision=(context.program_ir or {}).get('revision', 0) + 1,
            summary='离线修改已有声明或 ST 正文', write_report=result.report)
        data = json_bytes(result.report['definition_after'])
        (Path(directory) / 'definition.json').write_bytes(data)
        payload['artifacts']['definition'] = {'path': 'definition.json', 'sha256': hashlib.sha256(data).hexdigest()}
        payload['metadata']['validation']['source_checks'] = result.report.get('source_checks', [])
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'definition': result.report['definition_after'],
        'verification': payload['metadata']['validation'],
        'pending_action': {'type': 'accept_gxw_candidate', 'project_id': context.project_id,
            'base_version_id': context.version_id, 'target_mode': payload['target_mode'],
            'summary': payload['metadata']['summary'], '_confirmed_spec': copy.deepcopy(_generation_spec(context)),
            '_gxw_candidate': snapshot}}


def generate_candidate(output, snapshot, context, images, job):
    """Use the existing tool contracts for one model-assisted offline edit."""
    from agent_runtime.generation_tools import _get_generation_context
    from agent_runtime.plc_tools import build_default_tool_registry
    from application.model_api import _request_model, _user_message_with_images, bound_provider_profile, current_provider
    from model_runtime.responses import ResponseContract
    from .fbd import unpack_candidate
    if snapshot.get('program'):
        context = source_context(context, snapshot['program'])
    generation = _get_generation_context(context, {
        'user_requirement': snapshot.get('text', ''), 'execution_mode': 'background',
        'catalog_scope': snapshot.get('catalog_scope') or 'task',
        'templates': snapshot.get('catalog_templates')})
    source_project = read_offline_project(context.native_snapshot)
    tool = generation['output_contract']['tool']
    ladder_target = tool == 'patch_program'
    fbd_target = (context.version or {}).get('target_mode') == 'fbd'
    if not (ladder_target or fbd_target):
        generation['project_comments'], generation['comment_tables'], _ = source_project.comments()
        generation['declaration_tables'] = source_project.declaration_tables()
        generation['project_sources'] = source_project.sources()
        generation['selected_source'] = read_source(context.native_snapshot,
                                                    (context.version or {}).get('program_name'))
    if fbd_target:
        tool = 'edit_gxw_project'
    if tool not in {'patch_program', 'create_st_candidate', 'create_sfc_candidate', 'create_fbd_candidate', 'edit_gxw_project'}:
        raise GXWFormatError('selected source does not expose an existing offline edit contract')
    registry = build_default_tool_registry()
    schemas = {item['function']['name']: item['function']['parameters'] for item in registry.schemas()}
    schema = schemas[tool]
    declaration_tool = {'tool': 'create_fbd_candidate',
        'operations': ['edit_global_labels', 'edit_pou_labels'], 'schema': schemas['create_fbd_candidate']}
    transaction_tool = {'tool': 'edit_gxw_project', 'schema': schemas['edit_gxw_project'],
                        'version_id': context.version_id}
    instructions = generation.get('generation_instructions', '') + (
        '\n\n# Existing source edit response\nReturn JSON {"summary":"...","arguments":{...}} using the supplied tool schema. '
        'For an existing declaration-table edit, return '
        '{"summary":"...","tool":"create_fbd_candidate","arguments":{...}} with '
        'operation edit_global_labels or edit_pou_labels and its exact current source_name. '
        'declarations contains upserts, remove_declarations names explicit removals. '
        'For joint edits of several existing sources or interfaces return tool edit_gxw_project, '
        'with the exact current version_id, source names and language-specific commands. '
        'Use save_policy=draft for offline editing (patch.save_policy for patch_program); '
        'preserve diagnostics in the editable source artifact. '
        'If the request cannot be expressed safely, return {"unsupported":"reason"}. '
        'Modify the selected existing source or its existing declaration tables. Internal instructions, '
        'graph objects and declarations may be added or removed using the supplied contracts. '
        'Do not create engineering-level POUs, FB definitions, declaration tables, resources or tasks; '
        'do not change task registration or request native operations. '
        'patch_program arguments must contain the nested patch object.')
    from application.fbd_context import compile_envelope, json_text
    from application.generation_wire import context_system_prompt
    envelope = {'tool_schema': schema, 'existing_declaration_tool': declaration_tool,
                'project_transaction_tool': transaction_tool}
    optional_paths = []
    if ladder_target:
        from application.compact_bindings import instruction_form_units
        from shared.model_context import technical_value

        # Share the generation packet's field paths so a continuation can
        # inherit the confirmed spec and append only changes to this source.
        current = generation['current_model']
        record_fields = ('record_index', 'op', 'args', 'operand_forms', 'kind', 'text', 'reason')
        networks = []
        for network in current['networks']:
            records = [{key: row[key] for key in record_fields if key in row}
                       for row in network['raw_instructions']]
            networks.append({**network, 'raw_instructions': records})
        envelope.update({
            'context_schema_version': 3,
            'plc_model': generation['plc_model'],
            'request': generation['generation_request'],
            'confirmed_spec': technical_value(generation.get('confirmed_spec')),
            'input_conditions': generation['input_conditions'],
            **({'prior_user_input': generation['prior_user_input']} if 'prior_user_input' in generation else {}),
            'knowledge': generation['knowledge'],
            **({'knowledge_coverage': generation['knowledge_coverage']} if 'knowledge_coverage' in generation else {}),
            'current_version_id': generation['current_version_id'],
            'current_model': {**current, 'networks': networks},
            'output_contract': generation['output_contract'],
            'project_sources': source_project.sources(),
        })
        envelope['knowledge'] = [*envelope['knowledge'], *instruction_form_units(context.plc_model, envelope['knowledge'])]
    elif fbd_target:
        # The MCP result retains its compatibility fields. The model needs the
        # shared engineering input only once, not duplicate catalogs, graphs,
        # prompts and internal delivery receipts surrounding the same input.
        envelope['context'] = {key: generation[key] for key in (
            'project_id', 'plc_model', 'target_mode', 'current_version_id', 'generation_input')}
    else:
        envelope['context'] = generation
        optional_paths = [('context', 'selected_source', 'svg'),
                          ('context', 'selected_source', 'model'),
                          ('context', 'project_comments')]
    tool_options = {}
    if ladder_target and bound_provider_profile():
        from application.ladder_instruction_tool import INSTRUCTION_LOOKUP_PROMPT, instruction_tool_for
        from application.ladder_model_session import LadderModelSession, ladder_tools

        tools = ladder_tools()
        lookup = instruction_tool_for(
            current_provider(), model_name=snapshot.get('model', {}).get('model'), plc_model=context.plc_model,
            options={}, stream=True, tools=tools,
            on_stage=lambda stage, message: job.emit('progress', {'stage': stage, 'message': message}))
        if lookup is not None:
            session = LadderModelSession(phase='source_edit', lookup=lookup)
            session.remember(envelope['knowledge'])
            tool_options = {'tools': tools, 'ladder_session': session}
            instructions += INSTRUCTION_LOOKUP_PROMPT + (
                '\n已有 GXW 的有序源码变更使用上面的 patch_program 参数。'
                '可用 lookup_plc_instructions 查证，完成后返回原 record_index 和修改内容。')
    envelope, budget_report = compile_envelope(
        envelope, instructions=instructions, optional_paths=optional_paths,
        model_profile=bound_provider_profile(), image_count=len(images or ()),
        plc_model=context.plc_model, request=str(snapshot.get('text') or ''),
        task_type='edit', optional_templates=(
            generation.get('generation_handoff', {}).get('catalog_report', {}).get('optional_templates', ())
            if fbd_target else ()))
    response = _request_model([{'role': 'system', 'content': context_system_prompt(instructions)},
        _user_message_with_images(json_text(envelope), images),
        ], model_name=snapshot.get('model', {}).get('model'),
        effort=None, stream=True, on_reasoning_chunk=lambda value: job.emit('reasoning', {'text': value}),
        on_content_chunk=lambda value: job.emit('content', {'text': value}),
        response_contract=ResponseContract('gxw_existing_edit', 'json', human_paths=('summary', 'unsupported')),
        **tool_options)
    text = response.message.content.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    answer = json.loads(text)
    if (isinstance(answer, dict) and (context.version or {}).get('target_mode') == 'fbd'
            and {'summary', 'model'} <= answer.keys()
            and not answer.keys() - {'summary', 'model', 'device_comments', 'comment_table'}):
        # Preserve the former single-FBD model response while executing the
        # same source-bound transaction as the new multi-source contract.
        answer = {'summary': answer['summary'], 'arguments': {
            'version_id': context.version_id, 'edits': [{'source_name': context.version['program_name'],
                'command': {'model': answer['model']}}],
            **{key: answer[key] for key in ('device_comments', 'comment_table') if key in answer}}}
    if isinstance(answer, dict) and set(answer) == {'unsupported'} and isinstance(answer['unsupported'], str):
        raise GXWFormatError(answer['unsupported'])
    if (not isinstance(answer, dict) or not {'summary', 'arguments'} <= set(answer)
            or set(answer) - {'summary', 'arguments', 'tool'}
            or not isinstance(answer['summary'], str) or not isinstance(answer['arguments'], dict)):
        raise GXWFormatError('offline source edit requires summary and tool arguments')
    arguments = answer['arguments']
    selected_tool = answer.get('tool', tool)
    if not isinstance(selected_tool, str) or selected_tool not in {tool, 'create_fbd_candidate', 'edit_gxw_project'}:
        raise GXWFormatError('offline generation only uses the selected source or existing declaration contract')
    if selected_tool not in {tool, 'edit_gxw_project'} and arguments.get('operation') not in {'edit_global_labels', 'edit_pou_labels'}:
        raise GXWFormatError('alternate offline tool only edits existing declaration tables')
    tool = selected_tool
    if tool == 'edit_gxw_project':
        arguments.setdefault('version_id', context.version_id)
        arguments.setdefault('program', (context.version or {}).get('program_name'))
    if tool == 'patch_program':
        if not isinstance(arguments.get('patch'), dict):
            raise GXWFormatError('offline Ladder editing requires its nested patch')
        for key in ('base_revision', 'base_ir_sha256'):
            arguments['patch'].setdefault(key, generation['output_contract'][key])
        if 'save_policy' not in arguments:
            arguments['patch'].setdefault('save_policy', 'draft')
    else:
        arguments.setdefault('save_policy', 'draft')
    if tool == 'create_st_candidate' and arguments.get('operation') not in {
            'edit_st_program', 'edit_st_fb', 'edit_st_function', 'edit_st_source'}:
        raise GXWFormatError('offline ST generation only edits an existing source')
    if tool == 'create_sfc_candidate' and arguments.get('operation') != 'edit':
        raise GXWFormatError('offline SFC generation only edits an existing source')
    if tool == 'create_fbd_candidate' and arguments.get('operation') not in {
            'edit_st_fb', 'edit_st_function', 'edit_st_source', 'edit_global_labels', 'edit_pou_labels'}:
        raise GXWFormatError('offline definition generation only edits existing definitions or tables')
    job.checkpoint()
    result = registry.call(tool, arguments, context)
    if not result['ok']:
        raise GXWFormatError(result['error']['message'])
    pending = result['data']['pending_action']
    key = {'accept_gxw_candidate': '_gxw_candidate', 'accept_st_candidate': '_st_candidate',
           'accept_sfc_candidate': '_sfc_candidate'}[pending['type']]
    payload = unpack_candidate(pending[key], output)
    from application.confirmed_generation_context import direct_history_fields
    payload['metadata'].update(summary=answer['summary'],
        generation_handoff={**direct_history_fields((context.version or {}).get('generation_handoff')),
                            'workflow': 'existing_gxw_offline', 'tool': tool,
                            **({'instruction_lookup': lookup.receipts} if tool_options and lookup.receipts else {}),
                            'budget_report': budget_report})
    return payload


class GXWFileService:
    def __init__(self, workbench):
        self.workbench = workbench

    def inspect(self, *, data_base64=None, path=None, program=None):
        return inspect_offline_project(read_file_input(data_base64=data_base64, path=path), program)

    def source(self, project_id, version_id, program=None):
        context = self.workbench.projects.tool_context(project_id, version_id)
        return read_source_tool(context, {'program': program} if program else {})

    def edit_project(self, *, project_id, version_id, request_id, direct_request=False, **arguments):
        from .workspace import ConflictError, atomic_json, canonical_hash, read_json, record_id
        workbench = self.workbench
        workbench.writable()
        record_id(request_id)
        command = dict(project_id=project_id, version_id=version_id, **arguments)
        digest = canonical_hash(command)
        key = hashlib.sha256(request_id.encode()).hexdigest()
        receipt = workbench.state_dir / 'gxw_edits' / (key + '.json')
        with workbench.lock.thread_lock:
            if receipt.exists():
                saved = read_json(receipt)
                if saved['command_hash'] != digest:
                    raise ConflictError('Request ID is already bound to another GXW edit')
                return workbench.proposals.get(saved['proposal_id'])
            context = workbench.projects.tool_context(project_id, version_id)
            result = transaction_candidate(context, dict(version_id=version_id, **arguments))
            proposal = workbench.proposal_workflow.pending_proposal(result['pending_action'],
                'gxw_edit_' + key, base_version_id=version_id, direct_request=direct_request)
            atomic_json(receipt, {'command_hash': digest, 'proposal_id': proposal['id']})
            return proposal

    def import_project(self, *, data_base64=None, path=None, program=None, project_id=None,
                       version_id=None, request_id=None, filename=None):
        from uuid import uuid4
        from .workspace import ConflictError, atomic_json, canonical_hash, read_json, record_id
        workbench = self.workbench
        raw = read_file_input(data_base64=data_base64, path=path)
        project = read_offline_project(raw)
        selected = project.select(program)
        workbench.writable()
        request_id = request_id or uuid4().hex
        record_id(request_id)
        digest = canonical_hash({'data': base64.b64encode(raw).decode('ascii'),
            'program': selected['source_name'], 'project_id': project_id, 'version_id': version_id,
            'filename': filename})
        key = hashlib.sha256(request_id.encode()).hexdigest()
        receipt = workbench.state_dir / 'gxw_commands' / (key + '.json')
        workbench.state_dir.mkdir(parents=True, exist_ok=True)
        with workbench.lock.thread_lock:
            saved_command = read_json(receipt) if receipt.exists() else None
            if saved_command is not None:
                if saved_command['command_hash'] != digest:
                    raise ConflictError('Request ID is already bound to another GXW import')
                if saved_command.get('proposal_id'):
                    proposal = workbench.proposals.get(saved_command['proposal_id'])
                    result = proposal.get('result') or {}
                    return {'status': proposal['status'], 'project_id': saved_command['project_id'],
                        'version_id': result.get('version_id'), 'proposal_id': proposal['id'],
                        'project': workbench.projects.project(saved_command['project_id'])}
                project_id = saved_command['project_id']
            with tempfile.TemporaryDirectory(dir=workbench.state_dir, prefix='gxw-import-') as directory:
                # Validate the complete immutable source before allocating a
                # new project. Failed imports do not leave empty projects.
                payload = prepare_candidate(directory, raw=raw, program=selected['source_name'])
                if project_id is None:
                    title = Path(filename or path or selected['name']).stem[:160]
                    created = workbench.project_commands.create_imported_project(name=title,
                        raw=raw, program=selected['source_name'])
                    project_id = created['id']
                else:
                    saved = workbench.projects.raw_project(project_id)
                    from gxworks2.project_profiles import native_project_profile
                    if native_project_profile(saved['plc_model']) != native_project_profile(project.context['cpu']):
                        raise ValueError('imported GXW CPU differs from selected project')
                    if version_id is None and saved.get('active_version_id'):
                        raise ValueError('import into an existing project requires its explicit base version_id')
                atomic_json(receipt, {'command_hash': digest, 'project_id': project_id})
                payload.update(type='accept_generated_program', project_id=project_id,
                    base_version_id=version_id, _confirmed_spec=copy.deepcopy(workbench.projects.raw_project(project_id).get('confirmed_spec')))
                proposal = workbench.proposal_workflow.pending_proposal(payload, 'gxw_' + key,
                    base_version_id=version_id, direct_request=True)
                atomic_json(receipt, {'command_hash': digest, 'project_id': project_id, 'proposal_id': proposal['id']})
            result = proposal.get('result') or {}
            return {'status': proposal['status'], 'project_id': project_id, 'version_id': result.get('version_id'),
                    'proposal_id': proposal['id'], 'project': workbench.projects.project(project_id)}
