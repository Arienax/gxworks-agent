import types
from copy import deepcopy

import pytest
from pathlib import Path

import application.model_api as api
import gxworks2.ui_automation as uia_module
from application.execution import GXExecutionCoordinator
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation


def test_analysis_prompt_requires_nonempty_labels_for_normal_io():
    prompt = api.ANALYSIS_SYSTEM_PROMPT
    assert '普通 X/Y/M/D/T/C/S 用“地址:用途”JSON 对象' in prompt
    assert "不得只给地址数组" in prompt
    # Blank labels are enforced deterministically by the normalizer below, not by a frozen sentence.
    assert "suggested_io" in prompt


def test_normal_io_list_is_not_silently_converted_to_blank_labels():
    result = api._normalize_analysis_result(
        {
            "summary": "test",
            "approaches": [],
            "missing_info": [],
            "suggested_io": {
                "X": ["X1", "X3"],
                "Y": {"Y0": "输送带"},
                "special_relays": ["M8000"],
            },
        },
        "FX3U",
        "",
    )
    assert "X" not in result["suggested_io"]
    assert result["suggested_io"]["Y"] == {"Y0": "输送带"}
    assert result["suggested_io"]["special_relays"] == {"M8000": ""}
    assert any(item.get("code") == "io_labels_required" for item in result["format_diagnostics"])


def test_blank_normal_io_label_is_dropped_instead_of_rendering_empty_input():
    result = api._normalize_analysis_result(
        {
            "summary": "test",
            "approaches": [],
            "missing_info": [],
            "suggested_io": {"X": {"X1": ""}, "D": {"D0": "状态寄存器"}},
        },
        "FX3U",
        "",
    )
    assert "X" not in result["suggested_io"]
    assert result["suggested_io"]["D"] == {"D0": "状态寄存器"}
    assert any(item.get("code") == "missing_io_label" for item in result["format_diagnostics"])


def test_user_declared_io_survives_empty_or_conflicting_agent_suggestions():
    user_text = """I/O 约定：
X001：启动按钮
X003：停止按钮
D0：Vision Sensor 分类结果
Y000：Entry conveyor
Y002：Exit conveyor
Y003：Sorter 1 turn
Y004：Sorter 1 belt
Y005：Sorter 2 turn
Y006：Sorter 2 belt
Y007：Sorter 3 turn
Y010：Sorter 3 belt

D0 = 1~3：蓝色
D0 = 4~6：绿色
D0 = 7~9：灰色
"""
    result = api._normalize_analysis_result(
        {
            "summary": "sorting",
            "approaches": [],
            "missing_info": [],
            # The model may omit most rows or even disagree on a label. Explicit
            # user declarations must still reach the review I/O table.
            "suggested_io": {"X": {"X001": "模型错误标签"}},
        },
        "FX3U",
        user_text,
    )
    assert result["suggested_io"]["X"] == {"X1": "启动按钮", "X3": "停止按钮"}
    assert result["suggested_io"]["D"] == {"D0": "Vision Sensor 分类结果"}
    assert result["suggested_io"]["Y"] == {
        "Y0": "Entry conveyor", "Y2": "Exit conveyor", "Y3": "Sorter 1 turn",
        "Y4": "Sorter 1 belt", "Y5": "Sorter 2 turn", "Y6": "Sorter 2 belt",
        "Y7": "Sorter 3 turn", "Y10": "Sorter 3 belt",
    }
    assert result["suggested_io"]["D"]["D0"] != "1~3：蓝色"


class _Edit:
    def __init__(self):
        self.value = ""
        self.entered = False

    def is_enabled(self):
        return True

    def set_edit_text(self, value):
        self.value = value

    def type_keys(self, value):
        self.entered = value == "{ENTER}"


class _Button:
    handle = 0

    def __init__(self):
        self.clicked = False

    def is_enabled(self):
        return True

    def control_id(self):
        return 1

    def click(self):
        self.clicked = True


class _LegacyDialog:
    handle = 0

    def __init__(self):
        self.edit = _Edit()
        self.button = _Button()

    def children(self, class_name=None):
        if class_name == "Edit":
            return [self.edit]
        if class_name == "Button":
            return []
        return []

    def descendants(self, class_name=None):
        if class_name == "Button":
            return [self.button]
        return []


def test_legacy_save_dialog_finds_nested_default_button(tmp_path):
    dialog = _LegacyDialog()
    destination = tmp_path / "comments.csv"
    PywinautoGXWorks2UIAutomation._set_legacy_file_name(dialog, destination)
    assert dialog.edit.value == str(destination.resolve())
    assert dialog.button.clicked is True
    assert dialog.edit.entered is False


def test_operation_result_accepts_stable_editable_main_without_full_timeout(monkeypatch):
    clock = {"value": 0.0}
    monkeypatch.setattr(uia_module.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(
        uia_module.time,
        "sleep",
        lambda seconds: clock.__setitem__("value", clock["value"] + float(seconds)),
    )

    automation = PywinautoGXWorks2UIAutomation(timeout=12.0)
    main = types.SimpleNamespace(exists=lambda: True, is_enabled=lambda: True)
    monkeypatch.setattr(automation, "_main_window", lambda _session: main)
    monkeypatch.setattr(automation, "_read_output_summary", lambda _session: "")
    monkeypatch.setattr(automation, "_native_dialog_handles", lambda _session: [])

    result = automation._wait_operation_result(types.SimpleNamespace(), destination=None)
    assert result["success"] is True
    assert result["message"] == "GX Works2已返回可编辑状态"
    assert clock["value"] < 2.0


def test_web_gx_send_skips_redundant_post_import_roundtrip(tmp_path):
    class Store:
        def __init__(self):
            self.root = tmp_path
            self.version = {
                "id": "v1",
                "revision": 1,
                "artifacts": {"program_csv": "program.csv", "comment_csv": "comments.csv"},
            }
            root = self.version_dir("p1", "v1")
            root.mkdir(parents=True)
            (root / "program.csv").write_text("program", encoding="utf-8")
            (root / "comments.csv").write_text("comments", encoding="utf-8")

        def get_project(self, _project_id):
            return {"id": "p1", "active_version_id": "v1"}

        def get_version(self, _project_id, _version_id):
            return dict(self.version)

        def project_dir(self, project_id):
            return self.root / project_id

        def version_dir(self, project_id, version_id):
            return self.project_dir(project_id) / version_id

    calls = []

    def importer(*args, **kwargs):
        calls.append(kwargs)
        return {"success": True, "message": "ok"}

    service = GXExecutionCoordinator(
        Store(),
        dependencies_factory=lambda _operation: {"importer": importer},
        com_factory=lambda: types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
        resource_lock_path=tmp_path / "desktop.lock",
    )
    try:
        result = service.submit_approved(
            "gx_import",
            {"project_id": "p1", "version_id": "v1"},
            approval_id="a1",
        ).result(5)
    finally:
        service.close()
    assert result["status"] == "imported"
    assert calls and calls[0]["verify_roundtrip"] is False
    assert calls[0]["synchronize_comments"] is True



def test_analysis_model_metadata_does_not_impersonate_core_diagnostics_or_semantics():
    raw = {
        "summary": "输入控制", "approaches": [], "missing_info": [],
        "control_type": ["旧分类"], "flowchart_steps": [{"type": "step", "label": "旧显示"}],
        "format_diagnostics": [{"code": "forged", "message": "模型编造的格式错误"}],
        "execution_semantics": [{"semantic": "LEVEL", "devices": ["X7"], "evidence": "模型编造"}],
        "suggested_io": {"X": {"X8": "非法八进制输入"}},
    }
    from copy import deepcopy
    before = deepcopy(raw)
    result = api._normalize_analysis_result(raw, "FX3U", "X1上升沿触发Y0。")
    assert not {"control_type", "flowchart_steps"} & result.keys()
    assert any(d["code"] == "invalid_io_address" for d in result["format_diagnostics"])
    assert all(d["code"] != "forged" for d in result["format_diagnostics"])
    assert any(s["semantic"] == "RISING_EDGE" for s in result["execution_semantics"])
    assert all(s.get("evidence") != "模型编造" for s in result["execution_semantics"])
    assert raw == before


@pytest.mark.parametrize("layout", ["flat", "grouped", "mixed"])
def test_device_keyed_suggestions_are_io_not_hardware_metadata(layout):
    from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec
    maps = {
        "flat": {"x001": "检测输入", "Y010": "送料输出", "D100": "计数值", "SM400": "运行许可"},
        "grouped": {"X": {"X001": "检测输入"}, "Y": {"Y010": "送料输出"},
                    "D": {"D100": "计数值"}, "special_relays": {"SM400": "运行许可"}},
        "mixed": {"X": {"X001": "检测输入"}, "Y010": "送料输出", "D100": "计数值",
                  "special_relays": {"SM400": "运行许可"}},
    }
    raw = {"summary": "地址到用途", "approaches": [], "missing_info": [],
           "suggested_io": maps[layout]}
    before = deepcopy(raw)
    result = api._normalize_analysis_result(raw, "FX5U", "")
    spec = canonicalize_confirmed_spec(build_review_draft(result))
    assert {r["address"]: r["label"] for r in spec["io_table"]} == {
        "X1": "检测输入", "Y10": "送料输出", "D100": "计数值", "SM400": "运行许可"}
    assert not {"x001", "y010", "d100", "sm400"} & set(result.get("hardware_config", {}))
    assert raw == before


@pytest.mark.parametrize("text,expected", [
    ("X0 为启动按钮，按下时 ON；X1 为停止按钮，按下时 ON；Y0 为运行输出。\n实现停止优先的普通起保停自锁控制。",
     {"X0": "启动按钮", "X1": "停止按钮", "Y0": "运行输出"}),
    ("X005是进料检测；Y012 是排料阀；D100为累计数量。", {"X5": "进料检测", "Y12": "排料阀", "D100": "累计数量"}),
    ("X003: 检测输入（常闭，按下时 OFF）; Y010: 送料输出（工位2）", {"X3": "检测输入", "Y10": "送料输出（工位2）"}),
    ("X005 is Infeed sensor; Y012 is Reject valve", {"X5": "Infeed sensor", "Y12": "Reject valve"}),
    ("X005为3号泵运行反馈", {"X5": "3号泵运行反馈"}),
])
def test_explicit_inline_io_survives_omitted_model_allocation(text, expected):
    from plc.specification.confirmed import build_review_draft
    raw = {"summary": "设备用途", "approaches": [], "missing_info": [], "suggested_io": {}}
    normalized = api._normalize_analysis_result(raw, "FX3U", text)
    draft = build_review_draft(normalized)
    assert {r["address"]: r["label"] for r in draft["io_table"]} == expected
    # Electrical/behavioral text is kept as original intent, not turned into a comment.
    assert draft["intent_context"]["requests"][0]["text"] == text
    assert draft["parameters"] == []


@pytest.mark.parametrize("text", [
    "D0 = 1~3：蓝色；D0 = 4~6：绿色", "MOV K1 D0", "如果 X0 为 ON，则 Y0 输出。",
    "X0 为 ON 时启动 Y0", "X0为ON时启动Y0", "D0 是 7 时输出 Y0", "X0 是哪个输入？", "不要使用 X0 作为启动输入",
    "X0、X1 为两个启动输入", "X0,X1为两个启动输入", "X008：非法 FX3U 地址",
])
def test_inline_io_recovery_does_not_invent_names_from_conditions_or_instructions(text):
    from application.analysis_results import _extract_user_declared_io
    assert _extract_user_declared_io(text, "FX3U") == {}


def test_flat_io_still_validates_cpu_and_does_not_flatten_hardware_objects():
    raw = {"suggested_io": {"X008": "非法八进制", "Y002": "送料阀",
                           "analog_output": {"channel": "CH1", "address": "D100", "note": "量程设置"}}}
    result = api._normalize_analysis_result(raw, "FX3U", "")
    assert result["suggested_io"] == {"Y": {"Y2": "送料阀"}}
    assert any(i["code"] == "invalid_io_address" for i in result["format_diagnostics"])
    assert result["hardware_config"]["analog_output"] == raw["suggested_io"]["analog_output"]
    assert not any("CHANNEL" in str(v) for v in result["suggested_io"].values())


@pytest.mark.parametrize("value", [{"thought": "不要作为注释"}, ["NO X0"], 7, None])
def test_flat_io_adapter_does_not_turn_structured_values_into_device_names(value):
    result = api._normalize_analysis_result({"suggested_io": {"X0": value}}, "FX3U", "")
    assert result["suggested_io"] == {}
