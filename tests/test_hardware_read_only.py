"""No real devices: every read uses a fake backend or mocked child process."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.hardware import HardwareService
from application.workbench import WorkbenchService
from plc.core import PLCCore
from gxworks2.hardware_reader import HardwareError, HardwareReader, normalize_read_addresses
from plc.ir import build_plc_ir
from storage.session import SessionStore


class ReaderDouble:
    def __init__(self):
        self.calls = []
        self.digest = "a" * 64
        self.callback = None

    def availability(self):
        return {"available": True, "backend": "test_read_only", "message": "isolated test double"}

    def fingerprint(self):
        return self.digest

    def read_once(self, station, addresses, model, **kwargs):
        self.calls.append((station, addresses, model, kwargs))
        if self.callback:
            self.callback()
        return {"logical_station": station, "values": {address: i for i, address in enumerate(addresses)}}


@pytest.fixture
def hardware(tmp_path):
    store = SessionStore(tmp_path / "workspace")
    project = store.create_project("只读设备接入测试")
    program = build_plc_ir({"device_comments": {}, "rungs": [{"rung_id": 1, "debug_note": "启停", "header_element": None,
        "shared_inputs": [], "branches": [{"branch_id": 1, "y_offset_level": 0,
        "inputs": [{"type": "NO", "address": "X0", "label": ""}], "outputs": [{"type": "COIL", "address": "Y0", "label": ""}]}]}]}, plc_model="FX3U")
    version_id, output = store.prepare_version(project["id"])
    metadata = store._ir_metadata(program)
    metadata.update(target_mode="ladder", plc_model="FX3U", artifacts=PLCCore().compile_project(program, output)["artifacts"])
    store.complete_version(project["id"], version_id, metadata)
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: pytest.fail("Hardware must not use models"))
    service.start()
    clock = [100.0]
    reader = ReaderDouble()
    hardware = HardwareService(service, reader=reader, clock=lambda: clock[0])
    service.hardware = hardware
    try:
        yield hardware, service, reader, project["id"], version_id, clock
    finally:
        service.close()


def proposal(hardware, *, station=2, ttl=60):
    module, _, _, project, version, _ = hardware
    return module.propose(project, version, "operator-a", {"logical_station": station,
        "target_label": "测试电柜 A", "addresses": ["X0", "D0"], "ttl_seconds": ttl})


def approval_command(session):
    return {**{k: session["target"][k] for k in ("logical_station", "target_label", "addresses", "ttl_seconds")}, "mapping_confirmed": True}


def approve(hardware, session):
    module, _, _, project, version, _ = hardware
    return module.approve(project, version, session["id"], "operator-a", approval_command(session))


def read(hardware, session, *, addresses=None, request="read_one", owner="operator-a"):
    module, _, _, project, version, _ = hardware
    return module.read(project, version, session["id"], owner,
        {"request_id": request, "addresses": addresses or ["X0", "D0"]})


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_default_off_queries_proposals_and_approval_never_connect(hardware):
    module, service, reader, project, version, _ = hardware
    state = files(service.state_dir)
    assert module.list(project, version, "operator-a")["sessions"] == []
    assert module.list(project, version, "operator-a")["default_enabled"] is False
    assert files(service.state_dir) == state
    workspace = files(service.store.base_dir)
    session = proposal(hardware)
    assert session["status"] == "pending"
    assert approve(hardware, session)["status"] == "active"
    assert reader.calls == []
    assert files(service.store.base_dir) == workspace


def test_manual_read_is_audited_and_duplicate_request_does_not_read_again(hardware):
    module, service, reader, project, version, clock = hardware
    session = proposal(hardware)
    approve(hardware, session)
    result = read(hardware, session)
    assert result["status"] == "read"
    assert result["values"] == {"X0": 0, "D0": 1}
    assert len(reader.calls) == 1
    clock[0] += 100  # Saved observations remain readable without physical I/O.
    assert read(hardware, session)["replayed"] is True
    assert len(reader.calls) == 1
    item = module.list(project, version, "operator-a")["sessions"][0]
    assert item["status"] == "expired"
    assert [entry["action"] for entry in item["audit"]] == ["proposed", "approved", "read_requested", "read_completed"]
    assert item["audit"][-1]["values"] == result["values"]
    assert item["read_count"] == 1


def test_pending_outside_expired_revoked_and_other_operator_are_rejected(hardware):
    module, _, reader, project, version, clock = hardware
    session = proposal(hardware)
    with pytest.raises(HardwareError, match="未确认"):
        read(hardware, session)
    approve(hardware, session)
    with pytest.raises(HardwareError, match="白名单"):
        read(hardware, session, addresses=["Y0"])
    with pytest.raises(HardwareError, match="另一操作员"):
        read(hardware, session, owner="operator-b")
    clock[0] += 61
    with pytest.raises(HardwareError, match="过期"):
        read(hardware, session)
    module.revoke(project, version, session["id"], "operator-a")
    assert module.list(project, version, "operator-a")["sessions"][0]["status"] == "revoked"
    assert reader.calls == []


def test_confirmation_must_repeat_exact_target_scope_and_duration(hardware):
    module, _, reader, project, version, _ = hardware
    session = proposal(hardware)
    expected = approval_command(session)
    for key, value in (("logical_station", 3), ("target_label", "另一个设备"), ("addresses", ["X0"]),
                       ("ttl_seconds", 300), ("mapping_confirmed", False)):
        with pytest.raises(HardwareError, match="不一致"):
            module.approve(project, version, session["id"], "operator-a", {**expected, key: value})
    assert reader.calls == []


def test_pending_authorization_expires_and_restart_revokes_active_session(hardware):
    module, service, reader, project, version, clock = hardware
    expired = proposal(hardware)
    clock[0] += 301
    with pytest.raises(HardwareError, match="过期"):
        approve(hardware, expired)
    session = proposal(hardware)
    approve(hardware, session)
    reopened = HardwareService(service, reader=reader, clock=lambda: clock[0])
    assert all(item["status"] == "revoked" for item in reopened.list(project, version, "operator-a")["sessions"])
    with pytest.raises(HardwareError, match="过期"):
        reopened.read(project, version, session["id"], "operator-a", {"request_id": "after_restart", "addresses": ["X0"]})
    assert reader.calls == []


def test_version_and_helper_changes_invalidate_authorization(hardware):
    module, service, reader, project, version, _ = hardware
    session = proposal(hardware)
    reader.digest = "b" * 64
    with pytest.raises(HardwareError, match="变化"):
        approve(hardware, session)
    reader.digest = "a" * 64
    approve(hardware, session)
    changed = service.store.get_project(project)
    changed["versions"][0]["summary"] = "changed after approval"
    service.store.save_project(changed)
    with pytest.raises(HardwareError, match="版本已变化"):
        read(hardware, session)
    assert reader.calls == []


def test_read_after_deadline_records_failure_and_no_accepted_values(hardware):
    module, _, reader, project, version, clock = hardware
    session = proposal(hardware)
    approve(hardware, session)
    reader.callback = lambda: clock.__setitem__(0, clock[0] + 61)
    with pytest.raises(HardwareError, match="未获验收"):
        read(hardware, session)
    with pytest.raises(HardwareError, match="不会自动重试"):
        read(hardware, session)
    assert len(reader.calls) == 1
    audit = module.list(project, version, "operator-a")["sessions"][0]["audit"]
    assert audit[-1]["action"] == "read_failed"
    assert all("values" not in row for row in audit)


def test_read_cannot_begin_when_pre_read_audit_cannot_be_saved(hardware, monkeypatch):
    _, _, reader, _, _, _ = hardware
    session = proposal(hardware)
    approve(hardware, session)
    def fail(*args):
        raise OSError("isolated disk failure")
    monkeypatch.setattr("application.hardware.atomic_json", fail)
    with pytest.raises(OSError):
        read(hardware, session)
    assert reader.calls == []


def test_read_failure_is_audited_without_leaking_backend_exception(hardware):
    module, _, reader, project, version, _ = hardware
    session = proposal(hardware)
    approve(hardware, session)
    def fail():
        raise RuntimeError("password=private implementation text")
    reader.callback = fail
    with pytest.raises(HardwareError, match="适配器执行失败"):
        read(hardware, session)
    assert "password" not in json.dumps(module.list(project, version, "operator-a"))


def test_read_only_workspace_cannot_create_hardware_permission(hardware):
    _, service, reader, _, _, _ = hardware
    service.read_only = True
    with pytest.raises(PermissionError):
        proposal(hardware)
    assert reader.calls == []


@pytest.mark.parametrize("addresses", [["X8"], ["D0Z0"], ["K4M0"], ["Y0;SetDevice"], ["../private"], [], ["SM0"]])
def test_only_bounded_simple_model_addresses_are_accepted(addresses):
    with pytest.raises(HardwareError):
        normalize_read_addresses(addresses, "FX3U")


def test_child_process_read_schema_is_fixed_and_response_is_checked(tmp_path, monkeypatch):
    executable = tmp_path / "reader.exe"
    executable.write_bytes(b"isolated binary stand-in")
    reader = HardwareReader(executable)
    monkeypatch.setattr(reader, "availability", lambda: {"available": True})
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": "read", "backend": "mx_logical_station_read_only",
            "logical_station": 2, "values": {"X0": 1}}))
    monkeypatch.setattr('gxworks2.hardware_reader.subprocess.run', run)
    assert reader.read_once(2, ["x00"], "FX3U", expected_fingerprint=reader.fingerprint(), timeout=3)["values"] == {"X0": 1}
    args, kwargs = calls[0]
    assert args == [str(executable)]
    assert json.loads(kwargs["input"]) == {"operation": "read", "protocol_version": 2, "logical_station": 2, "devices": [{"key": "X0", "device": "X0"}]}
    assert 0 < kwargs["timeout"] <= 3
    assert not kwargs.get("shell")
    with pytest.raises(HardwareError, match="目标或地址"):
        reader.read_once(3, ["X0"], "FX3U", expected_fingerprint=reader.fingerprint(), timeout=3)


def test_permission_expiring_during_durable_audit_never_calls_reader(hardware, monkeypatch):
    module, _, reader, project, version, clock = hardware
    session = proposal(hardware)
    approve(hardware, session)
    audit = module._audit
    def slow_audit(record, action, **details):
        audit(record, action, **details)
        if action == "read_requested":
            clock[0] += 61
    monkeypatch.setattr(module, "_audit", slow_audit)
    with pytest.raises(HardwareError, match="本次没有连接设备"):
        read(hardware, session)
    assert reader.calls == []
    assert module.list(project, version, "operator-a")["sessions"][0]["audit"][-1]["action"] == "read_failed"


def test_reader_fingerprint_check_cannot_extend_remaining_permission(tmp_path, monkeypatch):
    executable = tmp_path / "reader.exe"
    executable.write_bytes(b"isolated binary stand-in")
    reader = HardwareReader(executable)
    clock = [100.0]
    def slow_fingerprint():
        clock[0] += 10
        return "a" * 64
    monkeypatch.setattr(reader, "fingerprint", slow_fingerprint)
    monkeypatch.setattr('gxworks2.hardware_reader.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('gxworks2.hardware_reader.subprocess.run', lambda *args, **kwargs: pytest.fail("Expired permission must not start any process"))
    with pytest.raises(HardwareError, match="没有启动读取进程"):
        reader.read_once(2, ["X0"], "FX3U", expected_fingerprint="a" * 64, timeout=3)


def source_version(service, project, mode):
    version, output = service.store.prepare_version(project)
    name = "program.gxw" if mode == "fbd" else "program.st"
    if mode == "fbd":
        from gxw.object_model import default_baseline
        (output / name).write_bytes(default_baseline())
    else:
        (output / name).write_text("Y0 := X0;", encoding="utf-8")
    service.store.complete_version(project, version, {"target_mode": mode, "plc_model": "FX3U",
        "artifacts": {"gxw" if mode == "fbd" else "st": name}})
    return version, output / name


@pytest.mark.parametrize("mode", ["fbd", "st"])
@pytest.mark.parametrize("phase", ["approval", "read"])
def test_fbd_and_st_file_only_changes_invalidate_hardware_permission(hardware, mode, phase):
    module, service, reader, project, _, clock = hardware
    version, path = source_version(service, project, mode)
    selected = (module, service, reader, project, version, clock)
    session = proposal(selected)
    if phase == "read":
        approve(selected, session)
    before = service.projects.raw_version(project, version)
    path.write_bytes(path.read_bytes() + b"changed source bytes")
    assert service.projects.raw_version(project, version) == before
    with pytest.raises(HardwareError, match="变化"):
        if phase == "read":
            read(selected, session)
        else:
            approve(selected, session)
    assert reader.calls == []


@pytest.mark.parametrize("mode", ["fbd", "st"])
def test_hardware_permission_requires_existing_managed_source_file(hardware, mode):
    module, service, reader, project, _, clock = hardware
    version, path = source_version(service, project, mode)
    path.unlink()
    with pytest.raises(HardwareError, match="源文件缺失"):
        proposal((module, service, reader, project, version, clock))
    assert reader.calls == []


def test_no_write_or_cpu_controls_in_hardware_reader_source():
    source = Path("hardware_reader/Program.cs").read_text(encoding="utf-8")
    assert "mx.ActLogicalStationNumber = station" in source
    assert "mx.GetDevice(device, out data)" in source
    for forbidden in (".SetDevice(", ".WriteDevice", ".SetCpuStatus(", ".SetClockData(", "ActUnitType", "UNIT_SIMULATOR2", "HttpListener"):
        assert forbidden not in source


def test_operator_routes_reject_agent_tokens_and_unknown_mutating_fields(hardware, tmp_path):
    pytest.importorskip("fastapi", reason="Web integration requires requirements-web.txt")
    pytest.importorskip("httpx", reason="Web integration requires requirements-web.txt")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from integrations.web.hardware_routes import register
    module, service, reader, project, version, _ = hardware
    # create_app owns start/close only during lifespan; the fixture already owns
    # this service, so make requests without starting a second writer lease.
    origin = "http://127.0.0.1:8765"
    app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service,
                     operator_token="operator-test", agent_token="agent-test", origin=origin)
    if not any(route.path.endswith("/hardware") for route in app.routes):
        register(app, service)
    client = TestClient(app, base_url=origin)
    path = f"/api/projects/{project}/versions/{version}/hardware"
    assert client.get(path, headers={"Authorization": "Bearer agent-test"}).status_code == 401
    session = client.post("/api/session", json={"token": "operator-test"}).json()
    headers = {"Origin": origin, "X-CSRF-Token": session["csrf"]}
    response = client.post(path + "/sessions", headers=headers, json={"logical_station": 2,
        "target_label": "测试柜", "addresses": ["X0"], "ttl_seconds": 60, "write_values": {"Y0": 1}})
    assert response.status_code == 422
    assert client.post(path + "/write", headers=headers, json={"Y0": 1}).status_code in (404, 405)
    assert reader.calls == []
    command = {"logical_station": 2, "target_label": "测试柜", "addresses": ["X0"], "ttl_seconds": 60}
    created = client.post(path + "/sessions", headers=headers, json=command)
    assert created.status_code == 201, created.text
    permission = created.json()
    assert permission["status"] == "pending"
    endpoint = path + "/sessions/" + permission["id"]
    response = client.post(endpoint + "/read", headers=headers, json={"request_id": "premature", "addresses": ["X0"]})
    assert response.status_code == 400
    assert reader.calls == []
    confirmed = client.post(endpoint + "/approve", headers=headers, json={**command, "mapping_confirmed": True})
    assert confirmed.status_code == 200, confirmed.text
    assert reader.calls == []
    observed = client.post(endpoint + "/read", headers=headers, json={"request_id": "manual", "addresses": ["X0"]})
    assert observed.status_code == 200, observed.text
    assert observed.json()["values"] == {"X0": 0}
    assert len(reader.calls) == 1
