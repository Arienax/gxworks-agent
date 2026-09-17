"""Synthetic transport acceptance: no network, user credentials or PLC calls."""
import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from application.model_detection import inspect_openai_compatible
from model_capabilities import (
    apply_parameter_contract, capability_scope, metadata_parameters,
    normalize_parameter_support, parameter_error, scoped_parameters,
)
from model_provider import (
    AssistantMessage, ModelProviderError, ModelRequest, OpenAICompatibleProvider,
    ToolCall, UserMessage, collect_response, coerce_message, strip_legacy_provider_fields,
)


class Rejection(Exception):
    def __init__(self, parameter, *, status=400, code="invalid_value", message=None):
        self.status_code = status
        self.body = {"error": {"param": parameter, "code": code,
                               "message": message or f"Invalid {parameter}"}}


class Endpoint:
    def __init__(self, *, efforts=("low", "high", "max"), temperatures=(0.0, 0.5, 1.0, 1.5, 2.0),
                 metadata=None, ignore=False, unsupported=False, list_error=None, legacy_limit=False):
        self.efforts, self.temperatures = efforts, temperatures
        self.metadata, self.ignore, self.unsupported = metadata, ignore, unsupported
        self.list_error, self.legacy_limit = list_error, legacy_limit
        self.calls, self.options = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=self.list)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        assert 0 < kwargs["timeout"] <= 15
        assert kwargs["max_retries"] == 0
        return self

    def list(self):
        if self.list_error:
            raise self.list_error
        return {"data": self.metadata or [{"id": "tenant-alias"}, {"id": "other-model"}]}

    def create(self, **params):
        self.calls.append(copy.deepcopy(params))
        if self.legacy_limit and "max_completion_tokens" in params:
            raise Rejection("max_completion_tokens", code="unsupported_parameter")
        wire = {**params, **params.get("extra_body", {})}
        assert wire["model"] == "tenant-alias"
        for name, allowed in (("reasoning_effort", self.efforts), ("temperature", self.temperatures)):
            if name not in wire or self.ignore:
                continue
            if self.unsupported:
                raise Rejection(name, code="unsupported_parameter", message="secret-must-not-leak")
            if wire[name] not in allowed:
                raise Rejection(name)
        message = {"content": "OK"}
        if wire.get("tools"):
            message["tool_calls"] = [{"id": "probe", "type": "function", "function": {
                "name": "capability_probe", "arguments": '{"value":"ok"}'}}]
            message["content"] = ""
        elif wire.get("response_format"):
            message["content"] = '{"probe":true}'
        return {"choices": [{"message": message}]}


def profile(**changes):
    return {"id": "custom", "adapter": "openai_compatible", "baseUrl": "https://gateway.invalid/custom/v2/",
            "model": "tenant-alias", "generationDefaults": {}, "requestOverrides": {}, **changes}


def provider(endpoint, **changes):
    return OpenAICompatibleProvider(profile(**changes), "synthetic-key", client=endpoint)


def descriptor(p, **parameters):
    return {"scope": capability_scope(p), "parameters": parameters}


def test_generic_detection_does_not_need_a_model_or_vendor_table():
    endpoint = Endpoint()
    original = profile(generationDefaults={"temperature": 1, "reasoning_effort": "max"})
    p = OpenAICompatibleProvider(original, "fake", client=endpoint)
    before = copy.deepcopy(p.profile)
    result = inspect_openai_compatible(p, "tenant-alias")
    support = result["contract"]
    assert support["parameters"]["reasoning_effort"]["status"] == "supported"
    assert support["parameters"]["reasoning_effort"]["source"] == "probe"
    assert support["parameters"]["reasoning_effort"]["values"] == ["low", "high", "max"]
    assert support["parameters"]["temperature"]["values"] == [0, .5, 1, 1.5, 2]
    assert support["parameters"]["temperature"]["requires"] == {"reasoning_effort": ["max"]}
    assert result["contract"]["capabilities"]["tools"]["status"] == "supported"
    assert result["contract"]["capabilities"]["structured_output"]["modes"] == ["json_object"]
    assert len(endpoint.calls) <= 18
    assert p.profile == before
    assert all("max_completion_tokens" in call for call in endpoint.calls)
    assert "synthetic-key" not in json.dumps(result)


def test_invalid_values_accepted_is_not_proof_of_support():
    result = inspect_openai_compatible(provider(Endpoint(ignore=True)), "tenant-alias")
    parameters = result["contract"]["parameters"]
    assert all(value["status"] == "accepted" for value in parameters.values())
    assert all("values" not in value for value in parameters.values())


def test_explicit_unsupported_is_distinct_from_unknown_and_omits_stale_defaults():
    result = inspect_openai_compatible(provider(Endpoint(unsupported=True)), "tenant-alias")
    p = profile()
    p["capabilityContract"] = result["contract"]
    from model_request_policy import resolve_request
    params = resolve_request(p, {"reasoning_effort": "high", "extra_body": {"temperature": 1},
                                      "response_format": {"type": "json_object"}, "tools": [{"type": "function"}]}).options
    assert "reasoning_effort" not in params and "temperature" not in params.get("extra_body", {})
    assert params["tools"] and params["response_format"]
    assert "secret-must-not-leak" not in json.dumps(result)


def test_fixed_temperature_has_no_adjustable_range():
    result = inspect_openai_compatible(provider(Endpoint(temperatures=(1.0,))), "tenant-alias")
    assert result["contract"]["parameters"]["temperature"]["status"] == "fixed"
    assert result["contract"]["parameters"]["temperature"]["values"] == [1.0]


def test_metadata_preserves_declared_ranges_and_new_effort_values():
    result = inspect_openai_compatible(provider(Endpoint(metadata=[{"id": "tenant-alias", "parameters": {
        "temperature": {"minimum": 0, "maximum": 1, "multipleOf": .1},
        "reasoning_effort": {"enum": ["economy", "balanced", "thorough"]}}}])), "tenant-alias")
    params = result["contract"]["parameters"]
    assert params["temperature"]["step"] == .1
    assert params["reasoning_effort"]["values"] == ["economy", "balanced", "thorough"]
    assert params["reasoning_effort"]["source"] == "metadata"


def test_malformed_metadata_cannot_make_invalid_sliders():
    assert metadata_parameters({"parameters": {"temperature": {"minimum": 2, "maximum": -1}}}) == {}
    assert metadata_parameters({"parameters": ["temperature"]}) == {}
    assert metadata_parameters({"parameters": {"reasoning_effort": {"enum": "high"}}}) == {}


def test_missing_models_endpoint_and_unlisted_alias_still_probe_selected_chat_model():
    endpoint = Endpoint(list_error=Rejection("models", status=404))
    result = inspect_openai_compatible(provider(endpoint), "tenant-alias")
    assert result["models"] == [] and not result["model_listing_available"]
    assert result["recommended_model"] == "tenant-alias"
    endpoint = Endpoint(metadata=[{"id": "not-the-selected-model"}])
    result = inspect_openai_compatible(provider(endpoint), "tenant-alias")
    assert result["recommended_model"] == "tenant-alias"
    assert all(call["model"] == "tenant-alias" for call in endpoint.calls)


def test_no_model_and_no_listing_is_actionable_failure():
    with pytest.raises(ModelProviderError):
        inspect_openai_compatible(provider(Endpoint(list_error=Rejection("models", status=404))), "__discover__")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_rate_limit_are_not_reported_as_missing_capabilities(status):
    endpoint = Endpoint(list_error=Rejection("models", status=status))
    with pytest.raises(ModelProviderError):
        inspect_openai_compatible(provider(endpoint), "tenant-alias")
    assert not endpoint.calls


def test_legacy_output_limit_negotiates_only_the_named_rejection():
    endpoint = Endpoint(legacy_limit=True)
    result = inspect_openai_compatible(provider(endpoint), "tenant-alias")
    assert result["contract"]["capabilities"]["tools"]["status"] == "supported"
    assert sum("max_completion_tokens" in call for call in endpoint.calls) == 1
    assert all("max_tokens" in call for call in endpoint.calls[1:])


def test_unrelated_400_is_not_a_parameter_verdict():
    assert parameter_error(Rejection("messages", message="bad system message"), "temperature") == "unknown"
    assert parameter_error(Rejection("temperature", status=429), "temperature") == "unknown"
    assert parameter_error(Rejection("temperature"), "temperature") == "rejected"


def test_saved_contract_is_scoped_to_endpoint_model_and_thinking_extensions():
    p = profile()
    p["parameterSupport"] = descriptor(p, reasoning_effort={"status": "supported", "source": "manual", "values": ["high"]})
    assert scoped_parameters(p)
    for change in ({"model": "other"}, {"baseUrl": "https://other.invalid/v1"},
                   {"requestOverrides": {"extra_body": {"enable_thinking": True}}}):
        assert scoped_parameters({**p, **change}) == {}
    assert scoped_parameters(p, "other") == {}
    p["generationDefaults"]["reasoning_effort"] = "max"
    with pytest.raises(ValueError):
        apply_parameter_contract({"reasoning_effort": "low"}, p)


def test_temperature_is_invalidated_by_reasoning_mode_changes():
    p = profile()
    p["parameterSupport"] = descriptor(p, temperature={"status": "supported", "source": "probe",
        "values": [0, 1], "reasoning_effort": "none"})
    p["generationDefaults"]["temperature"] = 0
    assert apply_parameter_contract({"temperature": 0, "reasoning_effort": "none"}, p)["temperature"] == 0
    with pytest.raises(ValueError):
        apply_parameter_contract({"temperature": 0, "extra_body": {"reasoning_effort": "high"}}, p)


@pytest.mark.parametrize("item", [
    {"status": "supported", "source": "probe", "values": []},
    {"status": "supported", "source": "probe", "values": [True]},
    {"status": "supported", "source": "probe", "minimum": 0, "maximum": float("inf"), "step": .1},
    {"status": "fixed", "source": "probe", "values": [0, 1]},
    {"status": "supported", "source": "probe", "values": [1], "raw_error": "secret"},
])
def test_invalid_contract_rejected(item):
    with pytest.raises(ValueError):
        normalize_parameter_support(descriptor(profile(), temperature=item))


def test_sdk_unknown_extensions_go_to_extra_body_not_model_branches():
    def create(*, model, messages, stream, temperature=None, extra_body=None):
        pass
    params = {"model": "any-model", "messages": [], "stream": False,
              "enable_thinking": True, "thinking_budget": 100, "temperature": 1}
    result = OpenAICompatibleProvider._sdk_params(create, params)
    assert result["extra_body"] == {"enable_thinking": True, "thinking_budget": 100}
    assert result["temperature"] == 1 and "enable_thinking" in params


def test_extra_body_cannot_replace_canonical_tools_messages_or_response_contract():
    for field in ("messages", "tools", "tool_choice", "model", "stream", "response_format"):
        p = provider(Endpoint(), requestOverrides={"extra_body": {field: "override"}})
        with pytest.raises(ModelProviderError):
            p._request_params(ModelRequest((UserMessage("test"),)))


@pytest.mark.parametrize("stream", [False, True])
def test_private_thought_signatures_roundtrip_without_entering_callbacks_or_history(stream):
    signature = "opaque-signature-never-display"
    raw_call = {"id": "call-1", "index": 0, "function": {"name": "probe", "arguments": "{}"},
                "extra_content": {"google": {"thought_signature": signature}}}
    message = {"content": "", "tool_calls": [raw_call]}
    response = iter([{"choices": [{"delta": message}]}]) if stream else {"choices": [{"message": message}]}
    endpoint = Endpoint()
    endpoint.chat.completions.create = lambda **params: response
    p = provider(endpoint)
    events = []
    collected = collect_response(p, ModelRequest((UserMessage("检查"),), stream=stream), on_event=events.append)
    assert signature not in repr(collected.message)
    assert signature not in str([asdict(event) for event in events])
    assert signature not in json.dumps(strip_legacy_provider_fields(asdict(collected.message)))
    replay = p._request_params(ModelRequest((collected.message,)))
    assistant = next(m for m in replay["messages"] if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == signature
    other = p._request_params(ModelRequest((collected.message,), model="different"))
    assert signature not in json.dumps(other)
    untrusted = coerce_message({"role": "assistant", "content": "公开", "_provider_fields": collected.message._provider_fields})
    assert untrusted._provider_fields == {}


@pytest.mark.parametrize("base_url", [
    "https://dashscope.aliyuncs.com/compatible-mode/v1/",
    "https://api.moonshot.cn/v1/", "https://api.anthropic.com/v1/",
    "https://generativelanguage.googleapis.com/v1beta/openai/", "https://tenant.invalid/llm/v9/",
])
def test_real_openai_sdk_serializes_all_compatible_endpoints_with_one_transport(base_url):
    openai = pytest.importorskip("openai")
    import httpx
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append((str(request.url), body))
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "tenant-alias", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "完成。"}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = openai.OpenAI(api_key="test-not-real", base_url=base_url, http_client=http)
        p = OpenAICompatibleProvider(profile(baseUrl=base_url, generationDefaults={"enable_thinking": True}), "test-not-real", client=client)
        result = collect_response(p, ModelRequest((UserMessage("测试"),), stream=False))
    assert result.message.content == "完成。"
    assert seen[0][0] == base_url + "chat/completions"
    assert seen[0][1]["enable_thinking"] is True and "extra_body" not in seen[0][1]


@pytest.mark.parametrize("invalid", [False, 0, "", []])
def test_falsy_nonobject_contract_is_rejected(invalid):
    with pytest.raises(ValueError):
        normalize_parameter_support(invalid)


def test_effective_parameter_matches_sdk_extra_body_precedence_and_deletion():
    from model_capabilities import effective_parameter
    p = profile(generationDefaults={"reasoning_effort": "low", "extra_body": {"reasoning_effort": "high"}},
                requestOverrides={"reasoning_effort": "max"})
    assert effective_parameter(p, "reasoning_effort") == "high"
    p["requestOverrides"]["extra_body"] = {"reasoning_effort": None}
    assert effective_parameter(p, "reasoning_effort") == "max"
    p["requestOverrides"]["extra_body"] = None
    assert effective_parameter(p, "reasoning_effort") == "max"


@pytest.mark.parametrize("selected", ["low", "max", None])
def test_slider_and_server_default_override_real_workflow_and_agent_hints(selected):
    import api
    import plc_agent
    from test_model_provider import _Client, _chunk

    settings = profile(generationDefaults={"temperature": .5})
    if selected is not None:
        settings["generationDefaults"]["reasoning_effort"] = selected
    settings["parameterSupport"] = descriptor(settings,
        reasoning_effort={"status": "supported", "source": "probe", "values": ["low", "high", "max"]},
        temperature={"status": "supported", "source": "probe", "values": [0, .5, 1], "reasoning_effort": selected})
    client = _Client([
        {"choices": [{"message": {"content": "完成。"}}]},
        iter([_chunk(content="完成。")]),
    ])
    p = OpenAICompatibleProvider(settings, "test-key", client=client)
    # Exercise the production analysis/repair helper with its own stage hint.
    with api.provider_scope(p):
        api._request_model([{"role": "user", "content": "检查"}], effort="high", stream=False,
                           options={"temperature": 1.5})
    # Exercise the production agent, which normally supplies effort=high.
    runtime = SimpleNamespace(list_tools=lambda context: [])
    plc_agent.run_tool_agent("检查", context=None, provider=p, runtime=runtime)
    assert len(client.completions.calls) == 2
    for request in client.completions.calls:
        if selected is None:
            assert "reasoning_effort" not in request
        else:
            assert request["reasoning_effort"] == selected
        assert request["temperature"] == .5


def test_unknown_controls_preserve_explicit_advanced_options():
    p = profile()
    p["parameterSupport"] = descriptor(p, reasoning_effort={"status": "unknown", "source": "probe"})
    assert apply_parameter_contract({"reasoning_effort": "high"}, p)["reasoning_effort"] == "high"
