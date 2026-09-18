import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from application.jobs import JobManager
from application.proposals import ProposalService
from application.workspace import ConflictError, WorkspaceBusyError, WorkspaceWriterLock, atomic_json
from storage.session import SessionStore


@pytest.fixture
def services(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("隔离工程")
    lock = WorkspaceWriterLock(store.base_dir, tmp_path / "locks").acquire()
    state = tmp_path / "state"
    try:
        yield store, project, lock, state
    finally:
        lock.release()


def _bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _program():
    from plc.ir import build_plc_ir
    return build_plc_ir({"device_comments": {}, "rungs": [{"rung_id": 1, "debug_note": "启动", "header_element": None,
        "shared_inputs": [], "branches": [{"branch_id": 1, "y_offset_level": 0,
        "inputs": [{"type": "NO", "address": "X0", "label": ""}],
        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}]}]}]},
        plc_model="FX3U", program_name="MAIN", revision=1)


def _base(store, project_id):
    from plc.core import PLCCore
    program = _program()
    version_id, output = store.prepare_version(project_id)
    metadata = store._ir_metadata(program)
    metadata.update(target_mode="ladder", plc_model="FX3U", artifacts=PLCCore().compile_project(program, output)["artifacts"])
    return store.complete_version(project_id, version_id, metadata), program


def test_workspace_lock_owns_process_and_thread_boundary_without_workspace_writes(services, tmp_path):
    store, _, lock, _ = services
    before = _bytes(store.base_dir)
    contender = WorkspaceWriterLock(store.base_dir, tmp_path / "locks")
    assert contender.thread_lock is lock.thread_lock
    with pytest.raises(WorkspaceBusyError):
        contender.acquire()
    code = "from application.workspace import WorkspaceWriterLock; import sys; WorkspaceWriterLock(sys.argv[1],sys.argv[2]).acquire()"
    environment = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run([sys.executable, "-c", code, str(store.base_dir), str(tmp_path / "locks")],
                            env=environment, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert b"WorkspaceBusyError" in result.stderr
    assert _bytes(store.base_dir) == before
    lock.release()
    contender.acquire().release()


def test_application_state_cannot_be_reused_by_another_workspace(services, tmp_path):
    store, _, lock, state = services
    manager = JobManager(state, lock)
    manager.shutdown()
    before = _bytes(state)
    other_store = SessionStore(base_dir=tmp_path / "other-workspace")
    with WorkspaceWriterLock(other_store.base_dir, tmp_path / "locks") as other_lock:
        with pytest.raises(ConflictError, match="another workspace"):
            JobManager(state, other_lock)
        with pytest.raises(ConflictError, match="another workspace"):
            ProposalService(other_store, state, other_lock)
    assert _bytes(state) == before
    assert str(store.base_dir).encode() not in (state / ".workspace.json").read_bytes()
    # Reopening either service for the original workspace remains valid.
    ProposalService(store, state, lock)
    reopened = JobManager(state, lock)
    reopened.shutdown()


def test_job_snapshot_idempotency_and_reconnection(services):
    store, project, lock, state = services
    before = _bytes(store.base_dir)
    manager = JobManager(state, lock)
    started, finish = threading.Event(), threading.Event()
    snapshot = {"project_id": project["id"], "version_id": "v0001", "requirements": {"text": "original"}}
    seen = []
    def worker(ctx):
        started.set()
        assert finish.wait(10)
        seen.append(ctx.snapshot)
        changed = ctx.snapshot
        changed["requirements"]["text"] = "worker mutation"
        assert ctx.snapshot["requirements"]["text"] == "original"
        ctx.emit("progress", {"progress": 50, "api_key": "private", "_candidate_ir": {"hidden": True}})
        return {"proposal_id": "proposal_fake", "_private": "hidden", "path": "C:/private"}
    try:
        job = manager.submit("generate", snapshot, worker, request_id="same")
        assert started.wait(10)
        snapshot["requirements"]["text"] = "caller mutation"
        duplicate = manager.submit("generate", {"project_id": project["id"], "version_id": "v0001", "requirements": {"text": "original"}}, worker, request_id="same")
        assert duplicate["id"] == job["id"]
        with pytest.raises(ConflictError):
            manager.submit("generate", snapshot, worker, request_id="same")
        assert "snapshot" not in manager.get(job["id"])
    finally:
        finish.set()
        manager.shutdown()
    assert seen[0]["requirements"]["text"] == "original"
    completed = manager.get(job["id"])
    assert completed["status"] == "completed"
    assert completed["result"] == {"proposal_id": "proposal_fake"}
    events = manager.events(job["id"])
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["project_id"] == project["id"] and e["version_id"] == "v0001" for e in events)
    assert manager.events(job["id"], events[-2]["sequence"]) == events[-1:]
    assert "private" not in json.dumps(events)
    assert _bytes(store.base_dir) == before


def test_job_cancels_only_at_checkpoint_and_restart_does_not_reexecute(services):
    _, project, lock, state = services
    manager = JobManager(state, lock)
    started, finish = threading.Event(), threading.Event()
    ran = []
    def worker(ctx):
        started.set()
        assert finish.wait(10)
        ctx.checkpoint()
        ran.append("effect")
    job = manager.submit("generate", {"project_id": project["id"]}, worker)
    assert started.wait(10)
    assert manager.cancel(job["id"])["status"] == "cancelling"
    finish.set()
    manager.shutdown()
    assert manager.get(job["id"])["status"] == "cancelled"
    assert not ran
    private = manager._load(job["id"])
    private.update(status="running", cancel_requested=False)
    manager._save(private)
    restored = JobManager(state, lock)
    try:
        assert restored.get(job["id"])["status"] == "interrupted"
        assert restored.events(job["id"])[-1]["event_type"] == "interrupted"
        assert not ran
    finally:
        restored.shutdown()


def test_job_snapshot_refuses_credentials(services):
    _, _, lock, state = services
    manager = JobManager(state, lock)
    try:
        with pytest.raises(ValueError, match="Credentials"):
            manager.submit("generate", {"nested": {"api_key": "secret"}}, lambda ctx: {})
        assert manager.list() == []
    finally:
        manager.shutdown()


def test_job_progress_and_cancel_remain_available_during_engineering_transaction(services):
    _, project, lock, state = services
    manager = JobManager(state, lock)
    started, finish = threading.Event(), threading.Event()
    def worker(ctx):
        with lock.thread_lock:
            started.set()
            assert finish.wait(10)
            ctx.checkpoint()
    job = manager.submit("execution", {"project_id": project["id"]}, worker)
    assert started.wait(10)
    try:
        # The long engineering transaction must not lock out the event stream.
        event = manager.emit(job["id"], "progress", {"text": "现场操作中"})
        assert event["sequence"] > 1
        assert manager.events(job["id"])[-1] == event
        assert manager.cancel(job["id"])["cancel_requested"]
    finally:
        finish.set()
        manager.shutdown()
    assert manager.get(job["id"])["status"] == "cancelled"


def test_proposal_new_ir_acceptance_is_private_single_use_and_local(services):
    store, project, lock, state = services
    service = ProposalService(store, state, lock)
    before = _bytes(store.base_dir)
    payload = {"_candidate_ir": _program(), "candidate_id": "candidate_unit"}
    proposal = service.create("accept_local", project["id"], payload,
                              public_summary={"summary": "new", "api_key": "hidden"}, request_id="new")
    assert _bytes(store.base_dir) == before
    assert "_candidate_ir" not in json.dumps(proposal)
    assert "hidden" not in json.dumps(proposal)
    payload["_candidate_ir"]["revision"] = 5
    assert service.read_private(proposal["id"])["_candidate_ir"]["revision"] == 1
    accepted = service.accept(proposal["id"])
    assert accepted["status"] == "accepted"
    assert service.accept(proposal["id"]) == accepted
    current = store.get_project(project["id"])
    assert len(current["versions"]) == 1
    assert current["active_version_id"] == accepted["result"]["version_id"]
    assert not (store.project_dir(project["id"]) / "GX Works2 Backups").exists()


@pytest.mark.parametrize("change", ["active", "spec", "artifact", "payload"])
def test_proposal_rejects_changed_engineering_inputs_before_version_write(services, change):
    store, project, lock, state = services
    version, program = _base(store, project["id"])
    service = ProposalService(store, state, lock)
    proposal = service.create("accept_local", project["id"], {"_candidate_ir": program}, base_version_id=version["id"])
    if change == "active":
        changed = store.get_project(project["id"])
        changed["active_version_id"] = None
        store.save_project(changed)
    elif change == "spec":
        changed = store.get_project(project["id"])
        changed["confirmed_spec"] = {"project_goal": "changed"}
        store.save_project(changed)
    elif change == "artifact":
        path = store.version_dir(project["id"], version["id"]) / version["artifacts"]["ir"]
        path.write_text("{}", encoding="utf-8")
    else:
        record = service._load(proposal["id"])
        record["private_payload"]["_candidate_ir"]["revision"] = 99
        service._save(record)
    before = _bytes(store.base_dir)
    with pytest.raises(ConflictError):
        service.accept(proposal["id"])
    assert service.get(proposal["id"])["status"] == "conflict"
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("filename", [
    "program.ir.json:secret", "C:program.ir.json", "C:/private/program.json",
    "//server/share/program.json", "\\rooted.json", "/tmp/program.json",
    "../program.json", "nested\\..\\program.json", "absolute_inside",
])
@pytest.mark.parametrize("operation", ["create", "approve"])
def test_proposal_manifest_paths_rejected_before_artifact_hash_reads(services, monkeypatch, filename, operation):
    store, project, lock, state = services
    version, _ = _base(store, project["id"])
    service = ProposalService(store, state, lock)
    proposal = None
    if operation == "approve":
        proposal = service.create("gx_import", project["id"], {"version_id": version["id"]}, base_version_id=version["id"])
    if filename == "absolute_inside":
        filename = str(store.version_dir(project["id"], version["id"]) / version["artifacts"]["ir"])
    changed = store.get_project(project["id"])
    # Put the malicious entry last to verify preflight happens before any hash.
    changed["versions"][0]["artifacts"]["malicious"] = filename
    store.save_project(changed)
    atomic_json(store.version_dir(project["id"], version["id"]) / "version.json", changed["versions"][0])
    before = _bytes(store.base_dir)
    with monkeypatch.context() as blocked:
        blocked.setattr(Path, "read_bytes", lambda *_: pytest.fail("Unsafe manifest must be rejected before artifact reads"))
        blocked.setattr(store, "load_program_ir", lambda *_args, **_kwargs: pytest.fail("Unsafe manifest reached IR loading"))
        if operation == "create":
            with pytest.raises(ValueError, match="Invalid artifact manifest"):
                service.create("gx_import", project["id"], {"version_id": version["id"]}, base_version_id=version["id"])
        else:
            with pytest.raises(ConflictError):
                service.accept(proposal["id"], lambda *_: pytest.fail("Unsafe proposal must not execute"))
            assert service.get(proposal["id"])["status"] == "conflict"
    assert _bytes(store.base_dir) == before


def test_proposal_patch_reuses_core_without_legacy_migration(services):
    from plc.core import PLCCore
    store, project, lock, state = services
    version, program = _base(store, project["id"])
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append({"type": "NC", "address": "X1", "label": ""})
    from plc.ir import canonical_sha256
    candidate = PLCCore().patch_program(program, {"base_revision": 1, "base_ir_sha256": canonical_sha256(program),
        "target_revision": 2, "operations": [{"operation": "modify_network", "network": "N0001", "ladder": replacement}]})
    service = ProposalService(store, state, lock)
    proposal = service.create("accept_local", project["id"], {"_candidate_ir": candidate["candidate_ir"]}, base_version_id=version["id"])
    before = _bytes(store.version_dir(project["id"], version["id"]))
    accepted = service.accept(proposal["id"])
    assert accepted["result"]["version_id"] == "v0002"
    assert _bytes(store.version_dir(project["id"], version["id"])) == before


def test_external_proposal_actions_require_callback_and_never_retry_uncertain_execution(services):
    store, project, lock, state = services
    service = ProposalService(store, state, lock)
    proposal = service.create("gx_import", project["id"], {"version_id": None})
    with pytest.raises(ValueError, match="coordinator"):
        service.accept(proposal["id"])
    seen = []
    def uncertain(payload, approval_id):
        assert service.get(approval_id)["status"] == "executing"
        seen.append(approval_id)
        raise RuntimeError("private failure")
    with pytest.raises(RuntimeError):
        service.accept(proposal["id"], uncertain)
    assert service.get(proposal["id"])["status"] == "interrupted"
    with pytest.raises(ConflictError):
        service.accept(proposal["id"], uncertain)
    assert seen == [proposal["id"]]
    record = service._load(proposal["id"])
    record["status"] = "executing"
    service._save(record)
    restarted = ProposalService(store, state, lock)
    assert restarted.get(proposal["id"])["status"] == "interrupted"
    assert "private failure" not in json.dumps(restarted.get(proposal["id"]))


def test_st_artifacts_are_frozen_and_managed(services):
    store, project, lock, state = services
    staging = state / "generation" / "one"
    staging.mkdir(parents=True)
    data = b"VAR x : BOOL; END_VAR\nx := TRUE;"
    (staging / "program.st").write_bytes(data)
    service = ProposalService(store, state, lock)
    payload = {"target_mode": "st", "staging_dir": str(staging), "artifacts": {
        "st": {"path": "program.st", "sha256": hashlib.sha256(data).hexdigest()}},
        "metadata": {"summary": "ST candidate"}}
    proposal = service.create("accept_local", project["id"], payload)
    (staging / "program.st").write_text("mutated", encoding="utf-8")
    accepted = service.accept(proposal["id"])
    version = store.get_version(project["id"], accepted["result"]["version_id"])
    assert (store.version_dir(project["id"], version["id"]) / version["artifacts"]["st"]).read_bytes() == data
    assert version["summary"] == "ST candidate"
    assert "staging_dir" not in json.dumps(accepted)


@pytest.mark.parametrize("bad_path", ["../outside.st", "C:/outside.st", "program.st:stream"])
def test_st_staging_manifest_refuses_path_escape(services, bad_path):
    store, project, lock, state = services
    staging = state / "staging"
    staging.mkdir(parents=True)
    service = ProposalService(store, state, lock)
    before = _bytes(store.base_dir)
    with pytest.raises(ValueError):
        service.create("accept_local", project["id"], {"target_mode": "st", "staging_dir": str(staging),
            "artifacts": {"st": {"path": bad_path, "sha256": "0" * 64}}})
    assert _bytes(store.base_dir) == before
    assert service.list() == []


def test_concurrent_approvals_execute_callback_once(services):
    from concurrent.futures import ThreadPoolExecutor
    store, project, lock, state = services
    service = ProposalService(store, state, lock)
    proposal = service.create("simulation", project["id"], {})
    seen = []
    def execute(payload, proposal_id):
        seen.append(proposal_id)
        return {"status": "completed", "run_id": "run_once"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.accept, proposal["id"], execute) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    assert seen == [proposal["id"]]


def test_unavailable_execution_is_failed_consumed_and_never_reported_accepted(services):
    store, project, lock, state = services
    service = ProposalService(store, state, lock)
    proposal = service.create("simulation", project["id"], {})
    seen = []
    def execute(payload, proposal_id):
        seen.append(proposal_id)
        return {"status": "unavailable", "passed": False}
    result = service.accept(proposal["id"], execute)
    assert result["status"] == "failed"
    assert result["result"] == {"status": "unavailable", "passed": False}
    assert service.accept(proposal["id"], execute) == result
    assert seen == [proposal["id"]]


def test_legacy_base_is_not_migrated_when_previewed_or_accepted(services):
    store, project, lock, state = services
    version, program = _base(store, project["id"])
    legacy = store.get_project(project["id"])
    legacy["versions"][0]["artifacts"].pop("ir")
    store.save_project(legacy)
    atomic_json(store.version_dir(project["id"], version["id"]) / "version.json", legacy["versions"][0])
    old = _bytes(store.version_dir(project["id"], version["id"]))
    service = ProposalService(store, state, lock)
    before = _bytes(store.base_dir)
    proposal = service.create("accept_local", project["id"], {"_candidate_ir": program}, base_version_id=version["id"])
    assert _bytes(store.base_dir) == before
    service.accept(proposal["id"])
    assert _bytes(store.version_dir(project["id"], version["id"])) == old
    assert "ir" not in store.get_version(project["id"], version["id"])["artifacts"]


def test_public_events_keep_full_accepted_text(services):
    _, _, lock, state = services
    manager = JobManager(state, lock)
    accepted = "完整工程分析。" * 5000
    def worker(ctx):
        ctx.emit("content", {"text": accepted})
        return {"analysis_id": "analysis_complete"}
    job = manager.submit("analysis", {}, worker)
    manager.shutdown()
    content = next(e for e in manager.events(job["id"]) if e["event_type"] == "content")
    assert content["payload"]["text"] == accepted


def test_proposal_reject_is_idempotent_and_cannot_be_approved(services):
    store, project, lock, state = services
    service = ProposalService(store, state, lock)
    proposal = service.create("debug", project["id"], {})
    assert service.reject(proposal["id"])["status"] == "rejected"
    assert service.reject(proposal["id"])["status"] == "rejected"
    with pytest.raises(ConflictError):
        service.accept(proposal["id"], lambda *_: {})


def test_headless_application_modules_import_without_qt_models_or_devices(tmp_path):
    code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt6','PyQt5','qt_compat','main','api','model_provider','openai','pywinauto','win32com','config','config_manager','credential_store'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
import application.workspace, application.events, application.jobs, application.proposals
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path("src").resolve())), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
