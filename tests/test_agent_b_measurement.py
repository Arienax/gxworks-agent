"""Actual generation-path measurement contract; all providers here are offline."""
import copy
import json
from types import SimpleNamespace

import pytest

from model_profile_fixtures import offline_runtime_profile

from application.compact_protocol import canonical_compact_example, compact_response_schema, compact_protocol_prompt
from application.generation_agent import _FirstJSONObjectProvider
from model_runtime.provider import ModelRequest, OpenAICompatibleProvider, ReasoningDelta, TextDelta, Usage, UserMessage, _usage_event
from scripts.benchmark_agent_b import ObservedProvider, main, run_case, schedule, summarize


@pytest.mark.parametrize("details", ["completion_tokens_details", "output_tokens_details"])
def test_reported_reasoning_usage_is_preserved_including_empty_choice_tail(details, monkeypatch):
    captured = []
    import shared.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, "emit", lambda event, **fields: captured.append((event, fields)))
    provider = OpenAICompatibleProvider({"adapter": "openai_compatible", "model": "offline"}, "offline-key", client=object())
    metering = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, details: {"reasoning_tokens": 15}}
    response = [
        {"choices": [{"delta": {"content": '{"r":[]}'}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": metering},
    ]
    events = list(provider._streaming_events(response))
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.reasoning_tokens == 15 and usage.raw_usage == metering
    assert captured[-1][1]["reasoning_tokens"] == 15 and captured[-1][1]["finish_seen"]


def test_missing_reasoning_meter_is_unknown_not_zero():
    usage = _usage_event({"prompt_tokens": 10, "completion_tokens": 20})
    assert usage.reasoning_tokens is None
    assert "reasoning_tokens" not in usage.raw_usage
    assert _usage_event({"input_tokens": 10, "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 0}}).reasoning_tokens == 0


def test_json_framing_does_not_drop_reasoning_or_usage_and_observation_sees_duplicates():
    class Provider:
        profile = {}
        def stream(self, request):
            yield ReasoningDelta("先核对事实")
            yield TextDelta('{"r":[]}')
            yield TextDelta('{"r":["duplicate"]}')
            yield ReasoningDelta("保留提供商实际返回的后续记录")
            yield Usage(1, 2, 3, 1)
    observed = ObservedProvider(Provider())
    request = ModelRequest((UserMessage("fixture"),), options={"reasoning_effort": "high"}, stream=True)
    events = list(_FirstJSONObjectProvider(observed).stream(request))
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == '{"r":[]}'
    assert len([event for event in events if isinstance(event, ReasoningDelta)]) == 2
    assert any(isinstance(event, Usage) and event.reasoning_tokens == 1 for event in events)
    attempt = observed.attempts[0]
    assert 'duplicate' in attempt["raw_content"] and attempt["transport_completed"]
    assert attempt["first_content_ms"] <= attempt["json_complete_ms"] <= attempt["transport_ms"]
    assert request.options == {"reasoning_effort": "high"}


def test_prompt_and_schema_have_one_canonical_required_shape():
    import jsonschema
    example = canonical_compact_example()
    jsonschema.validate(example, compact_response_schema())
    schema = compact_response_schema()
    prompt = compact_protocol_prompt()
    rung = schema["properties"]["r"]["items"]
    branch = rung["properties"]["b"]["items"]
    assert "顶层对象必填字段：" + "、".join(schema["required"]) in prompt
    assert "每个梯级必填字段：" + "、".join(rung["required"]) in prompt
    assert "每个输出分支必填字段：" + "、".join(branch["required"]) in prompt
    assert '"root"' not in prompt
    assert "这些字段不省略" in compact_protocol_prompt()


@pytest.mark.parametrize("root_key", ["r", "root"])
def test_runner_uses_real_single_call_path_without_changing_effort(monkeypatch, root_key):
    from test_confirmed_input_protocol import old_confirmed_spec, compact
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: KnowledgeContext("", {"records": []}))
    class Provider:
        profile = offline_runtime_profile()
        def __init__(self):
            self.requests = []
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({root_key: compact()["r"]}))
            yield Usage(10, 20, 30, 12)
    provider = Provider()
    record = run_case({"case_id": "hold", "confirmed_spec": old_confirmed_spec()}, "automatic", provider=provider, effort="high")
    assert record["generation_status"] == "completed", record
    assert record["behavior"] == {"status": "not_covered", "reason": "no_behavior_evaluator"}
    assert record["structural_valid"] and record["semantic_validation"]["legacy_compatibility"] is True
    assert record["model_calls"] == 1 and provider.requests[0].max_retries == 0
    assert "reasoning_effort" not in provider.requests[0].options
    assert record["attempts"][0]["usage"]["reasoning_tokens"] == 12
    assert agent._build_knowledge_context("fixture").manifest == {"records": []}


@pytest.mark.parametrize("behavior_status", ["verified", "failed", "not_covered"])
def test_benchmark_keeps_semantic_receipts_separate_from_behavior(monkeypatch, behavior_status):
    from test_generation_agent_boundary import OneShotProvider, _spec
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    specification = _spec()
    specification["selected_approach"] = {
        "approach_id": "direct", "name": "direct",
        "implementation_semantics": [{"kind": "structure", "status": "required", "value": "direct_logic"}],
        "explicit_user_constraints": {"required_opcodes": ["MOV"]},
    }
    inspected = []
    def evaluate(case, result):
        inspected.append(result["ladder"])
        return {"status": behavior_status, "source": "offline_evaluator"}
    provider = OneShotProvider()
    record = run_case({"case_id": "semantic-receipt", "confirmed_spec": specification},
                      "automatic", provider=provider, evaluator=evaluate)
    assert record["generation_status"] == "completed", record
    assert record["structural_valid"]
    assert record["semantic_validation"]["status"] == "violated"
    assert record["semantic_validation"]["violations"]
    assert record["behavior"] == {"status": behavior_status, "source": "offline_evaluator"}
    assert len(inspected) == len(provider.requests) == record["model_calls"] == 1


def test_dry_run_never_loads_provider_or_calls_model(tmp_path, monkeypatch, capsys):
    import model_runtime.provider as provider
    monkeypatch.setattr(provider, "get_active_provider", lambda: pytest.fail("Dry-run must not access credentials"))
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"case_id": "fixture", "confirmed_spec": {"summary": "example"}}), encoding="utf-8")
    assert main([str(path), "--repeat", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["scheduled_runs"] == 4


def test_failure_and_unknown_usage_are_not_silently_dropped():
    rows = [{"arm": "automatic", "end_to_end_ms": 100, "generation_status": "failed",
             "behavior": {"status": "not_covered"}, "attempts": [{"usage": None}]},
            {"arm": "automatic", "end_to_end_ms": 200, "generation_status": "completed",
             "behavior": {"status": "verified"}, "attempts": [{"usage": {"reasoning_tokens": 20}}]}]
    result = summarize(rows)
    assert result["groups"]["automatic"]["runs"] == 2
    assert result["groups"]["automatic"]["reasoning_usage_known_runs"] == 1
    assert result["performance_acceptance"] == "not_established"
    tasks = schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)
    assert len(tasks) == 12 and tasks == schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)


def test_transcript_metering_and_custom_headers_remain_credential_redacted(tmp_path):
    from shared.tracing import sanitize, record_model_response
    headers = {"extra_headers": {"X-Api-Key": "nonstandard-secret", "Cookie": "session-secret"}}
    assert sanitize(headers) == {"extra_headers": {"X-Api-Key": "<redacted>", "Cookie": "<redacted>"}}
    request = ModelRequest((UserMessage("fixture"),))
    from model_runtime.provider import AssistantMessage, RawModelResponse
    raw = RawModelResponse(AssistantMessage("{}"), Usage(10, 20, 30, 12, {"completion_tokens_details": {"reasoning_tokens": 12}}), (), False, "")
    assert record_model_response(tmp_path, "job_fixture", 1, 1, raw, request)
    row = json.loads((tmp_path / "diagnostics/job_fixture.transcript.jsonl").read_text())
    assert row["usage"]["reasoning_tokens"] == 12
    assert row["usage"]["raw_usage"]["completion_tokens_details"]["reasoning_tokens"] == 12


@pytest.mark.parametrize("case_id", ["design-conveyor", "typed-parameter-identity", "user-edited-prose", "legacy-generation-view", "historical-review-recovery", "servo-typed-handoff"])
def test_offline_context_replay_uses_real_components_and_single_completions(case_id):
    from scripts.context_replay import load_cases, run_case
    case = next(row for row in load_cases() if row["case_id"] == case_id)
    result = run_case(case, include_content=True)
    assert result["passed"], result
    assert result["real_model_calls"] == 0
    assert result["provider_fixture_calls"] == (2 if "analysis" in case else 1)
    assert result["live_latency_improvement"] == "not_measured"
    spec = result["content"]["generation_spec"]
    if case_id == "typed-parameter-identity":
        params = {p["id"]: p for p in spec["parameters"]}
        assert params["duration"]["value"] == 0 and type(params["duration"]["value"]) is int
        assert params["enabled"]["value"] is False
        assert params["transport_mode"]["semantic_key"] == "transport.mode"
    if case_id == "servo-typed-handoff":
        assert "module" not in {p["id"] for p in spec["parameters"]}
        assert all(b["active_level"] == 1 for b in spec["io_bindings"] if b["kind"] == "X")
        assert len([b for b in spec["io_bindings"] if b["kind"] == "X"]) == 3
    if case_id == "legacy-generation-view":
        assert spec["summary"] == case["confirmed_spec"]["summary"]
        assert "note" not in spec["parameters"][0]


def test_offline_replay_refuses_network_and_ambiguous_archives(tmp_path):
    import socket
    import zipfile
    from scripts.context_replay import offline_environment, archive_case
    with offline_environment() as attempts:
        with pytest.raises(RuntimeError):
            socket.create_connection(("provider.invalid", 443))
    assert len(attempts) == 1
    path = tmp_path / "diagnostic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("job.json", "{}")
        archive.writestr("transcript.jsonl", "")
    with pytest.raises(ValueError, match="confirmed specification"):
        archive_case(path)


def test_offline_archive_reader_never_uses_recorded_credentials_or_reconstructs_analysis(tmp_path):
    import zipfile
    from scripts.context_replay import archive_case
    path = tmp_path / "diagnostic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("job.json", json.dumps({"snapshot": {"project": {"confirmed_spec": {"summary": "保留"}}, "api_key": "SECRET_DO_NOT_USE"}}))
        archive.writestr("transcript.jsonl", json.dumps({"event": "model_response", "content": '{"r":[]}'}))
    case = archive_case(path)
    assert "SECRET_DO_NOT_USE" not in json.dumps(case)
    assert "analysis" not in case and case["capture_scope"].startswith("generation_only")


def test_archive_replay_result_is_attached_atomically_and_replaces_prior_member(tmp_path):
    import zipfile
    from scripts.context_replay import REPLAY_MEMBER, attach_replay_result
    path = tmp_path / "gxworks-interaction.zip"
    job = json.dumps({"id": "job_fixture", "snapshot": {"project": {"confirmed_spec": {"summary": "keep"}}}})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("job.json", job)
        archive.writestr("transcript.jsonl", "{}\n")
        archive.writestr("README.txt", "operator export")
    attach_replay_result(path, {"schema_version": 1, "passed": False, "marker": 1})
    attach_replay_result(path, {"schema_version": 1, "passed": True, "marker": 2})
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist().count(REPLAY_MEMBER) == 1
        assert archive.read("job.json").decode() == job
        assert archive.read("README.txt").decode() == "operator export"
        assert json.loads(archive.read(REPLAY_MEMBER)) == {
            "schema_version": 1, "passed": True, "marker": 2
        }
    assert not list(tmp_path.glob("*.replay.tmp"))


def test_archive_cli_writes_back_by_default_but_output_keeps_archive_read_only(tmp_path, monkeypatch, capsys):
    import zipfile
    import scripts.context_replay as replay

    def make_archive(name):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("job.json", "{}")
            archive.writestr("transcript.jsonl", "{}\n")
        return path

    monkeypatch.setattr(replay, "archive_case", lambda path: {"case_id": "operator_archive"})
    monkeypatch.setattr(replay, "run_case", lambda case, include_content=False: {
        "case_id": "operator_archive", "passed": True
    })

    attached = make_archive("attached.zip")
    assert replay.main(["--archive", str(attached)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["member"] == replay.REPLAY_MEMBER and status["passed"] is True
    with zipfile.ZipFile(attached) as archive:
        assert replay.REPLAY_MEMBER in archive.namelist()

    detached_source = make_archive("detached-source.zip")
    detached = tmp_path / "detached.json"
    assert replay.main(["--archive", str(detached_source), "--output", str(detached)]) == 0
    assert json.loads(detached.read_text(encoding="utf-8"))["passed"] is True
    with zipfile.ZipFile(detached_source) as archive:
        assert replay.REPLAY_MEMBER not in archive.namelist()


@pytest.mark.parametrize("operation", ["getaddrinfo", "gethostbyname", "gethostbyname_ex", "sendto"])
def test_offline_replay_blocks_dns_and_datagram_paths_and_restores_patches(operation):
    import socket
    from scripts.context_replay import offline_environment
    owner = socket.socket if operation == "sendto" else socket
    original = getattr(owner, operation)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        with offline_environment() as attempts:
            with pytest.raises(RuntimeError, match="Offline replay forbids"):
                if operation == "sendto":
                    channel.sendto(b"fixture", ("127.0.0.1", 9))
                elif operation == "getaddrinfo":
                    socket.getaddrinfo("provider.invalid", 443)
                else:
                    getattr(socket, operation)("provider.invalid")
        assert attempts == ["blocked_network_or_provider_access"]
    assert getattr(owner, operation) is original


@pytest.mark.parametrize("include_content", [False, True])
def test_failed_candidate_replay_keeps_consumed_response_and_evidence_counts(include_content):
    from scripts.context_replay import load_cases, run_case as replay_case
    case = copy.deepcopy(next(row for row in load_cases() if row["case_id"] == "legacy-generation-view"))
    # Synthetic malformed instruction, never a deployable motion program.
    case["completion"] = {"r": [{"h": None, "s": [], "b": [
        {"i": ["NO M0"], "o": ["ZRN X0 X0 Y0 K2000 K500"]}
    ]}]}
    before = copy.deepcopy(case)
    report = replay_case(case, include_content=include_content)
    assert not report["passed"]
    assert report["failure_stage"] == "candidate_validation"
    assert report["error_type"] == "PLCJsonValidationError"
    assert report["provider_fixture_calls"] == 1 and report["real_model_calls"] == 0
    assert report["generation_evidence_count"] > 0
    assert report["generation_prompt_chars"] > 0
    assert report["checks"]["no_network_attempts"] and report["checks"]["input_unchanged"]
    assert case == before
    if include_content:
        assert "ZRN" in report["content"]["error_message"]
        assert "4 operand" in report["content"]["error_message"]
        assert report["content"]["generation_evidence"]
    else:
        assert "content" not in report and "error_message" not in report


def test_replay_failure_before_provider_creation_has_zero_consumptions(monkeypatch):
    from scripts.context_replay import run_case as replay_case
    import knowledge.scope as scope
    def unavailable(*args, **kwargs):
        raise RuntimeError("Bearer private-test-secret-123456")
    monkeypatch.setattr(scope, "retrieval_plan", unavailable)
    report = replay_case({"case_id": "setup-failure"})
    assert not report["passed"] and report["failure_stage"] == "runtime_setup"
    assert report["provider_fixture_calls"] == report["generation_evidence_count"] == 0
    assert "private-test-secret" not in json.dumps(report)
