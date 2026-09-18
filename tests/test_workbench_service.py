"""Service boundary checks against temporary engineering projects only."""

import base64
import copy
import json
from pathlib import Path

import pytest

from application.projects import ProjectError, ProjectService
from application.settings import SettingsService
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from plc.core import PLCCore
from plc.ir import build_plc_ir
from storage.session import SessionStore


_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def _bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _program(input_address="X0"):
    return build_plc_ir({"device_comments": {}, "rungs": [{"rung_id": 1, "debug_note": "启动输出",
        "header_element": None, "shared_inputs": [], "branches": [{"branch_id": 1, "y_offset_level": 0,
        "inputs": [{"type": "NO", "address": input_address, "label": ""}],
        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}]}]}]},
        plc_model="FX3U", program_name="MAIN", revision=1)


@pytest.fixture
def saved(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("测试工程")
    program = _program()
    version_id, output = store.prepare_version(project["id"])
    metadata = store._ir_metadata(program)
    metadata.update(target_mode="ladder", plc_model="FX3U", artifacts=PLCCore().compile_project(program, output)["artifacts"])
    version = store.complete_version(project["id"], version_id, metadata)
    return store, project, version, program


@pytest.fixture
def workbench(saved, tmp_path):
    store, _, _, _ = saved
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (None, {"model": "unit-fake"}))
    service.start()
    try:
        yield service
    finally:
        service.close()


def _change_artifact(store, project_id, artifact_id, path):
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"][artifact_id] = str(path)
    store.save_project(project)


def test_legacy_analysis_output_restores_choices_without_writing(workbench, saved):
    store, project, _, _ = saved
    job = workbench.jobs.submit("analysis", {"project_id": project["id"]}, lambda context: {})
    workbench.jobs._futures[job["id"]].result(timeout=5)
    directory = workbench.state_dir / "outputs"
    directory.mkdir(exist_ok=True)
    output = {"analysis": {"missing_info": [{"id": "start_input", "question": "启动接哪个输入？", "options": ["X0", "X2"]}]},
              "spec_draft": {"parameters": [{"id": "start_input", "name": "旧问题措辞", "value": "X3", "source": "user"}]}}
    (directory / (job["id"] + ".json")).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    state_before, workspace_before = _bytes(workbench.state_dir), _bytes(store.base_dir)
    result = workbench.output(job["id"])
    assert result["spec_draft"]["parameters"][0] == {"id": "start_input", "name": "旧问题措辞", "value": "X3", "source": "user", "options": ["X0", "X2"]}
    assert _bytes(workbench.state_dir) == state_before
    assert _bytes(store.base_dir) == workspace_before


def test_spec_save_rejects_duplicate_raw_rows_before_canonicalization(workbench, saved):
    store, project, _, _ = saved
    spec = {"summary": "启停", "plc_model": "FX3U", "io_table": [
        {"address": "X0", "kind": "X", "label": "启动"},
        {"address": "X0", "kind": "X", "label": "停止"},
    ], "parameters": []}
    before = _bytes(store.base_dir)
    result = workbench.set_spec(project["id"], spec, None)
    assert result["valid"] is False
    assert any(issue["code"] == "duplicate_io_address" for issue in result["issues"]["errors"])
    assert _bytes(store.base_dir) == before


def test_saved_address_answers_return_actual_hash_and_keep_contact_choice(workbench, saved):
    from plc.specification.confirmed import build_review_draft
    from plc.ir import canonical_sha256

    store, project, _, _ = saved
    draft = build_review_draft({"summary": "启停", "suggested_io": {"X": {"X0": "启动", "X1": "停止"}, "Y": {"Y0": "输出"}},
        "missing_info": [{"id": "start_input", "question": "启动接哪个输入？"},
                         {"id": "stop_input", "question": "停止接哪个输入？常开还是常闭？"}]})
    draft["parameters"][0].update(value="X2", source="user")
    draft["parameters"][1].update(value="X1，常闭", source="user")
    result = workbench.set_spec(project["id"], draft, None)
    assert result["valid"] is True
    persisted = store.get_project(project["id"])["confirmed_spec"]
    assert result["spec"] == persisted
    assert result["hash"] == canonical_sha256(persisted)
    assert persisted["parameters"][0]["value"] == "X1，常闭"
    repeated = workbench.set_spec(project["id"], persisted, result["hash"])
    assert repeated["valid"] is True
    assert repeated["hash"] == result["hash"]
    assert repeated["spec"] == persisted


def test_project_reads_and_legacy_program_loading_never_write(saved):
    store, project, version, program = saved
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    service = ProjectService(store.base_dir)
    assert service.list_projects()[0]["id"] == project["id"]
    assert service.project(project["id"])["version_count"] == 1
    assert service.version(project["id"], version["id"])["id"] == version["id"]
    assert service.program(project["id"], version["id"])["program_name"] == program["program_name"]
    assert service.diagnostics(project["id"], version["id"])["ok"] is True
    assert _bytes(store.base_dir) == before


def test_read_only_workbench_does_not_create_workspace_state_or_config(tmp_path, monkeypatch):
    missing = tmp_path / "missing-workspace"
    state = tmp_path / "application-state"
    monkeypatch.setattr(SettingsService, "read_config", lambda self: pytest.fail("Reading projects must not load model settings"))
    service = WorkbenchService(missing, state, read_only=True)
    service.start()
    try:
        assert service.projects.list_projects() == []
        assert not missing.exists()
        assert not state.exists()
        with pytest.raises(PermissionError):
            service.create_project(name="forbidden")
    finally:
        service.close()


@pytest.mark.parametrize("bad_id", ["../outside", "..", "C:\\private", "project:stream", "project/name"])
def test_malicious_project_index_is_rejected_without_reading_or_writing(saved, bad_id):
    store, _, _, _ = saved
    store._write_json(store.index_path, {"projects": [bad_id]})
    before = _bytes(store.base_dir)
    with pytest.raises((ProjectError, ValueError)):
        ProjectService(store.base_dir).list_projects()
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("filename", ["../../../../../outside.json", "program.st:private.json"])
def test_manifest_path_escape_and_windows_ads_are_rejected(saved, filename):
    store, project, version, _ = saved
    if ":" in filename:
        # On Windows this creates a harmless ADS in a temporary test directory;
        # elsewhere it is a regular colon-named file, still forbidden by the API.
        path = store.version_dir(project["id"], version["id"]) / filename
        path.write_text('{"secret":"must-not-be-read"}', encoding="utf-8")
    _change_artifact(store, project["id"], "json", filename)
    before = _bytes(store.base_dir)
    with pytest.raises((ProjectError, ValueError)):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")
    assert _bytes(store.base_dir) == before


def test_manifest_absolute_path_outside_version_is_rejected(saved, tmp_path):
    store, project, version, _ = saved
    private = tmp_path / "private.json"
    private.write_text('{"api_key":"must-not-be-read"}', encoding="utf-8")
    _change_artifact(store, project["id"], "json", private)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")


def test_unregistered_artifacts_are_inaccessible_even_if_the_file_exists(saved):
    store, project, version, _ = saved
    (store.version_dir(project["id"], version["id"]) / "private.json").write_text("{}", encoding="utf-8")
    with pytest.raises(KeyError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "private")


def test_artifact_symlink_cannot_escape_the_version_root(saved, tmp_path):
    store, project, version, _ = saved
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret":"outside"}', encoding="utf-8")
    link = store.version_dir(project["id"], version["id"]) / "redirect.json"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("The test process cannot create symbolic links on this Windows installation")
    _change_artifact(store, project["id"], "json", link.name)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")


def test_legacy_metadata_and_reports_do_not_expose_paths_or_private_payloads(saved):
    store, project, version, _ = saved
    secret_path = r"C:\Users\operator\private\backup.gxw"
    changed = store.get_project(project["id"])
    changed["versions"][0]["gx_sync"] = {"status": "imported", "backup_path": secret_path,
        "details": {"destination": secret_path, "_candidate_ir": {"secret": True}, "api_key": "unit-secret"}}
    store.save_project(changed)
    report = store.create_report(project["id"], {"report_type": "inspection", "summary": "工程检查",
        "status": "passed", "workspace_dir": secret_path, "provider_configuration": {"api_key": "unit-secret"},
        "items": [{"message": "请核对", "source": secret_path}], "_candidate_ir": {"hidden": True}})
    service = ProjectService(store.base_dir)
    wire = json.dumps([service.version(project["id"], version["id"]), service.report(project["id"], report["report_id"])], ensure_ascii=False)
    assert "unit-secret" not in wire
    assert "backup.gxw" not in wire
    assert "_candidate_ir" not in wire
    assert "workspace_dir" not in wire


def test_report_embedded_error_paths_are_redacted(saved):
    store, project, _, _ = saved
    report = store.create_report(project["id"], {"report_type": "inspection", "summary": "工程检查",
        "status": "failed", "messages": [r"读取 C:\Users\operator\private\backup.gxw 时失败"]})
    wire = json.dumps(ProjectService(store.base_dir).report(project["id"], report["report_id"]), ensure_ascii=False)
    assert "backup.gxw" not in wire
    assert "读取" in wire


def test_attachment_upload_uses_managed_names_and_returns_frozen_image_values(saved, workbench):
    store, project, _, _ = saved
    uploaded = workbench.upload_attachment(project["id"], "../../drawing.png", base64.b64encode(_PNG).decode())
    assert uploaded["filename"] == "drawing.png"
    assert set(uploaded) == {"attachment_id", "filename", "media_type", "size_bytes"}
    images = workbench._attachments(project["id"], [uploaded["attachment_id"]])
    assert images[0].data == _PNG
    assert images[0].filename == "drawing.png"
    saved_attachment = json.loads((workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")).read_text(encoding="utf-8"))
    stored = store.attachments_dir(project["id"]) / saved_attachment["record"]["stored_name"]
    stored.write_bytes(_PNG + b"changed-after-snapshot")
    assert images[0].data == _PNG


def test_attachment_ids_cannot_cross_projects_or_escape_indexes(saved, workbench):
    store, project, _, _ = saved
    second = store.create_project("another")
    uploaded = workbench.upload_attachment(project["id"], "drawing.png", base64.b64encode(_PNG).decode())
    with pytest.raises(ValueError, match="another project"):
        workbench._attachments(second["id"], [uploaded["attachment_id"]])
    with pytest.raises(ValueError):
        workbench._attachments(project["id"], ["../escape"])
    index_path = workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["record"]["stored_name"] = "../private.png"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError):
        workbench._attachments(project["id"], [uploaded["attachment_id"]])


def test_submitted_job_keeps_attachment_bytes_and_inputs_from_submission(saved, workbench, monkeypatch):
    store, project, version, _ = saved
    uploaded = workbench.upload_attachment(project["id"], "drawing.png", base64.b64encode(_PNG).decode())
    captured = {}
    class CaptureJobs:
        def submit(self, kind, snapshot, worker, request_id):
            captured.update(kind=kind, snapshot=snapshot, worker=worker)
            return {"id": "job_capture"}
    class Context:
        def checkpoint(self):
            pass
    old_jobs = workbench.jobs
    workbench.jobs = CaptureJobs()
    def inspect_job(ctx, snapshot, context, images, provider):
        captured["executed_text"] = snapshot["text"]
        captured["executed_images"] = images
        return {"status": "completed"}
    monkeypatch.setattr(workbench, "_run_job", inspect_job)
    command = {"kind": "analysis", "project_id": project["id"], "version_id": version["id"],
        "request_id": "capture", "text": "submission input", "response_language": "zh-CN",
        "attachment_ids": [uploaded["attachment_id"]]}
    try:
        workbench.submit(command)
        command["text"] = "later mutation"
        index = json.loads((workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")).read_text(encoding="utf-8"))
        (store.attachments_dir(project["id"]) / index["record"]["stored_name"]).write_bytes(_PNG + b"later")
        captured["worker"](Context())
        assert captured["executed_text"] == "submission input"
        assert captured["executed_images"][0].data == _PNG
        assert "api_key" not in json.dumps(captured["snapshot"])
    finally:
        workbench.jobs = old_jobs


def test_saved_simulator_run_is_read_by_run_id_without_migrating_legacy_ir(saved):
    store, project, version, _ = saved
    suite = {"name": "offline fixture", "plc_model": "FX3U", "tests": [{"name": "idle",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 0, "expect": {"Y0": 0}}]}]}
    record = store.save_simulator_run(project["id"], version["id"], suite,
        {"status": "unavailable", "plc_model": "FX3U", "results": [], "counts": {"unavailable": 1}})
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    result = ProjectService(store.base_dir).simulator_run(project["id"], version["id"], record["run_id"])
    assert result["record"]["run_id"] == record["run_id"]
    assert result["result"]["status"] == "unavailable"
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("field,bad_path", [("suite_artifact", "../../outside.json"),
                                           ("trace_artifact", "trace.json:private.json")])
def test_simulator_evidence_manifest_rejects_escape_and_ads_before_loading(saved, field, bad_path):
    store, project, version, _ = saved
    suite = {"name": "offline fixture", "plc_model": "FX3U", "tests": [{"name": "idle",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 0, "expect": {"Y0": 0}}]}]}
    run = store.save_simulator_run(project["id"], version["id"], suite,
        {"status": "unavailable", "plc_model": "FX3U", "results": [], "counts": {"unavailable": 1}})
    changed = store.get_project(project["id"])
    changed["versions"][0]["simulator_runs"][0][field] = bad_path
    store.save_project(changed)
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).simulator_run(project["id"], version["id"], run["run_id"])
    assert _bytes(store.base_dir) == before


def test_snapshot_rejects_valid_ir_changed_in_place_without_metadata_change(saved, workbench):
    store, project, version, program = saved
    snapshot = {"project_id": project["id"], "project": store.get_project(project["id"]),
                "version": copy.deepcopy(version), "program_ir": copy.deepcopy(program)}
    workbench._check_snapshot(snapshot)
    path = store.version_dir(project["id"], version["id"]) / version["artifacts"]["ir"]
    store._write_json(path, _program("X1"))
    assert store.get_version(project["id"], version["id"]) == version
    before = _bytes(store.base_dir)
    with pytest.raises(ConflictError):
        workbench._check_snapshot(snapshot)
    assert _bytes(store.base_dir) == before


@pytest.fixture
def settings_files(tmp_path, monkeypatch):
    import storage.config as config_manager
    import storage.credentials as credential_store
    import shared.paths as resource_paths
    config_path = tmp_path / "user" / "config.json"
    template = tmp_path / "config.default.json"
    config = {"language": "zh-CN", "activeModelProfileId": "fake", "modelProfiles": [{
        "id": "fake", "name": "Fake model", "adapter": "openai_compatible", "baseUrl": "https://example.invalid/v1",
        "model": "unit-model", "credentialTarget": "test-target", "capabilities": {"tools": True}}]}
    template.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(config_manager, "get_config_path", lambda: config_path)
    monkeypatch.setattr(resource_paths, "resource_path", lambda name: template)
    monkeypatch.setattr(credential_store, "read_api_key", lambda target: "sk-unit-secret")
    monkeypatch.setattr(config_manager, "load_full_config", lambda: pytest.fail("Read-only settings cannot migrate configuration"))
    monkeypatch.setattr(config_manager, "save_config", lambda *args: pytest.fail("Reading settings cannot write configuration"))
    monkeypatch.setattr(credential_store, "write_api_key", lambda *args: pytest.fail("Reading settings cannot write credentials"))
    return config_path, template, config


def test_settings_read_does_not_create_or_migrate_config_or_return_credentials(settings_files):
    path, template, _ = settings_files
    before = template.read_bytes()
    result = SettingsService().public_settings()
    assert result["profiles"][0]["configured"] is True
    assert result["profiles"][0]["model"] == "unit-model"
    assert "sk-unit-secret" not in json.dumps(result)
    assert "credentialTarget" not in json.dumps(result)
    assert not path.exists()
    assert not path.parent.exists()
    assert template.read_bytes() == before


def test_legacy_inline_credentials_and_unknown_capabilities_are_not_public(settings_files):
    path, _, config = settings_files
    profile = config["modelProfiles"][0]
    profile["api_key"] = "legacy-inline-secret"
    profile["requestOverrides"] = {"headers": {"Authorization": "Bearer legacy-inline-secret"}}
    profile["capabilities"]["api_key"] = "nested-capability-secret"
    path.parent.mkdir()
    path.write_text(json.dumps(config), encoding="utf-8")
    before = path.read_bytes()
    wire = json.dumps(SettingsService().public_settings())
    assert "legacy-inline-secret" not in wire
    assert "nested-capability-secret" not in wire
    assert path.read_bytes() == before


def _save_test_plan(store, project_id, version_id):
    suite = {"name": "Regression", "plc_model": "FX3U", "tests": [{"name": "Start output",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 5, "set": {"X0": 1}},
        {"at_ms": 10, "expect": {"Y0": 1}}], "trace_devices": ["X0", "Y0"], "sample_ms": 5, "timeout_ms": 20}]}
    return store.save_simulator_test_plan(project_id, version_id, suite)


def test_plan_read_and_pending_execution_proposal_do_not_migrate_legacy_workspace(saved, workbench):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    loaded = workbench.projects.plan(project["id"], version["id"], plan["binding"]["plan_id"])
    assert loaded["binding"] == plan["binding"]
    proposal = workbench.execution_proposal({"action": "simulation", "project_id": project["id"],
        "version_id": version["id"], "plan_id": plan["binding"]["plan_id"], "request_id": "legacy-plan"})
    assert proposal["status"] == "pending"
    assert _bytes(store.base_dir) == before
    # The existing desktop call retains its explicitly requested default migration.
    store.load_simulator_test_plan(project["id"], version["id"], plan["binding"]["plan_id"])
    assert "ir" in store.get_version(project["id"], version["id"])["artifacts"]


@pytest.mark.parametrize("bad_path", ["../../outside.json", "plan.json:private.json", "C:/outside.json"])
def test_plan_manifest_rejects_escape_and_ads_before_store_load(saved, bad_path, monkeypatch):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    changed = store.get_project(project["id"])
    changed["versions"][0]["simulator_test_plans"][0]["plan_artifact"] = bad_path
    store.save_project(changed)
    service = ProjectService(store.base_dir)
    monkeypatch.setattr(service.store, "load_simulator_test_plan", lambda *args, **kwargs: pytest.fail("Unsafe plan reached storage"))
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        service.plan(project["id"], version["id"], plan["binding"]["plan_id"])
    assert _bytes(store.base_dir) == before


def test_plan_validates_underlying_ir_manifest_before_loading(saved, monkeypatch):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    _change_artifact(store, project["id"], "ir", "../../outside.json")
    service = ProjectService(store.base_dir)
    monkeypatch.setattr(service.store, "load_simulator_test_plan", lambda *args, **kwargs: pytest.fail("Unsafe IR reached storage"))
    with pytest.raises(ProjectError):
        service.plan(project["id"], version["id"], plan["binding"]["plan_id"])


@pytest.fixture
def saved_debug_plan(tmp_path):
    from test_plc_debug_loop import _project_with_failure, _diagnosis, _patch, _knowledge
    from application.debug_loop import DebugPatchLoopService
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    plan = DebugPatchLoopService(store, retriever=_knowledge).prepare_plan(project_id, version_id, run_id, _diagnosis(), _patch(program))
    saved = store.save_debug_plan(project_id, version_id, plan)
    return store, project_id, version_id, saved


def test_debug_plan_rebuilds_candidate_from_saved_evidence_without_writes(saved_debug_plan):
    store, project_id, version_id, saved = saved_debug_plan
    before = _bytes(store.base_dir)
    loaded = ProjectService(store.base_dir).plan(project_id, version_id, saved["plan_id"], kind="debug")
    assert loaded["candidate_ir"] == saved["candidate_ir"]
    assert loaded["source_run_id"] == saved["source_run_id"]
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("change", ["project", "version", "candidate", "suite"])
def test_debug_plan_rejects_cross_version_or_changed_candidate(saved_debug_plan, change):
    store, project_id, version_id, plan = saved_debug_plan
    path = store.project_dir(project_id) / "debug" / "plans" / (plan["plan_id"] + ".json")
    altered = copy.deepcopy(plan)
    if change == "project":
        altered["project_id"] = "other_project"
    elif change == "version":
        altered["base_version_id"] = "v9999"
    elif change == "candidate":
        altered["candidate_ir"]["revision"] += 1
    else:
        altered["regression_suite"]["name"] = "Different suite"
    store._write_json(path, altered)
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).plan(project_id, version_id, plan["plan_id"], kind="debug")
    assert _bytes(store.base_dir) == before
