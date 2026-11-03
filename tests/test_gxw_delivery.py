"""GXW-first application delivery, automatic compilation and explicit fallback."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application import gxw_delivery as delivery


@pytest.mark.parametrize('code,declarations', [
    ('flag := TRUE;', [{'name': 'flag', 'data_type': 'BOOL'}, {'name': 'FLAG', 'data_type': 'BOOL'}]),
    ('flag := TRUE;', [{'name': 'flag', 'data_type': 'ARRAY [4..1] OF BOOL'}]),
    ('flag := TRUE;', [{'name': 'flag', 'data_type': 'INT'}]),
    ('fixed := 2;', [{'name': 'fixed', 'data_type': 'INT', 'class_name': 'VAR_CONSTANT', 'initial_value': '1'}]),
    ('X0 := TRUE;', []), ('D9000 := 1;', []),
    ('flag := TRUE;', [{'name': 'flag', 'data_type': 'BOOL', 'device': 'X0'}]),
    ('value := 1;', [{'name': 'value', 'data_type': 'INT', 'device': 'D9000'}]),
    ('IF THEN Y0 := TRUE; END_IF;', []),
])
def test_new_st_candidate_rejects_definite_errors_before_native_work(code, declarations):
    from plc.st_candidate import prepare_source_candidate
    with pytest.raises(ValueError):
        prepare_source_candidate(code, declarations, plc_model='FX3U')


def test_global_st_labels_and_addresses_are_delivered_by_mcp(tmp_path):
    from application.engineering_session import EngineeringSession
    from agent_runtime.messages import ToolCall
    from gxw.active_context import parse_active_label_csv
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        session.invoke(ToolCall('create', 'create_project', {'name': 'Labels', 'plc_model': 'FX3U', 'target_mode': 'st'}))
        result = session.invoke(ToolCall('candidate', 'create_st_candidate', {'operation': 'generate',
            'st_text': 'flag := X0;', 'declarations': [{'name': 'flag', 'data_type': 'BOOL',
                'class_name': 'VAR_GLOBAL', 'device': 'M20', 'comment': '启动'}]}))
        assert not result.is_error, result.content
        data = result.data['data']
        version = data['version_id']
        assert data['delivery']['actual_format'] == 'txt'
        body = session.service.projects.artifact(session.project_id, version, 'st')
        assert body.name == 'program.txt' and body.read_text(encoding='utf-8') == 'flag := X0;'
        labels = session.service.projects.artifact(session.project_id, version, 'global_labels_csv')
        _, columns, rows = parse_active_label_csv(labels.read_bytes())
        assert rows[0][columns.index('软元件')] == 'M20'


def test_st_text_bundle_uses_the_checked_declarations_and_native_csv(tmp_path):
    from plc.st_candidate import prepare_source_candidate
    from gxw.active_context import parse_active_label_csv
    candidate, checked = prepare_source_candidate('flag := X0;', [
        {'name': 'flag', 'data_type': 'BOOL', 'device': 'M20', 'comment': '启动'},
        {'name': 'limit', 'data_type': 'INT', 'class_name': 'VAR_CONSTANT', 'initial_value': '5'},
        {'name': 'values', 'data_type': 'ARRAY [0..3] OF DINT'},
        {'name': 'counter', 'data_type': 'INT', 'initial_value': '7'}], plc_model='FX3U')
    assert not any(row['code'] == 'ST_LABEL_COVERAGE' for row in checked['findings'])
    result = delivery.fallback(tmp_path, 'st', 'FX3U', candidate, 'No installation')
    assert result['artifacts']['st']['path'] == 'program.txt'
    assert result['metadata']['delivery']['actual_format'] == 'txt'
    _, columns, rows = parse_active_label_csv((tmp_path / 'global_labels.csv').read_bytes())
    labels = {row[columns.index('标签名')]: dict(zip(columns, row)) for row in rows}
    assert labels['flag']['软元件'] == 'M20' and labels['flag']['注释'] == '启动'
    assert labels['limit']['类'] == 'VAR_GLOBAL_CONSTANT' and labels['limit']['常量'] == '5'
    assert labels['values']['数据类型'] == 'ARRAY [0..3] OF DINT'
    assert labels['values']['软元件'] == '' and labels['counter']['常量'] == ''
    assert result['metadata']['label_export_diagnostics'][0]['initial_value'] == '7'
    assert '7' in (tmp_path / 'program.txt').read_text(encoding='utf-8')
    assert json.loads((tmp_path / 'source_candidate.json').read_text(encoding='utf-8')) == candidate


@pytest.mark.parametrize('prepared_validation', [
    {'status': 'source_candidate', 'gx_compile': 'not_run'},
    {'status': 'source_preserved', 'source_readback': 'matched', 'messages': ['read back']},
])
def test_delivery_preserves_summary_static_results_and_generation_context(tmp_path, monkeypatch, prepared_validation):
    (tmp_path / 'program.st').write_text('Y0 := X0;', encoding='utf-8')
    def prepare(*args, **kwargs):
        return {'artifacts': {'st': {'path': 'program.txt'}}, 'metadata': {
            'summary': 'Generated source candidate', 'validation': prepared_validation,
            'delivery': {'degraded': True, 'reason': 'No installation'},
            'generation_handoff': {'wrong': True}}}
    monkeypatch.setattr(delivery, 'prepare_generated', prepare)
    metadata = {'target_mode': 'st', 'plc_model': 'FX3U', 'summary': 'User summary',
        'artifacts': {'st': 'program.st'}, 'validation': {'status': 'failed', 'static': 'failed',
            'messages': ['static error'], 'native': 'unverified'}, 'generation_handoff': {'receipt': 'kept'}}
    delivery.finalize_saved(tmp_path, metadata)
    assert metadata['summary'] == 'User summary' and metadata['generation_handoff'] == {'receipt': 'kept'}
    assert metadata['validation']['static'] == metadata['validation']['status'] == 'failed'
    assert metadata['validation']['messages'][0] == 'static error'


@pytest.mark.parametrize('answer', [
    {'st_code': 'Y0 := X0;'}, {'st_text': 'Y0 := X0;', 'declarations': []},
    {'operation': 'generate', 'st_text': 'Y0 := X0;'}])
def test_new_st_response_normalization_follows_the_saved_version(tmp_path, monkeypatch, answer):
    import application.model_api as model_api
    from agent_runtime.tool_context import build_tool_context
    from agent_runtime.plc_tools import build_default_tool_registry
    context = build_tool_context({'id': 'p', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
                                 version={'id': 'v1', 'target_mode': 'st'})
    schema = build_default_tool_registry()._tools['create_st_candidate'].parameters
    schema = next(rule for rule in schema['oneOf'] if rule['properties']['operation'].get('enum') == ['generate'])
    monkeypatch.setattr(delivery, 'new_generation_context', lambda *a, **k: {
        'generation_instructions': 'ST', 'output_contract': {'parameter_schema': schema},
        'generation_handoff': {'receipt': 'retained'}})
    monkeypatch.setattr(model_api, '_request_model', lambda *a, **k: SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(answer))))
    job = SimpleNamespace(emit=lambda *a: None, checkpoint=lambda: None)
    result = delivery.generate_source_candidate(tmp_path, {'text': 'Make ST'}, [], job, context)
    assert result['target_mode'] == 'st'
    assert result['metadata']['generation_handoff'] == {'receipt': 'retained'}
    assert result['metadata']['maintainability_review']
    assert result['artifacts']['global_labels_csv']['path'] == 'global_labels.csv'


def test_new_source_job_preserves_the_requested_change_scope(tmp_path, monkeypatch):
    from application.job_execution import JobExecution
    from plc.change_scope import scope_instruction
    scope = {'addresses': ['Y0']}
    class Captured(Exception): pass
    def generate(output, snapshot, *args):
        assert snapshot['text'] == 'Change output' + scope_instruction(scope)
        raise Captured()
    monkeypatch.setattr(delivery, 'generate_source_candidate', generate)
    execution = JobExecution(projects=SimpleNamespace(store=None), state_dir=tmp_path,
        lock=None, proposals=None, execution=None, previews=None, pending_proposal=None, save_local_proposal=None)
    with pytest.raises(Captured):
        execution.run(SimpleNamespace(job_id='job'), {'kind': 'generation', 'project_id': 'p',
            'text': 'Change output', 'response_language': 'zh-CN',
            'project': {'target_mode': 'fbd'}, 'version': {'target_mode': 'st'}, 'change_scope': scope},
            SimpleNamespace(native_snapshot=None), [], None)


@pytest.mark.parametrize('entry', ['mcp', 'web'])
def test_new_st_semantic_error_never_creates_a_saved_version(tmp_path, monkeypatch, entry):
    arguments = {'operation': 'generate', 'st_text': 'flag := TRUE;',
                 'declarations': [{'name': 'flag', 'data_type': 'INT'}]}
    if entry == 'mcp':
        from application.engineering_session import EngineeringSession
        from agent_runtime.messages import ToolCall
        with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
            session.invoke(ToolCall('create', 'create_project', {'name': 'Rejected', 'plc_model': 'FX3U', 'target_mode': 'st'}))
            result = session.invoke(ToolCall('candidate', 'create_st_candidate', arguments))
            assert result.is_error
            assert session.service.projects.raw_project(session.project_id)['versions'] == []
        return
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    import application.model_api as model_api
    from tests.test_web_api import _app, _login, _Provider, ORIGIN
    monkeypatch.setattr(model_api, '_request_model', lambda *a, **k: SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(arguments))))
    workspace, state = tmp_path / 'workspace', tmp_path / 'state'
    provider = _Provider()
    service = WorkbenchService(workspace, state, model_factory=lambda: (provider, {'model': provider.profile['model']}))
    with TestClient(_app(workspace, state, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', json={'name': 'Rejected', 'plc_model': 'FX3U', 'target_mode': 'st'}, headers=headers).json()
        service.store.set_confirmed_spec(project['id'], {'schema_version': 4, 'summary': 'test', 'io_table': [], 'parameters': []})
        response = client.post('/api/jobs', json={'kind': 'generation', 'project_id': project['id'],
            'request_id': 'bad-st', 'text': 'Generate'}, headers=headers)
        assert response.status_code == 202, response.text
        job = response.json()['id']
        service.jobs._futures[job].result(timeout=15)
        assert client.get('/api/jobs/' + job).json()['status'] == 'failed'
        assert service.projects.raw_project(project['id'])['versions'] == []
from tests.test_web_api import offline


@pytest.mark.parametrize('mode,primary', [('ladder', 'program_csv'), ('st', 'st'), ('fbd', 'fbd'), ('sfc', 'sfc')])
def test_unavailable_creation_has_explicit_language_fallback(tmp_path, monkeypatch, mode, primary):
    if mode == 'ladder':
        from plc.core import PLCCore
        from tests.test_generation_workflow import _ladder
        candidate = PLCCore().create_program_candidate(_ladder(), plc_model='FX3U')
        result = PLCCore().compile_project(candidate['candidate_ir'], tmp_path, validation_profile='generation_structural')
        metadata = {'target_mode': mode, 'plc_model': 'FX3U', 'artifacts': result['artifacts']}
    else:
        candidate = {'st_text': 'Y0 := X0;'} if mode == 'st' else {'graph_edits': []} if mode == 'sfc' else {'schema_version': 2}
        payload = delivery.fallback(tmp_path, mode, 'FX3U', candidate, None)
        metadata = payload['metadata']
        metadata['artifacts'] = {key: value['path'] for key, value in payload['artifacts'].items()}
    delivery.finalize_saved(tmp_path, metadata)
    assert metadata['delivery']['preferred_format'] == 'gxw'
    assert metadata['delivery']['degraded'] is True
    assert metadata['delivery']['primary_artifact'] == primary
    assert 'not found' in metadata['delivery']['reason']
    assert (tmp_path / metadata['artifacts'][primary]).is_file()


@pytest.mark.parametrize('found,failed', [(False, False), (True, False), (True, True)])
def test_existing_gxw_is_compiled_when_found_and_never_replaced_with_csv(tmp_path, monkeypatch, found, failed):
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    calls = []
    (tmp_path / 'program.gxw').write_bytes(b'candidate')
    metadata = {'target_mode': 'st', 'plc_model': 'FX3U', 'delivery_requested': True,
                'artifacts': {'gxw': 'program.gxw'}, 'validation': {
                    'compile_state': 'stale', 'native_compile': 'unverified',
                    'invalidated_results': {'compilation': 'stale', 'addresses': 'stale'}}}
    monkeypatch.setattr(delivery, 'installation', lambda: Path('E:/custom/GPPW2/GD2.exe') if found else None)
    def compile_project(self, raw):
        calls.append(raw)
        if failed:
            raise WorkspaceSaveError('Public check rejected', {'status': 'completed_rejected'})
        return SimpleNamespace(data=b'compiled', report={'validation': {'gx_compile': 'passed', 'cold_reopen': 'passed'}})
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'compile_project', compile_project)
    import application.gxw_files as files
    def prepare(output, *, raw, program, revision=1):
        (output / 'program.gxw').write_bytes(raw)
        return {'artifacts': {'gxw': {'path': 'program.gxw'}}, 'metadata': {}}
    monkeypatch.setattr(files, 'prepare_candidate', prepare)
    delivery.finalize_saved(tmp_path, metadata)
    assert calls == ([b'candidate'] if found else [])
    assert metadata['delivery']['actual_format'] == 'gxw'
    assert metadata['delivery']['degraded'] is False
    assert metadata['validation']['gx_compile'] == ('failed' if failed else 'passed' if found else 'not_run')
    assert (tmp_path / 'program.gxw').read_bytes() == (b'compiled' if found and not failed else b'candidate')
    assert bool(metadata.get('activation_blocked')) == failed
    if found and not failed:
        assert metadata['validation']['compile_state'] == 'current'
        assert metadata['validation']['native_compile'] == 'passed'
        assert metadata['validation']['invalidated_results'] == {'compilation': 'current', 'addresses': 'stale'}


def test_mcp_failed_native_delivery_keeps_its_diagnostic_status_and_artifacts():
    from application.engineering_session import EngineeringSession
    session = EngineeringSession.__new__(EngineeringSession)
    session.project_id = 'project'
    proposal = {'action': 'accept_local', 'status': 'accepted',
                'result': {'status': 'saved_native_diagnostic', 'version_id': 'v0001'}}
    session.service = SimpleNamespace(
        proposals=SimpleNamespace(get=lambda _id: proposal),
        projects=SimpleNamespace(raw_version=lambda *args: {
            'delivery': {'primary_artifact': 'gxw'}, 'validation': {'gx_compile': 'failed'}}))
    session._version_artifacts = lambda *args: {'artifacts': [{'id': 'gxw', 'path': 'candidate.gxw'}]}
    result = session._completed_response({'proposal_id': 'proposal', 'data': {'ok': True, 'data': {}}})
    assert result['status'] == 'saved_native_diagnostic'
    assert result['data']['version_id'] == 'v0001'
    assert result['data']['validation']['gx_compile'] == 'failed'
    assert result['data']['artifacts'] == [{'id': 'gxw', 'path': 'candidate.gxw'}]
    assert 'not activated' in result['data']['message']


@pytest.mark.parametrize('mode', ['st', 'sfc'])
def test_new_generation_discovery_never_starts_native_processes(monkeypatch, mode):
    from agent_runtime.tool_context import build_tool_context
    monkeypatch.setattr(delivery, 'create_blank', lambda *a, **k: pytest.fail('read-only discovery created a project'))
    context = build_tool_context({'id': 'p', 'plc_model': 'FX3U', 'target_mode': mode})
    result = delivery.new_generation_context(context, {'user_requirement': 'Make a program'})
    assert result['output_contract']['operation'] == 'generate'
    assert result['output_contract']['tool'] == 'create_' + mode + '_candidate'


@pytest.mark.parametrize('mode', ['ladder', 'st', 'fbd', 'sfc'])
def test_new_delivery_uses_headless_adapter_without_ide_creation(tmp_path, monkeypatch, mode):
    from gxworks2.engineering_driver import NativeEngineeringDriver
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    monkeypatch.setattr(delivery, 'installation', lambda: Path('D:/GXWORKS2/GPPW2/GD2.exe'))
    monkeypatch.setattr(NativeEngineeringDriver, 'start_new', lambda *a, **k: pytest.fail('IDE creation used'))
    calls = []
    def create(self, cpu, language, *, block):
        calls.append((cpu, language, block))
        return b'headless-created-project'
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create_project', create)
    assert delivery.create_blank(tmp_path, mode, 'L02') == b'headless-created-project'
    assert calls == [('L02', mode, None)]
    assert list(tmp_path.iterdir()) == []


def test_first_st_fb_uses_new_fbd_factory_for_the_requested_cpu(tmp_path, monkeypatch):
    import application.fbd as fbd
    calls = []
    def create(output, language, cpu):
        calls.append((output, language, cpu))
        raise ValueError('native factory selected')
    monkeypatch.setattr(delivery, 'create_blank', create)
    monkeypatch.setattr(fbd, '_target_baseline', lambda *a: pytest.fail('captured baseline selected'))
    with pytest.raises(ValueError, match='native factory selected'):
        fbd.prepare_candidate(tmp_path, plc_model='Q03UDV', create_new=True,
            authoring={'operation': 'create_st_fb', 'source_name': 'Control', 'st_text': '', 'declarations': []})
    assert calls == [(tmp_path, 'fbd', 'Q03UDV')]


def test_first_st_fb_accepts_a_new_fbd_project_but_other_definitions_need_a_version():
    from agent_runtime.plc_tools import build_default_tool_registry
    from agent_runtime.tool_context import build_tool_context, resolve_tool_context
    from agent_runtime.tool_errors import ToolPreconditionError
    definition = build_default_tool_registry()._tools['create_fbd_candidate']
    context = build_tool_context({'id': 'p', 'plc_model': 'Q03UDV', 'target_mode': 'fbd'})
    assert resolve_tool_context(definition, context, {'operation': 'create_st_fb'}) is context
    with pytest.raises(ToolPreconditionError, match='已保存'):
        resolve_tool_context(definition, context, {'operation': 'configure_project'})
    other = build_tool_context({'id': 'p', 'plc_model': 'Q03UDV', 'target_mode': 'st'})
    with pytest.raises(ToolPreconditionError, match='已保存'):
        resolve_tool_context(definition, other, {'operation': 'create_st_fb'})


def test_fbd_catalog_follows_shared_creation_choices_without_blocking_existing_sources(monkeypatch):
    from application.fbd import _target_baseline, FBDValidationError
    from gxw.project_templates import blank_fbd_baseline, fbd_generation_models, unsupported_fbd_models
    import plc.project_capabilities as capabilities
    raw = blank_fbd_baseline('Q03UDV')
    choices = capabilities.project_creation_options()
    target = next(row for row in choices['native_targets'] if row['menu_cpu'] == 'Q03UDV')
    for project_type in target['project_types']:
        project_type['target_modes'] = [mode for mode in project_type['target_modes'] if mode['value'] != 'fbd']
    monkeypatch.setattr(capabilities, 'project_creation_options', lambda: choices)
    assert 'Q03UDV' not in fbd_generation_models()
    assert any(row['cpu'] == 'Q03UDV' for row in unsupported_fbd_models())
    with pytest.raises(FBDValidationError, match='新建组合'):
        _target_baseline(None, 'Q03UDV')
    assert _target_baseline(raw, 'Q03UDV') == raw


def test_new_fbd_contract_keeps_comments_and_binds_the_catalog_program_only_for_validation(tmp_path):
    from agent_runtime.plc_tools import build_default_tool_registry
    from agent_runtime.tool_context import build_tool_context
    from agent_runtime.tool_registry import ToolRegistry
    from application.fbd import unpack_candidate
    arguments = {'operation': 'generate', 'device_comments': {'X0': 'Start'}, 'model': {
        'program': 'POU_01.Program.pou', 'schema_version': 1, 'canvas_height': 8,
        'nodes': [{'id': 'x', 'template': 'input', 'symbol': 'X0', 'x': 2, 'y': 2},
                  {'id': 'y', 'template': 'output', 'symbol': 'Y0', 'x': 12, 'y': 2}],
        'wires': [{'from': 'x.OUT', 'to': 'y.IN'}]}}
    ToolRegistry._validate_arguments(build_default_tool_registry()._tools['create_fbd_candidate'].parameters, arguments)
    context = build_tool_context({'id': 'new-fbd', 'plc_model': 'FX3U', 'target_mode': 'fbd'})
    action = delivery.source_candidate_action(context, arguments, 'fbd')
    assert arguments['model']['program'] == 'POU_01.Program.pou'
    payload = unpack_candidate(action['pending_action']['_fbd_candidate'], tmp_path)
    saved = json.loads((tmp_path / payload['artifacts']['source_candidate']['path']).read_bytes())
    assert saved['model']['program'] == 'POU_01.Program.pou'
    assert saved['device_comments'] == {'X0': 'Start'}


def test_l02_st_timer_and_counter_views_do_not_become_invalid_devices():
    from plc.st_validation import inspect_st_program
    report = inspect_st_program('TIMER_M(X0, TC0, 30); COUNTER_M(X15, CC0, 1000); '
                                'Y0 := TS0 AND NOT CS0; D0 := TN0 + CN0;', plc_model='L02')
    assert report['syntax'] == 'passed'
    assert not [row for row in report['findings'] if row['severity'] == 'error']
    assert report['native_compile'] == 'unverified'


def test_local_mcp_saves_the_prepared_ladder_once_and_reports_csv_fallback(tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from agent_runtime.messages import ToolCall
    from tests.test_generation_workflow import _ladder
    from plc.core import PLCCore
    calls = []
    original = PLCCore.compile_project
    def compile_once(self, *args, **kwargs):
        calls.append(True)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PLCCore, 'compile_project', compile_once)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        created = session.invoke(ToolCall('create', 'create_project', {'name': 'Delivery', 'plc_model': 'FX3U', 'target_mode': 'ladder'}))
        assert not created.is_error, created.content
        result = session.invoke(ToolCall('generate', 'create_program_candidate', {'ladder': _ladder()}))
        assert not result.is_error, result.content
        assert result.data['data']['delivery']['actual_format'] == 'csv'
        assert result.data['data']['version_id']
        repeated = session.invoke(ToolCall('generate', 'create_program_candidate', {'ladder': _ladder()}))
        assert repeated.data['data']['version_id'] == result.data['data']['version_id']
        assert len(calls) == 1


@pytest.mark.parametrize('mode,cpu', [('st', 'FX3U'), ('st', 'L02'), ('sfc', 'FX3U'), ('fbd', 'FX3U')])
def test_local_mcp_new_source_fallback_is_saved_and_downloadable(tmp_path, mode, cpu):
    from application.engineering_session import EngineeringSession
    from agent_runtime.messages import ToolCall
    arguments = {'operation': 'generate', 'st_text': 'IF X0 AND NOT X1 THEN D0 := D1 + D2; END_IF;'} if mode == 'st' else {
        'operation': 'generate', 'graph_edits': [{'kind': 'rebuild', 'symbols': [
            {'kind': 'initial_step', 'x': 0, 'y': 0, 'number': 0},
            {'kind': 'transition', 'x': 0, 'y': 1, 'number': 0},
            {'kind': 'line', 'x': 0, 'start': 2, 'end': 2},
            {'kind': 'jump', 'x': 0, 'y': 3, 'number': 0}]}]}
    if mode == 'fbd':
        from tests.test_web_fbd import fresh_two_timers
        arguments = {'operation': 'generate', 'model': fresh_two_timers()}
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        assert not session.invoke(ToolCall('create', 'create_project', {'name': 'Source', 'plc_model': cpu, 'target_mode': mode})).is_error
        result = session.invoke(ToolCall('generate', 'create_' + mode + '_candidate', arguments))
        assert not result.is_error, result.content
        assert result.data['data']['delivery']['degraded'] is True
        artifacts = session.invoke(ToolCall('files', 'get_project_artifacts', {}))
        assert not artifacts.is_error, artifacts.content
        assert all(Path(row['path']).is_file() for row in artifacts.data['data']['artifacts'])


def test_native_compile_rejection_keeps_saved_gxw_and_previous_active_version(tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from agent_runtime.messages import ToolCall
    from tests.test_generation_workflow import _ladder
    from gxw.project_templates import blank_ladder_baseline
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        session.invoke(ToolCall('create', 'create_project', {'name': 'Compile', 'plc_model': 'FX3U', 'target_mode': 'ladder'}))
        first = session.invoke(ToolCall('first', 'create_program_candidate', {'ladder': _ladder()}))
        old = first.data['data']['version_id']
        monkeypatch.setattr(delivery, 'installation', lambda: Path('E:/GX/GPPW2/GD2.exe'))
        monkeypatch.setattr(delivery, 'create_blank', lambda *a, **k: blank_ladder_baseline('FX3U'))
        def reject(self, raw):
            raise WorkspaceSaveError('public check rejected', {'status': 'completed_rejected'})
        monkeypatch.setattr(NativeWorkspaceSourceSave, 'compile_project', reject)
        result = session.invoke(ToolCall('second', 'create_program_candidate', {'ladder': _ladder()}))
        assert not result.is_error, result.content
        saved = result.data['data']
        assert saved['delivery']['actual_format'] == 'gxw'
        assert saved['validation']['gx_compile'] == 'failed'
        assert session.service.projects.raw_project(session.project_id)['active_version_id'] == old
        assert saved['version_id'] != old
        assert any(row['id'] == 'gxw' and Path(row['path']).is_file() for row in saved['artifacts'])


@pytest.mark.parametrize('source', ['configured', 'running', 'registry'])
def test_finder_accepts_custom_install_directories(tmp_path, monkeypatch, source):
    from gxworks2.finder import GXWorks2Finder
    folder = tmp_path / '非系统盘 自定义目录' / 'GPPW2'
    folder.mkdir(parents=True)
    executable = folder / 'GD2.exe'
    executable.write_bytes(b'test executable')
    monkeypatch.delenv('GXWORKS2_EXE', raising=False)
    monkeypatch.setattr(GXWorks2Finder, 'INSTALL_CANDIDATES', ())
    monkeypatch.setattr(GXWorks2Finder, 'list_running', lambda self: [SimpleNamespace(executable=str(executable))] if source == 'running' else [])
    monkeypatch.setattr(GXWorks2Finder, '_registered_executables', staticmethod(lambda: [executable] if source == 'registry' else []))
    if source == 'configured':
        monkeypatch.setenv('GXWORKS2_EXE', str(executable))
    assert GXWorks2Finder().find_executable() == executable.resolve()


@pytest.mark.parametrize('found', [True, False])
def test_finder_checks_all_fixed_drives_and_ignores_stale_candidates(tmp_path, monkeypatch, found):
    from gxworks2.finder import GXWorks2Finder
    monkeypatch.setenv('GXWORKS2_EXE', str(tmp_path / 'missing/GD2.exe'))
    monkeypatch.setattr(GXWorks2Finder, 'INSTALL_CANDIDATES', ())
    monkeypatch.setattr(GXWorks2Finder, 'list_running', lambda self: [])
    monkeypatch.setattr(GXWorks2Finder, '_registered_executables', staticmethod(lambda: [tmp_path / 'stale/GD2.exe']))
    monkeypatch.setattr(GXWorks2Finder, '_fixed_drives', staticmethod(lambda: [tmp_path / 'C', tmp_path / '非系统盘 E']))
    target = tmp_path / '非系统盘 E/Program Files (x86)/MELSOFT/GPPW2/GD2.exe'
    if found:
        target.parent.mkdir(parents=True)
        target.write_bytes(b'executable')
    assert GXWorks2Finder().find_executable() == (target.resolve() if found else None)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows registry discovery')
def test_finder_reads_both_registry_hives_and_views(tmp_path, monkeypatch):
    import sys
    from gxworks2.finder import GXWorks2Finder
    calls = []
    class Key:
        def __init__(self, kind): self.kind = kind
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def open_key(hive, path, reserved=0, access=0):
        if isinstance(hive, Key):
            return Key('entry')
        calls.append((hive, access))
        return Key('app' if 'App Paths' in path else 'uninstall')
    def query(key, name):
        if key.kind == 'app': return str(tmp_path / '应用 路径/GD2.exe'), 1
        return ('GX Works2' if name == 'DisplayName' else str(tmp_path / '安装 目录')), 1
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, KEY_READ=4,
        KEY_WOW64_32KEY=8, KEY_WOW64_64KEY=16, OpenKey=open_key, QueryValueEx=query,
        QueryInfoKey=lambda key: (1,), EnumKey=lambda key, index: 'gx')
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    paths = GXWorks2Finder._registered_executables()
    assert {hive for hive, _ in calls} == {1, 2}
    assert {access for _, access in calls} == {12, 20}
    assert tmp_path / '应用 路径/GD2.exe' in paths
    assert tmp_path / '安装 目录/GPPW2/GD2.exe' in paths


def test_new_project_gui_initialization_preserves_source_and_workspace_ranges():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.offline_project import read_offline_project
    from gxw.project_metadata import project_gui_configuration, write_gui_system_variables, initialize_created_project_gui
    from gxw.project_writer import write_project_replacements
    from gxw.visible_engineering import source_projection
    raw = blank_fbd_baseline('FX3U')
    project = read_offline_project(raw)
    name = next(name for name in project.streams if name.casefold() == 'gppw2.gpj')
    original = project.streams[name]
    gui = project_gui_configuration(raw)
    ranges = {key: (0xffffffff, 0xffffffff) for key in gui['workspace_ranges'] if key not in {'R', 'W', 'B'}}
    empty = write_gui_system_variables(original, ranges, cpu='FX3U/FX3UC')
    uninitialized = write_project_replacements(raw, {name: (project.mapping[name], original, empty)}, invalidate=False).data
    assert project_gui_configuration(uninitialized)['status'] != 'matched'
    fixed = initialize_created_project_gui(uninitialized)
    assert project_gui_configuration(fixed)['status'] == 'matched'
    assert source_projection(fixed) == source_projection(uninitialized)


@pytest.mark.parametrize('allocation_first', [False, True])
def test_gui_range_edit_preserves_repeated_native_option_sections(allocation_first):
    from gxw.project_metadata import write_gui_system_variables
    allocation = ('[Option]\r\n\tSystemVariable:\r\n'
                  '\t\tDMin\tTYPE_DWORD\t10\r\n\t\tDMax\tTYPE_DWORD\t19\r\n'
                  '\t\tWordDevice\tTYPE_WORD\t0\r\n\t\tBitDevice\tTYPE_WORD\t3\r\n')
    other = '[Option]\r\n\tOptionEditor:\r\n\t\tTitle\tTYPE_STRING\t"配置"\r\n'
    text = (allocation + other if allocation_first else other + allocation) + '\0'
    changed = write_gui_system_variables(text.encode('utf-16le'), {'D': (10, 29)}, cpu='FX3U/FX3UC')
    assert changed.decode('utf-16le') == text.replace('DMax\tTYPE_DWORD\t19', 'DMax\tTYPE_DWORD\t29')


def test_new_factory_initialization_aligns_inactive_pools_before_gui_mirroring(monkeypatch):
    import struct
    from gxw.project_templates import blank_fbd_baseline
    from gxw.offline_project import read_offline_project
    from gxw.project_metadata import read_project_system_variables, project_gui_configuration
    from gxw.project_writer import write_project_replacements
    from gxw.visible_engineering import source_projection
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave

    raw = blank_fbd_baseline('FX3U')
    project = read_offline_project(raw)
    name = next(name for name in project.streams if name.endswith('.prj'))
    original = project.streams[name]
    offset = read_project_system_variables(original)['offset'] + 8  # R pair in native record.
    metadata = bytearray(original)
    struct.pack_into('<II', metadata, offset, 512, 7999)
    raw = write_project_replacements(raw, {name: (project.mapping[name], original, bytes(metadata))}, invalidate=False).data
    calls = []

    def native_setter(self, before, command, *, timeout=None):
        calls.append(command)
        assert command == {'operation': 'configure_project', 'source_name': name,
                           'options': {'system_variable_ranges': {'R': None}}}
        current = read_offline_project(before)
        metadata = bytearray(current.streams[name])
        struct.pack_into('<II', metadata, offset, 0xffffffff, 0xffffffff)
        return write_project_replacements(before, {name: (current.mapping[name], current.streams[name], bytes(metadata))}, invalidate=False)

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'author', native_setter)
    native = NativeWorkspaceSourceSave(installation=Path('.'))
    fixed = native._initialize_created_project(raw)
    assert project_gui_configuration(fixed)['status'] == 'matched'
    expected = source_projection(raw)
    expected['configuration']['ranges']['R'] = (0xffffffff, 0xffffffff)
    assert source_projection(fixed) == expected
    assert native._initialize_created_project(fixed) == fixed
    assert len(calls) == 1


@pytest.mark.parametrize('missing_result', [False, True])
def test_uncertain_native_compilation_is_not_converted_to_fallback(tmp_path, monkeypatch, missing_result):
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    (tmp_path / 'program.gxw').write_bytes(b'candidate')
    metadata = {'target_mode': 'st', 'plc_model': 'FX3U', 'delivery_requested': True,
                'artifacts': {'gxw': 'program.gxw'}, 'validation': {}}
    monkeypatch.setattr(delivery, 'installation', lambda: Path('E:/GX/GD2.exe'))
    def timeout(self, raw):
        error = WorkspaceSaveError('原生工作区没有返回完整结果。' if missing_result else '本次原生工作区操作超时，隔离进程已停止。')
        if missing_result: error.preserve_delivery = True
        raise error
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'compile_project', timeout)
    with pytest.raises(WorkspaceSaveError, match='完整结果' if missing_result else '超时'):
        delivery.finalize_saved(tmp_path, metadata)
    assert metadata['delivery']['actual_format'] == 'gxw'
    assert not metadata['delivery']['degraded']


def test_interrupted_delivery_retains_its_allocated_files_and_recovery_location(tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from agent_runtime.messages import ToolCall
    from tests.test_generation_workflow import _ladder
    from gxw.project_templates import blank_ladder_baseline
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    monkeypatch.setattr(delivery, 'installation', lambda: Path('E:/GX/GD2.exe'))
    monkeypatch.setattr(delivery, 'create_blank', lambda *args, **kwargs: blank_ladder_baseline('FX3U'))
    def timeout(self, raw): raise WorkspaceSaveError('Native compile timeout')
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'compile_project', timeout)
    with EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state') as session:
        session.invoke(ToolCall('create', 'create_project', {'name': 'Interrupted', 'plc_model': 'FX3U', 'target_mode': 'ladder'}))
        result = session.invoke(ToolCall('generate', 'create_program_candidate', {'ladder': _ladder()}))
        assert result.is_error
        proposal, = session.service.proposals.list(session.project_id)
        assert proposal['status'] == 'interrupted'
        retained = session.service.proposals._load(proposal['id'])['result']['observation']['delivery_output']
        assert (Path(retained['directory']) / 'program.gxw').is_file()
        assert not session.service.projects.raw_project(session.project_id)['active_version_id']


@pytest.mark.parametrize('mode', ['fbd', 'sfc'])
def test_web_structured_source_preview_and_saved_fallback_use_public_contract(tmp_path, mode):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from tests.test_web_api import _app, _login, ORIGIN
    workspace, state = tmp_path / 'workspace', tmp_path / 'state'
    service = WorkbenchService(workspace, state)
    if mode == 'fbd':
        from tests.test_web_fbd import fresh_two_timers
        arguments = {'operation': 'generate', 'model': fresh_two_timers()}
    else:
        arguments = {'operation': 'generate', 'graph_edits': [{'kind': 'rebuild', 'symbols': [
            {'kind': 'initial_step', 'x': 0, 'y': 0, 'number': 0},
            {'kind': 'transition', 'x': 0, 'y': 1, 'number': 0},
            {'kind': 'jump', 'x': 0, 'y': 2, 'number': 0}]}]}
    with TestClient(_app(workspace, state, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', json={'name': 'Source', 'plc_model': 'FX3U',
            'target_mode': mode}, headers=headers).json()
        result = service.agent_call({'project_id': project['id'], 'version_id': None,
            'name': 'create_' + mode + '_candidate', 'arguments': arguments, 'call_id': 'new-source'})
        proposal = result['proposal_id']
        preview = client.get('/api/proposals/' + proposal + '/preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()['program']['kind'] == 'source_candidate'
        accepted = client.post('/api/proposals/' + proposal + '/decision', json={'decision': 'accept'}, headers=headers)
        assert accepted.status_code == 200, accepted.text
        version = accepted.json()['proposal']['result']['version_id']
        program = client.get(f'/api/projects/{project["id"]}/versions/{version}/program')
        assert program.status_code == 200, program.text
        assert program.json()['source'] == preview.json()['program']['source']
        saved = client.get('/api/projects/' + project['id']).json()['versions'][0]
        assert saved['delivery']['primary_artifact'] == mode
        assert saved['delivery']['degraded']
        summary = client.get(f'/api/projects/{project["id"]}/versions/{version}/delivery')
        assert summary.status_code == 200, summary.text


@pytest.mark.parametrize('mode', ['st', 'sfc', 'fbd'])
def test_web_new_source_generation_saves_shared_delivery_without_native_install(tmp_path, monkeypatch, offline, mode):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    import application.model_api as model_api
    from tests.test_web_api import _app, _login, _complete, _Provider, ORIGIN
    source = {'operation': 'generate', 'st_text': 'Y0 := X0;', 'declarations': [
        {'name': 'local_flag', 'data_type': 'BOOL', 'class_name': 'VAR'}]} if mode == 'st' else {
        'operation': 'generate', 'graph_edits': [{'kind': 'rebuild', 'symbols': [
            {'kind': 'initial_step', 'x': 0, 'y': 0, 'number': 0},
            {'kind': 'transition', 'x': 0, 'y': 1, 'number': 0},
            {'kind': 'jump', 'x': 0, 'y': 2, 'number': 0}]}]}
    if mode == 'fbd':
        from tests.test_web_fbd import fresh_two_timers
        source = {'summary': 'New FBD', 'model': fresh_two_timers()}
    requests = []
    def respond(*args, **kwargs):
        requests.append(args)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(source)))
    monkeypatch.setattr(model_api, '_request_model', respond)
    workspace, state = tmp_path / 'workspace', tmp_path / 'state'
    provider = _Provider()
    service = WorkbenchService(workspace, state, model_factory=lambda: (provider, {'model': provider.profile['model']}))
    with TestClient(_app(workspace, state, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', json={'name': 'Generate source', 'plc_model': 'FX3U',
            'target_mode': mode}, headers=headers).json()
        service.store.set_confirmed_spec(project['id'], {'schema_version': 4, 'summary': 'X0 drives Y0',
            'io_table': [{'address': 'X0', 'kind': 'X', 'label': 'Input'}, {'address': 'Y0', 'kind': 'Y', 'label': 'Output'}],
            'parameters': [], 'selected_approach': {'approach_id': 'user', 'name': 'Direct'}})
        _, output = _complete(client, service, client.post('/api/jobs', json={'kind': 'generation',
            'project_id': project['id'], 'request_id': 'generate-source', 'text': 'X0 drives Y0'}, headers=headers))
        assert len(requests) == 1
        assert output['generation']['delivery']['degraded']
        saved = service.projects.raw_version(project['id'], output['version_id'])
        if mode == 'st':
            candidate = json.loads(service.projects.artifact(project['id'], saved['id'], 'source_candidate').read_bytes())
            assert candidate['declarations'] == source['declarations']
        else:
            preview = client.get('/api/proposals/' + output['proposal_id'] + '/preview')
            assert preview.status_code == 200, preview.text
