"""FBD approval, persistence and desktop boundaries, using isolated workspaces."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from application.execution import GXExecutionCoordinator
from application.fbd import prepare_candidate
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from gxw.object_model import default_baseline, read_project, read_project_context
from tests.test_gxw_object_model import two_timers, source_binding_baseline, project_rows_variant
from tests.test_execution_coordinator import FakeCOM
from tests.test_web_api import _app, _login, ORIGIN
from tests.test_workbench_service import _program


@pytest.fixture
def service(tmp_path):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", model_factory=lambda: (None, {}))
    service.start()
    yield service
    service.close()


def generate(service, request_id="generate"):
    p = service.create_project(name="FBD", target_mode="fbd")
    command = {"operation": "generate", "project_id": p["id"], "request_id": request_id, "model": two_timers()}
    return p, command, service.fbd.propose(command)


def accept(service, proposal):
    result = service.decide(proposal["id"], "accept")["proposal"]["result"]
    return result["version_id"]


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
    assert [row.name for row in declarations["1.Labels.lh"].rows] == ["TIMER_A", "TIMER_B"]
    with pytest.raises(ConflictError):
        service.fbd.propose({**command, "operation": "edit"})


def test_edits_bind_base_and_synchronize_labels_without_mutating_prior_version(service):
    p, _, proposal = generate(service)
    base = accept(service, proposal)
    original = service.projects.artifact(p["id"], base, "gxw").read_bytes()
    model = service.projects.program(p["id"], base)
    model["nodes"][0]["symbol"] = "TON_C"
    model["declaration_edits"] = {"1.Labels.lh": {"renames": {"TIMER_A": "TON_C"}}}
    edit = service.fbd.propose({"operation": "edit", "project_id": p["id"], "version_id": base,
        "request_id": "edit", "model": model})
    assert service.proposal_preview(edit["id"])["diff"]["declarations_changed"]
    newer = accept(service, edit)
    assert service.projects.artifact(p["id"], base, "gxw").read_bytes() == original
    assert service.projects.program(p["id"], newer)["labels"]["1.Labels.lh"][0]["name"] == "TON_C"
    assert service.projects.version(p["id"], newer)["parent_version_id"] == base


def test_changed_artifact_and_false_preview_are_rejected_before_acceptance(service):
    p = service.create_project(name="Tamper fixture", target_mode="fbd")
    pending = prepare_candidate(service.state_dir / "pending-tamper", model=two_timers())
    proposal = service.proposals.create("accept_local", p["id"], pending)
    payload = service.proposals.read_private(proposal["id"])
    from pathlib import Path
    (Path(payload["staging_dir"]) / "program.gxw").write_bytes(b"changed")
    with pytest.raises(ConflictError):
        accept(service, proposal)
    assert not service.projects.raw_project(p["id"])["versions"]
    data = prepare_candidate(service.state_dir / "tampered", model=two_timers())
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
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({"summary": "两个定时器串接", "model": two_timers()})))
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


@pytest.mark.parametrize("invalid_connection", [False, True])
def test_english_fbd_summary_is_allowed_but_dangling_connection_is_rejected(service, invalid_connection):
    from model_runtime.provider import ReasoningDelta, TextDelta
    model = two_timers()
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
            response = client.post('/api/agent/tools/call', headers={'Authorization': 'Bearer ' + AGENT},
                json={**selected, 'name': name, 'call_id': name, 'arguments': arguments})
            assert response.status_code == 200, response.text
            assert not response.json()['is_error'], response.text
            return response.json()

        context = call('get_generation_context', {})['data']['data']
        assert context['current_model'] == editor['model']
        assert context['output_contract']['schema_version'] == 2
        assert context['catalog']['nodes'] == catalog['nodes']
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
        assert candidate['data']['data']['verification']['gx_compile'] == 'not_run'
        assert client.get('/api/projects/' + project['id']).json()['version_count'] == 1


def test_native_envelope_preview_and_save_share_source_plan_and_keep_v2(service, monkeypatch):
    import application.execution as execution
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(service.state_dir / 'test-native.lock'))
    from gxw.container_writer import replace_project_stream
    from gxw.native_write import native_source_plan, verify_native_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    # Only the application boundary is doubled; this envelope is not native
    # acceptance evidence. Live native save/cold-compile is a separate check.
    raw, _ = replace_project_stream(source_binding_baseline(), 'Project.gd2', b'0' * 64)
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
    saved = service.fbd.propose({'operation': 'edit', 'project_id': project['id'],
        'version_id': base, 'request_id': 'native-edit', 'model': draft})
    current = accept(service, saved)
    assert calls[0] == calls[1]
    assert service.projects.program(project['id'], current) == preview['model']
    assert preview['model']['schema_version'] == 2
    assert preview['model']['program'] == '1.Program.pou'
    assert '1.Labels.lh' in preview['model']['labels']
    assert preview['gx_compile'] == service.projects.version(project['id'], current)['validation']['gx_compile'] == 'not_run'
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
    updated = next(n for n in preview['model']['nodes'] if n['symbol'] == 'NATIVE_UPDATED')
    assert [p['name'] for p in updated['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']


def test_native_save_failure_or_busy_resource_leaves_base_version_unchanged(service, monkeypatch):
    import application.execution as execution
    from application.execution import DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: DesktopResourceLock(service.state_dir / 'test-native.lock'))
    from application.fbd import FBDValidationError
    from gxw.container_writer import replace_project_stream
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError
    raw, _ = replace_project_stream(source_binding_baseline(), 'Project.gd2', b'0' * 64)
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
        service.fbd.preview(project['id'], model, base)
    assert not calls
    with pytest.raises(FBDValidationError, match='native-source-readback-mismatch'):
        service.fbd.propose({'operation': 'edit', 'project_id': project['id'], 'version_id': base,
                            'request_id': 'failed-native-edit', 'model': model})
    assert len(service.projects.raw_project(project['id'])['versions']) == 1
    assert service.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw
