"""Conservative, model-free cleanup of generation-owned ladder conditions.

This is not a PLC validator or an intent repair. Unknown forms stay unchanged;
the existing API validator remains responsible for accepting the candidate.
Core facts classify stable contacts, comparisons and pure parallel expressions.
Evaluation is never moved across unknown effects or writes to predicate operands.
"""

import copy
import re
from collections import Counter

from plc.condition_analysis import ConditionAnalysis
from plc.generation_contract import MAX_LABEL_LEN


def _merged_annotation(values):
    """Retain complete, distinct annotations within the existing field limit."""
    text = "\n".join(dict.fromkeys(str(value) for value in values if value))
    return text if len(text) <= MAX_LABEL_LEN else None


def _writes_conflict(reads, writes):
    return writes is None or any(device in writes or re.sub(r"\d+$", "*", device) in writes for device in reads)


def _predicate(element, analysis):
    value = analysis.condition(element)
    return (value.identity, value.reads) if value.identity is not None else None


def _combine_conditions(items):
    merged = copy.deepcopy(items[0])
    label = _merged_annotation(item.get("label") for item in items)
    if label is None:
        return None
    if label:
        merged["label"] = label
    if merged.get("type") == "parallel_block":
        for b, branch in enumerate(merged["branches"]):
            for i in range(len(branch)):
                value = _combine_conditions([item["branches"][b][i] for item in items])
                if value is None:
                    return None
                branch[i] = value
    return merged


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


def _deduplicate(elements, initial, summary, rung_id, path, analysis):
    seen, result = dict(initial), []
    for index, element in enumerate(elements):
        predicate = _predicate(element, analysis)
        if predicate is None:
            seen.clear()
            summary.skip(analysis.condition(element).reason, [rung_id], f"{path}[{index}]")
        elif predicate[0] in seen:
            merged = _combine_conditions([seen[predicate[0]], element])
            if merged is None:
                summary.skip("annotation_capacity", [rung_id], path)
            else:
                seen[predicate[0]].update(merged)
                summary.change("remove_duplicate_condition", [rung_id], path=f"{path}[{index}]",
                               removed=copy.deepcopy(element))
                continue
        else:
            seen[predicate[0]] = element
        result.append(element)
    return result


def _parallel_inputs(elements, summary, rung_id, path, analysis):
    result = []
    for index, element in enumerate(elements):
        if analysis.condition(element).kind != "parallel" or _predicate(element, analysis) is None:
            result.append(element)
            continue
        original = copy.deepcopy(element)
        change_start = len(summary.data["changes"])
        arms = [_deduplicate(arm, {}, summary, rung_id, f"{path}[{index}].branches[{b}]", analysis)
                for b, arm in enumerate(element["branches"])]
        # Pure Boolean OR arms may have a shared series prefix. Keep edges and
        # opaque expressions at their original evaluation sites.
        common, _ = _shared_conditions(arms, [], analysis)
        if not common:
            element["branches"] = arms
            result.append(element)
            continue
        if element.get("label"):
            label = _merged_annotation([common[-1].get("label"), element["label"]])
            if label is None:
                result.append(original)
                del summary.data["changes"][change_start:]
                summary.skip("annotation_capacity", [rung_id], path)
                continue
            common[-1]["label"] = label
        remaining = [_without_shared(arm, common, analysis) for arm in arms]
        if not all(remaining) and any(e.get("label") for arm in remaining for e in arm):
            # Boolean absorption must not silently discard a source annotation
            # about a different device or move that annotation to another one.
            result.append(original)
            del summary.data["changes"][change_start:]
            summary.skip("annotated_absorption", [rung_id], path)
            continue
        result.extend(common)
        if all(remaining):
            tail = copy.deepcopy(element)
            tail["branches"] = remaining
            tail.pop("label", None)
            result.append(tail)
        summary.change("factor_parallel_condition", [rung_id], path=f"{path}[{index}]",
                       conditions=copy.deepcopy(common))
    return result


def _prefix(rung):
    return ([rung["header_element"]] if rung.get("header_element") is not None else []) + rung.get("shared_inputs", [])


def _without_shared(elements, common, analysis):
    remaining = list(elements)
    for item in common:
        identity = _predicate(item, analysis)[0]
        for i, element in enumerate(remaining):
            predicate = _predicate(element, analysis)
            if predicate and predicate[0] == identity:
                del remaining[i]
                break
    return remaining


def _shared_conditions(paths, effects, analysis):
    """Intersect leading pure conjunctions, preserving the first path's order.

    Reordering is limited to pure reads before the first edge/opaque condition.
    Whole parallel expressions remain branch-local under the existing schema.
    Writes by the last output cannot invalidate a later condition evaluation.
    """
    segments, reason = [], "different_branch_conditions"
    for path in paths:
        segment = {}
        for element in path:
            predicate = _predicate(element, analysis)
            if predicate is None:
                reason = analysis.condition(element).reason
                break
            segment.setdefault(predicate[0], element)
        segments.append(segment)
    writes = set()
    for effect in effects[:-1]:
        writes = None if effect is None or writes is None else writes | effect
    common = []
    for identity, element in segments[0].items():
        if any(identity not in s for s in segments[1:]):
            continue
        if analysis.condition(element).kind == "parallel":
            reason = "parallel_shared_form"
            continue
        if _writes_conflict(_predicate(element, analysis)[1], writes):
            reason = "stateful_or_unknown_output" if writes is None else "read_after_write"
            continue
        merged = _combine_conditions([s[identity] for s in segments])
        if merged is None:
            reason = "annotation_capacity"
            continue
        common.append(merged)
    return common, None if common else reason


def _normalize_rung(rung, summary, analysis):
    rung_id = rung["rung_id"]
    header = rung.get("header_element")
    known = {_predicate(header, analysis)[0]: header} if _predicate(header, analysis) else {}
    shared = _deduplicate(rung.get("shared_inputs", []), known, summary, rung_id, "shared_inputs", analysis)
    if shared != rung.get("shared_inputs", []):
        rung["shared_inputs"] = shared
    prefix = _prefix(rung)
    known = {_predicate(item, analysis)[0]: item for item in prefix if _predicate(item, analysis)}
    if any(_predicate(item, analysis) is None for item in prefix):
        known = {}
    writes = set()
    for index, branch in enumerate(rung["branches"]):
        safe = {key: item for key, item in known.items()
                if not _writes_conflict(_predicate(item, analysis)[1], writes)}
        path = f"branches[{index}].inputs"
        inputs = _parallel_inputs(branch.get("inputs", []), summary, rung_id, path, analysis)
        branch["inputs"] = _deduplicate(inputs, safe, summary, rung_id, path, analysis)
        effects = analysis.writes(branch)
        writes = None if writes is None or effects is None else writes | effects
    branches = rung["branches"]
    if len(branches) < 2:
        return
    common, reason = _shared_conditions([b["inputs"] for b in branches],
                                       [analysis.writes(b) for b in branches], analysis)
    if common:
        rung["shared_inputs"] = rung.get("shared_inputs", []) + common
        for branch in branches:
            branch["inputs"] = _without_shared(branch["inputs"], common, analysis)
        summary.change("extract_common_prefix", [rung_id], conditions=copy.deepcopy(common))
    elif reason != "different_branch_conditions":
        summary.skip(reason, [rung_id], "common_conditions")


def condition_count(rung):
    return _input_count(_prefix(rung)) + sum(_input_count(b.get("inputs", [])) for b in rung["branches"])


def _input_count(elements):
    return sum(sum(_input_count(arm) for arm in e.get("branches", []) if isinstance(arm, list))
               if isinstance(e, dict) and e.get("type") == "parallel_block" and isinstance(e.get("branches"), list) else 1
               for e in elements)


def _group(rung, analysis):
    prefix = _prefix(rung)
    if not prefix and any(not b.get("inputs") for b in rung["branches"]):
        return None, "implicit_power_flow"
    effects, coils = [], set()
    for branch in rung["branches"]:
        effect = analysis.writes(branch)
        if effect is None:
            return None, next((analysis.output(o).reason for o in branch["outputs"] if analysis.output(o).writes is None), "stateful_or_unknown_output")
        effects.append(effect)
        for output in branch["outputs"]:
            if output.get("type") == "COIL":
                from plc.device_identity import canonical_device
                address = canonical_device(output.get("address"))
                if address in coils:
                    return None, "duplicate_coil_target"
                coils.add(address)
    reason = _can_rehome(prefix, effects, analysis)
    if reason:
        return None, reason
    paths = [prefix + b.get("inputs", []) for b in rung["branches"]]
    common, reason = _shared_conditions(paths, [], analysis)
    return {"conditions": {_predicate(c, analysis)[0]: c for c in common}, "reason": reason,
            "expanded_count": sum(_input_count(p) for p in paths),
            "effects": effects, "coils": coils}, None


def _can_rehome(prefix, effects, analysis):
    if len(effects) < 2 or not prefix:
        return None
    writes = set()
    for effect in effects[:-1]:
        writes = None if writes is None or effect is None else writes | effect
    for element in prefix:
        predicate = _predicate(element, analysis)
        if predicate is None:
            return "shared_evaluation_site"
        if _writes_conflict(predicate[1], writes):
            return "stateful_or_unknown_output" if writes is None else "read_after_write"
    return None


def _merge(rungs, common, analysis):
    result = copy.deepcopy(rungs[0])
    result["header_element"], result["shared_inputs"], result["branches"] = None, copy.deepcopy(common), []
    for rung in rungs:
        for original in rung["branches"]:
            branch = copy.deepcopy(original)
            branch.update(inputs=copy.deepcopy(_without_shared(_prefix(rung) + branch.get("inputs", []), common, analysis)),
                          branch_id=len(result["branches"]) + 1, y_offset_level=len(result["branches"]))
            result["branches"].append(branch)
    note = _merged_annotation(r.get("debug_note") for r in rungs)
    if note:
        result["debug_note"] = note
    return result


def _partition(rungs, analysis, summary, allocate_id):
    """Minimum condition occurrences, then networks, then representation changes.

    Whole original rungs always remain available choices. Splitting is allowed
    only when re-evaluating their saved prefix preserves its value and frequency.
    All alternatives are contiguous; output calls are never deleted or reordered.
    """
    units, originals = [], {}
    for rung in rungs:
        start = len(units)
        effects = [analysis.writes(b) for b in rung["branches"]]
        reason = ("implicit_power_flow" if not _prefix(rung) and any(not b.get("inputs") for b in rung["branches"])
                  else _can_rehome(_prefix(rung), effects, analysis))
        if len(rung["branches"]) > 1 and not reason:
            for branch in rung["branches"]:
                atom = {key: copy.deepcopy(value) for key, value in rung.items() if key != "branches"}
                atom["branches"] = [copy.deepcopy(branch)]
                units.append(atom)
        else:
            units.append(rung)
            if reason:
                summary.skip(reason, [rung["rung_id"]], "branch_partition")
        originals[start] = (len(units), rung)
    size = len(units)
    groups = [_group(r, analysis) for r in units]
    costs, choices = [None] * (size + 1), [None] * size
    costs[size] = (0, 0, 0)
    for start in range(size - 1, -1, -1):
        # A single unit is always a valid fallback, even with opaque effects.
        candidates = [(start + 1, units[start], False, condition_count(units[start]))]
        if start in originals:
            end, original = originals[start]
            candidates.append((end, original, True, condition_count(original)))
        coils, note_values, writes, previous_last, ids = set(), {}, set(), set(), {}
        intersection = None
        expanded_count = branch_count = 0
        for end in range(start, size):
            group, reason = groups[end]
            ids[units[end]["rung_id"]] = None
            if reason:
                summary.skip(reason, ids, "adjacent_networks")
                break
            if coils.intersection(group["coils"]):
                summary.skip("duplicate_coil_target", ids, "adjacent_networks")
                break
            coils.update(group["coils"])
            expanded_count += group["expanded_count"]
            branch_count += len(group["effects"])
            if intersection is None:
                intersection = group["conditions"]
            else:
                retained = {}
                for key, element in intersection.items():
                    if key not in group["conditions"]:
                        continue
                    merged = _combine_conditions([element, group["conditions"][key]])
                    if merged is None:
                        summary.skip("annotation_capacity", ids, "adjacent_networks")
                    else:
                        retained[key] = merged
                intersection = retained
            writes.update(previous_last, *group["effects"][:-1])
            previous_last = group["effects"][-1]
            if units[end].get("debug_note"):
                note_values[units[end]["debug_note"]] = None
            if _merged_annotation(note_values) is None:
                summary.skip("annotation_capacity", ids, "adjacent_networks")
                break
            if end == start:
                continue
            if not intersection:
                summary.skip(group["reason"] or "different_branch_conditions", ids, "adjacent_networks")
                break
            common = [element for element in intersection.values()
                      if not _writes_conflict(_predicate(element, analysis)[1], writes)]
            if not common:
                summary.skip("read_after_write", ids, "adjacent_networks")
                break  # Later groups only shrink the intersection and add writes.
            # Store the small shared condition list, materialize only the
            # winning partitions instead of copying every possible program.
            candidates.append((end + 1, common, False, expanded_count - len(common) * (branch_count - 1)))
        for end, candidate, original, count in candidates:
            rest = costs[end]
            cost = (count + rest[0], 1 + rest[1], (0 if original else 1) + rest[2])
            if costs[start] is None or cost < costs[start]:
                costs[start], choices[start] = cost, (end, candidate, original)
    result, assigned, cursor = [], set(), 0
    while cursor < size:
        end, chosen, original = choices[cursor]
        chosen = (_merge(units[cursor:end], chosen, analysis) if isinstance(chosen, list)
                  else copy.deepcopy(chosen))
        source_ids = list(dict.fromkeys(r["rung_id"] for r in units[cursor:end]))
        target = source_ids[0]
        if target in assigned:
            target = allocate_id()
        assigned.add(target)
        chosen["rung_id"] = target
        if not original:
            if len(source_ids) > 1:
                preceding = copy.deepcopy(units[cursor])
                for source_id in source_ids[1:]:
                    source = next(r for r in rungs if r["rung_id"] == source_id)
                    operation = "merge_adjacent_coils" if all(o.get("type") == "COIL" for b in chosen["branches"] for o in b["outputs"]) else "merge_adjacent_branches"
                    summary.change(operation, [target, source_id], target_rung_id=target, removed_rung_id=source_id,
                                   source_rung=copy.deepcopy(source), previous_target_rung=copy.deepcopy(preceding))
                    preceding = chosen
            else:
                summary.change("factor_branch_subgroup", source_ids, target_rung_id=target,
                               branch_ids=[b.get("branch_id") for r in units[cursor:end] for b in r["branches"]])
        result.append(chosen)
        cursor = end
    return result


def normalize_shared_conditions(ladder, *, allowed_rung_ids=None, plc_model="FX3U"):
    """Normalize only owned source rungs; keep calls, order and cached evaluations.

    Fresh programs may allocate new subgroup IDs outside all existing IDs.
    Scoped edits preserve every network identity and order for the existing
    change-scope validator; only their internal conditions may be normalized.
    """
    normalized, summary = copy.deepcopy(ladder), _Summary()
    if not isinstance(normalized, dict) or not isinstance(normalized.get("rungs"), list):
        summary.skip("unsupported_container", [])
        return normalized, summary.data
    try:
        allowed = None if allowed_rung_ids is None else set(allowed_rung_ids)
    except TypeError:
        summary.skip("invalid_scope", [])
        return normalized, summary.data
    if allowed is not None and any(type(item) is not int for item in allowed):
        summary.skip("invalid_scope", [])
        return normalized, summary.data
    rungs = normalized["rungs"]
    ids = [r.get("rung_id") for r in rungs if isinstance(r, dict) and type(r.get("rung_id")) is int]
    duplicates = sorted(i for i, count in Counter(ids).items() if count > 1)
    if duplicates:
        summary.skip("duplicate_rung_ids", duplicates)
        return normalized, summary.data
    analysis = ConditionAnalysis(plc_model)
    next_id = max([0, *ids]) + 1
    def allocate_id():
        nonlocal next_id
        value, next_id = next_id, next_id + 1
        return value
    output, pending = [], []
    def flush():
        if pending:
            if allowed is not None:
                output.extend(pending)
                if len(pending) > 1 or any(len(r["branches"]) > 1 for r in pending):
                    summary.skip("scope_preserves_network_identity", [r["rung_id"] for r in pending])
            else:
                output.extend(_partition(pending, analysis, summary, allocate_id))
            pending.clear()
    before = sum(condition_count(r) for r in rungs if _rung_shape(r))
    for rung in rungs:
        if not _rung_shape(rung):
            flush()
            summary.skip("unsupported_container", [])
            output.append(rung)
        elif allowed is not None and rung["rung_id"] not in allowed:
            flush()
            summary.skip("outside_scope", [rung["rung_id"]])
            output.append(rung)
        else:
            _normalize_rung(rung, summary, analysis)
            pending.append(rung)
    flush()
    normalized["rungs"] = output
    summary.data["statistics"] = {"conditions_before": before,
        "conditions_after": sum(condition_count(r) for r in output if _rung_shape(r)),
        "networks_before": len(rungs), "networks_after": len(output)}
    return normalized, summary.data
