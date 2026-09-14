from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker count {text.count(old)} != 1")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/application/field_repair.py",
    '''        observed = str(row.get("observed_opcode") or current or "").strip().upper()\n        operands = parent.get("operands") if isinstance(parent.get("operands"), list) else []\n        candidates, basis, deterministic = _opcode_repair_candidates(observed, operands, plc_model)\n''',
    '''        observed_evidence = row.get("observed_opcode")\n        observed = str(observed_evidence or current or "").strip().upper()\n        # A generic error path is not evidence that a catalogued opcode is a\n        # typo. Only a validator-proven rejected opcode, or an actually unknown\n        # current token, may enter mnemonic candidate generation.\n        if not observed_evidence and DEFAULT_INSTRUCTION_REGISTRY.resolve(observed) is not None:\n            return _blocked(saved, row, segments, current)["target"]\n        operands = parent.get("operands") if isinstance(parent.get("operands"), list) else []\n        candidates, basis, deterministic = _opcode_repair_candidates(observed, operands, plc_model)\n''',
)

replace_once(
    "tests/test_field_patch_repair_protocol.py",
    '''def test_multiple_or_ambiguous_diagnostics_do_not_expand_to_whole_rung():\n    base = _base()\n    repair = plan(base, [\n        {"path": "content$.rungs.0.debug_note", "reason": "field_too_long"},\n        {"path": "content$.rungs.0.branches.0.outputs.0.opcode", "reason": "invalid_ladder_structure"},\n    ])\n    assert repair["target"]["strategy"] == "blocked"\n''',
    '''def test_multiple_or_ambiguous_diagnostics_do_not_expand_to_whole_rung():\n    base = _base()\n    repair = plan(base, [\n        {"path": "content$.rungs.0.debug_note", "reason": "field_too_long"},\n        {"path": "content$.rungs.0.branches.0.outputs.0.opcode", "reason": "invalid_ladder_structure"},\n    ])\n    assert repair["mode"] == "field_patch"\n    assert [target["strategy"] for target in repair["targets"]] == ["deterministic", "blocked"]\n    assert all(target["path"].startswith("/rungs/0/") for target in repair["targets"])\n''',
)
