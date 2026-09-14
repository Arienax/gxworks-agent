"""Minimal path-addressed repair for rejected ladder JSON.

Field repair is intentionally deterministic. Scalar engineering fields that
cannot be inferred without changing PLC semantics are marked blocked instead of
being offered to an LLM. The only whole-rung fallback currently authorized is
a parallel_block misplaced in shared_inputs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from application.generation_repair import RepairAssemblyError, candidate_base
from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
from plc_generation_contract import MAX_LABEL_LEN

MODE = "field_patch"
SCHEMA_VERSION = 1

_OPTIONAL_TEXT_FIELDS = frozenset({"label", "debug_note"})


def base_sha256(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _segments(path):
    text = str(path or "").strip()
    if text.startswith("content$"):
        text = text[8:]
    elif text.startswith("$"):
        text = text[1:]
    text = re.sub(r"\[([0-9]+)\]", r".\1", text).strip(".")
    if not text:
        return []
    out = []
    for part in text.split("."):
        if not part or part == "*":
            return None
        out.append(int(part) if part.isdigit() else part)
    return out


def _lookup(root, segments):
    current = root
    for segment in segments:
        if isinstance(segment, int):
            if not isinstance(current, list) or not 0 <= segment < len(current):
                raise KeyError(segment)
            current = current[segment]
        else:
            if not isinstance(current, dict) or segment not in current:
                raise KeyError(segment)
            current = current[segment]
    return current


def _pointer(segments):
    return "/" + "/".join(str(x).replace("~", "~0").replace("/", "~1") for x in segments)


def _branch_index(segments):
    for index, segment in enumerate(segments[:-1]):
        if segment == "branches" and isinstance(segments[index + 1], int):
            return segments[index + 1]
    return None


def _value_schema(parent, field, reason):
    if field in _OPTIONAL_TEXT_FIELDS and reason == "field_too_long":
        return {"type": ["string", "null"], "maxLength": MAX_LABEL_LEN}
    if field == "branch_id":
        return {"type": "integer", "minimum": 1}
    if field == "y_offset_level":
        return {"type": "integer", "minimum": 0}
    return None


def _target(saved, row, segments, *, strategy, current_value=None,
            value_schema=None, deterministic_value=None, context=None):
    path = _pointer(segments) if segments else "/rungs"
    target = {
        "path": path,
        "diagnostic_path": str(row.get("path") or ""),
        "reason": str(row.get("reason") or "invalid_ladder_structure"),
        "current_value": copy.deepcopy(current_value),
        "strategy": strategy,
        "value_schema": copy.deepcopy(value_schema or {"type": "null"}),
    }
    if strategy == "deterministic":
        target["deterministic_value"] = copy.deepcopy(deterministic_value)
    if isinstance(context, dict) and context:
        target["context"] = copy.deepcopy(context)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": base_sha256(saved),
        "target": target,
    }


def _blocked(saved, row, segments=None, current_value=None):
    """Return a non-LLM plan that explicitly refuses semantic guessing."""
    safe_segments = segments if segments else ["rungs"]
    return _target(
        saved, row, safe_segments,
        strategy="blocked",
        current_value=current_value,
        value_schema={"type": "null"},
    )


def _edit_distance_at_most_one(left, right):
    left = str(left or "").upper()
    right = str(right or "").upper()
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        j += 1
    return True


def _opcode_repair_candidates(observed, operands, plc_model):
    """Return evidence-bounded opcode replacements without semantic guessing."""
    token = str(observed or "").strip().upper()
    if not token:
        return [], "none", False
    count = len(operands) if isinstance(operands, list) else 0
    allowed = []
    for mnemonic in generation_app_instr_mnemonics(plc_model):
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
        if spec is not None and spec.accepts_arity(count):
            allowed.append(mnemonic)
    allowed_set = set(allowed)
    strict = []
    if token.startswith("DD"):
        candidate = token[1:]
        if candidate in allowed_set:
            strict.append(candidate)
    if token.endswith("PP"):
        candidate = token[:-1]
        if candidate in allowed_set and candidate not in strict:
            strict.append(candidate)
    if len(strict) == 1:
        return strict, "modifier_normalization", True
    if strict:
        return sorted(strict), "modifier_normalization", False
    nearby = sorted(mnemonic for mnemonic in allowed if _edit_distance_at_most_one(token, mnemonic))
    if 0 < len(nearby) <= 8:
        return nearby, "single_edit_catalog_match", False
    return [], "none", False


def _plan_single_target(saved, row, plc_model):
    reason = str(row.get("reason") or "invalid_ladder_structure")
    segments = _segments(row.get("path"))

    if reason == "invalid_shared_input":
        return None
    if not segments or segments[0] != "rungs":
        return _blocked(saved, row, segments)["target"]
    try:
        current = _lookup(saved, segments)
        parent = _lookup(saved, segments[:-1])
    except KeyError:
        return _blocked(saved, row, segments)["target"]

    leaf = segments[-1]
    if (
        leaf == "opcode" and isinstance(parent, dict)
        and str(parent.get("type") or "").upper() == "APP_INSTR"
    ):
        observed_evidence = row.get("observed_opcode")
        observed = str(observed_evidence or current or "").strip().upper()
        # A generic error path is not evidence that a catalogued opcode is a
        # typo. Only a validator-proven rejected opcode, or an actually unknown
        # current token, may enter mnemonic candidate generation.
        if not observed_evidence and DEFAULT_INSTRUCTION_REGISTRY.resolve(observed) is not None:
            return _blocked(saved, row, segments, current)["target"]
        operands = parent.get("operands") if isinstance(parent.get("operands"), list) else []
        candidates, basis, deterministic = _opcode_repair_candidates(observed, operands, plc_model)
        if candidates:
            context = {
                "mutable_field": "opcode",
                "observed_opcode": observed,
                "candidate_basis": basis,
                "allowed_values": list(candidates),
                "immutable_operands": copy.deepcopy(operands),
            }
            return _target(
                saved, row, segments,
                strategy="deterministic" if deterministic else "constrained_model",
                current_value=current,
                value_schema={"type": "string", "enum": list(candidates)},
                deterministic_value=candidates[0] if deterministic else None,
                context=context,
            )["target"]
        return _blocked(saved, row, segments, current)["target"]

    if isinstance(leaf, str):
        rule = _value_schema(parent, leaf, reason)
        if rule is not None:
            if leaf in _OPTIONAL_TEXT_FIELDS:
                deterministic_value = current[:MAX_LABEL_LEN] if isinstance(current, str) else None
            else:
                branch_index = _branch_index(segments)
                if branch_index is None:
                    return _blocked(saved, row, segments, current)["target"]
                deterministic_value = branch_index + 1 if leaf == "branch_id" else branch_index
            return _target(
                saved, row, segments,
                strategy="deterministic",
                current_value=current,
                value_schema=rule,
                deterministic_value=deterministic_value,
            )["target"]

    return _blocked(saved, row, segments, current)["target"]


def _plan_targets(plan):
    if not isinstance(plan, dict):
        return []
    targets = plan.get("targets")
    if isinstance(targets, list):
        return [item for item in targets if isinstance(item, dict)]
    target = plan.get("target")
    return [target] if isinstance(target, dict) else []


def plan(base, violations, plc_model="FX3U"):
    """Plan all independent validator-proven repairs from one validation pass.

    Pure structural shared-input errors still use the bounded whole-rung path.
    Scalar violations are batched into one field_patch. A mixed structure +
    scalar case becomes composite only when every scalar correction is already
    deterministic; the caller can apply those locally before one structure call.
    """
    saved = candidate_base(base)
    rows = [row for row in (violations or []) if isinstance(row, dict)]
    if saved is None:
        return None
    if not rows:
        row = {"path": "content$.rungs", "reason": "invalid_ladder_structure"}
        return _blocked(saved, row, ["rungs"])

    targets = []
    structural = []
    for row in rows:
        target = _plan_single_target(saved, row, plc_model)
        if target is None:
            structural.append(copy.deepcopy(row))
        else:
            targets.append(target)

    if structural:
        if not targets:
            return None
        if all(target.get("strategy") == "deterministic" for target in targets):
            return {
                "schema_version": SCHEMA_VERSION,
                "mode": "composite",
                "base_sha256": base_sha256(saved),
                "targets": targets,
                "structural_violations": structural,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "blocked_batch",
            "base_sha256": base_sha256(saved),
            "targets": targets,
            "structural_violations": structural,
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": base_sha256(saved),
        "targets": targets,
    }
    if len(targets) == 1:
        result["target"] = copy.deepcopy(targets[0])
    return result


def deterministic_response(repair_payload):
    """Materialize every deterministic field patch without calling a model."""
    if not isinstance(repair_payload, dict) or repair_payload.get("repair_mode") != MODE:
        return None
    targets = _plan_targets(repair_payload)
    if not targets:
        return None
    patches = []
    for target in targets:
        strategy = target.get("strategy")
        if strategy == "deterministic":
            value = copy.deepcopy(target.get("deterministic_value"))
        elif strategy == "blocked":
            value = None
        else:
            return None
        patches.append({"path": target.get("path"), "value": value})
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": repair_payload.get("base_sha256"),
        "patches": patches,
    }


def _pointer_segments(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
    out = []
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not token:
            raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
        out.append(int(token) if token.isdigit() else token)
    return out


def _check_value(value, rule):
    expected = rule.get("type")
    kinds = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "null" and value is None)
        for kind in kinds
    ):
        raise RepairAssemblyError("$.patches[0].value", "repair_shape_invalid")
    if "enum" in rule and value not in rule["enum"]:
        raise RepairAssemblyError("$.patches[0].value", "repair_scope_violation")
    if isinstance(value, str) and len(value) > rule.get("maxLength", len(value)):
        raise RepairAssemblyError("$.patches[0].value", "field_too_long")
    if isinstance(value, int) and value < rule.get("minimum", value):
        raise RepairAssemblyError("$.patches[0].value", "repair_shape_invalid")


def _blocked_error(target):
    path = str(target.get("diagnostic_path") or "$")
    if path.startswith("content$"):
        path = "$" + path[len("content$"):]
    elif not path.startswith("$"):
        path = "$"
    reason = str(target.get("reason") or "invalid_ladder_structure")
    return RepairAssemblyError(path, reason)


def apply(base, response, repair_plan):
    """Apply one authorized batch of path-addressed patches to an immutable baseline."""
    saved = candidate_base(base)
    if saved is None or not isinstance(repair_plan, dict) or repair_plan.get("mode") not in {MODE, "composite"}:
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    if base_sha256(saved) != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    targets = _plan_targets(repair_plan)
    if not targets:
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")
    blocked = next((target for target in targets if target.get("strategy") == "blocked"), None)
    if blocked is not None:
        raise _blocked_error(blocked)

    required = {"schema_version", "mode", "base_sha256", "patches"}
    if not isinstance(response, dict) or set(response) != required:
        raise RepairAssemblyError("$", "repair_shape_invalid")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("mode") != MODE:
        raise RepairAssemblyError("$.mode", "repair_shape_invalid")
    if response.get("base_sha256") != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    patches = response.get("patches")
    if not isinstance(patches, list) or len(patches) != len(targets):
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")
    if any(not isinstance(item, dict) or set(item) != {"path", "value"} for item in patches):
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")

    by_path = {target.get("path"): target for target in targets}
    if None in by_path or len(by_path) != len(targets):
        raise RepairAssemblyError("$.patches", "repair_scope_violation")
    patch_paths = [patch.get("path") for patch in patches]
    if len(set(patch_paths)) != len(patch_paths) or set(patch_paths) != set(by_path):
        raise RepairAssemblyError("$.patches", "repair_scope_violation")

    changed = False
    for patch in patches:
        target = by_path[patch["path"]]
        _check_value(patch.get("value"), target.get("value_schema") or {})
        segments = _pointer_segments(target["path"])
        current = _lookup(saved, segments)
        if current != patch.get("value"):
            changed = True
        parent = _lookup(saved, segments[:-1])
        leaf = segments[-1]
        if isinstance(leaf, int):
            parent[leaf] = copy.deepcopy(patch["value"])
        else:
            if not isinstance(parent, dict) or leaf not in parent:
                raise RepairAssemblyError("$.patches", "repair_scope_violation")
            parent[leaf] = copy.deepcopy(patch["value"])
    if not changed:
        raise RepairAssemblyError("$.patches", "repair_no_progress")
    return saved
