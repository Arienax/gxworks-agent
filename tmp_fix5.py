from pathlib import Path
path = Path("checks/test_ladder_acceptance_comparison.py")
text = path.read_text(encoding="utf-8")
old = '''def test_the_same_declaration_on_a_continuation_row_differs(tmp_path):
    rows = [list(row) for row in BASE]
    rows[2][3] = ""            # operand moved to the continuation row below
    rows.insert(2, ["", "", "MOV", "K500", "", "声明A", ""])
    assert _status(tmp_path, rows, _with_pi(2, "声明A")) == "failed"'''
new = '''def test_the_same_declaration_on_a_continuation_row_differs(tmp_path):
    """Both exports hold the same instruction and operands; only the row differs."""
    left = [list(row) for row in BASE]
    left.insert(3, ["", "", "", "K501", "", "声明A", ""])
    right = [list(row) for row in left]
    right[3][5] = ""
    right[2][5] = "声明A"
    assert _status(tmp_path, left, right) == "failed"'''
assert text.count(old) == 1
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("continuation case replaced")
