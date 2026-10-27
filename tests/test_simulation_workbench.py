"""Human plan edits and replay against version-bound, offline-only evidence."""
import copy

import pytest

from application.simulation_workbench import SimulationWorkbenchError, SimulationWorkbenchService
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from simulator import InMemoryTestBackend, SimulatorRegressionService
from test_plc_debug_loop import _project_with_failure, _suite, _base_logic


@pytest.fixture
def scenario(tmp_path):
    store, pid, vid, program, run_id = _project_with_failure(tmp_path)
    project = store.get_project(pid)
    project["versions"][0]["confirmed_spec_snapshot"] = {
        "execution_semantics": [{"semantic": "LEVEL", "devices": ["X0", "Y0"], "evidence": "输入决定输出"}]}
    store.save_project(project)
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: pytest.fail("Model must not be initialized"))
    service.start()
    try:
        yield store, service, SimulationWorkbenchService(service), pid, vid, run_id
    finally:
        service.close()


def save_plan(scenario, **overrides):
    _store, _service, simulation, pid, vid, _run = scenario
    command = {"suite": _suite(), "requirement_links": {"motor_stop": ["SEM001"]},
               "issue_ids": ["finding-stop-missing"], "expected_ir_sha256": simulation.read(pid, vid)["ir_sha256"]}
    command.update(overrides)
    return simulation.save(pid, vid, **command)


def test_edit_creates_new_immutable_version_bound_plan_and_persists_links(scenario):
    store, service, simulation, pid, vid, _run = scenario
    first = save_plan(scenario)
    first_id = first["binding"]["plan_id"]
    record = store.get_version(pid, vid)["simulator_test_plans"][0]
    original_bytes = (store.version_dir(pid, vid) / record["plan_artifact"]).read_bytes()
    draft = copy.deepcopy(first["suite"])
    draft["tests"][0]["steps"][-1]["expect"][0]["value"] = 1
    second = save_plan(scenario, suite=draft, source_plan_id=first_id)
    assert second["binding"]["plan_id"] != first_id
    assert (store.version_dir(pid, vid) / record["plan_artifact"]).read_bytes() == original_bytes
    reloaded = SimulationWorkbenchService(service).read(pid, vid)
    assert len(reloaded["plans"]) == 2
    assert reloaded["plans"][0]["requirement_links"] == {"motor_stop": ["SEM001"]}
    assert reloaded["plans"][0]["issue_ids"] == ["finding-stop-missing"]
    assert reloaded["plans"][0]["source_plan_id"] == first_id
    assert reloaded["requirements"][0]["status"] == "linked"
    assert reloaded["requirements"][0]["latest_run"] is None
    assert reloaded["requirements"][0]["tests"][0]["latest_run"] is None
    assert "verified" not in reloaded["requirements"][0]
    # The standard approval reader sees the exact metadata-bound suite.
    executable = service.projects.plan(pid, vid, second["binding"]["plan_id"])
    assert executable["suite"] == second["suite"]
    assert service.proposals.list() == []


@pytest.mark.parametrize("overrides,error", [
    ({"expected_ir_sha256": "0" * 64}, ConflictError),
    ({"requirement_links": {"motor_stop": ["SEM999"]}}, SimulationWorkbenchError),
    ({"requirement_links": {"missing-test": ["SEM001"]}}, SimulationWorkbenchError),
    ({"source_plan_id": "nonexistent"}, KeyError),
])
def test_invalid_binding_or_links_do_not_create_plans(scenario, overrides, error):
    store, _service, _simulation, pid, vid, _run = scenario
    with pytest.raises(error):
        save_plan(scenario, **overrides)
    assert store.get_version(pid, vid)["simulator_test_plans"] == []


def test_manual_edits_reject_invalid_timing_and_illegal_writes(scenario):
    draft = _suite()
    draft["tests"][0]["steps"][-1]["at_ms"] = 1
    with pytest.raises(SimulationWorkbenchError, match="time order"):
        save_plan(scenario, suite=draft)
    draft = _suite()
    draft["tests"][0]["initial"]["Y0"] = 0
    with pytest.raises(SimulationWorkbenchError, match="not allowed"):
        save_plan(scenario, suite=draft)


def test_replay_uses_observed_reads_and_identifies_test_backend(scenario):
    _store, _service, simulation, pid, vid, run_id = scenario
    replay = simulation.replay(pid, vid, run_id)
    case = replay["cases"][0]
    assert case["backend_kind"] == "test_memory_not_plc_simulator"
    assert case["observations"]
    assert not {"write", "initial_write"} & {e["event"] for e in case["observations"]}
    failed = next(a for a in case["assertions"] if not a["passed"])
    assert failed["actual"] == 1
    assert failed["expected"] == {"operator": "eq", "value": 0}
    assert case["requirement_ids"] == []


def test_executed_manual_plan_keeps_associations_with_exact_run(scenario):
    store, _service, simulation, pid, vid, _run = scenario
    plan = save_plan(scenario)
    executed = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=_base_logic)).run_version_suite(pid, vid, plan["suite"])
    replay = simulation.replay(pid, vid, executed["record"]["run_id"])
    assert replay["cases"][0]["requirement_ids"] == ["SEM001"]
    assert replay["cases"][0]["issue_ids"] == ["finding-stop-missing"]
    assert replay["binding"]["version_id"] == vid
    requirement = simulation.read(pid, vid)["requirements"][0]
    latest = requirement["tests"][0]["latest_run"]
    assert latest["run_id"] == executed["record"]["run_id"]
    assert latest["backend_kind"] == "test_memory_not_plc_simulator"
    assert latest["status"] == "failed"
    assert latest["verification"]["status"] == "failed"
    assert requirement["latest_run"] == latest


def test_coverage_never_matches_another_plan_by_test_name_alone(scenario):
    store, _service, simulation, pid, vid, _run = scenario
    first = save_plan(scenario)
    SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=_base_logic)).run_version_suite(pid, vid, first["suite"])
    modified = copy.deepcopy(first["suite"])
    modified["tests"][0]["steps"][-1]["expect"][0]["value"] = 1
    second = save_plan(scenario, suite=modified, source_plan_id=first["binding"]["plan_id"])
    requirement = simulation.read(pid, vid)["requirements"][0]
    assert next(t for t in requirement["tests"] if t["plan_id"] == second["binding"]["plan_id"])["latest_run"] is None
    assert next(t for t in requirement["tests"] if t["plan_id"] == first["binding"]["plan_id"])["latest_run"]["status"] == "failed"


def test_coverage_rejects_corrupt_evidence_instead_of_reusing_index_success(scenario):
    store, _service, simulation, pid, vid, _run = scenario
    plan = save_plan(scenario)
    execution = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=_base_logic)).run_version_suite(pid, vid, plan["suite"])
    record = execution["record"]
    trace_path = store.version_dir(pid, vid) / record["trace_artifact"]
    trace = store._read_json(trace_path)
    trace["result"]["results"][0]["status"] = "passed"
    store._write_json(trace_path, trace)
    data = simulation.read(pid, vid)
    assert record["run_id"] in data["unavailable_runs"]
    assert data["requirements"][0]["latest_run"] is None


def test_coverage_blocks_incomplete_evidence_even_if_test_row_says_passed(scenario, monkeypatch):
    store, service, simulation, pid, vid, _run = scenario
    plan = save_plan(scenario)
    executed = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=_base_logic)).run_version_suite(pid, vid, plan["suite"])
    original = service.projects.simulator_run
    def blocked(pid, vid, run_id):
        saved = original(pid, vid, run_id)
        if run_id == executed["record"]["run_id"]:
            saved["verification"] = {"status": "blocked", "category": "incomplete"}
            saved["result"]["results"][0]["status"] = "passed"
        return saved
    monkeypatch.setattr(service.projects, "simulator_run", blocked)
    latest = simulation.read(pid, vid)["requirements"][0]["latest_run"]
    assert latest["recorded_status"] == "passed"
    assert latest["status"] == "blocked"


def test_read_only_rejects_save_and_reads_never_modify_files(scenario, tmp_path):
    store, _service, _simulation, pid, vid, run_id = scenario
    save_plan(scenario)
    readonly = WorkbenchService(store.base_dir, tmp_path / "readonly", read_only=True)
    view = SimulationWorkbenchService(readonly)
    before = {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()}
    data = view.read(pid, vid)
    view.replay(pid, vid, run_id)
    with pytest.raises(PermissionError):
        view.save(pid, vid, suite=_suite(), requirement_links={}, issue_ids=[], expected_ir_sha256=data["ir_sha256"])
    assert before == {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()}


def test_http_simulation_routes_require_operator_and_existing_approval(scenario):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    store, service, _simulation, pid, vid, _run = scenario
    # Scenario owns lifecycle already; TestClient without context does not start twice.
    app = create_app(store.base_dir, service=service, operator_token="operator", agent_token="agent")
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    path = f"/api/projects/{pid}/versions/{vid}/simulation-workbench"
    assert client.get(path).status_code == 401
    session = client.post("/api/session", json={"token": "operator"}).json()
    data = client.get(path).json()
    command = {"suite": _suite(), "requirement_links": {"motor_stop": ["SEM001"]},
               "issue_ids": ["finding"], "expected_ir_sha256": data["ir_sha256"]}
    assert client.post(path + "/plans", json=command).status_code == 403
    response = client.post(path + "/plans", json=command, headers={"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": session["csrf"]})
    assert response.status_code == 201, response.text
    assert response.json()["issue_ids"] == ["finding"]
    assert "execute" not in response.json()
    assert client.get(path + "/live").status_code == 200
    assert client.get(path + "/live").json() is None


def test_saved_replay_exposes_one_timeline_for_all_tests_without_starting_a_server(scenario):
    _store, _service, simulation, pid, vid, run_id = scenario
    replay = simulation.replay(pid, vid, run_id)
    timeline = replay["timeline"]
    assert timeline["source"]["kind"] == "recorded_test_execution"
    assert timeline["sample_count"] > 0 and timeline["config"]["signals"]
    assert timeline["binding"] == {"project_id": pid, "version_id": vid}
    assert "url" not in timeline
