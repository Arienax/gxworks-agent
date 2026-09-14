from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    write(path, text.replace(old, new, 1))


runtime = "src/runtime_diagnostics.py"
replace_once(
    runtime,
    '''def _export_repair_baseline_opcode(snapshot):\n    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):\n        return None\n    baseline = snapshot.get("repair_baseline")\n    if not isinstance(baseline, dict):\n        return None\n    allowed_ids = {\n        int(value) for value in (snapshot.get("allowed_rung_ids") or [])\n        if isinstance(value, int) and not isinstance(value, bool)\n    }\n    opcodes = set()\n    for rung in baseline.get("rungs") or []:\n        if not isinstance(rung, dict):\n            continue\n        if allowed_ids and rung.get("rung_id") not in allowed_ids:\n            continue\n        for branch in rung.get("branches") or []:\n            if not isinstance(branch, dict):\n                continue\n            for output in branch.get("outputs") or []:\n                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":\n                    continue\n                opcode = output.get("opcode")\n                if isinstance(opcode, str) and opcode.strip():\n                    opcodes.add(opcode.strip().upper())\n    return next(iter(opcodes)) if len(opcodes) == 1 else None\n''',
    '''def _export_repair_baseline_app_instrs(snapshot):\n    """Return bounded APP_INSTR identity metadata from the repair baseline only."""\n    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):\n        return []\n    baseline = snapshot.get("repair_baseline")\n    if not isinstance(baseline, dict):\n        return []\n    allowed_ids = {\n        int(value) for value in (snapshot.get("allowed_rung_ids") or [])\n        if isinstance(value, int) and not isinstance(value, bool)\n    }\n    entries = []\n    for rung in baseline.get("rungs") or []:\n        if not isinstance(rung, dict):\n            continue\n        rung_id = rung.get("rung_id")\n        if allowed_ids and rung_id not in allowed_ids:\n            continue\n        for branch in rung.get("branches") or []:\n            if not isinstance(branch, dict):\n                continue\n            branch_id = branch.get("branch_id")\n            for output_index, output in enumerate(branch.get("outputs") or []):\n                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":\n                    continue\n                opcode = _safe_opcode(output.get("opcode"))\n                if opcode is None:\n                    continue\n                entry = {"output_index": output_index, "opcode": opcode}\n                if isinstance(rung_id, int) and not isinstance(rung_id, bool):\n                    entry["rung_id"] = rung_id\n                if isinstance(branch_id, int) and not isinstance(branch_id, bool):\n                    entry["branch_id"] = branch_id\n                entries.append(entry)\n                if len(entries) >= 32:\n                    return entries\n    return entries\n''',
)
replace_once(
    runtime,
    '''def _enrich_export_opcode_context(records, state_dir, job):\n    snapshot = _export_job_snapshot(state_dir, job)\n    baseline_opcode = _export_repair_baseline_opcode(snapshot)\n    project = snapshot.get("project") if isinstance(snapshot, dict) else None\n''',
    '''def _enrich_export_opcode_context(records, state_dir, job):\n    snapshot = _export_job_snapshot(state_dir, job)\n    baseline_app_instrs = _export_repair_baseline_app_instrs(snapshot)\n    baseline_opcode = (\n        baseline_app_instrs[0].get("opcode")\n        if len(baseline_app_instrs) == 1 else None\n    )\n    project = snapshot.get("project") if isinstance(snapshot, dict) else None\n''',
)
replace_once(
    runtime,
    '''                if baseline_opcode is not None:\n                    violation["baseline_opcode"] = baseline_opcode\n                if allowed is not None:\n                    violation["allowed_by_registry"] = observed.upper() in allowed\n''',
    '''                if baseline_app_instrs:\n                    violation["baseline_app_instrs"] = [dict(item) for item in baseline_app_instrs]\n                if baseline_opcode is not None:\n                    violation["baseline_opcode"] = baseline_opcode\n                if allowed is not None:\n                    violation["allowed_by_registry"] = observed.upper() in allowed\n''',
)
replace_once(
    runtime,
    '''            return ('observed_opcode' in value or 'baseline_opcode' in value\n                    or any(contains_observed_opcode(item) for item in value.values()))\n''',
    '''            return ('observed_opcode' in value or 'baseline_opcode' in value\n                    or 'baseline_app_instrs' in value\n                    or any(contains_observed_opcode(item) for item in value.values()))\n''',
)
replace_once(
    runtime,
    '''             'For APP_INSTR opcode validation failures only, baseline_opcode records one unambiguous opcode from the saved repair baseline, observed_opcode records the exact rejected mnemonic, and allowed_by_registry reports whether the observed mnemonic is generation-allowed for the selected PLC model; operands, addresses and reply bodies remain excluded.\\n'\n''',
    '''             'For APP_INSTR opcode validation failures only, baseline_app_instrs records up to 32 APP_INSTR identities from the allowed repair baseline scope (rung_id, branch_id, output_index, opcode only); baseline_opcode remains only when that list has one entry; observed_opcode records the exact rejected mnemonic; allowed_by_registry reports whether it is generation-allowed. Operands, addresses and reply bodies remain excluded.\\n'\n''',
)


test_path = "tests/test_runtime_diagnostics.py"
test = read(test_path)
old = '''    baseline = json.loads(json.dumps(ladder))\n    baseline["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "DADD"\n    job = {\n        "id": "job_test", "status": "failed", "kind": "generation",\n        "snapshot": {\n            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],\n            "project": {"plc_model": "FX3U"},\n        },\n    }\n    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:\n'''
new = '''    baseline = json.loads(json.dumps(ladder))\n    outputs = baseline["rungs"][0]["branches"][0]["outputs"]\n    outputs[0]["opcode"] = "DADD"\n    outputs.append({\n        "type": "APP_INSTR", "opcode": "MOV",\n        "operands": ["PRIVATE_SECOND_OPERAND"], "label": None,\n    })\n    private_record = {\n        "id": "job_test",\n        "snapshot": {\n            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],\n            "project": {"plc_model": "FX3U"},\n        },\n    }\n    jobs_dir = tmp_path / "jobs"\n    jobs_dir.mkdir()\n    (jobs_dir / "job_test.json").write_text(json.dumps(private_record), encoding="utf-8")\n    # Match the real HTTP path: the exporter receives a public job without snapshot.\n    job = {"id": "job_test", "status": "failed", "kind": "generation"}\n    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:\n'''
if test.count(old) != 1:
    raise SystemExit("tests/test_runtime_diagnostics.py: baseline fixture marker mismatch")
test = test.replace(old, new, 1)
old = '''    assert violation["baseline_opcode"] == "DADD"\n    assert violation["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert violation["allowed_by_registry"] is False\n    assert '\"baseline_opcode\": \"DADD\"' in log\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert '\"allowed_by_registry\": false' in log\n    assert "PRIVATE_OPERAND" not in payload\n'''
new = '''    assert violation["baseline_app_instrs"] == [\n        {"output_index": 0, "opcode": "DADD", "rung_id": 1, "branch_id": 1},\n        {"output_index": 1, "opcode": "MOV", "rung_id": 1, "branch_id": 1},\n    ]\n    assert "baseline_opcode" not in violation\n    assert violation["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert violation["allowed_by_registry"] is False\n    assert '\"baseline_app_instrs\"' in log\n    assert '\"opcode\": \"DADD\"' in log\n    assert '\"opcode\": \"MOV\"' in log\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert '\"allowed_by_registry\": false' in log\n    assert "PRIVATE_OPERAND" not in payload\n    assert "PRIVATE_SECOND_OPERAND" not in payload\n'''
if test.count(old) != 1:
    raise SystemExit("tests/test_runtime_diagnostics.py: assertion marker mismatch")
write(test_path, test.replace(old, new, 1))
