"""Diagnostic transport tests; these do not reproduce a real model/UI failure."""
import json

import pytest

from application.jobs import JobManager
from application.workspace import WorkspaceWriterLock, public_payload
from model_runtime.provider import AssistantMessage, ModelRequest, RawModelResponse, ResponseRejectedError, UserMessage
from model_runtime.responses import LanguageViolation
from application.response_contracts import ANALYSIS_RESPONSE


def _rejection(violations=None):
    request = ModelRequest((UserMessage("起保停"),), response_language="zh-CN", response_contract=ANALYSIS_RESPONSE)
    raw = RawModelResponse(AssistantMessage('{"summary":"中文正文。","private":"raw-secret-sentinel"}',
                                           reasoning="Private reasoning raw-secret-sentinel"), None, (), True)
    return ResponseRejectedError(request, (raw,), violations or (LanguageViolation("reasoning", "latin_prose"),))


@pytest.fixture
def manager(tmp_path):
    with WorkspaceWriterLock(tmp_path / "workspace", tmp_path / "locks") as lock:
        manager = JobManager(tmp_path / "state", lock)
        try:
            yield manager
        finally:
            manager.shutdown()


@pytest.mark.parametrize("wrapped", [False, True])
def test_rejection_details_persist_to_job_and_reconnect_event_without_raw_response(manager, wrapped):
    error = _rejection()
    def worker(ctx):
        try:
            raise error
        except ResponseRejectedError as rejection:
            if wrapped:
                raise RuntimeError("Wrapper contains raw-secret-sentinel") from rejection
            raise
    job = manager.submit("analysis", {"project_id": "project", "response_language": "zh-CN"}, worker)
    manager._futures[job["id"]].result(timeout=5)
    completed = manager.get(job["id"])
    assert completed["status"] == "failed" and completed["error_code"] == "response_rejected"
    details = completed["error_details"]
    assert details == {"response_language": "zh-CN", "contract_name": "analysis",
        "diagnostic_id": error.response_sha256[:16], "violations": [{"path": "reasoning", "reason": "latin_prose"}],
        "violation_count": 1, "truncated": False}
    event = manager.events(job["id"], completed["last_sequence"] - 1)[0]
    assert event["event_type"] == "failed" and event["payload"]["error_details"] == details
    assert not any(e["event_type"] in ("content", "reasoning") for e in manager.events(job["id"]))
    persisted = (manager.directory / (job["id"] + ".json")).read_text(encoding="utf-8")
    assert "raw-secret-sentinel" not in persisted and "raw_attempts" not in persisted and "raw_response" not in persisted


def test_dynamic_json_keys_and_unknown_reasons_cannot_leak_through_diagnostics(manager):
    violations = (
        LanguageViolation("content$.suggested_io.X.api-secret-key", "latin_prose"),
        LanguageViolation("tool_calls[2].arguments.ladder.rungs.0.debug_note", "unsupported_script"),
        LanguageViolation("C:/private/private-secret", "private-secret"),
        LanguageViolation("content$.summary", "invalid_prose_field"),
    )
    error = _rejection(violations)
    error.contract_name = "private-secret"
    def worker(ctx):
        raise error
    job = manager.submit("analysis", {}, worker)
    manager._futures[job["id"]].result(timeout=5)
    details = manager.get(job["id"])["error_details"]
    assert details["contract_name"] == "custom"
    assert details["violations"] == [
        {"path": "content$.suggested_io.X.*", "reason": "latin_prose"},
        {"path": "tool_calls[2].arguments.ladder.rungs.0.debug_note", "reason": "unsupported_script"},
        {"path": "response", "reason": "invalid_response"},
        {"path": "content$.summary", "reason": "invalid_prose_field"},
    ]
    wire = json.dumps(manager.get(job["id"])) + json.dumps(manager.events(job["id"]))
    assert "private-secret" not in wire and "api-secret-key" not in wire


def test_diagnostic_projection_is_bounded_and_does_not_widen_path_whitelist():
    details = {"response_language": "zh-CN", "contract_name": "analysis", "diagnostic_id": "a" * 16,
               "violations": [{"path": "content$.summary", "reason": "latin_prose", "raw": "private-secret"}] * 20,
               "violation_count": 20, "api_key": "private-secret"}
    projected = public_payload({"path": "relative-private-file", "error_details": details})
    assert "path" not in projected
    assert len(projected["error_details"]["violations"]) == 16
    assert projected["error_details"]["violation_count"] == 20
    assert projected["error_details"]["truncated"] is True
    assert "private-secret" not in json.dumps(projected)
    assert public_payload(projected) == projected


def test_other_errors_never_publish_arbitrary_attributes(manager):
    error = RuntimeError("SDK secret-sentinel")
    error.error_details = {"message": "secret-sentinel"}
    error.violations = (LanguageViolation("secret-sentinel", "secret-sentinel"),)
    def worker(ctx):
        raise error
    job = manager.submit("analysis", {}, worker)
    manager._futures[job["id"]].result(timeout=5)
    result = manager.get(job["id"])
    assert result["error_code"] == "job_failed" and result["error_details"] is None
    assert "secret-sentinel" not in json.dumps(manager.events(job["id"]))


def test_old_job_records_remain_readable_without_error_details(manager):
    job = manager.submit("analysis", {}, lambda ctx: {"status": "completed"})
    manager._futures[job["id"]].result(timeout=5)
    saved = manager._load(job["id"])
    saved.pop("error_details", None)
    manager._save(saved)
    assert manager.get(job["id"])["error_details"] is None


def test_http_job_and_sse_deliver_same_safe_diagnostic(manager, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from integrations.web.responses import Job, JobEvent
    def worker(ctx):
        raise _rejection((LanguageViolation("content", "invalid_json_object"),))
    job = manager.submit("analysis", {}, worker)
    manager._futures[job["id"]].result(timeout=5)
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(manager.lock.workspace, tmp_path / "unused-state", read_only=True)
    service.jobs = manager
    app = create_app(manager.lock.workspace, service=service, origin=origin, operator_token="operator-test")
    with TestClient(app, base_url=origin) as client:
        client.post("/api/session", json={"token": "operator-test"}, headers={"Origin": origin})
        result = client.get("/api/jobs/" + job["id"])
        assert result.status_code == 200
        parsed = Job.model_validate(result.json())
        assert parsed.error_details.violations[0].reason == "invalid_json_object"
        response = client.get("/api/jobs/" + job["id"] + "/events?after=2")
        assert response.status_code == 200
        event = JobEvent.model_validate(json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))))
        assert event.payload["error_details"] == parsed.error_details.model_dump(exclude_unset=True)
        assert "raw-secret-sentinel" not in response.text + result.text


def test_generation_failure_details_survive_job_http_and_sse(manager, tmp_path):
    from plc.candidate_repair import GenerationValidationError
    from plc.validation import PLCJsonValidationError
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from integrations.web.responses import Job, JobEvent
    error = GenerationValidationError([PLCJsonValidationError(
        "$.rungs[36].shared_inputs[3].type: unknown type 'parallel_block'; raw-secret-sentinel")],
        attempts=3, language="zh-CN")
    def worker(ctx): raise error
    job = manager.submit("generation", {}, worker)
    manager._futures[job["id"]].result(timeout=5)
    completed = manager.get(job["id"])
    assert completed["error_code"] == "generation_validation_failed"
    assert completed["error_details"]["attempt_count"] == 3
    assert completed["error_details"]["violations"] == [
        {"path": "content$.rungs.36.shared_inputs.3.type", "reason": "invalid_shared_input"}]
    assert "raw-secret-sentinel" not in json.dumps(manager.events(job["id"]))
    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(manager.lock.workspace, tmp_path / "read-state", read_only=True)
    service.jobs = manager
    with TestClient(create_app(manager.lock.workspace, service=service, origin=origin,
                    operator_token="test-operator"), base_url=origin) as client:
        client.post("/api/session", json={"token": "test-operator"}, headers={"Origin": origin})
        resource = Job.model_validate(client.get("/api/jobs/" + job["id"]).json())
        stream = client.get("/api/jobs/" + job["id"] + "/events?after=2")
        event = JobEvent.model_validate(json.loads(next(line[6:] for line in stream.text.splitlines() if line.startswith("data: "))))
        assert resource.error_details.stage == "generation_validation"
        assert event.payload["error_details"] == resource.error_details.model_dump(exclude_unset=True)


def test_generation_diagnostics_remain_bounded_when_read_from_disk():
    from application.job_errors import public_error_details
    data = {"stage": "generation_validation", "attempt_count": 99999, "max_attempts": True,
            "stop_reason": "secret-sentinel", "response_language": "zh-CN", "contract_name": "ladder",
            "violations": [{"path": "content$.rungs.36.secret_sentinel", "reason": "secret-sentinel"}]}
    result = public_error_details(data)
    assert result["attempt_count"] == 3 and result["max_attempts"] == 0
    assert "sentinel" not in json.dumps(result)
    assert public_error_details(result) == result
