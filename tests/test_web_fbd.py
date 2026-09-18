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
from gxw.object_model import default_baseline, export_object_model, read_project
from tests.test_gxw_object_model import two_timers
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
    assert service.projects.program(p["id"], version_id) == export_object_model(program, declarations)
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
    import application.model_workflows as api
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


def test_approved_gx_import_uses_own_copy_on_com_queue(service, tmp_path):
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
