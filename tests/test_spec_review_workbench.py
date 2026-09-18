import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.desktop.qt import QApplication
from ui.desktop.workbench import RequirementReviewCard, SpecificationWorkbenchDialog


_APPLICATION = None


def _app():
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    return _APPLICATION


def _analysis():
    return {
        "plc_model": "FX3U",
        "summary": "X0 启动 Y0，并确认启动保持方式。",
        "approaches": [
            {
                "name": "自保持控制",
                "description": "使用常规自保持回路。",
                "generation_contract": {
                    "required_structures": ["self_hold"],
                },
            }
        ],
        "missing_info": [
            {
                "id": "stop_mode",
                "question": "停止方式",
                "options": ["X1 常闭停止", "软件停止"],
                "required": True,
            }
        ],
        "suggested_io": {
            "X": {"X0": "启动按钮", "X1": "停止按钮"},
            "Y": {"Y0": "运行输出"},
        },
    }


def test_requirement_review_is_compact_summary_card():
    _app()
    card = RequirementReviewCard(_analysis(), "做一个启停控制", plc_model="FX3U")
    assert card.objectName() == "SpecSummaryCard"
    assert card.open_button.text() == "打开规格工作台"
    assert "FX3U" in card.summary.text()
    assert card._draft_modified is False


def test_specification_workbench_has_requested_three_column_layout():
    _app()
    dialog = SpecificationWorkbenchDialog(
        _analysis(),
        "做一个启停控制",
        previous_spec=None,
        plc_model="FX3U",
    )
    assert dialog.minimumWidth() >= 1000
    assert dialog.minimumHeight() >= 700
    assert dialog.mode_badge.text() == "首次确认"
    assert dialog.nav.count() == 5
    assert dialog.stack.count() == 5
    assert [dialog.nav.item(i).text() for i in range(dialog.nav.count())] == [
        "概览",
        "实现方案",
        "控制参数",
        "I/O 映射",
        "高级约束",
    ]
    assert dialog.validation_summary.objectName() == "SpecValidationSummary"
    assert dialog.confirm_button.text() == "确认并生成"
    dialog.close()


def test_specification_workbench_marks_previous_spec_as_delta():
    _app()
    previous = {
        "schema_version": 3,
        "plc_model": "FX3U",
        "summary": "旧规格",
        "selected_approach": {"name": "旧方案"},
        "parameters": [],
        "io_table": [],
    }
    dialog = SpecificationWorkbenchDialog(
        _analysis(),
        "修改启停控制",
        previous_spec=previous,
        plc_model="FX3U",
    )
    assert dialog.mode_badge.text() == "差异确认"
    dialog.close()


def test_pyinstaller_specs_use_importable_editor_package():
    assert Path("src/ui/desktop/workbench/editor.py").is_file()
    facade = Path("src/ui/desktop/workbench/__init__.py").read_text(encoding="utf-8")
    assert "from .review import RequirementReviewCard, SpecificationWorkbenchDialog" in facade
    for relative in (
        "packaging/pyinstaller/desktop.spec",
        "packaging/pyinstaller/desktop-win7.spec",
    ):
        text = (Path(relative)).read_text(encoding="utf-8")
        assert 'src/ui/desktop/workbench.py' not in text
        assert "Path(SPECPATH).resolve().parents[1]" in text
