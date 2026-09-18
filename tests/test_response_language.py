"""Behavioral acceptance tests using deterministic, network-free providers.

These prove observable script mismatches are rejected before publication. They
do not claim semantic language identification for shared Han or Latin scripts.
"""

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from shared.i18n import get_language, language_context, set_language
from model_runtime.provider import (
    ModelProviderError,
    ModelRequest,
    ReasoningDelta,
    ResponseRejectedError,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
    Usage,
    UserMessage,
    collect_response,
)
from agent_runtime.agent import run_tool_agent
from model_runtime.response_language import ResponseContract, preserved_annotations
from application.response_contracts import (
    ANALYSIS_RESPONSE,
    LADDER_RESPONSE,
    ST_RESPONSE,
    tool_argument_contract,
)


LANGUAGE_CASES = (
    ("en", "The output relay is enabled.", "输出继电器已经启动。"),
    ("zh-CN", "输出继电器已经启动。", "The output relay is enabled."),
    ("ja", "出力リレーが有効になっています。", "输出继电器已经启动。"),
)


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    set_language("zh-CN")
    try:
        yield
    finally:
        set_language(previous)


class FakeProvider:
    """Yield one planned event sequence for each real orchestration request."""

    def __init__(self, rounds, *, before_event=None):
        self.rounds = list(rounds)
        self.requests = []
        self.before_event = before_event

    def stream(self, request):
        self.requests.append(request)
        for event in self.rounds.pop(0):
            if self.before_event:
                self.before_event()
            if isinstance(event, Exception):
                raise event
            yield event


class FakeRuntime:
    def __init__(self, evidence=None):
        self.calls = []
        self.evidence = evidence or {
            "source": "manual.pdf",
            "page": 7,
            "text": "定时器使用 T0，手册原文必须保留。",
        }

    def list_tools(self, context):
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in ("search_plc_manual", "create_program_candidate", "patch_program")
        )

    def invoke(self, call, context):
        self.calls.append(call)
        data = {"ok": True, "data": copy.deepcopy(self.evidence)}
        return ToolResult(call.id, call.name, json.dumps(data, ensure_ascii=False), data)


def request_for(language, **kwargs):
    return ModelRequest(
        (SystemMessage("Follow the response contract."), UserMessage("Check X0 and Y0.")),
        response_language=language,
        **kwargs,
    )


def candidate_ladder(label="Start input"):
    return {
        "device_comments": {"X0": label, "Y0": "Run output"},
        "rungs": [
            {
                "rung_id": 10,
                "debug_note": "Read X0 and drive Y0.",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": label}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "Run output"}],
                    }
                ],
            }
        ],
    }


def split_text(text, width):
    return [text[index:index + width] for index in range(0, len(text), width)]


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("bad_channel", ["content", "content_and_reasoning"])
def test_wrong_content_is_rejected_before_any_callback_or_tool_publication(
    language, good, bad, stream, bad_channel
):
    published = []
    call = ToolCall("call-1", "search_plc_manual", {"query": "X0"})
    events = [
        ReasoningDelta(bad if bad_channel == "content_and_reasoning" else good),
        ToolCallStart(call.id, call.name),
        ToolCallEnd(call),
        TextDelta(bad),
        Usage(12, 8, 20),
    ]
    provider = FakeProvider([events])

    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(
            provider,
            request_for(language, stream=stream),
            on_event=published.append,
            on_content_chunk=published.append,
            on_reasoning_chunk=published.append,
            fallback_to_non_stream=True,
        )

    assert published == []
    assert len(provider.requests) == 1  # A language rejection is not a transport retry.
    error = captured.value
    assert error.response_language == language
    assert error.retryable is False
    assert error.raw_response.message.content == bad
    assert error.raw_response.message.tool_calls == (call,)
    assert error.raw_attempts[-1] == error.raw_response
    assert error.violations


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("width", [1, 7, 100000])
def test_wrong_reasoning_is_fully_hidden_without_rejecting_valid_content_or_tools(language, good, bad, stream, width):
    content_seen, reasoning_seen, event_seen = [], [], []
    call = ToolCall("call-1", "search_plc_manual", {"query": "X0"})
    reasoning = good + "\n" + bad
    content = "\r\n" + good + "\n  "
    events = [*(ReasoningDelta(chunk) for chunk in split_text(reasoning, width)),
              ToolCallStart(call.id, call.name), ToolCallEnd(call),
              *(TextDelta(chunk) for chunk in split_text(content, width)), Usage(12, 8, 20)]
    def unpublished_during_generation():
        assert content_seen == reasoning_seen == event_seen == []
    provider = FakeProvider([events], before_event=unpublished_during_generation)
    result = collect_response(provider, request_for(language, stream=stream),
        on_event=event_seen.append, on_content_chunk=content_seen.append, on_reasoning_chunk=reasoning_seen.append,
        fallback_to_non_stream=True)
    assert len(provider.requests) == 1
    assert result.message.content == content and "".join(content_seen) == content
    assert result.message.reasoning == "" and reasoning_seen == []
    assert result.message.tool_calls == (call,) and result.usage == Usage(12, 8, 20)
    assert event_seen == [event for event in events if not isinstance(event, ReasoningDelta)]
    # Suppressed text remains only in explicit backend diagnostics, not the
    # accepted message, callback events or routine result representation.
    assert result.raw_attempts[-1].message.reasoning == reasoning
    assert result.raw_attempts[-1].events == tuple(events)
    assert bad not in repr(result)


@pytest.mark.parametrize("invalid_channel", ["none", "content", "tool", "both"])
def test_hidden_reasoning_does_not_weaken_content_or_candidate_argument_acceptance(invalid_channel):
    good, bad = "启动后保持输出，停止时复位。", "The output remains enabled."
    content = json.dumps({"summary": bad if invalid_channel in ("content", "both") else good}, ensure_ascii=False)
    call = ToolCall("candidate-1", "patch_program", {"patch": {"operations": [{"ladder": {
        "debug_note": bad if invalid_channel in ("tool", "both") else good}}]}})
    events = [ReasoningDelta(bad), TextDelta(content), ToolCallStart(call.id, call.name), ToolCallEnd(call)]
    provider = FakeProvider([events])
    published = []
    request = request_for("zh-CN", response_contract=ANALYSIS_RESPONSE,
        tool_response_contracts=((call.name, tool_argument_contract(call.name)),))
    if invalid_channel == "none":
        result = collect_response(provider, request, on_event=published.append)
        assert result.message.content == content and result.message.reasoning == ""
        assert result.message.tool_calls == (call,)
        assert published == events[1:]
    else:
        with pytest.raises(ResponseRejectedError) as captured:
            collect_response(provider, request, on_event=published.append, fallback_to_non_stream=True)
        assert published == []
        paths = [violation.path for violation in captured.value.violations]
        assert any(path.startswith("content") for path in paths) == (invalid_channel in ("content", "both"))
        assert any(path.startswith("tool_calls") for path in paths) == (invalid_channel in ("tool", "both"))
        assert not any(path.startswith("reasoning") for path in paths)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
@pytest.mark.parametrize("width", [1, 2, 7, 4097, 100000])
def test_long_rejected_stream_is_independent_of_arbitrary_chunk_boundaries(language, good, bad, width):
    text = (good + " ") * 200 + bad
    chunks = split_text(text, width)
    published = []
    provider = FakeProvider([[TextDelta(chunk) for chunk in chunks]])

    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(provider, request_for(language), on_content_chunk=published.append)

    assert captured.value.raw_response.message.content == text
    assert published == []


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
@pytest.mark.parametrize("width", [1, 5, 100000])
def test_accepted_stream_preserves_exact_text_reasoning_events_and_usage(language, good, bad, width):
    content = "\r\n" + good + "\nLD X0\nMOV K1 D100\nOUT Y0\n  "
    chunks = split_text(content, width)
    content_seen, reasoning_seen, event_seen = [], [], []
    events = [ReasoningDelta(good), *(TextDelta(chunk) for chunk in chunks), Usage(2, 3, 5)]

    def nothing_published_during_generation():
        assert content_seen == reasoning_seen == event_seen == []

    provider = FakeProvider([events], before_event=nothing_published_during_generation)
    result = collect_response(
        provider,
        request_for(language),
        on_content_chunk=content_seen.append,
        on_reasoning_chunk=reasoning_seen.append,
        on_event=event_seen.append,
    )

    assert result.message.content == content
    assert "".join(content_seen).encode("utf-8") == content.encode("utf-8")
    assert "".join(reasoning_seen) == good
    assert event_seen == events
    assert result.usage == Usage(2, 3, 5)
    assert result.response_language == language
    assert result.raw_attempts[-1].message == result.message


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
def test_structured_human_fields_reject_wrong_language_without_touching_machine_or_evidence(language, good, bad):
    contract = ResponseContract("diagnostic", "json", ("summary", "findings.*.description"))
    payload = {
        "summary": good,
        "findings": [{"description": bad, "network_id": "N0001"}],
        "source": {"text": "定时器使用 T0", "path": "C:\\手册\\FX3U.pdf"},
        "opcode": "MOV",
        "operand": ["K1", "D100"],
        "enum": "启停控制",
    }
    raw = json.dumps(payload, ensure_ascii=False)
    provider = FakeProvider([[TextDelta(raw)]])

    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(provider, request_for(language, response_contract=contract))

    assert captured.value.raw_response.message.content == raw
    assert any("findings" in violation.path for violation in captured.value.violations)
    payload["findings"][0]["description"] = good
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    result = collect_response(FakeProvider([[TextDelta(raw)]]), request_for(language, response_contract=contract))
    assert result.message.content == raw
    assert json.loads(result.message.content)["source"] == payload["source"]


@pytest.mark.parametrize("invalid_json", ["not a JSON object", "[]", '{"summary":'])
def test_invalid_structured_response_never_reaches_callbacks(invalid_json):
    published = []
    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(
            FakeProvider([[TextDelta(invalid_json)]]),
            request_for("en", response_contract=ANALYSIS_RESPONSE),
            on_content_chunk=published.append,
        )
    assert published == []
    assert captured.value.raw_response.message.content == invalid_json


@pytest.mark.parametrize("field_value", [["输出已经启动。"], {"text": "输出已经启动。"}])
def test_declared_human_field_cannot_hide_wrong_language_in_a_container(field_value):
    # Legacy parsers can stringify these values; they must not evade acceptance.
    raw = json.dumps({"summary": field_value}, ensure_ascii=False)
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta(raw)]]), request_for("en", response_contract=ANALYSIS_RESPONSE))


@pytest.mark.parametrize("language,text", [
    ("zh-CN", "The output is now enabled: X0."),
    ("zh-CN", '{"summary":"The output is now enabled."}'),
    ("en", "Result: 输出已经启动。"),
    ("en", "`输出已经启动。`"),
    ("en", "```text\n输出已经启动。\n```"),
    ("en", "```python\n输出已经启动。\n```"),
    ("en", "Report C:\\temp\\program.st 输出已经启动。"),
    ("ja", "The output is now enabled: X0."),
])
def test_punctuation_backticks_fences_or_paths_do_not_exempt_new_prose(language, text):
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta(text)]]), request_for(language))


def test_rag_exact_quotation_preserves_source_but_new_summary_must_match_language():
    original = "定时器使用 T0，手册原文必须保留。"
    request = ModelRequest(
        (SystemMessage("Retrieved evidence from manual.pdf, page 7:\n" + original),
         UserMessage("Explain the timer behavior.")),
        response_language="en",
    )
    content = 'The manual states: "' + original + '" Use T0 for this timer.'
    result = collect_response(FakeProvider([[TextDelta(content)]]), request)
    assert result.message.content == content
    for invalid in (original, '"定时器必须改用 T1。"', content + "\n输出已经启动。"):
        with pytest.raises(ResponseRejectedError):
            collect_response(FakeProvider([[TextDelta(invalid)]]), request)


def test_ladder_preserves_known_annotations_but_rejects_new_wrong_language_labels():
    existing = candidate_ladder("用户确认的启动按钮")
    raw = json.dumps(existing, ensure_ascii=False, separators=(",", ":"))
    request = request_for("en", response_contract=LADDER_RESPONSE,
                          preserved_annotations=preserved_annotations(existing))
    result = collect_response(FakeProvider([[TextDelta(raw)]]), request)
    assert result.message.content == raw
    assert json.loads(result.message.content)["rungs"][0]["branches"][0]["inputs"][0]["address"] == "X0"
    changed = copy.deepcopy(existing)
    changed["rungs"][0]["branches"][0]["inputs"][0]["label"] = "模型新写的按钮说明"
    wrong = json.dumps(changed, ensure_ascii=False)
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta(wrong)]]), request)


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
@pytest.mark.parametrize("syntax", ["(* {} *)", "// {}\n", "(* (* {} *) *)"])
def test_st_comments_are_checked_while_code_literals_and_addresses_remain_opaque(language, good, bad, syntax):
    code = syntax.format(bad) + "\nY0 := X0;\nmessage := '用户提供的字面量 (* 不是注释 *)';"
    raw = json.dumps({"st_code": code}, ensure_ascii=False)
    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(FakeProvider([[TextDelta(raw)]]), request_for(language, response_contract=ST_RESPONSE))
    assert captured.value.raw_response.message.content == raw
    valid = "(* " + good + " *)\nY0 := X0;\nmessage := '中文原文字面量 // 不翻译';"
    raw_valid = json.dumps({"st_code": valid}, ensure_ascii=False)
    result = collect_response(FakeProvider([[TextDelta(raw_valid)]]), request_for(language, response_contract=ST_RESPONSE))
    assert result.message.content == raw_valid


def test_st_exact_existing_comment_is_preserved_but_new_comment_is_checked():
    source = {"st_code": "(* 用户已确认的原始注释 *)\nY0 := X0;"}
    request = request_for("en", response_contract=ST_RESPONSE,
                          preserved_annotations=preserved_annotations(source))
    raw = json.dumps(source, ensure_ascii=False)
    assert collect_response(FakeProvider([[TextDelta(raw)]]), request).message.content == raw
    changed = json.dumps({"st_code": source["st_code"] + "\n// 模型新写的说明"}, ensure_ascii=False)
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta(changed)]]), request)


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
def test_tool_loop_preserves_evidence_but_rejects_wrong_final_answer(language, good, bad):
    set_language(language)
    call = ToolCall("manual-1", "search_plc_manual", {"query": "T0"})
    runtime = FakeRuntime()
    content = []
    provider = FakeProvider([[ToolCallEnd(call)], [TextDelta(bad)]])
    with pytest.raises(ResponseRejectedError) as captured:
        run_tool_agent("Explain T0", context=object(), provider=provider,
                       runtime=runtime, on_content_chunk=content.append)
    assert content == []
    assert runtime.calls == [call]
    assert captured.value.raw_response.message.content == bad
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.data["data"] == runtime.evidence
    assert json.loads(tool_message.content)["data"] == runtime.evidence


def test_tool_loop_accepts_english_final_with_verbatim_chinese_manual_quote():
    set_language("en")
    runtime = FakeRuntime()
    final = 'The manual states: "' + runtime.evidence["text"] + '" Check T0.'
    provider = FakeProvider([
        [ToolCallEnd(ToolCall("manual-1", "search_plc_manual", {"query": "T0"}))],
        [TextDelta(final)],
    ])
    result = run_tool_agent("Explain T0", context=object(), provider=provider, runtime=runtime)
    assert result.content == final
    assert result.rounds == 2


@pytest.mark.parametrize("as_mapping", [False, True])
def test_tool_evidence_quotation_uses_the_content_sent_to_the_provider(as_mapping):
    source = "定时器使用 T0，手册原文必须保留。"
    message = (
        {"role": "tool", "tool_call_id": "manual-1", "name": "search_plc_manual", "content": source}
        if as_mapping else ToolResult("manual-1", "search_plc_manual", source)
    )
    request = ModelRequest.from_messages([message], response_language="en")
    content = 'The manual states: "' + source + '" Check T0.'
    result = collect_response(FakeProvider([[TextDelta(content)]]), request)
    assert result.message.content == content
    assert request.messages[0].content == source


@pytest.mark.parametrize("as_json_string", [False, True])
@pytest.mark.parametrize("tool_name", ["create_program_candidate", "patch_program"])
def test_wrong_candidate_tool_annotations_are_rejected_before_tool_execution(tool_name, as_json_string):
    set_language("en")
    ladder = candidate_ladder("模型新写的错误语言说明")
    payload = (
        {"ladder": ladder, "program_name": "MAIN"}
        if tool_name == "create_program_candidate"
        else {"patch": {"operations": [{"operation": "modify_network", "network": "N0001",
                                       "ladder": ladder["rungs"][0]}]}}
    )
    arguments = json.dumps(payload, ensure_ascii=False) if as_json_string else payload
    call = ToolCall("candidate-1", tool_name, arguments)
    provider = FakeProvider([[ToolCallEnd(call)]])
    runtime = FakeRuntime()
    published = []
    with pytest.raises(ResponseRejectedError) as captured:
        run_tool_agent("Prepare a candidate", context=object(), provider=provider,
                       runtime=runtime, on_content_chunk=published.append)
    assert runtime.calls == []
    assert published == []
    assert captured.value.raw_response.message.tool_calls == (call,)
    assert any("arguments" in violation.path for violation in captured.value.violations)


def test_candidate_tool_argument_contract_keeps_program_machine_tokens_unchanged():
    payload = {"ladder": candidate_ladder(), "program_name": "MAIN"}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    call = ToolCall("candidate-1", "create_program_candidate", encoded)
    published = []
    result = collect_response(
        FakeProvider([[ToolCallEnd(call)]]),
        request_for("en", tool_response_contracts=((call.name, tool_argument_contract(call.name)),)),
        on_event=published.append,
    )
    assert result.message.tool_calls == (call,)
    assert published == [ToolCallEnd(call)]
    assert result.message.tool_calls[0].arguments == encoded


@pytest.mark.parametrize("language,good,bad", LANGUAGE_CASES)
def test_fallback_discards_partial_callbacks_and_keeps_original_language(language, good, bad):
    set_language(language)
    failed_call = ToolCall("failed-1", "create_program_candidate", {})
    provider = FakeProvider([
        [TextDelta(bad), ReasoningDelta(bad), ToolCallEnd(failed_call),
         ModelProviderError("fixture stream unavailable", code="unavailable", retryable=True)],
        [TextDelta(good)],
    ])
    callbacks, events = [], []
    result = collect_response(
        provider,
        ModelRequest((UserMessage("Check X0"),)),
        on_content_chunk=callbacks.append,
        on_reasoning_chunk=callbacks.append,
        on_event=events.append,
        fallback_to_non_stream=True,
        on_fallback=lambda _error: set_language("ja" if language != "ja" else "en"),
    )
    assert [request.response_language for request in provider.requests] == [language, language]
    assert [request.stream for request in provider.requests] == [True, False]
    assert callbacks == [good]
    assert events == [TextDelta(good)]
    assert result.message.content == good
    assert result.message.tool_calls == ()
    assert len(result.raw_attempts) == 2
    assert result.raw_attempts[0].message.content == bad
    assert result.raw_attempts[0].message.tool_calls == (failed_call,)


def test_wrong_fallback_retains_both_attempts_without_publishing_either():
    provider = FakeProvider([
        [TextDelta("Partial attempt."), ModelProviderError("fixture timeout", code="timeout")],
        [TextDelta("输出已经启动。")],
    ])
    published = []
    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(provider, request_for("en"), fallback_to_non_stream=True,
                         on_content_chunk=published.append, on_event=published.append)
    assert published == []
    assert len(captured.value.raw_attempts) == 2
    assert captured.value.raw_attempts[0].message.content == "Partial attempt."
    assert captured.value.raw_attempts[1].message.content == "输出已经启动。"


def test_language_change_after_request_creation_does_not_change_acceptance_or_instruction():
    set_language("en")
    request = ModelRequest((UserMessage("Check X0"),))
    set_language("ja")
    provider = FakeProvider([[TextDelta("X0 is active.")]])
    result = collect_response(provider, request)
    assert result.response_language == "en"
    assert "English (en)" in provider.requests[0].messages[0].content
    assert result.message.content == "X0 is active."
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta("入力を確認します。")]]), request)


def test_tool_rounds_share_the_run_language_when_global_setting_changes():
    set_language("en")
    runtime = FakeRuntime()

    class ChangingProvider:
        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                set_language("ja")
                yield ToolCallEnd(ToolCall("manual-1", "search_plc_manual", {"query": "T0"}))
            else:
                yield TextDelta("The manual was checked.")

    provider = ChangingProvider()
    result = run_tool_agent("Explain T0", context=object(), provider=provider, runtime=runtime)
    assert result.content == "The manual was checked."
    assert [request.response_language for request in provider.requests] == ["en", "en"]
    assert get_language() == "ja"  # The setting itself still changed outside the run.


def test_nested_context_restores_the_outer_request_language_after_rejection():
    set_language("zh-CN")
    with language_context("en"):
        outer = ModelRequest((UserMessage("Check X0"),))
        with language_context("ja"):
            inner = ModelRequest((UserMessage("Check X0"),))
            with pytest.raises(ResponseRejectedError):
                collect_response(FakeProvider([[TextDelta("The output is active.")]]), inner)
        assert get_language() == "en"
        assert collect_response(FakeProvider([[TextDelta("X0 is active.")]]), outer).response_language == "en"
    assert get_language() == "zh-CN"


def test_concurrent_thread_contexts_keep_independent_acceptance_languages():
    barrier = threading.Barrier(3)

    def run(case):
        language, good, bad = case
        with language_context(language):
            request = ModelRequest((UserMessage("Check X0"),))
            barrier.wait(timeout=10)
            result = collect_response(FakeProvider([[TextDelta(good)]]), request)
            with pytest.raises(ResponseRejectedError):
                collect_response(FakeProvider([[TextDelta(bad)]]), request)
            return result.response_language, result.message.content

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run, LANGUAGE_CASES))
    assert results == [(language, good) for language, good, _bad in LANGUAGE_CASES]


def test_concurrent_async_contexts_keep_independent_acceptance_languages():
    async def run(case):
        language, good, _bad = case
        with language_context(language):
            request = ModelRequest((UserMessage("Check X0"),))
            await asyncio.sleep(0)
            response = collect_response(FakeProvider([[TextDelta(good)]]), request)
            return response.response_language, response.message.content

    async def gather():
        return await asyncio.gather(*(run(case) for case in LANGUAGE_CASES))

    assert asyncio.run(gather()) == [(language, good) for language, good, _bad in LANGUAGE_CASES]


def test_reusing_messages_with_a_changed_target_has_only_one_language_instruction():
    provider = FakeProvider([[TextDelta("Done.")], [TextDelta("完了しました。")]])
    collect_response(provider, request_for("en"))
    reused = replace(provider.requests[0], response_language="ja")
    result = collect_response(provider, reused)
    systems = "\n".join(message.content for message in provider.requests[1].messages if isinstance(message, SystemMessage))
    assert systems.count("[Application response language]") == 1
    assert "English (en)" not in systems
    assert result.response_language == "ja"


def test_json_final_contract_allows_tool_only_turn_but_still_checks_final_prose():
    contract = ResponseContract("summary", "json", ("summary",))
    call = ToolCall("manual-1", "search_plc_manual", {"query": "T0"})
    request = request_for("en", response_contract=contract)
    result = collect_response(FakeProvider([[ToolCallEnd(call)]]), request)
    assert result.message.content == ""
    assert result.message.tool_calls == (call,)
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta('{"summary":"新写中文。"}')]]), request)
    with pytest.raises(ResponseRejectedError):
        collect_response(FakeProvider([[TextDelta("")]]), request)


@pytest.mark.parametrize("payload", [
    {"findings": ["新写中文。"]},
    {"findings": {"description": "新写中文。"}},
    {"local_findings": [{"recommended_changes": ["新写中文。"]}]},
    {"verification_steps": ["新写中文。"]},
    {"online_checks": {"condition": "新写中文。"}},
    {"possible_causes": "新写中文。"},
])
def test_legacy_inspection_shapes_cannot_bypass_language_acceptance(payload):
    from application.response_contracts import INSPECTION_RESPONSE
    with pytest.raises(ResponseRejectedError):
        collect_response(
            FakeProvider([[TextDelta(json.dumps(payload, ensure_ascii=False))]]),
            request_for("en", response_contract=INSPECTION_RESPONSE),
        )


@pytest.mark.parametrize("text", ["检查・完成", "检查ー完成"])
def test_japanese_punctuation_is_not_evidence_of_japanese_prose(text):
    with pytest.raises(ResponseRejectedError) as captured:
        collect_response(FakeProvider([[TextDelta(text)]]), request_for("ja"))
    assert captured.value.violations[0].reason == "ambiguous_han_only"
