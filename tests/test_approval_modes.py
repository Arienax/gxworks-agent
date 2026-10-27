"""User-selected consent rules. Real stores/routes; external executions are spies."""
import copy
from concurrent.futures import Future
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from application.approval import ApprovalPolicy, allows
from application.workspace import ConflictError
from application.workbench import WorkbenchService
from test_web_api import ORIGIN, AGENT, _app, _login, _Provider, _complete, offline


def test_policy_default_read_never_creates_files(tmp_path):
    policy = ApprovalPolicy(tmp_path / 'not-created')
    assert policy.read() == {'mode': 'ask', 'revision': 0}
    assert not policy.path.parent.exists()


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
@pytest.mark.parametrize('action', ['gx_import', 'simulation', 'debug', 'native_engineering', 'write_plc', 'force_device', 'shell'])
def test_delegation_is_bounded_to_existing_actions(mode, action):
    expected = action in {'gx_import', 'simulation', 'debug', 'native_engineering'} and (mode == 'full' or (mode == 'auto' and action in {'simulation', 'debug'}))
    assert allows(mode, action, {'project_id': 'p', 'version_id': 'v', 'plan': {}}) is expected
    assert not allows(mode, action, {})
    if action in {'simulation', 'debug', 'native_engineering'}:
        assert not allows(mode, action, {'project_id': 'p', 'version_id': 'v'})


def test_workspace_settings_require_explicit_full_consent_and_compare_revision(tmp_path):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state')
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        assert client.get('/api/settings/approval').status_code == 401
        headers = _login(client)
        initial = client.get('/api/settings/approval').json()
        assert initial == {'mode': 'ask', 'revision': 0, 'local_autosave': True, 'read_only': False}
        value = {'mode': 'full', 'expected_revision': 0}
        assert client.put('/api/settings/approval', json=value, headers=headers).status_code == 403
        value['confirm_full_access'] = True
        assert client.put('/api/settings/approval', json=value).status_code == 403
        assert client.put('/api/settings/approval', json=value, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
        assert client.put('/api/settings/approval', json=value, headers=headers).json()['mode'] == 'full'
        # Even full mode does not remove session, host, origin or CSRF checks.
        assert client.post('/api/projects', json={'name': 'no-csrf'}).status_code == 403
        assert client.get('/api/settings/approval', headers={'Host': 'evil.example'}).status_code == 403
        assert client.put('/api/settings/approval', json={'mode':'auto','expected_revision':0}, headers=headers).status_code == 409
        assert client.put('/api/settings/approval', json={'mode':'invented','expected_revision':1}, headers=headers).status_code == 422
        assert client.put('/api/settings/approval', json={'mode':'ask','expected_revision':1}, headers=headers).json()['mode'] == 'ask'
        unauthenticated = TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN)
        assert unauthenticated.put('/api/settings/approval', json=value, headers={'Authorization': 'Bearer '+AGENT, 'Origin': ORIGIN}).status_code == 401
    reopened = WorkbenchService(service.store.base_dir, service.state_dir)
    reopened.start()
    try:
        assert reopened.approval_settings()['mode'] == 'ask'
        assert reopened.approval_settings()['revision'] == 2
        assert reopened.jobs.list() == []
    finally:
        reopened.close()


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_generation_saves_once_and_exports_without_a_second_approval(offline, tmp_path, mode):
    provider = _Provider()
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state', model_factory=lambda: (provider, {'model':'offline'}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        service.update_approval_settings(mode=mode, expected_revision=0, confirm_full_access=True)
        pid = service.create_project(name='autosave')['id']
        command = {'kind':'generation','project_id':pid,'request_id':'only-once','text':'X0 controls Y0','response_language':'en'}
        jid, output = _complete(client, service, client.post('/api/jobs', json=command, headers=headers))
        assert output['status'] == 'saved' and output['version_id']
        project = client.get('/api/projects/'+pid).json()
        assert project['version_count'] == 1 and project['active_version_id'] == output['version_id']
        assert service.proposals.get(output['proposal_id'])['summary']['approval']['source'] == 'local_autosave'
        assert not [p for p in service.proposals.list(pid) if p['status'] == 'pending']
        vid = output['version_id']
        for aid in ('program_csv','comment_csv','svg','ir','json','st_from_ir'):
            response = client.get(f'/api/projects/{pid}/versions/{vid}/artifacts/{aid}?download=true')
            assert response.status_code == 200 and response.content and 'attachment' in response.headers['content-disposition']
        assert client.post('/api/jobs', json=command, headers=headers).json()['id'] == jid
        assert service.projects.project(pid)['version_count'] == 1 and len(provider.requests) == 1


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_confirmed_approach_mismatch_does_not_block_local_generation_save(offline, tmp_path, mode):
    from test_generation_delivery import prepared, generate
    service, client = prepared(tmp_path)
    with client:
        service.update_approval_settings(mode=mode, expected_revision=0, confirm_full_access=True)
        pid, jid, output, _ = generate(client, service, blocked=True)
        assert output['status'] == 'saved' and output.get('version_id')
        assert service.projects.project(pid)['version_count'] == 1
        assert service.proposals.get(output['proposal_id'])['status'] == 'accepted'
        assert client.get(f'/api/jobs/{jid}/preview').json()['read_only'] is True


def setup_external(service, mode, monkeypatch):
    from test_application_persistence import _base
    import application.execution
    monkeypatch.setattr(application.execution, 'read_gx_environment', lambda: {
        'status': 'ready', 'passed': True, 'desktop_execution_required': True,
        'gx_works2_running': True, 'project_open': True, 'message': 'GX Works2 已运行。',
    })
    pid = service.create_project(name='execution-spy')['id']
    base, _ = _base(service.store, pid)
    service.store.activate_version(pid, base['id'])
    service.update_approval_settings(mode=mode, expected_revision=0, confirm_full_access=True)
    calls = []
    def execute(*args, **kwargs):
        calls.append((args, kwargs))
        future = Future(); future.set_result({'status':'completed','passed':False})
        return future
    monkeypatch.setattr(service.execution, 'submit_approved', execute)
    monkeypatch.setattr(service.projects, 'plan', lambda p,v,i,kind: {'plan_id':i,'project_id':p,'base_version_id':v,'kind':kind})
    return pid, base['id'], calls


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
@pytest.mark.parametrize('action', ['gx_import', 'simulation', 'debug'])
def test_mode_actually_controls_external_dispatch_and_never_duplicates(tmp_path, monkeypatch, mode, action):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state'); service.start()
    try:
        pid, vid, calls = setup_external(service, mode, monkeypatch)
        command = {'action':action,'project_id':pid,'version_id':vid,'plan_id':'bound-plan','request_id':'external-once'}
        proposal = service.execution_proposal(command)
        automatic = mode == 'full' or (mode == 'auto' and action != 'gx_import')
        assert bool(proposal.get('execution_job_id')) is automatic
        if automatic:
            service.jobs._futures[proposal['execution_job_id']].result(timeout=10)
            assert len(calls) == 1
            saved = service.proposals.get(proposal['id'])
            assert saved['status'] == 'accepted' and saved['summary']['approval'] == {'source':'policy','mode':mode}
        else:
            assert calls == [] and proposal['status'] == 'pending'
        replay = service.execution_proposal(command)
        assert replay['id'] == proposal['id'] and len(calls) == int(automatic)
    finally:
        service.close()


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_auto_prepared_gx_target_still_requires_the_existing_approval_policy(tmp_path, monkeypatch, mode):
    import application.execution

    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state'); service.start()
    try:
        pid, vid, calls = setup_external(service, mode, monkeypatch)
        monkeypatch.setattr(application.execution, 'read_gx_environment', lambda: {
            'gx_works2_installed': True, 'gx_works2_running': False, 'project_open': False})
        proposal = service.execution_proposal({'action': 'gx_import', 'project_id': pid,
                                              'version_id': vid, 'request_id': 'prepare-after-approval'})
        if mode == 'full':
            service.jobs._futures[proposal['execution_job_id']].result(timeout=10)
            assert len(calls) == 1
        else:
            assert proposal['status'] == 'pending' and calls == []
    finally:
        service.close()


@pytest.mark.parametrize('installed,model,target', [(False, 'FX3U', 'ladder'),
                                                   (True, 'FX5U', 'ladder'),
                                                   (True, 'LJ72MS15', 'ladder'),
                                                   (True, 'FX3U', 'fbd')])
def test_gx_auto_prepare_preflight_keeps_installation_cpu_and_target_limits(tmp_path, monkeypatch, installed, model, target):
    import application.execution

    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state'); service.start()
    try:
        pid, vid, calls = setup_external(service, 'ask', monkeypatch)
        project = service.store.get_project(pid)
        for version in project['versions']:
            if version['id'] == vid:
                version.update(plc_model=model, target_mode=target)
        service.store.save_project(project)
        monkeypatch.setattr(application.execution, 'read_gx_environment', lambda: {
            'gx_works2_installed': installed, 'gx_works2_running': False, 'project_open': False})
        with pytest.raises(application.execution.ExecutionUnavailableError):
            service.execution_proposal({'action': 'gx_import', 'project_id': pid,
                                       'version_id': vid, 'request_id': 'unverified-target'})
        assert calls == [] and service.proposals.list(pid) == []
    finally:
        service.close()


def test_escalating_mode_does_not_execute_old_pending_actions(tmp_path, monkeypatch):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state'); service.start()
    try:
        pid, vid, calls = setup_external(service, 'ask', monkeypatch)
        command = {'action':'gx_import','project_id':pid,'version_id':vid,'request_id':'pending'}
        pending = service.execution_proposal(command)
        service.update_approval_settings(mode='full', expected_revision=0, confirm_full_access=True)
        assert calls == [] and service.proposals.get(pending['id'])['status'] == 'pending'
        assert service.execution_proposal(command)['status'] == 'pending' and calls == []
    finally:
        service.close()


def test_downgrade_cancels_queued_automatic_execution_before_effect(tmp_path, monkeypatch):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state'); service.start()
    entered, release = threading.Event(), threading.Event()
    try:
        pid, vid, calls = setup_external(service, 'full', monkeypatch)
        original = service.jobs.submit
        def gated(kind, snapshot, worker, **kwargs):
            def wait(ctx):
                entered.set(); assert release.wait(10); return worker(ctx)
            return original(kind, snapshot, wait, **kwargs)
        monkeypatch.setattr(service.jobs, 'submit', gated)
        result = service.execution_proposal({'action':'gx_import','project_id':pid,'version_id':vid,'request_id':'downgrade'})
        assert entered.wait(5)
        service.update_approval_settings(mode='ask', expected_revision=1)
        release.set()
        service.jobs._futures[result['execution_job_id']].result(timeout=10)
        assert service.jobs.get(result['execution_job_id'])['status'] == 'failed'
        assert service.proposals.get(result['id'])['status'] == 'pending' and calls == []
    finally:
        release.set(); service.close()


def test_cli_has_no_interactive_permission_mode_prompt():
    text = Path('scripts/start_web.ps1').read_text(encoding='utf-8-sig')
    assert '选择打开方式' not in text and 'Read-Host "选择' not in text
    assert '网页设置' in text
    assert 'if ($ReadOnly)' in text  # Explicit recovery invocation remains supported.


def test_public_approval_audit_is_an_enum_projection_not_untrusted_metadata():
    from application.proposals import ProposalService
    result = ProposalService._public({"summary": {"approval": {"source": "policy", "mode": "full", "credentials": "never-public"}}})
    assert result["summary"]["approval"] == {"source": "policy", "mode": "full"}
    assert "never-public" not in str(result)
    assert "approval" not in ProposalService._public({"summary": {"approval": {"source": "untrusted", "mode": "full"}}})["summary"]
