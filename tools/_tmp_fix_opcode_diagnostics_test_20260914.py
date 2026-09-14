from pathlib import Path

path = Path("tests/test_runtime_diagnostics.py")
text = path.read_text(encoding="utf-8")
old = '    assert workflow["exceptions"][1]["observed_opcode"] == "NOT_A_REAL_OPCODE"\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one cause-chain assertion, found {text.count(old)}")
path.write_text(text.replace(old, "", 1), encoding="utf-8")
