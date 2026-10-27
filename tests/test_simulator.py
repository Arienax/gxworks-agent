import json
from pathlib import Path

import pytest

from plc.ir import build_plc_ir, canonical_sha256
from application.session import ProjectSession as SessionStore
from simulator import (
    FaultInjectingBackend,
    InMemoryTestBackend,
    PLCTestRunner,
    SimulatorRegressionService,
    TestCaseValidationError as SimulatorCaseValidationError,
    normalize_test_case,
    normalize_test_suite,
)
from simulator.gateway import (
    GXSimulatorGatewayClient,
    GatewayOperationError,
    GatewayUnavailableError,
    detect_simulator_environment,
)


@pytest.mark.parametrize('fault', [None, 'snapshot', 'iteration'])
def test_process_inventory_reads_unicode_snapshot_and_never_hides_enumeration_failure(monkeypatch, fault):
    import ctypes
    import simulator.gateway as gateway
    from types import SimpleNamespace
    if gateway.os.name != 'nt':
        pytest.skip('Windows process inventory')
    state = dict(index=0, error=0, closed=[])
    names = ['GXSimulator2.exe', 'unknown�.exe', '中文进程.exe']
    def snapshot(flags, pid):
        assert (flags, pid) == (2, 0)
        state['error'] = 5
        return ctypes.c_void_p(-1).value if fault == 'snapshot' else 123
    def first(handle, pointer):
        assert handle == 123 and pointer._obj.dwSize == ctypes.sizeof(pointer._obj)
        pointer._obj.szExeFile = names[0]
        return True
    def advance(handle, pointer):
        state['index'] += 1
        if fault == 'iteration':
            state['error'] = 5
            return False
        if state['index'] == len(names):
            state['error'] = 18
            return False
        pointer._obj.szExeFile = names[state['index']]
        return True
    def close(handle):
        state['closed'].append(handle)
        return True
    kernel = SimpleNamespace(CreateToolhelp32Snapshot=snapshot,
        Process32FirstW=first, Process32NextW=advance, CloseHandle=close)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: state['error'])
    if fault:
        with pytest.raises(OSError):
            gateway._running_processes()
    else:
        assert gateway._running_processes() == ['gxsimulator2.exe', 'unknown�.exe', '中文进程.exe']
    assert state['closed'] == ([] if fault == 'snapshot' else [123])


def _case(*, steps, initial=None, invariants=None, faults=None, timeout_ms=500):
    return {
        "name": "fixture",
        "plc_model": "FX3U",
        "initial": initial or {},
        "steps": steps,
        "invariants": invariants or [],
        "fault_injections": faults or [],
        "sample_ms": 5,
        "timeout_ms": timeout_ms,
    }


@pytest.mark.parametrize("address", ["Y0", "T0", "C0", "S0", "M8000", "D8010"])
def test_dsl_rejects_output_timer_counter_state_and_special_writes(address):
    with pytest.raises(SimulatorCaseValidationError, match="writes|special"):
        normalize_test_case(
            _case(initial={address: 0}, steps=[{"at_ms": 0, "set": {address: 1}}])
        )


@pytest.mark.parametrize(
    ("address", "value"),
    [("X0", 2), ("M0", -1), ("D0", 65536), ("D0", 1.5)],
)
def test_dsl_enforces_plc_input_value_ranges(address, value):
    with pytest.raises(SimulatorCaseValidationError):
        normalize_test_case(
            _case(initial={address: value}, steps=[{"at_ms": 0, "set": {address: value}}])
        )


def test_dsl_requires_explicit_initial_values_for_all_stimulus_devices():
    with pytest.raises(SimulatorCaseValidationError, match="must define.*X0"):
        normalize_test_case(_case(steps=[{"at_ms": 10, "set": {"X0": 1}}]))


def test_dsl_rejects_unordered_steps_duplicate_faults_and_mixed_models():
    with pytest.raises(SimulatorCaseValidationError, match="nondecreasing"):
        normalize_test_case(
            _case(
                initial={"X0": 0},
                steps=[
                    {"at_ms": 20, "set": {"X0": 1}},
                    {"at_ms": 10, "set": {"X0": 0}},
                ],
            )
        )
    with pytest.raises(SimulatorCaseValidationError, match="one fault"):
        normalize_test_case(
            _case(
                initial={"X0": 0},
                steps=[{"at_ms": 0, "expect": {"Y0": 0}}],
                faults=[
                    {"type": "stuck_on", "device": "X0", "at_ms": 0},
                    {"type": "stuck_off", "device": "X0", "at_ms": 10},
                ],
            )
        )
    with pytest.raises(SimulatorCaseValidationError, match="suite PLC model"):
        normalize_test_suite(
            {
                "name": "mixed",
                "plc_model": "FX3U",
                "tests": [
                    {
                        **_case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}]),
                        "plc_model": "FX5U",
                    }
                ],
            }
        )


def test_runner_executes_steps_waits_and_captures_full_device_trace():
    def logic(backend, _milliseconds):
        if backend.now_ms >= 25 and backend.values.get("X0"):
            backend.values["Y0"] = 1
            backend.values["D0"] = 42

    backend = InMemoryTestBackend(on_advance=logic)
    result = PLCTestRunner(backend).run(
        _case(
            initial={"X0": 0, "D0": 0},
            steps=[
                {"at_ms": 10, "set": {"X0": 1}},
                {
                    "at_ms": 10,
                    "wait_for": {"Y0": 1},
                    "timeout_ms": 50,
                    "poll_ms": 5,
                },
                {"at_ms": 30, "expect": {"D0": 42}},
            ],
            timeout_ms=100,
        )
    )
    assert result["status"] == "passed"
    assert all(item["passed"] for item in result["assertions"])
    samples = [item for item in result["trace"] if item["event"].endswith("sample")]
    assert samples
    assert {"X0", "Y0", "D0"}.issubset(samples[-1]["values"])


def test_runner_emits_live_test_step_write_and_assertion_progress():
    updates = []

    def logic(backend, values):
        if "X0" in values:
            backend.values["Y0"] = values["X0"]

    result = PLCTestRunner(
        InMemoryTestBackend(on_write=logic),
        progress=updates.append,
    ).run_suite(
        {
            "name": "live progress",
            "plc_model": "FX3U",
            "tests": [
                _case(
                    initial={"X0": 0},
                    steps=[
                        {"at_ms": 10, "set": {"X0": 1}},
                        {"at_ms": 20, "expect": {"Y0": 1}},
                    ],
                )
            ],
        }
    )

    assert result["status"] == "passed"
    events = [item["event"] for item in updates]
    assert "test_started" in events
    assert "step_started" in events
    assert "device_write" in events
    assert "assertion" in events
    assert "test_completed" in events
    assert updates[-1]["event"] == "suite_completed"
    assert updates[-1]["percent"] == 100
    assertion = next(item for item in updates if item["event"] == "assertion")
    assert assertion["address"] == "Y0"
    assert assertion["actual"] == 1
    assert assertion["passed"] is True


def test_runner_without_invariants_samples_at_step_boundaries_not_every_tick():
    class AdvanceCountingBackend(InMemoryTestBackend):
        def __init__(self):
            super().__init__()
            self.advances = []

        def advance_ms(self, milliseconds):
            self.advances.append(milliseconds)
            super().advance_ms(milliseconds)

    backend = AdvanceCountingBackend()
    result = PLCTestRunner(backend).run(
        _case(
            initial={"X0": 0},
            steps=[
                {"at_ms": 100, "set": {"X0": 1}},
                {"at_ms": 200, "expect": {"Y0": 0}},
            ],
            timeout_ms=300,
        )
    )

    assert result["status"] == "passed"
    assert backend.advances == [100, 100]


def test_runner_resets_capable_simulator_cpu_before_each_test_case():
    class ResettingBackend(InMemoryTestBackend):
        supports_cpu_reset = True

        def __init__(self):
            super().__init__(on_write=self._logic)
            self.reset_count = 0

        @staticmethod
        def _logic(backend, values):
            if values.get("X0") == 1:
                backend.values["Y0"] = 1

        def reset_cpu(self, devices=(), initial_values=None):
            self.reset_count += 1
            self.values.clear()
            self.values.update(initial_values or {})
            return {
                "reset": True,
                "cpu_run": True,
                "run_monitor": 1,
                "cleared_devices": list(devices),
                "initial_values": dict(initial_values or {}),
            }

    backend = ResettingBackend()
    updates = []
    result = PLCTestRunner(backend, progress=updates.append).run_suite(
        {
            "name": "isolated cases",
            "plc_model": "FX3U",
            "tests": [
                {
                    **_case(
                        initial={"X0": 0},
                        steps=[
                            {"at_ms": 10, "set": {"X0": 1}},
                            {"at_ms": 20, "expect": {"Y0": 1}},
                        ],
                    ),
                    "name": "latch output",
                },
                {
                    **_case(
                        initial={"X0": 0},
                        steps=[{"at_ms": 10, "expect": {"Y0": 0}}],
                    ),
                    "name": "clean next case",
                },
            ],
        }
    )

    assert result["status"] == "passed"
    assert backend.reset_count == 2
    assert len([item for item in updates if item["event"] == "cpu_reset"]) == 2


def test_real_backend_capability_captures_fx3u_scan_monitor_without_writes():
    backend = InMemoryTestBackend(
        initial={"D8010": 68, "D8011": 59, "D8012": 144}
    )
    backend.supports_scan_monitor = True
    result = PLCTestRunner(backend).run(
        _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}])
    )

    monitor = result["scan_monitor"]
    assert monitor["sampled"] is True
    assert monitor["latest_current_ms"] == 6.8
    assert monitor["observed_minimum_ms"] == 5.9
    assert monitor["observed_maximum_ms"] == 14.4
    assert monitor["warning_exceeded"] is False
    samples = [item for item in result["trace"] if item["event"].endswith("sample")]
    assert samples[-1]["scan_monitor"]["raw"] == {
        "D8010": 68,
        "D8011": 59,
        "D8012": 144,
    }


def test_scan_monitor_is_batched_with_each_trace_sample():
    class CountingBackend(InMemoryTestBackend):
        supports_scan_monitor = True

        def __init__(self):
            super().__init__(initial={"D8010": 1, "D8011": 1, "D8012": 1})
            self.read_calls = []

        def read_many(self, addresses):
            self.read_calls.append(tuple(addresses))
            return super().read_many(addresses)

    backend = CountingBackend()
    result = PLCTestRunner(backend).run(
        _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}])
    )
    trace_samples = [
        item for item in result["trace"] if item["event"].endswith("sample")
    ]
    sample_reads = [
        call for call in backend.read_calls if {"D8010", "D8011", "D8012"}.issubset(call)
    ]
    assert len(sample_reads) == len(trace_samples)
    assert all("Y0" in call for call in sample_reads)


def test_scan_monitor_read_failure_falls_back_without_failing_control_test():
    class UnsupportedMonitorBackend(InMemoryTestBackend):
        supports_scan_monitor = True

        def read_many(self, addresses):
            if "D8010" in addresses:
                raise RuntimeError("D8010 unsupported by this simulator edition")
            return super().read_many(addresses)

    result = PLCTestRunner(UnsupportedMonitorBackend()).run(
        _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}])
    )
    assert result["status"] == "passed"
    assert result["scan_monitor"]["sampled"] is False
    assert "D8010 unsupported" in result["scan_monitor"]["error"]
    samples = [item for item in result["trace"] if item["event"].endswith("sample")]
    assert samples
    assert "D8010 unsupported" in samples[0]["scan_monitor_error"]


def test_memory_backend_does_not_fabricate_scan_monitor_values():
    result = PLCTestRunner(InMemoryTestBackend()).run(
        _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}])
    )
    assert result["scan_monitor"]["supported"] is True
    assert result["scan_monitor"]["backend_capable"] is False
    assert result["scan_monitor"]["sampled"] is False
    assert all("scan_monitor" not in item for item in result["trace"])


def test_invariants_find_mutex_maximum_on_minimum_off_sequence_and_state_faults():
    result = PLCTestRunner(InMemoryTestBackend()).run(
        _case(
            initial={"X0": 0, "X1": 0, "D0": 0},
            steps=[
                {"at_ms": 5, "set": {"X0": 1}},
                {"at_ms": 10, "set": {"X1": 1, "D0": 9}},
            ],
            invariants=[
                {"type": "mutual_exclusion", "devices": ["X0", "X1"]},
                {"type": "maximum_on_time", "device": "X0", "duration_ms": 2},
                {"type": "minimum_off_time", "device": "X0", "duration_ms": 10},
                {"type": "sequence_constraint", "devices": ["X1", "X0"]},
                {"type": "state_constraint", "device": "D0", "allowed": [0, 1]},
            ],
            timeout_ms=20,
        )
    )
    assert result["status"] == "failed"
    assert {item["type"] for item in result["invariant_violations"]} == {
        "mutual_exclusion",
        "maximum_on_time",
        "minimum_off_time",
        "sequence_constraint",
        "state_constraint",
    }
    assert len(result["invariant_violations"]) == 5


@pytest.mark.parametrize(
    ("fault", "expected_at_20", "expected_at_40"),
    [
        ({"type": "stuck_on", "device": "X0", "at_ms": 10}, 1, 1),
        ({"type": "stuck_off", "device": "X0", "at_ms": 10}, 0, 0),
        ({"type": "drop_signal", "device": "X0", "at_ms": 10, "duration_ms": 20}, 0, 1),
    ],
)
def test_faults_change_the_wrapped_backend_not_only_observed_values(
    fault, expected_at_20, expected_at_40
):
    backend = InMemoryTestBackend(initial={"X0": 1})
    wrapped = FaultInjectingBackend(backend, [fault])
    wrapped.connect()
    wrapped.advance_ms(20)
    assert backend.values["X0"] == expected_at_20
    wrapped.advance_ms(20)
    assert backend.values["X0"] == expected_at_40


def test_signal_delay_and_bounce_are_deterministic():
    delayed_backend = InMemoryTestBackend(initial={"X0": 0})
    delayed = FaultInjectingBackend(
        delayed_backend,
        [{"type": "signal_delay", "device": "X0", "at_ms": 0, "delay_ms": 10}],
    )
    delayed.connect()
    delayed.write_many({"X0": 1})
    assert delayed_backend.values["X0"] == 0
    delayed.advance_ms(10)
    assert delayed_backend.values["X0"] == 1

    bounced_backend = InMemoryTestBackend(initial={"X0": 0})
    bounced = FaultInjectingBackend(
        bounced_backend,
        [
            {
                "type": "signal_bounce",
                "device": "X0",
                "at_ms": 0,
                "duration_ms": 10,
                "interval_ms": 2,
            }
        ],
    )
    bounced.connect()
    bounced.write_many({"X0": 1})
    bounced.advance_ms(10)
    assert bounced_backend.values["X0"] == 1


def test_fault_wrapper_restores_original_device_values_on_disconnect():
    backend = InMemoryTestBackend(initial={"X0": 0, "X1": 1})
    wrapped = FaultInjectingBackend(
        backend,
        [
            {"type": "stuck_on", "device": "X0", "at_ms": 0},
            {"type": "stuck_off", "device": "X1", "at_ms": 0},
        ],
    )

    wrapped.connect()
    assert backend.values == {"X0": 1, "X1": 0}
    wrapped.disconnect()

    assert backend.values == {"X0": 0, "X1": 1}
    assert not backend.connected


def test_unavailable_gateway_aborts_remaining_suite(monkeypatch):
    class OfflineBackend:
        backend_kind = "offline"

        def connect(self):
            raise RuntimeError("GX Simulator2 gateway unavailable")

        def disconnect(self):
            pass

    suite = {
        "name": "offline",
        "tests": [
            _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}]),
            {**_case(steps=[{"at_ms": 0, "expect": {"Y1": 0}}]), "name": "second"},
        ],
    }
    result = PLCTestRunner(OfflineBackend()).run_suite(suite)
    assert result["status"] == "unavailable"
    assert result["attempted_count"] == 1
    assert result["executed_count"] == 0
    assert result["not_executed_count"] == 2


@pytest.mark.parametrize(
    "failure",
    [
        GatewayOperationError(
            "Unknown gateway endpoint. [NOT_FOUND]",
            status=404,
            code="NOT_FOUND",
        ),
        GatewayUnavailableError("GX Simulator2 gateway unavailable: timed out"),
    ],
)
def test_gateway_setup_failure_aborts_suite_without_claiming_execution(failure):
    class BrokenResetBackend(InMemoryTestBackend):
        supports_cpu_reset = True

        def reset_cpu(self, devices=(), initial_values=None):
            raise failure

    suite = {
        "name": "gateway setup failure",
        "tests": [
            _case(steps=[{"at_ms": 0, "expect": {"Y0": 0}}]),
            {**_case(steps=[{"at_ms": 0, "expect": {"Y1": 0}}]), "name": "second"},
        ],
    }

    result = PLCTestRunner(BrokenResetBackend()).run_suite(suite)

    assert result["status"] == "unavailable"
    assert result["attempted_count"] == 1
    assert result["executed_count"] == 0
    assert result["not_executed_count"] == 2
    assert len(result["results"]) == 1
    assert result["results"][0]["environment_failure"] is True
    assert result["results"][0]["execution_started"] is False
    assert result["results"][0]["setup_stage"] == "cpu_reset"


def test_gateway_client_rejects_non_loopback_urls():
    with pytest.raises(ValueError, match="loopback"):
        GXSimulatorGatewayClient("http://192.0.2.10:17831")


def test_environment_detection_reports_evidence_without_claiming_route(monkeypatch):
    monkeypatch.setattr("simulator.gateway._running_processes", lambda: [])
    monkeypatch.setattr("simulator.gateway._registry_progid_exists", lambda _name: True)
    monkeypatch.setenv("GX_SIMULATOR_LOGICAL_STATION", "10")
    result = detect_simulator_environment()
    assert result["mx_component_progids"]
    assert not result["ready_for_gateway"]


def test_environment_detection_recognizes_real_fx_simulator_process_names(monkeypatch):
    monkeypatch.setattr(
        "simulator.gateway._running_processes",
        lambda: ["fxsimrun2.exe", "simmanager.exe", "gd2.exe"],
    )
    monkeypatch.setattr(
        "simulator.gateway._registry_progid_exists", lambda _name: True
    )

    result = detect_simulator_environment()

    assert result["simulator_processes"] == ["fxsimrun2.exe", "simmanager.exe"]
    assert result["ready_for_gateway"] is True


def test_environment_detection_does_not_count_its_own_gateway_as_simulator(monkeypatch):
    monkeypatch.setattr(
        "simulator.gateway._running_processes",
        lambda: ["plcai.gxsimulator2gateway.exe", "gd2.exe"],
    )
    monkeypatch.setattr(
        "simulator.gateway._registry_progid_exists", lambda _name: True
    )

    result = detect_simulator_environment()

    assert result["simulator_processes"] == []
    assert result["ready_for_gateway"] is False


def test_environment_detection_finds_simulator_beside_gxworks2(monkeypatch, tmp_path):
    gx_root = tmp_path / "GPPW2"
    gx_executable = gx_root / "GD2.exe"
    simulator = gx_root / "GX Simulator2" / "FXCPU" / "FXSimRun2.exe"
    simulator.parent.mkdir(parents=True)
    gx_executable.write_bytes(b"")
    simulator.write_bytes(b"")
    monkeypatch.setenv("GXWORKS2_EXE", str(gx_executable))
    monkeypatch.delenv("GX_SIMULATOR2_EXE", raising=False)
    monkeypatch.setattr("simulator.gateway._running_processes", lambda: [])
    monkeypatch.setattr(
        "simulator.gateway._registry_progid_exists", lambda _name: False
    )

    result = detect_simulator_environment()

    assert str(simulator.resolve()) in result["simulator_executables"]
    assert result["simulator_installed"] is True
    assert result["mx_component_installed"] is False
    assert result["ready_for_gateway"] is False


def _rung(rung_id, input_address, output_address):
    return {
        "rung_id": rung_id,
        "debug_note": "fixture",
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": input_address, "label": ""}],
                "outputs": [{"type": "COIL", "address": output_address, "label": ""}],
            }
        ],
    }


def test_version_bound_regression_persists_suite_and_trace(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="sim", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    ladder = {"device_comments": {}, "rungs": [_rung(10, "X0", "Y0")]}
    program = build_plc_ir(ladder, revision=1)
    store._write_json(version_dir / "ladder.json", ladder)
    store._write_json(version_dir / "program.ir.json", program)
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "revision": 1,
            "ir_sha256": canonical_sha256(program),
            "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
        },
    )

    def logic(backend, values):
        if "X0" in values:
            backend.values["Y0"] = values["X0"]

    suite = {
        "name": "motor",
        "tests": [
            _case(
                initial={"X0": 0},
                steps=[
                    {"at_ms": 10, "set": {"X0": 1}},
                    {"at_ms": 10, "expect": {"Y0": 1}},
                ],
            )
        ],
    }
    execution = SimulatorRegressionService(
        store,
        backend=InMemoryTestBackend(on_write=logic),
    ).run_version_suite(project["id"], version_id, suite)
    assert execution["result"]["status"] == "passed"
    record = execution["record"]
    assert record["ir_sha256"] == canonical_sha256(program)
    assert (version_dir / record["suite_artifact"]).is_file()
    assert (version_dir / record["trace_artifact"]).is_file()
    loaded = store.load_simulator_run(project["id"], version_id, record["run_id"])
    assert loaded["binding"]["version_id"] == version_id
    assert loaded["result"]["status"] == "passed"
    assert store.get_version(project["id"], version_id)["last_simulator_status"] == "passed"


def test_gateway_source_has_simulator_route_and_build_stays_outside_repo():
    root = Path(__file__).resolve().parents[1]
    source = (root / "simulator_gateway" / "Program.cs").read_text(encoding="utf-8")
    build_script = (root / "tools" / "build_simulator_gateway.ps1").read_text(
        encoding="utf-8"
    )
    release_script = (root / "scripts" / "build_web_package.ps1").read_text(
        encoding="utf-8"
    )
    assert "UnitSimulator2 = 0x30" in source
    assert "control.ActUnitType = UnitSimulator2" in source
    assert "ActLogicalStationNumber" not in source
    policy = (root / "src/plc/device_policy.py").read_text(encoding="utf-8")
    assert '_STIMULUS_PREFIXES = frozenset({"X", "M", "D"})' in policy
    assert 'CpuStatus(3, "MX_CPU_RESET_FAILED")' in source
    assert 'CpuStatus(0, "MX_CPU_RUN_FAILED")' in source
    assert source.index(
        'WriteCalls(initial, "MX_INITIALIZE_FAILED")'
    ) < source.index('CpuStatus(3, "MX_CPU_RESET_FAILED")')
    assert 'request.Path == "/cpu/reset"' in source
    assert "must be built outside the project directory" in build_script
    assert 'build_release.py' in release_script
    release_builder = (root / 'scripts/build_release.py').read_text(encoding='utf-8')
    assert "'build_simulator_gateway.ps1', 'simulator-gateway'" in release_builder
    assert 'GX_RELEASE_HELPERS' in release_builder
    spec = (root / "packaging/pyinstaller/release.spec").read_text(encoding="utf-8")
    assert "('simulator-gateway', 'PlcAi.GxSimulator2Gateway.exe')" in spec


@pytest.mark.parametrize("fault", [None, "physical", "reconnected", "wrong_response", "missing_value", "bool_value"])
def test_bound_read_is_read_only_and_rejects_wrong_or_changed_simulator_target(monkeypatch, fault):
    from simulator.gateway import GatewayError
    from plc.device_policy import native_read_plan
    client = GXSimulatorGatewayClient()
    requests = []
    health = {"simulator_only": fault != "physical", "route": "GX Simulator2", "unit_type": 0x30,
              "connected": True, "target_binding": "other" if fault == "reconnected" else "connection-1"}
    def request(method, path, payload=None):
        requests.append((method, path, payload))
        if path == "/health":
            return health
        assert path == "/observations/read"
        assert payload["target_binding"] == "connection-1"
        return {"simulator_only": True, "route": "GX Simulator2",
                "target_binding": "other" if fault == "wrong_response" else "connection-1",
                "values": {} if fault == "missing_value" else {"X0": True if fault == "bool_value" else 0, "D0": 7}}
    monkeypatch.setattr(client, "_request", request)
    if fault:
        with pytest.raises(GatewayError):
            client.read_bound(native_read_plan(["X0", "D0"]), "connection-1")
    else:
        assert client.read_bound(native_read_plan(["X0", "D0"]), "connection-1") == {"X0": 0, "D0": 7}
    assert all(path in {"/health", "/observations/read"} for _, path, _ in requests)
    assert client.connected is False


def test_simulator_observation_service_uses_core_policy_and_keeps_program_correspondence_unverified():
    from application.program_inspection import ProgramInspectionService
    from agent_runtime.tool_errors import ToolPreconditionError
    class Gateway:
        def __init__(self):
            self.plans = []
        def health(self):
            return {"simulator_only": True, "route": "GX Simulator2", "unit_type": 0x30,
                    "connected": True, "target_binding": "connection-1"}
        def read_bound(self, plan, binding):
            assert binding == "connection-1"
            self.plans.append(plan)
            return {item["key"]: 1 if item["key"] == "M8000" else 0 for item in plan}
    gateway = Gateway()
    service = ProgramInspectionService(gateway=gateway)
    target = service.list_simulator_targets()["targets"][0]["target_ref"]
    result = service.get_simulator_status(target)
    assert result["run_state"] == "run"
    assert result["program_correspondence"] == "unverified"
    read = service.read_simulator_devices(target, ["X0", "S0", "D0"])
    assert read["values"] == {"X0": 0, "S0": 0, "D0": 0}
    assert read["evidence_kind"] == "runtime_device_observation" and not read["physical_plc"]
    count = len(gateway.plans)
    for addresses in (["X8"], [], ["D0"] * 65, ["D0-D9"], ["M8000", "invalid"]):
        with pytest.raises(ToolPreconditionError) as error:
            service.read_simulator_devices(target, addresses)
        assert error.value.code == "INVALID_ARGUMENTS"
    assert len(gateway.plans) == count


def test_simulator_discovery_reports_unavailable_without_starting_or_connecting():
    from application.program_inspection import ProgramInspectionService
    class Gateway:
        def health(self):
            raise GatewayUnavailableError("not running")
    result = ProgramInspectionService(gateway=Gateway()).list_simulator_targets()
    assert result["available"] is False and result["targets"] == []
    assert result["runtime_verified"] is False


@pytest.mark.parametrize('cpu,native_code,model_code,monitor', [
    ('FX3U',0x208,0xF3,'M8000'),('FX3G',0x209,0xF4,'M8000'),('FX3S',0x20A,0xF5,'M8000'),
    ('Q00U',0x81,0x261,'SD203'),('Q03UDV',0xD1,0x366,'SD203'),('Q06UDEH',0x92,0x26A,'SD203'),
    ('L02',0xA1,0x541,'SD203'),('L06',0xA5,0x544,'SD203'),('L26',0xA4,0x545,'SD203'),
])
def test_native_simulator_profiles_use_independent_sdk_cpu_codes(cpu,native_code,model_code,monitor):
    from plc.device_policy import native_simulator_profile, native_simulator_read_plan, native_write_plan
    profile = native_simulator_profile(cpu)
    assert profile['cpu_type'] == native_code and profile['device'] == monitor
    assert profile['model_code'] == model_code and model_code != native_code
    assert profile['unit_type'] == 0x30 and profile['target_simulator'] == 0
    assert native_simulator_read_plan([monitor],cpu) == [{'key':monitor,'device':monitor}]
    for special in profile['read_devices']:
        with pytest.raises(ValueError): native_write_plan({special:0},cpu)


def test_native_simulator_scope_does_not_admit_other_models_or_special_devices():
    from plc.device_policy import native_simulator_profile, native_simulator_read_plan
    for cpu in ('Q04UDV','L02S','FX1N','FX5U','unknown'):
        with pytest.raises(ValueError): native_simulator_profile(cpu)
    for address in ('SD1','SM1','SD203.0','SD0-D9','SM0;D0'):
        with pytest.raises(ValueError): native_simulator_read_plan([address],'Q06UDEH')


def test_ql_simulator_run_state_ignores_stop_cause_bits_and_rejects_unknown_values():
    from hypothesis import given,settings,strategies as st
    from plc.device_policy import simulator_cpu_is_running
    @settings(max_examples=64,derandomize=True,deadline=None)
    @given(cause=st.integers(min_value=0,max_value=4095),status=st.sampled_from([0,2,3]))
    def invariant(cause,status):
        for cpu in ('Q00U','Q03UDV','Q06UDEH','L02','L06','L26'):
            assert simulator_cpu_is_running(cause*16+status,cpu) is (status==0)
    invariant()
    for value in (None,False,True,0.0,'0'):
        assert simulator_cpu_is_running(value,'L06') is False


@pytest.mark.parametrize('cpu,native_code,model_code', [('FX3G',0x209,0xF4),('Q03UDV',0xD1,0x366),('L06',0xA5,0x544)])
@pytest.mark.parametrize('boundary', ['matched','different_cpu','setting_used_as_model','physical_route','different_simulator'])
def test_gateway_connection_attests_core_selected_cpu_before_device_access(monkeypatch,cpu,native_code,model_code,boundary):
    from simulator.gateway import REQUIRED_GATEWAY_CAPABILITIES, GatewayOperationError
    client = GXSimulatorGatewayClient(plc_model=cpu)
    calls=[]
    monkeypatch.setattr(client,'health',lambda:dict(simulator_only=True,protocol_version=3,
        capabilities={**dict.fromkeys(REQUIRED_GATEWAY_CAPABILITIES,True),'target_cpu_binding':True,'model_code_binding':True}))
    def request(method,path,payload):
        calls.append((method,path,payload))
        if path=='/disconnect':return {'connected':False}
        assert payload=={'protocol_version':3,'cpu_type':native_code,'model_code':model_code}
        return dict(cpu_type=native_code, observed_cpu_type=(model_code+1 if boundary=='different_cpu'
                    else native_code if boundary=='setting_used_as_model' else model_code),
                    unit_type=0x13 if boundary=='physical_route' else 0x30,
                    target_simulator=1 if boundary=='different_simulator' else 0)
    monkeypatch.setattr(client,'_request',request)
    if boundary=='matched':
        client.connect()
        assert client.connected and len(calls)==1
    else:
        with pytest.raises(GatewayOperationError,match='selected target'):client.connect()
        assert not client.connected and [row[1] for row in calls]==['/connect','/disconnect']


def test_non_fx_gateway_requires_cpu_attestation_before_connect(monkeypatch):
    from simulator.gateway import REQUIRED_GATEWAY_CAPABILITIES, GatewayProtocolError
    client=GXSimulatorGatewayClient(plc_model='L02')
    monkeypatch.setattr(client,'health',lambda:dict(simulator_only=True,protocol_version=3,
        capabilities=dict.fromkeys(REQUIRED_GATEWAY_CAPABILITIES,True)))
    monkeypatch.setattr(client,'_request',lambda *_:pytest.fail('No connect without exact CPU capability'))
    with pytest.raises(GatewayProtocolError,match='cannot attest'):client.connect()


@pytest.mark.parametrize("kind,value,words", [
    ("INT", -32768, [0x8000]), ("UINT", 65535, [0xffff]),
    ("DINT", -2147483648, [0, 0x8000]), ("UDINT", 4294967295, [0xffff, 0xffff]),
    ("REAL", 1.0, [0, 0x3f80]), ("REAL", -2.5, [0, 0xc020]), ("BOOL", True, [1]),
])
def test_typed_signal_storage_known_independent_words(kind, value, words):
    from simulator.signals import decode_signal, encode_signal
    assert encode_signal(value, kind) == words
    assert decode_signal(words, kind) == value


def test_typed_signal_properties_over_integer_and_float32_domains():
    from hypothesis import given, settings, strategies as st
    from simulator.signals import decode_signal, encode_signal

    @settings(max_examples=64, derandomize=True, deadline=None)
    @given(value=st.integers(min_value=-(2**31), max_value=2**31 - 1))
    def signed(value):
        words = encode_signal(value, "DINT")
        # Independent integer byte representation also fixes word order.
        expected = value.to_bytes(4, "little", signed=True)
        assert bytes([words[0] & 255, words[0] >> 8, words[1] & 255, words[1] >> 8]) == expected
        assert decode_signal(words, "DINT") == value

    @settings(max_examples=64, derandomize=True, deadline=None)
    @given(value=st.floats(width=32, allow_nan=False, allow_infinity=False))
    def real(value):
        assert decode_signal(encode_signal(value, "REAL"), "REAL") == value
    signed()
    real()


@pytest.mark.parametrize("signals", [
    [{"id": "x", "address": "X8", "data_type": "BOOL"}],
    [{"id": "x", "address": "D7999", "data_type": "DINT", "role": "input"}],
    [{"id": "x", "address": "Y0", "data_type": "BOOL", "role": "input"}],
    [{"id": "x", "address": "M0", "data_type": "REAL"}],
    [{"id": "x", "address": "D0", "data_type": "BOOL"}],
])
def test_typed_view_reuses_device_policy_before_any_io(signals):
    from simulator.signals import normalize_scenario
    with pytest.raises(ValueError):
        normalize_scenario({"signals": signals})


@pytest.mark.parametrize("numeric", [False, True])
def test_same_typed_scenario_runner_handles_independent_boolean_and_numeric_cases(numeric):
    from simulator.signals import normalize_scenario
    if numeric:
        signals = [
            {"id": "input", "address": "D100", "data_type": "REAL", "role": "input"},
            {"id": "output", "address": "D200", "data_type": "REAL", "role": "output"},
            {"id": "integer", "address": "D300", "data_type": "DINT", "role": "input"},
            {"id": "copy", "address": "D400", "data_type": "DINT", "role": "output"},
        ]
        initial = {"input": 0, "integer": 0}
        steps = [{"at_ms": 100, "set": {"input": -2.5, "integer": -2147483648}},
                 {"at_ms": 200, "expect": {"output": -2.5, "copy": -2147483648}}]
        def copy_values(backend, _values):
            for dst, src in ((200, 100), (201, 101), (400, 300), (401, 301)):
                backend.values[f"D{dst}"] = backend.values.get(f"D{src}", 0)
    else:
        signals = [{"id": "start", "address": "X0", "data_type": "BOOL", "role": "input"},
                   {"id": "motor", "address": "Y0", "data_type": "BOOL", "role": "output"}]
        initial = {"start": False}
        steps = [{"at_ms": 100, "set": {"start": True}}, {"at_ms": 200, "expect": {"motor": True}}]
        def copy_values(backend, _values):
            backend.values["Y0"] = backend.values.get("X0", 0)
    config, suite = normalize_scenario({"signals": signals, "initial": initial, "steps": steps,
                                        "duration_ms": 300, "sample_ms": 50})
    assert normalize_test_suite(suite) == suite
    from simulator.observation import SimulationObservation
    view = SimulationObservation(config, source={"kind": "test_double"})
    result = PLCTestRunner(InMemoryTestBackend(on_write=copy_values), progress=view.progress).run_suite(suite)
    assert result["passed"]
    state = view.snapshot()
    assert state["sample_count"] >= 7
    assert len([event for event in state["events"] if event["kind"] == "assertion"]) == (2 if numeric else 1)
    assert any(sample["values"].get("output") == -2.5 for sample in state["samples"]) if numeric else any(sample["values"].get("motor") is True for sample in state["samples"])


def test_read_only_view_is_bounded_stoppable_and_exposes_target_change(tmp_path):
    import time
    from application.program_inspection import ProgramInspectionService
    from application.simulator_view import SimulationViewService
    from simulator.gateway import GatewayOperationError
    (tmp_path / "simulator.html").write_text("<html>view</html>")
    class Gateway:
        calls = 0
        changed = False
        def health(self):
            return {"simulator_only": True, "route": "GX Simulator2", "unit_type": 48,
                    "connected": True, "target_binding": "one"}
        def read_bound(self, plan, binding):
            self.calls += 1
            assert binding == "one"
            if self.changed:
                raise GatewayOperationError("target changed", code="STALE_SIMULATOR_TARGET")
            return {item["key"]: self.calls % 2 for item in plan}
    gateway = Gateway()
    inspection = ProgramInspectionService(gateway=gateway)
    target = inspection.list_simulator_targets()["targets"][0]["target_ref"]
    config = {"signals": [{"id": "x", "address": "X0", "data_type": "BOOL"}], "duration_ms": 500, "sample_ms": 50}
    views = SimulationViewService(static_dir=tmp_path)
    try:
        first = views.watch(inspection, target, config)
        assert first["sample_count"] == 1 and first["status"] == "recording"
        stopped = views.stop(first["view_ref"])
        count = gateway.calls
        time.sleep(.07)
        assert gateway.calls == count and stopped["status"] == "stopped"
        second = views.watch(inspection, target, config)
        gateway.changed = True
        deadline = time.monotonic() + 1
        while views.get(second["view_ref"]).status == "recording" and time.monotonic() < deadline:
            time.sleep(.02)
        state = views.get(second["view_ref"]).snapshot()
        assert state["status"] == "error" and "changed" in state["error"]
        assert state["sample_count"] == 1 and state["source"]["program_correspondence"] == "unverified"
    finally:
        views.close()


def test_generic_view_accepts_a_wide_project_with_existing_gateway_limit():
    from simulator.signals import normalize_scenario
    from simulator.models import MAX_TRACE_DEVICES
    signals = [{"id": f"word_{i}", "address": f"D{i}", "data_type": "WORD"} for i in range(128)]
    config, suite = normalize_scenario({"signals": signals})
    assert len(config["signals"]) == len(suite["tests"][0]["trace_devices"]) == 128
    assert MAX_TRACE_DEVICES == 256


def test_connect_simulator_preserves_native_failure_code_without_starting_a_test(monkeypatch):
    from application.program_inspection import ProgramInspectionService
    from agent_runtime.tool_errors import ToolPreconditionError
    import simulator.runtime
    class Runtime:
        def ensure_gateway(self):
            return {}
        def client(self, **_kwargs):
            return self
        def health(self):
            return {"connected": False}
        def connect(self):
            raise GatewayOperationError("MX 0x01809001", code="SIMULATOR_CONNECT_FAILED")
    monkeypatch.setattr(simulator.runtime, "get_simulator_gateway_runtime", lambda: Runtime())
    with pytest.raises(ToolPreconditionError) as error:
        ProgramInspectionService().connect_simulator()
    assert error.value.code == "SIMULATOR_CONNECT_FAILED" and "0x01809001" in str(error.value)


def test_native_timer_counter_values_are_counts_and_bool_aliases_cannot_overlap_writes():
    from simulator.signals import normalize_scenario, suite_view_config, decode_values
    from plc.device_policy import native_read_value_kind
    assert native_read_value_kind("T0") == native_read_value_kind("C0") == "integer"
    with pytest.raises(ValueError, match="bit device"):
        normalize_scenario({"signals": [{"id": "count", "address": "C0", "data_type": "BOOL"}]})
    suite = normalize_test_suite({"tests": [{"name": "counter", "steps": [{"at_ms": 0, "expect": {"C0": 500}}]}]})
    signal = suite_view_config(suite)["signals"][0]
    assert signal["data_type"] == "RAW"
    assert decode_values([signal], {"C0": 500}) == ({"C0": 500}, {})
    with pytest.raises(ValueError, match="overlap"):
        normalize_scenario({"signals": [
            {"id": "one", "address": "M01", "data_type": "BOOL", "role": "input"},
            {"id": "two", "address": "M1", "data_type": "BOOL", "role": "input"}],
            "initial": {"one": False, "two": True}})
