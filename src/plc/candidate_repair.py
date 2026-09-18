"""Candidate-local, deterministic repair assembly; never activates a project.

A rejected generated program is NOT the saved project baseline and cannot yet
be converted to a validated PLC IR. Assemble a repair on that private JSON
snapshot first, then send the complete result through the ordinary validators.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from plc.validation import PLCJsonValidationError, validate_ladder_partial

MAX_VALIDATION_REPAIRS = 3
REPAIR_BUDGET_SECONDS = 240


class GenerationError(RuntimeError):
    """A rejected or invalid candidate; no project version has been activated."""


class RepairAssemblyError(PLCJsonValidationError):
    def __init__(self, path, reason):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _rung_map(rungs, path="$.rungs"):
    if not isinstance(rungs, list):
        raise RepairAssemblyError(path, "repair_base_invalid")
    result = {}
    for index, rung in enumerate(rungs):
        identifier = rung.get("rung_id") if isinstance(rung, dict) else None
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 0 or identifier in result:
            raise RepairAssemblyError(f"{path}[{index}].rung_id", "repair_identity_invalid")
        result[identifier] = rung
    return result


def check_candidate_containers(value):
    """Fail with schema errors before legacy normalizers traverse containers."""
    def object_at(item, path):
        if not isinstance(item, dict):
            raise RepairAssemblyError(path, "repair_shape_invalid")
    def list_at(item, path):
        if not isinstance(item, list):
            raise RepairAssemblyError(path, "repair_shape_invalid")
        for i, child in enumerate(item):
            object_at(child, f"{path}[{i}]")
        return item
    object_at(value, "$")
    for i, rung in enumerate(list_at(value.get("rungs", []), "$.rungs")):
        location = f"$.rungs[{i}]"
        if rung.get("header_element") is not None:
            object_at(rung["header_element"], location + ".header_element")
        list_at(rung.get("shared_inputs", []), location + ".shared_inputs")
        for j, branch in enumerate(list_at(rung.get("branches", []), location + ".branches")):
            for field in ("inputs", "outputs"):
                list_at(branch.get(field, []), f"{location}.branches[{j}].{field}")


def candidate_base(value):
    """Check merge identity, not PLC validity. Return a private snapshot or None."""
    if not isinstance(value, dict) or set(value) != {"device_comments", "rungs"}:
        return None
    if not isinstance(value["device_comments"], dict) or not value["rungs"]:
        return None
    try:
        _rung_map(value["rungs"])
    except RepairAssemblyError:
        return None
    return copy.deepcopy(value)


def materialize_partial(base, partial, *, replacement_only=False):
    """Assemble before validating, so invalid initial edit deltas are repairable.

    No normalization of contacts, ordering, polarity, or control logic occurs.
    Replacement-only mode forbids automatic removal/addition of whole rungs.
    Normal user-requested edits retain the established insert/delete semantics.
    """
    saved = candidate_base(base)
    if saved is None:
        raise RepairAssemblyError("$.mode", "repair_base_invalid")
    if not isinstance(partial, dict) or partial.get("mode") != "partial":
        raise RepairAssemblyError("$.mode", "repair_shape_invalid")
    if set(partial) - {"mode", "rungs", "device_comments", "delete_rung_ids"}:
        raise RepairAssemblyError("$", "repair_shape_invalid")
    changes = _rung_map(partial.get("rungs", []))
    deletes = partial.get("delete_rung_ids", [])
    comments = partial.get("device_comments", {})
    if (not isinstance(deletes, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in deletes)
            or len(set(deletes)) != len(deletes) or not isinstance(comments, dict)):
        raise RepairAssemblyError("$", "repair_shape_invalid")
    existing = _rung_map(saved["rungs"])
    if set(deletes) & set(changes) or set(deletes) - set(existing):
        raise RepairAssemblyError("$.delete_rung_ids", "repair_scope_violation")
    if replacement_only and (deletes or set(changes) - set(existing)):
        raise RepairAssemblyError("$.rungs", "repair_scope_violation")
    rungs = [copy.deepcopy(changes.get(i, rung)) for i, rung in existing.items() if i not in deletes]
    additions = [copy.deepcopy(rung) for i, rung in changes.items() if i not in existing]
    if additions:
        rungs = sorted(rungs + additions, key=lambda rung: rung["rung_id"])
    saved["rungs"] = rungs
    saved["device_comments"].update(copy.deepcopy(comments))
    return saved


def assemble_validation_repair(base, response, *, plc_model="FX3U"):
    """An automatic repair must preserve every unrelated rung and its order."""
    saved = candidate_base(base)
    if saved is None:
        raise RepairAssemblyError("$.mode", "repair_base_invalid")
    if isinstance(response, dict) and response.get("mode") == "partial":
        # Validate only the replacement rungs, not the still-invalid base.
        validate_ladder_partial(response, plc_model=plc_model)
        result = materialize_partial(saved, response, replacement_only=True)
    else:
        result = candidate_base(response)
        if result is None:
            raise RepairAssemblyError("$", "repair_shape_invalid")
        original_ids = [r["rung_id"] for r in saved["rungs"]]
        if [r["rung_id"] for r in result["rungs"]] != original_ids:
            raise RepairAssemblyError("$.rungs", "repair_scope_violation")
        # Omitted comments in a repair are not requests to delete saved notes.
        result["device_comments"] = {**saved["device_comments"], **result["device_comments"]}
    if result == saved:
        raise RepairAssemblyError("$.rungs", "repair_no_progress")
    return result


# Only protocol locations, never arbitrary exception text, enter public events.
_SEGMENTS = frozenset({"rungs", "rung_id", "branches", "branch_id", "y_offset_level", "shared_inputs",
    "header_element", "inputs", "outputs", "type", "address", "expression", "opcode", "operands",
    "label", "debug_note", "value", "device_comments", "mode", "delete_rung_ids", "confirmed_spec",
    "selected_approach", "networks"})
REPAIR_REASONS = frozenset({"invalid_shared_input", "invalid_ladder_structure", "field_too_long",
    "repair_base_invalid", "repair_identity_invalid", "repair_shape_invalid", "repair_scope_violation",
    "repair_no_progress"})


def validation_diagnostic(error):
    text = str(error)
    path = getattr(error, "path", None) if isinstance(error, RepairAssemblyError) else None
    if path is None:
        match = re.match(r"(\$(?:\.[A-Za-z_][A-Za-z_0-9]*|\[[0-9]+\])*)\s*:", text)
        path = match.group(1) if match else "$"
    parts = re.sub(r"\[([0-9]+)\]", r".\1", path.lstrip("$").lstrip(".")).split(".")
    safe = [part if part in _SEGMENTS or re.fullmatch(r"[0-9]{1,6}", part) else "*" for part in parts if part][:24]
    reason = (error.reason if isinstance(error, RepairAssemblyError) else
              "invalid_json_object" if isinstance(error, json.JSONDecodeError) else
              "invalid_shared_input" if "shared_inputs" in safe and "parallel_block" in text else
              "field_too_long" if ("must be <=" in text or "invalid text length" in text) else
              "repair_scope_violation" if ("evidence-external" in text or "out-of-scope" in text) else
              "invalid_ladder_structure")
    row = {"path": "content$" + ("." + ".".join(safe) if safe else ""), "reason": reason}
    observed_opcode = getattr(error, "observed_opcode", None)
    if isinstance(observed_opcode, str) and observed_opcode:
        row["observed_opcode"] = observed_opcode
    return row


class GenerationValidationError(GenerationError):
    def __init__(self, errors, *, attempts, language, stop_reason="attempt_limit",
                 max_attempts=MAX_VALIDATION_REPAIRS):
        rows = [validation_diagnostic(error) for error in errors][-16:]
        self.diagnostics = {"response_language": language, "contract_name": "ladder",
            "diagnostic_id": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16],
            "violations": rows, "violation_count": len(rows), "truncated": False,
            "stage": "generation_validation", "attempt_count": attempts,
            "max_attempts": max_attempts, "stop_reason": stop_reason}
        super().__init__("梯形图候选未通过硬校验；未接受任何程序。" +
                         "; ".join(row["path"] + ": " + row["reason"] for row in rows))
