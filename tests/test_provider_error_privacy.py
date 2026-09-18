"""Provider failures cannot disclose credentials through tasks or inspections."""
import json

import pytest

import application.model_api as api
from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from application.planning import EvidenceDebugPlanWorkflow
from application.base import WorkflowError
from application.workbench import WorkbenchService
from model_runtime.provider import ModelProviderError, ModelRequest, OpenAICompatibleProvider, UserMessage, collect_response
from storage.session import SessionStore
from test_candidate_diff import _program, _save


SENTINEL = "sk-private-auth-sentinel-DO-NOT-DISCLOSE"


def _failure(typed=True):
    message = f"Incorrect API key provided: {SENTINEL}; Authorization: Bearer {SENTINEL}"
    return ModelProviderError(message, code="authentication", status_code=401) if typed else RuntimeError(message)


class _FailingProvider:
    def __init__(self, typed=True):
        self.typed, self.calls = typed, []

    def stream(self, request):
        self.calls.append(request.stream)
        raise _failure(self.typed)
        yield


@pytest.mark.parametrize("status", [401, 429, 503, None])
def test_sdk_exception_text_remains_only_in_private_cause(status):
    from test_model_provider import _Client, _profile
    original = RuntimeError(f"Incorrect API key provided: {SENTINEL}")
    original.status_code = status
    provider = OpenAICompatibleProvider(_profile("deepseek-default"), "offline-key", client=_Client([original]))
    with pytest.raises(ModelProviderError) as captured:
        list(provider.stream(ModelRequest((UserMessage("Hello"),))))
    assert SENTINEL not in str(captured.value)
    assert captured.value.__cause__ is original
    assert captured.value.status_code == status


@pytest.mark.parametrize("typed", [False, True])
def test_collect_response_classifies_errors_without_exposing_text_or_changing_fallback(typed):
    provider, fallbacks = _FailingProvider(typed), []
    with pytest.raises(ModelProviderError) as captured:
        collect_response(provider, ModelRequest((UserMessage("Hello"),)), fallback_to_non_stream=True,
            on_fallback=lambda error: fallbacks.append(str(error)))
    assert SENTINEL not in str(captured.value) + repr(fallbacks)
    assert captured.value.code == ("authentication" if typed else "provider_error")
    assert provider.calls == [True] and fallbacks == []
    assert captured.value.__cause__ is not None


@pytest.mark.parametrize("typed", [False, True])
def test_generation_injected_transport_error_is_safe_and_never_replayed(tmp_path, capsys, typed):
    events, calls = [], []
    def fail_stream(*args, **kwargs):
        calls.append("stream")
        raise _failure(typed)
    def fallback(*args, **kwargs):
        calls.append("fallback")
        return '{"st_code":"Y0 := X0;"}'
    from plc.candidate_repair import GenerationError
    with pytest.raises(GenerationError) as failure:
        GenerationWorkflow(GenerationRequest("Input controls output", target_mode="st", model_name="offline"),
            tmp_path, lambda *event: events.append(event),
            GenerationDependencies(stream_response=fail_stream, generate_json=fallback)).run()
    assert list(tmp_path.iterdir()) == []
    assert calls == ["stream"]
    assert SENTINEL not in str(failure.value) + json.dumps(events, ensure_ascii=False) + capsys.readouterr().out
    assert not any(kind == "progress" and payload.get("stage") == "fallback" for kind, payload in events)


@pytest.mark.parametrize("typed", [False, True])
def test_evidence_planning_injected_model_error_never_escapes_public_exception(tmp_path, monkeypatch, typed):
    from test_plc_debug_loop import _project_with_failure
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    def fail(*args, **kwargs):
        raise _failure(typed)
    monkeypatch.setattr(api, "debug_evidence_diagnosis", fail)
    events = []
    workflow = EvidenceDebugPlanWorkflow("debug", store, project_id, version_id, run_id,
        program_ir=program, saved_run=store.load_simulator_run(project_id, version_id, run_id),
        provider=object(), model_name="offline", on_event=lambda *event: events.append(event))
    with pytest.raises(WorkflowError) as captured:
        workflow.run()
    assert SENTINEL not in str(captured.value) + json.dumps(events, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["generation", "review", "test_plan", "agent", "analysis"])
@pytest.mark.parametrize("typed", [False, True])
def test_http_jobs_events_outputs_and_saved_reports_never_echo_provider_credentials(tmp_path, monkeypatch, capsys, kind, typed):
    pytest.importorskip("fastapi", reason="HTTP privacy regression requires requirements-web.txt")
    pytest.importorskip("httpx", reason="HTTP privacy regression requires httpx")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(api, "_build_model_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(api, "load_full_config", lambda: {})
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project("Privacy fixture")["id"]
    version_id = _save(store, project_id, program=_program())
    provider = _FailingProvider(typed)
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (provider, {"model": "offline"}))
    origin = "http://127.0.0.1:8765"
    app = create_app(store.base_dir, state_dir=tmp_path / "state", origin=origin, service=service,
        operator_token="privacy-operator")
    with TestClient(app, base_url=origin) as client:
        login = client.post("/api/session", json={"token": "privacy-operator"}, headers={"Origin": origin})
        assert login.status_code == 200
        headers = {"Origin": origin, "X-CSRF-Token": login.json()["csrf"]}
        submitted = client.post("/api/jobs", headers=headers, json={"kind": kind, "project_id": project_id,
            "version_id": version_id, "request_id": "auth-error", "text": "Inspect the program", "response_language": "en"})
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["id"]
        service.jobs._futures[job_id].result(timeout=15)
        job = client.get(f"/api/jobs/{job_id}")
        stream = client.get(f"/api/jobs/{job_id}/events")
        output = client.get(f"/api/jobs/{job_id}/output")
        assert stream.status_code == 200
        public_text = job.text + stream.text + output.text
        assert job.json()["status"] == ("completed" if kind == "review" else "failed")
        if kind == "review":
            report_id = job.json()["result"]["report_id"]
            report = client.get(f"/api/projects/{project_id}/reports/{report_id}")
            assert report.status_code == 200
            assert report.json()["ai_status"] == "failed" and report.json()["status"] == "partial"
            public_text += report.text
            assert "authentication failed" in report.text if typed else "model service request failed" in report.text
        assert SENTINEL not in public_text + capsys.readouterr().out
        assert not service.proposals.list(project_id)
        assert len(store.get_project(project_id)["versions"]) == 1
        assert provider.calls, "Regression must exercise the provider failure, not an earlier setup error"
    for path in [*store.base_dir.rglob("*.json"), *(tmp_path / "state").rglob("*.json")]:
        assert SENTINEL not in path.read_text(encoding="utf-8"), path
