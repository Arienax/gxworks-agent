import json
import io
from pathlib import Path
import urllib.error

import pytest

from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
from simulator import InMemoryTestBackend
from simulator.gateway import (
    GXSimulatorGatewayClient,
    GatewayOperationError,
    GatewayProtocolError,
)
from simulator.planning import (
    SimulatorTestPlanError,
    build_test_generation_context,
    normalize_generated_test_suite,
)
from simulator.runtime import SimulatorGatewayRuntime
from simulator.workflow import SimulatorVersionWorkflowService


def _compatible_gateway_health(**overrides):
    result = {
        "service": "plc-ai-gx-simulator2-gateway",
        "simulator_only": True,
        "protocol_version": 2,
        "capabilities": {
            "device_read": True,
            "device_write": True,
            "cpu_reset": True,
            "scan_monitor": True,
        },
    }
    result.update(overrides)
    return result


def _rung(rung_id, source="X0", target="Y0"):
    return {
        "rung_id": rung_id,
        "debug_note": "启动输出",
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": source, "label": "启动"}],
                "outputs": [{"type": "COIL", "address": target, "label": "输出"}],
            }
        ],
    }


def _program():
    return build_plc_ir(
        {
            "device_comments": {"X0": "启动按钮", "Y0": "电机"},
            "rungs": [_rung(10)],
        },
        revision=3,
    )


def _rising_edge_program():
    return build_plc_ir(
        {
            "device_comments": {"X0": "启动按钮", "Y0": "电机"},
            "rungs": [
                {
                    "rung_id": 1,
                    "debug_note": "启动上升沿",
                    "header_element": None,
                    "shared_inputs": [],
                    "branches": [
                        {
                            "branch_id": 1,
                            "y_offset_level": 0,
                            "inputs": [
                                {"type": "P", "address": "X0", "label": "启动"}
                            ],
                            "outputs": [
                                {"type": "COIL", "address": "Y0", "label": "电机"}
                            ],
                        }
                    ],
                }
            ],
        },
        revision=4,
    )


def _suite(*, expected=1):
    return {
        "schema_version": 1,
        "name": "启动回归",
        "plc_model": "FX3U",
        "tests": [
            {
                "schema_version": 1,
                "name": "启动输出",
                "description": "X0 启动后 Y0 输出",
                "initial": {"X0": 0},
                "steps": [
                    {"at_ms": 5, "set": {"X0": 1}},
                    {"at_ms": 10, "expect": {"Y0": expected}},
                ],
                "trace_devices": ["X0", "Y0"],
                "sample_ms": 5,
                "timeout_ms": 20,
            }
        ],
    }


def _store_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="sim workflow", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    program = _program()
    ladder = {
        "device_comments": {"X0": "启动按钮", "Y0": "电机"},
        "rungs": [_rung(10)],
    }
    store._write_json(version_dir / "program.ir.json", program)
    store._write_json(version_dir / "ladder.json", ladder)
    (version_dir / "program.csv").write_text("fixture", encoding="utf-8")
    (version_dir / "comments.csv").write_text("fixture", encoding="utf-8")
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "revision": 3,
            "ir_sha256": canonical_sha256(program),
            "artifacts": {
                "json": "ladder.json",
                "ir": "program.ir.json",
                "program_csv": "program.csv",
                "comment_csv": "comments.csv",
            },
        },
    )
    return store, project["id"], version_id, program


def test_generation_context_is_version_bound_and_contains_behavior_not_ladder_artwork():
    context = build_test_generation_context(_program())
    assert context["binding"]["revision"] == 3
    assert context["binding"]["ir_sha256"] == canonical_sha256(_program())
    assert context["networks"][0]["reads"] == ["X0"]
    assert context["networks"][0]["writes"] == ["Y0"]
    assert "ladder" not in context["networks"][0]


def test_generated_suite_rejects_unknown_devices_and_assertion_free_tests():
    unknown = _suite()
    unknown["tests"][0]["steps"][1]["expect"] = {"Y1": 1}
    with pytest.raises(SimulatorTestPlanError, match="程序之外.*Y1"):
        normalize_generated_test_suite(unknown, _program())

    assertion_free = _suite()
    assertion_free["tests"][0]["steps"] = [{"at_ms": 5, "set": {"X0": 1}}]
    with pytest.raises(SimulatorTestPlanError, match="没有期望值"):
        normalize_generated_test_suite(assertion_free, _program())


def test_generated_suite_rearms_repeated_rising_edge_from_cached_plan():
    suite = _suite()
    case = suite["tests"][0]
    case["steps"] = [
        {"id": "first_start", "at_ms": 100, "set": {"X0": 1}},
        {"id": "first_result", "at_ms": 200, "expect": {"Y0": 1}},
        {"id": "restart", "at_ms": 300, "set": {"X0": 1}},
        {"id": "second_result", "at_ms": 400, "expect": {"Y0": 1}},
    ]
    case["timeout_ms"] = 500

    normalized = normalize_generated_test_suite(suite, _rising_edge_program())

    steps = normalized["tests"][0]["steps"]
    restart_index = next(index for index, step in enumerate(steps) if step["id"] == "restart")
    rearm = steps[restart_index - 1]
    assert rearm["id"].startswith("auto_edge_rearm_before_restart")
    assert rearm["set"] == {"X0": 0}
    assert 200 <= rearm["at_ms"] < 300
    assert normalized["tests"][0]["metadata"]["normalization_repairs"] == [
        {
            "kind": "edge_rearm",
            "device": "X0",
            "inactive_value": 0,
            "before_step_id": "restart",
            "inserted_step_id": rearm["id"],
            "at_ms": rearm["at_ms"],
        }
    ]


def test_generated_suite_keeps_explicit_rising_edge_release_unchanged():
    suite = _suite()
    case = suite["tests"][0]
    case["steps"] = [
        {"id": "first_start", "at_ms": 100, "set": {"X0": 1}},
        {"id": "release", "at_ms": 200, "set": {"X0": 0}},
        {"id": "restart", "at_ms": 300, "set": {"X0": 1}},
        {"id": "result", "at_ms": 400, "expect": {"Y0": 1}},
    ]
    case["timeout_ms"] = 500

    normalized = normalize_generated_test_suite(suite, _rising_edge_program())

    assert [step["id"] for step in normalized["tests"][0]["steps"]] == [
        "first_start",
        "release",
        "restart",
        "result",
    ]
    assert "normalization_repairs" not in normalized["tests"][0]["metadata"]


def test_state_machine_ir_adds_proven_state_constraint_to_every_generated_test():
    ladder = {
        "device_comments": {
            "X0": "启动",
            "X1": "停止",
            "D0": "主状态机",
            "Y0": "运行输出",
        },
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "首次扫描进入停止态",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "M8002", "label": "首次扫描"}],
                        "outputs": [{"type": "BLOCK_OUTPUT", "expression": "MOV K0 D0", "label": "停止态"}],
                    }
                ],
            },
            {
                "rung_id": 2,
                "debug_note": "停止态启动",
                "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K0", "label": "停止态"},
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "P", "address": "X0", "label": "启动"}],
                        "outputs": [{"type": "BLOCK_OUTPUT", "expression": "MOV K1 D0", "label": "运行态"}],
                    }
                ],
            },
            {
                "rung_id": 3,
                "debug_note": "运行态停止",
                "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": "运行态"},
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X1", "label": "停止"}],
                        "outputs": [{"type": "BLOCK_OUTPUT", "expression": "MOV K0 D0", "label": "停止态"}],
                    }
                ],
            },
            {
                "rung_id": 4,
                "debug_note": "运行输出",
                "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": "运行态"},
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "运行"}],
                    }
                ],
            },
        ],
    }
    program = build_plc_ir(ladder, revision=7)
    suite = _suite()
    normalized = normalize_generated_test_suite(suite, program)

    assert normalized["tests"][0]["invariants"] == [
        {
            "type": "state_constraint",
            "device": "D0",
            "allowed": [0, 1],
            "name": "D0 状态机合法状态",
        }
    ]
    assert "D0" in normalized["tests"][0]["trace_devices"]


def test_non_state_machine_program_does_not_gain_invented_invariants():
    normalized = normalize_generated_test_suite(_suite(), _program())
    assert normalized["tests"][0]["invariants"] == []


def test_generated_suite_drops_point_expectation_duplicated_in_invariants():
    suite = _suite()
    suite["tests"][0]["invariants"] = [
        {"at_ms": 10, "expect": {"Y0": 1}}
    ]

    normalized = normalize_generated_test_suite(suite, _program())

    test = normalized["tests"][0]
    assert test["invariants"] == []
    assert len(test["steps"]) == 2
    assert test["metadata"]["normalization_repairs"] == [
        {
            "kind": "misplaced_expectation",
            "source_invariant_index": 0,
            "action": "dropped_duplicate",
            "at_ms": 10,
        }
    ]


def test_generated_suite_moves_unique_point_expectation_out_of_invariants():
    suite = _suite()
    suite["tests"][0]["invariants"] = [
        {"at_ms": 15, "expect": {"Y0": 0}}
    ]

    normalized = normalize_generated_test_suite(suite, _program())

    test = normalized["tests"][0]
    assert test["invariants"] == []
    assert test["steps"][-1]["at_ms"] == 15
    assert test["steps"][-1]["expect"] == [
        {"address": "Y0", "operator": "eq", "value": 0}
    ]
    assert test["metadata"]["normalization_repairs"][0]["action"] == "moved_to_step"


def test_generated_suite_does_not_hide_ambiguous_invalid_invariant():
    suite = _suite()
    suite["tests"][0]["invariants"] = [
        {"at_ms": 10, "expect": {"Y0": 1}, "note": "ambiguous"}
    ]

    with pytest.raises(ValueError, match="unsupported invariant"):
        normalize_generated_test_suite(suite, _program())


def test_generated_suite_makes_repeated_human_step_names_unique():
    suite = _suite()
    suite["tests"][0]["steps"] = [
        {"id": "松开启动按钮", "at_ms": 5, "set": {"X0": 1}},
        {"id": "松开启动按钮", "at_ms": 10, "expect": {"Y0": 1}},
    ]

    normalized = normalize_generated_test_suite(suite, _program())

    test = normalized["tests"][0]
    assert [step["id"] for step in test["steps"]] == [
        "松开启动按钮",
        "松开启动按钮（2）",
    ]
    assert test["metadata"]["normalization_repairs"] == [
        {
            "kind": "duplicate_step_id",
            "step_index": 1,
            "original_id": "松开启动按钮",
            "replacement_id": "松开启动按钮（2）",
        }
    ]


def test_saved_test_plan_is_immutable_and_rejects_cross_version_binding(tmp_path):
    store, project_id, version_id, program = _store_version(tmp_path)
    plan = store.save_simulator_test_plan(project_id, version_id, _suite())
    loaded = store.load_simulator_test_plan(
        project_id, version_id, plan["binding"]["plan_id"]
    )
    assert loaded == plan
    path = (
        store.version_dir(project_id, version_id)
        / "tests"
        / "plans"
        / f"{plan['binding']['plan_id']}.json"
    )
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["binding"]["version_id"] = "v9999"
    store._write_json(path, tampered)
    with pytest.raises(ValueError, match="stale|cross-version"):
        store.load_simulator_test_plan(
            project_id, version_id, plan["binding"]["plan_id"]
        )


def test_latest_test_plan_reuses_newest_exact_version_cache(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    first = store.save_simulator_test_plan(project_id, version_id, _suite(expected=0))
    latest = store.save_simulator_test_plan(project_id, version_id, _suite(expected=1))

    loaded = store.load_latest_simulator_test_plan(project_id, version_id)

    assert loaded["binding"]["plan_id"] == latest["binding"]["plan_id"]
    assert loaded["binding"]["plan_id"] != first["binding"]["plan_id"]
    assert loaded["cache_reused"] is True
    assert loaded["suite"]["tests"][0]["steps"][1]["expect"] == [
        {"address": "Y0", "operator": "eq", "value": 1}
    ]


def test_latest_test_plan_skips_damaged_newest_artifact(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    first = store.save_simulator_test_plan(project_id, version_id, _suite(expected=0))
    latest = store.save_simulator_test_plan(project_id, version_id, _suite(expected=1))
    latest_path = (
        store.version_dir(project_id, version_id)
        / "tests"
        / "plans"
        / f"{latest['binding']['plan_id']}.json"
    )
    latest_path.write_text("not json", encoding="utf-8")

    loaded = store.load_latest_simulator_test_plan(project_id, version_id)

    assert loaded["binding"]["plan_id"] == first["binding"]["plan_id"]
    assert loaded["suite"]["tests"][0]["steps"][1]["expect"] == [
        {"address": "Y0", "operator": "eq", "value": 0}
    ]


def test_latest_test_plan_rejects_cache_after_ir_changes(tmp_path):
    store, project_id, version_id, program = _store_version(tmp_path)
    store.save_simulator_test_plan(project_id, version_id, _suite())
    program["revision"] = 4
    store._write_json(
        store.version_dir(project_id, version_id) / "program.ir.json", program
    )

    assert store.load_latest_simulator_test_plan(project_id, version_id) is None


class _Result:
    def __init__(self, success=True, error_code=None):
        self.success = success
        self.error_code = error_code

    def to_dict(self):
        return {
            "success": self.success,
            "stage": "complete",
            "message": "imported",
            "error_code": self.error_code,
            "details": {},
        }


class _Preparation:
    def __init__(self, success=True, message="ready"):
        self.success = success
        self.message = message

    def to_dict(self):
        return {"success": self.success, "message": self.message}


class _Preparer:
    def __init__(self, *, stop_success=True, prepare_success=True):
        self.stop_success = stop_success
        self.prepare_success = prepare_success
        self.events = []

    def stop_if_running(self, progress=None):
        self.events.append("stop")
        return _Preparation(self.stop_success, "stopped" if self.stop_success else "busy")

    def prepare(self):
        self.events.append("prepare")
        return _Preparation(self.prepare_success, "ready" if self.prepare_success else "offline")


class _PreflightFailurePreparer(_Preparer):
    def preflight(self, progress=None):
        self.events.append("preflight")
        return _Preparation(False, "MX Component ActProgType 未安装")


def _bound_plan(store, project_id, version_id, suite):
    return store.save_simulator_test_plan(project_id, version_id, suite)


def test_approved_workflow_stops_imports_prepares_runs_and_persists_trace(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    plan = _bound_plan(store, project_id, version_id, _suite())
    events = []

    def importing(path, **kwargs):
        events.append(("import", Path(path).name, Path(kwargs["comment_csv_path"]).name))
        return _Result()

    def logic(backend, values):
        if "X0" in values:
            backend.values["Y0"] = values["X0"]

    preparer = _Preparer()
    result = SimulatorVersionWorkflowService(
        store,
        importer=importing,
        preparer=preparer,
        backend=InMemoryTestBackend(on_write=logic),
    ).run_approved_plan(project_id, version_id, plan)
    assert result["status"] == "passed"
    assert preparer.events == ["stop", "prepare", "stop"]
    assert result["final_stop"]["success"] is True
    assert events == [("import", "program.csv", "comments.csv")]
    run_id = result["execution"]["record"]["run_id"]
    assert store.load_simulator_run(project_id, version_id, run_id)["result"]["status"] == "passed"


def test_workflow_does_not_start_simulator_after_incomplete_import(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    plan = _bound_plan(store, project_id, version_id, _suite())
    preparer = _Preparer()
    result = SimulatorVersionWorkflowService(
        store,
        importer=lambda *args, **kwargs: _Result(True, "baseline_write_failed"),
        preparer=preparer,
        backend=InMemoryTestBackend(),
    ).run_approved_plan(project_id, version_id, plan)
    assert result["status"] == "import_failed"
    assert preparer.events == ["stop"]
    assert store.list_simulator_runs(project_id, version_id) == []


def test_environment_unavailable_is_persisted_not_classified_as_logic_failure(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    plan = _bound_plan(store, project_id, version_id, _suite())
    preparer = _Preparer(prepare_success=False)
    result = SimulatorVersionWorkflowService(
        store,
        importer=lambda *args, **kwargs: _Result(),
        preparer=preparer,
        backend=InMemoryTestBackend(),
    ).run_approved_plan(project_id, version_id, plan)
    assert result["status"] == "unavailable"
    assert result["execution"]["result"]["counts"]["failed"] == 0
    run_id = result["execution"]["record"]["run_id"]
    saved = store.load_simulator_run(project_id, version_id, run_id)
    assert saved["result"]["status"] == "unavailable"


def test_failed_connection_preflight_does_not_import_or_toggle_simulator(tmp_path):
    store, project_id, version_id, _program_ir = _store_version(tmp_path)
    plan = _bound_plan(store, project_id, version_id, _suite())
    imported = []
    preparer = _PreflightFailurePreparer()

    result = SimulatorVersionWorkflowService(
        store,
        importer=lambda *args, **kwargs: imported.append((args, kwargs)),
        preparer=preparer,
        backend=InMemoryTestBackend(),
    ).run_approved_plan(project_id, version_id, plan)

    assert result["status"] == "unavailable"
    assert result["message"] == "MX Component ActProgType 未安装"
    assert preparer.events == ["preflight"]
    assert imported == []
    run_id = result["execution"]["record"]["run_id"]
    assert store.load_simulator_run(project_id, version_id, run_id)["result"][
        "status"
    ] == "unavailable"


def test_gateway_client_resolves_lazy_runtime_token_at_request_time(monkeypatch):
    client = GXSimulatorGatewayClient(token="")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true, "simulator_only": true}'

    def urlopen(request, timeout):
        captured["token"] = request.headers.get("X-plc-gateway-token")
        return Response()

    monkeypatch.setenv("GX_SIMULATOR_GATEWAY_TOKEN", "0123456789abcdef")
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert client.health()["simulator_only"] is True
    assert captured["token"] == "0123456789abcdef"


def test_gateway_client_rejects_legacy_protocol_before_connect(monkeypatch):
    client = GXSimulatorGatewayClient(token="0123456789abcdef")
    monkeypatch.setattr(
        client,
        "health",
        lambda: {
            "service": "plc-ai-gx-simulator2-gateway",
            "simulator_only": True,
        },
    )

    with pytest.raises(GatewayProtocolError, match="版本过旧"):
        client.connect()


def test_gateway_client_uses_independent_cpu_reset_timeout(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b'{"ok": true, "reset": true}'

    def urlopen(_request, timeout):
        captured["timeout"] = timeout
        return Response()

    client = GXSimulatorGatewayClient(
        token="0123456789abcdef",
        timeout=2.0,
        reset_timeout=17.0,
    )
    client.connected = True
    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    client.reset_cpu(["M0"], initial_values={"X0": 0})

    assert captured["timeout"] == 17.0


def test_gateway_http_errors_preserve_status_and_error_code(monkeypatch):
    payload = io.BytesIO(
        b'{"error":"Unknown gateway endpoint.","error_code":"NOT_FOUND"}'
    )

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            404,
            "Not Found",
            {},
            payload,
        )

    client = GXSimulatorGatewayClient(token="0123456789abcdef")
    client.connected = True
    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    with pytest.raises(GatewayOperationError) as raised:
        client.reset_cpu([])

    assert raised.value.status == 404
    assert raised.value.code == "NOT_FOUND"


def test_gateway_runtime_does_not_start_during_construction(monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: calls.append(args))
    runtime = SimulatorGatewayRuntime(executable="missing.exe")
    assert calls == []
    assert runtime._process is None


def test_gateway_runtime_passes_custom_url_port_to_gateway(monkeypatch, tmp_path):
    executable = tmp_path / "gateway.exe"
    executable.write_bytes(b"gateway")
    captured = {}

    class Process:
        returncode = None

        @staticmethod
        def poll():
            return None

    def popen(*args, **kwargs):
        captured["environment"] = kwargs["env"]
        return Process()

    runtime = SimulatorGatewayRuntime(
        base_url="http://127.0.0.1:17837",
        executable=executable,
    )
    health_calls = iter(
        [None, _compatible_gateway_health()]
    )
    monkeypatch.setattr(runtime, "health", lambda: next(health_calls))
    monkeypatch.setattr("subprocess.Popen", popen)

    runtime.ensure_gateway()

    assert captured["environment"]["GX_SIMULATOR_GATEWAY_PORT"] == "17837"


def test_gateway_runtime_isolates_unknown_legacy_gateway(monkeypatch, tmp_path):
    executable = tmp_path / "gateway.exe"
    executable.write_bytes(b"gateway")
    captured = {}

    class Process:
        returncode = None

        @staticmethod
        def poll():
            return None

    def popen(*args, **kwargs):
        captured["environment"] = kwargs["env"]
        return Process()

    runtime = SimulatorGatewayRuntime(
        base_url="http://127.0.0.1:17831",
        executable=executable,
    )
    precreated_client = runtime.client(timeout=5.0)
    health_calls = iter(
        [
            {
                "service": "plc-ai-gx-simulator2-gateway",
                "simulator_only": True,
            },
            _compatible_gateway_health(),
        ]
    )
    monkeypatch.setattr(runtime, "health", lambda: next(health_calls))
    monkeypatch.setattr(
        runtime,
        "_free_loopback_url",
        lambda: "http://127.0.0.1:27931",
    )
    monkeypatch.setattr("subprocess.Popen", popen)

    result = runtime.ensure_gateway()

    assert runtime.base_url == "http://127.0.0.1:27931"
    assert precreated_client.base_url == runtime.base_url
    assert len(precreated_client.token) >= 16
    assert captured["environment"]["GX_SIMULATOR_GATEWAY_PORT"] == "27931"
    assert result["protocol_compatible"] is True
    assert result["endpoint_isolation"]["from"].endswith(":17831")


def test_gateway_runtime_prefers_bundled_release_over_legacy_local_copy(
    monkeypatch, tmp_path
):
    from simulator import runtime as runtime_module

    release = tmp_path / "release"
    bundled = release / "simulator-gateway" / "PlcAi.GxSimulator2Gateway.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"bundled")
    local = (
        tmp_path
        / "local"
        / "PLC AI Studio"
        / "simulator-gateway"
        / "PlcAi.GxSimulator2Gateway.exe"
    )
    local.parent.mkdir(parents=True)
    local.write_bytes(b"legacy")
    monkeypatch.setattr(runtime_module.os.sys, "executable", str(release / "app.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("GX_SIMULATOR_GATEWAY_EXE", raising=False)

    assert SimulatorGatewayRuntime().find_executable() == bundled.resolve()


def test_explicit_gateway_override_still_has_highest_priority(monkeypatch, tmp_path):
    configured = tmp_path / "configured-gateway.exe"
    configured.write_bytes(b"configured")
    monkeypatch.setenv("GX_SIMULATOR_GATEWAY_EXE", str(configured))
    assert SimulatorGatewayRuntime().find_executable() == configured.resolve()
