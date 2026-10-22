"""PI declarations take part in the program comparison with their position."""
from __future__ import annotations
import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from native_ladder_acceptance import _compare_rows, _program_rows  # noqa: E402

HEADER = [["project"], ["PLC信息:", "FXCPU FX3U/FX3UC"],
          ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"]]


def _write(path, rows):
    buffer = io.StringIO()
    csv.writer(buffer, delimiter="\t", lineterminator="\r\n").writerows(HEADER + rows)
    path.write_text(buffer.getvalue(), encoding="utf-16")
    return path


def _status(tmp_path, left, right):
    first = _write(tmp_path / "left.csv", left)
    second = _write(tmp_path / "right.csv", right)
    return _compare_rows(_program_rows(first), _program_rows(second))["status"]


BASE = [["0", "启动后点亮", "", "", "", "", ""],
        ["0", "", "LD", "M8002", "", "", ""],
        ["1", "", "MOV", "K500", "", "", ""],
        ["2", "", "OUT", "Y0", "", "", ""]]


def _with_pi(row_index, text):
    rows = [list(row) for row in BASE]
    rows[row_index][5] = text
    return rows


def test_identical_declarations_match(tmp_path):
    assert _status(tmp_path, _with_pi(1, "声明A"), _with_pi(1, "声明A")) == "passed"


def test_changed_declaration_text_differs(tmp_path):
    assert _status(tmp_path, _with_pi(1, "声明A"), _with_pi(1, "声明B")) == "failed"


def test_the_same_declaration_on_another_instruction_differs(tmp_path):
    assert _status(tmp_path, _with_pi(1, "声明A"), _with_pi(2, "声明A")) == "failed"


def test_the_same_declaration_on_a_continuation_row_differs(tmp_path):
    rows = [list(row) for row in BASE]
    rows[2][3] = ""            # operand moved to the continuation row below
    rows.insert(2, ["", "", "MOV", "K500", "", "声明A", ""])
    assert _status(tmp_path, rows, _with_pi(2, "声明A")) == "failed"


def test_a_declaration_only_row_is_compared(tmp_path):
    rows = [list(row) for row in BASE]
    rows.insert(2, ["", "", "", "", "", "声明A", ""])
    assert _status(tmp_path, rows, BASE) == "failed"
    assert _status(tmp_path, rows, [list(row) for row in rows]) == "passed"


def test_a_declaration_only_row_moved_differs(tmp_path):
    left = [list(row) for row in BASE]
    left.insert(1, ["", "", "", "", "", "声明A", ""])
    right = [list(row) for row in BASE]
    right.insert(3, ["", "", "", "", "", "声明A", ""])
    assert _status(tmp_path, left, right) == "failed"


def test_a_multiline_declaration_is_read_as_one_cell(tmp_path):
    left = _with_pi(1, "第一行\n第二行")
    right = _with_pi(1, "第一行\n第三行")
    assert _status(tmp_path, left, right) == "failed"
    assert _status(tmp_path, left, _with_pi(1, "第一行\n第二行")) == "passed"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        for name, case in sorted(globals().items()):
            if name.startswith("test_") and callable(case):
                case(Path(directory))
                print("ok", name)
