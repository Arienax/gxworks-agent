"""Completed is not synonymous with an acceptable candidate; real HTTP regressions."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from application.workspace import ConflictError
from model_runtime.provider import TextDelta
from test_web_api import ORIGIN, _Provider, _app, _complete, _login, offline, _ladder, offline_runtime_profile


def prepared(tmp_path, blocked=False):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state',
                              model_factory=lambda: (_Provider(), {'model': 'offline'}))
    client = TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN)
    return service, client


def generate(client, service, *, blocked=False):
    headers = _login(client)
    project = client.post('/api/projects', json={'name': 'Delivery fixture'}, headers=headers).json()['id']
    # Frozen fixtures represent already-confirmed projects, including legacy specs.
    spec = {'summary': 'X0 controls Y0', 'io_table': [], 'parameters': []}
    if blocked:
        spec['selected_approach'] = {'name': 'MOV approach', 'generation_contract': {
            'required_opcodes': ['MOV'], 'enforce': True}}
    service.store.set_confirmed_spec(project, spec)
    job_id, output = _complete(client, service, client.post('/api/jobs', headers=headers,
        json={'project_id': project, 'kind': 'generation', 'request_id': 'delivery',
              'text': 'X0 controls Y0', 'response_language': 'en'}))
    return project, job_id, output, headers


def test_confirmed_approach_is_context_not_a_second_generation_gate(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        pid, jid, output, headers = generate(client, service, blocked=True)
        assert output['status'] == 'saved' and output.get('proposal_id') and output.get('version_id')
        assert service.jobs.get(jid)['result']['status'] == 'saved'
        result = client.get(f'/api/jobs/{jid}/preview?theme=light')
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['read_only'] and '<svg' in body['svg'] and body['program']['networks']
        assert 'contract_mismatch' not in body
        assert service.projects.project(pid)['version_count'] == 1
        assert service.proposals.get(output['proposal_id'])['status'] == 'accepted'


def test_saved_fast_path_preview_refuses_tampered_ir(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        pid, _, output, _ = generate(client, service, blocked=True)
        vid = output['version_id']
        version = service.store.get_version(pid, vid)
        path = service.store.version_dir(pid, vid) / version['artifacts']['ir']
        program = json.loads(path.read_text(encoding='utf-8'))
        program['program_name'] = 'CHANGED'
        path.write_text(json.dumps(program), encoding='utf-8')
        assert client.get(f'/api/projects/{pid}/versions/{vid}/preview').status_code in (404, 409)


def test_ready_job_preview_uses_its_exact_automatically_saved_version(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        pid, jid, output, headers = generate(client, service)
        preview = client.get(f'/api/jobs/{jid}/preview')
        assert preview.status_code == 200, preview.text
        assert preview.json()['proposal_id'] == output['proposal_id']
        assert preview.json()['ladder']['rungs'] == _ladder()['rungs']
        assert service.projects.project(pid)['version_count'] == 1
        response = client.post(f'/api/proposals/{output["proposal_id"]}/decision', json={'decision': 'accept'}, headers=headers)
        assert response.status_code == 200 and response.json()['proposal']['status'] == 'accepted'
        project = client.get(f'/api/projects/{pid}').json()
        assert project['version_count'] == 1
        vid = project['active_version_id']
        svg = client.get(f'/api/projects/{pid}/versions/{vid}/artifacts/svg')
        assert svg.status_code == 200 and '<svg' in svg.text


def test_preview_without_output_is_an_explicit_error_not_an_empty_svg(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        _, jid, _, _ = generate(client, service)
        (service.state_dir / 'outputs' / (jid + '.json')).unlink()
        assert client.get(f'/api/jobs/{jid}/preview').status_code == 404
        assert client.get('/api/jobs/not-a-job/preview').status_code == 404


def test_refresh_rebuilds_from_frozen_ir_not_old_svg_cache(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        pid, jid, output, _ = generate(client, service)
        old_cache = service.state_dir / 'previews' / output['proposal_id']
        old_cache.mkdir(parents=True, exist_ok=True)
        (old_cache / 'ladder.svg').write_text('BROKEN OLD CACHE', encoding='utf-8')
        before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        for _ in range(3):
            result = client.get(f'/api/proposals/{output["proposal_id"]}/preview?theme=light')
            assert result.status_code == 200, result.text
            assert '<svg' in result.json()['svg'] and 'BROKEN OLD CACHE' not in result.text
            assert result.headers['cache-control'] == 'no-store'
        assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert service.projects.project(pid)['version_count'] == 1
        assert service.proposals.get(output['proposal_id'])['status'] == 'accepted'


def test_manual_version_redraw_recovers_missing_svg_without_writes(offline, tmp_path):
    service, client = prepared(tmp_path)
    with client:
        pid, jid, output, headers = generate(client, service)
        accepted = client.post(f'/api/proposals/{output["proposal_id"]}/decision',
                              json={'decision': 'accept'}, headers=headers)
        assert accepted.status_code == 200
        vid = service.projects.project(pid)['active_version_id']
        service.projects.artifact(pid, vid, 'svg').unlink()
        before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        for theme in ('light', 'dark'):
            response = client.get(f'/api/projects/{pid}/versions/{vid}/preview?theme={theme}')
            assert response.status_code == 200, response.text
            assert response.json()['read_only'] and response.json()['version_id'] == vid
            assert '<svg' in response.json()['svg']
            assert response.json()['ladder']['rungs'] == _ladder()['rungs']
        assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert service.projects.project(pid)['version_count'] == 1
        assert client.get(f'/api/projects/{pid}/versions/{vid}/preview?theme=bad').status_code == 422
        raw_ir = service.projects.artifact(pid, vid, 'ir')
        document = json.loads(raw_ir.read_text(encoding='utf-8'))
        document['program_name'] = 'TAMPERED'
        raw_ir.write_text(json.dumps(document), encoding='utf-8')
        assert client.get(f'/api/projects/{pid}/versions/{vid}/preview').status_code in (400, 409)


def test_version_redraw_in_readonly_session_does_not_migrate(offline, tmp_path):
    from test_web_api import _legacy_workspace
    store, pid, vid, _ = _legacy_workspace(tmp_path / 'workspace')
    service = WorkbenchService(store.base_dir, tmp_path / 'state', read_only=True,
        model_factory=lambda: pytest.fail('Redraw may not request a model'))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with TestClient(_app(store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        assert client.get(f'/api/projects/{pid}/versions/{vid}/preview').status_code == 401
        _login(client)
        result = client.get(f'/api/projects/{pid}/versions/{vid}/preview')
        assert result.status_code == 200, result.text
        assert '<svg' in result.json()['svg']
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


# Repaired candidates must immediately re-enter the normal preview/artifact delivery path.
class BrokenCompactProvider:
    def __init__(self):
        self.requests = []
        self.profile = offline_runtime_profile()

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("missing-quote repair must be deterministic and model-free")
        # One complete compact rung except for the missing quote after D106.
        yield TextDelta('{"r":[{"b":[{"i":["NO X0","> D220 D106],"o":["COIL Y0"]}]}]}')


def test_preserved_invalid_candidate_can_be_repaired_and_immediately_rendered(offline, tmp_path):
    provider = BrokenCompactProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "repair-render"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {
            "summary": "X0 controls Y0 after the tracked position passes D106",
            "io_table": [],
            "parameters": [],
        })

        created = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "broken-compact",
            "text": "generate confirmed ladder",
            "response_language": "zh-CN",
        })
        assert created.status_code == 202, created.text
        bad_job = created.json()["id"]
        service.jobs._futures[bad_job].result(timeout=15)

        preserved = client.get(f"/api/jobs/{bad_job}").json()
        assert preserved["status"] == "completed", preserved
        assert preserved["result"]["status"] == "saved_invalid"
        bad_version = preserved["result"]["version_id"]
        bad_preview = client.get(f"/api/projects/{project}/versions/{bad_version}/preview")
        assert bad_preview.status_code == 200, bad_preview.text
        assert "<svg" in bad_preview.json()["svg"]

        response = client.post(
            f"/api/jobs/{bad_job}/repair",
            headers=headers,
            json={"request_id": "repair-broken-compact"},
        )
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)

        repaired = client.get(f"/api/jobs/{repair_job}").json()
        assert repaired["status"] == "completed", repaired
        assert repaired["result"]["status"] == "saved"
        repaired_version = repaired["result"]["version_id"]
        assert repaired_version != bad_version
        assert len(provider.requests) == 1, "deterministic format repair must not call model"

        job_preview = client.get(f"/api/jobs/{repair_job}/preview")
        assert job_preview.status_code == 200, job_preview.text
        assert "<svg" in job_preview.json()["svg"]

        version = client.get(f"/api/projects/{project}/versions/{repaired_version}").json()
        artifacts = {item["id"]: item for item in version["artifacts"]}
        for artifact_id in ("svg", "program_csv", "comment_csv"):
            assert artifacts[artifact_id]["available"] is True
            data = client.get(
                f"/api/projects/{project}/versions/{repaired_version}/artifacts/{artifact_id}"
            )
            assert data.status_code == 200 and data.content
