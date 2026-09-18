"""Language preferences affect presentation, never PLC or transport payloads."""

import ast
import copy
import os
from pathlib import Path
import re
from string import Formatter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from shared.i18n import (
    DisplayLanguageGuard, catalog, get_language, language_context,
    normalize_language, runtime_text, set_language, tr, translate,
)
from ui.desktop.qt import (
    QApplication, QComboBox, QDialog, QLabel, QLineEdit, QListWidgetItem,
    QMenu, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QWidget,
)


_APPLICATION = QApplication.instance() or QApplication([])
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


def test_qt_live_labels_tabs_actions_and_headers_preserve_editor_and_item_data():
    label = QLabel(tr("设置"))
    button = QPushButton(tr("取消"))
    editor = QLineEdit()
    editor.setText("用户原文 X0 → Y0")
    editor.setPlaceholderText(tr("等待中"))
    combo = QComboBox()
    combo.addItem(tr("梯形图"), "ladder")
    tabs = QTabWidget()
    tabs.addTab(QWidget(), tr("语言"))
    menu = QMenu()
    action = menu.addAction(tr("设置"))
    table = QTableWidget(1, 1)
    table.setHorizontalHeaderLabels([tr("设置")])
    table.setItem(0, 0, QTableWidgetItem(tr("取消")))
    item = QListWidgetItem(tr("设置"))  # Qt items are not hashable.
    for language in ("en", "ja", "zh-CN"):
        set_language(language)
        assert label.text() == translate("设置")
        assert button.text() == translate("取消")
        assert tabs.tabText(0) == translate("语言")
        assert action.text() == translate("设置")
        assert table.horizontalHeaderItem(0).text() == translate("设置")
        assert table.item(0, 0).text() == translate("取消")
        assert item.text() == translate("设置")
        assert combo.itemText(0) == translate("梯形图")
        assert combo.currentData() == "ladder"
        assert editor.text() == "用户原文 X0 → Y0"
    label.setText("原文")
    set_language("en")
    assert label.text() == "原文"  # Explicit replacement removes the old binding.
    for widget in (label, button, editor, combo, tabs, menu, table):
        widget.close()


def test_settings_save_language_without_api_key_and_cancel_does_not_apply(monkeypatch):
    import ui.desktop.dialogs.config as config_dialog
    from storage.config import DEFAULT_MODEL_PROFILES
    config = {"language": "zh-CN", "activeModelProfileId": "deepseek-default",
              "modelProfiles": copy.deepcopy(list(DEFAULT_MODEL_PROFILES))}
    saved = []
    monkeypatch.setattr(config_dialog, "load_full_config", lambda: copy.deepcopy(config))
    monkeypatch.setattr(config_dialog, "get_api_key", lambda *_: "")
    monkeypatch.setattr(config_dialog, "save_config", lambda value: saved.append(copy.deepcopy(value)))
    def no_credentials(*_args, **_kwargs):
        pytest.fail("Changing only language must not write or delete API credentials")
    monkeypatch.setattr(config_dialog, "write_api_key", no_credentials)
    monkeypatch.setattr(config_dialog, "delete_api_key", no_credentials)
    dialog = config_dialog.RequestTemplateConfigDialog()
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("ja"))
    dialog.reject()
    assert not saved
    assert get_language() == "zh-CN"
    dialog = config_dialog.RequestTemplateConfigDialog()
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("en"))
    dialog._on_save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert saved[0]["language"] == "en"
    assert get_language() == "en"
    assert not dialog.api_key_configured
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        translate("API 设置"), translate("API 高级设置"), translate("语言")]


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


def test_thinking_panel_displays_accepted_bytes_without_reinterpreting_language():
    from ui.desktop.widgets.activity import ThinkingPanel
    set_language("en")
    panel = ThinkingPanel()
    # This Chinese quotation is accepted source evidence, not generated prose.
    # A display-time language heuristic must not discard or rewrite it.
    panel.append_reasoning('Source: "用户原文". ')
    panel.append_content('Done: network_id=N0001, X0 → Y0.')
    panel.set_status("等待中")
    text = panel.content_edit.toPlainText()
    assert 'Source: "用户原文".' in text
    assert 'network_id=N0001' in text
    assert "X0 → Y0" in text
    assert panel.status_label.text() == translate("等待中")
    panel.close()


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


@pytest.mark.parametrize("language", ["en", "ja"])
def test_workbench_settings_entry_and_live_switch_keep_draft(monkeypatch, tmp_path, language):
    import ui.desktop.main_window as main
    from storage.session import SessionStore
    monkeypatch.setattr(main, "SessionStore", lambda *args, **kwargs: SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path))
    monkeypatch.setattr(main, "load_full_config", lambda: {"language": language})
    window = main._IndustrialWorkbenchUI()
    assert get_language() == language
    assert window.settings_button.text() == translate("设置")
    window.composer_edit.setPlainText("用户原文 X0")
    target_mode = window.target_combo.currentData()
    set_language("ja" if language == "en" else "en")
    assert window.settings_button.text() == translate("设置")
    assert window.composer_edit.toPlainText() == "用户原文 X0"
    assert window.target_combo.currentData() == target_mode
    window.close()


@pytest.mark.parametrize("language,expected", [("en", "Cancel"), ("ja", "キャンセル"), ("zh-CN", "取消")])
def test_standard_dialog_labels_follow_language(language, expected):
    from ui.desktop.qt import QCoreApplication
    set_language(language)
    assert QCoreApplication.translate("QDialogButtonBox", "Cancel") == expected
    # An unknown native message must fall back to its source, never blank text.
    assert QCoreApplication.translate("QFileDialog", "unrecognized native text") == "unrecognized native text"
    if language == "ja":
        assert QCoreApplication.translate("QFileDialog", "&Look in:") == "場所："
        assert QCoreApplication.translate("QFileDialog", "Files of &type:") == "ファイルの種類："


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
                raise ModelProviderError("fixture transport failure")
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


@pytest.mark.parametrize("language", ["en", "ja"])
def test_reported_spec_navigation_validation_and_constraints(language):
    from ui.desktop.qt import QLabel
    from ui.desktop.workbench import SpecificationWorkbenchDialog
    # NAV_ITEMS was created at import time in Chinese, before selecting English.
    set_language(language)
    analysis = _reported_spec_analysis()
    dialog = SpecificationWorkbenchDialog(analysis, "User original text X0", plc_model="FX3U")
    nav_sources = ["概览", "实现方案", "控制参数", "I/O 映射", "高级约束"]
    try:
        assert [dialog.nav.item(i).text() for i in range(5)] == [translate(s) for s in nav_sources]
        assert len(dialog._validation_result()["errors"]) == 4
        assert not dialog.confirm_button.isEnabled()
        for question in analysis["missing_info"]:
            expected = translate("必填参数“{v0}”尚未填写").format(v0=question["question"])
            assert expected in dialog.validation_summary.text()
            assert expected in dialog.validation_details.text()
        for widget in (dialog.contract_preview, dialog.advanced_contract):
            assert translate("禁用指令 ") in widget.text()
            assert translate("自保持回路") in widget.text()
            assert translate("SET/RST锁存") in widget.text()
            assert translate("D寄存器步进状态机") in widget.text()
            assert "SET/RST" in widget.text()
        if language == "en":
            surfaces = [dialog.validation_summary, dialog.validation_details, dialog.contract_preview,
                        dialog.advanced_contract, *dialog.findChildren(QLabel, "ApproachDescription")]
            assert surfaces
            for widget in surfaces:
                assert not re.search(r"[\u3400-\u9fff]", widget.text()), widget.text()
        before = copy.deepcopy(dialog._current_draft())
        changed = []
        dialog.draft_changed.connect(changed.append)
        for next_language in ("ja", "zh-CN", "en"):
            set_language(next_language)
            assert [dialog.nav.item(i).text() for i in range(5)] == [translate(s) for s in nav_sources]
            assert dialog._current_draft() == before
            assert len(dialog._validation_result()["errors"]) == 4
            assert not dialog.confirm_button.isEnabled()
        assert not changed  # Retranslation must not create draft edits.
        dialog.nav.setCurrentRow(4)
        assert dialog.page_title.text() == translate("高级约束")
        dialog.editor.parameter_table.item(0, 1).setText("X0")
        assert len(dialog._validation_result()["errors"]) == 3
        assert 'Required parameter' in dialog.validation_summary.text()
        assert "尚未填写" not in dialog.validation_details.text()
    finally:
        dialog.close()


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
    from ui.desktop.workbench.editor import RequirementReviewCard as _SpecificationEditor
    issue = {"code": "required_parameter_missing", "path": "$.parameters[0].value", "row": 0,
             "message": '必填参数“用户自定义名称 {X0} / start_button”尚未填写'}
    before = copy.deepcopy(issue)
    set_language("en")
    rendered = _SpecificationEditor._validation_message(issue)
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
    from ui.desktop.workbench.editor import RequirementReviewCard as _SpecificationEditor
    set_language("en")
    rendered = _SpecificationEditor._validation_message({"message": message})
    assert not re.search(r"[\u3400-\u9fff]", rendered), rendered


def test_destroyed_spec_dialog_unsubscribes_language_updates():
    from ui.desktop.qt import QCoreApplication, QEvent
    from ui.desktop.workbench import SpecificationWorkbenchDialog
    dialog = SpecificationWorkbenchDialog(_reported_spec_analysis(), "Start/stop", plc_model="FX3U")
    # Keep the Python wrapper alive after C++ destruction to exercise the actual
    # lifecycle hazard, rather than relying on garbage collection timing.
    dialog.deleteLater()
    event_type = getattr(QEvent.Type, "DeferredDelete", None)
    if event_type is None:  # PyQt5 compatibility surface
        event_type = QEvent.DeferredDelete
    QCoreApplication.sendPostedEvents(None, event_type)
    set_language("en")
    set_language("ja")
