"""Native GX read/compare fixtures, with filesystem exports replacing the GUI."""

import threading
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.execution import GXExecutionCoordinator
from gxworks2.csv_manager import CSVManager
from gxworks2.models import GXWorks2Session
from gxworks2.sync_service import GXWorks2SyncService
from plc.core import PLCCore
from plc.ir import build_plc_ir, ir_to_ladder
from storage.session import SessionStore


def _program(output="Y0"):
    return build_plc_ir({
        "device_comments": {"X0": "启动", output: "运行"},
        "rungs": [{"rung_id": 1, "header_element": None, "shared_inputs": [], "branches": [{
            "branch_id": 1, "y_offset_level": 0,
            "inputs": [{"type": "NO", "address": "X0", "label": ""}],
            "outputs": [{"type": "COIL", "address": output, "label": ""}],
        }]}],
    }, plc_model="FX3U", revision=1)


class FixtureDesktop:
    def __init__(self, directory):
        self.directory = directory
        self.saved = 0
        self.exports = []

    def inspect_project(self, session):
        return {"automation_available": True, "project_open": True, "program_ready": True, "project_name": "Isolated fixture"}

    def export_current_program(self, session, destination):
        self.exports.append(threading.get_ident())
        Path(destination).write_bytes((self.directory / "program.csv").read_bytes())

    def export_current_comments(self, session, destination):
        self.exports.append(threading.get_ident())
        Path(destination).write_bytes((self.directory / "comments.csv").read_bytes())

    def save_project(self, session):
        self.saved += 1
        return {"success": True}


@pytest.fixture
def fixture(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("Native read fixture", plc_model="FX3U")
    version_id, directory = store.prepare_version(project["id"])
    program = _program()
    compiled = PLCCore().compile_project(program, directory)
    version = store.complete_version(project["id"], version_id, {
        **store._ir_metadata(program), "target_mode": "ladder", "plc_model": "FX3U", "artifacts": compiled["artifacts"],
    })
    native = tmp_path / "native-gx-export"
    PLCCore().compile_project(_program("Y1"), native)
    desktop = FixtureDesktop(native)
    session = GXWorks2Session(10, 20, "Isolated fixture - GX Works2", executable="GD2.exe", project_open=True, project_name="Isolated fixture", project_state_known=True)
    finder = SimpleNamespace(find_running=lambda: session)
    sync = GXWorks2SyncService(finder, desktop, CSVManager(), tmp_path / "gx-evidence", max_export_attempts=1)
    com_events = []
    com = SimpleNamespace(CoInitialize=lambda: com_events.append(("init", threading.get_ident())), CoUninitialize=lambda: com_events.append(("uninit", threading.get_ident())))
    coordinator = GXExecutionCoordinator(store, resource_lock_path=tmp_path / "gx.lock", com_factory=lambda: com,
        dependencies_factory=lambda operation: {"reader": sync.read_current_snapshot, "inspector": sync.inspect})
    try:
        yield store, project["id"], version, desktop, sync, coordinator, com_events
    finally:
        coordinator.close()


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_native_read_builds_roundtrip_checked_candidate_without_version_save(fixture):
    store, project_id, version, desktop, sync, coordinator, events = fixture
    before = _files(store.base_dir)
    result = coordinator.submit_read("read_gx", {"project_id": project_id, "version_id": version["id"]}).result(10)
    assert result["status"] == "read", result
    assert result["passed"] is False
    assert result["requires_local_acceptance"] is True
    assert result["_candidate_ir"]["revision"] == 2
    ladder = ir_to_ladder(result["_candidate_ir"])
    assert ladder["rungs"][0]["branches"][0]["outputs"][0]["address"] == "Y001"
    assert result["candidate_metadata"]["source_kind"] == "gxworks2_sync"
    assert "artifacts" not in result["candidate_metadata"]
    assert desktop.saved == 0
    assert result["sync"]["details"]["gx_save"]["attempted"] is False
    assert not sync.baseline_store.state_root.exists()
    assert _files(store.base_dir) == before
    assert [name for name, _ in events] == ["init", "uninit"]
    assert set(desktop.exports) == {events[0][1]}


def test_sync_inspection_returns_three_way_comparison_without_pushing_or_saving(fixture):
    store, project_id, version, desktop, sync, coordinator, events = fixture
    before = _files(store.base_dir)
    result = coordinator.submit_read("inspect_gx", {"project_id": project_id, "version_id": version["id"]}).result(10)
    assert result["status"] == "inspected", result
    assert result["sync"]["status"] == "unbound"
    assert result["sync"]["details"]["diff"]["changed_instruction_count"] > 0
    assert result["sync"]["details"]["baseline_write_enabled"] is False
    assert "_candidate_ir" not in result
    assert result["passed"] is False
    assert desktop.saved == 0
    assert _files(store.base_dir) == before
    assert not sync.baseline_store.state_root.exists()


def test_equal_sync_read_does_not_create_baseline_but_legacy_default_still_does(fixture):
    store, project_id, version, desktop, sync, coordinator, events = fixture
    root = store.version_dir(project_id, version["id"])
    desktop.directory = root
    result = coordinator.submit_read("inspect_gx", {"project_id": project_id, "version_id": version["id"]}).result(10)
    assert result["sync"]["status"] == "synced"
    assert "未写入" in result["sync"]["message"]
    assert not sync.baseline_store.state_root.exists()
    assert desktop.saved == 0
    # The opt-in readonly flags must not alter existing Qt's default behavior.
    original = sync.inspect(root / "program.csv", root / "comments.csv")
    assert original.success is True
    assert desktop.saved == 1
    assert sync.baseline_store.state_root.exists()


def test_empty_project_can_read_native_initial_candidate_without_activation(fixture):
    store, _, _, desktop, sync, coordinator, events = fixture
    empty = store.create_project("Empty read target", plc_model="FX3U")
    before = _files(store.base_dir)
    result = coordinator.submit_read("read_gx", {"project_id": empty["id"], "version_id": None}).result(10)
    assert result["status"] == "read", result
    assert result["_candidate_ir"]["revision"] == 1
    assert result["version_id"] is None
    assert _files(store.base_dir) == before


def test_environment_unavailable_read_is_not_success_or_candidate(fixture):
    store, project_id, version, desktop, sync, coordinator, events = fixture
    sync.finder.find_running = lambda: None
    result = coordinator.submit_read("read_gx", {"project_id": project_id, "version_id": version["id"]}).result(10)
    assert result["status"] == "unavailable"
    assert result["passed"] is False
    assert "_candidate_ir" not in result
    assert desktop.exports == []


def test_read_entry_cannot_be_used_as_approval_shortcut(fixture):
    *_, coordinator, events = fixture
    for operation in ("gx_import", "simulation", "debug", "write_plc"):
        with pytest.raises(ValueError):
            coordinator.submit_read(operation, {})


def test_unverified_native_instruction_is_explicitly_unsupported_not_loosened(fixture):
    store, project_id, version, desktop, sync, coordinator, events = fixture
    path = desktop.directory / "program.csv"
    with path.open(encoding="utf-16", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    for row in rows[3:]:
        if len(row) > 3 and row[2] == "OUT":
            row[2], row[3] = "FUTURE_VENDOR_OP", "D0 D10"
    with path.open("w", encoding="utf-16", newline="") as stream:
        csv.writer(stream, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)
    before = _files(store.base_dir)
    result = coordinator.submit_read("read_gx", {"project_id": project_id, "version_id": version["id"]}).result(10)
    assert result["status"] == "unsupported", result
    assert result["error_code"] == "unverified_native_instruction"
    assert result["findings"][0]["opcode"] == "FUTURE_VENDOR_OP"
    assert "_candidate_ir" not in result
    assert result["passed"] is False
    assert _files(store.base_dir) == before
