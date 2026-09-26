"""Conservative, model-free cleanup of generation-owned ladder conditions.

This is not a PLC validator or an intent repair. Unknown forms stay unchanged;
the existing API validator remains responsible for accepting the candidate.
Only direct ordinary contacts/comparisons are pure predicates here. Evaluation
is never moved across unknown effects or writes to predicate operands.
"""

import copy
import re

from plc.generation_contract import MAX_LABEL_LEN


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


def _merged_annotation(values):
    """Retain whole distinct notes without making a valid candidate invalid."""
    text = "\n".join(dict.fromkeys(str(value) for value in values if value))
    return text if len(text) <= MAX_LABEL_LEN else None


def _writes_conflict(reads, writes):
    return any(device in writes or device[0] + "*" in writes for device in reads)


def _known_writes(branch):
    """Conservative effects, using the Core catalogue rather than a new opcode list.

    Typed outputs have exact destinations. Applied instructions may write a
    range (including double words), so reserve their entire destination family.
    Special/indirect operands, vendor operations and control flow remain barriers.
    """
    from plc.instructions import get_instruction_spec, InstructionCategory, SemanticKind, OperandRole

    writes = set()
    for output in branch["outputs"]:
        kind = output.get("type")
        if kind in {"COIL", "PLS", "PLF"} and not set(output) - {"type", "address", "label"}:
            device = _device(output.get("address"))
            if not device or not device.startswith(("M", "Y")):
                return None
            writes.add(device)
            continue
        if kind in {"TIMER", "COUNTER"} and not set(output) - {"type", "address", "value", "label"}:
            family = "T" if kind == "TIMER" else "C"
            if not re.fullmatch(family + r"\d+", str(output.get("address", ""))):
                return None
            writes.add(family + "*")
            continue
        if kind == "APP_INSTR" and not set(output) - {"type", "opcode", "operands", "label"}:
            op, operands = output.get("opcode"), output.get("operands", [])
        elif kind == "BLOCK_OUTPUT" and not set(output) - {"type", "expression", "label"}:
            parts = str(output.get("expression", "")).split()
            op, operands = (parts[0], parts[1:]) if parts else ("", [])
        else:
            return None
        spec = get_instruction_spec(op)
        if (spec is None or spec.category != InstructionCategory.ACTION
                or spec.semantic_kind not in {SemanticKind.FUNCTION, SemanticKind.COIL, SemanticKind.COMPARISON}
                or not isinstance(operands, list) or len(operands) != len(spec.operands)
                or not spec.write_indexes):
            return None
        for operand, contract in zip(operands, spec.operands):
            token = str(operand).strip().upper()
            if contract.role == OperandRole.CONTROL:
                return None
            # Indirect, packed-bit, string and special-register effects are not
            # inferred from a base address. Even reads can select hidden state.
            if not (_operand(token) or re.fullmatch(r"[TC]\d+", token)):
                return None
            if contract.role in {OperandRole.WRITE, OperandRole.READ_WRITE}:
                match = re.fullmatch(r"([MYDTC])\d+", token)
                if not match:
                    return None
                writes.add(match[1] + "*")
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
            if prior_writes is not None and not _writes_conflict(reads, prior_writes):
                safe_facts[identity] = reads
            elif any((_predicate(item) or (None,))[0] == identity for item in branch.get("inputs", [])):
                summary.skip("stateful_or_unknown_output" if prior_writes is None else "read_after_write",
                             [rung_id], f"branches[{index}].inputs")
        inputs = branch.get("inputs", [])
        cleaned = _deduplicate(inputs, safe_facts, summary, rung_id, f"branches[{index}].inputs")
        if cleaned != inputs:
            branch["inputs"] = cleaned
        writes = _known_writes(branch)
        prior_writes = None if prior_writes is None or writes is None else prior_writes | writes

    branches = rung["branches"]
    if len(branches) < 2 or not all(branch.get("inputs") for branch in branches):
        return
    # Only the prefix is moved; branch-local parallel/edge suffixes stay put.
    if any(item is None for item in predicates):
        summary.skip("non_pure_condition", [rung_id], "common_prefix")
        return
    writes_before_last = set()
    for branch in branches[:-1]:
        writes = _known_writes(branch)
        if writes is None:
            summary.skip("stateful_or_unknown_output", [rung_id], "common_prefix")
            return
        writes_before_last.update(writes)
    common = []
    for items in zip(*(branch["inputs"] for branch in branches)):
        predicates = [_predicate(item) for item in items]
        if any(item is None for item in predicates) or any(item[0] != predicates[0][0] for item in predicates[1:]):
            break
        if _writes_conflict(predicates[0][1], writes_before_last):
            summary.skip("read_after_write", [rung_id], "common_prefix")
            break
        shared = copy.deepcopy(items[0])
        label = _merged_annotation(item.get("label") for item in items)
        if label is None:
            summary.skip("annotation_capacity", [rung_id], "common_prefix")
            break
        if label:
            shared["label"] = label
        common.append(shared)
    if common:
        rung["shared_inputs"] = rung.get("shared_inputs", []) + common
        removed = [copy.deepcopy(branch["inputs"][:len(common)]) for branch in branches]
        for branch in branches:
            branch["inputs"] = branch["inputs"][len(common):]
        summary.change("extract_common_prefix", [rung_id], conditions=copy.deepcopy(common),
                       removed_branch_conditions=removed)


def _rung_group(rung):
    prefix = ([rung["header_element"]] if rung.get("header_element") is not None else []) + rung.get("shared_inputs", [])
    if any(_predicate(item) is None for item in prefix):
        return None, "non_pure_condition"
    paths, writes, coils = [], set(), set()
    for branch in rung["branches"]:
        effects = _known_writes(branch)
        if effects is None:
            return None, "stateful_or_unknown_output"
        for output in branch["outputs"]:
            if output.get("type") == "COIL":
                target = _device(output.get("address"))
                if target in coils:
                    return None, "duplicate_coil_target"
                coils.add(target)
        paths.append(prefix + branch.get("inputs", []))
        writes.update(effects)
    return {"prefix": prefix, "paths": paths, "writes": writes, "coils": coils}, None


def _merged_prefix(groups):
    common = []
    paths = [path for group in groups for path in group["paths"]]
    # Only earlier outputs may invalidate a later evaluation. A family-wide
    # effect deliberately blocks both single- and multi-word range aliases.
    writes = set().union(*(group["writes"] for group in groups))
    reason = "non_pure_condition"
    for items in zip(*paths):
        predicates = [_predicate(item) for item in items]
        if any(item is None for item in predicates):
            break
        if any(item[0] != predicates[0][0] for item in predicates[1:]):
            reason = "different_branch_conditions"
            break
        if _writes_conflict(predicates[0][1], writes):
            reason = "read_after_write"
            break
        shared = copy.deepcopy(items[0])
        label = _merged_annotation(item.get("label") for item in items)
        if label is None:
            reason = "annotation_capacity"
            break
        if label:
            shared["label"] = label
        common.append(shared)
    if not common:
        return [], reason
    # Re-homing a longer pre-existing shared suffix would evaluate it per
    # branch. Do not do so when an earlier branch could change that predicate.
    for group in groups:
        for item in group["prefix"][len(common):]:
            if _writes_conflict(_predicate(item)[1], group["writes"]):
                return [], "read_after_write"
    return common, None


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
        left_group, left_reason = _rung_group(left)
        right_group, right_reason = _rung_group(right)
        ids = [left["rung_id"], right["rung_id"]]
        reason = left_reason or right_reason
        note = _merged_annotation(source.get("debug_note") for source in (left, right))
        if note is None:
            reason = reason or "annotation_capacity"
        common = []
        if not reason:
            if left_group["coils"].intersection(right_group["coils"]):
                reason = "duplicate_coil_target"
            else:
                common, reason = _merged_prefix([left_group, right_group])
        if reason:
            summary.skip(reason, ids, "adjacent_networks")
            index += 1
            continue
        source_left, source_right = copy.deepcopy(left), copy.deepcopy(right)
        combined = []
        for source, group in ((left, left_group), (right, right_group)):
            for original, path in zip(source["branches"], group["paths"]):
                branch = copy.deepcopy(original)
                branch.update(inputs=copy.deepcopy(path[len(common):]),
                              branch_id=len(combined) + 1, y_offset_level=len(combined))
                combined.append(branch)
        left["header_element"] = None
        left["shared_inputs"] = common
        left["branches"] = combined
        if note:
            left["debug_note"] = note
        pure_coils = all(output.get("type") == "COIL" for branch in combined for output in branch["outputs"])
        operation = "merge_adjacent_coils" if pure_coils else "merge_adjacent_branches"
        summary.change(operation, ids, target_rung_id=left["rung_id"], removed_rung_id=right["rung_id"],
                       source_rung=source_right, previous_target_rung=source_left)
        del rungs[index + 1]
    return normalized, summary.data
