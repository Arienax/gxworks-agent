"""Deterministic GX Works2 Structured Text rendering from canonical PLC IR.

The renderer follows the FXCPU Structured Programming Manual convention that
the rung result is passed as the first ``EN`` argument of basic/applied
instructions.  It consumes the embedded ladder network because that structure
retains branch grouping; the emitted network IDs make every ST statement
traceable back to the canonical IR.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Sequence, Set, Tuple

from plc.ir import PLCIRValidationError, is_plc_ir, validate_plc_ir


ST_RENDERER_SCHEMA_VERSION = 1

_COMPARE_RE = re.compile(r"^(<=|>=|<>|==|=|<|>)\s+([^\s]+)\s+([^\s]+)$")
_BIT_DEVICE_RE = re.compile(r"^(?:SM|X|Y|M|S)\d+(?:\.[0-9A-F])?$", re.I)
_TIMER_RE = re.compile(r"^T(\d+)$", re.I)
_COUNTER_RE = re.compile(r"^C(\d+)$", re.I)
_ST_IDENTIFIER_RE = re.compile(r"^[A-Z_][A-Z0-9_]*(?:\.[0-9A-F])?$", re.I)
_ST_OPERAND_RE = re.compile(
    r"^(?:(?:(?:SM|SD|X|Y|M|D|T|C|S|R)\d+(?:[VZ]\d+)?|"
    r"(?:V|Z)\d+)(?:\.[0-9A-F])?|"
    r"K-?\d+|H[0-9A-F]+|E[-+]?\d+(?:\.\d+)?|"
    r"K\d+(?:X|Y|M|S)\d+|[PI]\d+)$",
    re.I,
)


class STTranslationError(PLCIRValidationError):
    """Raised when the IR contains a construct with no safe ST lowering."""


def _escape_comment(value: Any) -> str:
    return " ".join(str(value or "").replace("*)", "* )").split())


def _st_operand(value: Any, path: str) -> str:
    text = str(value or "").strip().upper()
    if not text or not _ST_OPERAND_RE.fullmatch(text):
        raise STTranslationError(f"{path}: unsupported ST operand {value!r}")
    # GX Works2 ST examples use plain decimal values where a ladder mnemonic
    # uses K constants; hexadecimal and device syntax remain unchanged.
    if text.startswith("K") and re.fullmatch(r"K-?\d+", text):
        return text[1:]
    return text


def _bit_reference(address: Any, path: str) -> str:
    text = str(address or "").strip().upper()
    timer = _TIMER_RE.fullmatch(text)
    if timer:
        return f"TS{timer.group(1)}"
    counter = _COUNTER_RE.fullmatch(text)
    if counter:
        return f"CS{counter.group(1)}"
    if not _BIT_DEVICE_RE.fullmatch(text):
        raise STTranslationError(f"{path}: {address!r} is not a bit contact")
    return text


def _compare_expression(expression: Any, path: str) -> str:
    text = " ".join(str(expression or "").strip().upper().split())
    match = _COMPARE_RE.fullmatch(text)
    if not match:
        raise STTranslationError(
            f"{path}: comparison must be '<operator> <left> <right>'"
        )
    operator, left, right = match.groups()
    if operator == "==":
        operator = "="
    return (
        f"({_st_operand(left, path + '.left')} {operator} "
        f"{_st_operand(right, path + '.right')})"
    )


def _element_expression(element: Mapping[str, Any], path: str) -> str:
    element_type = str(element.get("type", "") or "").upper()
    if element_type in {"COMPARE", "BLOCK_INPUT"}:
        return _compare_expression(element.get("expression"), path + ".expression")
    address = _bit_reference(element.get("address"), path + ".address")
    if element_type == "NC":
        return f"NOT {address}"
    if element_type in {"P", "RISING"}:
        return f"LDP(TRUE, {address})"
    if element_type in {"F", "FALLING"}:
        return f"LDF(TRUE, {address})"
    if element_type == "NO":
        return address
    raise STTranslationError(f"{path}: unsupported input type {element_type!r}")


def _and(parts: Sequence[str]) -> str:
    clean = [str(part) for part in parts if str(part)]
    if not clean:
        return "TRUE"
    if len(clean) == 1:
        return clean[0]
    return " AND ".join(f"({part})" for part in clean)


def _or(parts: Sequence[str]) -> str:
    clean = [str(part) for part in parts if str(part)]
    if not clean:
        return "FALSE"
    if len(clean) == 1:
        return clean[0]
    return " OR ".join(f"({part})" for part in clean)


def _input_list_expression(
    elements: Sequence[Mapping[str, Any]], path: str
) -> str:
    terms: List[str] = []
    for index, element in enumerate(elements or []):
        element_path = f"{path}[{index}]"
        if str(element.get("type", "") or "") != "parallel_block":
            terms.append(_element_expression(element, element_path))
            continue
        branches = element.get("branches", []) or []
        if not branches:
            raise STTranslationError(
                f"{element_path}.branches: parallel block cannot be empty"
            )
        terms.append(
            _or(
                [
                    _input_list_expression(
                        branch,
                        f"{element_path}.branches[{branch_index}]",
                    )
                    for branch_index, branch in enumerate(branches)
                ]
            )
        )
    return _and(terms)


def _counter_coil(address: Any, path: str) -> Tuple[str, bool]:
    text = str(address or "").strip().upper()
    match = _COUNTER_RE.fullmatch(text)
    if not match:
        raise STTranslationError(f"{path}: invalid counter address {address!r}")
    number = int(match.group(1))
    return f"CC{number}", number >= 200


def _timer_coil(address: Any, path: str) -> str:
    text = str(address or "").strip().upper()
    match = _TIMER_RE.fullmatch(text)
    if not match:
        raise STTranslationError(f"{path}: invalid timer address {address!r}")
    return f"TC{match.group(1)}"


def _function_statement(
    condition: str,
    opcode: Any,
    operands: Sequence[Any],
    path: str,
) -> str:
    op = str(opcode or "").strip().upper()
    if not _ST_IDENTIFIER_RE.fullmatch(op):
        raise STTranslationError(f"{path}.opcode: unsupported opcode {opcode!r}")
    if op == "END":
        raise STTranslationError(f"{path}.opcode: END is implicit in a structured POU")
    args = [_st_operand(value, f"{path}.operands[{index}]") for index, value in enumerate(operands or [])]
    arguments = ", ".join([condition, *args])
    return f"{op}({arguments});"


def _output_statement(
    condition: str,
    output: Mapping[str, Any],
    path: str,
) -> str:
    output_type = str(output.get("type", "") or "").upper()
    if output_type == "COIL":
        target = _bit_reference(output.get("address"), path + ".address")
        return f"OUT({condition}, {target});"
    if output_type in {"PLS", "PLF"}:
        target = _bit_reference(output.get("address"), path + ".address")
        return f"{output_type}({condition}, {target});"
    if output_type == "TIMER":
        coil = _timer_coil(output.get("address"), path + ".address")
        preset = _st_operand(output.get("value"), path + ".value")
        return f"OUT_T({condition}, {coil}, {preset});"
    if output_type == "COUNTER":
        coil, double_word = _counter_coil(output.get("address"), path + ".address")
        preset = _st_operand(output.get("value"), path + ".value")
        opcode = "OUT_C_32" if double_word else "OUT_C"
        return f"{opcode}({condition}, {coil}, {preset});"
    if output_type == "APP_INSTR":
        return _function_statement(
            condition,
            output.get("opcode"),
            output.get("operands", []) or [],
            path,
        )
    if output_type == "BLOCK_OUTPUT":
        parts = str(output.get("expression", "") or "").strip().split()
        if not parts:
            raise STTranslationError(f"{path}.expression: empty block output")
        return _function_statement(condition, parts[0], parts[1:], path)
    raise STTranslationError(f"{path}: unsupported output type {output_type!r}")


def render_network_st(network: Mapping[str, Any]) -> str:
    """Render one canonical network without changing its execution order."""

    network_id = str(network.get("id", "") or "")
    rung = network.get("ladder")
    if not isinstance(rung, Mapping):
        raise STTranslationError(f"network {network_id} has no ladder structure")

    common_parts: List[str] = []
    header = rung.get("header_element")
    if isinstance(header, Mapping):
        common_parts.append(_element_expression(header, f"{network_id}.header_element"))
    shared = rung.get("shared_inputs", []) or []
    if shared:
        common_parts.append(
            _input_list_expression(shared, f"{network_id}.shared_inputs")
        )
    common = _and(common_parts)

    statements: List[str] = []
    branches = rung.get("branches", []) or []
    for branch_index, branch in enumerate(branches):
        inputs = branch.get("inputs", []) or []
        condition_parts = []
        if common_parts:
            condition_parts.append(common)
        if inputs:
            condition_parts.append(
                _input_list_expression(
                    inputs,
                    f"{network_id}.branches[{branch_index}].inputs",
                )
            )
        condition = _and(condition_parts)
        for output_index, output in enumerate(branch.get("outputs", []) or []):
            statements.append(
                _output_statement(
                    condition,
                    output,
                    f"{network_id}.branches[{branch_index}].outputs[{output_index}]",
                )
            )
    return "\n".join(statements)


def render_plc_ir_to_st(program: Mapping[str, Any]) -> str:
    """Render a complete PLC IR as GX Works2 ST with network trace markers."""

    if not is_plc_ir(program):
        raise STTranslationError("ST renderer requires canonical PLC IR")
    validate_plc_ir(program, validate_ladder=False)
    lines = [
        "(* Generated deterministically from PLC IR. *)",
        f"(* Program: {_escape_comment(program.get('program_name', 'MAIN'))}; "
        f"Revision: {program.get('revision', 0)}; "
        f"IR schema: {program.get('schema_version')} *)",
        "",
    ]
    networks = sorted(
        program.get("networks", []) or [], key=lambda item: int(item.get("order", 0))
    )
    for network in networks:
        marker = str(network.get("id", "") or "")
        comment = _escape_comment(network.get("comment", ""))
        lines.append(f"(* NETWORK {marker}{' - ' + comment if comment else ''} *)")
        rendered = render_network_st(network)
        if rendered:
            lines.extend(rendered.splitlines())
        else:
            lines.append("(* Empty network *)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def network_markers(st_text: str) -> Set[str]:
    return set(re.findall(r"\(\*\s+NETWORK\s+(N[A-Za-z0-9_-]+)", str(st_text)))


def validate_st_traceability(program: Mapping[str, Any], st_text: str) -> bool:
    """Verify that every IR network appears exactly once in the rendered ST."""

    expected = [str(item.get("id", "")) for item in program.get("networks", []) or []]
    observed = re.findall(r"\(\*\s+NETWORK\s+(N[A-Za-z0-9_-]+)", str(st_text))
    if observed != expected:
        raise STTranslationError(
            f"ST network markers {observed!r} do not match IR order {expected!r}"
        )
    if not str(st_text).rstrip().endswith(";") and expected:
        raise STTranslationError("rendered ST does not end in a complete statement")
    return True


__all__ = [
    "ST_RENDERER_SCHEMA_VERSION",
    "STTranslationError",
    "network_markers",
    "render_network_st",
    "render_plc_ir_to_st",
    "validate_st_traceability",
]
