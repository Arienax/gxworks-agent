"""Saved ordinary ST Program artifacts in the shared candidate transaction.

GXW is the source of declarations, bodies and task ownership. This module only
projects saved bytes; it never generates a project from a standalone ST file or
opens an offline native workspace to replace visible-session evidence.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from gxw.fb_authoring import st_program_snapshot
from gxw.native_diagnostics import native_validation_source_plan
from gxw.project_metadata import project_gui_configuration
from gxw.visible_engineering import source_projection
from gxworks2.project_profiles import native_project_profile
from .fbd import json_bytes


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
    if len(selected) != 1:
        raise ValueError('ST candidate requires the selected current ordinary ST Program')
    program, = selected
    actual, expected = native_project_profile(snapshot['cpu']), native_project_profile(plc_model)
    if (not isinstance(plc_model, str) or actual is None or expected is None
            or not actual.program_supported or actual.cpu != expected.cpu):
        raise ValueError('ST candidate GXW CPU differs from its selected PLC model')
    bindings = program['task_bindings']
    if len(bindings) != 1:
        raise ValueError('ST candidate requires one current source-bound task membership')
    binding, = bindings
    tasks = [task for task in snapshot['tasks']
             if (task['resource'], task['task']) == (binding['resource'], binding['task'])]
    if len(tasks) != 1 or [row for row in tasks[0]['programs'] if row['line'] == binding['line']] != [
            dict(name=program['name'], line=binding['line'])]:
        raise ValueError('ST candidate task row does not bind the exact current Program owner')
    return dict(cpu=snapshot['cpu'], selected_program=program,
        task_scopes=dict(resources=snapshot['resources'], tasks=snapshot['tasks']),
        project=_json_projection(source_projection(raw)),
        compiler_sources=native_validation_source_plan(raw, include_logical_names=True),
        gui_configuration=_json_projection(project_gui_configuration(raw)))


def _source_binding(source):
    program = source['selected_program']
    return dict(schema_version=1, cpu=source['cpu'], program=program['program'],
                owner=program['name'], kind='program', language='st',
                task_bindings=program['task_bindings'])


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


def prepare_candidate(output, *, imported, program_name, plc_model, summary='ST 原生工程候选'):
    """Project a selected saved Program; native acceptance remains separate."""
    raw = bytes(imported) if isinstance(imported, (bytes, bytearray)) else imported
    source = _source_snapshot(raw, program_name=program_name, plc_model=plc_model)
    report = dict(schema_version=1, operation='saved_st_program_projection', plc_model=plc_model, source=source)
    files = {'gxw': ('program.gxw', raw),
             'st': ('program.st', source['selected_program']['st_text'].encode('utf-8')),
             'write_report': ('write_report.json', json_bytes(report))}
    metadata = dict(target_mode='st', plc_model=plc_model, program_name=program_name, summary=summary,
        validation=dict(status='source_preserved', source_readback='matched', task_membership='source_bound',
            source_binding=_source_binding(source), native_save='not_run', native_source_save_required=False,
            gx_compile='not_run', execution='not_run', gui_configuration=source['gui_configuration']['status'],
            messages=['已读取保存工程中的普通 ST Program、声明和任务绑定；编译及运行验证分别执行。']))
    validate_candidate({name: data for name, (_path, data) in files.items()}, plc_model=plc_model,
                       program_name=program_name, metadata=metadata)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, (relative, data) in files.items():
        (output / relative).write_bytes(data)
        artifacts[name] = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest()}
    return dict(target_mode='st', plc_model=plc_model, staging_dir=str(output), artifacts=artifacts, metadata=metadata)
