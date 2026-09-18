"""Canonical project-level PLC intermediate representation.

The historical model-facing ladder format (``device_comments`` + ``rungs``)
remains a supported interchange format.  New versions are normalized into a
project-level IR immediately after validation.  Renderers consume the IR via
``ir_to_ladder`` so legacy projects and the existing GX Works2/SVG backends do
not need a flag day migration.

Derived fields (network instructions, reads, writes, devices and timing hints)
are deterministic.  They are deliberately recomputed after every patch rather
than trusted from an LLM response.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY
from plc_device_identity import (
    canonical_device, canonical_device_map, canonical_io_rows, canonical_ladder_devices,
    canonical_requirement_devices, canonical_analysis_devices,
)


IR_KIND = "plc_program_ir"
IR_SCHEMA_VERSION = 3

_DEVICE_RE = re.compile(
    r"^(SM|SD|X|Y|M|D|T|C|S|V|Z)(\d+)$", re.IGNORECASE
)
_INDEXED_DEVICE_RE = re.compile(
    r"^(?P<base>(?:SM|SD|X|Y|M|D|T|C|S)\d+)(?P<index>[VZ]\d+)$",
    re.IGNORECASE,
)
_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])"
    r"(?:(?P<base>(?:SM|SD|X|Y|M|D|T|C|S)\d+)(?P<index>[VZ]\d+)"
    r"|(?P<simple>(?:SM|SD|X|Y|M|D|T|C|S|V|Z)\d+))"
    r"(?![A-Z0-9_])",
    re.IGNORECASE,
)
_REVISION_RE = re.compile(r"(\d+)$")


class PLCIRValidationError(ValueError):
    """Raised when an IR or deterministic network patch is inconsistent."""


def canonical_sha256(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def is_plc_ir(payload: Any) -> bool:
    return isinstance(payload, Mapping) and payload.get("kind") == IR_KIND


def _normalized_device(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if _DEVICE_RE.fullmatch(text) else ""


def _device_tokens(value: Any) -> List[str]:
    """Return base devices plus index-register dependencies from an operand."""

    text = str(value or "").upper()
    result: List[str] = []
    for match in _DEVICE_TOKEN_RE.finditer(text):
        if match.group("base"):
            result.append(match.group("base").upper())
            result.append(match.group("index").upper())
        else:
            result.append(match.group("simple").upper())
    return result


def _device_sort_key(value: str) -> Tuple[int, int, str]:
    match = _DEVICE_RE.fullmatch(str(value or "").upper())
    if not match:
        return (999, 0, str(value))
    prefix_order = {
        "X": 0,
        "Y": 1,
        "M": 2,
        "SM": 3,
        "T": 4,
        "C": 5,
        "D": 6,
        "SD": 7,
        "S": 8,
        "V": 9,
        "Z": 10,
    }
    return (prefix_order.get(match.group(1).upper(), 99), int(match.group(2)), value)


def _network_id(rung_id: Any) -> str:
    if isinstance(rung_id, int) and rung_id >= 0:
        return f"N{rung_id:04d}"
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(rung_id or "").strip())
    if not text:
        raise PLCIRValidationError("network rung_id is empty")
    return "N_" + text


def revision_from_value(value: Any, default: int = 1) -> int:
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, int):
        return max(0, value)
    match = _REVISION_RE.search(str(value or "").strip())
    return int(match.group(1)) if match else int(default)


def _plc_descriptor(plc_model: str) -> Dict[str, str]:
    cpu = str(plc_model or "FX3U").strip().upper() or "FX3U"
    if cpu.startswith("FX5"):
        series = "MELSEC iQ-F"
    elif cpu.startswith("FX"):
        series = "FX"
    else:
        series = cpu
    return {"series": series, "cpu": cpu}


def _element_operands(element: Mapping[str, Any]) -> List[str]:
    expression = str(element.get("expression", "") or "").strip()
    element_type = str(element.get("type", "") or "").upper()
    if element_type in {"COMPARE", "BLOCK_INPUT"} or re.search(r"[<=>]", expression):
        parts = expression.split()
        if not parts:
            return []
        if parts[0] in {"=", ">", "<", "<=", ">=", "<>", "=="}:
            return parts[1:]
        if len(parts) >= 3:
            return [parts[0], parts[2]]
        return parts
    address = str(element.get("address", "") or "").strip()
    return [address] if address else []


def _input_opcode(element: Mapping[str, Any], *, first: bool, parallel: bool = False) -> str:
    element_type = str(element.get("type", "") or "").upper()
    expression = str(element.get("expression", "") or "").strip()
    if element_type in {"COMPARE", "BLOCK_INPUT"} or re.search(r"[<=>]", expression):
        match = re.search(r"([<=>]+)", expression)
        symbol = match.group(1) if match else "="
        return ("LD" if first else "OR" if parallel else "AND") + symbol
    if first:
        return {
            "NC": "LDI",
            "P": "LDP",
            "RISING": "LDP",
            "F": "LDF",
            "FALLING": "LDF",
        }.get(element_type, "LD")
    if parallel:
        return {
            "NC": "ORI",
            "P": "ORP",
            "RISING": "ORP",
            "F": "ORF",
            "FALLING": "ORF",
        }.get(element_type, "OR")
    return {
        "NC": "ANI",
        "P": "ANDP",
        "RISING": "ANDP",
        "F": "ANDF",
        "FALLING": "ANDF",
    }.get(element_type, "AND")


def lower_rung_instructions(rung: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Lower one graphical rung to deterministic GX-style instructions."""

    instructions: List[Dict[str, Any]] = []

    def emit(op: str, args: Sequence[Any] = (), path: str = "") -> None:
        item: Dict[str, Any] = {
            "op": str(op or "").strip().upper(),
            "args": [str(value).strip().upper() for value in args if str(value).strip()],
        }
        if path:
            item["path"] = path
        instructions.append(item)

    def parse_inputs(elements: Sequence[Mapping[str, Any]], first_input: bool, base_path: str) -> None:
        for element_index, element in enumerate(elements or []):
            path = f"{base_path}[{element_index}]"
            first = first_input and element_index == 0
            if str(element.get("type", "")) != "parallel_block":
                emit(_input_opcode(element, first=first), _element_operands(element), path)
                continue
            branches = [branch for branch in (element.get("branches", []) or []) if branch]
            if not branches:
                continue
            if len(branches) == 1:
                for child_index, child in enumerate(branches[0]):
                    emit(
                        _input_opcode(child, first=first and child_index == 0),
                        _element_operands(child),
                        f"{path}.branches[0][{child_index}]",
                    )
                continue
            for branch_index, branch in enumerate(branches):
                if len(branch) == 1:
                    child = branch[0]
                    emit(
                        _input_opcode(
                            child,
                            first=branch_index == 0,
                            parallel=branch_index > 0,
                        ),
                        _element_operands(child),
                        f"{path}.branches[{branch_index}][0]",
                    )
                    continue
                for child_index, child in enumerate(branch):
                    emit(
                        _input_opcode(child, first=child_index == 0),
                        _element_operands(child),
                        f"{path}.branches[{branch_index}][{child_index}]",
                    )
                if branch_index > 0:
                    emit("ORB", path=f"{path}.branches[{branch_index}]")
            if not first:
                emit("ANB", path=path)

    def parse_outputs(outputs: Sequence[Mapping[str, Any]], base_path: str) -> None:
        for output_index, output in enumerate(outputs or []):
            path = f"{base_path}[{output_index}]"
            output_type = str(output.get("type", "") or "").upper()
            if output_type == "COIL":
                emit("OUT", [output.get("address", "")], path)
            elif output_type in {"PLS", "PLF"}:
                emit(output_type, [output.get("address", "")], path)
            elif output_type in {"TIMER", "COUNTER"}:
                emit("OUT", [output.get("address", ""), output.get("value", "K0")], path)
            elif output_type == "APP_INSTR":
                emit(output.get("opcode", ""), output.get("operands", []) or [], path)
            elif output_type == "BLOCK_OUTPUT":
                parts = str(output.get("expression", "") or "").strip().split()
                if parts:
                    emit(parts[0], parts[1:], path)

    has_prefix = False
    header = rung.get("header_element")
    if isinstance(header, Mapping):
        emit(_input_opcode(header, first=True), _element_operands(header), "header_element")
        has_prefix = True
    shared = rung.get("shared_inputs", []) or []
    if shared:
        parse_inputs(shared, not has_prefix, "shared_inputs")
        has_prefix = True
    branches = rung.get("branches", []) or []
    if len(branches) == 1:
        branch = branches[0]
        parse_inputs(branch.get("inputs", []) or [], not has_prefix, "branches[0].inputs")
        parse_outputs(branch.get("outputs", []) or [], "branches[0].outputs")
    elif len(branches) > 1 and has_prefix:
        emit("MPS", path="branches")
        for branch_index, branch in enumerate(branches):
            if 0 < branch_index < len(branches) - 1:
                emit("MRD", path=f"branches[{branch_index}]")
            elif branch_index == len(branches) - 1:
                emit("MPP", path=f"branches[{branch_index}]")
            parse_inputs(
                branch.get("inputs", []) or [],
                False,
                f"branches[{branch_index}].inputs",
            )
            parse_outputs(
                branch.get("outputs", []) or [],
                f"branches[{branch_index}].outputs",
            )
    else:
        for branch_index, branch in enumerate(branches):
            parse_inputs(
                branch.get("inputs", []) or [],
                True,
                f"branches[{branch_index}].inputs",
            )
            parse_outputs(
                branch.get("outputs", []) or [],
                f"branches[{branch_index}].outputs",
            )
    return instructions


def _walk_input_elements(elements: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for element in elements or []:
        if not isinstance(element, Mapping):
            continue
        if str(element.get("type", "")) == "parallel_block":
            for branch in element.get("branches", []) or []:
                yield from _walk_input_elements(branch)
        else:
            yield element


def _instruction_access(opcode: Any, operands: Sequence[Any]) -> Tuple[Set[str], Set[str]]:
    """Return conservative access semantics using the shared instruction registry.

    A catalogue miss is intentionally not treated as a parse failure.  Unknown
    vendor instructions keep all device operands on the read side and never
    guess writes.  That makes imported GX programs preservable while avoiding
    unsafe dependency claims.
    """

    op = str(opcode or "").strip().upper()
    reads: Set[str] = set()
    writes: Set[str] = set()
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(op)
    write_indexes = set(spec.write_indexes if spec is not None else ())
    read_write_indexes = set(spec.read_write_indexes if spec is not None else ())
    for index, operand in enumerate(operands or []):
        text = str(operand or "").strip().upper()
        indexed = _INDEXED_DEVICE_RE.fullmatch(text)
        if indexed:
            base = indexed.group("base").upper()
            index_register = indexed.group("index").upper()
            # The index register is always read to resolve the effective
            # address.  For a write operand, only the base memory family is
            # conservatively recorded as written; the external operand string
            # remains D100Z0/X0V1/etc. in ladder/CSV artifacts.
            reads.add(index_register)
            if index in write_indexes:
                writes.add(base)
                if index in read_write_indexes:
                    reads.add(base)
            else:
                reads.add(base)
            continue

        tokens = set(_device_tokens(operand))
        if index in write_indexes:
            writes.update(tokens)
            if index in read_write_indexes:
                reads.update(tokens)
        else:
            reads.update(tokens)
    return reads, writes


def analyze_instruction_access(
    opcode: Any, operands: Sequence[Any]
) -> Tuple[List[str], List[str]]:
    """Return deterministic device reads/writes for one lowered instruction."""

    op = str(opcode or "").strip().upper()
    args = list(operands or [])
    reads: Set[str] = set()
    writes: Set[str] = set()
    if op.startswith(("LD", "AND", "OR")):
        for operand in args:
            reads.update(_device_tokens(operand))
    elif op == "OUT":
        if args:
            address = _normalized_device(args[0])
            if address:
                writes.add(address)
        for operand in args[1:]:
            reads.update(_device_tokens(operand))
    elif op in {"SET", "RST", "PLS", "PLF"}:
        if args:
            address = _normalized_device(args[0])
            if address:
                writes.add(address)
    elif op not in {"ANB", "ORB", "MPS", "MRD", "MPP", "INV", "NOP"}:
        reads, writes = _instruction_access(op, args)
    return (
        sorted(reads, key=_device_sort_key),
        sorted(writes, key=_device_sort_key),
    )


def analyze_rung_access(rung: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    reads: Set[str] = set()
    writes: Set[str] = set()
    condition_elements: List[Mapping[str, Any]] = []
    header = rung.get("header_element")
    if isinstance(header, Mapping):
        condition_elements.append(header)
    condition_elements.extend(_walk_input_elements(rung.get("shared_inputs", []) or []))
    for branch in rung.get("branches", []) or []:
        condition_elements.extend(_walk_input_elements(branch.get("inputs", []) or []))
    for element in condition_elements:
        reads.update(_device_tokens(element.get("address", "")))
        reads.update(_device_tokens(element.get("expression", "")))

    for branch in rung.get("branches", []) or []:
        for output in branch.get("outputs", []) or []:
            output_type = str(output.get("type", "") or "").upper()
            if output_type in {"COIL", "PLS", "PLF", "TIMER", "COUNTER"}:
                address = _normalized_device(output.get("address"))
                if address:
                    writes.add(address)
                if output_type in {"TIMER", "COUNTER"}:
                    reads.update(_device_tokens(output.get("value", "")))
            elif output_type == "APP_INSTR":
                local_reads, local_writes = _instruction_access(
                    output.get("opcode"), output.get("operands", []) or []
                )
                reads.update(local_reads)
                writes.update(local_writes)
            elif output_type == "BLOCK_OUTPUT":
                parts = str(output.get("expression", "") or "").strip().split()
                if parts:
                    local_reads, local_writes = _instruction_access(parts[0], parts[1:])
                    reads.update(local_reads)
                    writes.update(local_writes)
    return (
        sorted(reads, key=_device_sort_key),
        sorted(writes, key=_device_sort_key),
    )


def _normalize_io_map(
    confirmed_spec: Optional[Mapping[str, Any]] = None,
    io_map: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    if isinstance(io_map, Mapping):
        for address, value in io_map.items():
            row = dict(value) if isinstance(value, Mapping) else {"label": str(value)}
            row.setdefault("address", address)
            rows.append(row)
    elif isinstance(io_map, list):
        rows.extend(item for item in io_map if isinstance(item, Mapping))
    elif isinstance(confirmed_spec, Mapping):
        rows.extend(
            item
            for item in (confirmed_spec.get("io_table", []) or [])
            if isinstance(item, Mapping)
        )

    normalized: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        address = _normalized_device(row.get("address"))
        if not address:
            continue
        prefix_match = _DEVICE_RE.fullmatch(address)
        value = {
            "kind": str(row.get("kind") or prefix_match.group(1)).strip().upper(),
            "label": str(row.get("label") or row.get("description") or "").strip(),
            "source": str(row.get("source") or "confirmed").strip(),
        }
        normalized[address] = value
    return dict(sorted(normalized.items(), key=lambda item: _device_sort_key(item[0])))


def build_plc_ir(
    ladder: Mapping[str, Any],
    *,
    plc_model: str = "FX3U",
    program_name: str = "MAIN",
    revision: Any = 1,
    confirmed_spec: Optional[Mapping[str, Any]] = None,
    io_map: Optional[Any] = None,
    semantic_requirements: Optional[Sequence[Mapping[str, Any]]] = None,
    analysis_config: Optional[Mapping[str, Any]] = None,
    _canonicalize_devices: bool = True,
) -> Dict[str, Any]:
    if is_plc_ir(ladder):
        if semantic_requirements is None:
            logic = ladder.get("logic")
            if isinstance(logic, Mapping):
                semantic_requirements = logic.get("requirements")
        if analysis_config is None:
            analysis = ladder.get("analysis")
            if isinstance(analysis, Mapping):
                analysis_config = analysis.get("config")
        ladder = ir_to_ladder(ladder)
    if not isinstance(ladder, Mapping):
        raise PLCIRValidationError("ladder must be an object")
    if _canonicalize_devices:
        ladder = canonical_ladder_devices(ladder)
        semantic_requirements = canonical_requirement_devices(semantic_requirements)
        if isinstance(io_map, Mapping):
            io_map = canonical_device_map(io_map)
            io_map = {address: {**value, "address": address} if isinstance(value, Mapping) else value
                      for address, value in io_map.items()}
        elif isinstance(io_map, list):
            io_map = canonical_io_rows(io_map)
        elif isinstance(confirmed_spec, Mapping):
            io_map = canonical_io_rows(list(confirmed_spec.get("io_table", []) or []))
    rungs = ladder.get("rungs")
    comments = ladder.get("device_comments")
    if not isinstance(rungs, list) or not isinstance(comments, Mapping):
        raise PLCIRValidationError("ladder requires device_comments and rungs")

    networks: List[Dict[str, Any]] = []
    seen_networks: Set[str] = set()
    for order, raw_rung in enumerate(rungs):
        if not isinstance(raw_rung, Mapping):
            raise PLCIRValidationError(f"rungs[{order}] must be an object")
        rung = copy.deepcopy(dict(raw_rung))
        network_id = _network_id(rung.get("rung_id"))
        if network_id in seen_networks:
            raise PLCIRValidationError(f"duplicate network id {network_id}")
        seen_networks.add(network_id)
        reads, writes = analyze_rung_access(rung)
        networks.append(
            {
                "id": network_id,
                "order": order,
                "rung_id": rung.get("rung_id"),
                "comment": str(rung.get("debug_note", "") or ""),
                "instructions": lower_rung_instructions(rung),
                "reads": reads,
                "writes": writes,
                "ladder": rung,
            }
        )

    from plc_semantics import analyze_program_semantics

    semantic_analysis = analyze_program_semantics(
        networks,
        plc_model=plc_model,
        requirements=semantic_requirements,
    )
    networks = semantic_analysis["networks"]

    normalized_comments = {
        str(address).strip().upper(): str(comment)
        for address, comment in comments.items()
        if _normalized_device(address)
    }
    normalized_io = _normalize_io_map(confirmed_spec, io_map)
    readers: Dict[str, List[str]] = {}
    writers: Dict[str, List[str]] = {}
    for network in networks:
        for address in network["reads"]:
            readers.setdefault(address, []).append(network["id"])
        for address in network["writes"]:
            writers.setdefault(address, []).append(network["id"])

    all_addresses = set(readers) | set(writers) | {
        address
        for address in (_normalized_device(value) for value in normalized_comments)
        if address
    } | set(normalized_io)
    devices: Dict[str, Dict[str, Any]] = {}
    for address in sorted(all_addresses, key=_device_sort_key):
        prefix = _DEVICE_RE.fullmatch(address).group(1).upper()
        read_by = readers.get(address, [])
        written_by = writers.get(address, [])
        if read_by and written_by:
            access = "read_write"
        elif written_by:
            access = "write"
        elif read_by:
            access = "read"
        else:
            access = "declared"
        declaration = normalized_io.get(address, {})
        devices[address] = {
            "kind": prefix,
            "comment": str(normalized_comments.get(address, "") or ""),
            "comment_declared": address in normalized_comments,
            "access": access,
            "read_by": list(read_by),
            "written_by": list(written_by),
        }
        if declaration:
            devices[address]["io"] = copy.deepcopy(declaration)

    from plc_static_analyzer import analyze_static_program, normalize_analysis_config
    from plc_timing import analyze_scan_timing, assess_pulse_capture

    normalized_analysis_config = normalize_analysis_config(
        analysis_config,
        confirmed_spec=confirmed_spec,
        devices=devices,
    )
    if _canonicalize_devices:
        normalized_analysis_config = canonical_analysis_devices(normalized_analysis_config)
    timing = copy.deepcopy(semantic_analysis["timing"])
    timing_config = normalized_analysis_config.get("timing") or {}
    timing["performance"] = analyze_scan_timing(
        networks,
        plc_model=plc_model,
        scan_budget_ms=timing_config.get("scan_budget_ms"),
        scan_warning_ms=timing_config.get("scan_warning_ms", 15.0),
        allocation=timing_config.get("allocation") or {},
    )
    timing["pulse_capture_assessments"] = assess_pulse_capture(
        timing.get("requirements") or [],
        timing.get("coverage") or [],
        timing["performance"],
    )
    static_analysis = analyze_static_program(
        networks,
        devices=devices,
        timing=timing,
        logic=semantic_analysis["logic"],
        config=normalized_analysis_config,
        plc_model=plc_model,
    )

    normalized_name = str(program_name or "MAIN").strip() or "MAIN"
    payload: Dict[str, Any] = {
        "kind": IR_KIND,
        "schema_version": IR_SCHEMA_VERSION,
        "plc": _plc_descriptor(plc_model),
        "program_name": normalized_name,
        "revision": revision_from_value(revision),
        "networks": networks,
        "devices": devices,
        "timing": timing,
        "logic": semantic_analysis["logic"],
        "analysis": static_analysis,
        "io_map": normalized_io,
        "source": {
            "format": "ladder_v1",
            "ladder_sha256": canonical_sha256(
                {"device_comments": normalized_comments, "rungs": rungs}
            ),
        },
    }
    return payload


def ir_to_ladder(program: Mapping[str, Any]) -> Dict[str, Any]:
    if not is_plc_ir(program):
        if isinstance(program, Mapping) and isinstance(program.get("rungs"), list):
            return copy.deepcopy(dict(program))
        raise PLCIRValidationError("payload is neither PLC IR nor ladder JSON")
    comments = {
        str(address): str(record.get("comment", "") or "")
        for address, record in (program.get("devices", {}) or {}).items()
        if isinstance(record, Mapping)
        and (
            bool(record.get("comment_declared"))
            or bool(str(record.get("comment", "") or ""))
        )
    }
    networks = sorted(
        program.get("networks", []) or [],
        key=lambda item: int(item.get("order", 0)) if isinstance(item, Mapping) else 0,
    )
    rungs = []
    for network in networks:
        if not isinstance(network, Mapping) or not isinstance(network.get("ladder"), Mapping):
            raise PLCIRValidationError("each network requires a ladder object")
        rungs.append(copy.deepcopy(dict(network["ladder"])))
    return {"device_comments": comments, "rungs": rungs}


def validate_plc_ir(
    program: Mapping[str, Any],
    *,
    confirmed_spec: Optional[Mapping[str, Any]] = None,
    validate_ladder: bool = True,
    require_catalogued_instructions: bool = True,
) -> Mapping[str, Any]:
    if not is_plc_ir(program):
        raise PLCIRValidationError(f"kind must be {IR_KIND!r}")
    if program.get("schema_version") != IR_SCHEMA_VERSION:
        raise PLCIRValidationError(
            f"unsupported schema_version {program.get('schema_version')!r}"
        )
    plc = program.get("plc")
    if not isinstance(plc, Mapping) or not str(plc.get("cpu", "")).strip():
        raise PLCIRValidationError("plc.cpu is required")
    if not str(program.get("program_name", "")).strip():
        raise PLCIRValidationError("program_name is required")
    revision = program.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PLCIRValidationError("revision must be a non-negative integer")
    networks = program.get("networks")
    if not isinstance(networks, list):
        raise PLCIRValidationError("networks must be a list")
    seen_ids: Set[str] = set()
    seen_orders: Set[int] = set()
    for index, network in enumerate(networks):
        if not isinstance(network, Mapping):
            raise PLCIRValidationError(f"networks[{index}] must be an object")
        network_id = str(network.get("id", ""))
        if not network_id or network_id in seen_ids:
            raise PLCIRValidationError(f"invalid or duplicate network id {network_id!r}")
        seen_ids.add(network_id)
        order = network.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or order in seen_orders:
            raise PLCIRValidationError(f"network {network_id} has invalid order")
        seen_orders.add(order)
        rung = network.get("ladder")
        if not isinstance(rung, Mapping):
            raise PLCIRValidationError(f"network {network_id} requires ladder")
        if _network_id(rung.get("rung_id")) != network_id:
            raise PLCIRValidationError(f"network {network_id} does not match rung_id")
        expected_instructions = lower_rung_instructions(rung)
        if network.get("instructions") != expected_instructions:
            raise PLCIRValidationError(f"network {network_id} instructions are stale")
        expected_reads, expected_writes = analyze_rung_access(rung)
        if network.get("reads") != expected_reads:
            raise PLCIRValidationError(f"network {network_id} reads are stale")
        if network.get("writes") != expected_writes:
            raise PLCIRValidationError(f"network {network_id} writes are stale")
    if seen_orders != set(range(len(networks))):
        raise PLCIRValidationError("network order values must be contiguous from zero")
    if not isinstance(program.get("devices"), Mapping):
        raise PLCIRValidationError("devices must be an object")
    if not isinstance(program.get("timing"), Mapping):
        raise PLCIRValidationError("timing must be an object")
    if not isinstance(program.get("logic"), Mapping):
        raise PLCIRValidationError("logic must be an object")
    if not isinstance(program.get("analysis"), Mapping):
        raise PLCIRValidationError("analysis must be an object")
    if not isinstance(program.get("io_map"), Mapping):
        raise PLCIRValidationError("io_map must be an object")
    ladder = ir_to_ladder(program)
    source = program.get("source", {}) or {}
    expected_hash = str(source.get("ladder_sha256", "") or "")
    if expected_hash and expected_hash != canonical_sha256(ladder):
        raise PLCIRValidationError("source.ladder_sha256 does not match IR contents")
    rebuilt = build_plc_ir(
        ladder,
        plc_model=str(plc.get("cpu", "FX3U")),
        program_name=str(program.get("program_name", "MAIN")),
        revision=revision,
        io_map=program.get("io_map", {}),
        semantic_requirements=(program.get("logic") or {}).get("requirements", []),
        analysis_config=(program.get("analysis") or {}).get("config", {}),
        _canonicalize_devices=False,
    )
    for field in (
        "networks", "devices", "timing", "logic", "analysis", "io_map", "source"
    ):
        if program.get(field) != rebuilt.get(field):
            raise PLCIRValidationError(f"{field} is stale or inconsistent")
    if validate_ladder:
        from plc_json_validator import validate_ladder_full

        validate_ladder_full(
            ladder,
            plc_model=str(plc.get("cpu", "FX3U")),
            confirmed_spec=confirmed_spec,
            require_catalogued_instructions=require_catalogued_instructions,
        )
    return program


def apply_network_patch(program: Mapping[str, Any], patch: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply a deterministic network-scoped patch and recompute all analysis."""

    validate_plc_ir(program, validate_ladder=False)
    if not isinstance(patch, Mapping):
        raise PLCIRValidationError("patch must be an object")
    base_revision = patch.get("base_revision")
    if base_revision is not None and base_revision != program.get("revision"):
        raise PLCIRValidationError(
            f"stale patch revision {base_revision!r}; current revision is {program.get('revision')}"
        )
    base_sha256 = str(patch.get("base_ir_sha256", "") or "")
    if base_sha256 and base_sha256 != canonical_sha256(program):
        raise PLCIRValidationError("stale patch base_ir_sha256")
    operations = patch.get("operations")
    if not isinstance(operations, list) or not operations:
        raise PLCIRValidationError("patch.operations must be a non-empty list")

    ladder = ir_to_ladder(program)
    rungs = ladder["rungs"]

    def index_by_network() -> Dict[str, int]:
        return {_network_id(rung.get("rung_id")): index for index, rung in enumerate(rungs)}

    for operation_index, raw_operation in enumerate(operations):
        if not isinstance(raw_operation, Mapping):
            raise PLCIRValidationError(f"operations[{operation_index}] must be an object")
        operation = str(raw_operation.get("operation", "") or "").strip().lower()
        indexes = index_by_network()
        target = str(raw_operation.get("network", "") or "").strip()
        if operation in {"modify_network", "replace_network"}:
            if target not in indexes:
                raise PLCIRValidationError(f"unknown network {target!r}")
            replacement = raw_operation.get("ladder")
            if not isinstance(replacement, Mapping):
                network_data = raw_operation.get("network_data")
                replacement = (
                    network_data.get("ladder")
                    if isinstance(network_data, Mapping)
                    else None
                )
            if not isinstance(replacement, Mapping):
                raise PLCIRValidationError(f"{operation} requires ladder")
            replacement = copy.deepcopy(dict(replacement))
            if _network_id(replacement.get("rung_id")) != target:
                raise PLCIRValidationError(
                    f"replacement rung_id must preserve network {target}"
                )
            rungs[indexes[target]] = replacement
        elif operation == "delete_network":
            if target not in indexes:
                raise PLCIRValidationError(f"unknown network {target!r}")
            del rungs[indexes[target]]
        elif operation == "add_network":
            replacement = raw_operation.get("ladder")
            if not isinstance(replacement, Mapping):
                network_data = raw_operation.get("network_data")
                replacement = (
                    network_data.get("ladder")
                    if isinstance(network_data, Mapping)
                    else None
                )
            if not isinstance(replacement, Mapping):
                raise PLCIRValidationError("add_network requires ladder")
            replacement = copy.deepcopy(dict(replacement))
            new_id = _network_id(replacement.get("rung_id"))
            if new_id in indexes:
                raise PLCIRValidationError(f"network {new_id} already exists")
            after = str(raw_operation.get("after", "") or "").strip()
            if after:
                if after not in indexes:
                    raise PLCIRValidationError(f"unknown after network {after!r}")
                rungs.insert(indexes[after] + 1, replacement)
            else:
                rungs.append(replacement)
        else:
            raise PLCIRValidationError(f"unsupported patch operation {operation!r}")

    comment_updates = patch.get("device_comments", {}) or {}
    if not isinstance(comment_updates, Mapping):
        raise PLCIRValidationError("device_comments patch must be an object")
    for raw_address, raw_comment in comment_updates.items():
        address = _normalized_device(raw_address)
        if not address:
            raise PLCIRValidationError(f"invalid comment address {raw_address!r}")
        # A patch key is an explicit update to one physical device. Remove its
        # old padding aliases before applying it, including explicit deletion.
        address = canonical_device(address)
        for alias in list(ladder["device_comments"]):
            if canonical_device(alias) == address:
                del ladder["device_comments"][alias]
        if raw_comment is not None:
            ladder["device_comments"][address] = str(raw_comment)

    current_revision = int(program.get("revision", 0))
    target_revision = patch.get("target_revision", current_revision + 1)
    if isinstance(target_revision, bool) or not isinstance(target_revision, int):
        raise PLCIRValidationError("target_revision must be an integer")
    if target_revision <= current_revision:
        raise PLCIRValidationError("target_revision must increase")
    rebuilt = build_plc_ir(
        ladder,
        plc_model=str(program.get("plc", {}).get("cpu", "FX3U")),
        program_name=str(program.get("program_name", "MAIN")),
        revision=target_revision,
        io_map=program.get("io_map", {}),
        semantic_requirements=(program.get("logic") or {}).get("requirements", []),
        analysis_config=(program.get("analysis") or {}).get("config", {}),
        _canonicalize_devices=False,
    )
    validate_plc_ir(rebuilt)
    return rebuilt


def apply_ladder_partial_to_ir(
    program: Mapping[str, Any],
    partial: Mapping[str, Any],
    *,
    target_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Compatibility bridge from the existing LLM partial-rung response."""

    if not isinstance(partial, Mapping) or partial.get("mode") != "partial":
        raise PLCIRValidationError('partial ladder update requires mode="partial"')
    operations: List[Dict[str, Any]] = []
    existing = {
        network["id"]
        for network in program.get("networks", []) or []
        if isinstance(network, Mapping)
    }
    for rung in partial.get("rungs", []) or []:
        network_id = _network_id(rung.get("rung_id"))
        operations.append(
            {
                "operation": "modify_network" if network_id in existing else "add_network",
                "network": network_id,
                "ladder": rung,
            }
        )
    for rung_id in partial.get("delete_rung_ids", []) or []:
        operations.append(
            {"operation": "delete_network", "network": _network_id(rung_id)}
        )
    if not operations:
        # A comments-only patch still needs one deterministic operation.  Reuse
        # the first network without changing it; the rebuilt IR remains exact.
        first = (program.get("networks", []) or [None])[0]
        if not isinstance(first, Mapping):
            raise PLCIRValidationError("cannot apply comments-only patch to an empty program")
        operations.append(
            {
                "operation": "modify_network",
                "network": first["id"],
                "ladder": first["ladder"],
            }
        )
    patch: Dict[str, Any] = {
        "base_revision": program.get("revision"),
        "base_ir_sha256": canonical_sha256(program),
        "operations": operations,
        "device_comments": partial.get("device_comments", {}) or {},
    }
    if target_revision is not None:
        patch["target_revision"] = target_revision
    return apply_network_patch(program, patch)


__all__ = [
    "IR_KIND",
    "IR_SCHEMA_VERSION",
    "PLCIRValidationError",
    "analyze_instruction_access",
    "analyze_rung_access",
    "apply_ladder_partial_to_ir",
    "apply_network_patch",
    "build_plc_ir",
    "canonical_sha256",
    "ir_to_ladder",
    "is_plc_ir",
    "lower_rung_instructions",
    "revision_from_value",
    "validate_plc_ir",
]
