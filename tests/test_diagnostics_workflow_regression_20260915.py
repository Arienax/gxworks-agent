"""Regressions minimized from the 2026-09-15 diagnostics archive."""
import json
from pathlib import Path

import pytest

import application.model_api as api
from application.job_errors import workflow_error_code
from application.jobs import JobManager
from application.workspace import WorkspaceWriterLock
from model_runtime.provider import ModelProviderError


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "diagnostics_workflow_regression_20260915.json")
    .read_text(encoding="utf-8")
)
APP = (Path(__file__).parents[1] / "web" / "src" / "App.tsx").read_text(encoding="utf-8")


class RateLimitedProvider:
    def __init__(self, error):
        self.error = error
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        raise self.error
        yield  # pragma: no cover - keep this a generator


def _rate_limit_error():
    observed = FIXTURE["provider_exception"]
    return ModelProviderError(
        "rate limited",
        code=observed["code"],
        retryable=True,
        status_code=observed["status_code"],
    )


@pytest.mark.parametrize("stream", [False, True])
def test_analysis_rate_limit_propagates_without_json_parser(stream, monkeypatch):
    observed = FIXTURE["provider_exception"]
    provider = RateLimitedProvider(_rate_limit_error())
    parser_calls = []

    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")

    def fail_if_parsed(*args, **kwargs):
        parser_calls.append((args, kwargs))
        raise AssertionError("provider failure must not reach analysis JSON parsing")

    monkeypatch.setattr(api, "_parse_analysis_response", fail_if_parsed)
    function = api.analyze_requirement_streaming if stream else api.analyze_requirement
    options = {"response_language": "zh-CN"}
    if stream:
        options["on_content_chunk"] = lambda _text: None

    with api.provider_scope(provider), pytest.raises(ModelProviderError) as caught:
        function("modify the current saved project", **options)

    assert caught.value.code == observed["code"]
    assert caught.value.status_code == observed["status_code"]
    assert caught.value.retryable is True
    assert workflow_error_code(caught.value) == FIXTURE["expected"]["provider_error_code"]
    assert parser_calls == []
    assert len(provider.requests) == 1


def test_rate_limit_reaches_job_manager_as_model_rate_limit(tmp_path, monkeypatch):
    provider = RateLimitedProvider(_rate_limit_error())
    parser_calls = []
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")

    def fail_if_parsed(*args, **kwargs):
        parser_calls.append((args, kwargs))
        raise AssertionError("provider failure must not reach analysis JSON parsing")

    monkeypatch.setattr(api, "_parse_analysis_response", fail_if_parsed)
    with WorkspaceWriterLock(tmp_path / "workspace", tmp_path / "locks") as lock:
        manager = JobManager(tmp_path / "state", lock)
        try:
            def worker(_ctx):
                with api.provider_scope(provider):
                    return api.analyze_requirement(
                        "modify the current saved project",
                        response_language="zh-CN",
                    )

            job = manager.submit(
                "analysis",
                {"project_id": "project", "response_language": "zh-CN"},
                worker,
            )
            manager._futures[job["id"]].result(timeout=5)
            completed = manager.get(job["id"])
        finally:
            manager.shutdown()

    assert completed["status"] == "failed"
    assert completed["error_code"] == FIXTURE["expected"]["provider_error_code"]
    assert parser_calls == []
    assert len(provider.requests) == 1


def _between(start: str, end: str) -> str:
    return APP.split(start, 1)[1].split(end, 1)[0]


def test_saved_confirmed_project_uses_explicit_edit_regenerate_route():
    assert FIXTURE["observed_job"]["kind"] == "analysis"
    assert FIXTURE["project"] == {
        "workflow_mode": "generate",
        "has_confirmed_spec": True,
        "has_saved_version": True,
    }
    assert FIXTURE["expected"]["edit_regenerate_kind"] == "generation"

    route = _between("function syncComposerRoute(", "useEffect(() => {")
    assert '"edit-regenerate"' in route
    assert '"new-requirement"' in route
    assert "value.confirmed_spec" in route
    assert "value.version_count" in route
    assert "value.versions?.length" in route
    assert 'setIntent(editRegenerate ? "generation" : "analysis")' in route
    assert "text" not in route


def test_composer_route_is_resynchronized_when_same_project_matures():
    initial_load = _between('api<Project>(`/projects/${pid}`)', '.catch((e) => setError(e.message))')
    silent_reload = _between("async function reloadProjectSilently(", "async function refreshDrawing(")
    saved_version = _between("async function openSavedVersion(", "async function redrawVersion(")

    assert "syncComposerRoute(value)" in initial_load
    assert "syncComposerRoute(fresh)" in silent_reload
    assert "syncComposerRoute(fresh)" in saved_version
