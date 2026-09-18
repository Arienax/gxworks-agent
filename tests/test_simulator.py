import json
from pathlib import Path

import pytest

from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
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
    release_script = (root / "tools" / "build_release.ps1").read_text(
        encoding="utf-8"
    )
    assert "UnitSimulator2 = 0x30" in source
    assert "control.ActUnitType = UnitSimulator2" in source
    assert "ActLogicalStationNumber" not in source
    assert 'prefix != "X" && prefix != "M" && prefix != "D"' in source
    assert '_control.SetCpuStatus(3)' in source
    assert '_control.SetCpuStatus(0)' in source
    assert source.index(
        "foreach (KeyValuePair<string, object> item in rawInitialValues)"
    ) < source.index("_control.SetCpuStatus(3)")
    assert 'request.Path == "/cpu/reset"' in source
    assert "must be built outside the project directory" in build_script
    assert 'Join-Path $bundleRoot "simulator-gateway"' in release_script
    assert "build_simulator_gateway.ps1" in release_script
    assert "simulator-gateway\\PlcAi.GxSimulator2Gateway.exe" in release_script
