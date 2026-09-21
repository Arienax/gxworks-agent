"""Isolated GX execution ownership tests: no vendor software or desktop writes."""

import copy
import ast
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from application.execution import DesktopResourceLock, GXExecutionCoordinator, read_environment


class FakeStore:
    def __init__(self, root):
        self.root = root
        self.active = "v1"
        self.version = {"id": "v1", "revision": 1, "artifacts": {"program_csv": "program.csv", "comment_csv": "comments.csv"}}
        directory = self.version_dir("p1", "v1")
        directory.mkdir(parents=True)
        (directory / "program.csv").write_text("program", encoding="utf-8")
        (directory / "comments.csv").write_text("comments", encoding="utf-8")

    def get_project(self, project_id):
        return {"id": project_id, "active_version_id": self.active}

    def get_version(self, project_id, version_id):
        return copy.deepcopy(self.version)

    def project_dir(self, project_id):
        return self.root / project_id

    def version_dir(self, project_id, version_id):
        return self.project_dir(project_id) / version_id


class FakeCOM:
    def __init__(self, events):
        self.events = events

    def CoInitialize(self):
        self.events.append(("com_init", threading.get_ident()))

    def CoUninitialize(self):
        self.events.append(("com_uninit", threading.get_ident()))


@pytest.fixture
def store(tmp_path):
    return FakeStore(tmp_path / "workspace")


def coordinator(store, tmp_path, importer, events):
    return GXExecutionCoordinator(
        store, resource_lock_path=tmp_path / "desktop.lock",
        dependencies_factory=lambda operation: {"importer": importer},
        com_factory=lambda: FakeCOM(events),
    )


def test_queue_serializes_imports_on_one_fixed_com_thread_and_freezes_inputs(store, tmp_path):
    entered, release = threading.Event(), threading.Event()
    events = []
    calls = []

    def importer(path, **kwargs):
        calls.append(kwargs)
        events.append(("import", threading.get_ident()))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {"success": True, "message": "imported"}

    service = coordinator(store, tmp_path, importer, events)
    assert service._thread is None
    assert not (tmp_path / "desktop.lock").exists()
    try:
        payload = {"project_id": "p1", "version_id": "v1"}
        first = service.submit_approved("gx_import", payload, approval_id="a1")
        assert entered.wait(5)
        second = service.submit_approved("gx_import", payload, approval_id="a2")
        payload["version_id"] = "modified_after_submission"
        assert not second.running()
        assert len(calls) == 1
        release.set()
        assert first.result(5)["status"] == "imported"
        assert second.result(5)["status"] == "imported"
    finally:
        release.set()
        service.close()
    assert [name for name, _ in events] == ["com_init", "import", "com_uninit"] * 2
    assert len({thread_id for _, thread_id in events}) == 1
    assert events[0][1] != threading.get_ident()
    assert all(call["start_if_needed"] is False for call in calls)


def test_cross_process_desktop_lock_blocks_second_owner_and_is_released(tmp_path):
    path = tmp_path / "desktop.lock"
    code = "\n".join([
        "import sys",
        "from application.execution import DesktopResourceLock, ExecutionUnavailableError",
        "try:",
        "    with DesktopResourceLock(sys.argv[1]): print('acquired')",
        "except ExecutionUnavailableError:",
        "    print('owned')",
    ])
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    with DesktopResourceLock(path):
        checked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env, timeout=10)
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout.strip() == "owned"
    checked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env, timeout=10)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == "acquired"


def test_failure_balances_com_and_never_reports_passed(store, tmp_path):
    events = []

    def importer(*args, **kwargs):
        raise RuntimeError("import interrupted")

    with coordinator(store, tmp_path, importer, events) as service:
        result = service.submit_approved("import_gx", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "error"
    assert result["passed"] is False
    assert [event[0] for event in events] == ["com_init", "com_uninit"]


def test_queued_cancel_and_stale_version_never_import(store, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, events = [], []

    def importer(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"success": True}

    with coordinator(store, tmp_path, importer, events) as service:
        first = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1")
        assert entered.wait(5)
        cancelled = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a2")
        stale = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a3")
        assert cancelled.cancel()
        assert first.cancel() is False
        store.active = "v2"
        release.set()
        assert first.result(5)["status"] == "imported"
        assert stale.result(5)["status"] == "error"
        assert cancelled.cancelled()
    assert len(calls) == 1


def test_environment_observation_does_not_initialize_com_or_start_gateway(monkeypatch, store, tmp_path):
    import simulator.gateway
    import simulator.runtime

    def forbidden(*args, **kwargs):
        pytest.fail("Environment observation must not initialize execution")

    monkeypatch.setattr(simulator.runtime, "get_simulator_gateway_runtime", forbidden)
    monkeypatch.setattr(simulator.runtime.SimulatorGatewayRuntime, "ensure_gateway", forbidden)
    monkeypatch.setattr(simulator.gateway, "detect_simulator_environment", lambda: {"simulator_installed": False, "mx_component_installed": False})
    with GXExecutionCoordinator(store, dependencies_factory=forbidden, com_factory=forbidden) as service:
        assert service._thread is None
        result = read_environment()
    assert result["status"] == "unavailable"
    assert result["passed"] is False
    assert result["gateway_started"] is False


def test_import_is_not_a_gx_compile_or_simulator_pass(store, tmp_path):
    with coordinator(store, tmp_path, lambda *args, **kwargs: {"success": True}, []) as service:
        result = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "imported"
    assert result["passed"] is False
    assert result["gx_compile_status"] == "unverified"
    assert result["simulation_status"] == "not_run"


def test_unavailable_simulator_service_remains_unavailable(monkeypatch, store, tmp_path):
    from simulator.workflow import SimulatorVersionWorkflowService

    calls = []

    def run(self, project_id, version_id, plan, **kwargs):
        calls.append((project_id, version_id, plan))
        return {"status": "unavailable", "message": "desktop locked", "execution": {"result": {"passed": False}}}

    monkeypatch.setattr(SimulatorVersionWorkflowService, "run_approved_plan", run)
    service = GXExecutionCoordinator(store, resource_lock_path=tmp_path / "desktop.lock", com_factory=lambda: FakeCOM([]), dependencies_factory=lambda operation: {"importer": object(), "preparer": object()})
    with service:
        result = service.submit_approved("simulation", {"project_id": "p1", "version_id": "v1", "plan": {"binding": "frozen"}}, approval_id="a1").result(5)
    assert calls == [("p1", "v1", {"binding": "frozen"})]
    assert result["status"] == "unavailable"
    assert result["passed"] is False


@pytest.mark.parametrize("filename", ["../../outside.csv", "program.csv:hidden-stream", "C:\\private\\program.csv"])
def test_import_artifact_traversal_is_rejected(store, tmp_path, filename):
    store.version["artifacts"]["program_csv"] = filename
    with coordinator(store, tmp_path, lambda *a, **kw: pytest.fail("Must not import"), []) as service:
        result = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_no_arbitrary_operation_or_blank_approval_is_accepted(store, tmp_path):
    with coordinator(store, tmp_path, lambda *a, **kw: None, []) as service:
        with pytest.raises(ValueError):
            service.submit_approved("write_plc", {}, approval_id="approval")
        with pytest.raises(ValueError):
            service.submit_approved("gx_import", {}, approval_id="")
        assert service._thread is None
