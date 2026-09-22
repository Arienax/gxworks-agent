"""Derive bit predicates from confirmed bindings, never from proposal prose.

These are level tests, not a circuit design, edge detector or acceptance gate.
Callers supply the current canonical snapshot; nothing is cached or allocated.
"""
from __future__ import annotations

from collections.abc import Mapping

from plc.device_identity import canonical_device


def generation_input_conditions(bindings):
    """Return settled input predicates plus unresolved identities, without edits.

    Only explicitly typed input bindings participate. In particular a word
    register or an output with an active_level is not a boolean input. A stop
    action at bit=0 means its *inactive* level permits running. Other roles do
    not acquire a stop/interlock meaning from their name or address.
    """
    settled, unresolved = [], []
    for index, row in enumerate(bindings if isinstance(bindings, (list, tuple)) else ()):
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "").upper()
        role = str(row.get("role") or "").lower()
        # X is a physical input. M/S are considered only when the binding has
        # an explicit input role, never by scanning a human-readable label.
        if kind != "X" and not (kind in {"M", "S"} and role in {"input", "start", "stop", "interlock"}):
            continue
        identity = str(row.get("binding_id") or f"io_bindings[{index}]")
        address = canonical_device(row.get("address"))
        level = row.get("active_level")
        inactive = row.get("inactive_level")
        valid_level = type(level) is int and level in (0, 1)
        valid_address = (isinstance(address, str) and address.startswith(kind)
                         and address[len(kind):].isdigit())
        if kind == "X" and valid_address:
            valid_address = all(c in "01234567" for c in address[1:])
        if not valid_address or not valid_level or (
            inactive is not None and (type(inactive) is not int or inactive != 1 - level)
        ):
            unresolved.append(identity)
            continue
        active = ("NO " if level else "NC ") + address
        inactive_test = ("NC " if level else "NO ") + address
        result = {"binding_id": identity, "address": address,
                  "active_when": active, "inactive_when": inactive_test}
        if role:
            result["role"] = role
        if role == "stop":
            result["run_permit_when"] = inactive_test
        settled.append(result)
    return {"level_predicates": settled, "unresolved_input_bindings": unresolved}
