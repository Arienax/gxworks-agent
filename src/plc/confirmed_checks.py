"""Small, deterministic checks of explicitly confirmed Boolean primitives.

Not a natural-language requirement verifier. Coverage is deliberately narrow;
unsupported representations are left to the ordinary PLC review/simulation path.
No condition, address, polarity, or output is ever added to a model's candidate.
"""
from __future__ import annotations

import itertools
from plc.validation import PLCJsonValidationError


def check_direct_self_hold(ladder, spec):
    uncovered = {"check": "direct_self_hold_truth_table", "status": "not_covered"}
    if not isinstance(spec, dict):
        return uncovered
    approach = spec.get("selected_approach") or {}
    contract = approach.get("generation_contract") or {}
    required = set(contract.get("required_structures") or [])
    if not {"direct_logic", "self_hold"} <= required or required - {"direct_logic", "self_hold"}:
        return uncovered
    if contract.get("enforce") is False:
        return uncovered
    bindings = spec.get("io_bindings") or []
    roles = {}
    for item in bindings:
        if not isinstance(item, dict) or item.get("role") not in {"start", "stop", "output"}:
            continue
        role = item["role"]
        if role in roles:
            return uncovered  # Several machines require separate behavior contracts.
        roles[role] = item.get("address")
    if set(roles) != {"start", "stop", "output"} or len(set(roles.values())) != 3:
        return uncovered
    values = {p.get("id"): str(p.get("value") or "").strip()
              for p in spec.get("parameters", []) if isinstance(p, dict)}
    if values.get("start_mode") not in {"保持闭合即有效（普通起保停）", "保持闭合即有效", "level_high"}:
        return uncovered
    polarity = values.get("stop_contact_polarity", "")
    high = {"常开：未按下时断开，按下时接通", "NO", "normally_open_active_high"}
    low = {"常闭：未按下时接通，按下时断开", "NC", "normally_closed_active_low"}
    if polarity not in high | low:
        return uncovered
    # Do not infer the logic of optional interlocks, multiple loads, or active-low
    # starts from their absence in this small contract.
    if any(p.get("id") not in {"start_mode", "stop_contact_polarity", "fault_stop_input"}
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

    tested_conditions = [rung.get("header_element"), *rung.get("shared_inputs", []), *branch.get("inputs", [])]
    if not all(covered(item) for item in tested_conditions):
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

    # Prove the entire truth table, not just that a stop address appears somewhere.
    try:
        for start, stop, held in itertools.product((False, True), repeat=3):
            state = {roles["start"]: start, roles["stop"]: stop, roles["output"]: held}
            actual = condition(rung.get("header_element"), state) and all(
                condition(item, state) for item in rung.get("shared_inputs", []) + branch.get("inputs", []))
            healthy = not stop if polarity in high else stop
            if actual != ((start or held) and healthy):
                raise PLCJsonValidationError("$.rungs.0: confirmed direct self-hold start/stop behavior differs")
    except LookupError:
        return uncovered
    return {"check": "direct_self_hold_truth_table", "status": "verified", "states": 8}
