import copy
import json
import re


M8029_INSTRUCTIONS = {
    "PLSY",
    "DPLSY",
    "PLSV",
    "DRVI",
    "DDRVI",
    "DRVA",
    "DDRVA",
    "DVIT",
    "ZRN",
    "DSZR",
}


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


def _common_input_prefix(branches):
    input_lists = [branch.get("inputs", []) for branch in branches]
    if not input_lists:
        return []
    prefix = []
    for elements in zip(*input_lists):
        first = elements[0]
        first_logic = {
            key: value
            for key, value in first.items()
            if key != "label"
        }
        if all(
            {
                key: value
                for key, value in element.items()
                if key != "label"
            }
            == first_logic
            for element in elements[1:]
        ):
            prefix.append(copy.deepcopy(first))
        else:
            break
    return prefix


def _logic_key(element):
    return json.dumps(
        {
            key: value
            for key, value in element.items()
            if key != "label"
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _is_m8029(element):
    return str(element.get("address", "")).upper() == "M8029"


def _extract_common_inputs(branches):
    """Remove shared contacts even when M8029 appears before those contacts."""
    if len(branches) < 2:
        return []
    candidates = [
        item
        for item in branches[0].get("inputs", [])
        if not _is_m8029(item)
    ]
    common = []
    for candidate in candidates:
        key = _logic_key(candidate)
        if all(
            any(
                not _is_m8029(item) and _logic_key(item) == key
                for item in branch.get("inputs", [])
            )
            for branch in branches[1:]
        ):
            common.append(copy.deepcopy(candidate))

    for candidate in common:
        key = _logic_key(candidate)
        for branch in branches:
            inputs = branch.get("inputs", [])
            for index, item in enumerate(inputs):
                if not _is_m8029(item) and _logic_key(item) == key:
                    del inputs[index]
                    break
    return common


def _motion_branches(rung):
    return [
        branch
        for branch in rung.get("branches", [])
        if any(
            output.get("type") == "APP_INSTR"
            and str(output.get("opcode", "")).upper() in M8029_INSTRUCTIONS
            for output in branch.get("outputs", [])
        )
    ]


def _completion_branches(rung):
    return [
        branch
        for branch in rung.get("branches", [])
        if any(_is_m8029(item) for item in branch.get("inputs", []))
    ]


def _put_motion_instruction_last(branch):
    outputs = branch.get("outputs", [])
    motion = [
        output
        for output in outputs
        if output.get("type") == "APP_INSTR"
        and str(output.get("opcode", "")).upper() in M8029_INSTRUCTIONS
    ]
    if not motion:
        return False
    reordered = [output for output in outputs if output not in motion] + motion
    if reordered == outputs:
        return False
    branch["outputs"] = reordered
    return True


def _same_header(left, right):
    if left is None or right is None:
        return left is right
    return _logic_key(left) == _logic_key(right)


def _mergeable_header(left, right):
    if _same_header(left, right):
        return copy.deepcopy(left)
    # DeepSeek commonly puts the state comparison only on the following
    # M8029 rung. Promoting that comparison to the shared rung is stricter and
    # keeps both the motion instruction and completion action in that state.
    if left is None:
        return copy.deepcopy(right)
    if right is None:
        return copy.deepcopy(left)
    return None


def _renumber_branches(rung):
    for index, branch in enumerate(rung.get("branches", []), start=1):
        branch["branch_id"] = index
        branch["y_offset_level"] = index - 1


def normalize_m8029_parallel_branches(ladder):
    """Put motion instructions and their M8029 handling on one split rung."""
    normalized = copy.deepcopy(ladder)
    changed_rungs = []
    rungs = normalized.get("rungs", [])

    # DeepSeek often emits the instruction and M8029 handling as two adjacent
    # rungs. Merge that pair before extracting their duplicated conditions.
    index = 1
    while index < len(rungs):
        instruction_rung = rungs[index - 1]
        completion_rung = rungs[index]
        motion = _motion_branches(instruction_rung)
        for branch in motion:
            _put_motion_instruction_last(branch)
        completion = _completion_branches(completion_rung)
        merged_header = _mergeable_header(
            instruction_rung.get("header_element"),
            completion_rung.get("header_element"),
        )
        if (
            motion
            and completion
            and not _motion_branches(completion_rung)
            and (
                merged_header is not None
                or (
                    instruction_rung.get("header_element") is None
                    and completion_rung.get("header_element") is None
                )
            )
        ):
            instruction_rung["header_element"] = merged_header
            common = _extract_common_inputs(motion + completion)
            if common:
                instruction_rung["shared_inputs"] = (
                    copy.deepcopy(instruction_rung.get("shared_inputs", []))
                    + common
                )
            instruction_rung.setdefault("branches", []).extend(
                copy.deepcopy(completion)
            )
            _renumber_branches(instruction_rung)
            completion_ids = {id(branch) for branch in completion}
            completion_rung["branches"] = [
                branch
                for branch in completion_rung.get("branches", [])
                if id(branch) not in completion_ids
            ]
            changed_rungs.append(instruction_rung.get("rung_id"))
            if not completion_rung["branches"]:
                del rungs[index]
                continue
        index += 1

    for rung in rungs:
        motion_branches = _motion_branches(rung)
        reordered_motion = any(
            _put_motion_instruction_last(branch)
            for branch in motion_branches
        )
        if reordered_motion and rung.get("rung_id") not in changed_rungs:
            changed_rungs.append(rung.get("rung_id"))
        completion_branches = _completion_branches(rung)
        paired = motion_branches + completion_branches
        if not motion_branches or not completion_branches:
            continue

        common = _extract_common_inputs(paired)
        if not common:
            common = _common_input_prefix(paired)
            if common:
                for branch in paired:
                    branch["inputs"] = branch.get("inputs", [])[len(common):]
        if common:
            existing_shared = rung.get("shared_inputs", [])
            rung["shared_inputs"] = copy.deepcopy(existing_shared) + common
            if rung.get("rung_id") not in changed_rungs:
                changed_rungs.append(rung.get("rung_id"))
        _renumber_branches(rung)

    return normalized, changed_rungs


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
