from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker mismatch")
    write(path, text.replace(old, new, 1))


path = "src/runtime_diagnostics.py"
replace_once(
    path,
    """_BOOLEANS = {'stream', 'refusal_present', 'finish_seen', 'at_or_near_end', 'fenced',\n             'bom', 'traceback_truncated', 'content_present', 'prefix_complete_object',\n             'punctuation_only_tail'}\n""",
    """_BOOLEANS = {'stream', 'refusal_present', 'finish_seen', 'at_or_near_end', 'fenced',\n             'bom', 'traceback_truncated', 'content_present', 'prefix_complete_object',\n             'punctuation_only_tail', 'allowed_by_registry'}\n""",
)
replace_once(
    path,
    """        elif key == 'observed_opcode':\n            safe_opcode = _safe_opcode(value)\n            if safe_opcode is not None:\n                result[key] = safe_opcode\n""",
    """        elif key in ('baseline_opcode', 'observed_opcode'):\n            safe_opcode = _safe_opcode(value)\n            if safe_opcode is not None:\n                result[key] = safe_opcode\n""",
)

marker = """def export_diagnostics(state_dir, job):\n    \"\"\"Export one authorized job only; no configuration, events, prompts or sources.\"\"\"\n"""
helper = r'''def _export_repair_baseline_opcode(job):
    snapshot = job.get("snapshot") if isinstance(job, dict) else None
    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):
        return None
    baseline = snapshot.get("repair_baseline")
    if not isinstance(baseline, dict):
        return None
    allowed_ids = {
        int(value) for value in (snapshot.get("allowed_rung_ids") or [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    opcodes = set()
    for rung in baseline.get("rungs") or []:
        if not isinstance(rung, dict):
            continue
        if allowed_ids and rung.get("rung_id") not in allowed_ids:
            continue
        for branch in rung.get("branches") or []:
            if not isinstance(branch, dict):
                continue
            for output in branch.get("outputs") or []:
                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":
                    continue
                opcode = output.get("opcode")
                if isinstance(opcode, str) and opcode.strip():
                    opcodes.add(opcode.strip().upper())
    return next(iter(opcodes)) if len(opcodes) == 1 else None


def _enrich_export_opcode_context(records, job):
    baseline_opcode = _export_repair_baseline_opcode(job)
    snapshot = job.get("snapshot") if isinstance(job, dict) else None
    project = snapshot.get("project") if isinstance(snapshot, dict) else None
    plc_model = project.get("plc_model") if isinstance(project, dict) else None
    allowed = None
    try:
        from instruction_registry import generation_app_instr_mnemonics
        allowed = set(generation_app_instr_mnemonics(plc_model or "FX3U"))
    except Exception:
        allowed = None
    for record in records:
        if not isinstance(record, dict) or record.get("event") != "workflow_exception":
            continue
        for exception in record.get("exceptions") or []:
            if not isinstance(exception, dict):
                continue
            for violation in exception.get("violations") or []:
                if not isinstance(violation, dict):
                    continue
                observed = violation.get("observed_opcode")
                if not isinstance(observed, str) or not observed:
                    continue
                if baseline_opcode is not None:
                    violation["baseline_opcode"] = baseline_opcode
                if allowed is not None:
                    violation["allowed_by_registry"] = observed.upper() in allowed
    return records


def export_diagnostics(state_dir, job):
    """Export one authorized job only; no configuration, events, prompts or sources."""
'''
text = read(path)
if text.count(marker) != 1:
    raise SystemExit("runtime_diagnostics export marker mismatch")
write(path, text.replace(marker, helper, 1))

replace_once(
    path,
    """            else:\n                capture_status = ('export_truncated' if valid_count > _MAX_EXPORT_LINES else\n                                  'captured' if records else 'unreadable')\n    def contains_observed_opcode(value):\n""",
    """            else:\n                capture_status = ('export_truncated' if valid_count > _MAX_EXPORT_LINES else\n                                  'captured' if records else 'unreadable')\n    records = _enrich_export_opcode_context(records, job)\n    def contains_observed_opcode(value):\n""",
)
replace_once(
    path,
    """    def contains_observed_opcode(value):\n        if isinstance(value, dict):\n            return 'observed_opcode' in value or any(contains_observed_opcode(item) for item in value.values())\n        if isinstance(value, (list, tuple)):\n            return any(contains_observed_opcode(item) for item in value)\n        return False\n""",
    """    def contains_observed_opcode(value):\n        if isinstance(value, dict):\n            return ('observed_opcode' in value or 'baseline_opcode' in value\n                    or any(contains_observed_opcode(item) for item in value.values()))\n        if isinstance(value, (list, tuple)):\n            return any(contains_observed_opcode(item) for item in value)\n        return False\n""",
)
replace_once(
    path,
    """             'For APP_INSTR opcode validation failures only, observed_opcode records the exact normalized mnemonic rejected by the validator (max 64 characters); operands, addresses and reply bodies remain excluded.\\n'\n""",
    """             'For APP_INSTR opcode validation failures only, baseline_opcode records one unambiguous opcode from the saved repair baseline, observed_opcode records the exact rejected mnemonic, and allowed_by_registry reports whether the observed mnemonic is generation-allowed for the selected PLC model; operands, addresses and reply bodies remain excluded.\\n'\n""",
)


test_path = "tests/test_runtime_diagnostics.py"
test = read(test_path)
old = '''    job = {"id": "job_test", "status": "failed", "kind": "generation"}\n'''
new = '''    baseline = json.loads(json.dumps(ladder))\n    baseline["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "DADD"\n    job = {\n        "id": "job_test", "status": "failed", "kind": "generation",\n        "snapshot": {\n            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],\n            "project": {"plc_model": "FX3U"},\n        },\n    }\n'''
if test.count(old) != 1:
    raise SystemExit("runtime diagnostics job fixture marker mismatch")
test = test.replace(old, new, 1)
old = '''    assert workflow["exceptions"][0]["violations"][0]["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert "PRIVATE_OPERAND" not in payload\n'''
new = '''    violation = workflow["exceptions"][0]["violations"][0]\n    assert violation["baseline_opcode"] == "DADD"\n    assert violation["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert violation["allowed_by_registry"] is False\n    assert '\"baseline_opcode\": \"DADD\"' in log\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert '\"allowed_by_registry\": false' in log\n    assert "PRIVATE_OPERAND" not in payload\n'''
if test.count(old) != 1:
    raise SystemExit("runtime diagnostics assertion marker mismatch")
write(test_path, test.replace(old, new, 1))
