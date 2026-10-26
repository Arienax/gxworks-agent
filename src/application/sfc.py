"""Imported SFC source candidates in the shared version/proposal workflow.

Child and graph edits use source-bound Core codecs without a native process.
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
    from gxw.token_profiles import source_token_profile

    context = read_sfc_project_context(raw, program_name)
    profile = source_token_profile(context.project['cpu'], language='sfc')
    return {'programs': list(context.programs),
            'model': context.object_model(token_profile=profile, native_graph=native_graph),
            'gx_compile': 'not_run'}


def validate_candidate(artifacts):
    if not {'gxw', 'sfc', 'write_report'}.issubset(artifacts):
        raise ValueError('SFC candidate is missing required artifacts')
    model = json.loads(artifacts['sfc'])
    graph = json.loads(artifacts['write_report']).get('sfc_graph')
    if read_snapshot(artifacts['gxw'], model['program'], native_graph=graph)['model'] != model:
        raise ValueError('SFC projection does not match the GXW project')


def prepare_candidate(output, *, imported=None, baseline=None, program_name=None,
                      child_edits=None, graph_edits=None, native_graph=None, summary='SFC 源码候选',
                      device_comments=None, comment_table=None, declaration_edits=None, save_policy='checked'):
    from gxw.project_writer import write_project_replacements
    if save_policy not in ('draft', 'checked'):
        raise GXWFormatError('save_policy must be draft or checked')

    if imported is not None:
        if (baseline is not None or child_edits is not None or graph_edits is not None
                or device_comments is not None or comment_table is not None or declaration_edits is not None):
            raise ValueError('SFC 导入与子程序编辑分别执行。')
        raw, report = bytes(imported), {'operation': 'import', 'modified_objects': []}
    else:
        if baseline is None:
            raise ValueError('SFC 编辑需要已有 GXW 基础版本。')
        context = read_sfc_project_context(baseline, program_name)
        if child_edits is None and graph_edits is None and not device_comments and not declaration_edits:
            raise GXWFormatError('SFC 编辑需要子程序、图形、声明或注释修改。')
        from gxw.sfc_offline import edit_sfc_program
        from gxw.offline_project import read_offline_project
        source, graph_report = context.source.raw, None
        if graph_edits is not None or child_edits is not None:
            source, graph_report = edit_sfc_program(context, graph_edits=graph_edits,
                                                   child_edits=child_edits, graph=native_graph, save_policy=save_policy)
        project = read_offline_project(baseline)
        replacements = {context.program_name: (project.mapping[context.program_name], context.source.raw, source)}
        from gxw.offline_authoring import prepare_declaration_edits
        prepared, checks = prepare_declaration_edits(baseline, declaration_edits,
            source_replacements={context.program_name: source},
            expected_sources={context.program_name: context.source.raw}, save_policy=save_policy)
        if prepared is not None:
            replacements.update(prepared.replacements)
        result = write_project_replacements(baseline, replacements,
            objects=prepared.objects if prepared is not None else None,
            device_comments=device_comments, comment_table=comment_table)
        result.report['source_checks'] = checks
        from gxw.offline_edit import _artifact_state
        result.report.update(save_policy=save_policy, artifact_state=_artifact_state(checks))
        if graph_report is not None:
            result.report['offline_sfc_graph'] = graph_report
        raw, report = result.data, result.report
        program_name = result.saved_object_name(context.program_name)
        if graph_edits is not None:
            # The old coordinate cache describes the old source. Offline graph
            # decoding stays separate from a native getter result.
            native_graph = None
        elif native_graph is not None and context.native_graph(native_graph) is not None:
            current = read_sfc_project_context(raw, program_name)
            region = current.source.layout['graph']
            body = current.source.raw[region['offset']:region['offset'] + region['size']]
            if native_graph['source_body_hex'] == body.hex():
                native_graph = copy.deepcopy(native_graph)
                native_graph['logical_name'] = current.program_name
            else:
                native_graph = None
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
                    'native_source_save_required': False, 'write_backend': 'offline',
                    'offline_write': 'source_preserved' if imported is not None else 'passed',
                    'compile_state': 'unverified' if imported is not None else report.get('validation', {}).get('compile_state', 'stale'),
                    'source_checks': report.get('source_checks', []),
                    'save_policy': save_policy, 'artifact_state': report.get('artifact_state', 'draft'),
                    **({'invalidated_results': report['invalidated_results']} if 'invalidated_results' in report else {}),
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
        graph = self._graph_reads.get((project_id, version_id, selected)) or wb.projects.sfc_graph(project_id, version_id, selected)
        return raw, selected, graph

    def read(self, project_id, version_id, program=None, native=False):
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            raw, selected, graph = self._source(project_id, version_id, program)
            if native and read_snapshot(raw, selected, native_graph=graph)['model']['graph']['position_resolution'] != 'native-source-bound':
                with _native_workspace() as workspace:
                    graph = workspace.read_sfc_graph(raw, selected, timeout=None)
                wb.projects.record_sfc_graph(project_id, version_id, selected, graph)
                self._graph_reads[(project_id, version_id, selected)] = graph
            return read_snapshot(raw, selected, native_graph=graph)

    def preview(self, project_id, version_id, child_edits=None, program=None, graph_edits=None,
                declaration_edits=None, *, save_policy='checked'):
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            raw, selected, graph = self._source(project_id, version_id, program)
            with tempfile.TemporaryDirectory() as directory:
                payload = prepare_candidate(directory, baseline=raw, program_name=selected, child_edits=child_edits,
                                            graph_edits=graph_edits, native_graph=graph, declaration_edits=declaration_edits,
                                            save_policy=save_policy)
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
                if (command.get('child_edits') is not None or command.get('graph_edits') is not None
                        or command.get('declaration_edits') is not None):
                    raise ValueError('SFC 导入不接受子程序编辑。')
                arguments = {'imported': decode_upload(command.get('data_base64', '')),
                             'program_name': command.get('program'), 'summary': '导入已有 SFC 工程'}
            elif command['operation'] == 'edit':
                if base_id is None or command.get('data_base64') is not None:
                    raise ValueError('SFC 编辑需要指定基础版本，不接受导入字节。')
                raw, selected, graph = self._source(project_id, base_id, command.get('program'))
                arguments = {'baseline': raw, 'program_name': selected, 'child_edits': command.get('child_edits'),
                             'graph_edits': command.get('graph_edits'), 'native_graph': graph,
                             'declaration_edits': command.get('declaration_edits'),
                             'device_comments': command.get('device_comments'), 'comment_table': command.get('comment_table'),
                             'save_policy': command.get('save_policy', 'checked')}
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
