"""Imported SFC source candidates in the shared version/proposal workflow.

Child records are vendor source bytes; graph edits use the original converter.
Native acceptance and PLC execution remain separate evidence.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import tempfile

from gxw.models import GXWFormatError
from gxw.sfc_pou import read_sfc_project_context
from .fbd import decode_upload, json_bytes, _native_workspace
from .workspace import ConflictError, atomic_json, canonical_hash, read_json, record_id


def read_snapshot(raw, program_name=None, *, native_graph=None):
    from gxw.native_diagnostics import native_code_reader_scope

    context = read_sfc_project_context(raw, program_name)
    scope = native_code_reader_scope(context.project['cpu'])
    return {'programs': list(context.programs),
            'model': context.object_model(token_profile=scope['profile'] if scope else None, native_graph=native_graph),
            'gx_compile': 'not_run'}


def _child_edits(edits):
    if not isinstance(edits, list) or not edits:
        raise GXWFormatError('SFC 编辑需要现有动作或转换条件的子程序记录。')
    result = {}
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'kind', 'name', 'records_hex'}
                or edit['kind'] not in ('zoom', 'transition') or not isinstance(edit['name'], str)
                or not isinstance(edit['records_hex'], str) or len(edit['records_hex']) > 16 * 1024 * 1024):
            raise GXWFormatError('SFC 子程序编辑需要 kind、name 和完整 records_hex。')
        identity = edit['kind'], edit['name']
        if identity in result:
            raise GXWFormatError('SFC 子程序编辑身份重复。')
        try:
            result[identity] = bytes.fromhex(edit['records_hex'])
        except ValueError as error:
            raise GXWFormatError('SFC 子程序记录不是十六进制字节。') from error
    return result


def validate_candidate(artifacts):
    if not {'gxw', 'sfc', 'write_report'}.issubset(artifacts):
        raise ValueError('SFC candidate is missing required artifacts')
    model = json.loads(artifacts['sfc'])
    graph = json.loads(artifacts['write_report']).get('sfc_graph')
    if read_snapshot(artifacts['gxw'], model['program'], native_graph=graph)['model'] != model:
        raise ValueError('SFC projection does not match the GXW project')


def prepare_candidate(output, *, imported=None, baseline=None, program_name=None,
                      child_edits=None, graph_edits=None, native_graph=None, summary='SFC 源码候选'):
    from gxw.project_writer import prepare_sfc_project_write

    if imported is not None:
        if baseline is not None or child_edits is not None or graph_edits is not None:
            raise ValueError('SFC 导入与子程序编辑分别执行。')
        raw, report = bytes(imported), {'operation': 'import', 'modified_objects': []}
    else:
        if baseline is None:
            raise ValueError('SFC 编辑需要已有 GXW 基础版本。')
        context = read_sfc_project_context(baseline, program_name)
        if child_edits is None and graph_edits is None:
            raise GXWFormatError('SFC 编辑需要子程序记录、动作引用或跳转目标。')
        from gxw.sfc_pou import resolve_sfc_graph_edits
        graph_edits = resolve_sfc_graph_edits(context, native_graph, graph_edits)
        prepared = prepare_sfc_project_write(baseline, context.program_name, context.source,
                                             {} if child_edits is None else _child_edits(child_edits))
        # Use the original workspace for preview and save, including unsigned
        # containers. Conversion/checking remain separate operations.
        with _native_workspace() as workspace:
            result = (workspace.save(prepared, timeout=None, graph_edits=graph_edits) if graph_edits is not None
                      else workspace.save(prepared, timeout=None))
            if graph_edits is not None and any(row.get('kind') == 'rebuild' for row in graph_edits):
                # Original symbol writers use a hot grid; saving may compact
                # columns. Publish the actual saved project's cold getter grid.
                result.report['sfc_graph_write'] = copy.deepcopy(result.report['sfc_graph'])
                result.report['sfc_graph'] = workspace.read_sfc_graph(
                    result.data, result.saved_object_name(context.program_name), timeout=None)
        raw, report = result.data, result.report
        program_name = result.saved_object_name(context.program_name)
        if graph_edits is not None:
            native_graph = copy.deepcopy(report['sfc_graph'])
            current = read_sfc_project_context(raw, program_name)
            region = current.source.layout['graph']
            native_graph.update(logical_name=current.program_name,
                source_body_hex=current.source.raw[region['offset']:region['offset'] + region['size']].hex())
        elif native_graph is not None and context.native_graph(native_graph) is not None:
            current = read_sfc_project_context(raw, program_name)
            region = current.source.layout['graph']
            body = current.source.raw[region['offset']:region['offset'] + region['size']]
            if native_graph['source_body_hex'] == body.hex():
                native_graph = copy.deepcopy(native_graph)
                native_graph['logical_name'] = current.program_name
        if native_graph is not None:
            report['sfc_graph'] = native_graph
    snapshot = read_snapshot(raw, program_name, native_graph=report.get('sfc_graph'))
    model = snapshot['model']
    files = {'gxw': ('program.gxw', raw), 'sfc': ('sfc.json', json_bytes(model)),
             'write_report': ('write_report.json', json_bytes(report))}
    validate_candidate({name: data for name, (_path, data) in files.items()})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, (relative, data) in files.items():
        (output / relative).write_bytes(data)
        artifacts[name] = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest()}
    metadata = {'target_mode': 'sfc', 'plc_model': model['cpu'], 'program_name': model['program'],
                'summary': summary, 'validation': {'status': 'source_preserved', 'gx_compile': 'not_run',
                    'native_save': report.get('validation', {}).get('native_save', 'not_run'),
                    'native_source_save_required': imported is None,
                    'position_resolution': model['graph']['position_resolution'], 'issues': model['issues'],
                    'messages': ['已读取当前 SFC 图形、注册关系及子程序；原生编译和检查分别执行。']}}
    return {'target_mode': 'sfc', 'plc_model': model['cpu'], 'staging_dir': str(output),
            'artifacts': artifacts, 'metadata': metadata}


def source_diff(before, after):
    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key != 'source_offset'}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    a, b = stable(before), stable(after)
    original = {(row['kind'], row['name']): stable(row) for row in (before or {}).get('children', [])}
    modified = [{'kind': row['kind'], 'name': row['name']} for row in after['children']
                if original.get((row['kind'], row['name'])) != stable(row)]
    lines = unified_diff(json_bytes(a).decode().splitlines(True), json_bytes(b).decode().splitlines(True),
                         fromfile='base/sfc', tofile='candidate/sfc')
    return {'kind': 'sfc', 'has_changes': a != b, 'modified_children': modified, 'unified_diff': ''.join(lines)}


class SFCService:
    def __init__(self, workbench):
        self.workbench = workbench
        self._graph_reads = {}

    def _source(self, project_id, version_id, program=None):
        wb = self.workbench
        version = wb.projects.raw_version(project_id, version_id)
        raw = wb.projects.artifact(project_id, version_id, 'gxw').read_bytes()
        if program is None:
            program = version.get('program_name') if version.get('target_mode') == 'sfc' else None
        selected = read_sfc_project_context(raw, program).program_name
        graph = self._graph_reads.get((project_id, version_id, selected)) or wb.projects.sfc_graph(project_id, version_id)
        return raw, selected, graph

    def read(self, project_id, version_id, program=None, native=False):
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            raw, selected, graph = self._source(project_id, version_id, program)
            if native and read_snapshot(raw, selected, native_graph=graph)['model']['graph']['position_resolution'] != 'native-source-bound':
                with _native_workspace() as workspace:
                    graph = workspace.read_sfc_graph(raw, selected, timeout=None)
                self._graph_reads[(project_id, version_id, selected)] = graph
            return read_snapshot(raw, selected, native_graph=graph)

    def preview(self, project_id, version_id, child_edits=None, program=None, graph_edits=None):
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            raw, selected, graph = self._source(project_id, version_id, program)
            with tempfile.TemporaryDirectory() as directory:
                payload = prepare_candidate(directory, baseline=raw, program_name=selected, child_edits=child_edits,
                                            graph_edits=graph_edits, native_graph=graph)
                model = json.loads((Path(directory) / payload['artifacts']['sfc']['path']).read_bytes())
                return {'target_mode': 'sfc', 'program': model, 'validation': payload['metadata']['validation'],
                        'diff': source_diff(read_snapshot(raw, selected, native_graph=graph)['model'], model), 'gx_compile': 'not_run'}

    def propose(self, command):
        wb = self.workbench
        wb.writable()
        record_id(command['request_id'])
        digest = canonical_hash(command)
        key = hashlib.sha256(command['request_id'].encode()).hexdigest()
        saved_path = wb.state_dir / 'sfc_commands' / (key + '.json')
        with wb.lock.thread_lock:
            if saved_path.exists():
                saved = read_json(saved_path)
                if saved['command_hash'] != digest:
                    raise ConflictError('Request ID is already bound to another SFC command')
                return wb.proposals.get(saved['proposal_id'])
            project_id, base_id = command['project_id'], command.get('version_id')
            project = wb.projects.raw_project(project_id)
            if base_id is None and project.get('active_version_id'):
                raise ConflictError('Select the current base version before creating an SFC candidate')
            if command['operation'] == 'import':
                if command.get('child_edits') is not None or command.get('graph_edits') is not None:
                    raise ValueError('SFC 导入不接受子程序编辑。')
                arguments = {'imported': decode_upload(command.get('data_base64', '')),
                             'program_name': command.get('program'), 'summary': '导入已有 SFC 工程'}
            elif command['operation'] == 'edit':
                if base_id is None or command.get('data_base64') is not None:
                    raise ValueError('SFC 编辑需要指定基础版本，不接受导入字节。')
                raw, selected, graph = self._source(project_id, base_id, command.get('program'))
                arguments = {'baseline': raw, 'program_name': selected, 'child_edits': command.get('child_edits'),
                             'graph_edits': command.get('graph_edits'), 'native_graph': graph}
            else:
                raise ValueError('SFC 目前支持已有工程的导入及子程序编辑。')
            wb.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=wb.state_dir, prefix='sfc-') as directory:
                payload = prepare_candidate(directory, **arguments)
                payload['_confirmed_spec'] = copy.deepcopy(project.get('confirmed_spec'))
                payload = wb._with_candidate_diff(project_id, base_id, payload)
                proposal = wb.proposals.create('accept_local', project_id, payload,
                    public_summary={'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
                                    'diff': wb._diff_summary(payload['_preview_diff'])},
                    base_version_id=base_id, request_id='sfc_' + key)
            proposal = wb._save_local_proposal(proposal)
            atomic_json(saved_path, {'command_hash': digest, 'proposal_id': proposal['id']})
            return proposal
