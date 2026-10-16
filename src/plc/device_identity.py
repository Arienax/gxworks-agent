"""One spelling for Mitsubishi device aliases, without renumbering devices.

X001 and X1 denote the same index; X010 becomes X10, NEVER X8. This
representation-only boundary strips leading zeroes, not a base conversion.
CPU/range/writability validation remains the existing PLC validator's job.
Comments, literals, contact types and control topology are not rewritten.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping

DEVICE_PREFIXES = (
    "ER", "SM", "SD", "TS", "TC", "CS", "CC",
    "X", "Y", "M", "S", "T", "C", "D", "R", "V", "Z", "P", "I",
)
_DEVICE_PREFIX_PATTERN = "(?:" + "|".join(DEVICE_PREFIXES) + ")"
DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])" + _DEVICE_PREFIX_PATTERN
    + r"\d+(?:\.\d+)?(?![A-Za-z0-9_])",
    re.I,
)
# Read-only manual references include Q/L hexadecimal I/O/link devices and
# their additional device classes. This lexer grants neither an address range
# nor writability/execution support; the generation validator stays separate.
REFERENCE_DEVICE_PATTERN = r'(?:(?:SB|SW|DX|DY|[XYBW])[0-9A-F]+|(?:ZR|SM|SD|ST|TS|TC|CS|CC|ER|[MLFSTCDRVZPI])\d+)(?:\.[0-9A-F]+)?'
REFERENCE_DEVICE_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_])' + REFERENCE_DEVICE_PATTERN + r'(?![A-Za-z0-9_])', re.I)


def reference_device_tokens(value):
    """Literal source tokens for retrieval, without runtime CPU validation."""
    return tuple(match.group(0).upper() for match in REFERENCE_DEVICE_TOKEN_RE.finditer(str(value or '')))


def canonical_reference_device(value):
    if not isinstance(value, str) or not REFERENCE_DEVICE_TOKEN_RE.fullmatch(value.strip()):
        return value
    match = re.fullmatch(r'(ZR|SM|SD|ST|SB|SW|DX|DY|TS|TC|CS|CC|ER|[A-Z])([0-9A-F]+)(.*)', value.strip().upper())
    return match[1] + (match[2].lstrip('0') or '0') + match[3] if match else value


def reference_device_parts(value):
    if not isinstance(value, str) or not REFERENCE_DEVICE_TOKEN_RE.fullmatch(value.strip()):
        return None
    match = re.fullmatch(r'(ZR|SM|SD|ST|SB|SW|DX|DY|TS|TC|CS|CC|ER|[A-Z])([0-9A-F]+)', value.strip().upper())
    return match.groups() if match else None
_SIMPLE = re.compile("(" + _DEVICE_PREFIX_PATTERN + r")(\d+)", re.I)
_INDEXED = re.compile(r"((?:SM|SD|[XYMDTCS])\d+)([VZ]\d+)", re.I)


def device_tokens(value):
    """Return explicit Mitsubishi device tokens in source order."""
    text = str(value or "")
    return tuple(match.group(0).upper() for match in DEVICE_TOKEN_RE.finditer(text))


def canonical_device(value):
    """Normalize a simple device only; leave malformed/non-device values intact."""
    if not isinstance(value, str):
        return value
    match = _SIMPLE.fullmatch(value.strip())
    if match is None:
        return value
    return match[1].upper() + (match[2].lstrip("0") or "0")


def canonical_operand(value):
    """Handle explicit simple/indexed operands, never numbers, strings or names."""
    if not isinstance(value, str):
        return value
    match = _INDEXED.fullmatch(value.strip())
    if match:
        return canonical_device(match[1]) + canonical_device(match[2])
    return canonical_device(value)


def decimal_region_address(base, offset):
    """Resolve explicit decimal memory offsets; X/Y numbering is CPU-owned."""
    if type(offset) is int and offset == 0 and re.fullmatch(r'[XYTCS]\d+', str(base), re.I):
        return canonical_device(base)
    match = re.fullmatch(r'(SM|SD|M|D|R)(\d+)', str(base), re.I)
    if match is None or type(offset) is not int or int(match[2]) + offset < 0:
        raise ValueError('Device offset policy is unavailable')
    return match[1].upper() + str(int(match[2]) + offset)


def word_region_addresses(base, words, plc_model):
    """Adjacent words under the Core CPU profile, never bit or indexed devices.

    The operand width belongs to its instruction/type. Numbering and allocation
    belong to the device profile; absence of either is not permission to guess.
    """
    from plc.validation import parse_device_address, is_bit_device, device_address_radix
    parsed = parse_device_address(base, plc_model)
    if (parsed is None or is_bit_device(base, plc_model)
            or type(words) is not int or words < 1):
        raise ValueError('word_device_region_unverified:' + str(base))
    prefix, start = parsed
    radix = device_address_radix(prefix, plc_model)
    addresses = tuple(prefix + format(start + i, 'X' if radix == 16 else 'o' if radix == 8 else 'd')
                      for i in range(words))
    if any(parse_device_address(address, plc_model) is None for address in addresses):
        raise ValueError('word_device_region_exceeds_allocation:' + str(base))
    return addresses


def digit_specified_devices(value, plc_model="FX3U"):
    """Exact FX3U 16-bit digit specification; X/Y use the CPU's octal radix.

    JY997D16601-R, section 5.4 (PDF p162): Kn names n groups of four consecutive bits.
    Wider, indexed and other CPU forms remain outside this bounded subset.
    """
    match = re.fullmatch(r'K([1-4])([XYMS])(\d+)', str(value).strip(), re.I)
    if match is None:
        return None
    if str(plc_model).upper() != "FX3U":
        raise ValueError('Digit specification runtime is unavailable for this CPU')
    from plc.validation import parse_device_address
    head = parse_device_address(match[2].upper()+match[3], plc_model)
    if head is None:
        raise ValueError('Invalid digit specification head device')
    prefix, start = head
    radix = 'o' if prefix in {'X', 'Y'} else 'd'
    devices = tuple(prefix+format(start+i, radix) for i in range(4*int(match[1])))
    if any(parse_device_address(device, plc_model) is None for device in devices):
        raise ValueError('Digit specification exceeds the CPU device region')
    return devices


def canonical_expression(value):
    if not isinstance(value, str):
        return value
    # Only whole whitespace-delimited operands. In particular quoted strings,
    # K/H constants, bit-group syntax and unrelated labels stay untouched.
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\S+',
                  lambda m: m[0] if m[0].startswith(('"', "'")) else canonical_operand(m[0]), value)


def canonical_device_map(values):
    """Coalesce aliases using the exporter's established first-declaration rule.

    An explicitly empty declaration stays empty. Conflicting source comments
    remain in the immutable source/response audit; they cannot create multiple
    physical devices or override the first declaration through another alias.
    """
    if not isinstance(values, Mapping):
        return copy.deepcopy(values)
    result = {}
    for key, value in values.items():
        identity = canonical_device(key)
        if identity not in result:
            result[identity] = copy.deepcopy(value)
    return result


def canonical_io_rows(rows):
    """Merge zero-padding aliases, retaining row metadata and the chosen label.

    Exact duplicate rows still reach the draft validator before canonicalization.
    This adapter does not treat different physical addresses as interchangeable.
    """
    if not isinstance(rows, list):
        return copy.deepcopy(rows)
    groups, order = {}, []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("address"):
            order.append((False, copy.deepcopy(row)))
            continue
        address = canonical_device(row["address"])
        if address not in groups:
            order.append((True, address))
            groups[address] = []
        groups[address].append(row)
    result = []
    for grouped, entry in order:
        if not grouped:
            result.append(entry)
            continue
        candidates = sorted(groups[entry], key=lambda r: (
            str(r.get("source", "")).casefold() not in {"user", "confirmed"},
            str(r["address"]).strip().upper() != entry,
            not bool(r.get("label")), str(r["address"]),
        ))
        merged = copy.deepcopy(dict(candidates[0]))
        for row in candidates[1:]:
            for key, value in row.items():
                merged.setdefault(key, copy.deepcopy(value))
        merged["address"] = entry
        result.append(merged)
    return result


def canonical_ladder_devices(ladder):
    """Return a copy with consistent address/comment spelling, including ORs."""
    result = copy.deepcopy(ladder)
    if not isinstance(result, dict):
        return result
    if "device_comments" in result:
        result["device_comments"] = canonical_device_map(result["device_comments"])

    def element(item):
        if not isinstance(item, dict):
            return
        if "address" in item:
            item["address"] = canonical_operand(item["address"])
        if item.get("type") in {"COMPARE", "BLOCK_INPUT", "BLOCK_OUTPUT"} and "expression" in item:
            item["expression"] = canonical_expression(item["expression"])
        if item.get("type") in {"TIMER", "COUNTER"} and "value" in item:
            item["value"] = canonical_operand(item["value"])
        if item.get("type") == "APP_INSTR" and isinstance(item.get("operands"), list):
            item["operands"] = [canonical_operand(v) for v in item["operands"]]
        if item.get("type") == "parallel_block" and isinstance(item.get("branches"), list):
            for branch in item["branches"]:
                if isinstance(branch, list):
                    for child in branch:
                        element(child)

    rows = result.get("rungs")
    for rung in rows if isinstance(rows, list) else ():
        if not isinstance(rung, dict):
            continue
        element(rung.get("header_element"))
        shared = rung.get("shared_inputs")
        for item in shared if isinstance(shared, list) else ():
            element(item)
        branches = rung.get("branches")
        for branch in branches if isinstance(branches, list) else ():
            if not isinstance(branch, dict):
                continue
            for field in ("inputs", "outputs"):
                items = branch.get(field)
                for item in items if isinstance(items, list) else ():
                    element(item)
    return result


def canonical_requirement_devices(requirements):
    """Normalize typed requirement device lists, not their IDs or prose."""
    result = copy.deepcopy(requirements)
    if isinstance(result, (list, tuple)):
        for requirement in result:
            if isinstance(requirement, dict) and isinstance(requirement.get("devices"), list):
                requirement["devices"] = list(dict.fromkeys(canonical_device(v) for v in requirement["devices"]))
    return result


def canonical_analysis_devices(config):
    """Normalize address-bearing fields of an already-normalized analysis config."""
    result = copy.deepcopy(config)
    if not isinstance(result, dict):
        return result
    for pair in result.get("mutex", []) or []:
        if isinstance(pair, dict) and isinstance(pair.get("devices"), list):
            pair["devices"] = [canonical_device(v) for v in pair["devices"]]
    for expectation in result.get("same_scan_expectations", []) or []:
        if isinstance(expectation, dict) and "device" in expectation:
            expectation["device"] = canonical_device(expectation["device"])
    if isinstance(result.get("terminal_states"), Mapping):
        result["terminal_states"] = canonical_device_map(result["terminal_states"])
    return result
