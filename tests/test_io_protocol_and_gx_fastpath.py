import types
from pathlib import Path

import application.model_api as api
import gxworks2.ui_automation as uia_module
from application.execution import GXExecutionCoordinator
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation


def test_analysis_prompt_requires_nonempty_labels_for_normal_io():
    prompt = api.ANALYSIS_SYSTEM_PROMPT
    assert "普通类别 X/Y/M/D/T/C/S 必须使用 JSON 对象" in prompt
    assert "不得只返回地址数组" in prompt
    assert "每个地址都必须有非空说明" in prompt


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
