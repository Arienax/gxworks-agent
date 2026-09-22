"""Small, deterministic checks of confirmed Boolean input semantics.

Checks inspect only machine-readable bindings and the candidate topology. They
never infer roles from free-form requirement prose and never allocate devices.
"""
from __future__ import annotations

import re

from plc.validation import PLCJsonValidationError


def _self_hold_kernel(rung, branch, *, start, stop, output=None):
    outputs = branch.get("outputs", []) if isinstance(branch, dict) else []
    if len(outputs) != 1 or outputs[0].get("type") != "COIL":
        return None
    held = outputs[0].get("address")
    if not held or (output and held != output):
        return None

    conditions = [
        rung.get("header_element"),
        *(rung.get("shared_inputs", []) or []),
        *(branch.get("inputs", []) or []),
    ]
    conditions = [item for item in conditions if item is not None]
    parallels = [item for item in conditions
                 if isinstance(item, dict) and item.get("type") == "parallel_block"]
    if len(parallels) != 1:
        return None
    paths = parallels[0].get("branches", [])
    if len(paths) != 2 or any(len(path) != 1 for path in paths):
        return None
    contacts = [path[0] for path in paths]
    if not all(isinstance(item, dict) and item.get("type") in {"NO", "NC"}
               for item in contacts):
        return None
    if {item.get("address") for item in contacts} != {start, held}:
        return None
    hold = next((item for item in contacts if item.get("address") == held), None)
    start_contact = next((item for item in contacts if item.get("address") == start), None)
    if hold is None or start_contact is None or hold.get("type") != "NO":
        return None

    outside = [item for item in conditions if item is not parallels[0]]
    stop_contacts = [
        item for item in outside
        if isinstance(item, dict)
        and item.get("type") in {"NO", "NC"}
        and item.get("address") == stop
    ]
    if len(stop_contacts) != 1:
        return None
    return {
        "held": held,
        "start_contact": start_contact,
        "stop_contact": stop_contacts[0],
        "rung_id": rung.get("rung_id"),
    }


def check_direct_self_hold(ladder, spec):
    """Verify the confirmed start/stop polarity of an existing self-hold kernel.

    The program may contain arbitrary additional rungs and guards. Only the
    local latch kernel is checked; engineering behavior outside that kernel
    remains Review/simulator territory.
    """
    def uncovered(reason):
        return {
            "check": "direct_self_hold_truth_table",
            "status": "not_covered",
            "reason": reason,
        }

    if not isinstance(spec, dict):
        return uncovered("missing_spec")
    approach = spec.get("selected_approach") or {}
    contract = approach.get("generation_contract") or {}
    if contract.get("enforce") is False:
        return uncovered("outside_check_scope")
    explicit_primitive = "self_hold" in set(contract.get("required_structures") or [])

    roles, bound_ids, levels = {}, set(), {}
    for item in spec.get("io_bindings") or []:
        if not isinstance(item, dict) or item.get("role") not in {"start", "stop", "output"}:
            continue
        role = item["role"]
        address = item.get("address")
        if role in roles and roles[role] != address:
            return uncovered("ambiguous_roles")
        roles[role] = address
        if item.get("source_parameter_id"):
            bound_ids.add(item["source_parameter_id"])
        level = item.get("active_level")
        if type(level) is int and level in (0, 1):
            levels[role] = level

    if not roles.get("start") or not roles.get("stop") or roles["start"] == roles["stop"]:
        return uncovered("missing_roles")

    values = {
        p.get("id"): str(p.get("value") or "").strip()
        for p in spec.get("parameters", [])
        if isinstance(p, dict)
    }
    start_mode = values.get("start_mode")
    legacy_level_modes = {"保持闭合即有效（普通起保停）", "保持闭合即有效", "level_high"}
    if start_mode and start_mode not in legacy_level_modes:
        return uncovered("unsupported_start_mode")
    if "start" not in levels and start_mode in legacy_level_modes:
        levels["start"] = 1

    polarity = values.get("stop_contact_polarity", "")
    high = {"常开：未按下时断开，按下时接通", "NO", "normally_open_active_high"}
    low = {"常闭：未按下时接通，按下时断开", "NC", "normally_closed_active_low"}
    if "stop" not in levels and polarity in high | low:
        levels["stop"] = 1 if polarity in high else 0

    if "start" not in levels or "stop" not in levels:
        return uncovered("missing_input_levels")

    if any(
        re.search(r"上升沿|下降沿|rising|falling|edge", values.get(identifier, ""), re.I)
        for identifier in bound_ids
    ):
        return uncovered("edge_semantics")

    kernels = []
    for rung in (ladder or {}).get("rungs", []) or []:
        if not isinstance(rung, dict):
            continue
        branches = rung.get("branches", [])
        if not isinstance(branches, list):
            continue
        for branch in branches:
            kernel = _self_hold_kernel(
                rung,
                branch,
                start=roles["start"],
                stop=roles["stop"],
                output=roles.get("output"),
            )
            if kernel is not None:
                kernels.append(kernel)

    if not kernels:
        return uncovered("not_self_hold_topology" if explicit_primitive else "unsupported_topology")
    if len(kernels) != 1:
        return uncovered("ambiguous_self_hold_topology")

    kernel = kernels[0]
    expected_start = "NO" if levels["start"] else "NC"
    expected_stop = "NC" if levels["stop"] else "NO"
    if kernel["start_contact"].get("type") != expected_start:
        raise PLCJsonValidationError(
            "$.rungs: confirmed self-hold start polarity differs from input semantics"
        )
    if kernel["stop_contact"].get("type") != expected_stop:
        raise PLCJsonValidationError(
            "$.rungs: confirmed self-hold stop/run-permit polarity differs from input semantics"
        )

    return {
        "check": "direct_self_hold_truth_table",
        "status": "verified",
        "states": 8,
        "held_address": kernel["held"],
        "rung_id": kernel["rung_id"],
        "basis": "explicit_contract" if explicit_primitive
        else "confirmed_levels_and_candidate_topology",
    }
