import json
import io
from pathlib import Path
import urllib.error

import pytest


def sorting_scene(**updates):
    from simulator.plant import SortingScenario, RULES
    roles = {"present": "X2", "class_a": "X3", "class_b": "X4", "done_a": "X5", "done_b": "X6",
             "conveyor": "Y0", "sort_a": "Y1", "sort_b": "Y2"}
    return SortingScenario(**{**dict(id="two_bin_sorting", plc_model="FX3U",
        bindings={role: {"binding_id": "binding_" + role, "address": address, "active_level": 1} for role, address in roles.items()},
        workpieces=({"id": "p1", "class": "a", "arrival_ms": 0}, {"id": "p2", "class": "b", "arrival_ms": 240},
                    {"id": "p3", "class": "a", "arrival_ms": 480}),
        requirements={rule: "requirement_" + rule for rule in RULES}, step_ms=20, deadline_ms=1600,
        end_ms=1800, response_ms=80, return_ms=40, speed_per_ms=1.0, gate_position=100.0, gate_width=20.0,
        external_frames=({"timestamp_ms": 0, "inputs": {"X0": True, "X1": True}},)), **updates})


def sorting_program(*, alternative=False, mutant=None):
    from tests.test_simulator_verification import _bounded_ladder
    rows = [(["NO X0", "NO X1", "NC X2"], ["COIL Y0"]),
            (["NO X0", "NO X1", "NO X2", "NO X3"], ["COIL Y1"]),
            (["NO X0", "NO X1", "NO X2", "NO X4"], ["COIL Y2"])]
    if alternative: rows = [rows[2], rows[0], rows[1]]
    if mutant == "transfer_before_completion": rows[0] = (["NO X0", "NO X1"], ["COIL Y0"])
    if mutant == "same_type_binding":
        rows[1] = (rows[1][0], ["COIL Y2"]); rows[2] = (rows[2][0], ["COIL Y1"])
    if mutant == "shared_resource": rows[2] = (rows[1][0], ["COIL Y2"])
    if mutant == "all_off": rows = [(["NO X7"], ["COIL Y0", "COIL Y1", "COIL Y2"])]
    return _bounded_ladder(*rows)


@pytest.mark.parametrize("response_ms,spacing,stopped", [(80, 240, False), (140, 260, False), (80, 180, False), (140, 220, True)])
@pytest.mark.parametrize("alternative", [False, True])
def test_closed_loop_reference_and_alternative_complete_real_workpieces(response_ms, spacing, stopped, alternative):
    from simulator.closed_loop import run_closed_loop
    kwargs = {"response_ms": response_ms, "workpieces": ({"id": "a", "class": "a", "arrival_ms": 0},
               {"id": "b", "class": "b", "arrival_ms": spacing}, {"id": "c", "class": "a", "arrival_ms": 2*spacing})}
    if stopped:
        kwargs["external_frames"] = ({"timestamp_ms": 0, "inputs": {"X0": True, "X1": True}},
                                     {"timestamp_ms": 120, "inputs": {"X1": False}},
                                     {"timestamp_ms": 280, "inputs": {"X1": True}})
    report = run_closed_loop(sorting_program(alternative=alternative), sorting_scene(**kwargs), version_binding={"version_id": "reference"})
    assert report["status"] == "passed_in_modeled_scope", report["evaluation"]
    assert report["complete_run"] and report["coverage"]["scans_executed"] == 90
    assert {e["workpiece_id"] for e in report["events"] if e["kind"] == "sorted"} == {"a", "b", "c"}
    assert report["native_compile"] == "not_executed" and report["hardware_execution"] == "not_executed"
    if stopped:
        assert all(not any(frame["outputs"].values()) for frame in report["trace"] if 120 <= frame["timestamp_ms"] < 280)


@pytest.mark.parametrize("mutant,reason", [("transfer_before_completion", "left_station_before_sort_completed"),
    ("same_type_binding", "wrong_workpiece_bin"), ("shared_resource", "simultaneous_sort_commands")])
def test_closed_loop_detects_behavior_errors_after_structural_acceptance(mutant, reason):
    from simulator.closed_loop import run_closed_loop
    report = run_closed_loop(sorting_program(mutant=mutant), sorting_scene())
    assert report["status"] == "failed_in_modeled_scope" and report["complete_run"]
    assert reason in {v["reason"] for v in report["evaluation"]["constraint_violations"]}


def test_closed_loop_keeps_nonprogress_incomplete_and_unknown_cpu_distinct():
    from simulator.closed_loop import run_closed_loop
    scene = sorting_scene(external_frames=({"timestamp_ms": 0, "inputs": {"X7": False}},))
    off = run_closed_loop(sorting_program(mutant="all_off"), scene)
    assert off["status"] == "failed_in_modeled_scope" and off["evaluation"]["constraint_violations"] == []
    assert not off["evaluation"]["task_completed"]
    incomplete = run_closed_loop(sorting_program(), sorting_scene(), max_scans=5)
    assert incomplete["status"] == "unverified" and not incomplete["complete_run"] and incomplete["evaluation"] is None
    assert incomplete["reasons"] == ["scene_scan_budget_exhausted"]
    q = run_closed_loop(sorting_program(), sorting_scene(plc_model="Q00J"))
    assert q["status"] == "unverified" and q["coverage"]["scans_executed"] == 0


def test_closed_loop_sensor_feedback_cannot_be_supplied_as_external_inputs():
    with pytest.raises(ValueError, match="overwrite generated sensor"):
        sorting_scene(external_frames=({"timestamp_ms": 0, "inputs": {"X2": True}},))


def test_sorting_plant_response_is_calibrated_independently_of_any_plc_program():
    from simulator.plant import SortingPlant
    plant = SortingPlant(sorting_scene(workpieces=({"id": "piece", "class": "a", "arrival_ms": 0},)))
    assert plant.sensors()["X2"] is False
    for _ in range(5): plant.step({"Y0": True, "Y1": False, "Y2": False})
    assert plant.time_ms == 100 and plant.pieces["piece"]["position"] == 100
    assert plant.sensors()["X2"] is True and plant.sensors()["X3"] is True
    for _ in range(3): plant.step({"Y0": False, "Y1": True, "Y2": False})
    assert plant.pieces["piece"]["state"] == "on_belt" and plant.sensors()["X5"] is False
    plant.step({"Y0": False, "Y1": True, "Y2": False})
    assert plant.time_ms == 180 and plant.pieces["piece"]["bin"] == "a"
    assert plant.sensors()["X5"] is True and plant.sensors()["X2"] is False
    assert [(e["kind"], e["time_ms"]) for e in plant.events] == [("arrival", 0), ("detected", 100), ("sort_started", 100), ("sorted", 180)]


def test_sorting_plant_declared_physical_polarities_preserve_response_and_workpiece_identity():
    from hypothesis import given, settings, strategies as st
    from simulator.plant import SortingPlant

    @settings(max_examples=24, derandomize=True)
    @given(st.lists(st.integers(0, 1), min_size=8, max_size=8))
    def exercise(levels):
        scene = sorting_scene(workpieces=({"id": "piece", "class": "a", "arrival_ms": 0},))
        bindings = {role: {**row, "active_level": level} for (role, row), level in zip(scene.bindings.items(), levels)}
        plant = SortingPlant(sorting_scene(bindings=bindings, workpieces=scene.workpieces))
        def commands(active_role):
            return {bindings[role]["address"]: bool(bindings[role]["active_level"] if role == active_role else 1-bindings[role]["active_level"])
                    for role in ("conveyor", "sort_a", "sort_b")}
        for _ in range(5): plant.step(commands("conveyor"))
        assert plant.time_ms == 100 and plant.pieces["piece"]["position"] == 100
        assert plant.sensors()["X2"] is bool(bindings["present"]["active_level"])
        assert plant.sensors()["X4"] is bool(1-bindings["class_b"]["active_level"])
        for _ in range(3): plant.step(commands("sort_a"))
        assert plant.pieces["piece"]["state"] == "on_belt"
        plant.step(commands("sort_a"))
        assert plant.time_ms == 180 and plant.pieces["piece"]["bin"] == "a"
        assert plant.sensors()["X5"] is bool(bindings["done_a"]["active_level"])
    exercise()

from plc.ir import build_plc_ir, canonical_sha256
from application.session import ProjectSession as SessionStore
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
        "protocol_version": 3,
        "capabilities": {
            "device_read": True,
            "device_write": True,
            "cpu_reset": True,
            "scan_monitor": True,
            "native_device_plan": True,
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
    replies = iter([b'{"ok": true, "reset": true}', b'{"ok": true, "values": {"M8000": 1}}'])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return next(replies)

    def urlopen(_request, timeout):
        captured[_request.full_url] = timeout
        return Response()

    client = GXSimulatorGatewayClient(
        token="0123456789abcdef",
        timeout=2.0,
        reset_timeout=17.0,
    )
    client.connected = True
    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    client.reset_cpu(["M0"], initial_values={"X0": 0})

    assert captured[client.base_url + "/cpu/reset"] == 17.0


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
