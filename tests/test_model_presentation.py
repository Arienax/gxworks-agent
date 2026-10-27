"""Live previews are separate from accepted data and never release tool calls."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from application.model_progress import ModelJobContext, ModelProgressReporter
from model_runtime.provider import (ModelRequest, ResponseProgress, ResponsePreview, ResponseRejectedError,
    TextDelta, ReasoningDelta, ToolCall, ToolCallEnd, UserMessage,
    collect_response, response_policy_scope)
from application.response_contracts import ANALYSIS_RESPONSE, tool_argument_contract


class Provider:
    def __init__(self, events):
        self.events = events
    def stream(self, request):
        yield from self.events


def request():
    return ModelRequest((UserMessage("起保停"),), response_language="zh-CN", response_contract=ANALYSIS_RESPONSE)


def test_language_preference_preserves_text_and_request_policy_is_frozen():
    text = '{"summary":"Start and stop control."}'
    with response_policy_scope(enforce_language=False):
        preferred = request()
    assert collect_response(Provider([TextDelta(text)]), preferred).message.content == text
    with pytest.raises(ResponseRejectedError):
        collect_response(Provider([TextDelta(text)]), request())


@pytest.mark.parametrize("bad", ['{"summary":', '[]', '{"summary":{"wrong":"type"}}'])
def test_preferred_language_still_rejects_structure_and_discards_preview(bad):
    previews, accepted, calls = [], [], []
    with response_policy_scope(enforce_language=False, on_preview=previews.append):
        with pytest.raises(ResponseRejectedError):
            collect_response(Provider([TextDelta(bad)]), request(),
                             on_content_chunk=accepted.append, on_event=calls.append)
    assert previews[-1] == ResponsePreview("discard")
    assert accepted == calls == []


def test_progress_and_preview_arrive_before_response_finishes():
    entered, release = threading.Event(), threading.Event()
    progress, preview, accepted = [], [], []
    def paused():
        yield ReasoningDelta("Planning the requirement.")
        yield TextDelta('{"summary":"')
        entered.set()
        assert release.wait(5)
        yield TextDelta('起保停"}')
    def worker():
        with response_policy_scope(enforce_language=False, on_progress=progress.append, on_preview=preview.append):
            return collect_response(Provider(paused()), request(), on_content_chunk=accepted.append)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker)
        try:
            assert entered.wait(5)
            assert not future.done()
            assert [p.phase for p in progress] == ["waiting", "thinking", "receiving"]
            assert preview[-1] == ResponsePreview("content", '{"summary":"')
            assert accepted == []
        finally:
            release.set()
        assert future.result().message.content == '{"summary":"起保停"}'
    assert progress[-1].phase == "validating"
    assert preview[-1].kind == "complete"
    assert ''.join(accepted) == '{"summary":"起保停"}'


def test_invalid_tool_arguments_never_become_executable_in_preference_mode():
    previews, executable = [], []
    tool = ToolCall("call", "create_program_candidate", "{")
    with response_policy_scope(enforce_language=False, on_preview=previews.append):
        req = ModelRequest((UserMessage("生成"),), tool_response_contracts=(
            (tool.name, tool_argument_contract(tool.name)),))
        with pytest.raises(ResponseRejectedError):
            collect_response(Provider([ToolCallEnd(tool)]), req, on_event=executable.append)
    assert executable == []
    assert all(event.text == "" for event in previews)


def test_progress_is_throttled_but_phase_changes_and_preview_reset_are_immediate():
    records = []
    now = [0.0]
    reporter = ModelProgressReporter(SimpleNamespace(emit=lambda kind, data: records.append((kind, data))), clock=lambda: now[0])
    reporter(ResponseProgress("waiting"))
    reporter.preview(ResponsePreview("start"))
    reporter(ResponseProgress("receiving", 1))
    reporter.preview(ResponsePreview("content", "a"))
    for count in range(2, 102):
        reporter(ResponseProgress("receiving", count))
        reporter.preview(ResponsePreview("content", "b"))
    assert len([r for r in records if r[0] == "model_progress"]) == 2
    assert len([r for r in records if r[1].get("kind") == "delta"]) == 1
    now[0] = 1.0
    reporter(ResponseProgress("validating", 101))
    reporter.preview(ResponsePreview("complete"))
    assert ''.join(r[1].get("content", "") for r in records) == "a" + "b" * 100
    reporter.preview(ResponsePreview("discard"))
    assert records[-1][1]["kind"] == "discard"
    reporter(ResponseProgress("waiting"))
    reporter.preview(ResponsePreview("start"))
    assert records[-1][1]["request_number"] == 2


def test_display_limit_does_not_truncate_the_actual_response():
    records = []
    reporter = ModelProgressReporter(SimpleNamespace(emit=lambda kind, data: records.append((kind, data))))
    text = '{"summary":"' + '启停' * 40000 + '"}'
    with response_policy_scope(enforce_language=False, on_progress=reporter, on_preview=reporter.preview):
        result = collect_response(Provider([TextDelta(text)]), request())
    assert result.message.content == text
    assert len(''.join(r[1].get("content", "") for r in records)) == 64000
    assert records[-1][1]["truncated"] is True


def test_accepted_chunks_are_coalesced_before_next_stage_without_losing_text():
    records = []
    context = ModelJobContext(SimpleNamespace(emit=lambda kind, data: records.append((kind, data))))
    context.emit("reasoning", {"text": "思路"})
    for _ in range(1500):
        context.emit("content", {"text": "字"})
    assert records == []
    context.emit("progress", {"message": "检查程序"})
    assert records == [("reasoning", {"text": "思路"}), ("content", {"text": "字" * 1500}),
                       ("progress", {"message": "检查程序"})]
    context.flush()
    assert len(records) == 3
