from pathlib import Path
import re


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# 1) Attach the exact normalized APP_INSTR opcode to validator errors only.
replace_once(
    "src/plc_json_validator.py",
    '''def _fail(path, message):\n    raise PLCJsonValidationError(f"{path}: {message}")\n''',
    '''def _fail(path, message, *, observed_opcode=None):\n    error = PLCJsonValidationError(f"{path}: {message}")\n    if isinstance(observed_opcode, str) and observed_opcode:\n        error.observed_opcode = observed_opcode\n    raise error\n''',
)

path = "src/plc_json_validator.py"
text = read(path)
marker = '        opcode = str(opcode).strip().upper()\n'
start = text.index(marker)
end_marker = '\n        model = normalize_plc_model(plc_model)\n        _validate_shift_operands(opcode, operands, path, plc_model)'
end = text.index(end_marker, start)
block = text[start:end]
if "def fail_opcode" in block:
    raise SystemExit("plc_json_validator.py: fail_opcode already staged")
block = block.replace(
    marker,
    marker + '''        def fail_opcode(message):\n            _fail(f"{path}.opcode", message, observed_opcode=opcode)\n''',
    1,
)
block, multiline_count = re.subn(
    r'_fail\(\n\s+f"\{path\}\.opcode",\n',
    'fail_opcode(\n',
    block,
)
block, inline_count = re.subn(
    r'_fail\(f"\{path\}\.opcode",\s*',
    'fail_opcode(',
    block,
)
if multiline_count + inline_count < 6:
    raise SystemExit(
        f"plc_json_validator.py: expected >=6 opcode failure sites, got {multiline_count + inline_count}"
    )
write(path, text[:start] + block + text[end:])


# 2) Preserve the opcode through GenerationValidationError's private diagnostics.
replace_once(
    "src/application/generation_repair.py",
    '''    return {"path": "content$" + ("." + ".".join(safe) if safe else ""), "reason": reason}\n''',
    '''    row = {"path": "content$" + ("." + ".".join(safe) if safe else ""), "reason": reason}\n    observed_opcode = getattr(error, "observed_opcode", None)\n    if isinstance(observed_opcode, str) and observed_opcode:\n        row["observed_opcode"] = observed_opcode\n    return row\n''',
)


# 3) Whitelist only one bounded engineering token in runtime diagnostics.
replace_once(
    "src/runtime_diagnostics.py",
    '''def _number(value):\n    return max(0, min(value, 10**12)) if type(value) is int else None\n\n\ndef _safe_fields(fields):\n''',
    '''def _number(value):\n    return max(0, min(value, 10**12)) if type(value) is int else None\n\n\ndef _safe_opcode(value):\n    if not isinstance(value, str):\n        return None\n    token = value.strip().upper()\n    if not re.fullmatch(r"[A-Z0-9_.$@+\\-]{1,64}", token):\n        return "redacted"\n    lowered = token.lower()\n    if any(marker in lowered for marker in ("sk-", "bearer", "secret", "private", "api_key", "token", "password")):\n        return "redacted"\n    return token\n\n\ndef _safe_fields(fields):\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''        elif key in _IDS:\n            result[key] = _identifier(value)\n''',
    '''        elif key == 'observed_opcode':\n            safe_opcode = _safe_opcode(value)\n            if safe_opcode is not None:\n                result[key] = safe_opcode\n        elif key in _IDS:\n            result[key] = _identifier(value)\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''        item = {'error_type': type(error).__name__, 'code': getattr(error, 'code', ''),\n                'status_code': getattr(error, 'status_code', None), 'frames': frames[-32:],\n                'traceback_truncated': len(frames) > 32}\n''',
    '''        item = {'error_type': type(error).__name__, 'code': getattr(error, 'code', ''),\n                'status_code': getattr(error, 'status_code', None), 'frames': frames[-32:],\n                'traceback_truncated': len(frames) > 32}\n        observed_opcode = getattr(error, 'observed_opcode', None)\n        if observed_opcode is not None:\n            item['observed_opcode'] = observed_opcode\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''    meta = {'schema_version':_SCHEMA, 'job_id':job_id, 'capture_status':capture_status,\n            'job':_safe_fields({key: job.get(key) for key in ('kind', 'status', 'project_id', 'version_id')}),\n            'event_count':len(records), 'content_included':False, 'keys_included':False,\n            'captured_after_upgrade_only':True}\n''',
    '''    def contains_observed_opcode(value):\n        if isinstance(value, dict):\n            return 'observed_opcode' in value or any(contains_observed_opcode(item) for item in value.values())\n        if isinstance(value, (list, tuple)):\n            return any(contains_observed_opcode(item) for item in value)\n        return False\n\n    meta = {'schema_version':_SCHEMA, 'job_id':job_id, 'capture_status':capture_status,\n            'job':_safe_fields({key: job.get(key) for key in ('kind', 'status', 'project_id', 'version_id')}),\n            'event_count':len(records), 'content_included':False, 'keys_included':False,\n            'validation_values_included':any(contains_observed_opcode(item) for item in records),\n            'captured_after_upgrade_only':True}\n''',
)
replace_once(
    "src/runtime_diagnostics.py",
    '''             'workflow_exception includes source file/function/line plus validation attempts, stop_reason and safe violation paths when available.\\n'\n''',
    '''             'workflow_exception includes source file/function/line plus validation attempts, stop_reason and safe violation paths when available.\\n'\n             'For APP_INSTR opcode validation failures only, observed_opcode records the exact normalized mnemonic rejected by the validator (max 64 characters); operands, addresses and reply bodies remain excluded.\\n'\n''',
)


# 4) Regression: real validator failure -> private diagnostics -> exported ZIP.
test_path = "tests/test_runtime_diagnostics.py"
test_text = read(test_path)
replace_import = '''from application.generation_repair import GenerationValidationError\nfrom model_provider import (OpenAICompatibleProvider, ModelRequest, SystemMessage, UserMessage,\n'''
replace_with = '''from application.generation_repair import GenerationValidationError\nfrom plc_json_validator import PLCJsonValidationError, validate_ladder_candidate_structure\nfrom model_provider import (OpenAICompatibleProvider, ModelRequest, SystemMessage, UserMessage,\n'''
if test_text.count(replace_import) != 1:
    raise SystemExit("test_runtime_diagnostics.py: import marker mismatch")
test_text = test_text.replace(replace_import, replace_with, 1)

anchor = '''def test_generation_validation_exception_records_attempts_stop_and_paths(tmp_path):\n'''
if anchor not in test_text:
    raise SystemExit("test_runtime_diagnostics.py: generation validation anchor missing")
addition = r'''
def test_invalid_app_instr_opcode_is_exported_as_bounded_observed_value(tmp_path):
    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR",
                    "opcode": "NOT_A_REAL_OPCODE",
                    "operands": ["PRIVATE_OPERAND"],
                    "label": None,
                }],
            }],
        }],
    }
    with pytest.raises(PLCJsonValidationError) as caught:
        validate_ladder_candidate_structure(
            ladder, plc_model="FX3U", require_catalogued_instructions=True,
        )
    assert caught.value.observed_opcode == "NOT_A_REAL_OPCODE"
    failure = GenerationValidationError(
        [caught.value], attempts=0, max_attempts=0,
        language="zh-CN", stop_reason="final_validation",
    )
    with d.diagnostic_scope(tmp_path, "job_test"):
        d.exception_record(failure)

    job = {"id": "job_test", "status": "failed", "kind": "generation"}
    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:
        summary = json.loads(archive.read("summary.json"))
        log = archive.read("diagnostics.jsonl").decode()
        payload = archive.read("summary.json").decode() + log + archive.read("README.txt").decode()
    assert summary["validation_values_included"] is True
    workflow = summary["failure_analysis"]["workflow_exception"]
    assert workflow["exceptions"][0]["violations"][0]["observed_opcode"] == "NOT_A_REAL_OPCODE"
    assert workflow["exceptions"][1]["observed_opcode"] == "NOT_A_REAL_OPCODE"
    assert '"observed_opcode": "NOT_A_REAL_OPCODE"' in log
    assert "PRIVATE_OPERAND" not in payload


def test_observed_opcode_diagnostic_redacts_secret_like_tokens(tmp_path):
    error = PLCJsonValidationError("$.rungs[0].branches[0].outputs[0].opcode: invalid")
    error.observed_opcode = "SK-PRIVATE_TOKEN"
    with d.diagnostic_scope(tmp_path, "job_test"):
        d.exception_record(error)
    item = next(x for x in rows(tmp_path) if x["event"] == "workflow_exception")["exceptions"][0]
    assert item["observed_opcode"] == "redacted"
    assert "PRIVATE_TOKEN" not in json.dumps(rows(tmp_path))


'''
test_text = test_text.replace(anchor, addition + anchor, 1)
write(test_path, test_text)
