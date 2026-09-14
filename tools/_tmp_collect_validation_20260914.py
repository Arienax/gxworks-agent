from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker count {text.count(old)} != 1")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# plc_json_validator: add a bounded collector without changing fail-fast public APIs.
path = Path("src/plc_json_validator.py")
text = path.read_text(encoding="utf-8")
class_marker = '''class PLCJsonValidationError(ValueError):\n    pass\n\n\nclass ApproachContractValidationError'''
class_replacement = '''class PLCJsonValidationError(ValueError):\n    pass\n\n\nclass PLCJsonValidationAggregateError(PLCJsonValidationError):\n    \"\"\"Multiple independent structural violations from one candidate pass.\"\"\"\n\n    def __init__(self, errors):\n        self.errors = tuple(\n            error for error in (errors or ())\n            if isinstance(error, PLCJsonValidationError)\n        )\n        first = self.errors[0] if self.errors else PLCJsonValidationError(\"$: invalid ladder structure\")\n        super().__init__(str(first))\n\n\nclass ApproachContractValidationError'''
if class_marker not in text:
    raise SystemExit("aggregate class marker missing")
text = text.replace(class_marker, class_replacement, 1)

marker = '''def validate_ladder_candidate_structure(\n    data,\n    plc_model=\"FX3U\",\n    *,\n    require_catalogued_instructions=True,\n):\n'''
collector = r'''def collect_ladder_candidate_structure_errors(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
    limit=16,
):
    """Collect independent model-facing ladder violations in one bounded pass.

    Existing validators remain fail-fast. This collector invokes them at safe
    subtree boundaries so a malformed element cannot corrupt traversal of its
    siblings. At most one error is collected from each individual element call,
    while later rungs/branches/elements continue to be checked.
    """
    errors = []
    limit = max(1, min(int(limit or 16), 64))

    def capture(callable_, *args, **kwargs):
        if len(errors) >= limit:
            return False
        try:
            callable_(*args, **kwargs)
            return True
        except PLCJsonValidationError as error:
            errors.append(error)
            return False

    def fail(path, message):
        if len(errors) >= limit:
            return
        try:
            _fail(path, message)
        except PLCJsonValidationError as error:
            errors.append(error)

    try:
        model = normalize_plc_model(plc_model)
    except PLCJsonValidationError as error:
        return [error]

    if not isinstance(data, dict):
        capture(_require_dict, data, "$")
        return errors

    allowed = {"device_comments", "rungs"}
    extra = set(data) - allowed
    missing = allowed - set(data)
    if extra:
        fail("$", f"unexpected top-level fields: {sorted(extra)}")
    if missing:
        fail("$", f"missing top-level fields: {sorted(missing)}")
    if len(errors) >= limit:
        return errors

    comments = data.get("device_comments")
    if isinstance(comments, dict):
        for addr, comment in comments.items():
            if len(errors) >= limit:
                break
            if not isinstance(addr, str) or not addr:
                fail(f"$.device_comments.{addr!r}", "device address must be a non-empty string")
            else:
                capture(_validate_device_address, addr, f"$.device_comments.{addr}", model)
            capture(_check_text_length, comment, f"$.device_comments.{addr}")
    else:
        capture(_require_dict, comments, "$.device_comments")

    rungs = data.get("rungs")
    if not isinstance(rungs, list):
        capture(_require_list, rungs, "$.rungs")
        return errors

    seen_ids = set()
    for rung_idx, rung in enumerate(rungs):
        if len(errors) >= limit:
            break
        rung_path = f"$.rungs[{rung_idx}]"
        if not isinstance(rung, dict):
            capture(_require_dict, rung, rung_path)
            continue

        rung_id = rung.get("rung_id")
        if "rung_id" not in rung:
            fail(f"{rung_path}.rung_id", "missing required field")
        elif not isinstance(rung_id, int) or isinstance(rung_id, bool):
            fail(f"{rung_path}.rung_id", "expected integer")
        else:
            if rung_id in seen_ids:
                fail(f"{rung_path}.rung_id", f"duplicate rung_id {rung_id}")
            seen_ids.add(rung_id)

        if rung.get("debug_note") is not None:
            capture(_check_text_length, rung.get("debug_note"), f"{rung_path}.debug_note")

        header = rung.get("header_element")
        if header is not None:
            capture(
                _validate_element,
                header,
                f"{rung_path}.header_element",
                VALID_INPUT_TYPES - {"parallel_block"},
                plc_model=model,
                require_catalogued_instructions=require_catalogued_instructions,
            )

        shared_inputs = rung.get("shared_inputs", [])
        if isinstance(shared_inputs, list):
            for elem_idx, elem in enumerate(shared_inputs):
                capture(
                    _validate_element,
                    elem,
                    f"{rung_path}.shared_inputs[{elem_idx}]",
                    VALID_INPUT_TYPES - {"parallel_block"},
                    plc_model=model,
                    require_catalogued_instructions=require_catalogued_instructions,
                )
                if len(errors) >= limit:
                    break
        else:
            capture(_require_list, shared_inputs, f"{rung_path}.shared_inputs")

        branches = rung.get("branches")
        if not isinstance(branches, list):
            capture(_require_list, branches, f"{rung_path}.branches")
            continue
        for branch_idx, branch in enumerate(branches):
            if len(errors) >= limit:
                break
            branch_path = f"{rung_path}.branches[{branch_idx}]"
            if not isinstance(branch, dict):
                capture(_require_dict, branch, branch_path)
                continue
            if "branch_id" in branch and (
                not isinstance(branch["branch_id"], int)
                or isinstance(branch["branch_id"], bool)
            ):
                fail(f"{branch_path}.branch_id", "expected integer")

            inputs = branch.get("inputs", [])
            if isinstance(inputs, list):
                for elem_idx, elem in enumerate(inputs):
                    capture(
                        _validate_element,
                        elem,
                        f"{branch_path}.inputs[{elem_idx}]",
                        VALID_INPUT_TYPES,
                        plc_model=model,
                        require_catalogued_instructions=require_catalogued_instructions,
                    )
                    if len(errors) >= limit:
                        break
            else:
                capture(_require_list, inputs, f"{branch_path}.inputs")

            outputs = branch.get("outputs", [])
            if isinstance(outputs, list):
                for elem_idx, elem in enumerate(outputs):
                    capture(
                        _validate_element,
                        elem,
                        f"{branch_path}.outputs[{elem_idx}]",
                        VALID_OUTPUT_TYPES,
                        is_output=True,
                        plc_model=model,
                        require_catalogued_instructions=require_catalogued_instructions,
                    )
                    if len(errors) >= limit:
                        break
            else:
                capture(_require_list, outputs, f"{branch_path}.outputs")

    return errors


def raise_ladder_candidate_structure_errors(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
    limit=16,
):
    errors = collect_ladder_candidate_structure_errors(
        data,
        plc_model,
        require_catalogued_instructions=require_catalogued_instructions,
        limit=limit,
    )
    if errors:
        raise PLCJsonValidationAggregateError(errors)
    return data


'''
if marker not in text:
    raise SystemExit("collector insertion marker missing")
text = text.replace(marker, collector + marker, 1)
path.write_text(text, encoding="utf-8")

# plc_generation: generation path uses collector; public fail-fast validator stays intact elsewhere.
replace_once(
    "src/plc_generation.py",
    '''from plc_json_validator import (\n    PLCJsonValidationError, validate_ladder_candidate_structure,\n    validate_ladder_partial_structure,\n)\n''',
    '''from plc_json_validator import (\n    PLCJsonValidationError, raise_ladder_candidate_structure_errors,\n    validate_ladder_partial_structure,\n)\n''',
)
replace_once(
    "src/plc_generation.py",
    '''    validate_ladder_candidate_structure(parsed, plc_model=plc_model, require_catalogued_instructions=True)\n    from plc_condition_normalizer import normalize_shared_conditions\n    parsed, normalization = normalize_shared_conditions(parsed, allowed_rung_ids=normalization_scope)\n    validate_ladder_candidate_structure(parsed, plc_model=plc_model, require_catalogued_instructions=True)\n''',
    '''    raise_ladder_candidate_structure_errors(\n        parsed, plc_model=plc_model, require_catalogued_instructions=True\n    )\n    from plc_condition_normalizer import normalize_shared_conditions\n    parsed, normalization = normalize_shared_conditions(parsed, allowed_rung_ids=normalization_scope)\n    raise_ladder_candidate_structure_errors(\n        parsed, plc_model=plc_model, require_catalogued_instructions=True\n    )\n''',
)

# generation_repair: flatten aggregate errors and preserve real violation_count.
path = Path("src/application/generation_repair.py")
text = path.read_text(encoding="utf-8")
old = '''class GenerationValidationError(GenerationError):\n    def __init__(self, errors, *, attempts, language, stop_reason=\"attempt_limit\",\n                 max_attempts=MAX_VALIDATION_REPAIRS):\n        rows = [validation_diagnostic(error) for error in errors][-16:]\n        self.diagnostics = {\"response_language\": language, \"contract_name\": \"ladder\",\n            \"diagnostic_id\": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16],\n            \"violations\": rows, \"violation_count\": len(rows), \"truncated\": False,\n            \"stage\": \"generation_validation\", \"attempt_count\": attempts,\n            \"max_attempts\": max_attempts, \"stop_reason\": stop_reason}\n        super().__init__(\"梯形图候选未通过硬校验；未接受任何程序。\" +\n                         \"; \".join(row[\"path\"] + \": \" + row[\"reason\"] for row in rows))\n'''
new = '''def _flatten_validation_errors(errors):\n    result = []\n    stack = list(errors or ())\n    while stack:\n        error = stack.pop(0)\n        nested = getattr(error, \"errors\", None)\n        if isinstance(nested, (list, tuple)) and nested:\n            stack[0:0] = list(nested)\n        else:\n            result.append(error)\n    return result\n\n\nclass GenerationValidationError(GenerationError):\n    def __init__(self, errors, *, attempts, language, stop_reason=\"attempt_limit\",\n                 max_attempts=MAX_VALIDATION_REPAIRS):\n        flattened = _flatten_validation_errors(errors)\n        rows = [validation_diagnostic(error) for error in flattened[:16]]\n        total = len(flattened)\n        self.diagnostics = {\"response_language\": language, \"contract_name\": \"ladder\",\n            \"diagnostic_id\": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16],\n            \"violations\": rows, \"violation_count\": total, \"truncated\": total > len(rows),\n            \"stage\": \"generation_validation\", \"attempt_count\": attempts,\n            \"max_attempts\": max_attempts, \"stop_reason\": stop_reason}\n        super().__init__(\"梯形图候选未通过硬校验；未接受任何程序。\" +\n                         \"; \".join(row[\"path\"] + \": \" + row[\"reason\"] for row in rows))\n'''
if old not in text:
    raise SystemExit("GenerationValidationError marker missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
