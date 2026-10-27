"""FBD approval, persistence and desktop boundaries, using isolated workspaces."""

from tools.offline_inputs import private_path
import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest


def test_previous_fbd_native_save_preference_does_not_authorize_a_later_edit(monkeypatch):
    from application.fbd import _write_candidate, version_requires_native_save
    from gxw.object_model import default_baseline, read_project_context
    import application.fbd as fbd
    monkeypatch.setattr(fbd, '_native_candidate', lambda *a, **kw: pytest.fail('implicit native save'))
    context = read_project_context(default_baseline())
    model = context.object_model()
    model['nodes'][0]['symbol'] = 'X123'
    saved_version = {'validation':{'native_save_requested':True, 'native_save':'passed'}}
    assert version_requires_native_save(saved_version) is False
    result = _write_candidate(model, context.raw, native_save=version_requires_native_save(saved_version))
    assert result.report['write_backend'] == 'offline'
    assert read_project_context(result.data).program.nodes[0].symbol == 'X123'


def test_current_explicit_fbd_native_save_cannot_reuse_an_offline_preview(service, monkeypatch):
    import application.fbd as fbd
    from gxw.object_model import default_baseline, read_project_context
    from gxw.project_writer import write_prepared_project
    raw = default_baseline()
    project = service.create_project(name='Explicit one-shot native save', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import',project_id=project['id'],
        request_id='import-explicit',data_base64=base64.b64encode(raw).decode())))
    model = read_project_context(raw).object_model()
    model['nodes'][0]['symbol'] = 'X234'
    calls = []
    def native(operation, prepared):
        # Transport double only: this is not native acceptance evidence.
        assert operation == 'save'
        calls.append(prepared)
        return write_prepared_project(prepared)
    monkeypatch.setattr(fbd, '_native_candidate', native)
    service.fbd.preview(project['id'],model,base)
    assert calls == []
    command = dict(operation='edit',project_id=project['id'],version_id=base,
        request_id='current-explicit',model=model,native_save=True)
    proposal = service.fbd.propose(command)
    assert len(calls) == 1
    assert service.fbd.propose(command)['id'] == proposal['id']
    assert len(calls) == 1


@pytest.mark.parametrize('current_native', [False, True])
def test_model_fbd_edit_uses_only_the_current_native_save_argument(tmp_path, monkeypatch, current_native):
    import application.fbd as fbd
    import application.model_api as model_api
    from gxw.object_model import default_baseline, read_project_context
    from gxw.project_writer import write_prepared_project
    raw=default_baseline();model=read_project_context(raw).object_model();model['nodes'][0]['symbol']='X345'
    monkeypatch.setattr(model_api,'_request_model',lambda *a, **kw:SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({'summary':'Offline edit','model':model}))))
    calls=[]
    def native(operation,prepared):
        assert operation=='save';calls.append(operation)
        return write_prepared_project(prepared)
    monkeypatch.setattr(fbd,'_native_candidate',native)
    result=fbd.generate_candidate(tmp_path,dict(project={'plc_model':'FX3U'},
        version={'validation':{'native_save_requested':True}}, fbd_baseline=base64.b64encode(raw).decode(),
        text='Change input',native_save=current_native),[],SimpleNamespace(emit=lambda *a:None,checkpoint=lambda:None))
    assert calls==(['save'] if current_native else [])
    assert result['metadata']['validation']['native_save_requested'] is current_native
    assert result['metadata']['validation']['invalidated_results'] == {
        'scope': 'project_version', 'compilation': 'stale',
        'addresses': 'stale', 'diagnostics': 'stale'}


def test_internal_topology_candidate_reports_layers_without_claiming_native_or_behavior(tmp_path):
    from application.fbd import prepare_topology_candidate
    from gxw.object_model import default_baseline
    from tests.test_gxw_object_model import composition_topology
    payload = prepare_topology_candidate(tmp_path, composition_topology(), baseline=default_baseline(), plc_model="FX3U")
    acceptance = payload["metadata"]["engineering_acceptance"]
    assert acceptance["status"] == "unverified"
    assert [r["status"] for r in acceptance["layers"]] == ["passed", "passed", "unverified", "unverified", "unverified"]
    assert payload["metadata"]["validation"]["gx_compile"] == "not_run"


def test_imported_l_fbd_export_initializes_missing_pointer_range_through_shared_save(service, monkeypatch):
    import zipfile
    from pathlib import Path
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fbd-ui-entry-20261013.zip')) as archive:
        prefix = 'l-pointer-allocation/application-pointer-L02S-fixed/'
        entries = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist() if name.startswith(prefix)}
    original, candidate = entries['original.gxw'], entries['candidate.gxw']
    calls = []

    def save(_self, prepared, *, timeout=None):
        assert prepared.baseline == original and not prepared.outer.get('Project.gd2')
        assert native_source_plan(prepared)['system_variable_initialization'] == {
            'offset': 68, 'before': [-1, -1], 'after': [2048, 4095]}
        calls.append('save')
        return verify_native_save(prepared, candidate,
                                  json.loads(entries['save-observation.json']))

    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    project = service.create_project(name='L02S pointer initialization', plc_model='L02S/L02S-P', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='import-pointer',
        program='POU_01.Program.pou', data_base64=base64.b64encode(original).decode())))
    model = service.fbd.editor(project['id'], version_id=base)['model']
    node, = [n for n in model['nodes'] if n['symbol'] == 'APP_SOURCE_ALPHA']
    model = service.fbd.editor(project['id'], model, dict(action='update_node', id=node['id'], field='symbol',
        value='APP_POINTER_ALPHA'), base)['model']
    preview = service.fbd.preview(project['id'], model, base)
    assert calls == []
    final = accept(service, service.fbd.propose(dict(operation='edit', project_id=project['id'], version_id=base,
        request_id='save-pointer', model=model, native_save=True)))
    assert calls == ['save']
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == original
    assert service.projects.artifact(project['id'], final, 'gxw').read_bytes() == candidate
    assert service.fbd.editor(project['id'], version_id=final)['model'] == preview['model'] == json.loads(entries['saved-model.json'])

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from application.execution import GXExecutionCoordinator
from application.fbd import prepare_candidate
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from gxw.object_model import default_baseline, read_project, read_project_context
from tests.test_gxw_object_model import (two_timers, source_binding_baseline, project_rows_variant,
                                       flat_project_structure_baseline, compound_structure_context, created_fbd_context,
                                       source_creation_witness, source_creation_stage, created_inout_context)
from tests.test_execution_coordinator import FakeCOM
from tests.test_web_api import _app, _login, ORIGIN
from tests.test_workbench_service import _program


@pytest.fixture
def service(tmp_path):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", model_factory=lambda: (None, {}))
    service.start()
    yield service
    service.close()


@pytest.fixture(autouse=True)
def isolated_replay_desktop_lock(tmp_path, monkeypatch):
    """Frozen native replays must not contend with an actual GX desktop run."""
    import application.execution as execution
    implementation = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock',
        lambda path=None: implementation(path if path is not None else tmp_path / 'replay-desktop.lock'))


def generate(service, request_id="generate"):
    p = service.create_project(name="FBD", target_mode="fbd")
    command = {"operation": "generate", "project_id": p["id"], "request_id": request_id, "model": fresh_two_timers()}
    return p, command, service.fbd.propose(command)


def fresh_two_timers():
    from application.fbd import _target_context
    model = two_timers()
    model['program'] = _target_context(None, 'FX3U').program.logical_name
    return model


def accept(service, proposal):
    result = service.decide(proposal["id"], "accept")["proposal"]["result"]
    return result["version_id"]


def visible_native_version(service):
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    raw = _visible_cross_reference_witness()
    project = service.create_project(name='Visible native session', target_mode='fbd', plc_model='FX3U')
    proposal = service.fbd.propose({'operation': 'import', 'project_id': project['id'], 'request_id': 'native-baseline',
        'program': 'POU_01.Program.pou', 'data_base64': base64.b64encode(raw).decode()})
    return project['id'], accept(service, proposal), raw


def visible_native_plan():
    return {'schema_version': 1, 'commands': [{'operation': 'compile'}, {'operation': 'show_assignments',
        'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]},
        {'operation': 'save'}, {'operation': 'reopen'}]}


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_native_engineering_shared_action_freezes_approval_and_commits_one_verified_version(service, monkeypatch, mode):
    from concurrent.futures import Future
    from tests.test_execution_coordinator import native_delivery_receipt
    pid, base, raw = visible_native_version(service)
    service.update_approval_settings(mode=mode, expected_revision=0, confirm_full_access=True)
    calls = []
    def execute(operation, payload, **options):
        assert operation == 'native_engineering'
        calls.append(deepcopy(payload))
        future = Future()
        future.set_result({'status': 'completed', 'passed': True,
            '_native_result': native_delivery_receipt(raw, payload['plan']['commands'])})
        return future
    monkeypatch.setattr(service.execution, 'submit_approved', execute)
    plan = visible_native_plan()
    command = {'project_id': pid, 'version_id': base, 'name': 'execute_native_engineering',
               'arguments': {'plan': plan}, 'call_id': 'native-shared'}
    response = service.agent_call(command, direct_request=True)
    assert not response['is_error'], response
    proposal_id = response['proposal_id']
    original = deepcopy(plan)
    plan['commands'].append({'operation': 'save', 'pid': 123})
    assert service.proposals.read_private(proposal_id)['plan'] == original
    if mode == 'full':
        job_id = response['execution_job_id']
    else:
        assert not calls and service.proposals.get(proposal_id)['status'] == 'pending'
        job_id = service.decide(proposal_id, 'accept')['job']['id']
    service.jobs._futures[job_id].result(timeout=15)
    saved = service.proposals.get(proposal_id)
    assert saved['status'] == 'accepted', saved
    result = saved['result']
    final = result['version_id']
    assert final != base and result['passed'] is True and result['gx_compile_status'] == 'passed'
    assert service.projects.project(pid)['version_count'] == 2
    assert service.store.get_project(pid)['active_version_id'] == final
    assert service.projects.artifact(pid, base, 'gxw').read_bytes() == raw
    assert service.projects.artifact(pid, final, 'gxw').read_bytes() == raw
    assert service.projects.raw_version(pid, final)['parent_version_id'] == base
    assert service.projects.raw_version(pid, final)['validation']['native_source_save_required'] is True
    encoded = json.dumps(result)
    assert 'private' not in encoded and 'observation' not in encoded and 'identity' not in encoded
    assert 'data' not in result and result['findings'][-1]['status'] == 'unverified'
    # Approval/call replay must not launch a second native session or version.
    replay_job = service.decide(proposal_id, 'accept')['job']['id']
    service.jobs._futures[replay_job].result(timeout=15)
    replay = {**command, 'arguments': {'plan': original}}
    assert service.agent_call(replay, direct_request=True)['proposal_id'] == proposal_id
    assert len(calls) == 1 and service.projects.project(pid)['version_count'] == 2


@pytest.mark.parametrize('damage', ['failed_compile', 'unverified_bytes', 'stale_reopen'])
def test_native_engineering_failed_delivery_never_activates_observed_bytes(service, monkeypatch, damage):
    from concurrent.futures import Future
    from tests.test_execution_coordinator import native_delivery_receipt
    pid, base, raw = visible_native_version(service)
    plan = visible_native_plan()
    receipt = native_delivery_receipt(raw, plan['commands'])
    if damage == 'failed_compile':
        receipt.update(status='interrupted', passed=False)
        receipt['compile'].update(status='failed', errors=2)
    elif damage == 'unverified_bytes':
        receipt['unverified_data'] = b'private-observed-source'
    else:
        receipt['reopen']['revision'] = 88
    def execute(*_args, **_options):
        future = Future(); future.set_result({'status': 'completed', 'passed': True, '_native_result': receipt})
        return future
    monkeypatch.setattr(service.execution, 'submit_approved', execute)
    proposal = service.execution_proposal({'action': 'native_engineering', 'project_id': pid,
        'version_id': base, 'plan': plan, 'request_id': 'native-failure'})
    job = service.decide(proposal['id'], 'accept')['job']
    service.jobs._futures[job['id']].result(timeout=15)
    saved = service.proposals.get(proposal['id'])
    assert saved['status'] == 'interrupted', saved
    assert saved['result']['passed'] is False
    assert service.store.get_project(pid)['active_version_id'] == base
    assert service.projects.project(pid)['version_count'] == 1
    assert not (service.proposals.directory / proposal['id']).exists()
    assert 'private' not in json.dumps(saved) and 'data' not in saved['result']


def test_http_native_engineering_accepts_only_explicit_saved_version_plan_with_operator_approval(tmp_path):
    from tests.test_web_api import AGENT
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state', model_factory=lambda: (None, {}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        pid, base, _raw = visible_native_version(service)
        command = {'action': 'native_engineering', 'project_id': pid, 'version_id': base,
                   'plan': visible_native_plan(), 'request_id': 'native-http'}
        anonymous = TestClient(client.app, base_url=ORIGIN)
        assert anonymous.post('/api/proposals', json=command, headers={'Authorization': 'Bearer ' + AGENT}).status_code == 401
        assert client.post('/api/proposals', json=command).status_code == 403
        response = client.post('/api/proposals', json=command, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()['action'] == 'native_engineering' and response.json()['status'] == 'pending'
        assert service.execution._thread is None
        invalid = deepcopy(command)
        invalid.update(request_id='native-path')
        invalid['plan']['commands'][0]['path'] = r'D:\unmanaged.gxw'
        assert client.post('/api/proposals', json=invalid, headers=headers).status_code in (400, 422)
        assert service.store.get_project(pid)['active_version_id'] == base


def program_creation_native_replay(monkeypatch, tmp_path):
    import zlib
    from tests.test_gxw_object_model import empty_program_registration_witness
    from gxw.native_write import verify_native_scope_creation, verify_native_source_creation, verify_native_program_task_binding
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    import application.execution as execution
    before, after, case = empty_program_registration_witness()
    states = {key: zlib.decompress(base64.b64decode(value)) for key, value in case['creation_inputs'].items()}
    resource, task, name = case['resource'], case['task'], 'OwnedProgram_Core_C4'
    calls = []
    lock_path = tmp_path / 'native.lock'
    real_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: real_lock(lock_path))

    def held():
        with pytest.raises(execution.ExecutionUnavailableError), real_lock(lock_path):
            pass

    def create_resource(_self, data, selected, *, timeout):
        held(); assert data == states['resource'] and selected == resource and timeout is None
        calls.append('resource')
        return verify_native_scope_creation(data, selected, states['task'], case['creation_observations']['resource'], scope_kind='resource')

    def create_task(_self, data, selected, *, resource, timeout):
        held(); assert data == states['task'] and selected == task and resource == case['resource'] and timeout is None
        calls.append('task')
        return verify_native_scope_creation(data, selected, states['program'], case['creation_observations']['task'], scope_kind='task', resource=resource)

    def create(_self, data, selected, *, pou_kind, timeout):
        held(); assert data == states['program'] and selected == name and pou_kind == 'program' and timeout is None
        calls.append('program')
        return verify_native_source_creation(data, selected, before, case['creation_observations']['program'], pou_kind=pou_kind)

    def bind(_self, data, program, *, resource, task, timeout):
        held(); assert data == before and program == case['program'] and timeout is None
        calls.append('bind')
        return verify_native_program_task_binding(data, program, after, case['native_creation'], resource=resource, task=task)

    for method, implementation in (('create_resource', create_resource), ('create_task', create_task), ('create', create), ('bind_program', bind)):
        monkeypatch.setattr(NativeWorkspaceSourceSave, method, implementation)
    return states, after, case, calls


@pytest.mark.parametrize('scope_mode', ['existing_task', 'new_task', 'new_resource'])
def test_program_candidate_creates_and_registers_native_objects_under_one_lock(service, tmp_path, monkeypatch, scope_mode):
    states, after, case, calls = program_creation_native_replay(monkeypatch, tmp_path)
    phase = {'existing_task': 'program', 'new_task': 'task', 'new_resource': 'resource'}[scope_mode]
    raw = states[phase]
    resource, task, name = case['resource'], case['task'], 'OwnedProgram_Core_C4'
    project = service.create_project(name='Native Program', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='import',
        data_base64=base64.b64encode(raw).decode(), program='FBD_MATRIX.Program.pou')))
    command = dict(operation='create_program', project_id=project['id'], version_id=base, request_id='create-program',
        source_name=name, resource=resource, task=task, scope_mode=scope_mode)
    proposal = service.fbd.propose(command)
    version = accept(service, proposal)
    assert service.fbd.propose(command)['id'] == proposal['id']
    assert calls == (['resource', 'task'] if scope_mode == 'new_resource' else ['task'] if scope_mode == 'new_task' else []) + ['program', 'bind']
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
    assert service.projects.artifact(project['id'], version, 'gxw').read_bytes() == after
    preview = service.proposal_preview(proposal['id'])
    editor = service.fbd.editor(project['id'], version_id=version)
    assert preview['editor']['model'] == preview['program'] == editor['model']
    assert editor['model']['program'] == case['saved_program'] and editor['model']['cpu'] == case['cpu']
    assert editor['model']['schema_version'] == 2 and not editor['model']['nodes']
    selected = [row for row in editor['task_scopes']['tasks'] if row['resource'] == resource and row['task'] == task]
    assert selected[0]['programs'] == [{'name': name, 'line': 1}]
    assert preview['editor']['task_scopes'] == editor['task_scopes']
    validation = service.projects.raw_version(project['id'], version)['validation']
    assert validation['native_program_registration'] == 'passed' and validation['native_source_save_required'] is False
    assert validation['gx_compile'] == 'not_run'
    report = json.loads(service.projects.artifact(project['id'], version, 'write_report').read_bytes())
    assert report['created_program'] == case['saved_program']
    assert len(report['stages']) == len(calls) and report['validation']['gxworks_check'] == 'not_run'


@pytest.mark.parametrize('entry', ['web', 'connected_agent'])
def test_program_creation_http_and_connected_agent_reuse_the_shared_candidate(tmp_path, monkeypatch, entry):
    from tests.test_web_api import AGENT
    states, after, case, calls = program_creation_native_replay(monkeypatch, tmp_path)
    raw = states['program']
    with TestClient(_app(tmp_path / 'workspace', tmp_path / 'state'), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={'name': entry, 'target_mode': 'fbd'}).json()
        imported = client.post('/api/fbd/proposals', headers=headers, json={'operation': 'import',
            'project_id': project['id'], 'request_id': 'import', 'program': 'FBD_MATRIX.Program.pou',
            'data_base64': base64.b64encode(raw).decode()})
        assert imported.status_code == 201, imported.text
        selection = {'project_id': project['id'], 'version_id': imported.json()['result']['version_id']}
        arguments = {'operation': 'create_program', 'source_name': 'OwnedProgram_Core_C4',
            'resource': case['resource'], 'task': case['task'], 'scope_mode': 'existing_task'}
        if entry == 'web':
            command = {**selection, **arguments, 'request_id': 'create-program'}
            assert client.post('/api/fbd/proposals', json=command).status_code == 403
            response = client.post('/api/fbd/proposals', headers=headers, json=command)
            assert response.status_code == 201, response.text
            proposal = response.json()
        else:
            endpoint = '/api/agent/tools/call'
            auth = {'Authorization': 'Bearer ' + AGENT}
            catalog = client.post(endpoint, headers=auth, json={**selection, 'name': 'get_fbd_catalog',
                'call_id': 'catalog', 'arguments': {}}).json()['data']['data']
            web_catalog = client.get('/api/fbd/catalog', params=selection).json()
            assert catalog['task_scopes'] == web_catalog['task_scopes']
            command = {**selection, 'name': 'create_fbd_candidate', 'call_id': 'create-program', 'arguments': arguments}
            response = client.post(endpoint, headers=auth, json=command)
            assert response.status_code == 200 and not response.json()['is_error'], response.text
            proposal_id = response.json()['proposal_id']
            preview = client.get('/api/proposals/' + proposal_id + '/preview').json()
            assert preview['program']['program'] == case['saved_program']
            accepted = client.post('/api/proposals/' + proposal_id + '/decision', headers=headers, json={'decision': 'accept'})
            assert accepted.status_code == 200, accepted.text
            proposal = accepted.json()['proposal']
        version = proposal['result']['version_id']
        assert calls == ['program', 'bind']
        assert client.get(f'/api/projects/{project["id"]}/versions/{version}/artifacts/gxw').content == after
        editor = client.post('/api/fbd/editor', headers=headers, json={**selection, 'version_id': version}).json()
        assert editor['model']['program'] == case['saved_program'] and editor['model']['schema_version'] == 2
        current, = [row for row in editor['task_scopes']['tasks'] if row['task'] == case['task']]
        assert current['programs'] == [{'name': 'OwnedProgram_Core_C4', 'line': 1}]


@pytest.mark.parametrize('extra', [dict(source_name=None), dict(model={}), dict(program='FBD_MATRIX.Program.pou'),
                                 dict(data_base64='AA=='), dict(resource=None), dict(task=None),
                                 dict(scope_mode=None), dict(scope_mode='unknown')])
def test_program_creation_rejects_incomplete_or_mixed_requests_before_native_call(service, tmp_path, monkeypatch, extra):
    states, _, case, calls = program_creation_native_replay(monkeypatch, tmp_path)
    raw = states['resource']
    project = service.create_project(name='Program preflight', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='import',
        data_base64=base64.b64encode(raw).decode(), program='FBD_MATRIX.Program.pou')))
    command = dict(operation='create_program', project_id=project['id'], version_id=base, request_id='create',
        source_name='OwnedProgram_Core_C4', resource=case['resource'], task=case['task'], scope_mode='new_resource')
    with pytest.raises(ValueError):
        service.fbd.propose({**command, **extra})
    assert not calls
    assert len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


def test_program_creation_registration_failure_and_busy_lock_leave_the_base_version_intact(service, tmp_path, monkeypatch):
    import application.execution as execution
    from application.fbd import FBDValidationError
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    states, _, case, calls = program_creation_native_replay(monkeypatch, tmp_path)
    raw = states['resource']
    project = service.create_project(name='Program registration failure', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='import',
        data_base64=base64.b64encode(raw).decode(), program='FBD_MATRIX.Program.pou')))

    def fail_registration(*_, **__):
        calls.append('bind')
        raise WorkspaceSaveError('task-registration-readback-failed')

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'bind_program', fail_registration)
    command = dict(operation='create_program', project_id=project['id'], version_id=base, request_id='create',
        source_name='OwnedProgram_Core_C4', resource=case['resource'], task=case['task'], scope_mode='new_resource')
    with execution.DesktopResourceLock(), pytest.raises(ConflictError, match='原生资源'):
        service.fbd.propose(command)
    assert not calls
    with pytest.raises(FBDValidationError, match='task-registration-readback-failed'):
        service.fbd.propose(command)
    assert calls == ['resource', 'task', 'program', 'bind']
    with execution.DesktopResourceLock():
        pass
    assert len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


def test_candidate_is_reviewed_frozen_idempotent_and_accepted_as_fbd(service):
    p, command, proposal = generate(service)
    assert len(service.projects.raw_project(p["id"])["versions"]) == 1
    assert proposal["status"] == "accepted"
    preview = service.proposal_preview(proposal["id"])
    assert preview["target_mode"] == "fbd"
    assert "TIMER_A" in preview["svg"]
    assert preview["diff"]["after_object_count"] == 6
    assert preview["diff"]["declarations_changed"]
    assert service.fbd.propose(command)["id"] == proposal["id"]
    version_id = accept(service, proposal)
    assert service.fbd.propose(command)["status"] == "accepted"
    assert accept(service, proposal) == version_id
    version = service.projects.version(p["id"], version_id)
    assert version["target_mode"] == "fbd"
    assert version["validation"]["gx_compile"] == "not_run"
    assert version["capabilities"]["operations"]["fbd_edit"]
    assert not version["capabilities"]["operations"]["simulation"]
    raw = service.projects.artifact(p["id"], version_id, "gxw").read_bytes()
    program, declarations, _ = read_project(raw)
    assert service.projects.program(p["id"], version_id) == read_project_context(raw).object_model()
    assert [row.name for row in declarations["FBD_MATRIX.Labels.lh"].rows] == ["TIMER_A", "TIMER_B"]
    with pytest.raises(ConflictError):
        service.fbd.propose({**command, "operation": "edit"})


def test_edits_bind_base_and_synchronize_labels_without_mutating_prior_version(service):
    p, _, proposal = generate(service)
    base = accept(service, proposal)
    original = service.projects.artifact(p["id"], base, "gxw").read_bytes()
    model = service.projects.program(p["id"], base)
    model["nodes"][0]["symbol"] = "TON_C"
    model["declaration_edits"] = {"FBD_MATRIX.Labels.lh": {"renames": {"TIMER_A": "TON_C"}}}
    edit = service.fbd.propose({"operation": "edit", "project_id": p["id"], "version_id": base,
        "request_id": "edit", "model": model})
    assert service.proposal_preview(edit["id"])["diff"]["declarations_changed"]
    newer = accept(service, edit)
    assert service.projects.artifact(p["id"], base, "gxw").read_bytes() == original
    assert service.projects.program(p["id"], newer)["labels"]["FBD_MATRIX.Labels.lh"][0]["name"] == "TON_C"
    assert service.projects.version(p["id"], newer)["parent_version_id"] == base


def test_changed_artifact_and_false_preview_are_rejected_before_acceptance(service):
    p = service.create_project(name="Tamper fixture", target_mode="fbd")
    pending = prepare_candidate(service.state_dir / "pending-tamper", model=fresh_two_timers())
    proposal = service.proposals.create("accept_local", p["id"], pending)
    payload = service.proposals.read_private(proposal["id"])
    from pathlib import Path
    (Path(payload["staging_dir"]) / "program.gxw").write_bytes(b"changed")
    with pytest.raises(ConflictError):
        accept(service, proposal)
    assert not service.projects.raw_project(p["id"])["versions"]
    data = prepare_candidate(service.state_dir / "tampered", model=fresh_two_timers())
    svg = Path(data["staging_dir"]) / "fbd.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    import hashlib
    data["artifacts"]["svg"]["sha256"] = hashlib.sha256(svg.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="preview"):
        service.proposals.create("accept_local", p["id"], data)


def test_existing_ladder_converts_with_comments_in_source_artifact(service):
    from plc.core import PLCCore
    p = service.create_project(name="Relay", target_mode="ladder")
    source = _program()
    vid, root = service.store.prepare_version(p["id"])
    service.store.complete_version(p["id"], vid, {"target_mode": "ladder", "plc_model": "FX3U",
        "artifacts": PLCCore().compile_project(source, root)["artifacts"]}, activate=True)
    proposal = service.fbd.propose({"operation": "convert", "project_id": p["id"], "version_id": vid, "request_id": "convert"})
    result = accept(service, proposal)
    assert json.loads(service.projects.artifact(p["id"], result, "source_ladder").read_text(encoding="utf-8")) == source
    assert {n["symbol"] for n in service.projects.program(p["id"], result)["nodes"]} == {"X0", "Y0"}


def test_http_import_download_review_and_csrf_are_consistent(tmp_path):
    with TestClient(_app(tmp_path / "workspace", tmp_path / "state"), base_url=ORIGIN) as client:
        headers = _login(client)
        p = client.post('/api/projects', json={"name": "Native", "target_mode": "fbd"}, headers=headers).json()
        raw = default_baseline()
        upload = {"filename": "source.gxw", "data_base64": base64.b64encode(raw).decode()}
        assert client.post('/api/fbd/inspect', json=upload, headers=headers).json()["programs"] == ["1.Program.pou"]
        command = {"operation": "import", "project_id": p["id"], "request_id": "upload", "data_base64": upload["data_base64"]}
        assert client.post('/api/fbd/proposals', json=command).status_code == 403
        response = client.post('/api/fbd/proposals', json=command, headers=headers)
        assert response.status_code == 201, response.text
        proposal = response.json()
        preview = client.get('/api/proposals/' + proposal["id"] + '/preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()["target_mode"] == "fbd"
        result = client.post('/api/proposals/' + proposal["id"] + '/decision', json={"decision": "accept"}, headers=headers)
        assert result.status_code == 200, result.text
        vid = result.json()["proposal"]["result"]["version_id"]
        download = client.get(f'/api/projects/{p["id"]}/versions/{vid}/artifacts/gxw?download=true')
        assert download.content == raw
        assert 'attachment' in download.headers['content-disposition']


def test_generation_uses_model_contract_and_freezes_gxw_before_worker(service, monkeypatch):
    import application.model_api as api
    captured = []
    def model(messages, **kwargs):
        captured.append((messages, kwargs))
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({"summary": "两个定时器串接", "model": fresh_two_timers()})))
    monkeypatch.setattr(api, "_request_model", model)
    p = service.create_project(name="AI FBD", target_mode="fbd")
    command = {"kind": "generation", "project_id": p["id"], "request_id": "ai", "text": "两个定时器串接", "response_language": "zh-CN"}
    job = service.submit(command)
    service.jobs._futures[job["id"]].result(timeout=15)
    assert service.jobs.get(job["id"])["status"] == "completed", service.jobs.get(job["id"])
    assert captured[0][1]["response_contract"].name == "fbd"
    proposal = service.proposals.get(service.output(job["id"])["proposal_id"])
    vid = accept(service, proposal)
    # Snapshot evidence catches file-only changes even if version metadata is unchanged.
    raw = service.projects.artifact(p["id"], vid, "gxw").read_bytes()
    snapshot = {"project_id": p["id"], "project": service.projects.raw_project(p["id"]),
        "version": service.projects.raw_version(p["id"], vid), "fbd_baseline": base64.b64encode(raw).decode()}
    service.projects.artifact(p["id"], vid, "gxw").write_bytes(raw + b'changed')
    with pytest.raises(ConflictError):
        service._check_snapshot(snapshot)


def test_fbd_generation_commits_st_interface_body_and_all_callers_in_one_model_response(tmp_path, monkeypatch):
    from agent_runtime.plc_tools import build_tool_context
    from application.gxw_files import generate_candidate
    import application.model_api as model_api
    from gxw.fb_authoring import _context, _rows
    from gxw.object_model import read_project_context
    from gxw.offline_project import read_offline_project
    from gxw.project_writer import write_project_replacements
    from gxw.source_header import source_payload_offset
    from gxw.text_pou import parse_st_pou
    from tests.test_gxw_offline_authoring import native_project
    raw = native_project()
    project = read_offline_project(raw)
    second = 'CalcOutput_ST.Program.pou'
    old, graph = project.streams[second], project.streams['POU_01.Program.pou']
    converted = old[:source_payload_offset(old)] + graph[source_payload_offset(graph):]
    raw = write_project_replacements(raw, {second: (project.mapping[second], old, converted)}).data
    _, _, _, sources, _ = _context(raw)
    declarations = _rows(sources.declarations['ScaleValue.Labels.lh'], sources)
    for row in declarations:
        if row['name'] == 'InValue':
            row['class_name'] = 'VAR_IN_OUT'
    body = 'InValue := InValue + 1; OutValue := InValue;'
    edits = [dict(source_name='ScaleValue.Program.pou', command=dict(operation='edit_st_fb',
        st_text=body, declarations=declarations))]
    callers = ['POU_01.Program.pou', second]
    for source, actual in zip(callers, ['D0', 'D20']):
        call = dict(id='scale', type='ScaleValue', instance='ScaleInst',
                    arguments=dict(Enabled='TRUE', InValue=actual, OutValue='D40'))
        edits.append(dict(source_name=source, command=dict(model=dict(program=source, networks=[dict(calls=[call])]))))
    captured = []
    def response(messages, **kwargs):
        captured.append((messages, kwargs))
        wire = json.loads(messages[1].content)['context']['generation_input']
        assert {row['source_name'] for row in wire['source_context']['fbd_callers']} == set(callers)
        assert any(row['source_name'] == 'ScaleValue.Program.pou' for row in wire['source_context']['st_sources'])
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(dict(summary='联合修改',
            tool='edit_gxw_project', arguments=dict(version_id='v1', edits=edits, save_policy='checked')))))
    monkeypatch.setattr(model_api, '_request_model', response)
    import application.fbd as fbd
    monkeypatch.setattr(fbd, '_native_candidate', lambda *a, **k: pytest.fail('unexpected native operation'))
    context = build_tool_context(dict(id='joint-generation', target_mode='fbd', plc_model='FX3U'),
        version=dict(id='v1', target_mode='fbd', program_name=callers[0]), fbd_baseline=raw, native_snapshot=raw)
    job = SimpleNamespace(emit=lambda *a, **k: None, checkpoint=lambda: None)
    result = generate_candidate(tmp_path, dict(text='Change ScaleValue and all callers'), context, [], job)
    saved = (tmp_path / result['artifacts']['gxw']['path']).read_bytes()
    after = read_offline_project(saved)
    assert len(captured) == 1
    assert captured[0][1]['response_contract'].name == 'gxw_existing_edit'
    assert parse_st_pou(after.streams['ScaleValue.Program.pou']).text == body
    for source, actual in zip(callers, ['D0', 'D20']):
        bound = read_project_context(saved, source)
        assert any(row.class_code == 5 and row.name == 'InValue'
                   for row in bound.sources.declarations['ScaleValue.Labels.lh'].rows)
        assert any(node.kind.value == 'input' and node.symbol == actual for node in bound.program.nodes)
    assert result['metadata']['generation_handoff']['tool'] == 'edit_gxw_project'


@pytest.mark.parametrize('cpu,has_wrong_baseline', [
    ('Q00J', True), ('Q03UDV', True), ('L02', True), ('FX1N', True),
    ('FX5U', False), ('LJ72GF15-T2', False), ('FX1', False), ('FX1', True)])
def test_fbd_target_mismatch_is_rejected_before_model_or_native_write(tmp_path, monkeypatch, cpu, has_wrong_baseline):
    import application.fbd as fbd
    import application.model_api as api
    calls = []
    monkeypatch.setattr(api, '_request_model', lambda *a, **k: calls.append('model'))
    monkeypatch.setattr(fbd, '_write_candidate', lambda *a, **k: calls.append('write'))
    baseline = default_baseline() if has_wrong_baseline else None
    snapshot = {'project': {'plc_model': cpu}, 'text': 'M20 follows the first scan relay'}
    if baseline is not None:
        snapshot['fbd_baseline'] = base64.b64encode(baseline).decode()
    with pytest.raises(fbd.FBDValidationError, match='CPU|template'):
        fbd.generate_candidate(tmp_path, snapshot, [], SimpleNamespace(emit=lambda *_: None, checkpoint=lambda: None))
    with pytest.raises(fbd.FBDValidationError, match='CPU|template'):
        fbd.prepare_candidate(tmp_path, model=two_timers(), baseline=baseline, plc_model=cpu)
    assert calls == []
    assert not list(tmp_path.iterdir())


def test_fbd_imported_baseline_keeps_merged_cpu_menu_scope():
    from application.fbd import _target_context, inspect_upload
    from gxw.container_writer import replace_project_stream
    from gxw.models import GXWFormatError
    from tests.test_gxw_structured_reader import offline_fbd_project
    # Native FX3U/FX3UC is one menu identity; this permits a bound source,
    # without claiming that either physical CPU has been execution-tested.
    context = _target_context(default_baseline(), 'FX3UC')
    assert context.sources.cpu == 'FX3U/FX3UC'
    signed, case = offline_fbd_project(0)
    assert case['source'] in inspect_upload(base64.b64encode(signed).decode())['programs']
    assert _target_context(signed, 'Q13UDV', case['source']).sources.cpu == 'Q13UDV'
    corrupt, _ = replace_project_stream(signed, 'Project.gd2', b'0' * 64)
    with pytest.raises(GXWFormatError, match='integrity'):
        inspect_upload(base64.b64encode(corrupt).decode())
    with pytest.raises(GXWFormatError, match='integrity'):
        _target_context(corrupt, 'Q13UDV', case['source'])


@pytest.mark.parametrize('cpu,menu', [('Q03UDV', 'Q03UDV'), ('L02-P', 'L02/L02-P'),
    ('FX3UC', 'FX3U/FX3UC'), ('FXU/FX2C', 'FXU/FX2C')])
def test_shared_fbd_entry_creates_edits_and_saves_without_importing_a_baseline(service, cpu, menu):
    from gxworks2.project_profiles import native_project_profile
    project = service.create_project(name='Blank FBD ' + cpu, plc_model=cpu, target_mode='fbd')
    catalog = service.fbd.catalog(project['id'])
    assert menu in catalog['generation_plc_models'] and len(catalog['generation_plc_models']) == 56
    assert catalog['unsupported_generation_plc_models'][0]['cpu'] == 'FX1'
    view = service.fbd.editor(project['id'])
    draft = view['model']
    assert draft['schema_version'] == 2 and draft['nodes'] == [] and draft['wires'] == []
    draft.update(nodes=[dict(id='signal', template='input', symbol='X0', x=2, y=2),
                        dict(id='coil', template='output', symbol='Y0', x=10, y=2)],
                 wires=[{'from': 'signal.OUT', 'to': 'coil.IN'}])
    global_table, = [name for name, scope in catalog['declaration_tables'].items() if scope == 'global']
    draft['declaration_edits'] = {global_table: {'upserts': [dict(
        name='TemplateGlobal', data_type='BOOL', class_name='VAR_GLOBAL')]}}
    preview = service.fbd.preview(project['id'], draft)
    proposal = service.fbd.propose(dict(operation='generate', project_id=project['id'], request_id='new', model=draft))
    version = accept(service, proposal)
    raw = service.projects.artifact(project['id'], version, 'gxw').read_bytes()
    saved = read_project_context(raw)
    assert native_project_profile(saved.sources.cpu).cpu == menu
    assert saved.object_model() == preview['model']
    assert [(n.kind.value, n.symbol) for n in saved.program.nodes] == [('input', 'X0'), ('output', 'Y0')]
    assert len(saved.program.wires) == 1
    assert [(row.name, row.data_type) for row in saved.declarations[global_table].rows] == [('TemplateGlobal', 'BOOL')]
    assert service.projects.version(project['id'], version)['validation']['gx_compile'] == 'not_run'


@pytest.mark.parametrize("invalid_connection", [False, True])
def test_english_fbd_summary_is_allowed_but_dangling_connection_is_rejected(service, invalid_connection):
    from model_runtime.provider import ReasoningDelta, TextDelta
    model = fresh_two_timers()
    if invalid_connection:
        model["wires"][0]["from"] = "nonexistent_node.Q"
    class Provider:
        def stream(self, request):
            assert request.enforce_response_language is False
            yield ReasoningDelta("Checking the connections.")
            yield TextDelta(json.dumps({"summary": "Two connected timers.", "model": model}))
    service.model_factory = lambda: (Provider(), {"model": "offline"})
    project = service.create_project(name="English FBD summary", target_mode="fbd")
    job = service.submit({"kind": "generation", "project_id": project["id"], "request_id": "language-preference",
                         "text": "两个定时器串接", "response_language": "zh-CN"})
    service.jobs._futures[job["id"]].result(timeout=15)
    state = service.jobs.get(job["id"])
    assert state["status"] == ("failed" if invalid_connection else "completed")
    assert len(service.projects.raw_project(project["id"])["versions"]) == (0 if invalid_connection else 1)
    proposals = service.proposals.list(project["id"])
    if invalid_connection:
        assert proposals == []
        assert not list((service.state_dir / "staging").rglob("*.gxw"))
    else:
        assert len(proposals) == 1 and proposals[0]["status"] == "accepted"
        assert service.proposal_preview(proposals[0]["id"])["target_mode"] == "fbd"
        version = service.projects.version(project["id"], proposals[0]["result"]["version_id"])
        assert version["maintainability_review"]["model_calls"] == 0
        assert version["maintainability_review"]["used_capabilities"]
        assert version["generation_handoff"]["capability_discovery"]["model_calls"] == 0


def test_approved_gx_import_uses_own_copy_on_com_queue(service, tmp_path, gx_ready):
    p, _, candidate = generate(service)
    vid = accept(service, candidate)
    source = service.projects.artifact(p["id"], vid, "gxw")
    original = source.read_bytes()
    calls, events = [], []
    def importer(path, **kwargs):
        assert path != source and path.read_bytes() == original
        calls.append((path, kwargs))
        path.write_bytes(b"native save changes the execution copy")
        return {"success": True, "message": "opened"}
    execution = GXExecutionCoordinator(service.store, resource_lock_path=tmp_path / "desktop.lock",
        dependencies_factory=lambda _: {"gxw_importer": importer}, com_factory=lambda: FakeCOM(events))
    service.execution.close()
    service.execution = execution
    proposal = service.execution_proposal({"action": "gx_import", "project_id": p["id"], "version_id": vid, "request_id": "gx"})
    assert proposal["action"] == "gx_import"
    assert calls == []
    job = service.decide(proposal["id"], "accept")["job"]
    service.jobs._futures[job["id"]].result(timeout=15)
    result = service.proposals.get(proposal["id"])
    assert result["status"] == "accepted"
    assert result["result"]["gx_compile_status"] == "unverified"
    assert result["result"]["passed"] is False
    assert len(calls) == 1 and source.read_bytes() == original
    assert len(calls[0][0].stem) <= 30  # Native Open Project filename constraint.
    svg = service.projects.artifact(p["id"], vid, "svg").read_text(encoding="utf-8")
    assert service.projects.svg_preview(p["id"], vid, theme="dark") == svg


@pytest.mark.parametrize('endpoint', ['in.STATE', 'out.STATE'])
def test_source_bound_custom_fb_edit_preview_save_and_reread_preserve_opaque_records(service, endpoint):
    import struct
    from dataclasses import replace
    from gxw.models import UnknownRecord
    from gxw.project_writer import build_gxw_project
    from gxw.connectivity import build_connectivity_graph

    raw = source_binding_baseline()
    source, _, _ = read_project(raw)
    opaque = UnknownRecord(max(r.offset for r in source.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    raw = build_gxw_project(raw, replace(source, unknown_records=(opaque,),
        record_count=source.record_count + 1)).data
    project = service.create_project(name='Source-bound custom FB', target_mode='fbd')
    base = accept(service, service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'import-custom-fb', 'data_base64': base64.b64encode(raw).decode()}))
    model = service.projects.program(project['id'], base)
    assert (model['schema_version'], model['cpu'], model['unknown_record_count']) == (2, 'FX3U/FX3UC', 1)
    fb = model['nodes'][0]
    assert [p['name'] for p in fb['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    view = service.fbd.editor(project['id'], model, {'action': 'update_node', 'id': fb['id'],
        'field': 'symbol', 'value': 'RENAMED_STAGE_A'}, base)
    assert view['model']['declaration_edits']['1.Labels.lh']['renames'] == {'TIMER_A': 'RENAMED_STAGE_A'}
    custom = next(item for item in view['catalog'] if item['template'] == 'function_block:FLOW_PORTS')
    assert [p['name'] for p in custom['ports']] == [p['name'] for p in fb['ports']]
    assert custom['prototype_offset'] in {n['source_offset'] for n in model['nodes']}
    draft = view['model']
    port = next(p for p in fb['ports'] if p['name'] == endpoint)
    incoming = endpoint.startswith('in.')
    draft['nodes'].append({'id': 'word_terminal', 'template': 'input' if incoming else 'output',
        'symbol': 'D30', 'x': fb['x'] - 3 if incoming else fb['x'] + port['x'] + 1,
        'y': fb['y'] + port['y'] - 1})
    command = {'action': 'add_wire',
        'from': 'word_terminal.OUT' if incoming else fb['id'] + '.' + endpoint,
        'to': fb['id'] + '.' + endpoint if incoming else 'word_terminal.IN'}
    draft = service.fbd.editor(project['id'], draft, command, base)['model']
    preview = service.fbd.preview(project['id'], draft, base)
    assert preview['model']['schema_version'] == 2 and preview['gx_compile'] == 'not_run'
    assert 'RENAMED_STAGE_A' in preview['svg'] and 'STATE' in preview['svg']
    proposal = service.fbd.propose({'operation': 'edit', 'project_id': project['id'],
        'version_id': base, 'request_id': 'edit-custom-fb', 'model': draft})
    saved = accept(service, proposal)
    assert service.projects.program(project['id'], saved) == preview['model']
    assert service.projects.svg_preview(project['id'], saved) == preview['svg']
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
    result = service.projects.artifact(project['id'], saved, 'gxw').read_bytes()
    program, declarations, _ = read_project(result)
    assert [r.raw for r in program.unknown_records] == [opaque.raw]
    assert [r.name for r in declarations['1.Labels.lh'].rows] == ['RENAMED_STAGE_A', 'TIMER_B']
    current = read_project_context(result)
    block = next(n for n in program.nodes if n.symbol == 'RENAMED_STAGE_A')
    terminal = next(n for n in program.nodes if n.symbol == 'D30')
    ports = current.sources.callable(block)['ports']
    index = next(i for i, p in enumerate(ports) if p['name'] == endpoint)
    other = next(i for i, p in enumerate(ports) if p['name'] == ('out.STATE' if incoming else 'in.STATE'))
    graph = build_connectivity_graph(program)
    assert graph.ports_connected(block.offset, index, terminal.offset, 0)
    assert not graph.ports_connected(block.offset, other, terminal.offset, 0)


def test_native_project_member_types_survive_import_preview_candidate_and_reread(service):
    raw = flat_project_structure_baseline()
    project = service.create_project(name='Native structure source', target_mode='fbd', plc_model='FX0N')
    base = accept(service, service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'structure-import', 'program': 'FBD_MATRIX.Program.pou',
        'data_base64': base64.b64encode(raw).decode()}))
    model = service.projects.program(project['id'], base)
    assert model['cpu'] == 'FX0N' and model['schema_version'] == 2
    terminal = next(node for node in model['nodes'] if node['symbol'] == 'OwnedFlat_A.BoolIn[1]')
    assert terminal['ports'][0]['data_type'] == 'BOOL'
    draft = service.fbd.editor(project['id'], model,
        {'action': 'update_node', 'id': terminal['id'], 'field': 'symbol', 'value': 'OwnedFlat_A.BoolIn[0]'}, base)['model']
    preview = service.fbd.preview(project['id'], draft, base)
    saved = accept(service, service.fbd.propose({'operation': 'edit', 'project_id': project['id'],
        'version_id': base, 'request_id': 'structure-edit', 'model': draft}))
    rebuilt = service.projects.program(project['id'], saved)
    assert rebuilt == preview['model']
    assert next(node for node in rebuilt['nodes'] if node['symbol'] == 'OwnedFlat_A.BoolIn[0]')['ports'][0]['data_type'] == 'BOOL'
    assert 'OwnedFlat_A.BoolIn[0]' in preview['svg']
    assert preview['gx_compile'] == service.projects.version(project['id'], saved)['validation']['gx_compile'] == 'not_run'
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


@pytest.mark.parametrize('created',[False,True])
def test_selected_fb_local_array_rename_survives_preview_save_and_current_source_reread(service,created):
    import struct
    from dataclasses import replace
    from gxw.models import UnknownRecord
    from gxw.project_writer import build_gxw_project
    # Replayed source streams exercise the application contract. Native CPU
    # acceptance remains attached to the original frozen cold-check cases.
    replayed,case = created_fbd_context('FX3U/FX3UC') if created else compound_structure_context('FX3U/FX3UC', local_formal=True)
    program = case['selected_program']
    context = read_project_context(replayed.raw,program)
    local = context.sources.local_table
    owner = context.declarations[local].owner_name
    opaque = UnknownRecord(max(row.offset for row in context.program.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    raw = build_gxw_project(context.raw, replace(context.program, unknown_records=(opaque,),
        record_count=context.program.record_count + 1)).data
    before_caller = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    project = service.create_project(name='Local FBD FB source', target_mode='fbd')
    base = accept(service, service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'import-local-fb', 'data_base64': base64.b64encode(raw).decode(),
        'program': program}))
    model = service.projects.program(project['id'], base)
    assert model['program'] == program and model['unknown_record_count'] == 1
    draft = service.fbd.editor(project['id'], model, {'action': 'update_label',
        'table': local, 'name': 'STATE', 'field': 'name', 'value': '状态_App_B1'}, base)['model']
    preview = service.fbd.preview(project['id'], draft, base)
    saved = accept(service, service.fbd.propose({'operation': 'edit', 'project_id': project['id'],
        'version_id': base, 'request_id': 'rename-local-fb', 'model': draft}))
    current = service.projects.program(project['id'], saved)
    assert current == preview['model'] and current['schema_version'] == 2 and not current['issues']
    members = [node for node in current['nodes'] if node['symbol'].startswith('状态_App_B1[')]
    assert {node['symbol'] for node in members} == {'状态_App_B1[1].Spare', '状态_App_B1[0].Spare'}
    assert {node['ports'][0]['data_type'] for node in members} == {'WORD'}
    assert {node['ports'][0]['name'] for node in members} == {'IN', 'OUT'}
    assert '状态_App_B1' in preview['svg']
    assert service.projects.svg_preview(project['id'], saved) == preview['svg']
    result = service.projects.artifact(project['id'], saved, 'gxw').read_bytes()
    selected = read_project_context(result,program)
    caller = read_project_context(result, 'FBD_MATRIX.Program.pou')
    assert [row.raw for row in selected.program.unknown_records] == [opaque.raw]
    assert caller.program.raw == before_caller.program.raw
    assert caller.declarations['FBD_MATRIX.Labels.lh'].raw == before_caller.declarations['FBD_MATRIX.Labels.lh'].raw
    for node in caller.object_model()['nodes']:
        if node['template'] == 'function_block:'+owner:
            ports = {port['name']: port for port in node['ports']}
            assert (ports['in.状态_App_B1']['side'], ports['out.状态_App_B1']['side']) == ('in', 'out')
    assert preview['gx_compile'] == service.projects.version(project['id'], saved)['validation']['gx_compile'] == 'not_run'
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


def test_project_fb_type_selection_updates_draft_interface_declaration_preview_and_candidate(service):
    from tests.test_gxw_object_model import flat_project_structure_witnesses
    control = flat_project_structure_witnesses()['created_fbd_controls']['core_edit_control']
    context,case = created_fbd_context('FX3U/FX3UC',program='FBD_MATRIX.Program.pou',
        source_streams=control['after_formal_source_streams'])
    raw = context.raw;target = case['caller_type_transition']['after']
    project = service.create_project(name='Native project FB type',target_mode='fbd')
    base = accept(service,service.fbd.propose(dict(operation='import',project_id=project['id'],
        request_id='import-fb-type',program='FBD_MATRIX.Program.pou',data_base64=base64.b64encode(raw).decode())))
    model = service.projects.program(project['id'],base);node, = [n for n in model['nodes'] if n['symbol']=='TIMER_A']
    edited = service.fbd.editor(project['id'],model,dict(action='update_node',id=node['id'],field='template',
        value='function_block:'+target),base)
    interface = next(row['ports'] for row in edited['presentation']['nodes'] if row['id']==node['id'])
    assert [(p['name'],p['side']) for p in interface]==[('SIGNAL','in'),('in.'+control['formal_name'],'in'),
        ('RESULT','out'),('out.'+control['formal_name'],'out')]
    local = 'FBD_MATRIX.Labels.lh'
    declaration, = [r for r in edited['presentation']['rows'][local] if r['name']=='TIMER_A']
    assert declaration['data_type']==target and declaration['kind']=='function_block'
    draft = service.fbd.editor(project['id'],edited['model'],dict(action='update_node',id=node['id'],
        field='symbol',value=control['instance_name']),base)['model']
    preview = service.fbd.preview(project['id'],draft,base)
    saved = accept(service,service.fbd.propose(dict(operation='edit',project_id=project['id'],version_id=base,
        request_id='edit-fb-type',model=draft)))
    rebuilt = service.projects.program(project['id'],saved)
    assert rebuilt==preview['model'] and not rebuilt['issues']
    block, = [n for n in rebuilt['nodes'] if n['symbol']==control['instance_name']]
    assert block['template']=='function_block:'+target
    assert [p['name'] for p in block['ports']]==[p['name'] for p in interface]
    current = read_project_context(service.projects.artifact(project['id'],saved,'gxw').read_bytes(),'FBD_MATRIX.Program.pou')
    renamed, = [r for r in current.declarations[local].rows if r.name==control['instance_name']]
    assert renamed.data_type==target
    assert preview['gx_compile']=='not_run' and service.projects.artifact(project['id'],base,'gxw').read_bytes()==raw


def test_http_and_connected_agent_share_source_catalog_and_candidate_validation(tmp_path):
    from tests.test_web_api import AGENT
    with TestClient(_app(tmp_path / 'workspace', tmp_path / 'state'), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers,
            json={'name': 'FBD v2 application', 'target_mode': 'fbd'}).json()
        response = client.post('/api/fbd/proposals', headers=headers, json={'operation': 'import',
            'project_id': project['id'], 'request_id': 'import',
            'data_base64': base64.b64encode(source_binding_baseline()).decode()})
        assert response.status_code == 201, response.text
        version = response.json()['result']['version_id']
        selected = {'project_id': project['id'], 'version_id': version}
        catalog = client.get('/api/fbd/catalog', params=selected).json()
        assert (catalog['schema_version'], catalog['cpu']) == (2, 'FX3U/FX3UC')
        editor = client.post('/api/fbd/editor', headers=headers, json=selected).json()
        assert editor['catalog'] == catalog['nodes']

        def call(name, arguments):
            from application.read_delivery import assemble_read_pages
            def invoke(tool, args):
                response = client.post('/api/agent/tools/call', headers={'Authorization': 'Bearer ' + AGENT},
                    json={**selected, 'name': tool, 'call_id': tool + '-' + str(len(calls)), 'arguments': args})
                calls.append(tool)
                assert response.status_code == 200, response.text
                assert not response.json()['is_error'], response.text
                return response.json()
            calls = []
            result = invoke(name, arguments)
            if 'delivery' in result['data'].get('data', {}):
                pages = [result['data']]
                while not pages[-1]['data']['delivery']['complete']:
                    next_call = pages[-1]['data']['delivery']['next']
                    pages.append(invoke(next_call['tool'], next_call['arguments'])['data'])
                result['data']['data'] = assemble_read_pages(pages)
            return result

        context = call('get_generation_context', {})['data']['data']
        assert context['current_model_ref'] == '#/generation_input/previous'
        assert context['generation_input']['previous'] == catalog['parameter_model']
        graph_contract = context['output_contract']
        if graph_contract['tool'] == 'edit_gxw_project':
            graph_contract, = graph_contract['alternatives']
        assert graph_contract['schema_version'] == 2
        assert graph_contract['input_shape']=='parameters'
        assert context['catalog_ref'] == '#/generation_input/catalog'
        assert context['generation_input']['parameter_schema']==catalog['parameter_schema']
        assert all(not {'width','height','prototype_offset'}&node.keys() and
            all(not {'x','y'}&port.keys() for port in node['ports']) for node in context['generation_input']['catalog'])
        call('get_fbd_catalog', {})
        assert call('read_fbd_project', {})['data']['data']['model'] == editor['model']
        changed = client.post('/api/fbd/editor', headers=headers, json={**selected, 'model': editor['model'],
            'command': {'action': 'update_node', 'id': editor['model']['nodes'][0]['id'],
                        'field': 'symbol', 'value': 'AGENT_STAGE_A'}}).json()
        candidate = call('create_fbd_candidate', {'operation': 'edit', 'model': changed['model']})
        assert candidate['proposal_id']
        assert '"_fbd_candidate"' not in json.dumps(candidate)
        preview = client.get('/api/proposals/' + candidate['proposal_id'] + '/preview').json()
        assert preview['program']['schema_version'] == 2
        assert 'AGENT_STAGE_A' in preview['svg']
        assert 'verification' not in candidate['data']['data']
        assert client.get('/api/projects/' + project['id']).json()['version_count'] == 1


def test_current_explicit_native_save_normalizes_source_aliases_and_keeps_v2(service, monkeypatch):
    import application.execution as execution
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(service.state_dir / 'test-native.lock'))
    from gxw.container_writer import replace_project_stream
    from gxw.native_write import native_source_plan, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    # Only the application boundary is doubled; this envelope is not native
    # acceptance evidence. Live native save/cold-compile is a separate check.
    raw = source_binding_baseline()
    raw = project_rows_variant(raw, {'1.Program.pou': {'szName': '1.程序.pou'},
                                    '1.Labels.lh': {'szName': '1.标签.lh'}})
    calls = []

    def save(_self, prepared):
        calls.append(native_source_plan(prepared))
        assert prepared.baseline == raw
        hdb = prepared.outer['_hdb']
        for stream, old, new in prepared.replacements.values():
            if old != new:
                hdb, _ = replace_project_stream(hdb, stream, new)
        saved, _ = replace_project_stream(raw, '_hdb', hdb)
        saved = project_rows_variant(saved, {'1.程序.pou': {'szName': '1.Program.pou'},
                                            '1.标签.lh': {'szName': '1.Labels.lh'}})
        return verify_native_save(prepared, saved, {'operation': 'test_double'})

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    project = service.create_project(name='Native FBD v2', target_mode='fbd')
    imported = service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'native-import', 'data_base64': base64.b64encode(raw).decode()})
    base = accept(service, imported)
    original = service.projects.program(project['id'], base)
    assert original['program'] == '1.程序.pou'
    fb = next(n for n in original['nodes'] if n['template'].startswith('function_block:'))
    draft = service.fbd.editor(project['id'], original,
        {'action': 'update_node', 'id': fb['id'], 'field': 'symbol', 'value': 'NATIVE_UPDATED'}, base)['model']
    assert draft['declaration_edits']['1.标签.lh']['renames'] == {'TIMER_A': 'NATIVE_UPDATED'}
    preview = service.fbd.preview(project['id'], draft, base)
    assert preview['model']['program'] == '1.程序.pou'
    assert not calls
    saved = service.fbd.propose({'operation': 'edit', 'project_id': project['id'],
        'version_id': base, 'request_id': 'native-edit', 'model': draft, 'native_save': True})
    current = accept(service, saved)
    assert len(calls) == 1
    current_model = service.projects.program(project['id'], current)
    assert current_model['schema_version'] == 2
    assert current_model['program'] == '1.Program.pou'
    assert '1.Labels.lh' in current_model['labels']
    assert service.projects.version(project['id'], current)['validation']['gx_compile'] == 'not_run'
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
    updated = next(n for n in current_model['nodes'] if n['symbol'] == 'NATIVE_UPDATED')
    assert [p['name'] for p in updated['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']


@pytest.mark.parametrize('changed', ['project', 'version', 'baseline', 'model', 'policy', 'json_type', 'expired', 'save_policy'])
def test_prepared_preview_reuse_requires_the_exact_source_request_and_policy(monkeypatch, changed):
    import application.fbd as fbd
    from gxw.project_writer import ProjectWriteResult
    now, calls = [10.0], []
    monkeypatch.setattr(fbd.time, 'monotonic', lambda: now[0])
    def write(model, baseline=None, *, native_save=False, save_policy='checked'):
        calls.append(deepcopy((model, baseline, native_save)))
        return ProjectWriteResult(b'checked output', {'values': [1]})
    monkeypatch.setattr(fbd, '_write_candidate', write)
    service = fbd.FBDService(None)
    request = ['p1', 'v1', b'actual source', {'nodes': [{'x': 1}]}, True]
    first = service._preview_result(*request, create=True)
    first.report['values'].append('caller mutation')
    reused = service._preview_result(*request)
    assert reused.data == first.data and reused.report == {'values': [1]} and len(calls) == 1
    changed_request = deepcopy(request)
    changed_keywords = {'save_policy':'draft'} if changed=='save_policy' else {}
    if changed == 'project': changed_request[0] = 'p2'
    elif changed == 'version': changed_request[1] = 'v2'
    elif changed == 'baseline': changed_request[2] += b'changed'
    elif changed == 'model': changed_request[3]['nodes'][0]['x'] = 2
    elif changed == 'policy': changed_request[4] = False
    elif changed == 'json_type': changed_request[3]['nodes'][0]['x'] = True
    elif changed=='expired': now[0] = 311.0
    assert service._preview_result(*changed_request,**changed_keywords) is None
    assert len(calls) == 1
    service._preview_result(*changed_request, create=True,**changed_keywords)
    assert len(calls) == 2


def test_prepared_preview_keeps_generated_drafts_and_input_mutations_separate(monkeypatch):
    from hypothesis import given, settings, strategies as st
    import application.fbd as fbd
    from gxw.project_writer import ProjectWriteResult
    @settings(max_examples=30, deadline=None, derandomize=True)
    @given(values=st.lists(st.integers(-100, 100), max_size=15))
    def check(values):
        calls = []
        monkeypatch.setattr(fbd, '_write_candidate', lambda *args, **kwargs:
            calls.append(True) or ProjectWriteResult(b'checked', {'values': list(values)}))
        service = fbd.FBDService(None)
        model = {'values': list(values)}
        service._preview_result('p', None, b'blank source', model, False, create=True)
        model['values'].append(101)
        assert service._preview_result('p', None, b'blank source', model, False) is None
        reused = service._preview_result('p', None, b'blank source', {'values': values}, False)
        assert reused.report == {'values': values} and calls == [True]
    check()


def test_native_save_failure_or_busy_resource_leaves_base_version_unchanged(service, monkeypatch):
    import application.execution as execution
    from application.execution import DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: DesktopResourceLock(service.state_dir / 'test-native.lock'))
    from application.fbd import FBDValidationError
    from gxw.container_writer import replace_project_stream
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    raw = source_binding_baseline()
    project = service.create_project(name='Native failure', target_mode='fbd')
    base = accept(service, service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'import-native', 'data_base64': base64.b64encode(raw).decode()}))
    model = service.projects.program(project['id'], base)
    model['nodes'][0]['symbol'] = 'FAILED_UPDATE'
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'FAILED_UPDATE'}}}
    calls = []

    def save(_self, _prepared):
        calls.append(True)
        raise WorkspaceSaveError('native-source-readback-mismatch')

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    with DesktopResourceLock(service.state_dir / 'test-native.lock'), pytest.raises(ConflictError, match='原生资源'):
        service.fbd.propose({'operation': 'edit', 'project_id': project['id'], 'version_id': base,
                            'request_id': 'busy-native-edit', 'model': model, 'native_save': True})
    assert not calls
    with pytest.raises(FBDValidationError, match='native-source-readback-mismatch'):
        service.fbd.propose({'operation': 'edit', 'project_id': project['id'], 'version_id': base,
                            'request_id': 'failed-native-edit', 'model': model, 'native_save': True})
    assert len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


@pytest.mark.parametrize('cpu', ['Q04UDV', 'L02', 'FX1N/FX1NC', 'FX3U/FX3UC'])
def test_http_created_fb_uses_saved_roles_then_rebinds_formals_and_caller(tmp_path, monkeypatch, cpu):
    import application.execution as execution
    from gxw.native_write import verify_native_source_creation, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    raw, created, case = source_creation_witness(cpu)
    formal_context, _ = source_creation_stage(cpu)
    original_program = '1.Program.pou' if cpu == 'FX3U/FX3UC' else 'FBD_MATRIX.Program.pou'
    lock_path = tmp_path / 'native.lock'
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(lock_path))
    calls = []

    # Replay actual, CPU-specific native observations at the application seam;
    # these HTTP tests do not claim a new native execution.
    def create(_self, baseline, name):
        assert baseline == raw and name == case['created_name']
        with pytest.raises(execution.ExecutionUnavailableError), native_lock(lock_path):
            pass
        calls.append('create')
        return verify_native_source_creation(baseline, name, created, case['native_creation'])

    def save(_self, prepared):
        assert prepared.baseline == created
        calls.append('save')
        return verify_native_save(prepared, formal_context.raw, {'operation': 'application_fixture_replay'})

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', create)
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    with TestClient(_app(tmp_path / 'workspace', tmp_path / 'state'), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post('/api/projects', headers=headers, json={'name': cpu, 'target_mode': 'fbd', 'plc_model': cpu}).json()
        imported = client.post('/api/fbd/proposals', headers=headers, json={'operation': 'import',
            'project_id': project['id'], 'request_id': 'import', 'program': original_program,
            'data_base64': base64.b64encode(raw).decode()})
        assert imported.status_code == 201, imported.text
        base = imported.json()['result']['version_id']
        command = dict(operation='create_fb', project_id=project['id'], version_id=base,
                       request_id='create', source_name=case['created_name'])
        assert client.post('/api/fbd/proposals', json=command).status_code == 403
        response = client.post('/api/fbd/proposals', headers=headers, json=command)
        assert response.status_code == 201, response.text
        proposal = response.json()
        version = proposal['result']['version_id']
        assert client.post('/api/fbd/proposals', headers=headers, json=command).json()['id'] == proposal['id']
        preview = client.get('/api/proposals/' + proposal['id'] + '/preview').json()
        assert preview['program']['program'] == case['created_program']
        assert preview['editor']['model'] == preview['program']
        assert preview['editor']['source_svg'] == preview['svg']
        assert preview['diff']['before_object_count'] == preview['diff']['after_object_count'] == 0
        assert preview['diff']['has_changes']
        selection = dict(project_id=project['id'], version_id=version)
        editor = client.post('/api/fbd/editor', headers=headers, json=selection).json()
        assert editor['model']['schema_version'] == 2 and editor['model']['cpu'] == cpu
        assert not editor['model']['nodes'] and not editor['model']['issues']
        assert case['created_program'] in editor['programs'] and original_program in editor['programs']
        assert client.get(f'/api/projects/{project["id"]}/versions/{base}/artifacts/gxw').content == raw
        assert client.get(f'/api/projects/{project["id"]}/versions/{version}/artifacts/gxw').content == created
        assert calls == ['create']

        model = editor['model']
        model['canvas_height'] = 12
        model['nodes'] = [dict(id='sig', template='input', symbol='输入_Create_C1', x=3, y=2),
                          dict(id='out', template='output', symbol='输出_Create_C1', x=10, y=2)]
        model['wires'] = [{'from': 'sig.OUT', 'to': 'out.IN'}]
        model['declaration_edits'] = {case['created_declarations']: {'upserts': [
            dict(name='输入_Create_C1', data_type='BOOL', class_name='VAR_INPUT'),
            dict(name='输出_Create_C1', data_type='BOOL', class_name='VAR_OUTPUT')]}}
        draft = client.post('/api/fbd/preview', headers=headers, json={**selection, 'model': model})
        assert draft.status_code == 200, draft.text
        preview_model = draft.json()['model']
        if case['created_declarations'] != formal_context.sources.local_table:
            preview_model['labels'][formal_context.sources.local_table] = preview_model['labels'].pop(case['created_declarations'])
        expected_preview = formal_context.object_model()
        expected_preview['program'] = case['created_program']
        assert preview_model == expected_preview
        assert calls == ['create']
        saved = client.post('/api/fbd/proposals', headers=headers, json={**selection, 'operation': 'edit',
            'request_id': 'formals', 'model': model, 'native_save': True})
        assert saved.status_code == 201, saved.text
        formal_preview = client.get('/api/proposals/' + saved.json()['id'] + '/preview').json()
        assert '"program": "' + case['created_program'] + '"' in formal_preview['diff']['unified_diff']
        saved_version = saved.json()['result']['version_id']
        selected = dict(project_id=project['id'], version_id=saved_version, program=original_program)
        caller = client.post('/api/fbd/editor', headers=headers, json=selected)
        assert caller.status_code == 200, caller.text
        caller = caller.json()
        assert caller['model']['program'] == original_program
        assert caller['source_svg'] == read_project_context(formal_context.raw, original_program).svg()
        template, = [row for row in caller['catalog'] if row['template'] == 'function_block:' + case['created_name']]
        assert [(port['formal_name'], port['side']) for port in template['ports']] == [
            ('输入_Create_C1', 'in'), ('输出_Create_C1', 'out')]
        assert calls == ['create', 'save']
        metadata = client.get(f'/api/projects/{project["id"]}/versions/{saved_version}').json()
        assert metadata['validation']['gx_compile'] == 'not_run'


@pytest.mark.parametrize('extra', [dict(source_name=None), dict(model={}), dict(program='1.Program.pou'),
                                 dict(data_base64='AA==')])
def test_create_fb_rejects_unbound_or_mixed_requests_before_native_call(service, monkeypatch, extra):
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    p, _, proposal = generate(service)
    base = accept(service, proposal)
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', lambda *_: pytest.fail('unexpected native call'))
    command = dict(operation='create_fb', project_id=p['id'], version_id=base,
                   request_id='bad-create', source_name='New_FB')
    with pytest.raises(ValueError):
        service.fbd.propose({**command, **extra})
    with pytest.raises(ConflictError):
        service.fbd.propose({**command, 'version_id': None})
    assert len(service.projects.raw_project(p['id'])['versions']) == 1


def test_create_fb_native_failure_or_busy_lock_does_not_save_a_version(service, monkeypatch):
    import application.execution as execution
    from application.fbd import FBDValidationError
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    raw, _, case = source_creation_witness()
    project = service.create_project(name='FB failure', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='import', data_base64=base64.b64encode(raw).decode())))
    lock = execution.DesktopResourceLock
    path = service.state_dir / 'native.lock'
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: lock(path))
    calls = []

    def create(*_):
        calls.append(True)
        raise WorkspaceSaveError('creation-readback-failed')

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', create)
    command = dict(operation='create_fb', project_id=project['id'], version_id=base,
                   request_id='create', source_name=case['created_name'])
    with lock(path), pytest.raises(ConflictError, match='原生资源'):
        service.fbd.propose(command)
    assert not calls
    with pytest.raises(FBDValidationError, match='creation-readback-failed'):
        service.fbd.propose(command)
    assert calls == [True]
    assert len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


def test_library_import_shared_candidate_rebuilds_catalog_and_reviews_definitions(service, monkeypatch):
    from gxw.library_transfer import verify_library_import
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import library_transfer_witness
    raw, donor, saved = library_transfer_witness()
    project = service.create_project(name='Library definitions', plc_model='L02', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='base', data_base64=base64.b64encode(raw).decode())))
    calls = []

    def imported(_self, current, source, name):
        calls.append(name)
        return verify_library_import(current, source, name, saved, {})

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'import_library', imported)
    command = dict(operation='import_library', project_id=project['id'], version_id=base,
        request_id='library', data_base64=base64.b64encode(donor).decode(), library_name='MotionControl_JEC_V100A')
    proposal = service.fbd.propose(command)
    assert service.fbd.propose(command)['id'] == proposal['id'] and len(calls) == 1
    preview = service.proposal_preview(proposal['id'])
    assert preview['diff']['has_changes']
    assert len(preview['diff']['library_changes']['definitions']) == 16
    from integrations.web.responses import FBDPreview
    assert FBDPreview.model_validate(preview).diff.library_changes.definitions == preview['diff']['library_changes']['definitions']
    assert preview['diff']['before_object_count'] == preview['diff']['after_object_count'] == 0
    version = accept(service, proposal)
    catalog = service.fbd.catalog(project['id'], version)
    power, = [row for row in catalog['nodes'] if row['template'] == 'function_block:MC_Power_JEC']
    assert [(p['name'], p['side']) for p in power['ports'] if p['formal_name'] == 'Axis'] == [('in.Axis', 'in'), ('out.Axis', 'out')]
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
    assert service.projects.artifact(project['id'], version, 'gxw').read_bytes() == saved
    assert service.projects.version(project['id'], version)['validation']['native_source_save_required'] is False


def test_http_definition_authoring_reviews_structure_and_keeps_base_bytes(tmp_path,monkeypatch):
    import application.execution as execution
    from gxw.fb_authoring import verify_authoring
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import authored_fb_witness
    before,after,command,report=authored_fb_witness('v0002')
    lock=execution.DesktopResourceLock
    monkeypatch.setattr(execution,'DesktopResourceLock',lambda:lock(tmp_path/'native.lock'))
    calls=[]
    def author(_self,raw,arguments):
        assert raw==before and arguments==command
        calls.append(arguments['operation'])
        return verify_authoring(raw,arguments,after,report['native_workspace'])
    monkeypatch.setattr(NativeWorkspaceSourceSave,'author',author)
    with TestClient(_app(tmp_path/'workspace',tmp_path/'state'),base_url=ORIGIN) as client:
        headers=_login(client)
        project=client.post('/api/projects',headers=headers,json={'name':'Independent FB','plc_model':'FX3U','target_mode':'fbd'}).json()
        base=client.post('/api/fbd/proposals',headers=headers,json=dict(operation='import',project_id=project['id'],
            request_id='base',data_base64=base64.b64encode(before).decode())).json()['result']['version_id']
        request={**command,'project_id':project['id'],'version_id':base,'request_id':'structure'}
        response=client.post('/api/fbd/proposals',headers=headers,json=request)
        assert response.status_code==201,response.text
        proposal=response.json()
        assert client.post('/api/fbd/proposals',headers=headers,json=request).json()['id']==proposal['id']
        preview=client.get('/api/proposals/'+proposal['id']+'/preview').json()
        assert preview['diff']['has_changes']
        assert preview['diff']['definition_changes']['after']['name']=='GateState'
        assert preview['diff']['before_object_count']==preview['diff']['after_object_count']==0
        version=proposal['result']['version_id']
        assert client.get(f'/api/projects/{project["id"]}/versions/{base}/artifacts/gxw').content==before
        assert client.get(f'/api/projects/{project["id"]}/versions/{version}/artifacts/gxw').content==after
        assert client.get(f'/api/projects/{project["id"]}/versions/{version}/artifacts/definition').json()['kind']=='structure'
    assert calls==['create_structure']


@pytest.mark.parametrize('version',['v0030','v0036','v0037','v0040','v0045','v0047'])
def test_http_motion_declarations_and_configuration_reuse_shared_authoring(tmp_path,monkeypatch,version):
    import application.execution as execution
    from gxw.fb_authoring import verify_authoring
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import motion_authoring_witness
    before,after,command,report=motion_authoring_witness(version)
    lock=execution.DesktopResourceLock
    monkeypatch.setattr(execution,'DesktopResourceLock',lambda:lock(tmp_path/'native.lock'))
    calls=[]
    def author(_self,raw,arguments):
        assert raw==before and arguments==command
        calls.append(arguments['operation'])
        return verify_authoring(raw,arguments,after,report['native_workspace'])
    monkeypatch.setattr(NativeWorkspaceSourceSave,'author',author)
    with TestClient(_app(tmp_path/'workspace',tmp_path/'state'),base_url=ORIGIN) as client:
        headers=_login(client)
        project=client.post('/api/projects',headers=headers,json=dict(name='Motion configuration',plc_model='FX3U',target_mode='fbd')).json()
        imported=client.post('/api/fbd/proposals',headers=headers,json=dict(operation='import',project_id=project['id'],
            request_id='base',program='FBD_MATRIX.Program.pou',data_base64=base64.b64encode(before).decode()))
        assert imported.status_code==201,imported.text
        base=imported.json()['result']['version_id']
        request={k:v for k,v in command.items() if k!='device_profile'}
        request.update(project_id=project['id'],version_id=base,request_id='author')
        response=client.post('/api/fbd/proposals',headers=headers,json=request)
        assert response.status_code==201,response.text
        proposal=response.json()
        assert client.post('/api/fbd/proposals',headers=headers,json=request).json()['id']==proposal['id']
        preview=client.get('/api/proposals/'+proposal['id']+'/preview').json()
        assert preview['diff']['definition_changes']['after']==report['definition_after']
        assert client.get(f'/api/projects/{project["id"]}/versions/{base}/artifacts/gxw').content==before
        assert client.get(f'/api/projects/{project["id"]}/versions/{proposal["result"]["version_id"]}/artifacts/gxw').content==after
    assert calls==[command['operation']]


def test_definition_options_cannot_be_silently_ignored():
    from application.fbd import authoring_command
    with pytest.raises(ValueError,match='options'):
        authoring_command(dict(operation='create_structure',source_name='State',members=[],options={'program_capacity':64000}))


def test_compile_save_requires_current_baseline_before_native_work(tmp_path,monkeypatch):
    import application.fbd as fbd
    monkeypatch.setattr(fbd,'_native_candidate',lambda *_:pytest.fail('missing base must fail before native work'))
    with pytest.raises(ValueError,match='基础版本'):
        fbd.prepare_candidate(tmp_path,compile_save=True)


def test_compiled_candidate_reviews_code_change_and_preserves_both_source_graphs(service,monkeypatch):
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from gxw.native_write import verify_native_compilation_save
    from tests.test_gxw_object_model import motion_compilation_witness
    before,after,report=motion_compilation_witness()
    calls=[]
    def compile_(_self,raw):
        assert raw==before
        calls.append('compile')
        return verify_native_compilation_save(raw,after,report['native_validation'],report['cold_validation'],report['native_workspace'])
    monkeypatch.setattr(NativeWorkspaceSourceSave,'compile_project',compile_)
    project=service.create_project(name='Compiled Motion',plc_model='FX3U',target_mode='fbd')
    base=accept(service,service.fbd.propose(dict(operation='import',project_id=project['id'],request_id='base',
        program='PrgAxis2.Program.pou',data_base64=base64.b64encode(before).decode())))
    command=dict(operation='compile',project_id=project['id'],version_id=base,request_id='compile')
    proposal=service.fbd.propose(command)
    assert service.fbd.propose(command)['id']==proposal['id'] and calls==['compile']
    preview=service.proposal_preview(proposal['id'])
    assert preview['diff']['has_changes'] and preview['diff']['compilation_save']['saved_code']['status']=='matches'
    assert preview['diff']['compilation_save']['gui_configuration']=='missing'
    from integrations.web.responses import FBDPreview
    assert FBDPreview.model_validate(preview).diff.compilation_save.execution=='not_run'
    version=accept(service,proposal)
    metadata=service.projects.raw_version(project['id'],version)['validation']
    assert metadata['gx_compile']=='passed' and metadata['cold_reopen']=='passed'
    assert metadata['gui_configuration']=='missing' and metadata['status']=='unverified'
    assert any('GUI' in message for message in metadata['messages'])
    assert service.projects.artifact(project['id'],base,'gxw').read_bytes()==before
    assert service.projects.artifact(project['id'],version,'gxw').read_bytes()==after


def test_selected_pou_draft_is_bound_to_its_own_base_for_review(service):
    _, raw, case = source_creation_witness()
    project = service.create_project(name='POU selection', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='import', program='1.Program.pou', data_base64=base64.b64encode(raw).decode())))
    current = read_project_context(raw, '1.Program.pou')
    other = case['created_program']
    selected = service.fbd.editor(project['id'], version_id=base, program=other)
    with pytest.raises(ValueError, match='源码对象'):
        service.fbd.editor(project['id'], selected['model'], version_id=base, program=current.program.logical_name)
    candidate = service.fbd.propose(dict(operation='edit', project_id=project['id'], version_id=base,
        request_id='other-pou', model=selected['model']))
    diff = service.proposal_preview(candidate['id'])['diff']
    assert not diff['has_changes']
    assert diff['before_object_count'] == len(selected['model']['nodes'])


def test_native_created_noop_keeps_source_bytes_without_implicit_native_save(service, monkeypatch):
    from gxw.native_write import verify_native_source_creation
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    raw, saved, case = source_creation_witness()
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', lambda _, before, name:
        verify_native_source_creation(before, name, saved, case['native_creation']))
    project = service.create_project(name='Native backend retention', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='import', data_base64=base64.b64encode(raw).decode())))
    created = accept(service, service.fbd.propose(dict(operation='create_fb', project_id=project['id'],
        version_id=base, request_id='create', source_name=case['created_name'])))
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', lambda *_: pytest.fail('unchanged source must not call native save'))
    model = service.fbd.editor(project['id'], version_id=created)['model']
    noop = accept(service, service.fbd.propose(dict(operation='edit', project_id=project['id'], version_id=created,
        request_id='noop', model=model)))
    assert service.projects.artifact(project['id'], noop, 'gxw').read_bytes() == saved
    validation = service.projects.version(project['id'], noop)['validation']
    assert validation['native_save'] == 'not_run' and validation['native_source_save_required'] is False


def test_current_explicit_model_edit_of_created_fb_uses_the_native_backend(service, monkeypatch):
    import application.model_api as api
    from application.fbd import generate_candidate
    from gxw.native_write import verify_native_source_creation, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    raw, created, case = source_creation_witness()
    formal_context, _ = source_creation_stage()
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', lambda _, before, name:
        verify_native_source_creation(before, name, created, case['native_creation']))
    project = service.create_project(name='Model native edit', target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='import', data_base64=base64.b64encode(raw).decode())))
    version = accept(service, service.fbd.propose(dict(operation='create_fb', project_id=project['id'],
        version_id=base, request_id='create', source_name=case['created_name'])))
    model = service.projects.program(project['id'], version)
    model.update(canvas_height=12, nodes=[
        dict(id='sig', template='input', symbol='输入_Create_C1', x=3, y=2),
        dict(id='out', template='output', symbol='输出_Create_C1', x=10, y=2)],
        wires=[{'from': 'sig.OUT', 'to': 'out.IN'}], declaration_edits={case['created_declarations']: {'upserts': [
            dict(name='输入_Create_C1', data_type='BOOL', class_name='VAR_INPUT'),
            dict(name='输出_Create_C1', data_type='BOOL', class_name='VAR_OUTPUT')]}})
    monkeypatch.setattr(api, '_request_model', lambda *_args, **_kwargs:
        SimpleNamespace(message=SimpleNamespace(content=json.dumps({'summary': '原生 FB 形参', 'model': model}))))
    calls = []

    def save(_self, prepared):
        assert prepared.baseline == created
        calls.append(True)
        return verify_native_save(prepared, formal_context.raw, {})

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    payload = generate_candidate(service.state_dir / 'model-native-edit', dict(
        project=service.projects.raw_project(project['id']), version=service.projects.raw_version(project['id'], version),
        fbd_baseline=base64.b64encode(created).decode(), fbd_program=case['created_program'], text='连接两个 BOOL 形参',
        native_save=True), [],
        SimpleNamespace(emit=lambda *_: None, checkpoint=lambda: None))
    assert calls == [True]
    assert payload['metadata']['program_name'] == formal_context.program.logical_name
    assert payload['metadata']['validation']['native_save'] == 'passed'
    assert payload['metadata']['validation']['gx_compile'] == 'not_run'


def test_created_inout_application_candidate_replays_the_original_plan_and_rebinds_all_pairs(service, monkeypatch):
    import application.execution as execution
    from gxw.declarations import BASIC_TYPES, _type_parts
    from gxw.native_write import native_source_plan, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from application.fbd import catalog_snapshot, pack_candidate, unpack_candidate
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(service.state_dir / 'test-native.lock'))
    context, case = created_inout_context(stage='source')
    saved, _ = created_inout_context(stage='formal')
    model = context.object_model()
    model['declaration_edits'] = {context.sources.local_table: {'upserts': [
        dict(name=row['name'], data_type=row['data_type'], class_name='VAR_IN_OUT',
             **({'kind': 'structure'} if _type_parts(row['data_type'])[0] not in BASIC_TYPES else {}))
        for row in case['original_native_plan']['create']]}}
    calls = []

    def save(_self, prepared):
        assert prepared.baseline == context.raw
        calls.append(native_source_plan(prepared))
        assert calls[-1] == case['original_native_plan']
        return verify_native_save(prepared, saved.raw, case['native_formal_save'])

    # Original native bytes are replayed at the application seam. No new
    # native execution or completed compile/check is claimed by this test.
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    root = service.state_dir / 'inout-preview'
    payload = prepare_candidate(root, model=model, baseline=context.raw, native_save=True)
    restored_root = service.state_dir / 'inout-candidate'
    restored = unpack_candidate(pack_candidate(payload, root), restored_root)
    assert (restored_root / 'program.gxw').read_bytes() == saved.raw
    assert json.loads((restored_root / 'fbd.json').read_text(encoding='utf-8')) == saved.object_model()
    assert restored['metadata']['validation']['native_source_save_required'] is False
    assert restored['metadata']['validation']['native_save_requested'] is True
    assert restored['metadata']['validation']['gx_compile'] == 'not_run'
    templates = catalog_snapshot(saved.raw, case['caller_program'])['nodes']
    template, = [item for item in templates if item['template'] == 'function_block:' + case['original_native_plan']['program_name']]
    # The old witness records the pre-fix side-grouped connector sequence.
    # New calls use native IN_OUT pairs; their source formals stay identical.
    bindings = lambda ports: sorted(({k: v for k, v in port.items() if k not in ('x', 'y')}
                                     for port in ports), key=lambda port: port['name'])
    assert bindings(template['ports']) == bindings(case['native_ports'])
    for left, right in zip(template['ports'][::2], template['ports'][1::2]):
        assert left['formal_name'] == right['formal_name'] and left['y'] == right['y']
        assert (left['side'], right['side']) == ('in', 'out')
    assert calls == [case['original_native_plan']]


def test_root_local_application_candidate_uses_current_source_constructor_and_roundtrips(service, monkeypatch):
    import application.execution as execution
    from application.fbd import pack_candidate, unpack_candidate
    from gxw.declarations import BASIC_TYPES, _type_parts
    from gxw.native_write import native_source_plan, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import root_actual_context
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(service.state_dir / 'test-native.lock'))
    before, case = root_actual_context(stage='source')
    created, _ = root_actual_context(stage='created')
    body, _ = root_actual_context(stage='body')
    model = before.object_model()
    model['declaration_edits'] = {before.sources.local_table: {'upserts': [
        dict(name=r['name'], data_type=r['data_type'], class_name='VAR',
             **({'kind':'structure'} if _type_parts(r['data_type'])[0] not in BASIC_TYPES else {}))
        for r in case['actuals']]}}
    calls = []
    stage = next(r for r in case['stages'] if r['phase'] == 'body')

    def save(_self, prepared):
        plan = native_source_plan(prepared)
        calls.append(plan)
        if len(calls) == 1:
            assert prepared.baseline == before.raw and plan == case['original_native_plan']
            return verify_native_save(prepared, created.raw, case['native_creation'])
        assert prepared.baseline == created.raw and plan == stage['original_native_plan']
        return verify_native_save(prepared, body.raw, stage['native_save'])

    # Frozen original selected source bytes are replayed at the shared service
    # seam on Core-only containers; no new native execution is claimed here.
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    first_root = service.state_dir / 'root-local-create'
    first = prepare_candidate(first_root, model=model, baseline=before.raw, native_save=True)
    assert json.loads((first_root / 'fbd.json').read_text(encoding='utf-8')) == created.object_model()
    wanted = deepcopy(stage['source_model'])
    for key in ('labels', 'issues', 'unknown_record_count', 'cpu'):
        wanted.pop(key, None)
    for node in wanted['nodes']:
        node.pop('ports', None)
    second_root = service.state_dir / 'root-local-members'
    second = prepare_candidate(second_root, model=wanted, baseline=(first_root / 'program.gxw').read_bytes(), native_save=True)
    restored_root = service.state_dir / 'root-local-restored'
    restored = unpack_candidate(pack_candidate(second, second_root), restored_root)
    assert (restored_root / 'program.gxw').read_bytes() == body.raw
    assert json.loads((restored_root / 'fbd.json').read_text(encoding='utf-8')) == body.object_model()
    assert not body.object_model()['issues']
    assert restored['metadata']['validation']['native_save'] == 'passed'
    assert restored['metadata']['validation']['gx_compile'] == 'not_run'
    assert first['metadata']['validation']['native_source_save_required'] is False
    assert first['metadata']['validation']['native_save_requested'] is True
    assert calls == [case['original_native_plan'], stage['original_native_plan']]


@pytest.mark.parametrize('name', ['motion', 'mapping'])
def test_authored_fbd_edit_preview_candidate_and_restart_match_cold_checked_snapshot(service, tmp_path, monkeypatch, name):
    import zipfile
    from pathlib import Path
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_compiler import authored_fbd_validation_case
    original, _, _, _ = authored_fbd_validation_case(name, 'original')
    checked, _, _, witness = authored_fbd_validation_case('application-'+name, 'candidate')
    archive_path = private_path('research/' + 'evidence/gxw-authored-fbd-instances-20261012.zip')
    with zipfile.ZipFile(archive_path) as archive:
        prefix = 'application/application-'+name+'/'
        wanted = json.loads(archive.read(prefix+'saved-model.json'))
        frozen_draft = json.loads(archive.read(prefix+'draft.json'))
    # The original byte snapshots stay immutable. Current projections name
    # the already decoded kind-16 records as editable comments; old models
    # used the generic "unknown" template and remain accepted by the writer.
    for document,snapshot in ((frozen_draft,original),(wanted,checked)):
        source=read_project_context(snapshot,witness['selected_pou']).program
        comments={node.offset for node in source.nodes if node.kind_code==16}
        for node in document['nodes']:
            if node.get('source_offset') in comments and node['template']=='unknown':
                node['template']='comment'

    def unexpected_save(*args, **kwargs):
        pytest.fail('these original unsigned containers use the bounded offline writer; no native process belongs in this replay')
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', unexpected_save)
    project = service.create_project(name='Authored complete FBD', plc_model=witness['expected']['cpu'], target_mode='fbd')
    imported = service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='import-authored',
        data_base64=base64.b64encode(original).decode(), program=witness['selected_pou']))
    base = accept(service, imported)
    model = service.fbd.editor(project['id'], version_id=base)['model']
    renames = ({'CCIEFBasicStart_1': 'Axis1_Link_App', 'MC_Power_JEC_1': 'Axis1_Power_App',
                'MC_MoveAbsolute_JEC_1': 'Axis1_Move_App'} if name == 'motion' else
               {'SVFB_ReadObject_1': 'Servo_Read_App', 'SVFB_WriteObject_1': 'Servo_Write_App'})
    for before, after in renames.items():
        node, = [row for row in model['nodes'] if row['symbol'] == before]
        model = service.fbd.editor(project['id'], model,
            dict(action='update_node', id=node['id'], field='symbol', value=after), base)['model']
    port_node, port = ('Axis1_Power_App', 'Enable') if name == 'motion' else ('Servo_Read_App', 'i_bEN')
    node, = [row for row in model['nodes'] if row['symbol'] == port_node]
    view = service.fbd.editor(project['id'], model,
        dict(action='update_port', id=node['id'], port=port, negated=True), base)
    assert view['model'] == frozen_draft
    if name == 'motion':
        power = next(row for row in view['model']['nodes'] if row['symbol'] == port_node)
        axis = {row['name']: (row['side'], row['data_type'], row['class_code']) for row in power['ports'] if row['formal_name'] == 'Axis'}
        assert axis == {'in.Axis': ('in', 'AXIS_REF_JEC', 5), 'out.Axis': ('out', 'AXIS_REF_JEC', 5)}
        template = next(row for row in view['catalog'] if row['template'] == 'function_block:MC_Power_JEC')
        assert {row['name'] for row in template['ports']} >= {'in.Axis', 'out.Axis'}
    preview = service.fbd.preview(project['id'], view['model'], base)
    command = dict(operation='edit', project_id=project['id'], version_id=base,
                   request_id='edit-authored', model=view['model'])
    proposal = service.fbd.propose(command)
    saved = accept(service, proposal)
    assert service.fbd.propose(command)['id'] == proposal['id']
    assert service.fbd.editor(project['id'], version_id=saved)['model'] == preview['model'] == wanted
    assert wanted['schema_version'] == 2 and len(wanted['issues']) == (0 if name == 'motion' else 13)
    from gxw.offline_project import read_offline_project
    import hashlib
    actual = service.projects.artifact(project['id'], saved, 'gxw').read_bytes()
    expected_project, actual_project = read_offline_project(checked), read_offline_project(actual)
    assert expected_project.mapping == actual_project.mapping
    local = read_project_context(actual, wanted['program']).sources.local_table
    changed = {key for key in expected_project.streams if expected_project.streams[key] != actual_project.streams[key]}
    assert changed == {wanted['program'],local}
    history = expected_project.outer['history.xml']
    for key in changed:
        old, new = expected_project.streams[key], actual_project.streams[key]
        assert old[:50] == new[:50] and old[54:] == new[54:]
        assert old[50:54] == bytes(4) and new[50:54] == b'\1\0\0\0'
        digest = lambda value: base64.b64encode(hashlib.md5(value).digest())
        assert history.count(digest(old)) == 1
        history = history.replace(digest(old),digest(new))
    assert history == actual_project.outer['history.xml']
    assert {key:value for key,value in expected_project.outer.items() if key not in ('_hdb','history.xml')} == {
        key:value for key,value in actual_project.outer.items() if key not in ('_hdb','history.xml')}
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == original
    service.close()
    restarted = WorkbenchService(tmp_path/'workspace', tmp_path/'state', model_factory=lambda: (None, {}))
    restarted.start()
    try:
        assert restarted.fbd.editor(project['id'], version_id=saved)['model'] == wanted
    finally:
        restarted.close()


@pytest.mark.parametrize('case,cpu', [('motion','Q06UDV'), ('mapping','Q03UDV')])
def test_authored_import_history_does_not_replace_current_explicit_native_save(service, tmp_path, monkeypatch, case, cpu):
    import zipfile
    from pathlib import Path
    import application.execution as execution
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from gxw.native_write import native_source_plan, verify_native_save
    resource_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: resource_lock(tmp_path/'native-replay.lock'))
    path = private_path('research/' + 'evidence/gxw-authored-fbd-instances-20261012.zip')
    prefix = 'native-application/application-' + case + '/'
    with zipfile.ZipFile(path) as archive:
        files = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist() if name.startswith(prefix)}
    original = files['original.gxw']; draft = json.loads(files['draft.json']); calls = []
    def save(_self, prepared, *, timeout=None):
        stage = 'save-0' if not calls else 'save-1'
        assert prepared.baseline == original and not prepared.outer.get('Project.gd2') and timeout is None
        assert native_source_plan(prepared) == json.loads(files[stage+'-request.json'])['source']
        calls.append(stage)
        return verify_native_save(prepared, files[stage+'/candidate.gxw'], json.loads(files[stage+'-observation.json']))
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    project = service.create_project(name='native author', plc_model=cpu, target_mode='fbd')
    imported = service.fbd.propose(dict(operation='import', project_id=project['id'], request_id='native-import',
        program=draft['program'], data_base64=base64.b64encode(original).decode(), native_save=True))
    base = accept(service, imported)
    validation = service.projects.raw_version(project['id'],base)['validation']
    assert not calls and validation['native_save_requested'] is True and validation['native_source_save_required'] is False
    preview = service.fbd.preview(project['id'], draft, base)
    assert not calls
    command = dict(operation='edit',project_id=project['id'],version_id=base,request_id='native-edit',model=draft,native_save=True)
    proposal = service.fbd.propose(command); saved = accept(service, proposal)
    assert service.fbd.propose(command)['id'] == proposal['id'] and calls == ['save-0']
    model = service.fbd.editor(project['id'],version_id=saved)['model']
    assert model == preview['model'] == json.loads(files['saved-model.json'])
    assert model['schema_version'] == 2 and service.projects.raw_version(project['id'],saved)['validation']['native_save'] == 'passed'
    assert service.projects.artifact(project['id'],base,'gxw').read_bytes() == original
    assert service.projects.artifact(project['id'],saved,'gxw').read_bytes() == files['save-0/candidate.gxw']
    restarted = WorkbenchService(service.store.base_dir, service.state_dir, read_only=True)
    assert restarted.fbd.editor(project['id'],version_id=saved)['model'] == model
    assert restarted.projects.raw_version(project['id'],saved)['validation']['native_source_save_required'] is False
    offline = service.fbd.propose({**command, 'request_id':'backend-during-edit', 'native_save':False})
    assert offline['id'] != proposal['id'] and calls == ['save-0']


def test_fbd_import_native_save_choice_is_explicit_in_web_and_standalone_mcp(tmp_path):
    from gxw.object_model import default_baseline
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    from tests.test_web_api import _app, _login, ORIGIN
    raw = default_baseline(); data = base64.b64encode(raw).decode()
    app = _app(tmp_path/'workspace',tmp_path/'state')
    with TestClient(app,base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post('/api/projects',headers=headers,json={'name':'native import choice','target_mode':'fbd'}).json()['id']
        for invalid in (1, 'true'):
            response = client.post('/api/fbd/proposals',headers=headers,json=dict(operation='import',project_id=pid,
                request_id='invalid-native',data_base64=data,native_save=invalid))
            assert response.status_code == 422
        response = client.post('/api/fbd/proposals',headers=headers,json=dict(operation='import',project_id=pid,
            request_id='native-choice',data_base64=data,native_save=True))
        assert response.status_code == 201
        version = response.json()['result']['version_id']
        assert app.state.service.projects.raw_version(pid,version)['validation']['native_source_save_required'] is False
        transport = MCPToolAdapter(build_default_tool_runtime(),SessionToolContextProvider(tmp_path/'workspace',pid,version))
        proposal = transport.call_tool('create_fbd_candidate',{'operation':'import','data_base64':data,'native_save':True},'native-import')
        assert not proposal.is_error
        pending = proposal.structured_content['data']['pending_action']
        assert pending['native_source_save_required'] is False
        assert 'validation' not in pending


def test_menu_completion_application_uses_native_preview_and_save_then_reopens_current_ports(service, tmp_path, monkeypatch):
    import application.execution as execution
    resource_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: resource_lock(tmp_path/'replay-native.lock'))
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from gxw.native_write import verify_native_save, verify_native_source_creation
    from tests.test_gxw_compiler import menu_completion_witness
    case, entries = menu_completion_witness('application-FX0S')
    original, created = entries['original.gxw'], entries['created.gxw']
    calls = []

    def create(_self, baseline, name, *, pou_kind='function_block', timeout=None):
        assert baseline == original and name == 'APP_CONTEXT_FB' and pou_kind == 'function_block'
        calls.append('create')
        return verify_native_source_creation(baseline, name, created,
                                             json.loads(entries['create-observation.json']))

    def save(_self, prepared, *, timeout=None):
        assert prepared.baseline == created
        stage = 'preview' if calls == ['create'] else 'save'
        calls.append(stage)
        candidate = entries['preview-candidate.gxw' if stage == 'preview' else 'candidate.gxw']
        return verify_native_save(prepared, candidate, json.loads(entries[stage + '-observation.json']))

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', create)
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'save', save)
    project = service.create_project(name='FX0S module closure', plc_model=case['cpu'], target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='import-menu-completion', program='POU_01.Program.pou', data_base64=base64.b64encode(original).decode())))
    middle = accept(service, service.fbd.propose(dict(operation='create_fb', project_id=project['id'],
        version_id=base, request_id='create-menu-completion', source_name='APP_CONTEXT_FB')))
    model = service.fbd.editor(project['id'], version_id=middle, program='POU_01.Program.pou')['model']
    node, = [n for n in model['nodes'] if n['symbol'] == 'MODEL_STAGE_ALPHA']
    model = service.fbd.editor(project['id'], model,
        dict(action='update_node', id=node['id'], field='symbol', value='APP_SOURCE_ALPHA'), middle)['model']
    node, = [n for n in model['nodes'] if n['symbol'] == 'APP_SOURCE_ALPHA']
    model = service.fbd.editor(project['id'], model,
        dict(action='update_port', id=node['id'], port='SIGNAL', negated=True), middle)['model']
    assert model == json.loads(entries['draft.json'])
    preview = service.fbd.preview(project['id'], model, middle)
    assert calls == ['create']
    final = accept(service, service.fbd.propose(dict(operation='edit', project_id=project['id'],
        version_id=middle, request_id='edit-menu-completion', model=model,native_save=True)))
    assert calls == ['create', 'preview']
    wanted = json.loads(entries['saved-model.json'])
    assert service.fbd.editor(project['id'], version_id=final)['model'] == wanted
    # The offline preview retains the existing empty FB header-table name.
    # The independent explicit native save canonicalizes that role's alias.
    offline_preview = deepcopy(preview['model'])
    offline_preview['labels']['APP_CONTEXT_FB.Labels.lh'] = offline_preview['labels'].pop('APP_CONTEXT_FB.Header.lh')
    assert offline_preview == wanted
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == original
    assert service.projects.artifact(project['id'], final, 'gxw').read_bytes() == entries['preview-candidate.gxw']
    assert service.projects.raw_version(project['id'], final)['validation']['native_source_save_required'] is False
    node, = [n for n in wanted['nodes'] if n['symbol'] == 'APP_SOURCE_ALPHA']
    assert {p['name']: p['side'] for p in node['ports'] if p['formal_name'] == 'STATE'} == {
        'in.STATE': 'in', 'out.STATE': 'out'}
    service.close()
    restarted = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state', model_factory=lambda: (None, {}))
    restarted.start()
    try:
        assert restarted.fbd.editor(project['id'], version_id=final)['model'] == wanted
    finally:
        restarted.close()


@pytest.mark.parametrize('cpu,menu_cpu', [
    ('FXU/FX2C', 'FXU/FX2C'), ('L02S', 'L02S/L02S-P'), ('L02', 'L02/L02-P'),
    ('L06', 'L06/L06-P'), ('L26', 'L26/L26-P'), ('L26-BT', 'L26-BT/L26-PBT'), ('FX3G', 'FX3G/FX3GC'),
])
def test_fxu_application_edit_save_reopen_and_check_use_the_same_source_snapshot(service, tmp_path, monkeypatch, cpu, menu_cpu):
    import types
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import verify_native_save, verify_native_source_creation
    from tests.test_gxw_compiler import fxu_application_witness, merged_menu_application_witness, native_request_semantics
    entries, observation, snapshots = (fxu_application_witness() if cpu == 'FXU/FX2C'
                                       else merged_menu_application_witness(cpu))
    check_stage = 'check-fx-grammar' if cpu == 'FXU/FX2C' else 'check-fx3g-lexical' if cpu == 'FX3G' else 'check'
    calls = []

    def create(_self, baseline, name, *, pou_kind='function_block', timeout=None):
        assert baseline == entries['original.gxw'] and name == 'MODULE_FB' and pou_kind == 'function_block'
        calls.append('create')
        return verify_native_source_creation(baseline, name, entries['created.gxw'], json.loads(entries['create-observation.json']))

    stages = [('fb-save', 'created.gxw', 'fb-source.gxw'),
              ('preview', 'initial.gxw', 'candidate.gxw')]
    def save(_self, prepared, *, timeout=None):
        stage, baseline, candidate = stages[len(calls) - 1]
        assert prepared.baseline == entries[baseline]
        calls.append(stage)
        # The frozen compiler evidence belongs to this native output. Both
        # historical saves used the same prepared source; return that verified
        # output on the single preview save and reuse its exact bytes at submit.
        evidence_stage = 'save' if stage == 'preview' else stage
        return verify_native_save(prepared, entries[candidate], json.loads(entries[evidence_stage + '-observation.json']))

    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'create', create)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    project = service.create_project(name=menu_cpu + ' current source closure', plc_model=menu_cpu, target_mode='fbd')
    base = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        request_id='fxu-import', program='POU_01.Program.pou', data_base64=base64.b64encode(entries['original.gxw']).decode())))
    created = accept(service, service.fbd.propose(dict(operation='create_fb', project_id=project['id'],
        version_id=base, request_id='fxu-create', source_name='MODULE_FB')))
    fb_version = accept(service, service.fbd.propose(dict(operation='edit', project_id=project['id'],
        version_id=created, request_id='fxu-fb-ports', model=json.loads(entries['fb-draft.json']),native_save=True)))
    # This historical draft used the old side-grouped IN_OUT geometry. Keep
    # its rejection explicit; importing its frozen GXW still exercises edits
    # of an existing native layout without calling it a new GUI-valid graph.
    with pytest.raises(ValueError, match='dimensions/ports'):
        service.fbd.propose(dict(operation='edit', project_id=project['id'],
            version_id=fb_version, request_id='fxu-caller', model=json.loads(entries['initial-draft.json'])))
    assert calls == ['create', 'fb-save']
    initial = accept(service, service.fbd.propose(dict(operation='import', project_id=project['id'],
        version_id=fb_version, request_id='fxu-existing-layout', program='POU_01.Program.pou', native_save=True,
        data_base64=base64.b64encode(entries['initial.gxw']).decode())))
    model = service.fbd.editor(project['id'], version_id=initial, program='POU_01.Program.pou')['model']
    node, = [n for n in model['nodes'] if n['symbol'] == 'MODEL_STAGE_ALPHA']
    model = service.fbd.editor(project['id'], model,
        dict(action='update_node', id=node['id'], field='symbol', value='APP_SOURCE_ALPHA'), initial)['model']
    node, = [n for n in model['nodes'] if n['symbol'] == 'APP_SOURCE_ALPHA']
    model = service.fbd.editor(project['id'], model,
        dict(action='update_port', id=node['id'], port='SIGNAL', negated=True), initial)['model']
    assert model == json.loads(entries['draft.json'])
    preview = service.fbd.preview(project['id'], model, initial)
    assert calls == ['create', 'fb-save']
    final = accept(service, service.fbd.propose(dict(operation='edit', project_id=project['id'],
        version_id=initial, request_id='fxu-rename-and-port', model=model,native_save=True)))
    assert calls == ['create', 'fb-save', 'preview']
    wanted = json.loads(entries['saved-model.json'])
    assert service.fbd.editor(project['id'], version_id=final)['model'] == preview['model'] == wanted
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == entries['original.gxw']
    service.close()
    restarted = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state', model_factory=lambda: (None, {}))
    restarted.start()
    try:
        assert restarted.fbd.editor(project['id'], version_id=final)['model'] == wanted
        assert restarted.projects.artifact(project['id'], final, 'gxw').read_bytes() == entries['candidate.gxw']
        executable = tmp_path / 'collector-replay.exe'
        executable.write_bytes(b'process transport double; never executed')
        original_init = adapter.NativeWorkspaceValidation.__init__
        monkeypatch.setattr(adapter.NativeWorkspaceValidation, '__init__', lambda self: original_init(self, executable, tmp_path))
        monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))

        def collect(exe, request, root, timeout, error_type):
            assert exe == executable.resolve() and timeout is None
            assert (root / 'input.gxw').read_bytes() == entries['candidate.gxw']
            assert request['source'] == native_request_semantics(json.loads(entries[check_stage + '-request.json'])['source'])
            calls.append('check')
            for name, data in snapshots.items():
                (root / name).write_bytes(data)
            return 0, deepcopy(observation)
        monkeypatch.setattr(adapter, '_run_native_adapter', collect)
        response = restarted.agent_call(dict(project_id=project['id'], version_id=final, name='validate_project',
            arguments={'native': True, 'program': 'POU_01.Program.pou'}, call_id='fxu-current-check'))
        assert not response['is_error'], response
        result = response['data']['data']
        assert result['snapshot'] == {'project_id': project['id'], 'version_id': final,
            'requested_program': 'POU_01.Program.pou', 'cpu': cpu}
        assert result['compilation']['source_context']['status'] == 'current', result['compilation']['source_context']
        if cpu in ('FXU/FX2C', 'FX3G'):
            assert result['compilation']['acceptance'] == 'accepted'
            assert result['public_check'] == {'status': 'incomplete', 'completed': False}
            assert result['raw_backend_check']['status'] == 'completed-rejected'
            assert result['current_raw_source_check'] == 'established'
            assert {r['graph_projection']['instance'] for r in result['diagnostic_projection']['source_diagnostics']} == {
                'POU_01.APP_SOURCE_ALPHA', 'POU_01.MODEL_STAGE_BETA'}
            assert all(r['graph_projection']['status'] == 'uniquely_correlated'
                       for r in result['diagnostic_projection']['source_diagnostics'])
            assert {(r['graph_projection']['source']['symbol'], r['graph_projection']['caller']['symbol'],
                     r['graph_projection']['caller']['network']) for r in result['diagnostic_projection']['source_diagnostics']} == {
                ('Y1', 'APP_SOURCE_ALPHA', 1), ('Y1', 'MODEL_STAGE_BETA', 3)}
        else:
            assert result['compilation']['acceptance'] == 'rejected'
            assert result['public_check'] == {'status': 'not_requested', 'completed': False}
            assert result['raw_backend_check']['status'] == 'not_observed'
            assert result['current_raw_source_check'] == 'not_established'
            assert not result['diagnostic_projection']['source_diagnostics']
        assert calls[-1] == 'check'
    finally:
        restarted.close()


def _roundtrip_program_resource(model, value):
    parsed = model.model_validate(value)
    assert parsed.model_dump(exclude_unset=True) == value
    assert parsed.model_dump(mode='json', by_alias=True, exclude_unset=True) == value
    return parsed


@pytest.mark.parametrize('plc_model', ['FX3U', 'FX5U'])
def test_program_resource_fixed_ir_sections_preserve_actual_analysis_and_unknown_timing(plc_model):
    from plc.ir import build_plc_ir
    from tests.test_plc_static_analysis import ladder, rung, contact, compare, instruction, coil, timer
    from integrations.web.program_contracts import PLCProgram, ProgramTiming, ProgramLogic, ProgramAnalysis
    first_scan = 'SM402' if plc_model == 'FX5U' else 'M8002'
    clock = 'SM412' if plc_model == 'FX5U' else 'M8013'
    source = ladder(
        rung(1, inputs=[contact('NO', first_scan)], outputs=[instruction('MOV', ['K10', 'D100'])], note='初始化'),
        rung(2, header=compare('= D100 K10'), inputs=[contact('NO', 'X0')],
             outputs=[instruction('MOV', ['K20', 'D100'])]),
        rung(3, header=compare('= D100 K20'), inputs=[contact('NO', 'X1')],
             outputs=[instruction('MOV', ['K10', 'D100'])]),
        rung(4, header=compare('= D100 K20'), outputs=[coil('Y0')]),
        rung(5, inputs=[contact('NO', clock)], outputs=[timer('T0', 'K2')]),
        rung(6, inputs=[contact('NC', 'Y0')], outputs=[coil('Y1')]),
        rung(7, inputs=[contact('P', 'X2')], outputs=[coil('M2')]),
        rung(8, inputs=[contact('NO', 'M0')], outputs=[instruction('DRVI', ['K100', 'K1000', 'Y2', 'Y4'])]),
    )
    original = deepcopy(source)
    value = build_plc_ir(source, plc_model=plc_model,
        semantic_requirements=[{'semantic': 'FIRST_SCAN', 'devices': [first_scan], 'strict': True},
            {'semantic': 'RISING_EDGE', 'devices': ['X2'], 'pulse_width_ms': .01, 'strict': True}],
        analysis_config={'mutex': [{'devices': ['Y0', 'Y1'], 'source': 'contract-fixture'}],
            'same_scan_expectations': [{'device': 'D100', 'reader_network': 'N0002', 'writer_network': 'N0003'}],
            'terminal_states': {'D100': [20]},
            'timing': {'scan_budget_ms': .01, 'scan_warning_ms': 15, 'allocation': {'CONTROL': .004}}})
    parsed = _roundtrip_program_resource(PLCProgram, value)
    assert source == original
    assert isinstance(parsed.timing, ProgramTiming) and isinstance(parsed.logic, ProgramLogic)
    assert isinstance(parsed.analysis, ProgramAnalysis) and parsed.source.format == 'ladder_v1'
    assert parsed.logic.state_machines and parsed.logic.state_machines[0].transitions
    assert parsed.timing.timers['T0'].preset == 'K2'
    assert parsed.timing.coverage and parsed.timing.pulse_capture_assessments
    assert parsed.analysis.dependency_graph.writers['D100'] and parsed.analysis.findings
    assert parsed.timing.performance.estimate.is_exact is False
    assert parsed.timing.performance.supported is (plc_model == 'FX3U')
    if plc_model == 'FX5U':
        assert parsed.timing.performance.estimate.worst_ms is None
        assert parsed.timing.performance.scan_budget.status == 'unknown'
    schema = PLCProgram.model_json_schema()
    assert schema['properties']['timing']['$ref'].endswith('/ProgramTiming')
    assert schema['properties']['logic']['$ref'].endswith('/ProgramLogic')
    assert schema['properties']['analysis']['$ref'].endswith('/ProgramAnalysis')
    assert schema['properties']['source']['$ref'].endswith('/ProgramSource')


def test_program_resource_service_ir_and_recursive_ladder_preserve_nullable_fields(tmp_path):
    from tests.test_web_api import _legacy_workspace
    from plc.ir import build_plc_ir, ir_to_ladder
    from integrations.web.program_contracts import PLCProgram, LadderDocument, LadderElement
    store, project_id, version_id, _ = _legacy_workspace(tmp_path / 'program-resource-workspace')
    owner = WorkbenchService(store.base_dir, tmp_path / 'program-resource-state', read_only=True)
    _roundtrip_program_resource(PLCProgram, owner.projects.program(project_id, version_id))
    source = {'device_comments': {'X0': 'token', 'Y0': '_state'}, 'rungs': [{
        'rung_id': 1, 'debug_note': None, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0,
            'inputs': [{'type': 'parallel_block', 'label': None, 'branches': [
                [{'type': 'NO', 'address': 'X0', 'label': None}],
                [{'type': 'NC', 'address': 'X1'}]]}],
            'outputs': [{'type': 'COIL', 'address': 'Y0', 'label': None}]}]}]}
    for value in (source, ir_to_ladder(build_plc_ir(source))):
        parsed = _roundtrip_program_resource(LadderDocument, value)
        parallel = parsed.rungs[0].branches[0].inputs[0]
        assert isinstance(parallel, LadderElement) and parallel.branches[0][0].address == 'X0'
    assert LadderDocument.model_json_schema()['properties']['rungs']['items']['$ref'].endswith('/LadderRung')


def test_program_resource_fbd_catalog_and_port_edits_roundtrip_service_and_frozen_source(service):
    from gxw.editor import edit_draft, editor_catalog
    from integrations.web.program_contracts import FBDCatalog, FBDModel, FBDPortEdit, FBDParameterModel
    project = service.create_project(name='Typed FBD resources', target_mode='fbd', plc_model='FX3U')
    raw = source_binding_baseline()
    version = accept(service, service.fbd.propose({'operation': 'import', 'project_id': project['id'],
        'request_id': 'program-resource-import', 'data_base64': base64.b64encode(raw).decode()}))
    frozen, _ = created_fbd_context('Q04UDV')
    for value in (service.fbd.catalog(project['id'], version), editor_catalog(frozen)):
        parsed = _roundtrip_program_resource(FBDCatalog, value)
        assert isinstance(parsed.parameter_model, FBDParameterModel)
        assert parsed.parameter_model.program == value['program']
        assert parsed.empty_parameter_model.networks == []
        assert parsed.project_configuration.gui_configuration.status == value['project_configuration']['gui_configuration']['status']
        assert all(type(n) is int for pair in parsed.project_configuration.system_variable_ranges.values() for n in pair)
    model = service.fbd.editor(project['id'], version_id=version)['model']
    target = model['nodes'][0]
    edited = edit_draft(model, {'action': 'update_port', 'id': target['id'], 'port': 'in.STATE', 'negated': True})['model']
    parsed = _roundtrip_program_resource(FBDModel, edited)
    assert isinstance(parsed.nodes[0].port_edits['in.STATE'], FBDPortEdit)
    assert parsed.nodes[0].port_edits['in.STATE'].negated is True
    assert all(type(node.x) is int and type(node.y) is int for node in parsed.nodes)
    assert all(type(v) is int for wire in parsed.wires for point in (wire.start, wire.end) if point for v in point)


def test_program_resource_named_parameter_maps_and_json_schema_properties_are_data():
    from hypothesis import given, settings, strategies as st
    from integrations.web.program_contracts import FBDCatalog, FBDParameterModel
    from gxw.editor import editor_catalog
    keys = st.one_of(st.sampled_from(['token', 'password', '_state', 'in.STATE', 'out.STATE']),
        st.text(min_size=1, max_size=20))

    @settings(max_examples=40, deadline=None, derandomize=True)
    @given(st.dictionaries(keys, st.text(max_size=40), max_size=6))
    def parameter_maps(arguments):
        value = {'networks': [{'calls': [{'id': 'call', 'type': 'CurrentFB', 'instance': 'Instance',
            'arguments': arguments}], 'nodes': [{'id': 'node', 'template': 'function_block:CurrentFB',
            'port_edits': {name: {'negated': False} for name in arguments}}]}]}
        _roundtrip_program_resource(FBDParameterModel, value)
    parameter_maps()
    catalog = editor_catalog()
    catalog['parameter_schema'] = {'type': 'object', 'properties': {
        'token': {'type': 'string'}, '_state': {'type': 'object', 'properties': {'password': {'type': 'integer'}}}}}
    _roundtrip_program_resource(FBDCatalog, catalog)


@pytest.mark.parametrize('cpu,expected', [('unverified-cpu', {'unverified-cpu-connection-profile'}),
    ('FX3U/FX3UC', {'unobserved-connector-flags', 'unobserved-native-symbol', 'missing-reciprocal-port'})])
def test_program_resource_sfc_topology_issue_variants_keep_unresolved_reasons(cpu, expected):
    from gxw.sfc_pou import _native_connections
    from integrations.web.program_contracts import SFCTopology, SFCTopologyIssue
    cells = [{'id': 'cell:0:0', 'x': 0, 'y': 0, 'result': 3, 'branch': 0},
             {'id': 'cell:2:0', 'x': 2, 'y': 0, 'result': 0, 'type': 999, 'number': 0},
             {'id': 'cell:4:0', 'x': 4, 'y': 0, 'result': 3, 'branch': 4}]
    parsed = _roundtrip_program_resource(SFCTopology, _native_connections(cells, cpu=cpu))
    assert {issue.reason for issue in parsed.issues} == expected
    assert all(isinstance(issue, SFCTopologyIssue) for issue in parsed.issues)
    assert SFCTopology.model_json_schema()['properties']['issues']['items']['$ref'].endswith('/SFCTopologyIssue')


def test_program_resource_sfc_model_roundtrips_frozen_native_grid_without_semantic_promotion():
    from tests.test_native_validation import retained_sfc_diagnostic_witness
    from application.sfc import read_snapshot
    from integrations.web.program_contracts import SFCModel
    witness = retained_sfc_diagnostic_witness()
    value = read_snapshot(witness['raw'], native_graph=witness['graph'])['model']
    parsed = _roundtrip_program_resource(SFCModel, value)
    assert parsed.graph.position_resolution == value['graph']['position_resolution']
    assert all(type(cell.x) is int and type(cell.y) is int for cell in parsed.graph.cells)



def test_existing_function_authoring_keeps_interface_only_return_type_edits():
    from application.fbd import authoring_command
    assert authoring_command(dict(operation='edit_st_function',source_name='Calc.pou',return_type='DINT'))==dict(
        operation='edit_st_function',source_name='Calc.pou',return_type='DINT')
    assert authoring_command(dict(operation='edit_st_source',source_name='Calc.pou',st_text='Calc:=;',return_type='REAL'))==dict(
        operation='edit_st_source',source_name='Calc.pou',st_text='Calc:=;',return_type='REAL')
    with pytest.raises(ValueError,match='FUNCTION'):
        authoring_command(dict(operation='create_st_fb',source_name='FB',st_text='',declarations=[],return_type='BOOL'))



def test_http_st_primary_gxw_version_can_edit_preview_save_and_reopen_an_existing_fbd(tmp_path,monkeypatch):
    from tests.test_gxw_structured_reader import offline_fbd_project
    from gxw.offline_project import read_offline_project
    import application.fbd as fbd
    monkeypatch.setattr(fbd,'_native_candidate',lambda *args,**kwargs:pytest.fail('offline mixed-language editing started native'))
    raw,_=offline_fbd_project(6)
    with TestClient(_app(tmp_path/'workspace',tmp_path/'state'),base_url=ORIGIN) as client:
        headers=_login(client)
        imported=client.post('/api/gxw/import',headers=headers,json=dict(filename='Calculator.gxw',
            data_base64=base64.b64encode(raw).decode(),program='Calculator.Program.pou',request_id='mixed-import'))
        assert imported.status_code==201,imported.text
        result=imported.json();project=result['project_id']
        accepted=client.post('/api/proposals/'+result['proposal_id']+'/decision',headers=headers,json={'decision':'accept'})
        assert accepted.status_code==200,accepted.text
        base=accepted.json()['proposal']['result']['version_id']
        assert client.get(f'/api/projects/{project}/versions/{base}').json()['target_mode']=='st'
        selection=dict(project_id=project,version_id=base,program='SmallCalc.Program.pou')
        source=client.get('/api/gxw/source',params=selection)
        assert source.status_code==200,source.text
        response=client.post('/api/fbd/editor',headers=headers,json=selection)
        assert response.status_code==200,response.text
        editor=response.json()
        fbd_sources=['ExSEG16.Program.pou','ExSEG7.Program.pou','Mov4BIT.Program\\HMI.lnb','SmallCalc.Program.pou']
        assert editor['programs']==fbd_sources
        catalog=client.get('/api/fbd/catalog',params=selection)
        assert catalog.status_code==200,catalog.text
        assert catalog.json()['programs']==fbd_sources
        model=editor['model']
        assert model['program']=='SmallCalc.Program.pou'
        for node in model['nodes']:
            capabilities=next(row for row in editor['presentation']['nodes'] if row['id']==node['id'])
            assert capabilities['resizable']==(node['template']=='function_block:Calculator')
            assert capabilities['callable']==(node['template']=='function_block:Calculator')
        model['nodes'].extend([dict(id='cross_in',template='input',symbol='TRUE',x=2,y=20),
                              dict(id='cross_out',template='output',symbol='Y0',x=12,y=20),
                              dict(id='cross_comment',template='comment',symbol='Editable comment',x=2,y=25)])
        if model.get('blocks'):
            for node in model['nodes'][-3:]:node['block']=0
        resized=client.post('/api/fbd/editor',headers=headers,json=dict(**selection,model=model,
            command=dict(action='resize_node',id='cross_comment',width=18,height=4)))
        assert resized.status_code==200,resized.text
        resized=resized.json();model=resized['model']
        comment=next(row for row in resized['presentation']['nodes'] if row['id']=='cross_comment')
        assert comment['resizable'] is True and comment['callable'] is False
        assert next(row for row in model['nodes'] if row['id']=='cross_comment')['resize']==dict(width=18,height=4)
        wire={'from':'cross_in.OUT','to':'cross_out.IN'}
        if model.get('blocks'):wire['block']=0
        model['wires'].append(wire)
        preview=client.post('/api/fbd/preview',headers=headers,json=dict(project_id=project,version_id=base,
            model=model,save_policy='draft'))
        assert preview.status_code==200,preview.text
        proposed=client.post('/api/fbd/proposals',headers=headers,json=dict(project_id=project,version_id=base,
            operation='edit',request_id='mixed-edit',model=model,save_policy='draft'))
        assert proposed.status_code==201,proposed.text
        decision=client.post('/api/proposals/'+proposed.json()['id']+'/decision',headers=headers,json={'decision':'accept'})
        assert decision.status_code==200,decision.text
        final=decision.json()['proposal']['result']['version_id']
        reopened=client.post('/api/fbd/editor',headers=headers,json=dict(project_id=project,version_id=final,program='SmallCalc.Program.pou'))
        assert reopened.status_code==200,reopened.text
        assert reopened.json()['model']==preview.json()['model']
        download=client.get(f'/api/projects/{project}/versions/{final}/artifacts/gxw?download=true')
        assert download.status_code==200,download.text
        before,after=read_offline_project(raw),read_offline_project(download.content)
        assert {name for name in before.streams if before.streams[name]!=after.streams[name]}=={'SmallCalc.Program.pou'}
        assert client.get(f'/api/projects/{project}/versions/{base}/artifacts/gxw').content==raw


# --- Task-scoped FBD context: request selection, rule blocking and measured wires ---

def test_fbd_task_scope_selects_a_small_request_context_instead_of_the_directory():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.object_model import default_baseline
    catalog = catalog_snapshot(default_baseline())
    full = {node['template'] for node in catalog['nodes']}
    assert len(full) > 1000
    result = build_fbd_generation_context('使用TON实现延时控制', plc_model='FX3U', catalog=catalog)
    nodes = {node['template'] for node in result['generation_input']['catalog']}
    assert 0 < len(nodes) < 200, len(nodes)
    assert nodes < full
    assert {'contact', 'coil', 'input', 'output', 'function_block:TON', 'function:MOV',
            'function:@EQ-2'} <= nodes
    report = result['generation_handoff']['catalog_report']
    assert report['catalog_scope'] == 'task'
    assert report['available_template_count'] == len(full)
    assert report['expansion']['arguments'] == {'catalog_scope': 'full'}
    assert set(report['selected']) == nodes


def test_fbd_explicit_templates_are_case_insensitive_and_never_widen_the_table():
    from application.fbd import catalog_snapshot
    from application.fbd_context import FBDContextError, select_task_catalog
    from gxw.object_model import default_baseline
    catalog = catalog_snapshot(default_baseline())
    selection = select_task_catalog(catalog, templates=['function_block:ton'])
    assert [node['template'] for node in selection['nodes']][0] == 'function_block:TON'
    assert selection['missing_templates'] == []
    missing = select_task_catalog(catalog, templates=['function_block:NoSuchDefinition'])
    assert missing['nodes'] == []
    assert missing['missing_templates'][0]['template'] == 'function_block:NoSuchDefinition'
    with pytest.raises(FBDContextError):
        select_task_catalog(catalog, scope='everything')
    with pytest.raises(FBDContextError):
        select_task_catalog(catalog, templates='function_block:TON')


def test_fbd_graph_references_are_kept_case_insensitively_in_both_projections():
    from application.fbd import catalog_snapshot
    from application.fbd_context import select_task_catalog
    from gxw.object_model import default_baseline
    catalog = catalog_snapshot(default_baseline())
    flat = {'program': catalog['program'], 'wires': [], 'nodes': [
        {'id': 'n1', 'template': 'function_block:ton', 'symbol': 'T1', 'ports': []},
        {'id': 'n2', 'template': 'Function:MOV', 'symbol': '', 'ports': []}]}
    selection = select_task_catalog(catalog, scope='task', previous=flat)
    kept = {node['template'] for node in selection['nodes']}
    assert {'function_block:TON', 'function:MOV'} <= kept
    assert selection['unresolved_references'] == []


def test_fbd_required_language_rules_block_generation_without_a_bypass():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from application.fbd_context import (FBD_RULE_TARGETS, FBDContextRuleError, language_rule_report)
    from gxw.object_model import default_baseline
    report = language_rule_report('FX3U')
    assert report['status'] == 'verified' and report['mandatory_missing'] == []
    assert set(report['targets']) == set(FBD_RULE_TARGETS)
    assert 'FBD.TIMERS' not in FBD_RULE_TARGETS
    with pytest.raises(FBDContextRuleError) as failure:
        language_rule_report('FX5U')
    assert 'FBD.PROJECT' in str(failure.value)
    catalog = catalog_snapshot(default_baseline())
    with pytest.raises(FBDContextRuleError):
        build_fbd_generation_context('使用TON实现延时控制', plc_model='FX5U', catalog=catalog)
    with pytest.raises(TypeError):
        language_rule_report('FX3U', rules_required=True)


def test_fbd_wire_budget_uses_the_shared_compiler_and_drops_whole_units():
    from application.fbd_context import FBDContextBudgetError, FBDUnit, build_payload, fit_units
    mandatory = FBDUnit('schema', 'schema', 'schema_version', 2)
    rows = [{'name': 'IN', 'data_type': 'BOOL'}] * 20000
    optional = FBDUnit('structures', 'type', 'structures', {'TON': rows}, mandatory=False,
                       reason='composite member rows')
    payload, report = fit_units([mandatory, optional], instructions='system',
                                model_profile={'context_window': 24000})
    assert payload == {'schema_version': 2}
    assert report['budget_basis'] == 'application_wire_messages'
    assert report['compiler'] == 'application.context_compiler.ContextCompiler'
    assert report['fits'] is True and report['budget_state'] in {'available', 'known'}
    assert report['prune_rounds'] >= 1
    assert [unit['id'] for unit in report['dropped_units']] == ['structures']
    assert report['drops_reason'] == 'whole_unit_removal_no_json_truncation'
    assert build_payload([optional])['structures']['TON'] == rows
    with pytest.raises(FBDContextBudgetError):
        fit_units([FBDUnit('previous', 'baseline', 'previous', 'x' * 200000)],
                  instructions='system', model_profile={'context_window': 24000})


def test_fbd_call_bindings_read_nodes_and_calls_with_the_real_interface():
    from application.fbd import catalog_snapshot
    from application.fbd_context import call_bindings
    from gxw.object_model import default_baseline
    catalog = catalog_snapshot(default_baseline())
    nodes_only = {'program': catalog['program'], 'wires': [], 'nodes': [
        {'id': 'n1', 'template': 'function_block:TON', 'symbol': 'TimerA',
         'ports': [{'name': 'IN', 'data_type': 'BOOL', 'value': 'X0'},
                   {'name': 'PT', 'data_type': 'TIME', 'value': "T#1s"}]},
        {'id': 'n2', 'template': 'contact', 'symbol': 'X1', 'ports': []}]}
    with_calls = {'program': catalog['program'], 'networks': [{'id': 'net1',
        'nodes': [{'id': 'n3', 'template': 'function_block:CTU', 'symbol': 'CounterA', 'ports': []}],
        'calls': [{'id': 'c1', 'type': 'TON', 'instance': 'TimerB', 'arguments': {'IN': 'X2'}}]}]}
    rows = call_bindings(nodes_only, catalog) + call_bindings(with_calls, catalog)
    carriers = {(row['carrier'], row['template'], row['instance']) for row in rows}
    assert ('node', 'function_block:TON', 'TimerA') in carriers
    assert ('call', 'function_block:TON', 'TimerB') in carriers
    assert ('node', 'function_block:CTU', 'CounterA') in carriers
    assert all(row['interface'] and row['interface_status'] == 'available' for row in rows)
    timer = next(row for row in rows if row['instance'] == 'TimerA')
    assert {port['name'] for port in timer['interface']} >= {'IN', 'PT', 'Q', 'ET'}
    assert timer['arguments'][0] == {'name': 'IN', 'data_type': 'BOOL', 'value': 'X0'}
    assert all(row['template'] != 'contact' for row in rows)


def test_fbd_source_context_carries_st_bodies_interfaces_and_callers_in_one_transaction():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from application.fbd_context import source_program_context
    from gxw.object_model import read_project_context
    from tests.test_gxw_offline_authoring import native_project
    raw = native_project()
    source = source_program_context(raw)
    assert source['status'] == 'available', source.get('reason')
    bodies = {row['source_name']: row for row in source['st_sources']}
    fb = bodies['ScaleValue.Program.pou']
    assert 'OutValue := InValue' in fb['st_text']
    assert fb['source_id'] == 'ScaleValue.Labels.lh' and len(fb['declarations']) == 4
    callers = {row['source_name']: row for row in source['fbd_callers']}
    assert callers['POU_01.Program.pou']['calls'] == ['function_block:ScaleValue']
    assert callers['POU_01.Program.pou']['parameter_model']['networks']
    assert {row['source_id'] for row in source['pous']} >= {'ScaleValue.Labels.lh'}
    assert set(source['transaction']['editable_sources']) >= {'ScaleValue.Program.pou',
                                                             'POU_01.Program.pou'}
    assert source['transaction']['one_transaction'] is True
    assert source['native'] == {'compilation': 'not_run', 'execution': 'not_run'}
    catalog = catalog_snapshot(raw, 'POU_01.Program.pou')
    previous = read_project_context(raw, 'POU_01.Program.pou').object_model()
    result = build_fbd_generation_context('把 ScaleValue 增加一个 BOOL 输入并更新所有调用者',
        plc_model='FX3U', catalog=catalog, previous=previous, source_baseline=raw)
    generation = result['generation_input']
    assert generation['source_context']['st_sources']
    binding = generation['call_bindings'][0]
    assert (binding['template'], binding['instance']) == ('function_block:ScaleValue', 'ScaleInst')
    assert binding['interface_status'] == 'available'
    assert {port['name'] for port in binding['interface']} >= {'Enabled', 'InValue', 'OutValue'}
    assert 'function_block:ScaleValue' in {node['template'] for node in generation['catalog']}
    assert result['generation_handoff']['catalog_report']['unresolved_references'] == []
    paths = generation['supported_paths']
    assert paths['edit_existing_source']['supported'] is True
    assert paths['joint_multi_source_transaction']['supported'] is True
    assert paths['create_pou']['supported'] is False
    assert paths['ladder_fbd_conversion']['supported'] is False


def test_fbd_generation_input_keeps_the_existing_contract_and_reports_budget():
    from application.fbd import build_fbd_generation_context, catalog_snapshot
    from gxw.generation_contract import FBD_GENERATION_PROMPT
    from gxw.object_model import default_baseline
    catalog = catalog_snapshot(default_baseline())
    result = build_fbd_generation_context('使用TON实现延时控制', plc_model='FX3U', catalog=catalog)
    generation_input = result['generation_input']
    assert {'cpu', 'program', 'catalog', 'parameter_schema', 'declaration_tables', 'confirmed_spec',
            'existing_declarations', 'previous', 'request', 'call_bindings', 'source_context',
            'supported_paths', 'language_rules'} <= set(generation_input)
    assert generation_input['previous'] is None
    assert generation_input['call_bindings'] == []
    assert generation_input['source_context']['status'] == 'unavailable'
    assert generation_input['catalog_scope'] == 'task'
    assert generation_input['target']['cpu_menu']['cpu'].startswith('FX3U')
    assert generation_input['unknown_boundaries']
    assert generation_input['language_rules']['status'] == 'verified'
    assert result['generation_instructions'].startswith(FBD_GENERATION_PROMPT)
    handoff = result['generation_handoff']
    report = handoff['budget_report']
    assert report['budget_basis'] == 'application_wire_messages'
    assert report['compiler'] == 'application.context_compiler.ContextCompiler'
    assert report['drops_reason'] == 'whole_unit_removal_no_json_truncation'
    assert report['budget_state'] in {'available', 'known', 'unknown'}
    assert report['image_reserve_is_an_assumption'] is True
    assert handoff['omitted_units'] == []
    assert handoff['catalog_report']['catalog_scope'] == 'task'


def test_fbd_catalog_tool_keeps_the_full_table_and_narrows_only_when_asked():
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from application.read_delivery import assemble_read_pages
    context = build_tool_context({'id': 'fbd-catalog', 'plc_model': 'FX3U', 'target_mode': 'fbd'})
    registry = build_default_tool_registry()
    def read(arguments):
        pages = [registry.call('get_fbd_catalog', arguments, context)]
        while not pages[-1]['data']['delivery']['complete']:
            followup = pages[-1]['data']['delivery']['next']
            pages.append(registry.call(followup['tool'], followup['arguments'], context))
        return {**pages[0], 'data': assemble_read_pages(pages)}
    full = read({})
    assert full['ok'], full
    assert full['data']['catalog_scope'] == 'full'
    whole = {node['template'] for node in full['data']['nodes']}
    assert len(whole) == full['data']['available_template_count'] > 1000
    scoped = read({'catalog_scope': 'task'})
    assert scoped['ok'], scoped
    assert scoped['data']['catalog_scope'] == 'task'
    narrowed = {node['template'] for node in scoped['data']['nodes']}
    assert 0 < len(narrowed) < 200 and narrowed < whole
    assert scoped['data']['available_template_count'] == len(whole)
    assert scoped['data']['dependency_closure']['expansion']['arguments'] == {'catalog_scope': 'full'}
    single = read({'templates': ['ton']})
    assert single['ok'], single
    assert [node['template'] for node in single['data']['nodes']] == ['function_block:TON']
    missing = read({'templates': ['function_block:NoSuchDefinition']})
    assert missing['ok'], missing
    assert missing['data']['nodes'] == []
    assert missing['data']['missing_templates'][0]['template'] == 'function_block:NoSuchDefinition'
