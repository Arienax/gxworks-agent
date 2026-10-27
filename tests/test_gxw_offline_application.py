"""Application/MCP offline GXW acceptance over private native source controls."""
from __future__ import annotations

import base64
from copy import deepcopy
import csv
import ctypes
from functools import lru_cache
import io
from pathlib import Path
import subprocess
import struct
import zipfile

import pytest

from agent_runtime.messages import ToolCall
from application.engineering_session import EngineeringSession
from application.program_inspection import ProgramInspectionService
from application.workbench import WorkbenchService
from gxw.declarations import parse_declarations, serialize_label
from gxw.integrity import read_project_hdb
from gxw.models import GXWFormatError
from gxw.offline_project import read_offline_project, read_ordered_program
from gxw.sfc_pou import read_sfc_project_context
from gxw.text_pou import parse_st_pou
from plc.device_identity import canonical_device, canonical_operand
from plc.ir import canonical_sha256
from tools.offline_inputs import private_path
from plc.target_identity import Q_CPUS, PARAMETER_ONLY


WAREHOUSE = 'research/fixtures/gxw-offline/warehouse/'


@lru_cache(maxsize=1)
def q_native_origin_projects():
    import json
    manifest = json.loads(private_path('research/results/gxworks2-native-cpu-send-20261011.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(private_path(manifest['evidence'])) as archive:
        result = {}
        for case in manifest['cases']:
            if case['cpu'] not in Q_CPUS or not case['program_supported']:
                continue
            parent = case['raw'].rsplit('/', 1)[0] + '/'
            name = next(name for name in archive.namelist() if name.startswith(parent) and name.endswith('.gxw'))
            result[case['cpu']] = archive.read(name)
        assert set(result) == set(Q_CPUS) - PARAMETER_ONLY
        return result


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
def test_all_q_native_origin_import_edit_delivery_reimport_and_second_edit(workbench, no_native, cpu):
    raw = q_native_origin_projects()[cpu]
    imported = workbench.gxw_files.import_project(data_base64=encoded(raw), filename=cpu.replace('/', '-') + '.gxw')
    pid, vid = imported['project_id'], imported['version_id']
    project = workbench.projects.project(pid)
    assert project['native_project_type'] == 'simple' and project['use_labels'] is False
    for turn, (old, new) in enumerate([('M0', 'M2'), ('M2', 'M3')], 1):
        context = workbench.projects.tool_context(pid, vid)
        assert context.plc_model == cpu
        current_raw = version_bytes(workbench, pid, vid)
        current = read_ordered_program(read_offline_project(current_raw))
        target = next(row for row in current['source_records'] if row.get('op') == 'LD' and row['args'] == [old])
        command = {'project_id': pid, 'version_id': vid, 'name': 'patch_program', 'call_id': f'q-edit-{turn}',
                   'arguments': {'patch': {'base_revision': context.program_ir['revision'],
                       'base_ir_sha256': canonical_sha256(context.program_ir), 'save_policy': 'draft',
                       'instruction_edits': [{'operation': 'replace', 'record_index': target['record_index'],
                           'expected': {'op': 'LD', 'args': [old]}, 'instructions': [{'op': 'LD', 'args': [new]}]}]}}}
        response = workbench.agent_call(command)
        assert not response['is_error'], response
        proposal = workbench.proposals.get(response['proposal_id'])
        if proposal['status'] == 'pending':
            proposal = workbench.decide(proposal['id'], 'accept')['proposal']
        assert proposal['status'] == 'accepted', proposal
        saved = version_bytes(workbench, pid, proposal['result']['version_id'])
        assert read_ordered_program(read_offline_project(saved))['listing'].instructions[0].args == (new,)
        assert_untouched_streams(read_offline_project(current_raw), read_offline_project(saved),
                                {current['selected']['source_name']})
        again = workbench.gxw_files.import_project(data_base64=encoded(saved), filename=f'q-{turn}.gxw')
        pid, vid = again['project_id'], again['version_id']
        assert version_bytes(workbench, pid, vid) == saved
    assert raw == q_native_origin_projects()[cpu] and not no_native


@pytest.mark.parametrize('tamper', [False, True])
def test_older_ordered_access_projection_is_restored_without_rewriting_its_artifact(workbench, no_native, monkeypatch, tamper):
    from gxw.offline_project import edit_ladder_project
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    raw = q_native_origin_projects()['Q03UDV']
    project = read_offline_project(raw)
    ordered = read_ordered_program(project)
    end = next(row for row in ordered['source_records'] if row.get('op') == 'END')
    raw = edit_ladder_project(raw, ordered['selected']['source_name'], [dict(operation='insert_before',
        record_index=end['record_index'], expected=dict(op='END', args=[]), instructions=[dict(op='+', args=['K1', 'D20'])])],
        save_policy='draft').data
    with monkeypatch.context() as old:
        old.setattr(DEFAULT_INSTRUCTION_REGISTRY, '_source_forms', {})
        imported = workbench.gxw_files.import_project(data_base64=encoded(raw), filename='previous.gxw')
    pid, vid = imported['project_id'], imported['version_id']
    path = workbench.projects.artifact(pid, vid, 'ir')
    saved = path.read_bytes()
    if tamper:
        import json
        from application.workspace import ConflictError
        changed = json.loads(saved)
        changed['networks'][0]['writes'] = ['M999']
        path.write_text(json.dumps(changed), encoding='utf-8')
        with pytest.raises(ConflictError, match='Version IR changed after validation'):
            workbench.projects.verified_program(pid, vid)
        return
    program = workbench.projects.verified_program(pid, vid)
    assert any('D20' in network['writes'] for network in program['networks'])
    response_info = workbench.agent_call(dict(project_id=pid, version_id=vid, call_id='legacy-info',
        name='get_current_program_info', arguments={}))
    assert not response_info['is_error'], response_info
    from agent_runtime.program_tools import _get_current_program_info
    info = _get_current_program_info(workbench.projects.tool_context(pid, vid), {})
    assert info['ir_sha256'] == canonical_sha256(program)
    assert info['ir_sha256'] != workbench.projects.raw_version(pid, vid)['ir_sha256']
    current = read_ordered_program(read_offline_project(raw))
    target = next(row for row in current['source_records'] if row.get('op') == '+')
    response = workbench.agent_call(dict(project_id=pid, version_id=vid, call_id='legacy-edit', name='patch_program',
        arguments=dict(patch=dict(base_revision=program['revision'],
            save_policy='draft', instruction_edits=[dict(operation='replace', record_index=target['record_index'],
                expected=dict(op='+', args=['K1', 'D20']), instructions=[dict(op='+', args=['K2', 'D20'])])]))))
    assert not response['is_error'], response
    proposal = workbench.proposals.get(response['proposal_id'])
    if proposal['status'] == 'pending':
        proposal = workbench.decide(proposal['id'], 'accept')['proposal']
    assert proposal['status'] == 'accepted', proposal
    assert path.read_bytes() == saved


def test_shared_ladder_source_automatically_draws_all_warehouse_networks_with_bound_operands(no_native):
    import json
    import xml.etree.ElementTree as ET
    from application.gxw_files import read_source
    from plc.ir import ir_to_ladder
    raw = private_path(WAREHOUSE + 'warehouse.gxw').read_bytes()
    view = read_source(raw)
    assert len(view['source_records']) == 155
    assert all('operand_forms' in row for row in view['source_records'] if 'kind' not in row)
    ns = {'s': 'http://www.w3.org/2000/svg'}
    drawing = ET.fromstring(view['svg'])
    assert drawing.get('data-source-format') == 'ordered-ladder'
    networks = drawing.findall('.//s:g[@class="source-network"]', ns)
    rungs = ir_to_ladder(view['model'])['rungs']
    assert len(networks) == len(rungs) == 9
    for network, rung in zip(networks, rungs):
        rows = rung['ordered_instructions']
        assert json.loads(network.find('s:metadata', ns).text) == rows
        positions = [int(node.get('data-source-index')) for node in network.iter()
                     if node.get('data-source-index') is not None]
        assert sorted(positions) == [index for index, row in enumerate(rows) if row['op'] not in {'ANB', 'ORB'}]
        assert len(set(positions)) == len(positions)
    assert len(ir_to_ladder(view['model'])['device_comments']) == 22
    assert view['native_required'] is False and not no_native


def encoded(raw):
    return base64.b64encode(raw).decode('ascii')


def mixed_transaction(raw):
    from gxw.object_model import read_project_context
    model = read_project_context(raw, 'POU_01.Program.pou').object_model()
    for node in model['nodes']:
        node['x'] += 8
    for wire in model['wires']:
        wire['start'][0] += 8
        wire['end'][0] += 8
    model['declaration_edits'] = {'POU_01.Labels.lh': {'upserts': [
        {'name': 'JointDraft', 'data_type': 'INT', 'class_name': 'VAR'}]}}
    return [dict(source_name='CalcOutput_ST.Program.pou', command=dict(st_text='Counter := ;')),
            dict(source_name='POU_01.Program.pou', command=dict(model=model))]


def test_mixed_project_transaction_keeps_draft_diagnostics_and_reviews_both_sources(no_native):
    from application.gxw_files import project_source_changes
    from gxw.offline_edit import edit_offline_project
    raw, _ = native_source('st')
    before = read_offline_project(raw)
    result = edit_offline_project(raw, mixed_transaction(raw))
    after = read_offline_project(result.data)
    assert parse_st_pou(after.streams['CalcOutput_ST.Program.pou']).text == 'Counter := ;'
    assert any(row.name == 'JointDraft' for row in parse_declarations(
        after.streams['POU_01.Labels.lh'], logical_name='POU_01.Labels.lh').rows)
    changes = project_source_changes(raw, result.data)
    assert {row['source_name'] for row in changes} == {'CalcOutput_ST.Program.pou', 'POU_01.Program.pou'}
    assert all(row['unified_diff'] for row in changes)
    assert result.report['artifact_state'] == 'draft'
    assert_untouched_streams(before, after, {'CalcOutput_ST.Program.pou', 'POU_01.Program.pou', 'POU_01.Labels.lh'})
    assert not no_native


def test_mcp_multi_source_edit_switches_projection_and_reopens_a_draft_for_repair(tmp_path, no_native):
    raw, logical = native_source('st')
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw), 'program': logical}, 'joint-import')['data']
        pid, base = imported['project_id'], imported['version_id']
        view = invoke(session, 'read_gxw_source', {'program': 'POU_01.Program.pou'}, 'joint-read')['data']
        assert view['language'] == 'fbd' and session.version_id == base
        result = invoke(session, 'edit_gxw_project', {'version_id': base, 'edits': mixed_transaction(raw),
            'program': 'POU_01.Program.pou'}, 'joint-edit')
        assert result['status'] == 'saved'
        saved = result['data']['version_id']
        assert saved != base and session.version_id == base
        # An explicitly pinned MCP version remains pinned until the client
        # selects the returned version; source viewing never changes it.
        invoke(session, 'select_project', {'project_id': pid, 'version_id': saved}, 'joint-select-saved')
        preview = session.service.proposal_preview(result['proposal_id'])
        assert preview['target_mode'] == 'fbd'
        assert {row['language'] for row in preview['diff']['source_changes']} == {'st', 'fbd'}
        current = invoke(session, 'read_gxw_source', {'program': logical}, 'joint-draft-read')['data']
        assert current['model']['st_text'] == 'Counter := ;'
        stale = session.invoke(ToolCall('joint-stale', 'edit_gxw_project', {'version_id': base,
            'edits': [dict(source_name=logical, command=dict(st_text='Counter := 17;'))]}))
        assert stale.is_error
        repaired = invoke(session, 'edit_gxw_project', {'version_id': saved, 'program': logical,
            'save_policy': 'checked', 'edits': [dict(source_name=logical, command=dict(st_text='Counter := 17;'))]}, 'joint-repair')
        final = repaired['data']['version_id']
        assert final != saved
        assert version_bytes(session.service, pid, base) == raw
        downloaded = version_bytes(session.service, pid, final)
        reread = read_offline_project(downloaded)
        assert parse_st_pou(reread.streams[logical]).text == 'Counter := 17;'
        reimported = invoke(session, 'import_gxw_project', {'data_base64': encoded(downloaded), 'program': logical}, 'joint-reimport')['data']
        assert version_bytes(session.service, reimported['project_id'], reimported['version_id']) == downloaded
    assert not no_native


@pytest.mark.parametrize('kind', ['st', 'fbd', 'sfc'])
def test_file_source_ref_reads_selected_language_and_checks_without_creating_workspace(tmp_path, no_native, kind):
    raw, logical = native_source(kind)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspected = invoke(session, 'inspect_gxw_project', {'data_base64': encoded(raw), 'program': logical}, 'inspect')['data']
        ref = inspected['source_ref']
        source = invoke(session, 'read_' + kind + '_project', {'source_ref': ref}, 'read-source')['data']
        if kind == 'st':
            assert source['programs'][0]['program'] == logical
            assert source['programs'][0]['st_text']
        else:
            assert source['model']['program'] == logical
        check = invoke(session, 'validate_current_program', {'source_ref': ref}, 'check-source')['data']
        assert check['runtime_verified'] is False
        assert check['analysis_source']['program_name'] == logical
        rejected = session.invoke(ToolCall('wrong-source', 'read_' + kind + '_project',
            {'source_ref': ref, 'program': 'Different.Program.pou'})).data
        assert rejected['ok'] is False and rejected['error']['code'] == 'SOURCE_MISMATCH'
        assert not session.workspace.exists()
    assert not no_native


@pytest.mark.parametrize('kind', ['ladder', 'st', 'fbd', 'sfc'])
@pytest.mark.parametrize('described', [False, True])
def test_uncovered_source_returns_inventory_and_reason_without_claiming_readable(tmp_path, no_native, kind, described):
    from gxw.project_writer import write_project_replacements
    from gxw.source_header import source_payload_offset
    raw, logical = native_source(kind)
    project = read_offline_project(raw)
    original = project.streams[logical]
    truncated = original[:source_payload_offset(original) + 1]
    if described:
        control = read_offline_project(private_path(
            'research/experiments/offline-sfc-20261021/described-source-pending-v2/described-input.gxw').read_bytes())
        header = control.streams['POU_01.Program.pou']
        truncated = header[:source_payload_offset(header) - 42] + truncated[source_payload_offset(original) - 42:]
    damaged = write_project_replacements(raw,
        {logical: (project.mapping[logical], original, truncated)}, invalidate=False).data
    inspection = ProgramInspectionService().inspect_gxw_project(data_base64=encoded(damaged), program=logical)
    assert inspection['source_ref'] is None
    assert inspection['selected_source']['readable'] is False
    assert inspection['selected_source']['offline_editable'] is False
    assert inspection['selected_source']['reason']
    assert any(row['code'] == 'uncovered_source_format' for row in inspection['issues'])
    assert inspection['declaration_tables']
    assert not no_native


def test_fresh_process_offline_import_edit_export_with_no_gxworks_discovery(tmp_path):
    import json
    import sys
    source = private_path(WAREHOUSE + 'warehouse.gxw')
    script = r'''
import ctypes, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'src'))
def forbidden(*args, **kwargs):
    raise AssertionError('native process or DLL requested during fresh offline flow')
subprocess.Popen = forbidden
subprocess.run = forbidden
ctypes.CDLL = forbidden
ctypes.WinDLL = forbidden
from gxworks2.finder import GXWorks2Finder
GXWorks2Finder.find_executable = lambda self: None
GXWorks2Finder.list_running = lambda self, **kwargs: []
from application.workbench import WorkbenchService
from gxw.offline_project import read_offline_project, read_ordered_program
from agent_runtime.plc_tools import build_default_tool_registry
source, workspace, state = map(Path, sys.argv[1:])
raw = source.read_bytes()
service = WorkbenchService(workspace, state, model_factory=lambda: (None, {}))
service.start()
try:
    imported = service.gxw_files.import_project(path=str(source))
    context = service.projects.tool_context(imported['project_id'], imported['version_id'])
    current = read_ordered_program(read_offline_project(raw))
    row = next(row for row in current['source_records'] if row.get('op') == 'AND<' and row.get('args') == ['D1','K55'])
    patch = {'instruction_edits':[{'operation':'replace','record_index':row['record_index'],
        'expected':{'op':row['op'],'args':row['args']},'instructions':[{'op':'AND<','args':['D1','K54']}]}],
        'device_comments':{'S0':'Fresh offline source save'}}
    result = build_default_tool_registry().call('patch_program', {'patch':patch}, context)
    assert result['ok'], result
    proposal = service.proposal_workflow.pending_proposal(result['data']['pending_action'], 'fresh-save', direct_request=True)
    exported = service.projects.artifact(imported['project_id'], proposal['result']['version_id'], 'gxw').read_bytes()
    after = read_ordered_program(read_offline_project(exported))
    assert len(after['listing'].instructions) == 155 and len(after['raw_networks']) == 9 and len(after['device_comments']) == 22
    assert after['device_comments']['S0'] == 'Fresh offline source save'
    assert source.read_bytes() == raw
    print(json.dumps({'instructions':155,'networks':9,'comments':22,'gxworks_discovered':False}))
finally:
    service.close()
'''
    result = subprocess.run([sys.executable, '-c', script, str(source), str(tmp_path / 'workspace'),
        str(tmp_path / 'state')], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['gxworks_discovered'] is False


@lru_cache(maxsize=None)
def native_source(kind):
    if kind == 'ladder':
        return private_path(WAREHOUSE + 'warehouse.gxw').read_bytes(), 'MAIN.Program.pou'
    if kind in ('st', 'fbd'):
        with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-visible-st-program-creation-20261017.zip')) as source:
            raw = source.read('015-task-replace/saved.gxw')
        return raw, 'CalcOutput_ST.Program.pou' if kind == 'st' else 'POU_01.Program.pou'
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-object-edit-20261013.zip')) as source:
        return source.read('shared-save/input.gxw'), 'Block.Program.pou'


def warehouse_oracle():
    rows = list(csv.reader(io.StringIO(private_path(WAREHOUSE + 'MAIN.csv').read_text(encoding='utf-16')), delimiter='\t'))
    instructions = []
    for row in rows[3:]:
        if len(row) < 4:
            continue
        if row[2]:
            instructions.append({'op': row[2], 'args': [canonical_operand(row[3])] if row[3] else []})
        elif row[3]:
            instructions[-1]['args'].append(canonical_operand(row[3]))
    comments = list(csv.reader(io.StringIO(private_path(WAREHOUSE + 'COMMENT.csv').read_text(encoding='utf-16')), delimiter='\t'))
    return instructions, {canonical_device(row[0]): row[1] for row in comments[2:] if row}


@pytest.fixture
def workbench(tmp_path):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state',
                               model_factory=lambda: (None, {'model': 'isolated-offline-test'}))
    service.start()
    try:
        yield service
    finally:
        service.close()


@pytest.fixture
def no_native(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError('offline GXW flow attempted a native process/library call')
    for module, name in ((subprocess, 'Popen'), (subprocess, 'run'), (ctypes, 'CDLL'), (ctypes, 'WinDLL')):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, forbidden)
    import application.fbd as fbd
    monkeypatch.setattr(fbd, '_native_candidate', forbidden)
    monkeypatch.setattr(fbd, '_native_workspace', forbidden)
    return attempts


def invoke(session, name, arguments, call_id):
    result = session.invoke(ToolCall(call_id, name, arguments))
    assert not result.is_error, result.data
    assert result.data['ok'], result.data
    if result.data.get('data', {}).get('context_schema_version') == 3:
        from application.read_delivery import assemble_read_pages
        pages = [result.data]
        while pages[-1]['data']['delivery'].get('next'):
            continuation = pages[-1]['data']['delivery']['next']
            page = session.invoke(ToolCall(f'{call_id}-page-{len(pages)}',
                                          continuation['tool'], continuation['arguments']))
            assert not page.is_error and page.data['ok'], page.data
            pages.append(page.data)
        return {**result.data, 'data': assemble_read_pages(pages)}
    return result.data


def version_bytes(service, project_id, version_id):
    return service.projects.artifact(project_id, version_id, 'gxw').read_bytes()


def ordered_rows(raw, program='MAIN.Program.pou'):
    return read_ordered_program(read_offline_project(raw), program)


def assert_untouched_streams(before, after, targets):
    assert before.mapping == after.mapping
    assert set(before.outer) == set(after.outer)
    assert all(after.outer[name] == payload for name, payload in before.outer.items()
               if name not in {'_hdb', 'history.xml', 'Project.gd2'})
    original = read_project_hdb(before.outer).payloads
    saved = read_project_hdb(after.outer).payloads
    changed_streams = {before.mapping[name] for name in targets}
    assert set(original) == set(saved)
    assert all(saved[name] == payload for name, payload in original.items() if name not in changed_streams)


def patch_arguments(ir):
    return {'patch': {'base_revision': ir['revision'], 'base_ir_sha256': canonical_sha256(ir),
        'instruction_edits': [{'operation': 'replace', 'record_index': 1,
            'expected': {'op': 'MOV', 'args': ['K0', 'D0']},
            'instructions': [{'op': 'MOV', 'args': ['K2', 'D0']}]}],
        'device_comments': {'S0': '离线修改后的运行注释，完整保留长文本'}}}


def assert_warehouse(raw, *, modified=False):
    current = ordered_rows(raw)
    instructions, comments = warehouse_oracle()
    if modified:
        instructions[1] = {'op': 'MOV', 'args': ['K2', 'D0']}
        comments['S0'] = '离线修改后的运行注释，完整保留长文本'
    assert len(current['source_records']) == len(instructions) == 155
    assert current['coverage']['network_count'] == len(current['raw_networks']) == 9
    assert len(current['device_comments']) == 22 and current['device_comments'] == comments
    assert [{key: row[key] for key in ('op', 'args')} for row in current['source_records']] == instructions
    final = current['raw_networks'][-1]['raw_instructions']
    assert [row['op'] for row in final][:4] == ['LD=', 'OUT', 'OUT', 'MPS']
    assert [row['op'] for row in final][-1] == 'END'
    return current


def test_warehouse_file_inspection_preserves_155_instructions_9_networks_22_comments_without_native(no_native):
    raw, logical = native_source('ladder')
    service = ProgramInspectionService()
    result = service.inspect_gxw_project(data_base64=encoded(raw))
    assert result['cpu_menu'] == result['plc_model'] == 'FX3U/FX3UC'
    assert result['program_name'] == logical and result['source_ref']
    assert result['native_required'] is False and result['native_compile'] == 'not_run'
    assert result['coverage']['network_count'] == 9 and len(result['source_records']) == 155
    assert len(result['device_comments']) == 22 and not no_native
    assert_warehouse(raw)


def test_warehouse_shared_import_preview_accept_download_and_reimport(workbench, no_native, tmp_path):
    raw, logical = native_source('ladder')
    source_path = tmp_path / 'original.gxw'
    source_path.write_bytes(raw)
    original_on_disk = source_path.read_bytes()
    imported = workbench.gxw_files.import_project(path=str(source_path), filename='仓库.gxw')
    project_id, v1 = imported['project_id'], imported['version_id']
    assert imported['status'] == 'accepted' and v1
    assert version_bytes(workbench, project_id, v1) == raw
    current = workbench.projects.tool_context(project_id, v1)
    assert current.program_ir['source']['format'] == 'instruction_sequence_v1'
    response = workbench.agent_call({'project_id': project_id, 'version_id': v1, 'name': 'patch_program',
        'arguments': patch_arguments(current.program_ir), 'call_id': 'warehouse-web-patch'})
    assert not response['is_error'], response
    proposal_id = response['proposal_id']
    assert workbench.proposals.get(proposal_id)['status'] == 'pending'
    preview = workbench.proposal_preview(proposal_id)
    assert preview['diff']['has_changes']
    assert '<svg' in preview['svg']
    assert workbench.projects.raw_project(project_id)['active_version_id'] == v1
    accepted = workbench.decide(proposal_id, 'accept')['proposal']
    assert accepted['status'] == 'accepted'
    v2 = accepted['result']['version_id']
    assert v2 != v1 and workbench.projects.raw_version(project_id, v2)['parent_version_id'] == v1
    modified = version_bytes(workbench, project_id, v2)
    assert_warehouse(modified, modified=True)
    before, after = read_offline_project(raw), read_offline_project(modified)
    assert set(before.streams) == set(after.streams)
    assert all(after.streams[name] == payload for name, payload in before.streams.items()
               if name not in {logical, 'COMMENT.qcd'})
    assert_untouched_streams(before, after, {logical, 'COMMENT.qcd'})
    assert source_path.read_bytes() == original_on_disk
    assert version_bytes(workbench, project_id, v1) == raw
    downloaded = workbench.projects.artifact(project_id, v2, 'gxw')
    reimported = workbench.gxw_files.import_project(path=str(downloaded), filename='仓库-修改.gxw')
    assert reimported['project_id'] != project_id
    assert version_bytes(workbench, reimported['project_id'], reimported['version_id']) == modified
    assert not no_native


def test_local_mcp_import_read_patch_preview_and_download_reimport(tmp_path, no_native):
    raw, logical = native_source('ladder')
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspected = invoke(session, 'inspect_gxw_project', {'data_base64': encoded(raw)}, 'inspect')
        assert not session.workspace.exists()
        info = invoke(session, 'get_current_program_info', {'source_ref': inspected['data']['source_ref']}, 'inspect-info')
        assert info['data']['source_ref'] == inspected['data']['source_ref']
        assert info['data']['program_name'] == logical
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw), 'filename': '仓库.gxw'}, 'import')['data']
        project_id, v1 = imported['project_id'], imported['version_id']
        assert session.project_id == project_id and session.version_id == v1
        context = session.service.projects.tool_context(project_id, v1)
        patched = invoke(session, 'patch_program', patch_arguments(context.program_ir), 'patch')
        assert patched['status'] == 'saved'
        v2 = patched['data']['version_id']
        assert v2 != v1
        proposal = session.service.proposals.get(patched['proposal_id'])
        assert proposal['status'] == 'accepted' and proposal['summary']['approval']['source'] == 'local_autosave'
        preview = session.service.proposal_preview(proposal['id'])
        assert preview['diff']['has_changes'] and '<svg' in preview['svg']
        # The MCP session deliberately remains pinned until explicit selection.
        invoke(session, 'select_project', {'project_id': project_id, 'version_id': v2}, 'select-v2')
        artifacts = invoke(session, 'get_project_artifacts', {}, 'artifacts')['data']['artifacts']
        gxw = next(item for item in artifacts if item['id'] == 'gxw')
        downloaded = Path(gxw['path']).read_bytes()
        assert_warehouse(downloaded, modified=True)
        imported_again = invoke(session, 'import_gxw_project', {'data_base64': encoded(downloaded), 'program': logical}, 'reimport')['data']
        assert imported_again['project_id'] != project_id
        assert version_bytes(session.service, imported_again['project_id'], imported_again['version_id']) == downloaded
        assert version_bytes(session.service, project_id, v1) == raw
    assert not no_native


@pytest.mark.parametrize('record_hex', ['037f03', '033c0304d00104'])
def test_partial_records_import_edit_preview_download_reimport_without_native(tmp_path, no_native, record_hex):
    from gxw.project_writer import write_project_replacements
    from gxw.token_pou import frame_token_pou
    from integrations.web.responses import GXWInspection
    raw, logical = native_source('ladder')
    before = read_offline_project(raw)
    source = before.streams[logical]
    frame = frame_token_pou(source)
    inserted = bytes.fromhex(record_hex)
    changed = bytearray(source[:frame.body_offset] + inserted + source[frame.body_offset:])
    for offset in (frame.body_offset - 24, frame.body_offset - 20):
        struct.pack_into('<I', changed, offset, struct.unpack_from('<I', source, offset)[0] + len(inserted))
    raw = write_project_replacements(raw, {logical: (before.mapping[logical], source, bytes(changed))}).data
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspection = session.service.gxw_files.inspect(data_base64=encoded(raw))
        GXWInspection.model_validate(inspection)
        assert inspection['selected_source']['offline_editable']
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw)}, 'import')['data']
        context = session.service.projects.tool_context(imported['project_id'], imported['version_id'])
        assert context.program_ir['source']['format'] == 'instruction_sequence_v2'
        from integrations.web.responses import PLCProgram
        assert PLCProgram.model_validate(context.program_ir).analysis.counts.unverified > 0
        checked = invoke(session, 'validate_current_program', {}, 'check')['data']
        assert checked['status'] == 'unverified' and checked['runtime_verified'] is False
        arguments = patch_arguments(context.program_ir)
        arguments['patch']['instruction_edits'][0]['record_index'] += 1
        result = invoke(session, 'patch_program', arguments, 'edit')
        assert result['status'] == 'saved'
        preview = session.service.proposal_preview(result['proposal_id'])
        assert preview['diff']['has_changes'] and '<svg' in preview['svg']
        saved = version_bytes(session.service, imported['project_id'], result['data']['version_id'])
        current = ordered_rows(saved)
        assert current['source_records'][0]['raw_hex'] == record_hex
        assert current['source_records'][2]['args'] == ['K2', 'D0']
        assert len(current['listing'].instructions) == 155
        assert_untouched_streams(read_offline_project(raw), read_offline_project(saved),
                                {logical, *[name for name in before.streams if name.endswith('.qcd')]})
        again = invoke(session, 'import_gxw_project', {'data_base64': encoded(saved)}, 'again')['data']
        assert version_bytes(session.service, again['project_id'], again['version_id']) == saved
    assert not no_native


@pytest.mark.parametrize('kind', ['st', 'fbd', 'sfc'])
def test_native_authored_structured_sources_inspect_and_import_with_native_loading_blocked(workbench, no_native, kind):
    raw, logical = native_source(kind)
    inspected = workbench.projects.inspection.inspect_gxw_project(data_base64=encoded(raw), program=logical)
    assert inspected['selected_source']['source_name'] == logical
    assert inspected['target_mode'] == kind and inspected['native_required'] is False
    imported = workbench.gxw_files.import_project(data_base64=encoded(raw), program=logical, filename=kind + '.gxw')
    assert imported['status'] == 'accepted'
    version = workbench.projects.raw_version(imported['project_id'], imported['version_id'])
    assert version['target_mode'] == kind and version['program_name'] == logical
    assert version_bytes(workbench, imported['project_id'], imported['version_id']) == raw
    assert version['validation']['gx_compile'] == 'not_run'
    assert not no_native


def test_multi_program_file_requires_explicit_selection_before_import(workbench, no_native):
    raw, logical = native_source('st')
    inspected = workbench.gxw_files.inspect(data_base64=encoded(raw))
    assert inspected['selected_source'] is None
    assert len(inspected['programs']) >= 2
    assert workbench.projects.list_projects() == []
    with pytest.raises(GXWFormatError, match='Select one unambiguous'):
        workbench.gxw_files.import_project(data_base64=encoded(raw))
    assert workbench.projects.list_projects() == []
    imported = workbench.gxw_files.import_project(data_base64=encoded(raw), program=logical)
    assert imported['version_id'] and not no_native


@pytest.mark.parametrize('kind', ['st', 'fbd', 'sfc'])
def test_mcp_structured_read_edit_preview_download_and_reimport_without_native(tmp_path, no_native, kind):
    raw, logical = native_source(kind)
    before = read_offline_project(raw)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspected = invoke(session, 'inspect_gxw_project', {'data_base64': encoded(raw), 'program': logical}, 'inspect')
        info = invoke(session, 'get_current_program_info', {'source_ref': inspected['data']['source_ref']}, 'source-info')
        assert info['data']['target_mode'] == kind and info['data']['program_name'] == logical
        assert not session.workspace.exists()
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw), 'program': logical,
            'filename': kind + '.gxw'}, 'import')['data']
        project_id, v1 = imported['project_id'], imported['version_id']
        read = invoke(session, 'read_' + kind + '_project', {'program': logical}, 'read')['data']
        if kind == 'st':
            assert any(row['program'] == logical for row in read['programs'])
            arguments = {'operation': 'edit_st_program', 'source_name': 'CalcOutput_ST',
                'st_text': 'OfflineCounter := 7;\r\nOfflineCounter := OfflineCounter + 1;',
                'declarations': [{'name': 'OfflineCounter', 'data_type': 'INT'}]}
            expected_targets = {logical, 'CalcOutput_ST.Labels.lh'}
        elif kind == 'fbd':
            model = deepcopy(read['model'])
            for node in model['nodes']:
                node['x'] += 8
            for wire in model['wires']:
                wire['start'][0] += 8
                wire['end'][0] += 8
            model['declaration_edits'] = {'POU_01.Labels.lh': {'upserts': [
                {'name': 'OfflineCounter', 'data_type': 'INT'}]}}
            arguments = {'operation': 'edit', 'model': model}
            expected_targets = {logical, 'POU_01.Labels.lh'}
        else:
            original_sfc = read_sfc_project_context(raw, logical).object_model()
            zooms = [row for row in original_sfc['children'] if row['kind'] == 'zoom']
            original_child, native_payload = zooms[0], zooms[2]
            records_hex = ''.join(row['raw_hex'] for row in native_payload['records'])
            arguments = {'operation': 'edit', 'program': logical,
                'child_edits': [{'kind': original_child['kind'], 'name': original_child['name'], 'records_hex': records_hex}],
                'graph_edits': [{'step_reference': 1, 'action_number': 20}]}
            expected_targets = {logical}
        candidate = invoke(session, 'create_' + kind + '_candidate', arguments, 'edit')
        assert candidate['status'] == 'saved'
        v2 = candidate['data']['version_id']
        assert v2 != v1 and session.service.projects.raw_version(project_id, v2)['parent_version_id'] == v1
        preview = session.service.proposal_preview(candidate['proposal_id'])
        assert preview['target_mode'] == kind and preview['diff']['has_changes']
        if kind == 'st':
            assert 'OfflineCounter := 7;' in preview['st']
        elif kind == 'fbd':
            assert '<svg' in preview['svg']
        version = session.service.projects.raw_version(project_id, v2)
        validation = version['validation']
        assert validation['gx_compile'] == 'not_run' and validation['native_save'] == 'not_run'
        assert validation['write_backend'] == 'offline' and validation['compile_state'] == 'stale'
        invoke(session, 'select_project', {'project_id': project_id, 'version_id': v2}, 'select')
        artifacts = invoke(session, 'get_project_artifacts', {}, 'artifacts')['data']['artifacts']
        downloaded = Path(next(row['path'] for row in artifacts if row['id'] == 'gxw')).read_bytes()
        after = read_offline_project(downloaded)
        assert set(before.streams) == set(after.streams)
        assert all(after.streams[name] == payload for name, payload in before.streams.items() if name not in expected_targets)
        assert_untouched_streams(before, after, expected_targets)
        assert version_bytes(session.service, project_id, v1) == raw
        if kind == 'st':
            assert parse_st_pou(after.streams[logical]).text == arguments['st_text']
            document = parse_declarations(after.streams['CalcOutput_ST.Labels.lh'], logical_name='CalcOutput_ST.Labels.lh')
            assert [(row.name, row.data_type) for row in document.rows] == [('OfflineCounter', 'INT')]
        elif kind == 'fbd':
            from gxw.object_model import read_project_context
            initial = read_project_context(raw, logical)
            current = read_project_context(downloaded, logical)
            assert [(node.symbol, node.bbox.left, node.bbox.top) for node in current.program.nodes] == [
                (node.symbol, node.bbox.left + 8, node.bbox.top) for node in initial.program.nodes]
            old_labels = initial.declarations['POU_01.Labels.lh']
            new_labels = current.declarations['POU_01.Labels.lh']
            assert new_labels.rows[-1].name == 'OfflineCounter'
            assert [serialize_label(row) for row in new_labels.rows[:-1]] == [serialize_label(row) for row in old_labels.rows]
        else:
            saved_sfc = read_sfc_project_context(downloaded, logical).object_model()
            child = next(row for row in saved_sfc['children'] if row['name'] == original_child['name'])
            assert ''.join(row['raw_hex'] for row in child['records']) == records_hex
            assert len(saved_sfc['children']) == len(original_sfc['children'])
            # Clearing stale graph cache shifts internal source offsets; the
            # unchanged action records keep their identities and registrations.
            assert [{key: value for key, value in row.items() if key not in ('number_offset', 'record_offset')}
                    for row in saved_sfc['actions']] == [
                {key: value for key, value in row.items() if key not in ('number_offset', 'record_offset')}
                for row in original_sfc['actions']]
            changed = next(row for row in saved_sfc['graph']['records']
                           if row['kind'] == 'step-action-reference' and row['references'][0] == 1)
            assert changed['references'] == [1, 20]
        reimported = invoke(session, 'import_gxw_project', {'data_base64': encoded(downloaded), 'program': logical}, 'reimport')['data']
        assert reimported['project_id'] != project_id
        assert version_bytes(session.service, reimported['project_id'], reimported['version_id']) == downloaded
    assert not no_native


def test_single_ordinary_program_with_st_fb_is_selected_automatically(workbench, no_native):
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-visible-st-program-creation-20261017.zip')) as source:
        raw = source.read('001-start/baseline.gxw')
    inspected = workbench.gxw_files.inspect(data_base64=encoded(raw))
    assert inspected['selected_source']['source_name'] == 'POU_01.Program.pou'
    fb = next(row for row in inspected['programs'] if row['source_name'] == 'ScaleValue.Program.pou')
    assert fb['unit_kind'] == 'function_block'
    imported = workbench.gxw_files.import_project(data_base64=encoded(raw))
    assert workbench.projects.raw_version(imported['project_id'], imported['version_id'])['program_name'] == 'POU_01.Program.pou'
    assert version_bytes(workbench, imported['project_id'], imported['version_id']) == raw
    assert not no_native


@pytest.mark.parametrize('kind', ['ladder', 'sfc'])
def test_mcp_existing_token_source_local_labels_keep_target_body_and_reviewable_diff(tmp_path, no_native, kind):
    raw, logical = native_source(kind)
    before = read_offline_project(raw)
    table = logical.removesuffix('.Program.pou') + '.Labels.lh'
    source_kind = 1 if kind == 'ladder' else 240
    original = parse_declarations(before.streams[table], logical_name=table, source_kind=source_kind)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw), 'program': logical}, 'import')['data']
        project_id, v1 = imported['project_id'], imported['version_id']
        candidate = invoke(session, 'create_fbd_candidate', {'operation': 'edit_pou_labels',
            'source_name': logical, 'declarations': [{'name': 'OfflineLocal', 'data_type': 'INT',
                'comment': '已有声明表中的离线新行'}]}, 'add-label')
        assert candidate['status'] == 'saved'
        v2 = candidate['data']['version_id']
        version = session.service.projects.raw_version(project_id, v2)
        assert version['target_mode'] == kind and version['program_name'] == logical
        assert version['parent_version_id'] == v1
        validation = version['validation']
        assert validation['write_backend'] == 'offline' and validation['compile_state'] == 'stale'
        assert validation['gx_compile'] == validation['native_save'] == 'not_run'
        preview = session.service.proposal_preview(candidate['proposal_id'])
        assert preview['target_mode'] == kind and preview['diff']['has_changes']
        assert preview['diff']['definition_changed']
        assert 'OfflineLocal' in preview['diff']['unified_diff']
        saved = version_bytes(session.service, project_id, v2)
        after = read_offline_project(saved)
        labels = parse_declarations(after.streams[table], logical_name=table, source_kind=source_kind)
        assert [serialize_label(row) for row in labels.rows[:-1]] == [serialize_label(row) for row in original.rows]
        assert labels.rows[-1].name == 'OfflineLocal'
        assert labels.owner_name == original.owner_name
        assert labels.owner_sfc_block_number == original.owner_sfc_block_number
        assert after.streams[logical][:50] == before.streams[logical][:50]
        assert after.streams[logical][54:] == before.streams[logical][54:]
        assert_untouched_streams(before, after, {logical, table})
        if kind == 'sfc':
            previous = read_sfc_project_context(raw, logical).object_model()
            current = read_sfc_project_context(saved, logical).object_model()
            assert current['children'] == previous['children']
            assert current['graph'] == previous['graph']
        else:
            assert_warehouse(saved)
        invoke(session, 'select_project', {'project_id': project_id, 'version_id': v2}, 'select-v2')
        removed = invoke(session, 'create_fbd_candidate', {'operation': 'edit_pou_labels',
            'source_name': logical, 'declarations': [], 'remove_declarations': ['OfflineLocal']}, 'remove-label')
        final = read_offline_project(version_bytes(session.service, project_id, removed['data']['version_id']))
        assert [serialize_label(row) for row in parse_declarations(final.streams[table],
            logical_name=table, source_kind=source_kind).rows] == [serialize_label(row) for row in original.rows]
        assert final.streams[logical][54:] == before.streams[logical][54:]
        assert_untouched_streams(after, final, {logical, table})
        assert version_bytes(session.service, project_id, v1) == raw
        reimported = invoke(session, 'import_gxw_project', {'data_base64': encoded(saved),
            'program': logical}, 'reimport')['data']
        assert version_bytes(session.service, reimported['project_id'], reimported['version_id']) == saved
    assert not no_native


def test_mcp_ladder_body_declaration_and_comment_are_saved_as_one_offline_candidate(tmp_path, no_native):
    raw, logical = native_source('ladder')
    before = read_offline_project(raw)
    table = 'MAIN.Labels.lh'
    original = parse_declarations(before.streams[table], logical_name=table)
    ordered = read_ordered_program(before, logical)
    target = next(row for row in ordered['source_records'] if row.get('op') == 'MOV' and row['args'] == ['K0', 'D0'])
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw)}, 'import-joint')['data']
        pid, v1 = imported['project_id'], imported['version_id']
        generation = invoke(session, 'get_generation_context', {}, 'joint-context')['data']
        assert 'declaration_edits' in generation['output_contract']['editable_fields']
        patch = {'instruction_edits': [{'operation': 'replace', 'record_index': target['record_index'],
            'expected': {'op': 'MOV', 'args': ['K0', 'D0']},
            'instructions': [{'op': 'MOV', 'args': ['K2', 'D0']}]}],
            'declaration_edits': {table: {'upserts': [{'name': 'OfflineLocal', 'data_type': 'INT',
                'class_name': 'VAR', 'comment': '正文、声明、注释联合保存'}]}},
            'device_comments': {'S0': '离线联合事务中的完整注释。' * 4}}
        candidate = invoke(session, 'patch_program', {'patch': patch}, 'joint-edit')
        assert candidate['status'] == 'saved'
        saved = version_bytes(session.service, pid, candidate['data']['version_id'])
        after = read_offline_project(saved)
        current = read_ordered_program(after, logical)
        assert current['source_records'][target['record_index']]['args'] == ['K2', 'D0']
        assert current['device_comments']['S0'] == patch['device_comments']['S0']
        document = parse_declarations(after.streams[table], logical_name=table)
        assert document.rows[-1].name == 'OfflineLocal'
        assert [serialize_label(row) for row in document.rows[:-1]] == [serialize_label(row) for row in original.rows]
        assert_untouched_streams(before, after, {logical, table} | {
            name for name in before.streams if name.lower().endswith('.qcd')})
        preview = session.service.proposal_preview(candidate['proposal_id'])
        assert preview['diff']['has_changes']
        assert 'OfflineLocal' in preview['diff']['unified_diff']
        assert version_bytes(session.service, pid, v1) == raw
        validation = session.service.projects.raw_version(pid, candidate['data']['version_id'])['validation']
        assert validation['write_backend'] == 'offline' and validation['compile_state'] == 'stale'
        assert validation['source_checks'] and validation['native_save'] == 'not_run'
        assert validation['invalidated_results'] == {'scope': 'project_version', 'compilation': 'stale',
                                                     'addresses': 'stale', 'diagnostics': 'stale'}
    assert not no_native


def test_explicit_st_fb_import_generation_context_definition_edit_and_reimport(tmp_path, no_native):
    raw, _ = native_source('st')
    logical, table = 'ScaleValue.Program.pou', 'ScaleValue.Labels.lh'
    before = read_offline_project(raw)
    body = 'IF Enabled THEN\r\n OutValue := InValue * Gain + 17;\r\nELSE OutValue := 0; END_IF;\r\n'
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspection = invoke(session, 'inspect_gxw_project', {'data_base64': encoded(raw),
            'program': logical}, 'inspect-fb')['data']
        assert inspection['selected_source']['unit_kind'] == 'function_block'
        assert inspection['target_mode'] == 'st'
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw),
            'program': logical}, 'import-fb')['data']
        project_id, v1 = imported['project_id'], imported['version_id']
        assert version_bytes(session.service, project_id, v1) == raw
        generation = invoke(session, 'get_generation_context', {'execution_mode': 'background',
            'user_requirement': '保留接口，将输出算式增加 17'}, 'fb-context')['data']
        assert generation['target_mode'] == 'st' and generation['execution_mode'] == 'offline'
        assert generation['output_contract']['tool'] == 'create_fbd_candidate'
        assert generation['output_contract']['operation'] == 'edit_st_fb'
        assert generation['output_contract']['source_name'] == logical
        definition = invoke(session, 'read_fbd_definition', {'source_name': 'ScaleValue'}, 'read-definition')['data']
        original, = definition['definitions']
        assert original['kind'] == 'function_block' and original['language'] == 'st'
        candidate = invoke(session, 'create_fbd_candidate', {'operation': 'edit_st_fb',
            'source_name': logical, 'st_text': body}, 'edit-fb')
        assert candidate['status'] == 'saved'
        v2 = candidate['data']['version_id']
        version = session.service.projects.raw_version(project_id, v2)
        assert version['target_mode'] == 'st' and version['program_name'] == logical
        binding = version['validation']['source_binding']
        assert binding['kind'] == 'function_block' and binding['task_bindings'] == []
        assert version['validation']['write_backend'] == 'offline'
        assert version['validation']['compile_state'] == 'stale'
        assert version['validation']['gx_compile'] == version['validation']['native_save'] == 'not_run'
        preview = session.service.proposal_preview(candidate['proposal_id'])
        assert preview['target_mode'] == 'st' and preview['diff']['has_changes']
        assert preview['st'] == body.replace('\r\n', '\n') and '+ 17;' in preview['diff']['unified_diff']
        saved = version_bytes(session.service, project_id, v2)
        after = read_offline_project(saved)
        assert parse_st_pou(after.streams[logical]).text == body
        assert after.streams[table] == before.streams[table]
        assert_untouched_streams(before, after, {logical})
        assert version_bytes(session.service, project_id, v1) == raw
        reimported = invoke(session, 'import_gxw_project', {'data_base64': encoded(saved),
            'program': logical}, 'reimport-fb')['data']
        assert version_bytes(session.service, reimported['project_id'], reimported['version_id']) == saved
        reread = invoke(session, 'read_fbd_definition', {'source_name': 'ScaleValue'}, 'reread-definition')['data']
        changed, = reread['definitions']
        assert changed['st_text'] == body and changed['declarations'] == original['declarations']
    assert not no_native


@pytest.mark.parametrize('filename,logical,return_type,body', [
    ('worldofwheat-fx3g.gxw', 'getCurrentPlayer.Program.pou', 'INT', 'getCurrentPlayer := currentPlayer + 10;'),
    ('oph032-q06udv.gxw', 'LeapYear.Program.pou', 'BOOL', 'LeapYear := (i_Year MOD 4) = 0;'),
])
def test_existing_function_mcp_context_edit_preview_save_and_reimport(tmp_path, no_native,
                                                                    filename, logical, return_type, body):
    raw = private_path('research/fixtures/gxw-offline/st-authored/' + filename).read_bytes()
    before = read_offline_project(raw)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        inspection = invoke(session, 'inspect_gxw_project', {'data_base64': encoded(raw),
            'program': logical}, 'inspect')['data']
        assert inspection['selected_source']['unit_kind'] == 'function'
        assert inspection['selected_source']['offline_editable']
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw),
            'program': logical}, 'import')['data']
        generation = invoke(session, 'get_generation_context', {'execution_mode': 'background',
            'user_requirement': '修改当前FUNCTION正文，保持返回类型与调用接口'}, 'context')['data']
        assert generation['output_contract']['tool'] == 'create_st_candidate'
        assert generation['output_contract']['operation'] == 'edit_st_function'
        read = invoke(session, 'read_st_project', {'program': logical}, 'read')['data']
        assert read['programs'][0]['return_type'] == return_type
        candidate = invoke(session, 'create_st_candidate', {'operation': 'edit_st_function',
            'source_name': logical, 'st_text': body}, 'edit')
        assert candidate['status'] == 'saved'
        version = session.service.projects.raw_version(imported['project_id'], candidate['data']['version_id'])
        assert version['validation']['source_binding']['return_type'] == return_type
        assert version['validation']['source_binding']['task_bindings'] == []
        assert version['validation']['compile_state'] == 'stale'
        preview = session.service.proposal_preview(candidate['proposal_id'])
        assert preview['diff']['has_changes'] and body in preview['st']
        saved = version_bytes(session.service, imported['project_id'], candidate['data']['version_id'])
        assert parse_st_pou(read_offline_project(saved).streams[logical]).text == body
        assert_untouched_streams(before, read_offline_project(saved), {logical})
        reimported = invoke(session, 'import_gxw_project', {'data_base64': encoded(saved),
            'program': logical}, 'reimport')['data']
        assert version_bytes(session.service, reimported['project_id'], reimported['version_id']) == saved
    assert not no_native


@pytest.mark.parametrize('kind', ['st', 'fbd', 'sfc'])
def test_mcp_comment_only_edits_have_complete_service_and_http_diff_without_native(tmp_path, no_native, kind):
    from integrations.web.responses import FBDDiff, SFCDiff, STTextDiff
    raw, logical = native_source(kind)
    before = read_offline_project(raw)
    long_comment = '离线软元件注释必须完整保存并显示，不套用生成文本的十六字符截断规则。'
    assert len(long_comment) > 16
    assert not {'M0', 'M1'} & before.comments()[0].keys()
    expected = [
        {'table': 'COMMENT.qcd', 'address': 'M0', 'before': None, 'after': long_comment},
        {'table': 'COMMENT.qcd', 'address': 'M1', 'before': None, 'after': ''},
    ]
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        imported = invoke(session, 'import_gxw_project', {'data_base64': encoded(raw),
            'program': logical}, 'import')['data']
        project_id, v1 = imported['project_id'], imported['version_id']
        if kind == 'st':
            arguments = {'operation': 'edit_st_program', 'source_name': logical,
                'st_text': parse_st_pou(before.streams[logical]).text}
        elif kind == 'fbd':
            read = invoke(session, 'read_fbd_project', {'program': logical}, 'read-model')['data']
            arguments = {'operation': 'edit', 'model': deepcopy(read['model'])}
        else:
            arguments = {'operation': 'edit', 'program': logical}
        arguments.update(device_comments={'M0': long_comment, 'M1': ''}, comment_table='COMMENT.qcd')
        candidate = invoke(session, 'create_' + kind + '_candidate', arguments, 'comments')
        assert candidate['status'] == 'saved'
        v2 = candidate['data']['version_id']
        preview = session.service.proposal_preview(candidate['proposal_id'])
        diff = preview['diff']
        assert diff['has_changes'] and diff['comment_changes'] == expected
        assert long_comment in diff['unified_diff'] and 'device-comments' in diff['unified_diff']
        schema = {'st': STTextDiff, 'fbd': FBDDiff, 'sfc': SFCDiff}[kind]
        http_diff = schema.model_validate(diff).model_dump(exclude_unset=True)
        assert http_diff['comment_changes'] == expected and http_diff['has_changes']
        saved = version_bytes(session.service, project_id, v2)
        after = read_offline_project(saved)
        comments = after.comments()[0]
        assert comments['M0'] == long_comment and 'M1' in comments and comments['M1'] == ''
        assert after.streams[logical] == before.streams[logical]
        assert_untouched_streams(before, after, {'COMMENT.qcd'})
        assert version_bytes(session.service, project_id, v1) == raw
        validation = session.service.projects.raw_version(project_id, v2)['validation']
        assert validation['write_backend'] == 'offline'
        assert validation['gx_compile'] == validation['native_save'] == 'not_run'
    assert not no_native
