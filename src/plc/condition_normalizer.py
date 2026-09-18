"""Conservative, model-free cleanup of generation-owned ladder conditions.

This is not a PLC validator or an intent repair. Unknown forms stay unchanged;
the existing API validator remains responsible for accepting the candidate.
Only direct ordinary contacts/comparisons are pure predicates here. Evaluation
is never moved across stateful/unknown outputs or writes to predicate operands.
"""

import copy
import re


_OPERATORS = frozenset({"=", "<>", "<", ">", "<=", ">="})


def _device(value, *, word=False):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([XYM]{})(\d+)".format("|D" if word else ""), value.strip().upper())
    if match is None:
        return None
    prefix, digits = match.groups()
    if len(digits) > 6:
        return None
    if prefix in {"X", "Y"} and any(digit not in "01234567" for digit in digits):
        return None
    if prefix in {"M", "D"} and int(digits) >= 8000:
        return None  # CPU flags/registers may change outside ordinary logic.
    return prefix + (digits.lstrip("0") or "0")


def _operand(value):
    device = _device(value, word=True)
    if device:
        return device, {device}
    if isinstance(value, str) and re.fullmatch(r"K[+-]?\d+|H[0-9A-F]+", value.upper()):
        return value.upper(), set()
    return None


def _predicate(element):
    if not isinstance(element, dict):
        return None
    kind = element.get("type")
    if not isinstance(kind, str):
        return None
    if kind in {"NO", "NC"} and not set(element) - {"type", "address", "label"}:
        device = _device(element.get("address"))
        if device:
            return (kind, device), {device}
    if kind in {"COMPARE", "BLOCK_INPUT"} and not set(element) - {"type", "expression", "label"}:
        expression = element.get("expression")
        parts = expression.split() if isinstance(expression, str) else []
        if len(parts) != 3:
            return None
        if parts[0] in _OPERATORS:
            operator, left, right = parts
        elif parts[1] in _OPERATORS:
            left, operator, right = parts
        else:
            return None
        left, right = _operand(left), _operand(right)
        if left and right:
            return ("COMPARE", operator, left[0], right[0]), left[1] | right[1]
    return None


def _coil_writes(branch):
    """None is an explicit barrier, never a guessed empty write set."""
    writes = set()
    for output in branch["outputs"]:
        if (output.get("type") != "COIL"
                or set(output) - {"type", "address", "label"}):
            return None
        device = _device(output.get("address"))
        if not device or not device.startswith(("M", "Y")):
            return None
        writes.add(device)
    return writes


def _rung_shape(rung):
    if (not isinstance(rung, dict) or not isinstance(rung.get("rung_id"), int)
            or isinstance(rung.get("rung_id"), bool)):
        return False
    if set(rung) - {"rung_id", "header_element", "shared_inputs", "branches", "debug_note"}:
        return False
    if rung.get("header_element") is not None and not isinstance(rung["header_element"], dict):
        return False
    if not isinstance(rung.get("shared_inputs", []), list):
        return False
    branches = rung.get("branches")
    if not isinstance(branches, list) or not branches:
        return False
    for branch in branches:
        if not isinstance(branch, dict) or set(branch) - {"branch_id", "y_offset_level", "inputs", "outputs"}:
            return False
        if any(not isinstance(branch.get(key, []), list) for key in ("inputs", "outputs")):
            return False
        if not branch.get("outputs") or any(not isinstance(item, dict) for item in branch["outputs"]):
            return False
        if any(key in branch and (not isinstance(branch[key], int) or isinstance(branch[key], bool))
               for key in ("branch_id", "y_offset_level")):
            return False
    return True


class _Summary:
    def __init__(self):
        self.data = {"changes": [], "skipped": []}
        self.seen_skips = set()

    def change(self, operation, ids, **details):
        self.data["changes"].append({"operation": operation, "rung_ids": list(ids), **details})

    def skip(self, reason, ids, path=""):
        key = (reason, tuple(ids), path)
        if key not in self.seen_skips:
            self.seen_skips.add(key)
            self.data["skipped"].append({"reason": reason, "rung_ids": list(ids), "path": path})


def _deduplicate(elements, initial, summary, rung_id, path):
    seen = dict(initial)
    result = []
    for index, element in enumerate(elements):
        predicate = _predicate(element)
        if predicate is None:
            seen.clear()  # Do not move facts through an edge/opaque expression.
            summary.skip("non_pure_condition", [rung_id], path)
        elif predicate[0] in seen:
            summary.change("remove_duplicate_condition", [rung_id], path=f"{path}[{index}]",
                           removed=copy.deepcopy(element))
            continue
        else:
            seen[predicate[0]] = predicate[1]
        result.append(element)
    return result


def _normalize_rung(rung, summary):
    rung_id = rung["rung_id"]
    header = _predicate(rung.get("header_element"))
    initial = {header[0]: header[1]} if header else {}
    shared = rung.get("shared_inputs", [])
    cleaned = _deduplicate(shared, initial, summary, rung_id, "shared_inputs")
    if cleaned != shared:
        rung["shared_inputs"] = cleaned
    prefix = ([rung["header_element"]] if rung.get("header_element") is not None else []) + cleaned
    predicates = [_predicate(item) for item in prefix]
    shared_facts = {item[0]: item[1] for item in predicates if item}
    if any(item is None for item in predicates):
        shared_facts = {}

    prior_writes = set()
    for index, branch in enumerate(rung["branches"]):
        safe_facts = {}
        for identity, reads in shared_facts.items():
            if prior_writes is not None and not reads.intersection(prior_writes):
                safe_facts[identity] = reads
            elif any((_predicate(item) or (None,))[0] == identity for item in branch.get("inputs", [])):
                summary.skip("stateful_or_unknown_output" if prior_writes is None else "read_after_write",
                             [rung_id], f"branches[{index}].inputs")
        inputs = branch.get("inputs", [])
        cleaned = _deduplicate(inputs, safe_facts, summary, rung_id, f"branches[{index}].inputs")
        if cleaned != inputs:
            branch["inputs"] = cleaned
        writes = _coil_writes(branch)
        prior_writes = None if prior_writes is None or writes is None else prior_writes | writes

    branches = rung["branches"]
    if len(branches) < 2 or not all(branch.get("inputs") for branch in branches):
        return
    # The first implementation does not factor through parallel/edge suffixes.
    if (any(item is None for item in predicates)
            or any(_predicate(item) is None for branch in branches for item in branch["inputs"])):
        summary.skip("non_pure_condition", [rung_id], "common_prefix")
        return
    writes_before_last = set()
    for branch in branches[:-1]:
        writes = _coil_writes(branch)
        if writes is None:
            summary.skip("stateful_or_unknown_output", [rung_id], "common_prefix")
            return
        writes_before_last.update(writes)
    common = []
    for items in zip(*(branch["inputs"] for branch in branches)):
        predicates = [_predicate(item) for item in items]
        if any(item[0] != predicates[0][0] for item in predicates[1:]):
            break
        if predicates[0][1].intersection(writes_before_last):
            summary.skip("read_after_write", [rung_id], "common_prefix")
            break
        common.append(copy.deepcopy(items[0]))
    if common:
        rung["shared_inputs"] = rung.get("shared_inputs", []) + common
        removed = [copy.deepcopy(branch["inputs"][:len(common)]) for branch in branches]
        for branch in branches:
            branch["inputs"] = branch["inputs"][len(common):]
        summary.change("extract_common_prefix", [rung_id], conditions=copy.deepcopy(common),
                       removed_branch_conditions=removed)


def _coil_group(rung):
    prefix = ([rung["header_element"]] if rung.get("header_element") is not None else []) + rung.get("shared_inputs", [])
    expected = None
    reads, writes = set(), set()
    for branch in rung["branches"]:
        predicates = [_predicate(item) for item in prefix + branch.get("inputs", [])]
        if not predicates or any(item is None for item in predicates):
            return None, "non_pure_condition"
        identities = tuple(item[0] for item in predicates)
        if expected is not None and identities != expected:
            return None, "different_branch_conditions"
        expected = identities
        for _, devices in predicates:
            reads.update(devices)
        branch_writes = _coil_writes(branch)
        if branch_writes is None:
            return None, "stateful_or_unknown_output"
        if len(branch_writes) != len(branch["outputs"]) or writes.intersection(branch_writes):
            return None, "duplicate_coil_target"
        writes.update(branch_writes)
    if reads.intersection(writes):
        return None, "read_after_write"
    return (expected, reads, writes), None


def normalize_shared_conditions(ladder, *, allowed_rung_ids=None):
    """Return (deep-copied ladder, JSON-ready changes/skipped summary).

    allowed_rung_ids limits both local edits and every source of a merge. A
    merge retains the first rung id and all output order; it never ORs writers.
    Unknown containers/operations remain for the caller's existing validator.
    """
    normalized = copy.deepcopy(ladder)
    summary = _Summary()
    if not isinstance(normalized, dict) or not isinstance(normalized.get("rungs"), list):
        summary.skip("unsupported_container", [])
        return normalized, summary.data
    try:
        allowed = None if allowed_rung_ids is None else set(allowed_rung_ids)
    except TypeError:
        summary.skip("invalid_scope", [])
        return normalized, summary.data
    if allowed is not None and any(not isinstance(item, int) or isinstance(item, bool) for item in allowed):
        summary.skip("invalid_scope", [])
        return normalized, summary.data
    rungs = normalized["rungs"]
    seen_ids, duplicate_ids = set(), set()
    for rung in rungs:
        if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int):
            rung_id = rung["rung_id"]
            if rung_id in seen_ids:
                duplicate_ids.add(rung_id)
            seen_ids.add(rung_id)
    if duplicate_ids:
        # Do not accidentally make an invalid API candidate acceptable by
        # deleting one of its duplicate identities during a merge.
        summary.skip("duplicate_rung_ids", sorted(duplicate_ids))
        return normalized, summary.data
    eligible = set()
    for rung in rungs:
        if not _rung_shape(rung):
            summary.skip("unsupported_container", [])
            continue
        rung_id = rung["rung_id"]
        if allowed is not None and rung_id not in allowed:
            summary.skip("outside_scope", [rung_id])
            continue
        eligible.add(id(rung))
        _normalize_rung(rung, summary)

    index = 0
    while index + 1 < len(rungs):
        left, right = rungs[index:index + 2]
        if id(left) not in eligible or id(right) not in eligible:
            index += 1
            continue
        left_group, left_reason = _coil_group(left)
        right_group, right_reason = _coil_group(right)
        ids = [left["rung_id"], right["rung_id"]]
        reason = left_reason or right_reason
        if not reason and left_group[0] != right_group[0]:
            index += 1  # Different logic is not an attempted merge.
            continue
        if not reason and left_group[2].intersection(right_group[2]):
            reason = "duplicate_coil_target"
        if not reason and left.get("debug_note") and right.get("debug_note") and left["debug_note"] != right["debug_note"]:
            reason = "distinct_network_notes"
        if reason:
            summary.skip(reason, ids, "adjacent_networks")
            index += 1
            continue
        # Existing left branches share the same full predicate sequence. Their
        # branch-local remainder is evaluated once before the preserved writes.
        removed_left_conditions = [
            {"branch_id": branch.get("branch_id"), "inputs": copy.deepcopy(branch["inputs"])}
            for branch in left["branches"] if branch.get("inputs")
        ]
        left["shared_inputs"] = left.get("shared_inputs", []) + copy.deepcopy(left["branches"][0].get("inputs", []))
        for branch in left["branches"]:
            branch["inputs"] = []
        next_id = max((branch.get("branch_id", 0) for branch in left["branches"]), default=0) + 1
        next_y = max((branch.get("y_offset_level", 0) for branch in left["branches"]), default=0) + 1
        for offset, original in enumerate(right["branches"]):
            branch = copy.deepcopy(original)
            branch.update(inputs=[], branch_id=next_id + offset, y_offset_level=next_y + offset)
            left["branches"].append(branch)
        if not left.get("debug_note") and right.get("debug_note"):
            left["debug_note"] = right["debug_note"]
        summary.change("merge_adjacent_coils", ids, target_rung_id=left["rung_id"],
                       removed_rung_id=right["rung_id"], source_rung=copy.deepcopy(right),
                       removed_left_branch_conditions=removed_left_conditions)
        del rungs[index + 1]
    return normalized, summary.data
