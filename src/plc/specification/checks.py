"""Small, deterministic checks of confirmed Boolean input semantics.

Not a natural-language requirement verifier. Coverage is deliberately narrow;
unsupported representations are left to the ordinary PLC review/simulation path.
No condition, address, polarity, or output is ever added to a model's candidate.
"""
from __future__ import annotations

import itertools
import re
from plc.validation import PLCJsonValidationError


def _self_hold_topology(conditions, roles):
    """Recognize the candidate's exact two-contact hold path, not its prose.

    This only enables checking the explicitly confirmed electrical polarities
    of an already present self-hold circuit; it never invents a hard contract.
    """
    if len(conditions) != 2:
        return False
    parallels = [item for item in conditions if item.get("type") == "parallel_block"]
    stops = [item for item in conditions if item.get("type") in {"NO", "NC"}
             and item.get("address") == roles["stop"]]
    if len(parallels) != 1 or len(stops) != 1:
        return False
    paths = parallels[0].get("branches", [])
    if len(paths) != 2 or any(len(path) != 1 for path in paths):
        return False
    contacts = [path[0] for path in paths]
    return (all(item.get("type") in {"NO", "NC"} for item in contacts)
            and {item.get("address") for item in contacts} == {roles["start"], roles["output"]}
            and any(item.get("address") == roles["output"] and item.get("type") == "NO"
                    for item in contacts))


def check_direct_self_hold(ladder, spec):
    uncovered = {"check": "direct_self_hold_truth_table", "status": "not_covered"}
    if not isinstance(spec, dict):
        return uncovered
    approach = spec.get("selected_approach") or {}
    contract = approach.get("generation_contract") or {}
    required = set(contract.get("required_structures") or [])
    if required - {"direct_logic", "self_hold"} or contract.get("enforce") is False:
        return uncovered
    explicit_primitive = "self_hold" in required
    roles, bound_ids, levels = {}, set(), {}
    for item in spec.get("io_bindings") or []:
        if not isinstance(item, dict) or item.get("role") not in {"start", "stop", "output"}:
            continue
        role = item["role"]
        if role in roles:
            return uncovered
        roles[role] = item.get("address")
        if item.get("source_parameter_id"):
            bound_ids.add(item["source_parameter_id"])
        level = item.get("active_level")
        if type(level) is int and level in (0, 1):
            levels[role] = level
    if set(roles) != {"start", "stop", "output"} or len(set(roles.values())) != 3:
        return uncovered
    values = {p.get("id"): str(p.get("value") or "").strip()
              for p in spec.get("parameters", []) if isinstance(p, dict)}
    start_mode = values.get("start_mode")
    legacy_level_modes = {"保持闭合即有效（普通起保停）", "保持闭合即有效", "level_high"}
    if start_mode and start_mode not in legacy_level_modes:
        return uncovered
    if "start" not in levels and start_mode in legacy_level_modes:
        levels["start"] = 1
    polarity = values.get("stop_contact_polarity", "")
    high = {"常开：未按下时断开，按下时接通", "NO", "normally_open_active_high"}
    low = {"常闭：未按下时接通，按下时断开", "NC", "normally_closed_active_low"}
    if "stop" not in levels and polarity in high | low:
        levels["stop"] = 1 if polarity in high else 0
    if "start" not in levels or "stop" not in levels:
        return uncovered
    # A physical active level does not specify edge-vs-level execution.
    # Leave explicitly edge-triggered starts to their own semantic checks.
    if any(re.search(r"上升沿|下降沿|rising|falling|edge", values.get(identifier, ""), re.I)
           for identifier in bound_ids):
        return uncovered
    allowed_parameters = bound_ids | {"start_mode", "stop_contact_polarity", "fault_stop_input"}
    if any(p.get("id") not in allowed_parameters
           for p in spec.get("parameters", []) if isinstance(p, dict)):
        return uncovered
    if values.get("fault_stop_input", "不需要") not in {"不需要", "none", ""}:
        return uncovered
    if any(r.get("address") not in set(roles.values())
           for r in spec.get("io_table", []) if isinstance(r, dict)):
        return uncovered
    rungs = ladder.get("rungs", [])
    if len(rungs) != 1 or len(rungs[0].get("branches", [])) != 1:
        return uncovered
    rung, branch = rungs[0], rungs[0]["branches"][0]
    outputs = branch.get("outputs", [])
    if len(outputs) != 1 or outputs[0].get("type") != "COIL" or outputs[0].get("address") != roles["output"]:
        return uncovered

    def covered(item):
        if item is None:
            return True
        if not isinstance(item, dict):
            return False
        if item.get("type") in {"NO", "NC"}:
            return item.get("address") in set(roles.values())
        if item.get("type") == "parallel_block":
            return all(covered(child) for path in item.get("branches", []) for child in path)
        return False

    conditions = [rung.get("header_element"), *rung.get("shared_inputs", []), *branch.get("inputs", [])]
    if not all(covered(item) for item in conditions):
        return uncovered
    if not explicit_primitive and not _self_hold_topology([item for item in conditions if item is not None], roles):
        return uncovered

    def condition(item, state):
        if item is None:
            return True
        kind = item.get("type")
        if kind in {"NO", "NC"} and item.get("address") in state:
            value = state[item["address"]]
            return value if kind == "NO" else not value
        if kind == "parallel_block":
            return any(all(condition(child, state) for child in path) for path in item["branches"])
        raise LookupError("not covered")

    try:
        for start, stop, held in itertools.product((False, True), repeat=3):
            state = {roles["start"]: start, roles["stop"]: stop, roles["output"]: held}
            actual = all(condition(item, state) for item in conditions)
            expected = ((start == bool(levels["start"]) or held)
                        and stop != bool(levels["stop"]))
            if actual != expected:
                raise PLCJsonValidationError("$.rungs.0: confirmed direct self-hold start/stop behavior differs")
    except LookupError:
        return uncovered
    return {"check": "direct_self_hold_truth_table", "status": "verified", "states": 8,
            "basis": "explicit_contract" if explicit_primitive else "confirmed_levels_and_candidate_topology"}
