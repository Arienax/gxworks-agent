from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


# 1) Validator records whether the rejected APP_INSTR opcode is actually in the
# generation registry for this PLC model. This is diagnostic metadata only.
replace_once(
    "src/plc_json_validator.py",
    '''def _fail(path, message, *, observed_opcode=None):\n    error = PLCJsonValidationError(f"{path}: {message}")\n    if isinstance(observed_opcode, str) and observed_opcode:\n        error.observed_opcode = observed_opcode\n    raise error\n''',
    '''def _fail(path, message, *, observed_opcode=None, allowed_by_registry=None):\n    error = PLCJsonValidationError(f"{path}: {message}")\n    if isinstance(observed_opcode, str) and observed_opcode:\n        error.observed_opcode = observed_opcode\n    if type(allowed_by_registry) is bool:\n        error.allowed_by_registry = allowed_by_registry\n    raise error\n''',
)
replace_once(
    "src/plc_json_validator.py",
    '''        def fail_opcode(message):\n            _fail(f"{path}.opcode", message, observed_opcode=opcode)\n''',
    '''        def fail_opcode(message):\n            _fail(\n                f"{path}.opcode", message, observed_opcode=opcode,\n                allowed_by_registry=opcode in generation_app_instr_mnemonics(plc_model),\n            )\n''',
)


# 2) Preserve baseline + registry diagnostic metadata through GenerationValidationError.
replace_once(
    "src/application/generation_repair.py",
    '''    observed_opcode = getattr(error, "observed_opcode", None)\n    if isinstance(observed_opcode, str) and observed_opcode:\n        row["observed_opcode"] = observed_opcode\n    return row\n''',
    '''    baseline_opcode = getattr(error, "baseline_opcode", None)\n    if isinstance(baseline_opcode, str) and baseline_opcode:\n        row["baseline_opcode"] = baseline_opcode\n    observed_opcode = getattr(error, "observed_opcode", None)\n    if isinstance(observed_opcode, str) and observed_opcode:\n        row["observed_opcode"] = observed_opcode\n    allowed_by_registry = getattr(error, "allowed_by_registry", None)\n    if type(allowed_by_registry) is bool:\n        row["allowed_by_registry"] = allowed_by_registry\n    return row\n''',
)


# 3) Precisely map a failing repair output back to its immutable baseline output.
generation_path = "src/application/generation.py"
generation = read(generation_path)
marker = '''\n\n\nclass GenerationWorkflow:\n'''
if marker not in generation:
    raise SystemExit("generation.py: GenerationWorkflow marker missing")
helper = r'''


def _enrich_repair_opcode_diagnostic(error, candidate_text, baseline, allowed_rung_ids):
    """Attach the baseline APP_INSTR opcode corresponding to one rejected repair output.

    This is diagnostics-only. It never changes validation, repair scope, candidate
    bytes, or retry behavior. Ambiguous mappings are intentionally omitted.
    """
    observed = getattr(error, "observed_opcode", None)
    if not isinstance(observed, str) or not observed or not isinstance(baseline, dict):
        return error

    import json
    import re

    match = re.match(
        r"^\$\.rungs\[(\d+)\]\.branches\[(\d+)\]\.outputs\[(\d+)\]\.opcode\s*:",
        str(error),
    )
    if not match:
        return error
    rung_index, branch_index, output_index = map(int, match.groups())

    candidate_rung_id = None
    candidate_branch_id = None
    try:
        candidate = json.loads(str(candidate_text or ""))
    except (TypeError, ValueError, RecursionError):
        candidate = None
    if isinstance(candidate, dict):
        candidate_rungs = candidate.get("rungs")
        if isinstance(candidate_rungs, list) and rung_index < len(candidate_rungs):
            candidate_rung = candidate_rungs[rung_index]
            if isinstance(candidate_rung, dict):
                value = candidate_rung.get("rung_id")
                if isinstance(value, int) and not isinstance(value, bool):
                    candidate_rung_id = value
                branches = candidate_rung.get("branches")
                if isinstance(branches, list) and branch_index < len(branches):
                    candidate_branch = branches[branch_index]
                    if isinstance(candidate_branch, dict):
                        value = candidate_branch.get("branch_id")
                        if isinstance(value, int) and not isinstance(value, bool):
                            candidate_branch_id = value

    allowed = {
        int(value) for value in (allowed_rung_ids or [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if candidate_rung_id is None and len(allowed) == 1:
        candidate_rung_id = next(iter(allowed))

    baseline_rungs = baseline.get("rungs")
    if not isinstance(baseline_rungs, list):
        return error
    baseline_rung = next((
        rung for rung in baseline_rungs
        if isinstance(rung, dict) and rung.get("rung_id") == candidate_rung_id
    ), None)
    if baseline_rung is None:
        return error

    baseline_branches = baseline_rung.get("branches")
    if not isinstance(baseline_branches, list):
        return error
    baseline_branch = None
    if candidate_branch_id is not None:
        baseline_branch = next((
            branch for branch in baseline_branches
            if isinstance(branch, dict) and branch.get("branch_id") == candidate_branch_id
        ), None)
    if baseline_branch is None and branch_index < len(baseline_branches):
        candidate = baseline_branches[branch_index]
        if isinstance(candidate, dict):
            baseline_branch = candidate

    if baseline_branch is not None:
        outputs = baseline_branch.get("outputs")
        if isinstance(outputs, list) and output_index < len(outputs):
            output = outputs[output_index]
            if isinstance(output, dict) and output.get("type") == "APP_INSTR":
                opcode = output.get("opcode")
                if isinstance(opcode, str) and opcode.strip():
                    error.baseline_opcode = opcode.strip().upper()
                    return error

    # Structural repairs can legitimately move branches. Fall back only when the
    # baseline rung contains exactly one APP_INSTR, so the mapping is unambiguous.
    opcodes = []
    for branch in baseline_branches:
        if not isinstance(branch, dict):
            continue
        for output in branch.get("outputs") or []:
            if isinstance(output, dict) and output.get("type") == "APP_INSTR":
                opcode = output.get("opcode")
                if isinstance(opcode, str) and opcode.strip():
                    opcodes.append(opcode.strip().upper())
    if len(opcodes) == 1:
        error.baseline_opcode = opcodes[0]
    return error
'''
generation = generation.replace(marker, helper + marker, 1)
write(generation_path, generation)

replace_once(
    generation_path,
    '''            try:\n                parsed_json = parse_candidate(json_str)\n            except validation_errors as error:\n                try:\n                    parsed_json = cascade_format_repair(error)\n                except validation_errors as followup_error:\n                    persist_repair_candidate()\n                    raise GenerationValidationError([followup_error], attempts=1, max_attempts=1, language=self.response_language, stop_reason="explicit_repair_followup_failed") from followup_error\n                if parsed_json is None:\n                    persist_repair_candidate()\n                    raise GenerationValidationError([error], attempts=0, max_attempts=0, language=self.response_language, stop_reason="final_validation") from error\n''',
    '''            try:\n                parsed_json = parse_candidate(json_str)\n            except validation_errors as error:\n                if self.repair_mode:\n                    _enrich_repair_opcode_diagnostic(\n                        error, json_str, self.previous_json, self.allowed_rung_ids\n                    )\n                try:\n                    parsed_json = cascade_format_repair(error)\n                except validation_errors as followup_error:\n                    if self.repair_mode:\n                        _enrich_repair_opcode_diagnostic(\n                            followup_error, json_str, self.previous_json, self.allowed_rung_ids\n                        )\n                    persist_repair_candidate()\n                    raise GenerationValidationError([followup_error], attempts=1, max_attempts=1, language=self.response_language, stop_reason="explicit_repair_followup_failed") from followup_error\n                if parsed_json is None:\n                    persist_repair_candidate()\n                    raise GenerationValidationError([error], attempts=0, max_attempts=0, language=self.response_language, stop_reason="final_validation") from error\n''',
)
replace_once(
    generation_path,
    '''        except (PLCJsonValidationError, PLCIRValidationError) as error:\n            try:\n                persist_repair_candidate()\n            except (NameError, OSError):\n                pass\n            raise GenerationValidationError(\n''',
    '''        except (PLCJsonValidationError, PLCIRValidationError) as error:\n            if self.repair_mode:\n                try:\n                    _enrich_repair_opcode_diagnostic(\n                        error, locals().get("json_str", ""), self.previous_json, self.allowed_rung_ids\n                    )\n                except Exception:\n                    pass  # Diagnostics must never replace the original validation failure.\n            try:\n                persist_repair_candidate()\n            except (NameError, OSError):\n                pass\n            raise GenerationValidationError(\n''',
)


# 4) Runtime export whitelists only these bounded fields.
replace_once(
    "src/runtime_diagnostics.py",
    '''_BOOLEANS = {'stream', 'refusal_present', 'finish_seen', 'at_or_near_end', 'fenced',\n             'bom', 'traceback_truncated', 'content_present', 'prefix_complete_object',\n             'punctuation_only_tail'}\n''',
    '''_BOOLEANS = {'stream', 'refusal_present', 'finish_seen', 'at_or_near_end', 'fenced',\n             'bom', 'traceback_truncated', 'content_present', 'prefix_complete_object',\n             'punctuation_only_tail', 'allowed_by_registry'}\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''        elif key == 'observed_opcode':\n            safe_opcode = _safe_opcode(value)\n            if safe_opcode is not None:\n                result[key] = safe_opcode\n''',
    '''        elif key in ('baseline_opcode', 'observed_opcode'):\n            safe_opcode = _safe_opcode(value)\n            if safe_opcode is not None:\n                result[key] = safe_opcode\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''    def contains_observed_opcode(value):\n        if isinstance(value, dict):\n            return 'observed_opcode' in value or any(contains_observed_opcode(item) for item in value.values())\n        if isinstance(value, (list, tuple)):\n            return any(contains_observed_opcode(item) for item in value)\n        return False\n\n    meta = {'schema_version':_SCHEMA, 'job_id':job_id, 'capture_status':capture_status,\n            'job':_safe_fields({key: job.get(key) for key in ('kind', 'status', 'project_id', 'version_id')}),\n            'event_count':len(records), 'content_included':False, 'keys_included':False,\n            'validation_values_included':any(contains_observed_opcode(item) for item in records),\n''',
    '''    def contains_validation_value(value):\n        if isinstance(value, dict):\n            return (\n                'baseline_opcode' in value or 'observed_opcode' in value\n                or any(contains_validation_value(item) for item in value.values())\n            )\n        if isinstance(value, (list, tuple)):\n            return any(contains_validation_value(item) for item in value)\n        return False\n\n    meta = {'schema_version':_SCHEMA, 'job_id':job_id, 'capture_status':capture_status,\n            'job':_safe_fields({key: job.get(key) for key in ('kind', 'status', 'project_id', 'version_id')}),\n            'event_count':len(records), 'content_included':False, 'keys_included':False,\n            'validation_values_included':any(contains_validation_value(item) for item in records),\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''             'For APP_INSTR opcode validation failures only, observed_opcode records the exact normalized mnemonic rejected by the validator (max 64 characters); operands, addresses and reply bodies remain excluded.\\n'\n''',
    '''             'For APP_INSTR opcode validation failures only, baseline_opcode records the reliably matched immutable repair baseline mnemonic, observed_opcode records the exact normalized rejected mnemonic, and allowed_by_registry reports whether that observed mnemonic is generation-allowed for the selected PLC model; operands, addresses and reply bodies remain excluded.\\n'\n''',
)


# 5) Regression: DDADDP-style repair error carries all three values through export.
test_path = "tests/test_runtime_diagnostics.py"
test = read(test_path)
replace_once(
    test_path,
    '''from application.generation_repair import GenerationValidationError\n''',
    '''from application.generation import _enrich_repair_opcode_diagnostic\nfrom application.generation_repair import GenerationValidationError\n''',
)
old_test = '''    assert caught.value.observed_opcode == "NOT_A_REAL_OPCODE"\n    failure = GenerationValidationError(\n        [caught.value], attempts=0, max_attempts=0,\n        language="zh-CN", stop_reason="final_validation",\n    )\n'''
new_test = '''    assert caught.value.observed_opcode == "NOT_A_REAL_OPCODE"\n    assert caught.value.allowed_by_registry is False\n    baseline = json.loads(json.dumps(ladder))\n    baseline["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "DADD"\n    partial = {\n        "mode": "partial", "device_comments": {},\n        "rungs": ladder["rungs"], "delete_rung_ids": [],\n    }\n    _enrich_repair_opcode_diagnostic(\n        caught.value, json.dumps(partial), baseline, {1}\n    )\n    assert caught.value.baseline_opcode == "DADD"\n    failure = GenerationValidationError(\n        [caught.value], attempts=0, max_attempts=0,\n        language="zh-CN", stop_reason="final_validation",\n    )\n'''
if test.count(old_test) != 1:
    raise SystemExit("test_runtime_diagnostics.py: opcode assertion block mismatch")
test = test.replace(old_test, new_test, 1)
old_asserts = '''    assert workflow["exceptions"][0]["violations"][0]["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert "PRIVATE_OPERAND" not in payload\n'''
new_asserts = '''    violation = workflow["exceptions"][0]["violations"][0]\n    assert violation["baseline_opcode"] == "DADD"\n    assert violation["observed_opcode"] == "NOT_A_REAL_OPCODE"\n    assert violation["allowed_by_registry"] is False\n    assert '\"baseline_opcode\": \"DADD\"' in log\n    assert '\"observed_opcode\": \"NOT_A_REAL_OPCODE\"' in log\n    assert '\"allowed_by_registry\": false' in log\n    assert "PRIVATE_OPERAND" not in payload\n'''
if test.count(old_asserts) != 1:
    raise SystemExit("test_runtime_diagnostics.py: export assertion block mismatch")
test = test.replace(old_asserts, new_asserts, 1)
old_secret = '''    error.observed_opcode = "SK-PRIVATE_TOKEN"\n    with d.diagnostic_scope(tmp_path, "job_test"):\n        d.exception_record(error)\n    item = next(x for x in rows(tmp_path) if x["event"] == "workflow_exception")["exceptions"][0]\n    assert item["observed_opcode"] == "redacted"\n'''
new_secret = '''    error.baseline_opcode = "PRIVATE_BASELINE"\n    error.observed_opcode = "SK-PRIVATE_TOKEN"\n    error.allowed_by_registry = False\n    with d.diagnostic_scope(tmp_path, "job_test"):\n        d.exception_record(error)\n    item = next(x for x in rows(tmp_path) if x["event"] == "workflow_exception")["exceptions"][0]\n    assert item["baseline_opcode"] == "redacted"\n    assert item["observed_opcode"] == "redacted"\n    assert item["allowed_by_registry"] is False\n'''
if test.count(old_secret) != 1:
    raise SystemExit("test_runtime_diagnostics.py: secret redaction block mismatch")
write(test_path, test.replace(old_secret, new_secret, 1))
