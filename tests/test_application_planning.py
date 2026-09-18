import copy
import threading

import pytest

import application.model_workflows as api
from application.base import WorkflowError
from application.planning import EvidenceDebugPlanWorkflow, SimulatorTestPlanWorkflow
from application.review import InspectionWorkflow
from plc.ir import build_plc_ir
from test_generation_workflow import _ladder
from test_plc_debug_loop import _diagnosis, _knowledge, _patch, _project_with_failure
from test_plc_multi_agent import _specialist_output


def _suite():
    return {"name": "Regression", "plc_model": "FX3U", "tests": [{
        "name": "Start output", "plc_model": "FX3U", "initial": {"X0": 0},
        "steps": [{"at_ms": 5, "set": {"X0": 1}}, {"at_ms": 10, "expect": {"Y0": 1}}],
        "trace_devices": ["X0", "Y0"], "sample_ms": 5, "timeout_ms": 20,
    }]}


def test_basic_review_has_no_model_dependency_and_emits_local_result(monkeypatch):
    monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("Model initialized"))
    events = []
    ladder = _ladder()
    workflow = InspectionWorkflow("review", "program_review", {}, ladder, "v1", "FX3U",
                                  deep=False, on_event=lambda *event: events.append(event))
    ladder.clear()
    result = workflow.run()
    assert result["base_version_id"] == "v1"
    assert [kind for kind, payload in events] == ["progress", "local"]
    assert events[-1][1] == result


def test_deep_review_keeps_local_result_when_ai_fails(monkeypatch):
    monkeypatch.setattr(api, "run_multi_agent_specialist", lambda *a, **k: (_ for _ in ()).throw(ValueError("Offline error")))
    workflow = InspectionWorkflow("review", "program_review", {}, _ladder(), "v1", "FX3U", provider=object())
    result = workflow.run()
    assert result["ai_status"] == "failed"
    assert result["status"] == "partial"
    assert result["base_version_id"] == "v1"
    assert result["ai_error"] == "模型服务调用失败，请检查配置或稍后重试。"


def test_deep_review_retains_fixed_version_bound_specialist_route(monkeypatch):
    calls = []
    def specialist(role, payload, **kwargs):
        calls.append(role)
        return _specialist_output(payload, title=role)
    monkeypatch.setattr(api, "run_multi_agent_specialist", specialist)
    result = InspectionWorkflow("review", "program_review", {}, _ladder(), "v1", "FX3U",
                                project_id="p1", provider=object()).run()
    assert result["status"] == "complete"
    assert calls == ["reviewer", "timing_planner"]
    assert result["multi_agent"]["binding"]["version_id"] == "v1"
    assert result["multi_agent"]["authority"]["may_import"] is False


@pytest.mark.parametrize("stale", [False, True])
def test_test_plan_uses_frozen_ir_and_checks_under_write_lock(monkeypatch, stale):
    lock = threading.RLock()
    saved = []
    program = build_plc_ir(_ladder())
    class Store:
        def load_program_ir(self, *_args):
            pytest.fail("Frozen program replaced by current selection")
        def save_simulator_test_plan(self, project, version, suite, **kwargs):
            assert lock._is_owned()
            saved.append((project, version, suite))
            return {"suite": suite, **kwargs}
    monkeypatch.setattr(api, "generate_simulator_test_suite", lambda *a, **k: _suite())
    def before_save():
        assert lock._is_owned()
        if stale:
            raise ValueError("Base version changed")
    workflow = SimulatorTestPlanWorkflow("plan", Store(), "p1", "v1", program_ir=program,
                                         write_lock=lock, before_save=before_save)
    program.clear()
    if stale:
        with pytest.raises(WorkflowError, match="Base version changed"):
            workflow.run()
        assert saved == []
    else:
        result = workflow.run()
        assert len(saved) == 1
        assert result["source"] == "ai"


def test_evidence_plan_persists_bound_candidate_without_activating_version(monkeypatch, tmp_path):
    import application.debug_loop as plc_debug_loop
    store, project, version, program, run_id = _project_with_failure(tmp_path)
    monkeypatch.setattr(plc_debug_loop, "retrieve_knowledge", _knowledge)
    monkeypatch.setattr(api, "debug_evidence_diagnosis", lambda *a, **k: _diagnosis())
    monkeypatch.setattr(api, "debug_evidence_patch", lambda *a, **k: _patch(program))
    before = store.get_project(project)
    events = []
    result = EvidenceDebugPlanWorkflow("debug", store, project, version, run_id,
                                       program_ir=program,
                                       saved_run=store.load_simulator_run(project, version, run_id),
                                       on_event=lambda *event: events.append(event)).run()
    assert result["base_version_id"] == version
    assert result["source_run_id"] == run_id
    assert result["multi_agent"]["route"] == ["debug_agent", "patch_agent"]
    assert result["candidate_ir"]["revision"] == program["revision"] + 1
    after = store.get_project(project)
    assert after["active_version_id"] == before["active_version_id"]
    assert len(after["versions"]) == len(before["versions"])
    assert {kind for kind, payload in events} == {"progress"}
