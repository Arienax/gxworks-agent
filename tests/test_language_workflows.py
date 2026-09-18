"""Offline language acceptance regressions across public workflows and Qt.

Only model transport and retrieval are faked. The production request collector,
response parsers, compiler, and UI boundaries remain in the exercised paths.
"""

import copy
import json
import os
import re
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import application.model_api as api
import ui.desktop.main_window as main
from shared.i18n import get_language, language_context, set_language
from model_runtime.provider import ModelProviderError, ResponseRejectedError, TextDelta
from ui.desktop.qt import QApplication, QLabel
from ui.desktop.workbench import MessageBubble


_APPLICATION = QApplication.instance() or QApplication([])
_HAN = re.compile(r"[\u3400-\u9fff]")
_WRONG = {
    "en": "正在检查输入并准备输出。",
    "zh-CN": "The input is ready for the next operation.",
    "ja": "正在检查输入并准备输出。",
}
_GOOD = {
    "en": "The input controls the output.",
    "zh-CN": "输入信号控制输出。",
    "ja": "入力信号が出力を制御します。",
}


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    set_language("zh-CN")
    try:
        yield
    finally:
        set_language(previous)


class _Provider:
    def __init__(self, payload):
        self.raw = json.dumps(payload, ensure_ascii=False)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        # Even JSON keys, escapes, and multibyte words can be split anywhere.
        yield from (TextDelta(char) for char in self.raw)


@pytest.fixture
def offline(monkeypatch):
    """No credentials, network retrieval, saved history, or real device I/O."""
    monkeypatch.setattr(api, "load_full_config", lambda: {})
    monkeypatch.setattr(api, "_active_model_name", lambda *_args: "fake-model")
    from ui.desktop import workers
    monkeypatch.setattr(workers, "load_full_config", lambda: {})
    monkeypatch.setattr(main, "load_full_config", lambda: {})
    monkeypatch.setattr(workers, "get_active_model_name", lambda *_args: "fake-model")
    monkeypatch.setattr(main, "get_active_model_name", lambda *_args: "fake-model")
    monkeypatch.setattr(api, "_build_model_context", lambda *_a, **_k: "")
    monkeypatch.setattr(
        api, "build_workflow_prompt",
        lambda *_a, **_k: ("", SimpleNamespace(vendor="FX3U", task_type="generate")),
    )
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *_a, **_k: "")
    monkeypatch.setattr(api, "_save_history", lambda *_a: pytest.fail("Unexpected history write"))

    def unexpected_provider():
        pytest.fail("Every workflow test must inject a deterministic provider")

    monkeypatch.setattr(api, "get_active_provider", unexpected_provider)


def _analysis(prose):
    return {
        "summary": prose,
        "approaches": [],
        "missing_info": [],
        "suggested_io": {"X": {"X0": prose}, "Y": {"Y0": prose}},
        "assumptions": [],
    }


def _ladder(prose):
    return {
        "device_comments": {"X0": prose, "Y0": prose},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0", "label": prose}],
                "outputs": [{"type": "COIL", "address": "Y0", "label": prose}],
            }],
        }],
    }


def _payload(workflow, prose):
    if workflow.startswith("analysis"):
        return _analysis(prose)
    if workflow.startswith("ladder"):
        return _ladder(prose)
    if workflow.startswith("st"):
        return {"st_code": f"(* {prose} *)\nY0 := X0;"}
    if workflow == "test_suite":
        return {"name": "test_suite_1", "tests": [{"name": "test_1", "description": prose}]}
    return {"summary": prose, "findings": []}


def _invoke(workflow, **kwargs):
    request = "FX3U X0 controls Y0"
    if workflow == "analysis":
        return api.analyze_requirement(request, **kwargs)
    if workflow == "analysis_stream":
        return api.analyze_requirement_streaming(request, **kwargs)
    if workflow in {"ladder", "st"}:
        return api.generate_model_json(
            request, "fake-model", "low", workflow, persist_history=False, **kwargs
        )
    if workflow in {"ladder_stream", "st_stream"}:
        return api.stream_model_response(
            request, "fake-model", "low", workflow.split("_")[0],
            persist_history=False, **kwargs
        )
    if workflow == "debug":
        return api.debug_ladder(request, {}, model_name="fake-model", **kwargs)
    if workflow == "inspection":
        return api.inspect_ladder(
            "program_review", request, {}, {}, model_name="fake-model", **kwargs
        )
    if workflow == "multi_agent":
        return api.run_multi_agent_specialist(
            "reviewer", {"context": {"plc": {"cpu": "FX3U"}}},
            model_name="fake-model", **kwargs
        )
    if workflow == "test_suite":
        return api.generate_simulator_test_suite({}, model_name="fake-model", **kwargs)
    raise AssertionError(workflow)


@pytest.mark.parametrize("language", ["zh-CN", "en", "ja"])
@pytest.mark.parametrize("workflow", [
    "analysis", "analysis_stream", "ladder", "ladder_stream", "st", "st_stream",
    "debug", "inspection", "multi_agent", "test_suite",
])
def test_wrong_language_never_reaches_public_workflow_results(
    offline, monkeypatch, language, workflow
):
    provider = _Provider(_payload(workflow, _WRONG[language]))
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    chunks = []
    options = {"response_language": language}
    if workflow.endswith("_stream") or workflow == "test_suite":
        options["on_content_chunk"] = chunks.append

    # Defaults such as raise_errors=False must not turn rejection into success,
    # empty generation text, or a silently accepted domain candidate.
    with pytest.raises(ResponseRejectedError) as rejected:
        _invoke(workflow, **options)

    assert chunks == []
    assert len(provider.requests) == 1
    assert rejected.value.response_language == language
    assert rejected.value.raw_response.message.content == provider.raw
    assert rejected.value.violations


@pytest.mark.parametrize("language", ["zh-CN", "en", "ja"])
def test_request_preparation_cannot_change_the_workflow_language(
    offline, monkeypatch, language
):
    provider = _Provider(_analysis(_GOOD[language]))
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    preparation = []

    def retrieve(*_args, **_kwargs):
        preparation.append(get_language())
        set_language("ja" if language != "ja" else "en")
        return "\nManual evidence: 启动按钮驱动输出 X0 → Y0。\n"

    monkeypatch.setattr(api, "_build_knowledge_context", retrieve)
    set_language(language)
    result = api.analyze_requirement("FX3U X0 controls Y0")

    assert preparation == [language]
    assert provider.requests[0].response_language == language
    assert result["summary"] == _GOOD[language]
    assert any("启动按钮驱动输出" in str(message.content) for message in provider.requests[0].messages)


@pytest.mark.parametrize("language", ["zh-CN", "en", "ja"])
def test_real_qthread_keeps_parent_context_and_construction_language(
    offline, monkeypatch, language
):
    provider = _Provider(_analysis(_GOOD[language]))
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    with language_context(language):
        worker = main.AnalysisThread("language-thread", "FX3U X0 controls Y0")
    set_language("ja" if language != "ja" else "en")
    completed, failed = [], []
    worker.analysis_done.connect(lambda task, result: completed.append(result))
    worker.analysis_failed.connect(lambda task, error: failed.append(error))

    worker.start()
    assert worker.wait(5000), "Offline worker did not finish"
    _APPLICATION.processEvents()

    assert failed == []
    assert completed[0]["summary"] == _GOOD[language]
    assert [request.response_language for request in provider.requests] == [language]


def test_compiler_transport_fallback_preserves_language_and_discards_partial_output(
    offline, monkeypatch, tmp_path
):
    accepted = {"st_code": "(* The input controls the output. *)\nY0 := X0;"}

    class Provider(_Provider):
        def stream(self, request):
            if request.stream:
                self.requests.append(request)
                set_language("ja")
                raise ModelProviderError("Offline stream rejection", code="stream_not_supported")
            yield from super().stream(request)

    provider = Provider(accepted)
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    set_language("en")
    worker = main.CompilerThread("fallback-language", "FX3U X0 controls Y0", "low", "st", tmp_path)
    set_language("zh-CN")
    completed, failed, chunks = [], [], []
    worker.success.connect(lambda task, result: completed.append(result))
    worker.failure.connect(lambda task, error: failed.append(error))
    worker.content_updated.connect(lambda task, chunk: chunks.append(chunk))

    worker.run()

    assert failed == []
    assert len(completed) == 1
    assert [(request.stream, request.response_language) for request in provider.requests] == [
        (True, "en"), (False, "en")
    ]
    assert "Rejected partial stream" not in "".join(chunks)
    assert (tmp_path / "program.st").read_text(encoding="utf-8") == accepted["st_code"]


@pytest.mark.parametrize("target_mode", ["ladder", "st"])
def test_compiler_language_rejection_does_not_trigger_fallback_or_artifacts(
    offline, monkeypatch, tmp_path, target_mode
):
    provider = _Provider(_payload(target_mode, _WRONG["en"]))
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    set_language("en")
    worker = main.CompilerThread("rejected-language", "FX3U X0 controls Y0", "low", target_mode, tmp_path)
    failed, completed, chunks = [], [], []
    worker.success.connect(lambda task, result: completed.append(result))
    worker.failure.connect(lambda task, error: failed.append(error))
    worker.content_updated.connect(lambda task, chunk: chunks.append(chunk))

    worker.run()

    assert len(failed) == 1
    assert completed == []
    assert chunks == []
    assert len(provider.requests) == 1
    assert list(tmp_path.iterdir()) == []


def test_english_analysis_parser_keeps_local_diagnostics_in_request_language(
    offline, monkeypatch
):
    payload = _analysis(_GOOD["en"])
    payload["suggested_io"].update({"NOTE": "Assume existing wiring", "M": {"X1": "Stop input"}})
    provider = _Provider(payload)
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)

    result = api.analyze_requirement("FX3U X0 controls Y0", response_language="en")

    assert result["summary"] == payload["summary"]
    assert result["suggested_io"]["X"]["X1"] == "Stop input"
    assert result["hardware_config"]["note"] == "Assume existing wiring"
    assert result["format_diagnostics"]
    assert all(not _HAN.search(str(item["message"])) for item in result["format_diagnostics"])


def test_english_analysis_restored_design_question_uses_request_language(
    offline, monkeypatch
):
    provider = _Provider(_analysis("A variable frequency drive controls the motor."))
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)

    result = api.analyze_requirement("FX3U control a VFD motor", response_language="en")

    question = next(item for item in result["missing_info"] if item["id"] == "control_method")
    assert not _HAN.search(str(question["question"]))
    assert question["default"] in question["options"]
    assert question["required"] is True


def test_accepted_agent_text_is_persisted_and_displayed_without_token_rewriting():
    set_language("en")
    content = 'Inspect `network_id` and `manual_start_test`.\nSource: 「启动按钮」.\n{"network_id":"N0001"}'
    saved = []
    window = SimpleNamespace(
        active_task={"id": "agent-task", "project_id": "project"},
        current_project_id=None,
        store=SimpleNamespace(add_message=lambda *args, **kwargs: saved.append((args, kwargs))),
        activity_panel=SimpleNamespace(set_status=lambda _status: None),
        _set_busy=lambda *_args: None,
        _refresh_projects=lambda *_args: None,
    )
    payload = {"content": content, "rounds": 1, "audit": [], "pending_actions": []}
    before = copy.deepcopy(payload)
    main._IndustrialWorkbenchUI._tool_agent_done(window, "agent-task", payload)

    assert saved[0][0][2] == content
    assert payload == before
    bubble = MessageBubble("assistant", saved[0][0][2], kind="agent")
    try:
        assert bubble.findChild(QLabel, "MessageBody").text() == content
    finally:
        bubble.close()


def test_accepted_activity_chunks_preserve_tokens_and_evidence_after_ui_switch():
    content = 'Inspect `network_id`.\nSource: 「启动按钮」.\n{"network_id":"N0001"}'
    set_language("en")
    panel = main.ThinkingPanel()
    window = SimpleNamespace()
    set_language("ja")
    try:
        for token in content:
            chunk = main._IndustrialWorkbenchUI._activity_stream_chunk(window, "task", "content", token)
            panel.append_content(chunk)
        panel.flush_display()
        assert panel.content_edit.toPlainText() == content
    finally:
        panel.close()


def test_legacy_non_ladder_mode_uses_the_same_st_contract_as_its_prompt(offline, monkeypatch):
    provider = _Provider({"st_code": "(* 错误语言的说明。 *)\nY0 := X0;"})
    monkeypatch.setattr(api, "get_active_provider", lambda: provider)
    with pytest.raises(ResponseRejectedError):
        api.generate_model_json(
            "FX3U X0 controls Y0", "fake-model", "low", "ST", response_language="en",
        )
