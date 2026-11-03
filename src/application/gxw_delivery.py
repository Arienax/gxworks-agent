# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Shared GXW-first delivery at the application save boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar

_progress = ContextVar('gxw_delivery_progress', default=None)


@contextmanager
def delivery_progress(callback):
    token = _progress.set(callback)
    try:
        yield
    finally:
        _progress.reset(token)


def notify(stage, message):
    callback = _progress.get()
    if callback is not None:
        callback('progress', {'stage': stage, 'message': message})


class DeliveryUnavailable(RuntimeError):
    """No supported engineering environment; source artifacts remain deliverable."""


def validate_source_artifacts(files, mode):
    if mode == 'ladder':
        from plc.ir import validate_plc_ir, ir_to_ladder
        program = json.loads(files['ir'])
        validate_plc_ir(program, validate_ladder=False)
        if ir_to_ladder(program) != json.loads(files['json']):
            raise ValueError('Candidate JSON differs from the prepared IR')
    elif mode == 'st':
        from plc.st_frontend import parse_body
        parse_body(files['st'].decode('utf-8'))
    else:
        value = json.loads(files[mode])
        if not isinstance(value, dict):
            raise ValueError('Structured candidate must be an object')
        if mode == 'sfc':
            from gxw.sfc_offline import _symbol_graph
            rebuilds = [row for row in value['graph_edits'] if row.get('kind') == 'rebuild']
            if len(rebuilds) != 1:
                raise ValueError('New SFC requires one complete graph rebuild')
            _symbol_graph(rebuilds[0]['symbols'])


def installation():
    from gxworks2.finder import GXWorks2Finder
    return GXWorks2Finder().find_executable()


def create_blank(output, language, cpu, *, block=None):
    """Admit only proven headless creation, with an explicit source fallback."""
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(cpu)
    if profile is None or not profile.program_supported:
        raise DeliveryUnavailable(f'GX Works2 has no supported Program project for {cpu}.')
    if installation() is None:
        raise DeliveryUnavailable('GX Works2 was not found; configure GXWORKS2_EXE or install GX Works2.')
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    from application.execution import DesktopResourceLock
    notify('gxw_create', '正在无窗口创建 GXW 工程')
    try:
        with DesktopResourceLock():
            return NativeWorkspaceSourceSave().create_project(cpu, language, block=block)
    except WorkspaceSaveError as error:
        if getattr(error, 'preserve_delivery', False):
            raise
        raise DeliveryUnavailable('Headless project creation: ' + str(error)) from error


def fallback(output, mode, cpu, candidate, reason):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    from application.fbd import json_bytes
    import hashlib
    if mode == 'st':
        from gxworks2.csv_source_export import st_global_labels_csv
        labels, export_issues = st_global_labels_csv(candidate.get('declarations') or [])
        notes = ''.join('(* ' + row['name'] + ': ' + row['message'] +
                        (' ' + row['initial_value'] if 'initial_value' in row else '') + ' *)\n' for row in export_issues)
        files = {'st': ('program.txt', (notes + candidate['st_text']).encode('utf-8')),
                 'global_labels_csv': ('global_labels.csv', labels),
                 'source_candidate': ('source_candidate.json', json_bytes(candidate))}
    elif mode == 'fbd' and 'model' in candidate:
        files = {'fbd': ('fbd.json', json_bytes(candidate['model'])),
                 'source_candidate': ('source_candidate.json', json_bytes(candidate))}
    else:
        files = {mode: (mode + '.json', json_bytes(candidate))}
    artifacts = {}
    for key, (name, data) in files.items():
        (output / name).write_bytes(data)
        artifacts[key] = {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
    payload = {'target_mode': mode, 'plc_model': cpu, 'staging_dir': str(output), 'artifacts': artifacts,
            'metadata': {'target_mode': mode, 'plc_model': cpu, 'summary': 'Generated source candidate',
                         'validation': {'status': 'source_candidate', 'gx_compile': 'not_run', 'native_save': 'not_run'},
                         'delivery': delivery_result(mode, {k: v['path'] for k, v in artifacts.items()}, reason)}}
    if reason is None:
        payload['metadata'].pop('delivery')
        payload['metadata']['delivery_requested'] = True
    if mode == 'st' and export_issues:
        payload['metadata']['label_export_diagnostics'] = export_issues
    return payload


def prepare_generated(output, mode, cpu, candidate, *, baseline=None, block=None):
    """Prepare all four languages through their existing source-bound writers."""
    output = Path(output)
    try:
        raw = baseline if baseline is not None else create_blank(output, mode, cpu,
            block=(block or candidate.get('block')) if mode == 'sfc' else None)
    except DeliveryUnavailable as error:
        if mode == 'ladder':
            raise
        return fallback(output, mode, cpu, candidate, str(error))
    if baseline is None and mode in ('st', 'fbd'):
        from gxw.project_metadata import initialize_created_project_gui
        raw = initialize_created_project_gui(raw)
    from gxw.offline_project import read_offline_project
    project = read_offline_project(raw)
    selected = project.select(None)
    name = selected['source_name']
    if selected['language'] != mode:
        raise ValueError('Created project language differs from the requested candidate')
    if mode == 'sfc' and baseline is None:
        from gxw.sfc_pou import read_sfc_project_context
        from gxw.sfc_offline import initialize_empty_sfc_source
        from gxw.project_writer import write_project_replacements
        current = read_sfc_project_context(raw, name)
        body = initialize_empty_sfc_source(current.source)
        raw = write_project_replacements(raw, {name: (project.mapping[name], current.source.raw, body)}).data
        candidate = copy.deepcopy(candidate)
        # The native initializer assigns internal UUID names. Bind the model's
        # numbered initial objects to those exact identities, once, after New.
        current = read_sfc_project_context(raw, name)
        model = current.object_model()
        for edit in candidate.get('child_edits', []):
            if edit.get('number') != 0 and edit.get('name') not in {'action_0', 'transition_0'}:
                continue
            rows = model['actions'] if edit['kind'] == 'action' else [row for row in model['children'] if row['kind'] == edit['kind']]
            matches = [row for row in rows if row.get('number') in (None, 0)]
            if len(matches) == 1:
                edit['name'] = matches[0]['name']
                edit['operation'] = 'edit'
                edit.pop('number', None)
            elif not matches and edit['kind'] in {'action', 'transition'}:
                # The headless factory can start with no initial children.
                # Resolve the generation contract's initial aliases against
                # this actual empty collection, rather than assuming UI data.
                edit['operation'] = 'create'
                edit.setdefault('number', 0)
    if mode == 'ladder':
        from gxworks2.csv_export import lower_ladder_program
        from gxw.ladder_program_writer import build_ladder_project
        from application.gxw_files import prepare_candidate
        lowered = lower_ladder_program(candidate, plc_model=cpu, infer_device_comments=False)
        result = build_ladder_project(lowered, plc_model=cpu, baseline=raw)
        payload = prepare_candidate(output, raw=result.data, program=result.program, write_report=result.report,
                                    revision=candidate.get('revision', 1))
    elif mode == 'st':
        from gxw.offline_authoring import write_existing_definition
        from application.native_st import prepare_candidate
        command = {'operation': 'edit_st_program', 'source_name': name, 'st_text': candidate['st_text']}
        if candidate.get('declarations') is not None:
            global_rows = [row for row in candidate['declarations'] if row.get('class_name', '').startswith('VAR_GLOBAL')]
            command['declarations'] = [row for row in candidate['declarations'] if row not in global_rows]
            if global_rows:
                from gxw.st_program import project_context_snapshot
                tables = project_context_snapshot(raw, name)['globals']
                if len(tables) != 1:
                    from gxw.models import GXWFormatError
                    raise GXWFormatError('New ST global declarations require one actual global label table')
                raw = write_existing_definition(raw, {'operation': 'edit_global_labels',
                    'source_name': tables[0]['source'], 'declarations': global_rows}).data
        result = write_existing_definition(raw, command)
        payload = prepare_candidate(output, imported=result.data, program_name=name, plc_model=cpu)
    elif mode == 'fbd':
        from application.fbd import prepare_candidate
        options = candidate if 'model' in candidate else {}
        model = copy.deepcopy(options.get('model', candidate))
        model['program'] = name
        payload = prepare_candidate(output, model=model, baseline=raw, plc_model=cpu,
            device_comments=options.get('device_comments'), comment_table=options.get('comment_table'))
    elif mode == 'sfc':
        from application.sfc import prepare_candidate
        payload = prepare_candidate(output, baseline=raw, program_name=name,
            graph_edits=candidate.get('graph_edits'), child_edits=candidate.get('child_edits'),
            device_comments=candidate.get('device_comments'), declaration_edits=candidate.get('declaration_edits'))
    else:
        raise ValueError('Unsupported GXW delivery language')
    payload['metadata']['delivery_requested'] = True
    return payload


def delivery_result(mode, artifacts, reason=None):
    primary = ('gxw' if 'gxw' in artifacts else 'program_csv' if 'program_csv' in artifacts
               else 'st' if 'st' in artifacts else mode if mode in artifacts else next(iter(artifacts), None))
    return {'preferred_format': 'gxw', 'actual_format': 'gxw' if primary == 'gxw' else
            'csv' if primary == 'program_csv' else 'txt' if primary == 'st' else 'candidate',
            'primary_artifact': primary, 'degraded': primary != 'gxw', 'reason': reason}


def source_candidate_action(context, arguments, mode):
    """Freeze source data without launching a native editor before confirmation."""
    import tempfile
    from application.fbd import pack_candidate
    if mode == 'st':
        from plc.st_candidate import prepare_source_candidate
        from agent_runtime.tool_context import _confirmed_spec
        candidate, checked = prepare_source_candidate(arguments['st_text'], arguments.get('declarations'),
            plc_model=context.plc_model, confirmed_spec=_confirmed_spec(context))
    elif mode == 'fbd':
        candidate = {key: arguments[key] for key in ('model', 'device_comments', 'comment_table') if key in arguments}
        # Source-bound expansion checks ports and literals before any native
        # operation. The catalog is a read input, never the delivered project.
        from application.fbd import _target_baseline
        from gxw.object_model import prepare_object_project
        from gxw.offline_project import read_offline_project
        catalog = _target_baseline(None, context.plc_model)
        model = copy.deepcopy(candidate['model'])
        model['program'] = read_offline_project(catalog).select(None)['source_name']
        prepare_object_project(model, baseline=catalog)
    else:
        candidate = {key: arguments[key] for key in ('graph_edits', 'child_edits', 'declaration_edits', 'device_comments', 'block') if key in arguments}
        if not candidate.get('graph_edits') or not any(edit.get('kind') == 'rebuild' for edit in candidate['graph_edits']):
            raise ValueError('New SFC generation requires a complete graph rebuild')
    with tempfile.TemporaryDirectory(prefix=mode + '-source-') as directory:
        payload = fallback(directory, mode, context.plc_model, candidate, None)
        payload['metadata']['summary'] = arguments.get('summary') or f'Generated {mode.upper()} candidate'
        if mode == 'st':
            from plc.maintainability import review_maintainability
            payload['metadata']['semantic_validation'] = checked
            payload['metadata']['validation'].update(static=checked['status'], syntax=checked['syntax'],
                semantic=checked['semantic'], native='unverified', messages=[f['message'] for f in checked['findings']])
            payload['metadata']['maintainability_review'] = review_maintainability({'st_code': candidate['st_text']}, target_mode='st')
        frozen = pack_candidate(payload, directory)
    from agent_runtime.tool_context import _confirmed_spec
    return {'requires_confirmation': True, 'verification': payload['metadata']['validation'],
            'pending_action': {'type': 'accept_' + mode + '_candidate', 'project_id': context.project_id,
                'base_version_id': context.version_id or None, 'target_mode': mode, 'summary': payload['metadata']['summary'],
                'validation': payload['metadata']['validation'], '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)),
                '_' + mode + '_candidate': frozen}}


def new_generation_context(context, arguments):
    from agent_runtime.plc_tools import build_default_tool_registry
    from agent_runtime.tool_context import _confirmed_spec
    mode = (context.version or {}).get('target_mode') or context.project['target_mode']
    name = 'create_' + mode + '_candidate'
    schema = copy.deepcopy(build_default_tool_registry()._tools[name].parameters)
    schema['properties']['operation'] = {'const': 'generate', 'type': 'string'}
    if mode == 'st':
        schema = next(rule for rule in schema['oneOf'] if rule['properties']['operation'].get('enum') == ['generate'])
    from agent_runtime.generation_tools import _source_knowledge
    knowledge = _source_knowledge(context, arguments, '', mode, _confirmed_spec(context), task_type='generate')
    knowledge['generation_handoff']['decision_receipt_id'] = context.project.get('confirmed_decision_receipt_id')
    return {**knowledge, 'project_id': context.project_id, 'plc_model': context.plc_model, 'target_mode': mode,
            'current_version_id': context.version_id or None, 'generation_request': arguments.get('user_requirement', ''),
            'generation_instructions': 'Create a complete new ' + mode.upper() + ' program for the exact CPU. '
                'Submit the returned generate contract. Delivery prefers GXW and automatically compiles when GX Works2 is found. '
                'Headless creation resolves the selected CPU through the installed workspace component. '
                'Respect the selected CPU, project type and language; report the actual creation or verification failure. '
                'Do not use IDE New, live editing or captured projects as a workaround. '
                'For SFC provide a complete graph_edits rebuild and all required action/transition child_edits. '
                'For ST provide the implementation body and its complete declarations. '
                'For a complex FBD machine, assemble cohesive ST function blocks with named ports and signals; '
                'create missing ST FB definitions through the project-definition workflow before assembly. '
                'Keep sequence/state, timing/counting, alarms and output interlocks in suitable modules, '
                'rather than expanding the process into many Boolean gates or one opaque whole-machine FB. '
                'For new SFC, create zoom bodies with instructions and action registrations with qualifier/zoom; actions do not contain instructions. '
                'The initial action and transition use logical names action_0/transition_0 and number=0; their actual native identities are resolved on save.',
            'generation_input': {'plc_model': context.plc_model, 'confirmed_spec': _confirmed_spec(context), 'output_schema': schema},
            'output_contract': {'tool': name, 'operation': 'generate', 'parameter_schema': schema}}


def generate_source_candidate(output, snapshot, images, job, context):
    from application.model_api import _request_model, _user_message_with_images
    from model_runtime.responses import ResponseContract
    mode = (context.version or {}).get('target_mode') or context.project['target_mode']
    discovery = new_generation_context(context, {'user_requirement': snapshot.get('text', '')})
    response = _request_model([
        {'role': 'system', 'content': discovery['generation_instructions']},
        _user_message_with_images(json.dumps(discovery, ensure_ascii=False), images)],
        model_name=snapshot.get('model', {}).get('model'), stream=True,
        on_reasoning_chunk=lambda value: job.emit('reasoning', {'text': value}),
        on_content_chunk=lambda value: job.emit('content', {'text': value}),
        response_contract=ResponseContract(mode + '_generate', 'json', human_paths=('summary',)))
    text = response.message.content.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    arguments = json.loads(text)
    if mode == 'st' and isinstance(arguments, dict):
        if 'st_code' in arguments:
            if 'st_text' in arguments:
                raise ValueError('Provide st_code or st_text, not both')
            arguments['st_text'] = arguments.pop('st_code')
        arguments.setdefault('operation', 'generate')
    from agent_runtime.tool_registry import ToolRegistry
    ToolRegistry._validate_arguments(discovery['output_contract']['parameter_schema'], arguments)
    if arguments.get('operation') != 'generate':
        raise ValueError('New source generation requires operation=generate')
    job.checkpoint()
    action = source_candidate_action(context, arguments, mode)
    from application.fbd import unpack_candidate
    payload = unpack_candidate(action['pending_action']['_' + mode + '_candidate'], output)
    payload['metadata']['generation_handoff'] = discovery['generation_handoff']
    return payload


def finalize_saved(output, metadata):
    """Complete delivery once, before version activation, for Web and MCP alike."""
    output = Path(output)
    mode = metadata.get('target_mode')
    artifacts = metadata.get('artifacts') or {}
    if mode not in {'ladder', 'st', 'fbd', 'sfc'} or not artifacts:
        return
    # A read/import is not a generation request. Retain its original evidence.
    if (metadata.get('validation') or {}).get('offline_write') == 'source_preserved' and not metadata.get('delivery_requested'):
        return
    if artifacts.get('write_report') and not metadata.get('delivery_requested'):
        report = json.loads((output / artifacts['write_report']).read_bytes())
        if report.get('operation') in {'import', 'saved_st_program_projection'} and (metadata.get('validation') or {}).get('offline_write') != 'passed':
            return
    reason = (metadata.get('delivery') or {}).get('reason')
    if 'gxw' not in artifacts and not (metadata.get('delivery') or {}).get('degraded'):
        from gxw.models import GXWFormatError
        if mode == 'ladder' and artifacts.get('ir'):
            candidate = json.loads((output / artifacts['ir']).read_bytes())
        elif mode == 'st' and artifacts.get('st'):
            candidate = (json.loads((output / artifacts['source_candidate']).read_bytes())
                         if artifacts.get('source_candidate') else
                         {'st_text': (output / artifacts['st']).read_text(encoding='utf-8')})
        elif artifacts.get('source_candidate'):
            candidate = json.loads((output / artifacts['source_candidate']).read_bytes())
        else:
            candidate = json.loads((output / artifacts[mode]).read_bytes())
        try:
            payload = prepare_generated(output, mode, metadata['plc_model'], candidate)
        except (DeliveryUnavailable, GXWFormatError) as error:
            reason = str(error)
            payload = None if mode == 'ladder' else fallback(output, mode, metadata['plc_model'], candidate, reason)
        if payload is not None:
            artifacts.update({k: v['path'] for k, v in payload['artifacts'].items()})
            prepared = payload['metadata']
            for key, value in prepared.items():
                if key not in {'summary', 'validation', 'semantic_validation', 'maintainability_review', 'generation_handoff', 'generation_metadata'}:
                    metadata[key] = value
            metadata.setdefault('summary', prepared.get('summary', ''))
            existing_validation = metadata.setdefault('validation', {})
            for key, value in prepared.get('validation', {}).items():
                if key == 'messages':
                    existing_validation[key] = list(dict.fromkeys([*existing_validation.get(key, []), *value]))
                elif key not in existing_validation or (key not in {'status', 'static', 'syntax', 'semantic', 'native', 'native_compile', 'gx_compile', 'native_save'} and not key.endswith('_status')):
                    existing_validation[key] = value
            metadata['artifacts'] = artifacts
            reason = (metadata.get('delivery') or {}).get('reason')
    metadata['delivery'] = delivery_result(mode, artifacts, reason)
    metadata.setdefault('generation_metadata', {})['delivery'] = copy.deepcopy(metadata['delivery'])
    if any(metadata.get('validation', {}).get(key) == 'failed' for key in ('status', 'static', 'syntax', 'semantic')):
        metadata.update(activation_blocked=True, lifecycle_status='diagnostic')
    if 'gxw' not in artifacts:
        return
    validation = metadata.setdefault('validation', {})
    if (metadata.get('generation_metadata') or {}).get('delivery_finalized'):
        return
    if validation.get('gx_compile') == 'passed' and validation.get('cold_reopen') == 'passed':
        return
    if installation() is None:
        validation.update(gx_compile='not_run', native_compile_reason='GX Works2 was not found.')
        return
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceAdapterError
    from application.execution import DesktopResourceLock, ExecutionUnavailableError
    from gxw.models import GXWFormatError
    notify('native_compile', '正在进行原生编译、保存及冷重开检查')
    try:
        with DesktopResourceLock():
            result = NativeWorkspaceSourceSave().compile_project((output / artifacts['gxw']).read_bytes())
    except (WorkspaceAdapterError, GXWFormatError, ExecutionUnavailableError) as error:
        if getattr(error, 'preserve_delivery', False) or 'timeout' in str(error).lower() or '超时' in str(error):
            error.preserve_delivery = True
            raise  # An uncertain operation must retain the coordinator's recovery boundary.
        phase = getattr(error, 'phase', 'compile_save')
        validation.update(gx_compile='failed', native_compile_message=str(error), native_phase=phase)
        from application.fbd import json_bytes
        diagnostic = {'phase': phase, 'message': str(error), 'observation': getattr(error, 'observation', {}),
                      'validation': getattr(error, 'validation', None)}
        (output / 'native_diagnostics.json').write_bytes(json_bytes(diagnostic))
        artifacts['native_diagnostics'] = 'native_diagnostics.json'
        metadata.update(activation_blocked=True, lifecycle_status='diagnostic')
        notify('native_compile_failed', '原生编译未通过，GXW 候选和诊断已保留')
        return
    from application.gxw_files import prepare_candidate
    payload = prepare_candidate(output, raw=result.data, program=metadata.get('program_name'),
                                revision=metadata.get('revision', 1))
    artifacts.update({k: v['path'] for k, v in payload['artifacts'].items()})
    metadata.update({k: v for k, v in payload['metadata'].items() if k not in {'summary', 'validation'}})
    metadata['artifacts'] = artifacts
    validation.update(result.report['validation'])
    validation.update(native_compile='passed', compile_state='current')
    if isinstance(validation.get('invalidated_results'), dict):
        validation['invalidated_results']['compilation'] = 'current'
    if isinstance(metadata.get('semantic_validation'), dict):
        metadata['semantic_validation']['native_compile'] = 'passed'
    metadata['validation'] = validation
    from application.fbd import json_bytes
    (output / 'native_write_report.json').write_bytes(json_bytes(result.report))
    artifacts['native_write_report'] = 'native_write_report.json'
    notify('native_compile_passed', '原生编译、保存及冷重开检查已通过')
