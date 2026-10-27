import base64
import copy
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import application.model_api as api
from storage.config import DEFAULT_MODEL_PROFILES
from model_runtime.provider import (
    AssistantMessage,
    ImageAttachment,
    ModelProviderError,
    ModelRequest,
    OpenAICompatibleProvider,
    ReasoningDelta,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolCallStart,
    Usage,
    UserMessage,
    collect_response,
    create_provider,
)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("case", json.loads((Path(__file__).parent / "fixtures/model_json_tail_responses.json").read_text(encoding="utf-8")), ids=lambda case: case["id"])
def test_complete_json_with_stray_wrapper_tail_is_recovered_before_publication(case, stream):
    from model_runtime.responses import ResponseContract

    raw = case["raw_response"]
    expected, end = json.JSONDecoder().raw_decode(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    usage = Usage(input_tokens=10, output_tokens=20, total_tokens=30)
    calls = []

    class Provider:
        def stream(self, request):
            calls.append(request)
            # The wrapper tail may arrive in a separate transport delta.
            yield TextDelta(raw[:end])
            yield TextDelta(raw[end:])
            yield usage

    published = []
    response = collect_response(Provider(), ModelRequest((UserMessage("本地手册问答"),),
        stream=stream, response_contract=ResponseContract("manual_answer", "json", ("answer",))),
        on_content_chunk=published.append)
    assert json.loads(response.message.content) == expected
    assert response.message.content == raw[:end]
    assert published == [raw[:end]] and len(calls) == 1
    assert response.raw_attempts[0].message.content == raw
    assert response.usage == usage


@pytest.mark.parametrize("raw", [
    '{"answer":"保留原文"}{"answer":"另一答案"}',
    '{"answer":"保留原文"}[]', '{"answer":"保留原文"}""',
    '{"answer":"保留原文"} null', '{"answer":"保留原文"}说明',
    '{"answer":"缺少结束', '{"answer":"保留原文"',
    '[{"answer":"数组"}]"}',
])
def test_tail_recovery_does_not_discard_another_answer_or_infer_missing_fields(raw):
    from model_runtime.responses import recover_json_object_tail
    assert recover_json_object_tail(raw) == (raw, False)


def test_json_tail_recovery_preserves_generated_nested_values():
    from hypothesis import given, strategies as st
    from model_runtime.responses import recover_json_object_tail

    values = st.recursive(st.none() | st.booleans() | st.integers() | st.text(),
        lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(), inner, max_size=4),
        max_leaves=12)

    @given(st.dictionaries(st.text(), values, max_size=5), st.sampled_from(['"}', '}', '];', '，']))
    def check(payload, suffix):
        original = json.dumps(payload, ensure_ascii=False)
        repaired, changed = recover_json_object_tail(original + suffix)
        assert changed and repaired == original
        assert json.loads(repaired) == payload

    check()


def _tool_delta(index, *, call_id="", name="", arguments=""):
    return SimpleNamespace(
        index=index,
        id=call_id or None,
        function=SimpleNamespace(name=name or None, arguments=arguments),
    )


def _chunk(*, reasoning="", content="", tool_calls=None, usage=None, choices=True):
    values = []
    if choices:
        values.append(
            SimpleNamespace(
                delta=SimpleNamespace(
                    reasoning_content=reasoning or None,
                    content=content or None,
                    tool_calls=tool_calls or [],
                )
            )
        )
    return SimpleNamespace(choices=values, usage=usage)


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Client:
    def __init__(self, responses, models=()):
        self.completions = _Completions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[SimpleNamespace(id=value) for value in models]
            )
        )
        self.options = []

    def with_options(self, **kwargs):
        self.options.append(copy.deepcopy(kwargs))
        return self


def _profile(profile_id):
    return copy.deepcopy(
        next(item for item in DEFAULT_MODEL_PROFILES if item["id"] == profile_id)
    )


@pytest.mark.parametrize("profile_id", ["deepseek-default", "zhipu-glm-5.3-flash"])
def test_openai_compatible_profiles_normalize_sync_reasoning_tools_and_usage(profile_id):
    calls = [
        SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="read_network", arguments='{"network_id":"N0001"}'),
        ),
        SimpleNamespace(
            id="call_2",
            function=SimpleNamespace(name="get_diagnostics", arguments="{}"),
        ),
    ]
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    reasoning_content="先读取网络，再查看诊断。",
                    content="已完成检查。",
                    tool_calls=calls,
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )
    client = _Client([response])
    provider = OpenAICompatibleProvider(_profile(profile_id), "key", client=client)
    request = ModelRequest(
        (SystemMessage("system"), UserMessage("检查")),
        stream=False,
    )

    collected = collect_response(provider, request)

    assert collected.message.content == "已完成检查。"
    assert collected.message.reasoning == "先读取网络，再查看诊断。"
    assert [item.name for item in collected.message.tool_calls] == [
        "read_network",
        "get_diagnostics",
    ]
    assert collected.usage == Usage(11, 7, 18)
    assert client.completions.calls[0]["stream"] is False


def test_streaming_fragmented_multi_tool_calls_and_empty_chunks_are_normalized():
    usage = SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    chunks = [
        _chunk(choices=False),
        _chunk(reasoning="分析"),
        _chunk(
            reasoning="完成。",
            tool_calls=[
                _tool_delta(0, call_id="call_", name="read_", arguments="{"),
                _tool_delta(1, call_id="diag_", name="get_", arguments="{"),
            ],
        ),
        _chunk(
            content="开始执行",
            tool_calls=[
                _tool_delta(0, call_id="1", name="network", arguments='"network_id":"N0001"}'),
                _tool_delta(1, call_id="1", name="diagnostics", arguments="}"),
            ],
        ),
        _chunk(choices=False, usage=usage),
    ]
    provider = OpenAICompatibleProvider(
        _profile("deepseek-default"), "key", client=_Client([iter(chunks)])
    )

    events = list(
        provider.stream(ModelRequest((UserMessage("检查"),), stream=True))
    )

    assert [event.text for event in events if isinstance(event, ReasoningDelta)] == [
        "分析",
        "完成。",
    ]
    assert [event.text for event in events if isinstance(event, TextDelta)] == [
        "开始执行"
    ]
    assert [event.name for event in events if isinstance(event, ToolCallStart)] == [
        "read_network",
        "get_diagnostics",
    ]
    ended = [event.tool_call for event in events if isinstance(event, ToolCallEnd)]
    assert ended == [
        ToolCall("call_1", "read_network", '{"network_id":"N0001"}'),
        ToolCall("diag_1", "get_diagnostics", "{}"),
    ]
    assert events[-1] == Usage(20, 10, 30)


def test_provider_request_path_has_no_legacy_parameter_or_capability_branch():
    import inspect
    from model_runtime.provider import OpenAICompatibleProvider

    request_source = inspect.getsource(OpenAICompatibleProvider._request_params)
    runtime_source = inspect.getsource(OpenAICompatibleProvider._runtime_profile)

    assert "apply_parameter_contract" not in request_source
    assert 'profile.get("capabilities")' not in request_source
    assert "contract_capability_available(" in request_source
    assert "resolve_request(" in request_source
    assert "materialize_runtime_profile(" in runtime_source


def test_parameter_precedence_and_capability_constraints_for_built_in_profiles():
    tool = {
        "type": "function",
        "function": {"name": "get_diagnostics", "parameters": {"type": "object"}},
    }
    deepseek = OpenAICompatibleProvider(
        _profile("deepseek-default"), "key", client=_Client([iter([])])
    )
    # The catalog declares temperature incompatible with enabled thinking.
    # The provider must now enforce that contract instead of falling through a
    # legacy raw-parameter path.
    with pytest.raises(ModelProviderError) as conflict:
        deepseek._request_params(
            ModelRequest(
                (UserMessage("检查"),),
                tools=(tool,),
                options={"temperature": 0.2},
                stream=True,
            )
        )
    assert conflict.value.code == "invalid_request"

    deepseek_params = deepseek._request_params(
        ModelRequest(
            (UserMessage("检查"),),
            tools=(tool,),
            options={"response_format": None, "tool_choice": "auto"},
            stream=True,
        )
    )
    assert "temperature" not in deepseek_params
    assert "response_format" not in deepseek_params
    assert "tool_choice" not in deepseek_params
    assert deepseek_params["extra_body"]["thinking"]["type"] == "enabled"

    glm = OpenAICompatibleProvider(
        _profile("zhipu-glm-5.3-flash"), "key", client=_Client([iter([])])
    )
    glm_params = glm._request_params(
        ModelRequest(
            (UserMessage("检查"),),
            tools=(tool,),
            options={"temperature": 0.15, "reasoning_effort": "high"},
            stream=True,
        )
    )
    # Fresh built-ins no longer carry legacy tuning defaults. Explicit
    # low-level request values are validated by the catalog contract.
    assert glm_params["temperature"] == 0.15
    assert "top_p" not in glm_params
    assert glm_params["reasoning_effort"] == "high"
    assert glm_params["extra_body"]["thinking"] == {"type": "enabled"}
    assert glm_params["extra_body"]["tool_stream"] is True


@pytest.mark.parametrize(
    "profile_id",
    ["deepseek-v4-flash-vision-exp", "zhipu-glm-5.3-flash"],
)
def test_multimodal_profiles_encode_local_images_as_openai_compatible_blocks(
    profile_id,
):
    image = ImageAttachment(
        "接线图.png",
        "image/png",
        b"\x89PNG\r\n\x1a\nfixture",
    )
    provider = OpenAICompatibleProvider(
        _profile(profile_id), "key", client=_Client([iter([])])
    )

    params = provider._request_params(
        ModelRequest(
            (UserMessage("请识别图中的输入输出", (image,)),),
            stream=True,
        )
    )

    blocks = next(message for message in params["messages"] if message["role"] == "user")["content"]
    assert blocks[0] == {"type": "text", "text": "请识别图中的输入输出"}
    assert blocks[1]["type"] == "image_url"
    data_url = blocks[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == image.data


@pytest.mark.parametrize("profile_id", ["deepseek-default", "deepseek-v4-flash"])
def test_text_profiles_reject_images_before_calling_the_sdk(profile_id):
    provider = OpenAICompatibleProvider(
        _profile(profile_id), "key", client=_Client([iter([])])
    )
    image = ImageAttachment("图.png", "image/png", b"image")

    with pytest.raises(ModelProviderError) as captured:
        provider._request_params(
            ModelRequest((UserMessage("识别", (image,)),), stream=True)
        )

    assert captured.value.code == "image_not_supported"
    assert "不支持图片输入" in str(captured.value)


@pytest.mark.parametrize(
    ("profile_id", "effort", "thinking_required"),
    [
        ("zhipu-glm-5.3", "low", True),
        ("zhipu-glm-5.2", "high", False),
    ],
)
def test_glm_text_profiles_use_the_shared_streaming_tool_adapter(
    profile_id, effort, thinking_required
):
    tool = {
        "type": "function",
        "function": {"name": "get_diagnostics", "parameters": {"type": "object"}},
    }
    provider = OpenAICompatibleProvider(
        _profile(profile_id), "key", client=_Client([iter([])])
    )

    params = provider._request_params(
        ModelRequest(
            (UserMessage("检查"),),
            tools=(tool,),
            options={"reasoning_effort": effort},
            stream=True,
        )
    )

    assert params["model"] == _profile(profile_id)["model"]
    assert params["reasoning_effort"] == effort
    extra = params["extra_body"]
    if thinking_required:
        assert extra["thinking"]["type"] == "enabled"
    else:
        assert "thinking" not in extra
    assert extra["tool_stream"] is True


def test_assistant_reasoning_is_replayed_only_by_transport_adapter():
    provider = OpenAICompatibleProvider(
        _profile("deepseek-default"), "key", client=_Client([iter([])])
    )
    params = provider._request_params(
        ModelRequest(
            (
                AssistantMessage(
                    "",
                    (ToolCall("call_1", "get_diagnostics", "{}"),),
                    "保留本轮推理回放",
                ),
            ),
            stream=True,
        )
    )

    assistant = next(message for message in params["messages"] if message["role"] == "assistant")
    assert assistant["reasoning_content"] == "保留本轮推理回放"
    assert assistant["tool_calls"][0]["function"]["name"] == "get_diagnostics"


@pytest.mark.parametrize("profile_id", ["deepseek-default", "zhipu-glm-5.3-flash"])
def test_hidden_reasoning_replays_only_to_vendor_in_multiround_tool_requests(profile_id, monkeypatch):
    import agent_runtime.agent as plc_agent
    from agent_runtime.messages import ToolResult
    from agent_runtime.runtime import public_tool_result_data
    from application.projects import public
    from model_runtime.provider import strip_legacy_provider_fields

    private = "The internal analysis requires a tool before answering."
    arguments = '{"network_id":"N0001"}'
    client = _Client([
        iter([_chunk(reasoning=private), _chunk(tool_calls=[
            _tool_delta(0, call_id="call_1", name="read_network", arguments=arguments)])]),
        iter([_chunk(reasoning=private), _chunk(content="网络已经检查完成。")]),
    ])
    provider = OpenAICompatibleProvider(_profile(profile_id), "offline-key", client=client)
    invoked = []
    class Runtime:
        def list_tools(self, context):
            return [{"type": "function", "function": {"name": "read_network", "parameters": {
                "type": "object", "properties": {"network_id": {"type": "string"}}}}}]
        def invoke(self, call, context):
            invoked.append(call)
            return ToolResult(call.id, call.name, '{"ok":true,"data":{"network_id":"N0001"}}',
                              {"ok": True, "data": {"network_id": "N0001"}})
    accepted = []
    def collect_observed(*args, **kwargs):
        result = collect_response(*args, **kwargs)
        accepted.append(result)
        return result
    monkeypatch.setattr(plc_agent, "collect_response", collect_observed)
    displayed_reasoning, displayed_content, progress = [], [], []
    result = plc_agent.run_tool_agent("读取当前网络。", context=SimpleNamespace(program_ir=None, version=None, ladder=None),
        runtime=Runtime(), provider=provider, response_language="zh-CN", on_reasoning_chunk=displayed_reasoning.append,
        on_content_chunk=displayed_content.append, on_progress=progress.append)
    assert result.content == "网络已经检查完成。" and result.rounds == 2
    assert displayed_reasoning == [] and displayed_content == [result.content]
    assert len(invoked) == 1 and invoked[0].arguments == arguments
    assert len(client.completions.calls) == 2
    second_wire = client.completions.calls[1]["messages"]
    assistant = next(message for message in second_wire if message["role"] == "assistant")
    assert assistant["reasoning_content"] == private
    assert assistant["tool_calls"][0]["function"]["arguments"] == arguments
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert "_provider_reasoning" not in json.dumps(second_wire)
    for collected in accepted:
        assert collected.message.reasoning == "" and collected.message._provider_reasoning == private
        assert private not in repr(collected) and private not in repr(collected.message)
        message = asdict(collected.message)
        assert "_provider_reasoning" not in strip_legacy_provider_fields(message)
        assert private not in json.dumps(strip_legacy_provider_fields(message))
        # HTTP project projections and MCP tool projections independently strip
        # private fields, including after generic dataclass conversion.
        assert private not in json.dumps(public({"message": message}))
        assert private not in json.dumps(public_tool_result_data(ToolResult("probe", "probe", "", {"message": message})))
    assert private not in json.dumps(asdict(result)) + json.dumps(progress + displayed_content)


def test_private_reasoning_cannot_be_injected_from_message_dictionaries():
    from model_runtime.provider import coerce_message
    message = coerce_message({"role": "assistant", "content": "公开正文。", "_provider_reasoning": "Untrusted replay."})
    assert message.reasoning == "" and message._provider_reasoning == ""
    provider = OpenAICompatibleProvider(_profile("deepseek-default"), "offline-key", client=_Client([]))
    wire = provider._request_params(ModelRequest((message,), response_language="zh-CN"))
    assistant = next(item for item in wire["messages"] if item["role"] == "assistant")
    assert "reasoning_content" not in assistant and "Untrusted replay." not in json.dumps(wire)


def test_custom_openai_compatible_profile_needs_no_new_provider_code():
    profile = {
        "id": "custom",
        "name": "自定义服务",
        "adapter": "openai_compatible",
        "baseUrl": "https://example.invalid/v1",
        "model": "custom-model",
        "generationDefaults": {"temperature": 0.3},
        "capabilities": {},
        "requestOverrides": {"extra_body": {"vendor_flag": True}},
    }
    provider = create_provider(profile, "key", client=_Client([iter([])]))

    params = provider._request_params(ModelRequest((UserMessage("hi"),), stream=True))

    assert isinstance(provider, OpenAICompatibleProvider)
    assert params["model"] == "custom-model"
    assert params["temperature"] == 0.3
    assert params["extra_body"] == {"vendor_flag": True}


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [(401, "authentication", False), (429, "rate_limit", True), (503, "unavailable", True)],
)
def test_provider_normalizes_transport_errors(status, expected_code, retryable):
    error = RuntimeError("transport failed")
    error.status_code = status
    provider = OpenAICompatibleProvider(
        _profile("deepseek-default"), "key", client=_Client([error])
    )

    with pytest.raises(ModelProviderError) as captured:
        list(provider.stream(ModelRequest((UserMessage("hi"),), stream=True)))

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    assert captured.value.status_code == status


def test_local_progress_storage_permission_is_not_model_authentication():
    provider = OpenAICompatibleProvider(
        _profile("deepseek-default"), "key", client=_Client([PermissionError("local storage denied")])
    )
    with pytest.raises(ModelProviderError) as captured:
        list(provider.stream(ModelRequest((UserMessage("hi"),), stream=True)))
    assert captured.value.code == "provider_error"
    assert captured.value.status_code is None


def test_provider_rejects_protocol_response_without_choices():
    provider = OpenAICompatibleProvider(
        _profile("deepseek-default"),
        "key",
        client=_Client([SimpleNamespace(choices=[], usage=None)]),
    )

    with pytest.raises(ModelProviderError, match="候选") as captured:
        list(provider.stream(ModelRequest((UserMessage("hi"),), stream=False)))

    assert captured.value.code == "protocol"


def test_request_timeout_and_retry_options_are_applied_to_client():
    client = _Client([iter([])])
    provider = OpenAICompatibleProvider(_profile("deepseek-default"), "key", client=client)

    list(
        provider.stream(
            ModelRequest(
                (UserMessage("hi"),),
                stream=True,
                timeout=12.5,
                max_retries=0,
            )
        )
    )

    assert client.options == [{"timeout": 12.5, "max_retries": 0}]


def test_new_history_messages_strip_legacy_provider_fields_before_request():
    messages = api._build_clean_messages(
        [
            {
                "role": "assistant",
                "content": "可见正文",
                "reasoning_content": "旧厂商字段",
                "choices": ["旧响应"],
            }
        ],
        "system",
    )

    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "可见正文"},
    ]


def test_model_api_exposes_canonical_calls_without_global_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not any(name.startswith("call_deepseek_") for name in vars(api))
    assert not hasattr(api, "_save_history")
    assert not hasattr(api, "HISTORY_FILE")
    with pytest.raises(ValueError, match="project application service"):
        api._prepare_api_call("request", None, None, "ladder", persist_history=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("profile_id", ["zhipu-glm-5.3-flash"])
@pytest.mark.parametrize("stream", [True, False])
def test_real_request_parameters_keep_language_and_native_schema_before_acceptance(
    monkeypatch, profile_id, stream
):
    from model_runtime.provider import ResponseRejectedError
    from model_runtime.responses import ResponseContract

    raw = json.dumps({"summary": "仍然返回中文。"}, ensure_ascii=False)
    wire_response = iter([_chunk(content=raw)]) if stream else SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw, tool_calls=[]))],
    )
    client = _Client([wire_response])
    profile = _profile(profile_id)
    profile["capabilityOverrides"] = {
        "capabilities": {
            "structured_output": {
                "status": "supported",
                "modes": ["json_schema"],
            }
        }
    }
    provider = OpenAICompatibleProvider(profile, "offline-key", client=client)
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    native_format = {"type": "json_schema", "json_schema": {
        "name": "summary", "strict": True, "schema": {
            "type": "object", "properties": {"summary": {"type": "string"}},
            "required": ["summary"], "additionalProperties": False,
        },
    }}
    displayed = []
    with pytest.raises(ResponseRejectedError) as error:
        api._request_model(
            [{"role": "system", "content": "JSON only. 中文示例。"},
             {"role": "user", "content": "请分析 X0"}],
            stream=stream, response_language="en",
            response_contract=ResponseContract("test", "json", ("summary",)),
            options={"response_format": native_format},
            on_content_chunk=displayed.append,
        )
    assert displayed == []
    assert error.value.raw_response.message.content == raw
    assert len(client.completions.calls) == 1
    params = client.completions.calls[0]
    assert params["response_format"] == native_format
    assert params["stream"] == stream
    assert "response_language" not in params  # It is not an OpenAI wire option.
    assert "English (en)" in params["messages"][0]["content"]
    assert params["messages"][1]["content"] == "请分析 X0"


@pytest.mark.parametrize("stream", [True, False])
def test_deepseek_catalog_rejects_undeclared_json_schema_before_sdk(stream):
    native_format = {"type": "json_schema", "json_schema": {
        "name": "summary", "strict": True, "schema": {
            "type": "object", "properties": {"summary": {"type": "string"}},
            "required": ["summary"], "additionalProperties": False,
        },
    }}
    client = _Client([])
    provider = OpenAICompatibleProvider(_profile("deepseek-default"), "offline-key", client=client)

    with pytest.raises(ModelProviderError) as error:
        provider._request_params(
            ModelRequest(
                (UserMessage("fixture"),),
                stream=stream,
                options={"response_format": native_format},
            )
        )

    assert error.value.code == "invalid_request"
    assert client.completions.calls == []


def test_ladder_generation_replaces_stale_native_schema_with_current_opcode_contract(monkeypatch):
    captured = {}
    profile = _profile("zhipu-glm-5.3-flash")
    from model_runtime.contract import (
        CapabilityContract, CapabilityDescriptor, contract_scope,
    )
    capabilities = {
        "structured_output": CapabilityDescriptor.from_dict({
            "status": "supported",
            "source": "manual",
            "modes": ["json_schema"],
        })
    }
    contract = CapabilityContract(
        contract_scope(profile, {}, api_key=None),
        capabilities,
        {},
    )
    profile["capabilityContract"] = contract.to_dict()
    profile["userModelSettings"] = {
        "scope": profile["capabilityContract"]["scope"],
        "parameters": {},
    }
    monkeypatch.setattr(
        api, "_workflow_provider",
        lambda: SimpleNamespace(profile=profile, api_key=None),
    )
    monkeypatch.setattr(
        api,
        "_prepare_api_call",
        lambda *args, **kwargs: ([{"role": "system", "content": "system"}], [], False),
    )

    def request(messages, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(
                reasoning="",
                content='{"device_comments":{},"rungs":[]}',
            )
        )

    monkeypatch.setattr(api, "_request_model", request)
    api.stream_model_response(
        "X0 controls Y0", "offline", "low", "ladder", plc_model="FX3U"
    )

    native = captured["options"]["response_format"]
    assert native["type"] == "json_schema"
    assert native["json_schema"]["name"] == "ladder_candidate"
    schema = native["json_schema"]["schema"]

    def app_instr_rule(value):
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                opcode_rule = properties.get("opcode")
                if isinstance(opcode_rule, dict) and isinstance(opcode_rule.get("enum"), list):
                    return value
            for child in value.values():
                found = app_instr_rule(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = app_instr_rule(child)
                if found is not None:
                    return found
        return None

    rule = app_instr_rule(schema)
    assert rule is not None
    allowed = set(rule["properties"]["opcode"]["enum"])
    assert "MOV" in allowed
    assert "NOT_A_REAL_OPCODE" not in allowed
    assert "FLDE" not in allowed
    assert "OUT" not in allowed


def test_workflow_response_format_overrides_stale_profile_request_override():
    profile = _profile("zhipu-glm-5.3-flash")
    stale = {
        "type": "json_schema",
        "json_schema": {
            "name": "stale",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    profile.setdefault("requestOverrides", {})["response_format"] = stale
    profile["capabilityOverrides"] = {
        "capabilities": {
            "structured_output": {
                "status": "supported",
                "modes": ["json_schema"],
            }
        }
    }
    provider = OpenAICompatibleProvider(profile, "key", client=_Client([iter([])]))
    requested = {
        "type": "json_schema",
        "json_schema": {
            "name": "current",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }
    params = provider._request_params(
        ModelRequest(
            (UserMessage("generate"),),
            options={"response_format": requested},
            stream=True,
        )
    )
    assert params["response_format"] == requested

    params = provider._request_params(
        ModelRequest(
            (UserMessage("plain"),),
            options={"response_format": None},
            stream=True,
        )
    )
    assert "response_format" not in params


@pytest.mark.parametrize("with_tools", [False, True])
def test_usage_receipt_matches_final_wire_and_does_not_claim_tool_execution(with_tools):
    from model_runtime.usage import usage_scope
    profile = _profile("deepseek-default")
    profile.update(baseUrl="https://example.invalid/v1", model="unknown")
    profile["name"] = "Frozen profile"
    profile["generationDefaults"] = {"temperature": .733}
    client = _Client([SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK", tool_calls=[]))])])
    provider = OpenAICompatibleProvider(profile, "secret-only-for-transport", client=client)
    records = []
    tool = {"type":"function", "function":{"name":"search_plc_manual", "description":"private-tool-prose", "parameters":{"type":"object"}}}
    req = ModelRequest((UserMessage("private-engineering-prompt"),), stream=False, tools=(tool,) if with_tools else ())
    preview = OpenAICompatibleProvider.preview_request(profile, provider.api_key, req)
    with usage_scope(lambda kind, data: records.append((kind,data))):
        list(provider.stream(req))
    assert len(records) == 1 and records[0][0] == "model_request"
    receipt = records[0][1]
    assert receipt == preview
    wire = client.completions.calls[0]
    assert receipt["model"] == wire["model"]
    assert receipt["features"]["tools"] == bool(wire.get("tools")) == with_tools
    temperature = next(row for row in receipt["parameters"] if row["name"] == "temperature")
    assert temperature["value"] == wire["temperature"] == .733
    assert temperature["sent"] and receipt["effect_confirmed"] is False
    encoded = json.dumps(receipt)
    for secret in (provider.api_key,"private-engineering-prompt","private-tool-prose","search_plc_manual"):
        assert secret not in encoded


def test_usage_receipt_hides_free_text_parameter_values():
    from application.model_detection import resolve_profile_contract
    from model_runtime.usage import public_usage
    profile = {"id":"private", "name":"Private", "adapter":"openai_compatible", "model":"unknown", "baseUrl":"https://example.invalid/v1",
               "capabilityOverrides":{"parameters":{"custom_text":{"type":"string","status":"unknown"}}}}
    contract = resolve_profile_contract(profile, "private-key")["contract"]
    profile.update(capabilityContract=contract, userModelSettings={"scope":contract["scope"],"parameters":{
        "custom_text":{"mode":"value","value":"private-key"}, "temperature":{"mode":"value","value":.4}}})
    receipt = OpenAICompatibleProvider.preview_request(profile,"private-key",ModelRequest((UserMessage("private-prompt"),),stream=False))
    custom = next(row for row in receipt["parameters"] if row["name"] == "custom_text")
    assert custom["redacted"] and custom["sent"] and custom["value"] is None
    assert "private-key" not in json.dumps(receipt) and "private-prompt" not in json.dumps(receipt)
    assert public_usage({**receipt,"messages":["private-prompt"],"api_key":"private-key"}) == receipt


@pytest.mark.parametrize('location', ['message', 'tool', 'response_schema'])
def test_final_provider_budget_rejects_complete_oversized_wire_before_transport(location):
    from model_runtime.context_budget import final_request_budget
    profile = _profile('deepseek-default')
    # This explicit application-side capacity is independent of catalog defaults.
    profile['context_window'] = 20000
    profile['capabilityOverrides'] = {'capabilities': {'structured_output': {'status': 'supported', 'modes': ['json_schema']}}}
    profile['userModelSettings'] = {'parameters': {}}
    client = _Client([])
    provider = OpenAICompatibleProvider(profile, 'key', client=client)
    long_value = '完整事实 不可截断 ' * 12000
    tools, options, content = (), {}, 'Read the complete engineering context.'
    if location == 'message':
        content = long_value
    elif location == 'tool':
        tools = ({'type': 'function', 'function': {'name': 'read', 'description': long_value,
                 'parameters': {'type': 'object', 'properties': {}}}},)
    else:
        options = {'response_format': {'type': 'json_schema', 'json_schema': {'name': 'output',
            'schema': {'type': 'object', 'description': long_value, 'properties': {}}}}}
    request = ModelRequest((UserMessage(content),), tools=tools, options=options, stream=False)
    with pytest.raises(ModelProviderError) as failure:
        list(provider.stream(request))
    assert failure.value.code == 'context_budget_exceeded'
    assert client.completions.calls == []


def test_unknown_external_model_capacity_is_not_guessed_by_final_wire_accounting():
    from model_runtime.context_budget import final_request_budget
    options = {'messages': [{'role': 'user', 'content': '模型条件' * 4000}],
               'tools': [{'type': 'function', 'function': {'name': 'read', 'description': '接口'}}]}
    result = final_request_budget({}, options)
    assert result['fits'] is None and result['context_window'] is None
    assert result['utf8_bytes'] == len(json.dumps(options, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    assert result['estimated_input_tokens'] > 6000
