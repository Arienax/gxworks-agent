"""Language preferences affect presentation, never PLC or transport payloads."""

import ast
import copy
import os
from pathlib import Path
import re
from string import Formatter


import pytest

from shared.i18n import (
    DisplayLanguageGuard, catalog, get_language, language_context,
    normalize_language, runtime_text, set_language, tr, translate,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    set_language("zh-CN")
    yield
    set_language(previous)


@pytest.mark.parametrize("value,expected", [
    (None, "zh-CN"), ("unsupported", "zh-CN"), ("en-US", "en"),
    ("EN_gb", "en"), ("ja-JP", "ja"), ("jp", "ja"),
])
def test_language_normalization(value, expected):
    assert normalize_language(value) == expected


def test_catalogs_have_matching_keys_and_template_fields():
    assert catalog("en").keys() == catalog("ja").keys()
    for language in ("en", "ja"):
        for source, target in catalog(language).items():
            assert target.strip(), (language, source)
            def fields(value):
                return sorted((name, spec, conversion) for _, name, spec, conversion
                              in Formatter().parse(value) if name is not None)
            assert fields(source) == fields(target), (language, source)
            assert sorted(re.findall(r"%[sdif]", source)) == sorted(re.findall(r"%[sdif]", target)), source


def test_all_marked_ui_templates_have_english_coverage():
    missing = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                source = node.args[0].value
                if re.search(r"[\u3400-\u9fff\u3040-\u30ff]", translate(source, "en")):
                    missing.append((path.name, node.lineno, source))
    assert not missing


def test_live_translation_preserves_opaque_substitution_values():
    label = tr("当前项目：{v0}", v0="用户工程 D0")
    set_language("en")
    assert "用户工程 D0" in str(label)
    assert "当前项目" not in str(label)
    set_language("ja")
    assert "用户工程 D0" in str(label)






def test_model_policy_is_idempotent_snapshotted_and_preserves_payloads():
    from model_runtime.provider import ImageAttachment, ModelRequest, SystemMessage, UserMessage, with_response_language
    image = ImageAttachment("图.png", "image/png", b"image")
    history = (SystemMessage("Return JSON only."), UserMessage("原文 X0", (image,)))
    with language_context("ja"):
        request = ModelRequest(history, options={"response_format": {"type": "json_object"}})
    set_language("en")
    localized = with_response_language(request)
    assert "Japanese" in localized.messages[0].content
    assert "JSON-only" in localized.messages[0].content
    assert "Do not translate JSON keys" in localized.messages[0].content
    assert localized.messages[1] is history[1]
    assert request.messages == history
    assert history[0].content == "Return JSON only."
    assert with_response_language(localized) == localized
    assert localized.options == request.options


@pytest.mark.parametrize("language,good,bad", [
    ("zh-CN", "正在检查 X0 和 Y0。\n", "Checking all the inputs now.\n"),
    ("en", "Checking X0 and Y0.\n", "正在检查输入。\n"),
    ("ja", "X0 と Y0 を確認します。\n", "Checking the inputs.\n"),
    ("ja", "プログラムを確認します。\n", "正在检查程序。\n"),
])
def test_stream_guard_handles_split_chunks_and_warns_once(language, good, bad):
    guard = DisplayLanguageGuard(language)
    output = "".join(guard.feed(char) for char in good) + guard.flush()
    assert output == good
    output = "".join(guard.feed(char) for char in bad) + guard.flush()
    assert bad.strip() not in output
    assert translate("模型未遵守所选语言，已隐藏这段输出。请重试。", language) in output
    assert not (guard.feed(bad) + guard.flush()).strip()


@pytest.mark.parametrize("language", ["en", "ja"])
def test_stream_guard_preserves_machine_tokens(language):
    guard = DisplayLanguageGuard(language)
    payload = '{"network_id":"N0001","opcode":"LD","operand":"X0"}\nOUT Y0\n'
    assert guard.feed(payload) + guard.flush() == payload




@pytest.mark.parametrize("language", ["en", "ja"])
def test_naturalized_display_stream_is_pinned_to_language(language):
    from shared.display_names import DisplayTextStream, naturalize_display_text
    source = '"network_id": "N0001", "description": "X0 → Y0"\n'
    stream = DisplayTextStream(language)
    with language_context(language):
        expected = naturalize_display_text(source)
    set_language("zh-CN")
    assert "".join(stream.feed(char) for char in source) + stream.flush() == expected
    if language == "en":
        assert not re.search(r"[\u3400-\u9fff]", expected)






@pytest.mark.parametrize("utterance", ["重新生成程序", "Please regenerate the program.", "retry", "プログラムを再生成してください。", "再試行"])
def test_regeneration_command_recognizes_supported_languages(utterance):
    from application.request_intent import _is_regenerate_locked_spec_request
    assert _is_regenerate_locked_spec_request(utterance)
    assert not _is_regenerate_locked_spec_request(utterance + " X0 Y0")


def test_fallback_keeps_language_and_raw_response():
    from model_runtime.provider import ModelProviderError, ModelRequest, ResponseRejectedError, TextDelta, UserMessage, collect_response
    from model_runtime.responses import ResponseContract
    requests = []
    raw = '{"description":"原始内容","operand":"X0"}'
    class Provider:
        def stream(self, request):
            requests.append(request)
            if request.stream:
                set_language("ja")
                raise ModelProviderError("fixture stream rejection", code="stream_not_supported")
            yield TextDelta(raw)
    set_language("en")
    displayed = []
    request = ModelRequest((UserMessage("原文"),), response_contract=ResponseContract(
        "fixture", "json", ("description",),
    ))
    with pytest.raises(ResponseRejectedError) as rejected:
        collect_response(Provider(), request, on_content_chunk=displayed.append,
                         fallback_to_non_stream=True)
    assert rejected.value.raw_response.message.content == raw
    assert displayed == []
    assert [request.response_language for request in requests] == ["en", "en"]
    assert all("English" in request.messages[0].content for request in requests)


@pytest.mark.parametrize("language", ["en", "ja"])
def test_simulator_report_localizes_generated_labels_but_keeps_evidence(language):
    from simulator.reporting import build_simulator_report, render_simulator_report_text
    result = {"status": "failed", "results": [{"name": "test_1", "status": "failed", "setup_stage": "complete",
              "assertions": [{"step_id": "step_1", "at_ms": 10, "address": "Y0", "passed": False,
                              "detail": "actual=1, eq 0, tolerance=0.0"}]}]}
    workflow = {"status": "failed", "execution": {"result": result}}
    original = copy.deepcopy(workflow)
    set_language(language)
    report = build_simulator_report(workflow)
    text = render_simulator_report_text(report)
    assert "Y0" in text
    assert report["status"] == "failed"
    assert workflow == original
    if language == "en":
        assert not re.search(r"[\u3400-\u9fff]", text), text


def _reported_spec_analysis():
    questions = [
        "Which X input is the start pushbutton?",
        "Which X input is the stop pushbutton?",
        "Which Y output is controlled?",
        "Is the stop pushbutton wired as normally closed (NC) or normally open (NO)?",
    ]
    return {
        "plc_model": "FX3U",
        "summary": "Start/stop control with a self-holding circuit.",
        "approaches": [{
            "name": "Start input in parallel with output contact",
            "generation_guide": "One rung: (start NO input OR output contact) AND stop NC contact -> output COIL.",
            "generation_contract": {
                "forbidden_opcodes": ["SET", "RST"],
                "required_structures": ["self_hold"],
                "forbidden_structures": ["set_reset_latch", "register_state_machine"],
            },
        }],
        "missing_info": [{"id": f"parameter_{i}", "question": question, "required": True}
                         for i, question in enumerate(questions)],
        "suggested_io": {},
    }




def test_contract_translation_does_not_change_contracts_or_default_summary():
    from plc.specification.approach import format_contract_summary, generation_contract_signature, STRUCTURE_LABELS
    approach = _reported_spec_analysis()["approaches"][0]
    before = copy.deepcopy(approach)
    signature = generation_contract_signature(approach)
    original = format_contract_summary(approach)
    set_language("en")
    translated = str(format_contract_summary(approach, localized=True))
    assert "Self-holding circuit" in translated
    assert "D-register step state machine" in translated
    assert "SET/RST" in translated
    assert format_contract_summary(approach) == original
    assert generation_contract_signature(approach) == signature
    assert STRUCTURE_LABELS["self_hold"] == "自保持回路"
    assert approach == before


def test_validation_presentation_preserves_question_text_and_issue_fields():
    issue = {"code": "required_parameter_missing", "path": "$.parameters[0].value", "row": 0,
             "message": '必填参数“用户自定义名称 {X0} / start_button”尚未填写'}
    before = copy.deepcopy(issue)
    set_language("en")
    rendered = runtime_text(issue["message"])
    assert 'Required parameter' in rendered
    assert '用户自定义名称 {X0}' in rendered
    assert '尚未填写' not in rendered
    assert issue == before


@pytest.mark.parametrize("message", [
    "参数ID“start”重复（首次位于第 1 行）",
    "I/O 地址 X0 重复（首次位于第 1 行）",
    "X8 不是有效的 FX3U 八进制 X 地址",
    "D9000 超出 FX3U D0-D8511 范围",
    "M8001 是系统特殊软元件，使用前需核对读写属性和 CPU/硬件条件",
    "方案与第 1 个方案使用了相同生成约束，无法保证选项代表不同实现",
    "同一指令同时被要求和禁止：SET",
    "指令任选组没有可用候选：SET, RST 全部被显式禁止",
    "已填写的继电器输出类型与内置高速脉冲输出不兼容",
])
def test_local_validation_templates_are_translated_before_display(message):
    set_language("en")
    rendered = runtime_text(message)
    assert not re.search(r"[\u3400-\u9fff]", rendered), rendered
