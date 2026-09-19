"""Explicit mode transport and retry identity; no paid model or native calls."""
import copy
import hashlib
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

import application.workbench as workbench
from application.workspace import ConflictError, atomic_json, canonical_hash
from integrations.web.schemas import JobCreate


@pytest.mark.parametrize("mode", [None, "direct", "design"])
def test_http_command_mode_default_and_explicit_selection(mode):
    values = {"kind": "analysis", "project_id": "project_test", "request_id": "request_test"}
    if mode is not None:
        values["analysis_mode"] = mode
    assert JobCreate(**values).analysis_mode == (mode or "direct")
    schema = JobCreate.model_json_schema()
    assert schema["properties"]["analysis_mode"]["enum"] == ["direct", "design"]
    assert "analysis_mode" not in schema.get("required", [])


def service_stub(tmp_path):
    service = workbench.WorkbenchService.__new__(workbench.WorkbenchService)
    service.state_dir = tmp_path
    service.lock = SimpleNamespace(thread_lock=RLock())
    service.writable = lambda: None
    calls = []
    def submit(command):
        calls.append(copy.deepcopy(command))
        return {"id": "job_test"}
    service._submit_job = submit
    service.jobs = SimpleNamespace(get=lambda job_id: {"id": job_id})
    return service, calls


def command(**extra):
    return {"kind": "analysis", "project_id": "project_test", "request_id": "request_test",
            "text": "比较三泵控制方案", "response_language": "zh-CN", **extra}


def test_default_is_frozen_without_mutating_the_callers_command(tmp_path):
    service, calls = service_stub(tmp_path)
    request = command()
    service.submit(request)
    assert "analysis_mode" not in request
    assert calls[0]["analysis_mode"] == "direct"
    assert service.submit(command(analysis_mode="direct"))["id"] == "job_test"
    assert len(calls) == 1


def test_different_mode_is_a_different_request_not_a_retry(tmp_path):
    service, calls = service_stub(tmp_path)
    service.submit(command(analysis_mode="design"))
    assert service.submit(command(analysis_mode="design"))["id"] == "job_test"
    with pytest.raises(ConflictError):
        service.submit(command(analysis_mode="direct"))
    assert len(calls) == 1
    assert calls[0]["analysis_mode"] == "design"


def test_legacy_retry_returns_original_job_without_reinterpreting_it(tmp_path):
    service, calls = service_stub(tmp_path)
    request = command()
    name = hashlib.sha256(request["request_id"].encode()).hexdigest() + ".json"
    atomic_json(tmp_path / "job_commands" / name,
                {"command_hash": canonical_hash(request), "job_id": "legacy_job"})
    assert service.submit(request)["id"] == "legacy_job"
    assert calls == []
    with pytest.raises(ConflictError):
        service.submit(command(analysis_mode="design"))


def test_analysis_mode_is_not_added_to_generation_identity(tmp_path):
    service, calls = service_stub(tmp_path)
    service.submit(command(kind="generation", analysis_mode="design"))
    assert "analysis_mode" not in calls[0]
    assert service.submit(command(kind="generation"))["id"] == "job_test"
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["direct", "design"])
def test_submit_freezes_mode_in_the_actual_job_snapshot(tmp_path, mode):
    service, _ = service_stub(tmp_path)
    del service._submit_job
    context = SimpleNamespace(project={"plc_model": "FX3U", "target_mode": "ladder"},
                              version=None, version_id=None, program_ir=None)
    service.projects = SimpleNamespace(tool_context=lambda *args: context)
    service._command_scope = lambda *args: None
    service._attachments = lambda *args: []
    service.model_factory = lambda: (None, {})
    service.approval = SimpleNamespace(read=lambda: {"mode": "ask"})
    saved = []
    def submit(kind, snapshot, worker, **kwargs):
        saved.append(copy.deepcopy(snapshot))
        return {"id": "job_test"}
    service.jobs = SimpleNamespace(submit=submit)
    request = command(analysis_mode=mode)
    service.submit(request)
    request["analysis_mode"] = "direct" if mode == "design" else "design"
    assert saved[0]["analysis_mode"] == mode
    assert "analysis_mode" not in saved[0]["project"]


def test_single_approach_is_preselected_but_required_parameters_stay_unanswered():
    from plc.specification.confirmed import build_review_draft, validate_spec_draft
    analysis = {"plc_model": "FX3U", "analysis_mode": "direct", "summary": "延时启动",
                "approaches": [{"approach_id": "direct_plan", "name": "延时控制",
                                "generation_guide": "按确认的延时启动输出。"}],
                "missing_info": [{"id": "delay_seconds", "question": "延时时间？",
                                  "required": True, "default": "5", "options": ["5", "10"]}],
                "suggested_io": {"X": {"X0": "启动按钮"}, "Y": {"Y0": "输出"}}}
    draft = build_review_draft(analysis)
    assert draft["selected_approach"]["approach_id"] == "direct_plan"
    question = next(item for item in draft["parameters"] if item["id"] == "delay_seconds")
    assert question["required"] is True
    assert question["value"] == ""
    assert validate_spec_draft(draft, "FX3U")["errors"]
    assert "analysis_mode" not in draft
    assert "analysis_mode" not in draft["selected_approach"].get("generation_contract", {})


@pytest.mark.parametrize("mode", [None, "direct", "design"])
def test_analysis_worker_passes_snapshot_mode_and_persists_it(monkeypatch, tmp_path, mode):
    import application.model_api as api
    import plc.specification.confirmed as confirmed
    import json

    calls = []
    analysis = {"summary": "one implementation", "approaches": [{"name": "direct plan"}]}
    def analyze(text, **kwargs):
        calls.append((text, kwargs))
        return copy.deepcopy(analysis)
    monkeypatch.setattr(api, "analyze_requirement_streaming", analyze)
    monkeypatch.setattr(confirmed, "build_review_draft", lambda result, previous: {"summary": result["summary"]})
    service = workbench.WorkbenchService.__new__(workbench.WorkbenchService)
    service.state_dir = tmp_path
    snapshot = {**command(), "project": {"confirmed_spec": None, "messages": []}, "version_id": None}
    if mode is not None:
        snapshot["analysis_mode"] = mode
    ctx = SimpleNamespace(job_id="job_mode", emit=lambda *args: None)
    service._run_job(ctx, snapshot, None, [], None)
    output = json.loads((tmp_path / "outputs" / "job_mode.json").read_text())
    assert len(calls) == 1
    assert calls[0][1]["analysis_mode"] == (mode or "direct")
    assert output["analysis_mode"] == (mode or "direct")
    assert "analysis_mode" not in output["spec_draft"]
