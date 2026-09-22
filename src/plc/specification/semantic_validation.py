"""Confirmed-fact semantic validation for generated ladder candidates.

This layer is deliberately narrower than full engineering review. It checks only
machine-readable semantics already confirmed by the user/application. Generic
style heuristics and advisory engineering findings remain outside generation
acceptance.
"""
from __future__ import annotations

from collections.abc import Mapping

_VERSION = "confirmed-semantics-v1"


def _selected_approach_check(ladder, confirmed_spec):
    selected = (
        confirmed_spec.get("selected_approach")
        if isinstance(confirmed_spec, Mapping)
        else None
    )
    contract = (
        selected.get("generation_contract")
        if isinstance(selected, Mapping)
        else None
    )
    if not isinstance(contract, Mapping) or not contract:
        return {"check": "selected_approach_contract", "status": "not_applicable"}

    from plc.specification.approach import validate_ladder_against_selected_approach
    from plc.validation import ApproachContractValidationError

    issues = validate_ladder_against_selected_approach(ladder, confirmed_spec)
    if issues:
        raise ApproachContractValidationError(
            "$.confirmed_spec.selected_approach",
            str(selected.get("name") or "已选方案").strip() or "已选方案",
            issues,
        )
    return {"check": "selected_approach_contract", "status": "verified"}


def validate_confirmed_semantics(ladder, confirmed_spec, plc_model="FX3U"):
    """Validate confirmed semantics and return an inspectable coverage receipt."""
    if not isinstance(confirmed_spec, Mapping):
        return {"version": _VERSION, "status": "not_applicable", "checks": []}

    checks = [_selected_approach_check(ladder, confirmed_spec)]

    from plc.specification.checks import check_direct_self_hold
    checks.append(check_direct_self_hold(ladder, confirmed_spec))

    covered = [row for row in checks if row.get("status") == "verified"]
    unresolved = [row for row in checks if row.get("status") == "not_covered"]
    status = (
        "verified"
        if covered and not unresolved
        else "partial"
        if covered
        else "not_applicable"
    )
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": status,
        "checks": checks,
    }
