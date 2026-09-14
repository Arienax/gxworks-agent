from pathlib import Path

path = Path("tests/test_field_patch_repair_protocol.py")
text = path.read_text(encoding="utf-8")
old = '''def test_multiple_or_ambiguous_diagnostics_do_not_expand_to_whole_rung():\n    base = _base()\n    repair = plan(base, [\n'''
new = '''def test_multiple_or_ambiguous_diagnostics_do_not_expand_to_whole_rung():\n    base = _base()\n    base["rungs"][0]["debug_note"] = "x" * 100\n    repair = plan(base, [\n'''
if text.count(old) != 1:
    raise SystemExit(f"fixture marker count {text.count(old)} != 1")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
