"""Candidate reviews compare real bound versions before any local acceptance."""
import copy
from concurrent.futures import Future
import json

import pytest

import application.model_api as api
from application.workbench import WorkbenchService
from model_runtime.provider import TextDelta
from plc.core import PLCCore
from plc.ir import build_plc_ir
from storage.session import SessionStore


def _ladder(input_address="X0", comment="Output", rung_ids=(1,)):
    return {"device_comments": {input_address: "Input", "Y0": comment}, "rungs": [
        {"rung_id": rung_id, "header_element": None, "shared_inputs": [], "branches": [
            {"branch_id": 1, "y_offset_level": 0,
             "inputs": [{"type": "NO", "address": input_address, "label": ""}],
             "outputs": [{"type": "COIL", "address": "Y0" if len(rung_ids) == 1 else f"M{rung_id}", "label": ""}]}]}
        for rung_id in rung_ids]}


def _program(**kwargs):
    return build_plc_ir(_ladder(**kwargs), plc_model="FX3U", program_name="MAIN", revision=1)


def _save(store, project_id, program=None, st=None):
    version_id, directory = store.prepare_version(project_id)
    if st is not None:
        (directory / "program.st").write_text(st, encoding="utf-8")
        metadata = {"target_mode": "st", "artifacts": {"st": "program.st"}}
    else:
        metadata = store._ir_metadata(program)
        metadata.update(target_mode="ladder", artifacts=PLCCore().compile_project(program, directory)["artifacts"])
    return store.complete_version(project_id, version_id, metadata)["id"]


def test_core_initial_creation_has_old_null_and_full_new_network_without_mutating_input():
    program = _program()
    original = copy.deepcopy(program)
    diff = PLCCore().diff_programs(None, program)
    assert diff["added"] == ["N0001"]
    assert diff["before_network_count"] == 0
    assert diff["changes"][0]["before"] is None
    assert diff["changes"][0]["after"] == program["networks"][0]
    assert diff["has_changes"] is True
    diff["changes"][0]["after"]["reads"].append("X7")
    assert program == original


def test_core_identical_ir_has_no_changes_or_invented_success_status():
    program = _program()
    diff = PLCCore().diff_programs(program, copy.deepcopy(program))
    assert diff["has_changes"] is False
    assert diff["changes"] == diff["device_comment_changes"] == []
    assert diff["added"] == diff["deleted"] == diff["modified"] == []
    assert diff["property_changes"] == {}
    assert "passed" not in diff


def test_core_network_edit_exposes_both_old_and_new_instructions():
    before, after = _program(), _program(input_address="X1")
    diff = PLCCore().diff_programs(before, after)
    assert diff["modified"] == ["N0001"]
    change = diff["changes"][0]
    assert change["before"]["reads"] == ["X0"]
    assert change["after"]["reads"] == ["X1"]
    assert change["before"]["instructions"] != change["after"]["instructions"]


def test_core_add_delete_and_order_changes_are_reviewable():
    before, after = _program(rung_ids=(1, 2)), _program(rung_ids=(2, 3))
    diff = PLCCore().diff_programs(before, after)
    assert diff["added"] == ["N0003"]
    assert diff["deleted"] == ["N0001"]
    changes = {c["network"]: c for c in diff["changes"]}
    assert changes["N0001"]["before"] and changes["N0001"]["after"] is None
    assert changes["N0003"]["before"] is None and changes["N0003"]["after"]
    assert diff["before_network_order"] == ["N0001", "N0002"]
    assert diff["after_network_order"] == ["N0002", "N0003"]
    assert diff["network_order_changed"] is True


def test_core_comment_only_change_keeps_exact_old_and_new_comment():
    diff = PLCCore().diff_programs(_program(comment="Old output"), _program(comment="New output"))
    assert diff["device_comments_changed"] is True
    assert diff["device_comment_changes"] == [{"address": "Y0", "before": "Old output", "after": "New output"}]
    assert diff["has_changes"] is True


class _Provider:
    def __init__(self, payload):
        self.payload = payload

    def stream(self, request):
        yield TextDelta(json.dumps(self.payload, ensure_ascii=False))


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "_build_model_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "load_full_config", lambda: {})
    monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("Real provider must not be opened"))


@pytest.mark.parametrize("with_base", [False, True])
@pytest.mark.parametrize("mode", ["ladder", "st"])
def test_generation_freezes_real_diff_for_first_and_revised_candidates(tmp_path, offline, mode, with_base):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("Diff fixture", target_mode=mode)["id"]
    base_id = _save(store, project_id, program=_program(), st="Y0 := X0;" if mode == "st" else None) if with_base else None
    payload = _ladder(input_address="X1") if mode == "ladder" else {"st_code": "Y0 := X1;"}
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (_Provider(payload), {"model": "offline"}))
    service.start()
    try:
        job = service.submit({"kind": "generation", "project_id": project_id, "version_id": base_id,
            "request_id": "generation-review", "text": "Input controls output", "response_language": "en"})
        service.jobs._futures[job["id"]].result(timeout=15)
        completed = service.jobs.get(job["id"])
        assert completed["status"] == "completed", completed
        proposal_id = completed["result"]["proposal_id"]
        proposal = service.proposals.get(proposal_id)
        assert proposal["status"] == "accepted" and proposal["base_version_id"] == base_id
        assert completed["result"]["version_id"] == proposal["result"]["version_id"]
        assert "diff" in proposal["summary"]
        assert "_preview_diff" not in json.dumps(proposal)
        preview = service.proposal_preview(proposal_id)
        diff = preview["diff"]
        assert diff["kind"] == mode and diff["base_version_id"] == base_id
        assert diff["has_changes"] is True
        assert diff == service.proposals.read_private(proposal_id)["_preview_diff"]
        assert len(store.get_project(project_id)["versions"]) == int(with_base) + 1
        if mode == "ladder":
            assert diff["changes"][0]["after"]["reads"] == ["X1"]
            assert diff["changes"][0]["before"] is None if not with_base else diff["changes"][0]["before"]["reads"] == ["X0"]
        else:
            assert diff["before"] == ("Y0 := X0;" if with_base else "")
            assert diff["after"] == "Y0 := X1;"
            assert "+Y0 := X1;\n" in diff["unified_diff"]
            assert "\\ No newline at end of file" in diff["unified_diff"]
            if with_base:
                assert "-Y0 := X0;\n" in diff["unified_diff"]
        # Simulate selecting another version after the proposal was created.
        _save(store, project_id, program=_program(input_address="X7"), st="Y0 := X7;" if mode == "st" else None)
        assert service.proposal_preview(proposal_id)["diff"] == diff
    finally:
        service.close()


@pytest.mark.parametrize("explicit_version", [False, True])
def test_connected_agent_candidate_uses_bound_base_and_freezes_review(tmp_path, explicit_version):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("Agent diff")["id"]
    base_id = _save(store, project_id, program=_program())
    active_id = _save(store, project_id, program=_program(input_address="X7"))
    service = WorkbenchService(store.base_dir, tmp_path / "state")
    service.start()
    try:
        result = service.agent_call({"project_id": project_id, "version_id": base_id if explicit_version else None,
            "name": "create_program_candidate", "arguments": {"ladder": _ladder(input_address="X1")}, "call_id": "candidate"})
        preview = service.proposal_preview(result["proposal_id"])
        assert preview["diff"]["base_version_id"] == (base_id if explicit_version else active_id)
        assert preview["diff"]["modified"] == ["N0001"]
        assert preview["diff"]["changes"][0]["before"]["reads"] == (["X0"] if explicit_version else ["X7"])
        assert preview["diff"]["changes"][0]["after"]["reads"] == ["X1"]
    finally:
        service.close()


def test_st_identical_candidate_has_empty_text_diff(tmp_path, offline):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("ST unchanged", target_mode="st")["id"]
    base_id = _save(store, project_id, st="Y0 := X0;")
    service = WorkbenchService(store.base_dir, tmp_path / "state",
        model_factory=lambda: (_Provider({"st_code": "Y0 := X0;"}), {"model": "offline"}))
    service.start()
    try:
        job = service.submit({"kind": "generation", "project_id": project_id, "version_id": base_id,
            "request_id": "unchanged", "text": "Input controls output", "response_language": "en"})
        service.jobs._futures[job["id"]].result(timeout=15)
        current = service.jobs.get(job["id"])
        assert current["status"] == "completed", current
        diff = service.proposal_preview(current["result"]["proposal_id"])["diff"]
        assert diff["before"] == diff["after"] == "Y0 := X0;"
        assert diff["has_changes"] is False and diff["unified_diff"] == ""
    finally:
        service.close()


def test_gx_read_candidate_gets_the_same_core_review_with_no_desktop_execution(tmp_path, monkeypatch):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("GX review")["id"]
    base_id = _save(store, project_id, program=_program())
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: pytest.fail("GX read must not open a model"))
    service.start()
    try:
        def fake_read(*args, **kwargs):
            future = Future()
            future.set_result({"status": "read", "_candidate_ir": _program(input_address="X1"), "_confirmed_spec": None})
            return future
        monkeypatch.setattr(service.execution, "submit_read", fake_read)
        job = service.submit({"kind": "gx_read", "project_id": project_id, "version_id": base_id,
            "request_id": "gx-read", "response_language": "en"})
        service.jobs._futures[job["id"]].result(timeout=10)
        current = service.jobs.get(job["id"])
        assert current["status"] == "completed", current
        diff = service.proposal_preview(current["result"]["proposal_id"])["diff"]
        assert diff["modified"] == ["N0001"]
        assert diff["changes"][0]["before"]["reads"] == ["X0"]
        assert len(store.get_project(project_id)["versions"]) == 2
        assert current["result"]["version_id"] == store.get_project(project_id)["active_version_id"]
    finally:
        service.close()


@pytest.mark.parametrize("action", ["gx_import", "simulation"])
def test_execution_preview_uses_its_bound_program_artifacts_and_plan(tmp_path, action, gx_ready):
    from test_workbench_service import _save_test_plan
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("Execution review")["id"]
    base_id = _save(store, project_id, program=_program())
    plan_id = _save_test_plan(store, project_id, base_id)["binding"]["plan_id"] if action == "simulation" else None
    original_svg = (store.version_dir(project_id, base_id) / "ladder.svg").read_text(encoding="utf-8")
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: pytest.fail("Review must not open a model"))
    service.start()
    try:
        proposal = service.execution_proposal({"action": action, "project_id": project_id,
            "version_id": base_id, "plan_id": plan_id, "request_id": "execute-reviewed-version"})
        active_id = _save(store, project_id, program=_program(input_address="X7"))
        assert store.get_project(project_id)["active_version_id"] == active_id != base_id
        preview = service.proposal_preview(proposal["id"])
        assert preview["version_id"] == preview["version"]["id"] == base_id
        assert preview["program"]["networks"][0]["reads"] == ["X0"]
        assert preview["svg"] == original_svg
        assert "X0" in preview["st"]
        assert preview["version"]["capabilities"]["operations"]["gx_import"] is True
        assert preview["plan"]["binding"]["plan_id"] == plan_id if plan_id else preview["plan"] is None
        assert service.proposals.get(proposal["id"])["status"] == "pending"
    finally:
        service.close()


def test_execution_preview_fails_when_its_registered_artifact_is_missing(tmp_path, gx_ready):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("Missing preview artifact")["id"]
    base_id = _save(store, project_id, program=_program())
    service = WorkbenchService(store.base_dir, tmp_path / "state")
    service.start()
    try:
        proposal = service.execution_proposal({"action": "gx_import", "project_id": project_id,
            "version_id": base_id, "request_id": "missing-artifact"})
        (store.version_dir(project_id, base_id) / "ladder.svg").unlink()
        with pytest.raises(KeyError, match="Artifact file not found"):
            service.proposal_preview(proposal["id"])
        assert service.proposals.get(proposal["id"])["status"] == "pending"
    finally:
        service.close()


def test_execution_preview_http_contract_is_bound_to_proposal_not_selected_version(tmp_path, gx_ready):
    pytest.importorskip("fastapi", reason="HTTP review requires requirements-web.txt")
    pytest.importorskip("httpx", reason="HTTP review requires httpx")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("HTTP execution review")["id"]
    base_id = _save(store, project_id, program=_program())
    service = WorkbenchService(store.base_dir, tmp_path / "state",
        model_factory=lambda: pytest.fail("Preview must not create a model"))
    origin = "http://127.0.0.1:8765"
    app = create_app(store.base_dir, state_dir=tmp_path / "state", service=service,
        origin=origin, operator_token="review-operator")
    with TestClient(app, base_url=origin) as client:
        login = client.post("/api/session", json={"token": "review-operator"}, headers={"Origin": origin})
        assert login.status_code == 200
        headers = {"Origin": origin, "X-CSRF-Token": login.json()["csrf"]}
        response = client.post("/api/proposals", headers=headers, json={"action": "gx_import",
            "project_id": project_id, "version_id": base_id, "request_id": "bound-preview"})
        assert response.status_code == 201, response.text
        proposal_id = response.json()["id"]
        active_id = _save(store, project_id, program=_program(input_address="X7"))
        # Reading a different version is independent of the proposal's review.
        selected = client.get(f"/api/projects/{project_id}/versions/{active_id}/program")
        assert selected.status_code == 200 and selected.json()["networks"][0]["reads"] == ["X7"]
        preview_response = client.get(f"/api/proposals/{proposal_id}/preview")
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["version_id"] == preview["version"]["id"] == base_id
        assert preview["program"]["networks"][0]["reads"] == ["X0"]
        assert "<svg" in preview["svg"] and "X0" in preview["st"]
        assert preview["action"] == "gx_import" and preview["plan"] is None
        assert str(store.base_dir) not in preview_response.text
        assert "_candidate_ir" not in preview_response.text
        (store.version_dir(project_id, base_id) / "ladder.svg").unlink()
        assert client.get(f"/api/proposals/{proposal_id}/preview").status_code == 404
        assert client.get(f"/api/proposals/{proposal_id}").json()["status"] == "pending"
