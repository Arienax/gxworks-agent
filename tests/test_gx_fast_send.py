"""Shared Web/MCP sends, using isolated files and GX/dispatch spies only."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from gxworks2.csv_manager import CSVManager
from gxworks2.import_service import ImportService
from gxworks2.models import ImportErrorCode
from test_gxworks2_import import FakeAutomation, FakeFinder, _session, _write_comments, _write_program
from test_approval_modes import setup_external
from test_web_api import ORIGIN, _app, _login


@pytest.fixture
def fast_send(tmp_path, monkeypatch):
    program, comments = tmp_path / "program.csv", tmp_path / "comments.csv"
    _write_program(program)
    _write_comments(comments, [["X000", "New input"]])
    automation = FakeAutomation(program)

    def forbidden(*args, **kwargs):
        pytest.fail("A CSV import must never export or read the old baseline")

    monkeypatch.setattr(automation, "export_current_program", forbidden)
    monkeypatch.setattr(automation, "export_current_comments", forbidden)
    def save(session):
        automation.events.append("save")
        return {"success": True}
    monkeypatch.setattr(automation, "save_project", save, raising=False)
    service = ImportService(FakeFinder(_session()), automation, CSVManager(), tmp_path / "backups")
    monkeypatch.setattr(service.baseline_store, "load", forbidden)
    def send(**kwargs):
        return service.import_current_program(program, comment_csv_path=comments,
            pre_import_policy="manual_backup", synchronize_comments=True, save_project=True, **kwargs)
    return service, automation, program, comments, send


@pytest.mark.parametrize("baseline_state", ["missing", "corrupt", "changed_and_other_project"])
def test_fast_send_skips_all_exports_and_baseline_comparison(fast_send, baseline_state):
    service, automation, program, comments, send = fast_send
    identity = service.baseline_store.project_identity(_session(), project_name="FixtureProject")
    path = service.baseline_store.path_for(identity)
    if baseline_state == "corrupt":
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
    elif baseline_state == "changed_and_other_project":
        service.baseline_store.save(identity, program_semantic_sha256="a" * 64,
            program_file_sha256="b" * 64, comments_semantic_sha256="c" * 64,
            import_context={"project_id": "another-project"})
    stages = []
    result = send(progress=lambda stage, message: stages.append(stage),
                  import_context={"project_id": "selected-project", "version_id": "v1"})
    assert result.success and result.error_code is None
    assert automation.events == ["import_program", "import_comments", "save"]
    assert Path(result.backup_path).suffix == ".gxw"
    assert result.details["comment_backup_path"] == ""
    assert result.details["backup_performed"] is True
    assert result.details["version_protection"]["baseline_checked"] is False
    assert result.details["version_protection"]["baseline_found"] is None
    assert stages == ["validate_csv", "validate_comments", "check_project", "backup", "prepare_import",
                      "import", "import_comments", "record_baseline", "save_project", "verify"]
    assert set(result.details["timings_ms"]) == set(stages) | {"total"}
    assert all(value >= 0 for value in result.details["timings_ms"].values())
    assert not list(service.backup_root.rglob("*.csv"))
    import json
    baseline = json.loads(path.read_text(encoding="utf-8"))
    assert baseline["gx_program_semantic_sha256"] == service.csv_manager.program_semantic_sha256(program)
    assert baseline["gx_comment_semantic_sha256"] == service.csv_manager.comments_semantic_sha256(comments)
    assert baseline["import_context"]["project_id"] == "selected-project"


@pytest.mark.parametrize("failure", ["program_csv", "comment_csv", "not_running", "not_open", "not_editable", "no_driver"])
def test_fast_send_preconditions_still_stop_before_import(fast_send, monkeypatch, failure):
    service, automation, program, comments, send = fast_send
    if failure in {"program_csv", "comment_csv"}:
        (program if failure == "program_csv" else comments).write_text("invalid", encoding="utf-8")
    elif failure == "not_running":
        service.finder.session = None
    else:
        state = automation.inspect_project(_session())
        state[{"not_open": "project_open", "not_editable": "program_ready", "no_driver": "automation_available"}[failure]] = False
        monkeypatch.setattr(automation, "inspect_project", lambda session: state)
    result = send()
    assert not result.success
    assert result.error_code is not None
    assert not automation.events
    assert not service.baseline_store.state_root.exists()


@pytest.mark.parametrize("part", ["program", "comments"])
@pytest.mark.parametrize("throws", [False, True])
def test_fast_send_failures_do_not_record_baseline_save_or_retry(fast_send, monkeypatch, part, throws):
    service, automation, _, _, send = fast_send
    def fail(session, path):
        automation.events.append("failed_" + part)
        if throws:
            raise RuntimeError("GX rejected the import")
        return {"success": False, "message": "GX rejected the import"}
    monkeypatch.setattr(automation, "import_program_csv" if part == "program" else "import_comments_csv", fail)
    result = send()
    assert not result.success
    assert automation.events == (["import_program"] if part == "comments" else []) + ["failed_" + part]
    if part == "comments":
        assert "程序已导入" in result.message
        assert result.details["comment_backup_path"] == ""
    assert Path(result.backup_path).is_file()
    assert not service.baseline_store.state_root.exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_baseline_failure_is_warning_and_does_not_hide_save_failure(fast_send, monkeypatch, save_fails):
    service, automation, _, _, send = fast_send
    def fail(*args, **kwargs):
        raise OSError("baseline disk unavailable")
    monkeypatch.setattr(service.baseline_store, "save", fail)
    if save_fails:
        monkeypatch.setattr(automation, "save_project", lambda session: {"success": False, "message": "请手动保存工程。"})
    result = send()
    assert result.success and result.stage == "complete_with_warning"
    assert result.details["version_protection"]["status"] == "baseline_write_failed"
    assert result.error_code == (ImportErrorCode.PROJECT_SAVE_REQUIRED if save_fails else None)
    assert "未能记录同步基线" in result.message
    assert ("请手动保存" in result.message) is save_fails


def test_staging_is_independent_preserves_source_and_is_cleaned(fast_send, monkeypatch):
    service, automation, program, _, send = fast_send
    _write_program(program, statement="超长注释" * 100)
    original = program.read_bytes()
    observed = []
    def inspect_import(session, path):
        observed.append(Path(path))
        assert path != program and Path(path).is_file()
        assert service.csv_manager.validate(path).valid
        return {"success": True}
    monkeypatch.setattr(automation, "import_program_csv", inspect_import)
    assert send().success
    assert program.read_bytes() == original
    assert not observed[0].exists() and not observed[0].parent.exists()


@pytest.mark.parametrize("guard", [{"verify_roundtrip": True}, {"rollback_expected_current_sha256": "a" * 64},
                                   {"expected_current_comment_sha256": "b" * 64}])
def test_fast_send_cannot_silently_drop_roundtrip_or_rollback_guards(fast_send, guard):
    _, automation, _, _, send = fast_send
    result = send(**guard)
    assert result.error_code == ImportErrorCode.INVALID_REQUEST and automation.events == []


def test_public_import_api_forwards_explicit_policy(fast_send, monkeypatch):
    import gxworks2.api as api
    service, _, program, comments, _ = fast_send
    monkeypatch.setattr(api, "_service", service)
    result = api.import_current_program(program, comment_csv_path=comments, pre_import_policy="manual_backup")
    assert result.success and result.details["pre_import_policy"] == "project_file"


@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
def test_http_confirmation_is_bound_and_dispatches_once(tmp_path, monkeypatch, mode):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state")
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        pid, vid, calls = setup_external(service, mode, monkeypatch)
        headers = _login(client)
        command = {"action": "gx_import", "project_id": pid, "version_id": vid,
                   "request_id": "confirmed-send", "manual_backup_acknowledged": True}
        assert client.post("/api/proposals", json=command).status_code == 403
        assert client.post("/api/proposals", json=command, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        response = client.post("/api/proposals", json=command, headers=headers)
        assert response.status_code == 201, response.text
        proposal = response.json()
        assert service.proposals.read_private(proposal["id"])["manual_backup_acknowledged"] is True
        if mode == "full":
            job_id = proposal["execution_job_id"]
        else:
            assert calls == []  # Acknowledging backup does not itself bypass approval.
            approved = client.post(f'/api/proposals/{proposal["id"]}/decision', json={"decision": "accept"}, headers=headers)
            job_id = approved.json()["job"]["id"]
        service.jobs._futures[job_id].result(timeout=10)
        assert len(calls) == 1 and calls[0][0][1]["manual_backup_acknowledged"] is True
        assert client.post("/api/proposals", json=command, headers=headers).json()["id"] == proposal["id"]
        replay = client.post(f'/api/proposals/{proposal["id"]}/decision', json={"decision": "accept"}, headers=headers)
        service.jobs._futures[replay.json()["job"]["id"]].result(timeout=10)
        assert len(calls) == 1
        # Reusing this request ID without the acknowledgement changes its immutable payload.
        command["manual_backup_acknowledged"] = False
        assert client.post("/api/proposals", json=command, headers=headers).status_code == 409


@pytest.mark.parametrize("case", ["simulation", "debug", "fbd", "no_csv", "not_bool"])
def test_acknowledgement_cannot_enable_other_operations(tmp_path, monkeypatch, case):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state")
    service.start()
    try:
        pid, vid, calls = setup_external(service, "full", monkeypatch)
        if case == "fbd":
            monkeypatch.setattr(service.projects, "raw_version", lambda *args: {"target_mode": "fbd"})
        elif case == "no_csv":
            monkeypatch.setattr(service.projects, "raw_version", lambda *args: {"target_mode": "st", "artifacts": {"st": "program.st"}})
        with pytest.raises(ValueError, match="only supported"):
            service.execution_proposal({"action": case if case in {"simulation", "debug"} else "gx_import",
                "project_id": pid, "version_id": vid, "request_id": "wrong-action",
                "manual_backup_acknowledged": "true" if case == "not_bool" else True})
        assert not calls and not service.proposals.list(pid)
    finally:
        service.close()


def test_acknowledged_proposal_still_rejects_changed_version(tmp_path, monkeypatch):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state")
    service.start()
    try:
        pid, vid, calls = setup_external(service, "ask", monkeypatch)
        proposal = service.execution_proposal({"action": "gx_import", "project_id": pid, "version_id": vid,
            "request_id": "stale-send", "manual_backup_acknowledged": True})
        # Return a changed snapshot without writing a fake version into the store.
        original = service.proposals._project
        monkeypatch.setattr(service.proposals, "_project", lambda project_id: {**original(project_id), "active_version_id": "changed"})
        job = service.decide(proposal["id"], "accept")["job"]
        service.jobs._futures[job["id"]].result(timeout=10)
        assert not calls and service.proposals.get(proposal["id"])["status"] == "conflict"
    finally:
        service.close()


@pytest.mark.parametrize("acknowledged", [None, False, True])
def test_coordinator_uses_file_backup_policy_for_all_csv_sends(tmp_path, acknowledged, fast_send):
    from test_execution_coordinator import FakeStore, coordinator
    importer_service, automation, program, comments, _ = fast_send
    store = FakeStore(tmp_path / "workspace")
    directory = store.version_dir("p1", "v1")
    (directory / "program.csv").write_bytes(program.read_bytes())
    (directory / "comments.csv").write_bytes(comments.read_bytes())
    calls = []
    def importer(*args, **kwargs):
        calls.append(kwargs)
        return importer_service.import_current_program(*args, **kwargs).to_dict()
    payload = {"project_id": "p1", "version_id": "v1"}
    if acknowledged is not None:
        payload["manual_backup_acknowledged"] = acknowledged
    with coordinator(store, tmp_path, importer, []) as service:
        result = service.submit_approved("gx_import", payload, approval_id="approved").result(5)
    assert result["status"] == "imported" and not result["passed"]
    assert calls[0]["pre_import_policy"] == "project_file"
    assert calls[0]["synchronize_comments"] and calls[0]["save_project"]
    assert calls[0]["verify_roundtrip"] is False
    assert len(calls) == 1 and automation.events == ["import_program", "import_comments", "save"]
    assert result["gx_import_summary"]["backup_performed"] is True


def test_web_preserves_only_finite_known_gx_timing_fields():
    from application.workspace import public_payload
    raw = {"gx_import_summary": {"pre_import_policy": "project_file", "backup_performed": False,
        "csv_path": "C:/private/project.csv", "timings_ms": {
            "total": 4100.5, "import": 1000, "import_comments": 2900,
            "save_project": float("nan"), "backup": -1, "verify": True,
            "private": "C:/private", "_candidate_ir": {"secret": True}}},
        "import": {"private_payload": "hidden"}}
    expected = {"gx_import_summary": {"pre_import_policy": "project_file", "backup_performed": False,
                "timings_ms": {"total": 4100.5, "import": 1000, "import_comments": 2900}}}
    assert public_payload(raw) == expected
    assert public_payload(public_payload(raw)) == expected
