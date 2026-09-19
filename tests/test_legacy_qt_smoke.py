"""Small smoke checks for the frozen legacy Qt surface.

These protect existing entry points only; new feature parity belongs to Web.
"""
from pathlib import Path


def _main_window_source():
    return Path("src/ui/desktop/main_window.py").read_text(encoding="utf-8")


def test_contract_mismatch_repair_entry_remains_available():
    source = _main_window_source()
    assert "contract_repair_button" in source
    assert "修复方案约束" in source
    assert "def _repair_current_contract_mismatch(self):" in source
    assert "原始版本和 CSV 保持不变" in source


def test_gxw_reader_ui_entry_is_wired_read_only():
    text = _main_window_source()
    assert "QPushButton(tr('解析 GXW'))" in text
    assert "self.gxw_reader_button.clicked.connect(self._open_gxw_structured_reader)" in text
    assert "def _open_gxw_structured_reader(self):" in text
    assert "QFileDialog.getOpenFileName(" in text
    assert "GXW 结构化梯形图解析（只读）" in text
    assert "resolver.program_pou_names()" in text
    assert "parse_structured_pou(" in text
    assert "output.setReadOnly(True)" in text
    assert "不会修改GXW文件" in text


def test_gxw_reader_entry_does_not_join_gx_sync_busy_buttons():
    text = _main_window_source()
    block = text.split("def _gx_action_buttons", 1)[1].split("    def ", 1)[0]
    assert "gxw_reader_button" not in block
