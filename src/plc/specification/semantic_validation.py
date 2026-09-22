"""Confirmed implementation-semantic validation for generated ladder candidates.

New Agent-A analyses emit implementation_semantics. Core projects those semantics
into generation_contract for generation/tool compatibility and validates the same
semantic requirements here. Legacy specs without implementation_semantics keep the
previous narrow compatibility behavior.
"""
from __future__ import annotations

from collections.abc import Mapping

from plc.validation import PLCJsonValidationError

_VERSION = "confirmed-semantics-v2"
_BLOCKING_HANDOFF_GAPS = frozenset({
    "missing_roles",
    "ambiguous_roles",
    "missing_input_levels",
})


class ConfirmedSemanticValidationError(PLCJsonValidationError):
    """A candidate or semantic handoff contradicts confirmed machine facts."""


def semantic_requirements(confirmed_spec):
    """Return generic requirements from structure semantics and Core user constraints."""
    if not isinstance(confirmed_spec, Mapping):
        return []
    selected = confirmed_spec.get("selected_approach")
    if not isinstance(selected, Mapping):
        return []

    from plc.specification.approach import normalize_implementation_semantics
    from plc.specification.explicit_constraints import normalize_explicit_user_constraints

    result = []
    for index, item in enumerate(
        normalize_implementation_semantics(selected.get("implementation_semantics"))
    ):
        row = dict(item)
        row["requirement_id"] = f"implementation_semantics[{index}]"
        result.append(row)

    explicit = normalize_explicit_user_constraints(
        selected.get("explicit_user_constraints")
    )
    for field, kind, status in (
        ("required_opcodes", "opcode", "required"),
        ("forbidden_opcodes", "opcode", "forbidden"),
        ("required_devices", "device", "required"),
        ("forbidden_devices", "device", "forbidden"),
    ):
        for index, value in enumerate(explicit[field]):
            result.append({
                "requirement_id": f"explicit_user_constraints.{field}[{index}]",
                "kind": kind,
                "status": status,
                "value": value,
            })
    for index, item in enumerate(explicit["instruction_instances"]):
        result.append({
            "requirement_id": f"explicit_user_constraints.instruction_instances[{index}]",
            "kind": "instruction_instance",
            "status": "required",
            "opcode": item["opcode"],
            "operands": list(item["operands"]),
        })
    return result


def _ladder_instruction_instances(ladder):
    result = set()
    for rung in (ladder or {}).get("rungs", []) or []:
        for branch in rung.get("branches", []) if isinstance(rung, Mapping) else []:
            for output in branch.get("outputs", []) if isinstance(branch, Mapping) else []:
                if not isinstance(output, Mapping) or output.get("type") != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode") or "").strip().upper()
                operands = tuple(str(value).strip() for value in output.get("operands", []) or [])
                if opcode:
                    result.add((opcode, operands))
    return result


def _feature_coverage(ladder, requirements):
    from plc.specification.approach import inspect_ladder_features
    features = inspect_ladder_features(ladder)
    available = {
        "structure": set(features.get("structures") or []),
        "opcode": set(features.get("opcodes") or []),
        "device": set(features.get("devices") or []),
    }
    rows, violations = [], []
    for requirement in requirements:
        kind = requirement.get("kind")
        status = requirement.get("status")
        if kind not in available:
            continue
        if status == "any_of":
            values = list(requirement.get("values") or [])
            ok = bool(set(values) & available[kind])
            expected = values
        else:
            value = requirement.get("value")
            present = value in available[kind]
            ok = present if status == "required" else not present
            expected = value
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "contract_feature",
            "kind": kind,
            "semantic_status": status,
            "expected": expected,
            "status": "verified" if ok else "violated",
        }
        rows.append(row)
        if not ok:
            violations.append(row)
    return rows, violations


def _instruction_instance_coverage(ladder, requirements):
    actual = _ladder_instruction_instances(ladder)
    rows, violations = [], []
    for requirement in requirements:
        if requirement.get("kind") != "instruction_instance":
            continue
        expected = (requirement["opcode"], tuple(requirement.get("operands") or ()))
        ok = expected in actual
        row = {
            "requirement_id": requirement["requirement_id"],
            "check": "exact_instruction_instance",
            "kind": "instruction_instance",
            "semantic_status": "required",
            "expected": " ".join([expected[0], *expected[1]]).strip(),
            "status": "verified" if ok else "violated",
        }
        rows.append(row)
        if not ok:
            violations.append(row)
    return rows, violations


def _self_hold_truth_table_coverage(ladder, confirmed_spec, requirements):
    """Registered capability checker for a structure semantic, not text inference."""
    targets = [row for row in requirements
               if row.get("kind") == "structure"
               and row.get("status") == "required"
               and row.get("value") == "self_hold"]
    if not targets:
        return [], []
    from plc.specification.checks import check_direct_self_hold
    try:
        result = check_direct_self_hold(ladder, confirmed_spec)
    except PLCJsonValidationError as error:
        raise ConfirmedSemanticValidationError(str(error)) from error
    row = dict(result)
    row["requirement_id"] = targets[0]["requirement_id"]
    row["semantic_status"] = "required"
    if row.get("status") == "not_covered":
        row["status"] = "unresolved"
        reason = str(row.get("reason") or "unknown")
        if reason in _BLOCKING_HANDOFF_GAPS:
            raise ConfirmedSemanticValidationError(
                "$.confirmed_spec.io_bindings: required semantic handoff "
                f"is incomplete ({reason})"
            )
    return [row], []


def _feature_checker(ladder, _confirmed_spec, requirements):
    return _feature_coverage(ladder, requirements)


def _instruction_instance_checker(ladder, _confirmed_spec, requirements):
    return _instruction_instance_coverage(ladder, requirements)


_CHECKER_REGISTRY = (
    ("contract_features", _feature_checker),
    ("instruction_instances", _instruction_instance_checker),
    ("self_hold_truth_table", _self_hold_truth_table_coverage),
)


def _legacy_compatibility_check(ladder, confirmed_spec, plc_model):
    """Preserve old narrow behavior for saved specs without implementation_semantics."""
    selected = confirmed_spec.get("selected_approach") if isinstance(confirmed_spec, Mapping) else None
    contract = selected.get("generation_contract") if isinstance(selected, Mapping) else None
    checks = [{
        "check": "selected_approach_contract",
        "status": "deferred_to_review" if isinstance(contract, Mapping) and contract else "not_applicable",
    }]
    from plc.specification.checks import check_direct_self_hold
    try:
        checks.append(check_direct_self_hold(ladder, confirmed_spec))
    except PLCJsonValidationError as error:
        raise ConfirmedSemanticValidationError(str(error)) from error
    covered = [row for row in checks if row.get("status") == "verified"]
    unresolved = [row for row in checks if row.get("status") in {"not_covered", "unresolved"}]
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": "verified" if covered and not unresolved else "partial" if covered else "not_applicable",
        "requirements": [],
        "checks": checks,
        "legacy_compatibility": True,
    }


def validate_confirmed_semantics(ladder, confirmed_spec, plc_model="FX3U"):
    """Validate canonical implementation semantics and return coverage."""
    if not isinstance(confirmed_spec, Mapping):
        return {"version": _VERSION, "status": "not_applicable", "requirements": [], "checks": []}

    selected = confirmed_spec.get("selected_approach")
    canonical_sources = (
        isinstance(selected, Mapping)
        and (
            "implementation_semantics" in selected
            or "explicit_user_constraints" in selected
        )
    )
    requirements = semantic_requirements(confirmed_spec)
    if not canonical_sources:
        return _legacy_compatibility_check(ladder, confirmed_spec, plc_model)
    if not requirements:
        return {
            "version": _VERSION,
            "plc_model": str(plc_model or "").strip().upper(),
            "status": "not_applicable",
            "requirements": [],
            "checks": [],
            "legacy_compatibility": False,
        }

    checks, violations = [], []
    for _name, checker in _CHECKER_REGISTRY:
        rows, failed = checker(ladder, confirmed_spec, requirements)
        checks.extend(rows)
        violations.extend(failed)

    if violations:
        summary = ", ".join(
            f"{row.get('kind')}:{row.get('expected')}" for row in violations[:8]
        )
        raise ConfirmedSemanticValidationError(
            "$.confirmed_spec.selected_approach: generated candidate violates "
            "confirmed implementation semantics/user constraints: " + summary
        )

    unresolved = [row for row in checks if row.get("status") == "unresolved"]
    verified_ids = {row.get("requirement_id") for row in checks if row.get("status") == "verified"}
    requirement_ids = {row["requirement_id"] for row in requirements}
    unresolved_ids = {row.get("requirement_id") for row in unresolved}
    for requirement_id in sorted(requirement_ids - verified_ids - unresolved_ids):
        checks.append({
            "requirement_id": requirement_id,
            "check": "coverage",
            "status": "unresolved",
            "reason": "no_registered_checker",
        })
    unresolved = [row for row in checks if row.get("status") == "unresolved"]
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": "unresolved" if unresolved else "verified",
        "requirements": requirements,
        "checks": checks,
        "legacy_compatibility": False,
    }
