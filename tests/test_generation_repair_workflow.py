"""Generation fast path: no hidden semantic/validation re-generation."""
import json
import pytest

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from plc.candidate_repair import GenerationValidationError
from application.jobs import JobCancelled
from model_runtime.provider import ModelProviderError
from test_generation_repair_assembly import ladder40


def workflow(tmp_path, initial, *, previous=None, retry=None, cancelled=None):
    calls = []
    def generate_json(*args, **kwargs):
        calls.append((args, kwargs))
        if retry is None:
            pytest.fail("Direct generation must not enter a hidden repair call")
        if isinstance(retry, Exception):
            raise retry
        return json.dumps(retry, ensure_ascii=False)
    task = GenerationWorkflow(
        GenerationRequest("按已确认规格生成", previous_json=previous, model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=lambda *a, **k: ("", json.dumps(initial, ensure_ascii=False)),
            generate_json=generate_json,
            check_cancelled=cancelled,
        ),
    )
    return task, calls


def test_structurally_valid_candidate_never_calls_remote_repair(tmp_path):
    candidate = ladder40(invalid=False)
    task, calls = workflow(tmp_path, candidate)
    result = task.run()
    assert result["validation"]["status"] == "candidate_ready"
    assert result["repair_attempts"] == 0
    assert calls == []
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == candidate


def test_semantic_style_problem_is_not_repaired_during_generation(tmp_path):
    candidate = ladder40(invalid=False)
    # Duplicate a normal coil. Historical strict validation rejects this, but it
    # is a review/runtime concern rather than a reason to spend another model call.
    extra = json.loads(json.dumps(candidate["rungs"][0]))
    extra["rung_id"] = 999
    extra["branches"][0]["inputs"][0]["address"] = "X7"
    candidate["rungs"].append(extra)
    task, calls = workflow(tmp_path, candidate)
    result = task.run()
    assert result["validation"]["status"] == "candidate_ready"
    assert result["repair_attempts"] == 0 and calls == []


def test_structural_failure_stops_once_without_remote_repair(tmp_path):
    candidate = ladder40(invalid=False)
    candidate["rungs"][0]["branches"][0]["inputs"][0]["address"] = "BAD_ADDRESS"
    task, calls = workflow(tmp_path, candidate)
    with pytest.raises(GenerationValidationError) as rejected:
        task.run()
    assert rejected.value.diagnostics["attempt_count"] == 0
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", [[], {"rungs": ["bad"]}, {"rungs": [], "device_comments": {}}])
def test_shape_or_empty_candidate_is_not_secretly_regenerated(tmp_path, bad):
    task, calls = workflow(tmp_path, bad)
    with pytest.raises(GenerationValidationError):
        task.run()
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_transport_fallback_remains_distinct_from_semantic_repair(tmp_path):
    from model_runtime.provider import TextDelta
    candidate = ladder40(invalid=False)
    calls = []
    class Provider:
        def stream(self, request):
            calls.append(request.stream)
            if request.stream:
                raise ModelProviderError("explicit stream rejection", code="stream_not_supported")
            yield TextDelta(json.dumps(candidate, ensure_ascii=False))
    task = GenerationWorkflow(
        GenerationRequest("按已确认规格生成", model_name="offline"), tmp_path,
        dependencies=GenerationDependencies(provider=Provider()),
    )
    result = task.run()
    assert result["validation"]["status"] == "candidate_ready"
    assert calls == [True, False] and result["repair_attempts"] == 0


def test_cancellation_is_not_wrapped_or_retried(tmp_path):
    def cancelled():
        raise JobCancelled()
    task, calls = workflow(tmp_path, ladder40(invalid=False), cancelled=cancelled)
    with pytest.raises(JobCancelled):
        task.run()
    assert calls == [] and list(tmp_path.iterdir()) == []
