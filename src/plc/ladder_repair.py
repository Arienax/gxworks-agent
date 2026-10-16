import copy
import json
import re



def normalize_legacy_counter_outputs(ladder):
    """Convert the legacy ``TIMER`` + ``C`` schema into ``COUNTER``.

    Early project versions used one JSON output type for both T timers and C
    counters.  Keep old saved projects editable, while ensuring every new
    validation/generation path sees the unambiguous representation.
    """

    normalized = copy.deepcopy(ladder)
    converted = []
    for rung in normalized.get("rungs", []) or []:
        for branch in rung.get("branches", []) or []:
            for output in branch.get("outputs", []) or []:
                if str(output.get("type", "")).upper() != "TIMER":
                    continue
                address = str(output.get("address", "")).strip().upper()
                if not address.startswith("C"):
                    continue
                output["type"] = "COUNTER"
                if address and address not in converted:
                    converted.append(address)
    return normalized, converted


_TYPED_OUT_ADDRESS_RE = re.compile(r"^(SM|Y|M|T|C)(\d+)$", re.IGNORECASE)


def normalize_app_instr_out_outputs(ladder):
    """Convert a model-emitted ``APP_INSTR OUT`` into the typed JSON form.

    ``OUT`` is a real lowered PLC mnemonic, but it is not an application
    instruction in this interchange schema.  A normal coil, timer and counter
    have separate output objects so renderers and semantic analysis do not
    have to infer the meaning again.  Only unambiguous operand layouts are
    converted; malformed forms are deliberately left untouched for the hard
    validator to reject.
    """

    normalized = copy.deepcopy(ladder)
    converted = []
    for rung in normalized.get("rungs", []) or []:
        rung_id = rung.get("rung_id")
        for branch in rung.get("branches", []) or []:
            branch_id = branch.get("branch_id")
            for output in branch.get("outputs", []) or []:
                if str(output.get("type", "")).strip().upper() != "APP_INSTR":
                    continue
                if str(output.get("opcode", "")).strip().upper() != "OUT":
                    continue
                operands = output.get("operands", [])
                if not isinstance(operands, list) or not operands:
                    continue
                address = str(operands[0] or "").strip().upper()
                match = _TYPED_OUT_ADDRESS_RE.fullmatch(address)
                if match is None:
                    continue
                prefix = match.group(1).upper()
                if prefix in {"Y", "M", "SM"} and len(operands) == 1:
                    replacement = {"type": "COIL", "address": address}
                elif prefix == "T" and len(operands) == 2:
                    replacement = {
                        "type": "TIMER",
                        "address": address,
                        "value": operands[1],
                    }
                elif prefix == "C" and len(operands) == 2:
                    replacement = {
                        "type": "COUNTER",
                        "address": address,
                        "value": operands[1],
                    }
                else:
                    continue
                if "label" in output:
                    replacement["label"] = output.get("label")
                output.clear()
                output.update(replacement)
                location = f"rung {rung_id}, branch {branch_id}"
                converted.append(
                    f"{location}: OUT {address} -> {replacement['type']}"
                )
    return normalized, converted


def _expand_input_path(header, inputs):
    paths = [[]]
    if header:
        paths[0].append(copy.deepcopy(header))

    for element in inputs:
        if element.get("type") != "parallel_block":
            for path in paths:
                path.append(copy.deepcopy(element))
            continue

        expanded = []
        for path in paths:
            for branch in element.get("branches", []):
                expanded.append(path + copy.deepcopy(branch))
        paths = expanded or paths

    return paths


def _deduplicate_paths(paths):
    unique = []
    seen = set()
    for path in paths:
        key = json.dumps(path, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def merge_duplicate_coils(ladder):
    """Return a copy with duplicate COIL paths merged into one rung per address."""
    repaired = copy.deepcopy(ladder)
    rungs = repaired.get("rungs", [])
    occurrences = {}

    for rung_index, rung in enumerate(rungs):
        header = rung.get("header_element")
        for branch in rung.get("branches", []):
            for output in branch.get("outputs", []):
                if output.get("type") != "COIL":
                    continue
                address = str(output.get("address", "")).upper()
                occurrences.setdefault(address, []).append(
                    {
                        "rung_index": rung_index,
                        "conditions": _expand_input_path(
                            header, branch.get("inputs", [])
                        ),
                        "output": copy.deepcopy(output),
                    }
                )

    duplicates = {
        address: items
        for address, items in occurrences.items()
        if address and len(items) > 1
    }
    if not duplicates:
        return repaired, []

    duplicate_addresses = set(duplicates)
    for rung in rungs:
        kept_branches = []
        for branch in rung.get("branches", []):
            branch["outputs"] = [
                output
                for output in branch.get("outputs", [])
                if not (
                    output.get("type") == "COIL"
                    and str(output.get("address", "")).upper()
                    in duplicate_addresses
                )
            ]
            if branch["outputs"]:
                kept_branches.append(branch)
        rung["branches"] = kept_branches

    next_rung_id = max(
        (rung.get("rung_id", 0) for rung in rungs),
        default=0,
    ) + 1
    insertions = {}
    repaired_addresses = []

    for address, items in duplicates.items():
        conditions = _deduplicate_paths(
            [
                path
                for item in items
                for path in item["conditions"]
            ]
        )
        if any(not path for path in conditions):
            merged_inputs = []
        elif len(conditions) == 1:
            merged_inputs = conditions[0]
        else:
            merged_inputs = [
                {
                    "type": "parallel_block",
                    "branches": conditions,
                }
            ]

        output = items[0]["output"]
        output["address"] = address
        merged_rung = {
            "rung_id": next_rung_id,
            "debug_note": f"自动修复双线圈{address}：合并全部驱动条件",
            "header_element": None,
            "branches": [
                {
                    "branch_id": 1,
                    "y_offset_level": 0,
                    "inputs": merged_inputs,
                    "outputs": [output],
                }
            ],
        }
        next_rung_id += 1
        insertions.setdefault(items[0]["rung_index"], []).append(merged_rung)
        repaired_addresses.append(address)

    rebuilt_rungs = []
    for rung_index, rung in enumerate(rungs):
        rebuilt_rungs.extend(insertions.get(rung_index, []))
        if rung.get("branches"):
            rebuilt_rungs.append(rung)
    for rung_id, rung in enumerate(rebuilt_rungs, start=1):
        rung["rung_id"] = rung_id
    repaired["rungs"] = rebuilt_rungs
    return repaired, repaired_addresses
