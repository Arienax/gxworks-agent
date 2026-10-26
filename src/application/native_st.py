"""Saved ST Program, FB and FUNCTION artifacts in the shared candidate transaction.

GXW is the source of declarations, bodies and task ownership. Model intentions
are bound to that context and the existing native transaction; saved-source
projection v1 remains separate from the intention protocol v2.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from gxw.fb_authoring import st_program_snapshot
from gxw.models import GXWFormatError
from gxw.native_diagnostics import native_validation_source_plan
from gxw.project_metadata import project_gui_configuration
from gxw.st_program import project_context_snapshot
from gxworks2.project_profiles import native_project_profile
from .fbd import json_bytes


def candidate_native_plan(candidate, *, baseline, plc_model, program_name=None, confirmed_spec=None):
    """Bind v2 intentions to existing engineering commands, without executing."""
    from plc.st_candidate import normalize_candidate, DECLARATION_FIELDS
    from plc.st_frontend import STUncovered
    from plc.st_validation import inspect_st_program
    from gxw.st_program import project_context_snapshot
    from application.native_engineering import normalize_native_plan, validate_native_baseline
    live = str(plc_model).upper() != 'FX3U'
    validate_native_baseline(baseline, plc_model=plc_model, live=live)
    context = project_context_snapshot(baseline, program_name)
    normalized, parsed = normalize_candidate(candidate, project_context=context)
    program = normalized['program']
    if not program['name']:
        raise ValueError('ST candidate has no project-bound Program identity')
    rows = normalized['declaration_edits']['upserts']
    local_rows = [row for row in rows if 'scope' not in row]
    existing = [p for p in context['pous'] if p['name'].casefold() == program['name'].casefold()]
    selected = existing[0] if existing else None
    def merge(current, upserts):
        result = {r['name'].upper(): {k: v for k, v in r.items() if k in DECLARATION_FIELDS} for r in current}
        for row in upserts:
            result[row['name'].upper()] = {**result.get(row['name'].upper(), {}),
                                         **{k: v for k, v in row.items() if k in DECLARATION_FIELDS}}
        return list(result.values())
    declarations = merge(selected['declarations'] if selected else [], local_rows)
    # Check the exact draft declaration set, rather than the old saved labels.
    draft = dict(context, selected_source=selected['program'] if selected else '__candidate__')
    draft['globals'] = [dict(table, declarations=merge(table['declarations'],
        [row for row in rows if row.get('scope', {}).get('source') == table['source']])) for table in context['globals']]
    draft['pous'] = [p for p in context['pous'] if p is not selected] + [dict(name=program['name'], program=draft['selected_source'],
        kind='program', language='st', declarations=declarations, st_text=program['body'], task_bindings=[normalized['task_binding']])]
    for unit in parsed.units:
        if unit.kind != 'program':
            draft['pous'] = [p for p in draft['pous'] if p['name'].casefold() != unit.name.casefold()] + [dict(name=unit.name,
                program=unit.name + '.pou', kind=unit.kind, language='st', st_text=unit.body_text,
                declarations=[d.native_row() for d in unit.declarations], return_type=unit.return_type)]
    draft['structures'] = [row for row in context['structures'] if row['name'].upper() not in parsed.types] + [dict(name=name, status='available',
        members=[d.native_row() for d in fields]) for name, fields in parsed.types.items() if isinstance(fields, list)]
    report = inspect_st_program(program['body'], plc_model=plc_model, project_context=draft,
        hardware_context=(confirmed_spec or {}).get('hardware_profile'), confirmed_spec=confirmed_spec)
    if report['status'] == 'failed':
        raise ValueError('ST candidate failed local syntax/semantic checks: ' + '; '.join(f['message'] for f in report['findings'] if f['severity'] == 'error'))
    commands = []
    existing_types = {row['name'].upper(): row for row in context['structures']}
    for name, fields in parsed.types.items():
        if name in existing_types:
            before = existing_types[name]
            if (not isinstance(fields, list) or before['status'] != 'available'
                    or [(r['name'].upper(), r['data_type'].upper(), r.get('initial_value', '')) for r in before['members']] !=
                       [(d.name.upper(), d.data_type.upper(), d.initial_text) for d in fields]):
                raise ValueError('Source type conflicts with an existing GXW structure: ' + name)
            continue
        if not isinstance(fields, list):
            raise STUncovered('Native type-alias creation is not covered: ' + name)
        commands.append(dict(operation='create_structure', source_name=name,
            members=[dict(name=f.name, data_type=f.data_type, initial_value=f.initial_text) for f in fields]))
    for table in context['globals']:
        upserts = [{k: v for k, v in row.items() if k in DECLARATION_FIELDS} for row in rows
                   if row.get('scope', {}).get('source') == table['source']]
        if upserts:
            commands.append(dict(operation='edit_global_labels', source_name=table['source'], declarations=upserts))
    def native_rows(values):
        result = []
        for row in values:
            if any(row.get(k) for k in ('device', 'iec_address', 'member_devices')):
                raise STUncovered('Native local address editing is outside the current POU declaration operation')
            item = {k: v for k, v in row.items() if k in {'name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment'}}
            target = next((p for p in draft['pous'] if p['name'].upper() == row['data_type'].upper()), None)
            if target and target['kind'] == 'function_block':
                item['kind'] = 'function_block'
            elif any(s['name'].upper() == row['data_type'].upper() for s in draft['structures']):
                item['kind'] = 'structure'
            result.append(item)
        return result
    for unit in parsed.units:
        if unit.kind == 'program':
            continue
        if unit.kind != 'function_block':
            raise STUncovered('Native FUNCTION authoring requires a separately supported engineering operation')
        found = [p for p in context['pous'] if p['name'].casefold() == unit.name.casefold()]
        if found and (len(found) != 1 or found[0]['kind'] != 'function_block' or found[0]['language'] != 'st'):
            raise ValueError('Callable source identity is ambiguous or belongs to another language')
        commands.append(dict(operation='edit_st_fb' if found else 'create_st_fb', source_name=unit.name,
            st_text=unit.body_text, declarations=native_rows(merge(found[0]['declarations'] if found else [], [d.native_row() for d in unit.declarations]))))
    command = dict(operation='edit_st_program' if selected else 'create_st_program', source_name=program['name'],
                   st_text=program['body'])
    if selected and local_rows:
        commands.append(dict(operation='edit_pou_labels', source_name=program['name'], declarations=native_rows(local_rows)))
    if not selected:
        command.update(normalized['task_binding'], scope_mode='existing_task', declarations=native_rows(declarations))
    commands.append(command)
    commands.extend({'operation': name} for name in ('compile', 'save', 'reopen'))
    if live:
        from application.live_engineering import normalize_live_plan
        plan = normalize_live_plan(dict(schema_version=1, mode='live', commands=commands), plc_model=plc_model)
    else:
        plan = normalize_native_plan(dict(schema_version=1, commands=commands), plc_model=plc_model)
    return dict(candidate=normalized, plan=plan, inspection=report)


def model_project_context(context):
    """Only authoring context; native storage/options bytes never reach a model."""
    def declarations(rows):
        return [{key: row[key] for key in ('name', 'data_type', 'class_name', 'device', 'initial_value') if key in row} for row in rows]
    return dict(cpu=context['cpu'], selected_source=context.get('selected_source'),
        tasks=context['tasks'], resources=context['resources'],
        globals=[dict(name=g['name'], source=g['source'], declarations=declarations(g['declarations'])) for g in context['globals']],
        pous=[dict(name=p['name'], kind=p['kind'], language=p['language'], declarations=declarations(p['declarations']),
                   task_bindings=p.get('task_bindings', []),
                   **({'return_type': p['return_type']} if p.get('kind') == 'function' else {}),
                   **({'body': p['st_text']} if p.get('program') == context.get('selected_source') and p.get('language') == 'st' else {})) for p in context['pous']],
        structures=[dict(name=s['name'], members=declarations(s['members'])) for s in context['structures']])


def _json_projection(value):
    if isinstance(value, bytes):
        return {'bytes_base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {key: _json_projection(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_projection(item) for item in value]
    return value


def _source_snapshot(raw, *, program_name, plc_model):
    if not isinstance(raw, (bytes, bytearray)) or not 0 < len(raw) <= 30 * 1024 * 1024:
        raise ValueError('ST candidate requires saved GXW bytes within 30 MiB')
    if not isinstance(program_name, str) or not program_name:
        raise ValueError('ST candidate requires an explicit current logical POU')
    snapshot = st_program_snapshot(raw)
    selected = [row for row in snapshot['programs'] if row['program'] == program_name]
    project = project_context_snapshot(raw, program_name)
    if not selected:
        selected = [row for row in project['pous'] if row['program'] == program_name
                    and row['kind'] in ('function_block', 'function') and row['language'] == 'st'
                    and row['source_status'] == 'available']
    if len(selected) != 1:
        raise ValueError('ST candidate requires one selected current ST Program, FB or FUNCTION')
    program, = selected
    actual, expected = native_project_profile(snapshot['cpu']), native_project_profile(plc_model)
    if (not isinstance(plc_model, str) or actual is None or expected is None
            or not actual.program_supported or actual.cpu != expected.cpu):
        raise ValueError('ST candidate GXW CPU differs from its selected PLC model')
    bindings = program['task_bindings']
    if program['kind'] in ('function_block', 'function') and bindings:
        raise ValueError('An ST callable cannot acquire ordinary Program task membership')
    for binding in bindings:
        tasks = [task for task in snapshot['tasks']
                 if (task['resource'], task['task']) == (binding['resource'], binding['task'])]
        if len(tasks) != 1 or [row for row in tasks[0]['programs'] if row['line'] == binding['line']] != [
                dict(name=program['name'], line=binding['line'])]:
            raise ValueError('ST candidate task row does not bind the exact current Program owner')
    try:
        compiler_sources = native_validation_source_plan(raw, include_logical_names=True)
    except GXWFormatError as error:
        compiler_sources = dict(status='unverified', reason=str(error), native_compile='not_run')
    try:
        gui_configuration = _json_projection(project_gui_configuration(raw))
    except GXWFormatError as error:
        gui_configuration = dict(status='not_verified', reason=str(error))
    return dict(cpu=snapshot['cpu'], selected_program=program,
        task_scopes=dict(resources=snapshot['resources'], tasks=snapshot['tasks']),
        # Saved candidate readback must also retain readable declarations whose
        # address storage is outside the stricter visible editing contract.
        project=_json_projection(project),
        compiler_sources=compiler_sources, gui_configuration=gui_configuration)


def _source_binding(source):
    program = source['selected_program']
    return dict(schema_version=1, cpu=source['cpu'], program=program['program'],
                owner=program['name'], kind=program['kind'], language='st',
                task_bindings=program['task_bindings'],
                **({'return_type': program['return_type']} if program['kind'] == 'function' else {}))


def validate_candidate(artifacts, *, plc_model=None, program_name=None, metadata=None):
    """Re-read the exact GXW and reject a foreign body, task or metadata view."""
    if not isinstance(artifacts, dict) or not {'gxw', 'st', 'write_report'} <= artifacts.keys():
        raise ValueError('ST native candidate is missing required saved-source artifacts')
    report = json.loads(artifacts['write_report'])
    if (not isinstance(report, dict) or set(report) != {'schema_version', 'operation', 'plc_model', 'source'}
            or type(report['schema_version']) is not int or report['schema_version'] != 1
            or report['operation'] != 'saved_st_program_projection'
            or not isinstance(report['source'], dict)):
        raise ValueError('ST native candidate has no current saved-source report')
    expected_model = plc_model if plc_model is not None else report['plc_model']
    selection = report['source'].get('selected_program')
    if not isinstance(selection, dict):
        raise ValueError('ST native candidate report has no selected ordinary Program')
    selected = program_name if program_name is not None else selection.get('program')
    source = _source_snapshot(artifacts['gxw'], program_name=selected, plc_model=expected_model)
    if report['plc_model'] != expected_model or report['source'] != source:
        raise ValueError('ST native candidate source or task projection differs from the GXW')
    if artifacts['st'] != source['selected_program']['st_text'].encode('utf-8'):
        raise ValueError('ST candidate text differs from its exact saved GXW Program body')
    if metadata is not None:
        if (not isinstance(metadata, dict) or metadata.get('target_mode') != 'st'
                or metadata.get('plc_model') != expected_model or metadata.get('program_name') != selected
                or not isinstance(metadata.get('validation'), dict)
                or metadata['validation'].get('source_binding') != _source_binding(source)):
            raise ValueError('ST native candidate metadata differs from its saved source binding')
    return source


def bind_verification(metadata, source, *, confirmed_spec=None, compile_status='unverified', native_receipt=None):
    """Rebuild checks from saved bytes; callers supply already-verified receipts."""
    from plc.st_validation import inspect_st_program
    identity = (native_receipt or {}).get('identity') or {}
    project_context = {**source['project'], **({'software_version': identity['software_version']}
                                             if identity.get('software_version') else {})}
    inspection = inspect_st_program(source['selected_program']['st_text'], plc_model=metadata['plc_model'],
        project_context=project_context, confirmed_spec=confirmed_spec,
        hardware_context=(confirmed_spec or {}).get('hardware_profile'))
    inspection['native_compile'] = compile_status if compile_status in {'passed', 'failed'} else 'unverified'
    inspection['evidence']['source_binding'] = _source_binding(source)
    if native_receipt:
        identity = native_receipt.get('identity') or {}
        inspection['evidence']['native_compile'] = dict(status=inspection['native_compile'],
            revision=native_receipt.get('data_revision', native_receipt.get('revision')),
            **{k: identity[k] for k in ('software_version', 'cpu_menu', 'project_path') if k in identity})
        from plc.st_validation import review_native_warnings
        warnings = review_native_warnings(source['selected_program']['st_text'], inspection, native_receipt,
            owner=source['selected_program']['name'], plc_model=metadata['plc_model'], project_context=project_context)
        inspection['native_warning_review'] = warnings
        if warnings['status'] != 'passed':
            inspection['semantic'] = ('failed' if warnings['status'] == 'failed' or inspection['semantic'] == 'failed'
                                      else 'unverified')
            unresolved = [check for check in warnings['checks'] if check['status'] != 'passed']
            for check in unresolved or [warnings]:
                offset = (check.get('source_offsets') or [0])[0]
                before = source['selected_program']['st_text'][:offset]
                inspection['findings'].append(dict(code='ST_NATIVE_WARNING',
                    severity='error' if check['status'] == 'failed' else 'unverified',
                    message=check.get('reason', 'native_warning_requires_review'), offset=offset,
                    line=before.count('\n') + 1, column=len(before.rsplit('\n', 1)[-1]) + 1,
                    native_code=check.get('code'), native_row=check.get('native_row')))
            inspection['counts'] = {key: sum(f['severity'] == key for f in inspection['findings'])
                                    for key in ('error', 'warning', 'unverified')}
            inspection['status'] = ('failed' if inspection['status'] == 'failed' or inspection['counts']['error']
                                    else 'unverified')
            inspection['valid'] = False
    metadata['semantic_validation'] = inspection
    metadata['validation'].update({key: inspection[key] for key in ('syntax', 'semantic', 'native_compile', 'behavior')})
    return inspection


def prepare_candidate(output, *, imported, program_name, plc_model, summary='ST 原生工程候选', confirmed_spec=None,
                      compile_status='unverified', native_receipt=None):
    """Project a selected saved ST owner; native acceptance remains separate."""
    raw = bytes(imported) if isinstance(imported, (bytes, bytearray)) else imported
    source = _source_snapshot(raw, program_name=program_name, plc_model=plc_model)
    report = dict(schema_version=1, operation='saved_st_program_projection', plc_model=plc_model, source=source)
    files = {'gxw': ('program.gxw', raw),
             'st': ('program.st', source['selected_program']['st_text'].encode('utf-8')),
             'write_report': ('write_report.json', json_bytes(report))}
    metadata = dict(target_mode='st', plc_model=plc_model, program_name=program_name, summary=summary,
        validation=dict(status='source_preserved', source_readback='matched',
            task_membership=('source_bound' if source['selected_program']['task_bindings'] else 'unassigned')
                if source['selected_program']['kind'] == 'program' else 'not_applicable',
            source_binding=_source_binding(source), native_save='not_run', native_source_save_required=False,
            gx_compile='not_run', execution='not_run', gui_configuration=source['gui_configuration']['status'],
            messages=['已读取保存工程中的 ST 正文、声明和对象归属；Program 任务绑定按来源保留，编译与运行分别验证。']))
    bind_verification(metadata, source, confirmed_spec=confirmed_spec, compile_status=compile_status, native_receipt=native_receipt)
    validate_candidate({name: data for name, (_path, data) in files.items()}, plc_model=plc_model,
                       program_name=program_name, metadata=metadata)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, (relative, data) in files.items():
        (output / relative).write_bytes(data)
        artifacts[name] = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest()}
    return dict(target_mode='st', plc_model=plc_model, staging_dir=str(output), artifacts=artifacts, metadata=metadata)
