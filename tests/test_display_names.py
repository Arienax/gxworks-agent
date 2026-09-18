import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from shared.display_names import (
    DisplayTextStream,
    looks_like_internal_identifier,
    naturalize_display_text,
    naturalize_identifier,
    preferred_display_name,
    source_display_name,
    version_display_name,
)
# These assertions intentionally exercise the detailed legacy table editor,
# which the new review workbench now embeds rather than exposing on its facade.
from ui.desktop.workbench import _LegacyRequirementReviewCard as RequirementReviewCard


def test_function_style_identifier_is_presented_as_a_business_label():
    assert naturalize_identifier("function_1_A", kind="功能") == "功能 1A"


def test_common_test_and_step_identifiers_are_naturalized():
    assert naturalize_identifier("manual_start_test") == "手动启动测试"
    assert naturalize_identifier("pump1_start") == "1号泵启动"
    assert naturalize_identifier("restored") == "恢复正常"
    assert naturalize_identifier("N0010") == "程序段 10"


def test_generated_test_vocabulary_is_presented_as_operator_language():
    expected = {
        "FX3U_conveyor_counter_regression": "FX3U 输送带计数器回归测试",
        "auto_stop_via_mode_cycle": "通过模式切换停止自动运行",
        "timer_adds_second": "定时后追加第二台泵",
        "press_start": "按下启动按钮",
        "sensor3_on": "3号传感器接通",
        "start_blocked_by_stop": "停止按钮阻止启动",
    }
    for raw, display in expected.items():
        assert naturalize_identifier(raw) == display


def test_description_has_priority_over_internal_test_name():
    item = {
        "name": "manual_start_latch_and_auto_mode_stop",
        "description": "切换至手动模式后启动水泵并检查自保持",
    }
    assert preferred_display_name(item, kind="测试项目", index=1) == item["description"]


def test_opaque_ids_fall_back_to_a_numbered_natural_label():
    assert naturalize_identifier("sim_20260830T051311_1656cb2c", kind="仿真记录") == "仿真记录"
    assert naturalize_identifier("c2b39f8a0b9e4f218447", kind="测试项目", index=3) == "测试项目 3"
    assert naturalize_identifier("6b149840cb42", kind="项目") == "项目"
    assert naturalize_identifier("agent-0123abcdef", kind="任务") == "任务"


def test_embedded_identifier_is_replaced_but_plc_device_is_preserved():
    assert naturalize_display_text("执行 function_1_A，并检查 Y0") == "执行 功能 1A，并检查 Y0"
    assert naturalize_identifier("Y0") == "Y0"


def test_versions_get_a_readable_label_without_changing_storage_value():
    assert version_display_name("v0007") == "版本 7"
    assert source_display_name("deterministic") == "规则推导"


def test_identifier_detection_does_not_treat_normal_chinese_as_internal():
    assert looks_like_internal_identifier("主泵故障切换") is False
    assert looks_like_internal_identifier("auto_main_pump_fault") is True


def test_stream_converter_waits_for_a_complete_identifier():
    stream = DisplayTextStream()
    rendered = "".join(
        stream.feed(chunk)
        for chunk in ('测试名称：“fun', "ction_1_", 'A”。')
    ) + stream.flush()
    assert rendered == '测试名称：“功能 1A”。'
    assert "function_1_A" not in rendered


def test_stream_converter_waits_for_a_split_quoted_json_key_and_colon():
    stream = DisplayTextStream()
    rendered = "".join(
        stream.feed(chunk)
        for chunk in ('{"at', '_ms"', ': 100, "expect": {"Y0": 1}}')
    ) + stream.flush()
    assert "执行时间： 100" in rendered
    assert "项目" not in rendered


def test_validation_path_keeps_the_failing_test_and_constraint_visible():
    rendered = naturalize_display_text(
        "$.tests[1].invariants[0].type: unsupported invariant ''"
    )
    assert rendered.startswith("测试 2 / 运行约束 1 / 类型:")
    assert "程序内部位置" not in rendered


def test_hardware_model_and_file_names_are_not_mistaken_for_ui_ids():
    message = "驱动器 FR-D700，PLC FX3U-48MR，文件 program_before_import.csv"
    assert naturalize_display_text(message) == message


def test_dotted_dependency_name_is_presented_as_a_readable_component():
    message = "No module named 'pydantic_core.pydantic_core'"
    rendered = naturalize_display_text(message)
    assert "pydantic_core" not in rendered
    assert "API 运行组件" in rendered


def test_review_card_displays_natural_names_but_collects_stable_values():
    app = QApplication.instance() or QApplication([])
    analysis = {
        "summary": "function_1_A",
        "missing_info": [
            {
                "id": "function_1_A",
                "question": "function_1_A",
                "options": ["manual_start_test"],
                "default": "manual_start_test",
                "required": False,
                "source": "deterministic",
            }
        ],
        "suggested_io": {
            "M": {"M0": "function_1_A"},
        },
    }
    card = RequirementReviewCard(
        analysis,
        "测试需求",
        plc_model="FX3U",
    )

    parameter_row = next(
        row
        for row in range(card.parameter_table.rowCount())
        if card.parameter_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        == "function_1_A"
    )
    assert card.parameter_table.item(parameter_row, 0).text() == "功能 1A"
    assert card.parameter_table.item(parameter_row, 2).text() == "规则推导"
    assert "manual_start_test" not in card.parameter_table.item(
        parameter_row, 4
    ).text()
    collected_parameter = next(
        item
        for item in card._collect_parameters()
        if item["name"] == "function_1_A"
    )
    assert collected_parameter["source"] == "deterministic"
    assert "manual_start_test" in collected_parameter["note"]

    io_row = next(
        row
        for row in range(card.io_table_widget.rowCount())
        if card.io_table_widget.item(row, 1).text() == "M0"
    )
    assert card.io_table_widget.item(io_row, 2).text() == "功能 1A"
    collected_io = next(
        item for item in card._collect_io_table() if item["address"] == "M0"
    )
    assert collected_io["label"] == "function_1_A"
    assert "function_1_A" not in card.raw_preview.toPlainText()

    card.close()
    app.processEvents()
