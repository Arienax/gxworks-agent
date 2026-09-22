"""Confirmed-fact semantic validation for generated ladder candidates.

This layer is deliberately narrower than full engineering review. It checks only
machine-readable semantics already confirmed by the user/application. Generic
style heuristics and advisory engineering findings remain outside generation
acceptance.
"""
from __future__ import annotations

from collections.abc import Mapping

from plc.validation import PLCJsonValidationError

_VERSION = "confirmed-semantics-v1"
_BLOCKING_HANDOFF_GAPS = frozenset({
    "missing_roles",
    "ambiguous_roles",
    "missing_input_levels",
})


class ConfirmedSemanticValidationError(PLCJsonValidationError):
    """A candidate or semantic handoff contradicts confirmed machine facts."""


def _generation_contract(confirmed_spec):
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
    return contract if isinstance(contract, Mapping) else {}


def _selected_approach_receipt(confirmed_spec):
    contract = _generation_contract(confirmed_spec)
    return {
        "check": "selected_approach_contract",
        "status": "deferred_to_review" if contract else "not_applicable",
    }


def _self_hold_required(confirmed_spec):
    contract = _generation_contract(confirmed_spec)
    if contract.get("enforce") is False:
        return False
    return "self_hold" in set(contract.get("required_structures") or ())


def validate_confirmed_semantics(ladder, confirmed_spec, plc_model="FX3U"):
    """Validate confirmed semantics and return an inspectable coverage receipt.

    A narrow checker may decline an engineering shape it does not understand;
    that remains unresolved rather than becoming a new style gate. Missing
    machine identity/electrical facts required by an explicit semantic contract
    are different: accepting them would make deterministic validation impossible,
    so those handoff gaps fail closed without invoking model repair.
    """
    if not isinstance(confirmed_spec, Mapping):
        return {"version": _VERSION, "status": "not_applicable", "checks": []}

    checks = [_selected_approach_receipt(confirmed_spec)]

    from plc.specification.checks import check_direct_self_hold
    required = _self_hold_required(confirmed_spec)
    try:
        self_hold = check_direct_self_hold(ladder, confirmed_spec)
    except PLCJsonValidationError as error:
        raise ConfirmedSemanticValidationError(str(error)) from error

    self_hold["required"] = required
    if required and self_hold.get("status") == "not_covered":
        self_hold["status"] = "unresolved"
        reason = str(self_hold.get("reason") or "unknown")
        if reason in _BLOCKING_HANDOFF_GAPS:
            raise ConfirmedSemanticValidationError(
                "$.confirmed_spec.io_bindings: required self_hold semantic "
                f"handoff is incomplete ({reason})"
            )
    checks.append(self_hold)

    covered = [row for row in checks if row.get("status") == "verified"]
    unresolved = [row for row in checks if row.get("status") == "unresolved"]
    status = (
        "unresolved"
        if unresolved
        else "verified"
        if covered
        else "not_applicable"
    )
    return {
        "version": _VERSION,
        "plc_model": str(plc_model or "").strip().upper(),
        "status": status,
        "checks": checks,
    }
