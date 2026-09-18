import copy

import pytest

from plc.ir import build_plc_ir, canonical_sha256
from storage.session import SessionStore
from simulator import InMemoryTestBackend, PLCTestRunner, SimulatorRegressionService
from simulator.models import normalize_test_suite
from simulator.verification import (
    SimulatorEvidenceError, evaluate_suite_result, execution_binding,
)


def _suite():
    return normalize_test_suite({
        "name": "motor",
        "tests": [{
            "name": "start_stop", "initial": {"X0": 0},
            "steps": [
                {"id": "start", "at_ms": 0, "set": {"X0": 1}, "expect": {"Y0": 1}},
                {"id": "stop", "at_ms": 10, "set": {"X0": 0}, "wait_for": {"Y0": 0}},
            ],
        }],
    })


def _logic(backend, values):
    if "X0" in values:
        backend.values["Y0"] = values["X0"]


def _result(suite=None, backend=None):
    return PLCTestRunner(backend or InMemoryTestBackend(on_write=_logic)).run_suite(suite or _suite())


@pytest.fixture
def version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project(name="evidence", plc_model="FX3U")["id"]
    version_id, folder = store.prepare_version(project_id)
    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0"}],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }],
    }
    program = build_plc_ir(ladder)
    store._write_json(folder / "ladder.json", ladder)
    store._write_json(folder / "program.ir.json", program)
    store.complete_version(project_id, version_id, {
        "target_mode": "ladder", "plc_model": "FX3U",
        "revision": program["revision"], "ir_sha256": canonical_sha256(program),
        "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
    })
    return store, project_id, version_id, program, folder


def _execute(version):
    store, project_id, version_id, _, _ = version
    return SimulatorRegressionService(
        store, backend=InMemoryTestBackend(on_write=_logic),
    ).run_version_suite(project_id, version_id, _suite())


def test_complete_observed_run_passes_without_mutating_evidence():
    suite = _suite()
    result = _result(suite)
    before = copy.deepcopy((suite, result))
    verification = evaluate_suite_result(suite, result)
    assert verification["status"] == "passed"
    assert verification["program_repair_allowed"] is False
    assert (suite, result) == before


@pytest.mark.parametrize("corruption,category", [
    ("missing_case", "incomplete"), ("duplicate_case", "incomplete"),
    ("missing_assertion", "incomplete"), ("duplicate_assertion", "incomplete"),
    ("missing_observation", "incomplete"), ("missing_final_values", "incomplete"),
    ("not_executed", "incomplete"), ("partial_execution", "incomplete"),
    ("contradictory_assertion", "evidence_invalid"),
    ("wrong_counts", "evidence_invalid"), ("wrong_suite_hash", "evidence_invalid"),
    ("wrong_case_hash", "evidence_invalid"), ("malformed_counts", "evidence_invalid"),
])
def test_pass_claim_needs_complete_consistent_observations(corruption, category):
    result = _result()
    case = result["results"][0]
    if corruption == "missing_case":
        result["results"] = []
    elif corruption == "duplicate_case":
        result["results"].append(copy.deepcopy(case))
    elif corruption == "missing_assertion":
        case["assertions"].pop()
    elif corruption == "duplicate_assertion":
        case["assertions"].append(copy.deepcopy(case["assertions"][0]))
    elif corruption == "missing_observation":
        case["trace"] = [event for event in case["trace"] if event["event"] != "wait_for"]
    elif corruption == "missing_final_values":
        next(event for event in case["trace"] if event["event"] == "final_sample")["values"] = {}
    elif corruption == "not_executed":
        case["execution_started"] = False
    elif corruption == "partial_execution":
        result["executed_count"] = 0
    elif corruption == "contradictory_assertion":
        case["assertions"][0]["passed"] = False
    elif corruption == "wrong_counts":
        result["counts"]["failed"] = 1
    elif corruption == "wrong_suite_hash":
        result["suite_sha256"] = "0" * 64
    elif corruption == "wrong_case_hash":
        case["test_sha256"] = "0" * 64
    elif corruption == "malformed_counts":
        result["counts"] = []
    verdict = evaluate_suite_result(_suite(), result)
    assert verdict["status"] == "blocked"
    assert verdict["category"] == category
    assert verdict["program_repair_allowed"] is False


def test_action_only_run_is_not_behavioral_verification():
    suite = normalize_test_suite({"tests": [{
        "name": "stimulus_only", "initial": {"X0": 0},
        "steps": [{"set": {"X0": 1}}],
    }]})
    result = _result(suite)
    assert result["status"] == "passed"  # The runner did complete its actions.
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_changed_expectation_cannot_reuse_run_with_same_case_and_step_names():
    original = _suite()
    result = _result(original)
    changed = copy.deepcopy(original)
    changed["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    assert evaluate_suite_result(changed, result)["category"] == "evidence_invalid"


def test_missing_device_value_cannot_satisfy_not_equal_assertion():
    class MissingValueBackend(InMemoryTestBackend):
        def read_many(self, addresses):
            return {address: None if address == "Y0" else 0 for address in addresses}

    suite = normalize_test_suite({"tests": [{
        "name": "observed",
        "steps": [{"expect": [{"address": "Y0", "operator": "ne", "value": 0}]}],
    }]})
    result = _result(suite, MissingValueBackend())
    assert result["status"] == "passed"
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_assertion_and_invariant_failures_are_reviewable():
    suite = _suite()
    failed = _result(suite, InMemoryTestBackend())
    verification = evaluate_suite_result(suite, failed)
    assert verification["category"] == "assertion"
    assert verification["program_repair_allowed"]
    suite["tests"][0]["invariants"] = [{"type": "mutual_exclusion", "devices": ["Y0", "Y1"]}]
    suite = normalize_test_suite(suite)
    result = _result(suite, InMemoryTestBackend(initial={"Y1": 1}, on_write=_logic))
    verification = evaluate_suite_result(suite, result)
    assert verification["category"] == "invariant"
    assert verification["program_repair_allowed"]


@pytest.mark.parametrize("phase,category", [
    ("connect", "environment"), ("initial", "setup"), ("stimulus", "setup"), ("read", "runtime"),
])
def test_failure_taxonomy_uses_execution_stage_not_program_patch(phase, category):
    class BrokenBackend(InMemoryTestBackend):
        def connect(self):
            if phase == "connect":
                raise RuntimeError("simulator unavailable")
            return super().connect()

        def write_many(self, values):
            if phase == "initial" or (phase == "stimulus" and values.get("X0") == 1):
                raise RuntimeError("invalid test stimulus")
            return super().write_many(values)

        def read_many(self, addresses):
            if phase == "read" and self.values.get("X0") == 1:
                raise RuntimeError("observation read failed")
            return super().read_many(addresses)

    verdict = evaluate_suite_result(_suite(), _result(backend=BrokenBackend(on_write=_logic)))
    assert verdict["category"] == category
    assert verdict["status"] == "blocked"
    assert verdict["program_repair_allowed"] is False


def test_saved_run_binds_program_suite_result_and_keeps_backend_identity(version):
    execution = _execute(version)
    store, project_id, version_id, program, _ = version
    loaded = store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])
    binding = loaded["binding"]
    assert binding["ir_sha256"] == canonical_sha256(program)
    assert binding["suite_sha256"] == canonical_sha256(loaded["suite"])
    assert binding["result_sha256"] == canonical_sha256(loaded["result"])
    assert binding["binding_scope"] == "execution_snapshot"
    assert loaded["verification"]["status"] == "passed"
    assert loaded["record"]["backend_kinds"] == ["test_memory_not_plc_simulator"]


@pytest.mark.parametrize("corruption", ["suite_binding", "trace_binding", "suite_contents", "trace_contents", "missing_trace"])
def test_mismatched_or_modified_artifacts_cannot_be_loaded_as_evidence(version, corruption):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    target = folder / record["suite_artifact" if corruption.startswith("suite") else "trace_artifact"]
    payload = store._read_json(target)
    if corruption.endswith("binding"):
        payload["binding"]["version_id"] = "another_version"
    elif corruption == "suite_contents":
        payload["suite"]["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    elif corruption == "missing_trace":
        target.unlink()
        assert store.load_simulator_run(project_id, version_id, record["run_id"]) is None
        return
    else:
        payload["result"]["results"][0]["trace"].clear()
    store._write_json(target, payload)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, record["run_id"])


def test_program_change_during_execution_cannot_be_rebound_at_save(version):
    store, project_id, version_id, program, folder = version
    changed = copy.deepcopy(program)
    changed["revision"] += 1
    changed_once = False

    def change_program(backend, values):
        nonlocal changed_once
        _logic(backend, values)
        if not changed_once:
            changed_once = True
            store._write_json(folder / "program.ir.json", changed)
            store.update_version_metadata(project_id, version_id, {
                "revision": changed["revision"], "ir_sha256": canonical_sha256(changed),
            })

    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=change_program))
    with pytest.raises(SimulatorEvidenceError) as error:
        service.run_version_suite(project_id, version_id, _suite())
    assert error.value.category == "version_conflict"
    assert store.list_simulator_runs(project_id, version_id) == []


def test_invalid_pass_is_not_persisted_as_success(version):
    store, project_id, version_id, program, _ = version
    result = _result()
    result["results"] = []
    with pytest.raises(SimulatorEvidenceError):
        store.save_simulator_run(
            project_id, version_id, _suite(), result,
            execution_snapshot=execution_binding(project_id, version_id, program, _suite()),
        )
    assert store.list_simulator_runs(project_id, version_id) == []


def test_stale_approved_binding_is_rejected_before_runtime_preparation(version):
    store, project_id, version_id, program, _ = version

    class Preparer:
        def prepare(self):
            pytest.fail("A stale plan must not prepare the runtime")

    binding = execution_binding(project_id, version_id, program, _suite())
    binding["suite_sha256"] = "0" * 64
    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(), preparer=Preparer())
    with pytest.raises(SimulatorEvidenceError):
        service.run_version_suite(project_id, version_id, _suite(), expected_binding=binding)
    assert store.list_simulator_runs(project_id, version_id) == []


@pytest.mark.parametrize("field,value", [
    ("backend_kinds", ["gx_simulator2_gateway"]), ("counts", {"passed": 999}), ("passed", False),
])
def test_index_cannot_change_result_or_backend_provenance(version, field, value):
    execution = _execute(version)
    store, project_id, version_id, _, _ = version
    project = store.get_project(project_id)
    project["versions"][0]["simulator_runs"][0][field] = value
    store.save_project(project)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])


def test_legacy_record_remains_readable_without_migration_or_new_verdict(version):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    new_fields = (
        "evidence_schema_version", "binding_scope", "suite_sha256", "result_sha256",
    )
    for key in ("suite_artifact", "trace_artifact"):
        target = folder / record[key]
        payload = store._read_json(target)
        for field in new_fields:
            payload["binding"].pop(field)
        store._write_json(target, payload)
    project = store.get_project(project_id)
    old_record = project["versions"][0]["simulator_runs"][0]
    for field in (*new_fields, "verification"):
        old_record.pop(field)
    # A genuinely old version may not have a persisted IR yet.
    project["versions"][0]["artifacts"].pop("ir")
    store.save_project(project)
    (folder / "program.ir.json").unlink()
    project_folder = store.project_dir(project_id)
    before = {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}
    loaded = store.load_simulator_run(project_id, version_id, record["run_id"])
    assert loaded["result"]["status"] == "passed"
    assert loaded["verification"]["category"] == "legacy_evidence"
    assert loaded["verification"]["program_repair_allowed"] is False
    assert before == {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}
