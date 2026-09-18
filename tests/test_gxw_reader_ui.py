from pathlib import Path


def test_gxw_reader_ui_entry_is_wired():
    text = Path("src/ui/desktop/main_window.py").read_text(encoding="utf-8")
    assert "QPushButton(tr('解析 GXW'))" in text
    assert 'self.gxw_reader_button.clicked.connect(self._open_gxw_structured_reader)' in text
    assert 'def _open_gxw_structured_reader(self):' in text
    assert 'QFileDialog.getOpenFileName(' in text
    assert 'GXW 结构化梯形图解析（只读）' in text
    assert 'resolver.program_pou_names()' in text
    assert 'parse_structured_pou(' in text
    assert 'output.setReadOnly(True)' in text
    assert '不会修改GXW文件' in text


def test_gxw_reader_entry_does_not_join_gx_sync_busy_buttons():
    text = Path("src/ui/desktop/main_window.py").read_text(encoding="utf-8")
    block = text.split("def _gx_action_buttons", 1)[1].split("    def ", 1)[0]
    assert "gxw_reader_button" not in block
