"""Import GX Works2 statement-list CSV files into the canonical ladder model.

GX Works2 remains the interactive editor.  This module is the deterministic
reverse side of :func:`draw.generate_gx_works2_csv`: it converts the current
MAIN program and global device comments exported by GX Works2 back into the
application ladder model.  The importer deliberately fails closed when a
statement-list shape cannot be represented without changing its logic.

Instruction *recognition* is deliberately separate from CSV decoding.  The
raw mnemonic/operand stream is preserved first, then the shared instruction
registry is consulted for semantic categories.  This lets valid Mitsubishi
vendor instructions survive import even when the local catalogue does not yet
have enough metadata to validate their semantics.

Two results are produced from one decode.  The strict import above still needs
a lossless ladder model.  The read-only projection (:func:`inspect_gxworks2_csv`)
instead builds the ordered instruction IR from the raw networks; a rung that
the older graphical model cannot represent only limits the drawing preview.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory

from .csv_manager import CSVManager


_OPERAND_RE = re.compile(r'''"[^"]*"|'[^']*'|\S+''')
_ROOT_INPUT_RE = re.compile(r"^LD(?:I|P|F|=|<>|>=|<=|>|<)?$", re.I)
_AND_INPUT_RE = re.compile(r"^(?:AND|ANI|ANDP|ANDF|AND=|AND<>|AND>=|AND<=|AND>|AND<)$", re.I)
_OR_INPUT_RE = re.compile(r"^OR(?:I|P|F|=|<>|>=|<=|>|<)?$", re.I)
_COMPARE_SUFFIXES = ("<>", ">=", "<=", "=", ">", "<")
_CONDITION_SYSTEM_OPS = {"ANB", "ORB"}
_BRANCH_SYSTEM_OPS = {
    mnemonic
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()
    if DEFAULT_INSTRUCTION_REGISTRY.category_of(mnemonic)
    == InstructionCategory.BRANCH_CONTROL
} or {"MPS", "MRD", "MPP"}


class GXCSVImportError(ValueError):
    """Raised when a GX export cannot be represented losslessly."""


@dataclass
class RawInstruction:
    """Lossless GX statement-list instruction before semantic decoding.

    Unknown mnemonics are valid here.  This layer only represents what GX
    Works2 exported; catalogue coverage is a separate concern.
    """

    step: str
    op: str
    args: List[str] = field(default_factory=list)
    statement: str = ""
    label: str = ""
    source_row: int = 0

    def as_ir(self) -> Dict[str, Any]:
        # Keep the existing external instruction-group shape unchanged.
        return {"op": self.op, "args": list(self.args)}


# Internal compatibility alias so this first-stage refactor does not force a
# flag-day rename through the importer implementation or downstream tests.
_Instruction = RawInstruction


@dataclass(frozen=True)
class ParsedGXProgram:
    ladder: Dict[str, Any]
    program_name: str
    plc_info: str
    source_program_semantic_sha256: str
    source_comment_semantic_sha256: str
    network_instructions: List[List[Dict[str, Any]]]


def _tokens(value: Any) -> List[str]:
    return [item.strip() for item in _OPERAND_RE.findall(str(value or "")) if item.strip()]


def _normalize_title(value: Any, fallback: str = "MAIN") -> str:
    text = str(value or "").strip()
    for suffix in (" - 副本", "- 副本", " - COPY", "- COPY"):
        if text.upper().endswith(suffix.upper()):
            text = text[: -len(suffix)].strip()
            break
    return text or fallback


def _read_program_rows(path: Path) -> tuple[List[List[str]], List[_Instruction]]:
    with path.open("r", encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))

    records: List[_Instruction] = []
    last_instruction: Optional[_Instruction] = None
    for row_number, row in enumerate(rows[3:], start=4):
        padded = list(row) + [""] * max(0, 7 - len(row))
        step = padded[0].strip()
        statement = padded[1].strip()
        opcode = padded[2].strip().upper()
        operand_text = padded[3].strip()
        label = padded[6].strip()

        if opcode:
            record = _Instruction(
                step=step,
                op=opcode,
                args=_tokens(operand_text),
                statement=statement,
                label=label,
                source_row=row_number,
            )
            records.append(record)
            last_instruction = record
            continue

        if statement:
            records.append(
                _Instruction(
                    step=step,
                    op="",
                    statement=statement,
                    label=label,
                    source_row=row_number,
                )
            )
            last_instruction = None
            continue

        if operand_text:
            if last_instruction is None:
                raise GXCSVImportError(
                    f"GX Works2 CSV 第{row_number}行存在没有所属指令的操作数"
                )
            last_instruction.args.extend(_tokens(operand_text))
        if label:
            if last_instruction is None:
                raise GXCSVImportError(
                    f"GX Works2 CSV 第{row_number}行存在没有所属指令的注解"
                )
            last_instruction.label = (
                f"{last_instruction.label}；{label}"
                if last_instruction.label
                else label
            )
    return rows, records


def _document_newline(path: Path) -> str:
    """Native exports differ: program CSV is CRLF, comment CSV may be LF."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return "\r\n"
    return "\r\n" if b"\r\x00\n\x00" in raw else "\n"


def read_plc_info_target(program_csv_path: Any) -> Optional[str]:
    """The CPU the export names on its ``PLC信息`` line, if it names one.

    A native export states its own target, so a reader uses that identity
    instead of assuming FX3U for an export whose project model is unknown. A
    merged menu option resolves to its first member here because the rest of
    Core names devices and validation by a concrete CPU; step widths accept
    either spelling.
    """
    from gxworks2.project_profiles import plc_info_target

    path = Path(program_csv_path)
    try:
        with path.open("r", encoding="utf-16", newline="") as handle:
            rows = list(csv.reader(handle, delimiter="\t"))
    except OSError:
        return None
    for row in rows[:2]:
        if len(row) >= 2 and row[0].strip() == "PLC信息:":
            target = plc_info_target(row[1])
            if target is None:
                return None
            return target.removesuffix("(RemoteI/O)").split("/")[0]
    return None


def _comment_document(path: Any) -> tuple[str, Dict[str, str]]:
    """Read one native comment CSV: its document title and device comments."""
    selected = Path(path).expanduser().resolve()
    validation = CSVManager().validate_comments(selected, require_crlf=False)
    if not validation.valid:
        raise GXCSVImportError("软元件注释CSV格式错误：" + "；".join(validation.errors))
    with selected.open("r", encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    comments: Dict[str, str] = {}
    for row in rows[2:]:
        padded = list(row) + [""] * max(0, 2 - len(row))
        address = padded[0].strip().upper()
        comment = padded[1].strip()
        if address and comment:
            comments[address] = comment
    title = rows[0][0].strip() if rows and rows[0] else ""
    return title, comments


def read_device_comments(path: Any) -> Dict[str, str]:
    return _comment_document(path)[1]


def _is_root_input(op: str) -> bool:
    return bool(_ROOT_INPUT_RE.fullmatch(str(op or "").upper()))


def _is_condition(op: str) -> bool:
    normalized = str(op or "").upper()
    category = DEFAULT_INSTRUCTION_REGISTRY.category_of(normalized)
    if category == InstructionCategory.CONDITION:
        return True
    return bool(
        _ROOT_INPUT_RE.fullmatch(normalized)
        or _AND_INPUT_RE.fullmatch(normalized)
        or _OR_INPUT_RE.fullmatch(normalized)
        or normalized in _CONDITION_SYSTEM_OPS
    )


def _split_networks(
    records: Sequence[_Instruction],
) -> List[tuple[str, List[_Instruction], List[str]]]:
    """Group instructions into networks, keeping leading statement rows apart.

    The joined note is what a reader shows for the network.  The leading
    statements are the statement-only rows that preceded its first instruction;
    a source-bound writer needs them separately, otherwise re-emitting the note
    and the per-instruction statements would duplicate text.
    """
    networks: List[tuple[str, List[_Instruction], List[str]]] = []
    current: List[_Instruction] = []
    pending_statements: List[str] = []
    current_statements: List[str] = []
    leading_statements: List[str] = []
    has_action = False
    mps_depth = 0
    mps_seen = False
    after_mpp = False
    after_mpp_action = False

    def finish() -> None:
        nonlocal current, current_statements, leading_statements, has_action, mps_depth
        nonlocal mps_seen, after_mpp, after_mpp_action
        if current:
            note = "\n".join(item for item in current_statements if item).strip()
            networks.append((note, current, list(leading_statements)))
        current = []
        current_statements = []
        leading_statements = []
        has_action = False
        mps_depth = 0
        mps_seen = False
        after_mpp = False
        after_mpp_action = False

    for record in records:
        if not record.op:
            if record.statement:
                if current and has_action and mps_depth == 0:
                    finish()
                pending_statements.append(record.statement)
            continue

        if record.op == "END":
            finish()
            continue

        if _is_root_input(record.op) and current and has_action and mps_depth == 0:
            if not mps_seen or (after_mpp and after_mpp_action):
                finish()

        if not current:
            current_statements = list(pending_statements)
            pending_statements.clear()
            leading_statements = list(current_statements)
        if record.statement:
            current_statements.append(record.statement)
        current.append(record)

        if record.op == "MPS":
            mps_depth += 1
            mps_seen = True
        elif record.op == "MPP":
            mps_depth = max(0, mps_depth - 1)
            after_mpp = True
        elif not _is_condition(record.op) and record.op not in _BRANCH_SYSTEM_OPS:
            has_action = True
            if after_mpp:
                after_mpp_action = True

    finish()
    return networks


def _comment_for(address: str, comments: Mapping[str, str]) -> str:
    normalized = str(address or "").strip().upper()
    if normalized in comments:
        return str(comments[normalized])
    match = re.fullmatch(r"([XY])0+([0-7]+)", normalized)
    if match:
        compact = match.group(1) + (match.group(2).lstrip("0") or "0")
        if compact in comments:
            return str(comments[compact])
    return ""


def _input_element(record: _Instruction, comments: Mapping[str, str]) -> Dict[str, Any]:
    op = record.op.upper()
    suffix = next((item for item in _COMPARE_SUFFIXES if op.endswith(item)), "")
    if suffix:
        if len(record.args) < 2:
            raise GXCSVImportError(
                f"第{record.source_row}行 {op} 至少需要两个操作数"
            )
        return {
            "type": "BLOCK_INPUT",
            "expression": f"{suffix} {' '.join(record.args)}",
            "label": "",
        }

    if len(record.args) != 1:
        raise GXCSVImportError(
            f"第{record.source_row}行 {op} 必须且只能有一个触点操作数"
        )
    address = record.args[0].upper()
    if op in {"LDI", "ANI", "ORI"}:
        kind = "NC"
    elif op in {"LDP", "ANDP", "ORP"}:
        kind = "P"
    elif op in {"LDF", "ANDF", "ORF"}:
        kind = "F"
    else:
        kind = "NO"
    return {
        "type": kind,
        "address": address,
        "label": _comment_for(address, comments),
    }


def _node(kind: str, *children: Dict[str, Any]) -> Dict[str, Any]:
    flattened: List[Dict[str, Any]] = []
    for child in children:
        if child.get("kind") == "true" and kind == "and":
            continue
        if child.get("kind") == kind:
            flattened.extend(child.get("children", []))
        else:
            flattened.append(child)
    if not flattened:
        return {"kind": "true"}
    if len(flattened) == 1:
        return flattened[0]
    return {"kind": kind, "children": flattened}


def _parse_expression(
    records: Sequence[_Instruction],
    comments: Mapping[str, str],
    *,
    initial_true: bool = False,
) -> Dict[str, Any]:
    stack: List[Dict[str, Any]] = [{"kind": "true"}] if initial_true else []
    for record in records:
        op = record.op.upper()
        if _ROOT_INPUT_RE.fullmatch(op):
            stack.append({"kind": "term", "element": _input_element(record, comments)})
        elif _AND_INPUT_RE.fullmatch(op):
            if not stack:
                raise GXCSVImportError(f"第{record.source_row}行 {op} 前缺少逻辑结果")
            term = {"kind": "term", "element": _input_element(record, comments)}
            stack[-1] = _node("and", stack[-1], term)
        elif _OR_INPUT_RE.fullmatch(op):
            if not stack:
                raise GXCSVImportError(f"第{record.source_row}行 {op} 前缺少逻辑结果")
            term = {"kind": "term", "element": _input_element(record, comments)}
            stack[-1] = _node("or", stack[-1], term)
        elif op in {"ANB", "ORB"}:
            if len(stack) < 2:
                raise GXCSVImportError(f"第{record.source_row}行 {op} 缺少待合并逻辑块")
            right = stack.pop()
            left = stack.pop()
            stack.append(_node("and" if op == "ANB" else "or", left, right))
        else:
            raise GXCSVImportError(f"第{record.source_row}行 {op} 不是可解析的条件指令")
    if len(stack) != 1:
        raise GXCSVImportError("条件逻辑块没有完整合并，无法无损恢复梯形图")
    return stack[0]


def _series_from_node(node: Mapping[str, Any]) -> List[Dict[str, Any]]:
    kind = node.get("kind")
    if kind == "true":
        return []
    if kind == "term":
        return [dict(node["element"])]
    if kind == "and":
        result: List[Dict[str, Any]] = []
        for child in node.get("children", []):
            if child.get("kind") == "or":
                result.append(_parallel_from_or(child))
            else:
                result.extend(_series_from_node(child))
        return result
    if kind == "or":
        return [_parallel_from_or(node)]
    raise GXCSVImportError("条件表达式包含无法显示的逻辑节点")


def _parallel_from_or(node: Mapping[str, Any]) -> Dict[str, Any]:
    branches: List[List[Dict[str, Any]]] = []
    for child in node.get("children", []):
        series = _series_from_node(child)
        if any(item.get("type") == "parallel_block" for item in series):
            raise GXCSVImportError("条件包含嵌套并联块，当前项目模型无法无损表示")
        branches.append(series)
    if len(branches) < 2 or any(not branch for branch in branches):
        raise GXCSVImportError("并联条件结构不完整")
    return {"type": "parallel_block", "branches": branches}


def _output_element(record: _Instruction, comments: Mapping[str, str]) -> Dict[str, Any]:
    op = record.op.upper()
    label = record.label
    if op == "OUT":
        if not record.args:
            raise GXCSVImportError(f"第{record.source_row}行 OUT 缺少输出地址")
        address = record.args[0].upper()
        label = label or _comment_for(address, comments)
        if address.startswith("T"):
            if len(record.args) != 2:
                raise GXCSVImportError(f"第{record.source_row}行定时器OUT缺少设定值")
            return {"type": "TIMER", "address": address, "value": record.args[1], "label": label}
        if address.startswith("C"):
            if len(record.args) != 2:
                raise GXCSVImportError(f"第{record.source_row}行计数器OUT缺少设定值")
            return {"type": "COUNTER", "address": address, "value": record.args[1], "label": label}
        if len(record.args) != 1:
            raise GXCSVImportError(f"第{record.source_row}行普通OUT包含多余操作数")
        return {"type": "COIL", "address": address, "label": label}
    if op in {"PLS", "PLF"}:
        if len(record.args) != 1:
            raise GXCSVImportError(f"第{record.source_row}行 {op} 操作数数量不正确")
        address = record.args[0].upper()
        return {
            "type": op,
            "address": address,
            "label": label or _comment_for(address, comments),
        }
    # The historical external JSON format is intentionally retained.  A
    # missing catalogue entry is not an import error: APP_INSTR is the stable
    # lossless vendor-extension envelope used for round-trip preservation.
    return {
        "type": "APP_INSTR",
        "opcode": op,
        "operands": [str(item).upper() for item in record.args],
        "label": label,
    }


def _split_conditions_actions(
    records: Sequence[_Instruction],
) -> tuple[List[_Instruction], List[_Instruction]]:
    first_action = next(
        (index for index, record in enumerate(records) if not _is_condition(record.op)),
        len(records),
    )
    conditions = list(records[:first_action])
    actions = list(records[first_action:])
    unexpected = [record for record in actions if _is_condition(record.op)]
    if unexpected:
        record = unexpected[0]
        raise GXCSVImportError(
            f"第{record.source_row}行在输出指令之后重新开始条件，无法确定梯级边界"
        )
    if not actions:
        raise GXCSVImportError("梯级只有条件而没有输出或应用指令")
    return conditions, actions


def _rung_from_network(
    rung_id: int,
    note: str,
    records: Sequence[_Instruction],
    comments: Mapping[str, str],
) -> Dict[str, Any]:
    mps_positions = [index for index, record in enumerate(records) if record.op == "MPS"]
    if len(mps_positions) > 1:
        raise GXCSVImportError("一个梯级包含嵌套或重复MPS，当前项目模型无法无损表示")

    if not mps_positions:
        conditions, actions = _split_conditions_actions(records)
        expression = _parse_expression(conditions, comments) if conditions else {"kind": "true"}
        return {
            "rung_id": rung_id,
            "debug_note": note,
            "header_element": None,
            "shared_inputs": [],
            "branches": [
                {
                    "branch_id": 1,
                    "y_offset_level": 0,
                    "inputs": _series_from_node(expression),
                    "outputs": [_output_element(item, comments) for item in actions],
                }
            ],
        }

    mps_index = mps_positions[0]
    base_records = list(records[:mps_index])
    if not base_records or any(not _is_condition(item.op) for item in base_records):
        raise GXCSVImportError("MPS前必须存在完整的公共条件")
    base_expression = _parse_expression(base_records, comments)

    segments: List[List[_Instruction]] = []
    current: List[_Instruction] = []
    saw_mpp = False
    for record in records[mps_index + 1 :]:
        if record.op in {"MRD", "MPP"}:
            if not current:
                raise GXCSVImportError(f"第{record.source_row}行 {record.op} 前分支为空")
            segments.append(current)
            current = []
            if record.op == "MPP":
                saw_mpp = True
            continue
        if saw_mpp and not current and record.op in _BRANCH_SYSTEM_OPS:
            raise GXCSVImportError("MPP之后出现了无法配对的分支栈指令")
        current.append(record)
    if current:
        segments.append(current)
    if not saw_mpp or len(segments) < 2:
        raise GXCSVImportError("MPS梯级缺少配对的MPP或有效分支")

    shared_inputs = _series_from_node(base_expression)
    if any(item.get("type") == "parallel_block" for item in shared_inputs):
        raise GXCSVImportError(
            "MPS公共条件中包含并联块，当前预览模型无法无损显示，已停止同步"
        )

    branches = []
    for index, segment in enumerate(segments, start=1):
        conditions, actions = _split_conditions_actions(segment)
        expression = _parse_expression(conditions, comments, initial_true=True)
        branches.append(
            {
                "branch_id": index,
                "y_offset_level": index - 1,
                "inputs": _series_from_node(expression),
                "outputs": [_output_element(item, comments) for item in actions],
            }
        )
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": None,
        "shared_inputs": shared_inputs,
        "branches": branches,
    }


def parse_gxworks2_csv(
    program_csv_path: Any,
    comment_csv_path: Any,
) -> ParsedGXProgram:
    program_path = Path(program_csv_path).expanduser().resolve()
    comment_path = Path(comment_csv_path).expanduser().resolve()
    manager = CSVManager()
    validation = manager.validate(program_path)
    if not validation.valid:
        raise GXCSVImportError("程序CSV格式错误：" + "；".join(validation.errors))
    comments = read_device_comments(comment_path)
    rows, records = _read_program_rows(program_path)
    network_records = _split_networks(records)
    if not network_records:
        raise GXCSVImportError("GX Works2程序中没有可同步的梯级")

    rungs: List[Dict[str, Any]] = []
    used_ids = set()
    instruction_groups: List[List[Dict[str, Any]]] = []
    fallback_id = 0
    for note, instructions, _leading in network_records:
        first_step = next((item.step for item in instructions if item.step.isdigit()), "")
        candidate = int(first_step) if first_step else fallback_id
        while candidate in used_ids:
            candidate += 1
        used_ids.add(candidate)
        fallback_id = candidate + 1
        rungs.append(_rung_from_network(candidate, note, instructions, comments))
        instruction_groups.append([item.as_ir() for item in instructions])

    program_title = rows[0][0] if rows and rows[0] else validation.program_name
    plc_info = rows[1][1] if len(rows) > 1 and len(rows[1]) > 1 else validation.plc_info
    return ParsedGXProgram(
        ladder={"device_comments": comments, "rungs": rungs},
        program_name=_normalize_title(program_title),
        plc_info=str(plc_info or "").strip(),
        source_program_semantic_sha256=manager.program_semantic_sha256(program_path),
        source_comment_semantic_sha256=manager.comments_semantic_sha256(comment_path),
        network_instructions=instruction_groups,
    )


def _csv_source_map(
    network_id: str,
    instructions: Sequence[_Instruction],
) -> List[Dict[str, Any]]:
    """Bind each ordered instruction position to its original CSV coordinates.

    The ordered IR keeps only op/args, so the CSV row number, step, statement
    and label stay in this snapshot mapping instead of being pushed into the
    instruction records as fabricated native offsets.
    """
    return [
        {
            "network_id": network_id,
            "instruction_index": position,
            "step": item.step,
            "source_row": item.source_row,
            "statement": item.statement,
            "label": item.label,
        }
        for position, item in enumerate(instructions)
    ]


def locate_csv_source(
    source_map: Sequence[Mapping[str, Any]],
    network_id: str,
    instruction_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return the original CSV coordinates of ordered instruction positions.

    Without ``instruction_index`` every instruction of the network is returned,
    which is the finest location a network-scoped diagnostic can claim.
    """
    selected = str(network_id)
    rows = [dict(row) for row in source_map if str(row.get("network_id")) == selected]
    if instruction_index is None:
        return rows
    return [row for row in rows if row.get("instruction_index") == instruction_index]


def _finding_csv_sources(
    source_map: Sequence[Mapping[str, Any]],
    finding: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Locate one diagnostic through its network and instruction binding."""
    network_ids = [str(value) for value in finding.get("network_refs") or [] if value]
    if not network_ids:
        network_ids = [f"N{int(value):04d}" for value in finding.get("rung_ids") or []
                       if isinstance(value, int) and not isinstance(value, bool)]
    record_index = finding.get("record_index")
    if isinstance(record_index, bool) or not isinstance(record_index, int):
        record_index = None
    rows: List[Dict[str, Any]] = []
    for network_id in network_ids:
        for row in locate_csv_source(source_map, network_id, record_index):
            if row not in rows:
                rows.append(row)
    return rows


def _csv_diagnostics(
    source_map: Sequence[Mapping[str, Any]],
    program_ir: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Project static findings with their original CSV coordinates."""
    findings = ((program_ir or {}).get("analysis") or {}).get("findings") or []
    return [{**copy.deepcopy(dict(finding)),
             "csv_sources": _finding_csv_sources(source_map, finding)}
            for finding in findings if isinstance(finding, Mapping)]


def inspect_gxworks2_csv(program_csv_path, comment_csv_path, *, plc_model, program_name=None):
    """Read-only projection: ordered instruction IR first, drawing preview second.

    The exported statement list is authoritative.  Every network goes straight
    into :func:`plc.ordered_source.build_instruction_ir`, so an output followed
    by a later condition, or an output before ``MPS``, stays readable instead of
    being rejected by the older graphical model.  Instruction reading, ordered
    IR validity, drawing-preview completeness and semantic verification are
    reported separately.  A rung that cannot be drawn leaves the ordered IR,
    the read/write reference analysis and the static checks usable.
    """
    from plc.ir import validate_plc_ir
    from plc.ordered_source import build_instruction_ir
    from plc.validation import ValidationProfileUnavailableError

    program_path = Path(program_csv_path)
    validation = CSVManager().validate(program_path)
    if not validation.valid:
        raise GXCSVImportError("程序CSV格式错误：" + "；".join(validation.errors))
    comment_title, comments = _comment_document(comment_csv_path)
    rows, records = _read_program_rows(program_path)
    # Native CSV's document title may be the project name, rather than a POU.
    # Only the native host can bind that export to the selected program editor.
    selected_program = program_name or _normalize_title(rows[0][0])
    groups = _split_networks(records)
    networks: List[Dict[str, Any]] = []
    source_map: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    preview_rungs: List[Dict[str, Any]] = []
    preview_failures: List[Dict[str, Any]] = []
    for index, (note, instructions, leading) in enumerate(groups, 1):
        network_id = f"N{index:04d}"
        raw = [{**item.as_ir(), "step": item.step, "source_row": item.source_row,
                "statement": item.statement, "label": item.label} for item in instructions]
        positions = _csv_source_map(network_id, instructions)
        row = {"id": network_id, "comment": note, "parse_status": "ordered",
               "statements": [item for item in leading if item],
               "raw_instructions": raw, "source_map": positions}
        try:
            preview_rungs.append(_rung_from_network(index, note, instructions, comments))
            row["graph_preview_status"] = "complete"
        except GXCSVImportError as error:
            row.update(graph_preview_status="unavailable", graph_preview_reason=str(error))
            preview_failures.append({"network_id": network_id, "reason": str(error)})
        unknown.extend({"network_id": network_id, **item} for item in raw
                       if not DEFAULT_INSTRUCTION_REGISTRY.is_known(item["op"]))
        networks.append(row)
        source_map.extend(positions)

    # END terminates the program instead of describing a rung.  It stays in the
    # ordered IR of the last network (the same binding the GXW reader uses), and
    # it is excluded from the drawing preview only.
    terminal = next((record for record in reversed(records) if record.op == "END"), None)
    if networks and terminal is not None:
        last = networks[-1]
        last["raw_instructions"].append(
            {**terminal.as_ir(), "step": terminal.step, "source_row": terminal.source_row,
             "statement": terminal.statement, "label": terminal.label})
        position = _csv_source_map(last["id"], [terminal])[0]
        position["instruction_index"] = len(last["source_map"])
        last["source_map"].append(position)
        source_map.append(position)

    read_instructions = sum(1 for record in records if record.op)
    grouped_instructions = sum(len(network["raw_instructions"]) for network in networks)
    if not networks:
        instruction_status = "unavailable"
    elif read_instructions == grouped_instructions:
        instruction_status = "complete"
    else:
        instruction_status = "partial"

    program_ir: Optional[Dict[str, Any]] = None
    candidate: Optional[Dict[str, Any]] = None
    ir_failures: List[Dict[str, Any]] = []
    ir_status = "unavailable"
    if networks:
        try:
            candidate = build_instruction_ir(
                networks,
                plc_model=plc_model,
                program_name=selected_program,
                device_comments=comments,
            )
            validate_plc_ir(candidate, require_catalogued_instructions=False)
            program_ir, ir_status = candidate, "valid"
        except ValidationProfileUnavailableError as error:
            program_ir, ir_status = candidate, "unverified"
            ir_failures.append({"reason": str(error), "profile": "unavailable_target"})
        except (ValueError, TypeError) as error:
            ir_failures.append({"reason": str(error)})
            ir_status = "failed"

    if not networks:
        parse_status = "unavailable"
    elif instruction_status == "complete" and ir_status == "valid":
        parse_status = "complete"
    else:
        parse_status = "partial"
    if not networks:
        graph_preview_status = "unavailable"
    elif preview_failures:
        graph_preview_status = "partial"
    else:
        graph_preview_status = "complete"

    # Expected program steps come from the shared Core width rules.  The source
    # export keeps its own step labels; a disagreement is reported by kind so an
    # unresolved width is never confused with a known off-by-one.
    from gxworks2.csv_source_export import step_width_plan
    width_plan = step_width_plan({"raw_networks": networks, "plc_model": plc_model},
                                 plc_model=plc_model, step_source="source")
    width_summary = width_plan["summary"]
    if width_summary["mismatch"]:
        step_width_status = "differences_reported"
    elif width_summary["unknown"]:
        step_width_status = "incomplete"
    else:
        step_width_status = "source_matches_catalogue"
    step_width = {"status": step_width_status, "summary": width_summary,
                  "findings": width_plan["findings"][:40],
                  "truncated_findings": max(0, len(width_plan["findings"]) - 40)}

    coverage = {
        "parse_status": parse_status,
        "instruction_status": instruction_status,
        "instruction_count": read_instructions,
        "network_instruction_count": grouped_instructions,
        "network_count": len(networks),
        # Networks represented in the ordered IR, not networks that could be drawn.
        "parsed_network_count": len(program_ir.get("networks") or []) if program_ir else 0,
        "ir_status": ir_status,
        "ir_failures": ir_failures,
        # Drawing coverage is reported separately: it never blocks the IR.
        "graph_preview_status": graph_preview_status,
        "graph_preview_failures": preview_failures,
        "unknown_instructions": unknown,
        "step_width_status": step_width_status,
        "semantic_status": "unverified" if unknown or ir_status != "valid" else "static_only",
        "runtime_verified": False,
    }
    ladder = ({"device_comments": comments, "rungs": preview_rungs}
              if graph_preview_status == "complete" else None)
    return {"program_name": selected_program, "export_title": rows[0][0], "plc_info": validation.plc_info,
            "comment_title": comment_title, "plc_model": plc_model,
            "newlines": {"program": _document_newline(program_path),
                         "comments": _document_newline(Path(comment_csv_path))},
            "raw_networks": networks, "device_comments": comments, "coverage": coverage,
            "graph_preview": {"status": graph_preview_status, "failures": preview_failures},
            "step_width": step_width,
            "ladder": ladder, "program_ir": program_ir,
            "source_map": source_map, "diagnostics": _csv_diagnostics(source_map, program_ir),
            "raw_rows": rows}


def diff_gxworks2_programs(left_path: Any, right_path: Any) -> Dict[str, Any]:
    """Return a stable instruction-level summary suitable for a conflict UI."""

    manager = CSVManager()
    left = manager.program_semantic_payload(left_path).get("instructions", [])
    right = manager.program_semantic_payload(right_path).get("instructions", [])
    left = [item for item in left if item and item[0] != "END"]
    right = [item for item in right if item and item[0] != "END"]
    changed = []
    limit = max(len(left), len(right))
    for index in range(limit):
        before = left[index] if index < len(left) else None
        after = right[index] if index < len(right) else None
        if before != after:
            changed.append({"index": index + 1, "project": before, "gxworks2": after})
    return {
        "project_instruction_count": len(left),
        "gxworks2_instruction_count": len(right),
        "changed_instruction_count": len(changed),
        "changes": changed[:50],
        "truncated": len(changed) > 50,
    }


def _materialize_ordered_csv_version(
    program_csv_path: Any,
    comment_csv_path: Any,
    output_dir: Any,
    *,
    plc_model: str,
    program_name: str,
    revision: int,
) -> Dict[str, Any]:
    """Strict import of an export the older graphical model cannot represent.

    The ordered statement list is the authoritative source. Drawings are a
    preview, ST has no established translation for an ordered source, and the
    CSV interchange artifacts are re-serialized through the source-bound writer
    so the round-trip check compares statements, labels, step labels and the
    original rows, not only an instruction hash.
    """
    from gxworks2.csv_source_export import verify_gxworks2_csv_source, write_gxworks2_csv_from_source
    from plc.ir import IR_SCHEMA_VERSION, canonical_sha256, ir_to_ladder, validate_plc_ir
    from plc.semantics import SEMANTICS_SCHEMA_VERSION
    from plc.st_renderer import ST_RENDERER_SCHEMA_VERSION, render_plc_ir_to_st, validate_st_traceability
    from plc.static_analysis import STATIC_ANALYSIS_SCHEMA_VERSION
    from plc.timing import TIMING_ANALYSIS_SCHEMA_VERSION
    from rendering.ladder_svg import AdvancedSVGLadder

    selected_model = str(plc_model or "FX3U").strip().upper()
    snapshot = inspect_gxworks2_csv(program_csv_path, comment_csv_path,
                                    plc_model=selected_model, program_name=program_name)
    program_ir = snapshot.get("program_ir")
    if program_ir is None:
        reasons = "；".join(str(item.get("reason") or "") for item in
                            snapshot["coverage"].get("ir_failures") or [])
        raise GXCSVImportError(
            "GX Works2程序包含当前项目模型无法无损往返的结构，已停止同步"
            + ("：" + reasons.strip("；") if reasons else ""))

    # The snapshot IR is revision-agnostic; the stored version states its own.
    from plc.ordered_source import build_instruction_ir
    program_ir = build_instruction_ir(
        snapshot["raw_networks"], plc_model=selected_model,
        program_name=str(program_name or snapshot["program_name"] or "MAIN"),
        device_comments=snapshot["device_comments"], revision=int(revision))
    validate_plc_ir(program_ir, require_catalogued_instructions=False)
    rendered_ladder = ir_to_ladder(program_ir)
    ladder_text = json.dumps(rendered_ladder, ensure_ascii=False, indent=2)
    drawer = AdvancedSVGLadder()
    svg_text = drawer.generate_ladder(ladder_text)

    # ST is reported as unavailable instead of being guessed from an ordered
    # source; the export itself stays fully readable and checkable.
    st_text, st_status, st_reason = None, "unavailable", ""
    try:
        candidate_st = render_plc_ir_to_st(program_ir)
        validate_st_traceability(program_ir, candidate_st)
    except Exception as error:  # noqa: BLE001 - the reason is reported verbatim
        st_status, st_reason = "unavailable", str(error)[:500]
    else:
        st_text, st_status = candidate_st, "rendered"

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "ladder.json"
    ir_path = destination / "program.ir.json"
    svg_path = destination / "ladder.svg"
    st_path = destination / "program_from_ir.st"
    roundtrip_program_path = destination / "program.roundtrip.csv"
    roundtrip_comment_path = destination / "comments.roundtrip.csv"
    program_path = destination / "program.csv"
    comments_path = destination / "comments.csv"

    json_path.write_text(ladder_text, encoding="utf-8")
    ir_path.write_text(json.dumps(program_ir, ensure_ascii=False, indent=2), encoding="utf-8")
    svg_path.write_text(svg_text, encoding="utf-8")

    write_report = write_gxworks2_csv_from_source(
        snapshot, roundtrip_program_path, roundtrip_comment_path, plc_model=selected_model)
    verification = verify_gxworks2_csv_source(
        snapshot,
        program_csv_path=roundtrip_program_path,
        comment_csv_path=roundtrip_comment_path,
        source_program_csv_path=program_csv_path,
        source_comment_csv_path=comment_csv_path,
    )
    if verification["status"] != "passed":
        raise GXCSVImportError(
            "GX Works2程序无法按原始 CSV 来源保序往返，已停止同步："
            + json.dumps(verification["mismatches"][:3], ensure_ascii=False))

    # Preserve the exact native exports as the version's interchange artifacts.
    shutil.copy2(Path(program_csv_path).resolve(), program_path)
    shutil.copy2(Path(comment_csv_path).resolve(), comments_path)
    roundtrip_program_path.unlink(missing_ok=True)
    roundtrip_comment_path.unlink(missing_ok=True)
    if st_text is not None:
        st_path.write_text(st_text, encoding="utf-8")

    width_summary = write_report["width_summary"]
    if width_summary["mismatch"] or width_summary["unknown"]:
        width_message = ("程序步宽：" + str(width_summary["mismatch"]) + " 处来源步宽与共享宽度规则不一致，"
                         + str(width_summary["unknown"]) + " 处规则尚无法给出宽度；已按来源步号保留并记录")
    else:
        width_message = "程序步宽：原始步号与共享宽度规则逐条一致"

    logic = program_ir.get("logic") or {}
    timing_root = program_ir.get("timing") or {}
    performance = timing_root.get("performance") or {}
    analysis = program_ir.get("analysis") or {}
    artifacts = {"json": json_path.name, "ir": ir_path.name, "svg": svg_path.name,
                 "program_csv": program_path.name, "comment_csv": comments_path.name}
    if st_text is not None:
        artifacts["st_from_ir"] = st_path.name
    return {
        "target_mode": "ladder",
        "program_name": str(program_name or snapshot["program_name"] or "MAIN"),
        "revision": int(revision),
        "plc_model": selected_model,
        "ir_schema_version": IR_SCHEMA_VERSION,
        "ir_sha256": canonical_sha256(program_ir),
        "ladder_sha256": program_ir["source"]["ladder_sha256"],
        "st_from_ir_sha256": (None if st_text is None
                              else hashlib.sha256(st_text.encode("utf-8")).hexdigest()),
        "st_renderer_schema_version": ST_RENDERER_SCHEMA_VERSION,
        "st_status": st_status,
        "st_reason": st_reason,
        "semantic_schema_version": SEMANTICS_SCHEMA_VERSION,
        "semantic_summary": {
            "requirements": list(logic.get("requirements") or []),
            "coverage": list(timing_root.get("coverage") or []),
            "state_machine_count": len(logic.get("state_machines") or []),
            "regions": [
                {"code": item.get("code"), "kind": item.get("kind"),
                 "network_count": len(item.get("network_refs") or [])}
                for item in logic.get("regions") or []
                if isinstance(item, Mapping)
            ],
        },
        "static_analysis_schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
        "static_analysis_summary": {
            "counts": dict(analysis.get("counts") or {}),
            "rules_checked": list(analysis.get("rules_checked") or []),
            "dependency_nodes": len((analysis.get("dependency_graph") or {}).get("nodes") or []),
            "dependency_edges": len((analysis.get("dependency_graph") or {}).get("device_edges") or []),
        },
        "timing_analysis_schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
        "timing_summary": {
            "profile": performance.get("profile"),
            "estimate": dict(performance.get("estimate") or {}),
            "scan_budget": dict(performance.get("scan_budget") or {}),
            "scan_monitor": dict(performance.get("scan_monitor") or {}),
        },
        "width": int(drawer.width),
        "height": int(drawer.height),
        "source_kind": "gxworks2_sync",
        "source_format": (program_ir.get("source") or {}).get("format"),
        "source_program_semantic_sha256": CSVManager().program_semantic_sha256(
            Path(program_csv_path).expanduser().resolve()),
        "source_comment_semantic_sha256": CSVManager().comments_semantic_sha256(
            Path(comment_csv_path).expanduser().resolve()),
        "preservation": {
            "status": verification["status"],
            "checks": verification["checks"],
            "byte_identical": verification.get("byte_identical"),
            "step_source": write_report["step_source"],
            # ``width_mismatch``/``width_unknown`` stay visible: an unedited
            # export keeps its own steps, so a catalogue disagreement is reported
            # instead of triggering a silent renumbering.
            "step_width": {"findings": write_report["width_findings"],
                           "summary": write_report["width_summary"]},
            "statement_findings": write_report["statement_findings"],
            "unbound_instructions": verification.get("unbound_instructions", 0),
        },
        "coverage": snapshot["coverage"],
        "graph_preview": snapshot["graph_preview"],
        "artifacts": artifacts,
        "validation": {
            "status": "passed",
            "messages": [
                "GX Works2 有序指令按原始 CSV 行列、步号、声明与标签保序回写并通过往返比较",
                width_message,
                "图形预览不完整；有序 IR 仍可用于读取引用与静态检查",
                "ST 转换" + ("已生成" if st_text is not None else "不可用：" + (st_reason or "有序来源没有已建立的 ST 翻译")),
            ],
        },
    }


def materialize_gxworks2_version(
    program_csv_path: Any,
    comment_csv_path: Any,
    output_dir: Any,
    *,
    plc_model: str = "FX3U",
    program_name: str = "MAIN",
    revision: int = 1,
) -> Dict[str, Any]:
    """Create one immutable application version from a native GX export.

    ``plc_model`` is the saved CPU and decides the step widths written into the
    re-serialized artifacts; product callers pass the project's CPU, and the
    fallback export names its own target (``read_plc_info_target``). The default
    remains for the FX3U callers that predate per-CPU step widths.
    """

    try:
        parsed = parse_gxworks2_csv(program_csv_path, comment_csv_path)
    except GXCSVImportError:
        # The drawing model cannot represent every native statement list; the
        # ordered reader can.  Its own CSV validation raises the same error, so
        # an invalid document keeps its original failure.
        ordered = _materialize_ordered_csv_version(
            program_csv_path, comment_csv_path, output_dir,
            plc_model=plc_model, program_name=program_name, revision=revision)
        return ordered
    from rendering.ladder_svg import AdvancedSVGLadder
    from gxworks2.csv_export import generate_gx_works2_csv
    from plc.ir import IR_SCHEMA_VERSION, build_plc_ir, canonical_sha256, ir_to_ladder, validate_plc_ir
    from plc.validation import validate_ladder_full
    from plc.semantics import SEMANTICS_SCHEMA_VERSION
    from plc.static_analysis import STATIC_ANALYSIS_SCHEMA_VERSION
    from plc.st_renderer import ST_RENDERER_SCHEMA_VERSION, render_plc_ir_to_st, validate_st_traceability
    from plc.timing import TIMING_ANALYSIS_SCHEMA_VERSION

    selected_model = str(plc_model or "FX3U").strip().upper()
    # Native GX exports are trusted as the source of spelling, not as proof of
    # semantics.  Catalogue misses are therefore preserved for round-trip but
    # remain unavailable to strict Agent generation/repair paths.
    validate_ladder_full(
        parsed.ladder,
        plc_model=selected_model,
        require_catalogued_instructions=False,
    )
    program_ir = build_plc_ir(
        parsed.ladder,
        plc_model=selected_model,
        program_name=program_name or parsed.program_name,
        revision=revision,
        # Native snapshots retain their exported spelling for forensic round-trip.
        # Explorer/export projections coalesce aliases without rewriting evidence.
        _canonicalize_devices=False,
    )
    validate_plc_ir(
        program_ir,
        require_catalogued_instructions=False,
    )
    rendered_ladder = ir_to_ladder(program_ir)

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "ladder.json"
    ir_path = destination / "program.ir.json"
    svg_path = destination / "ladder.svg"
    st_path = destination / "program_from_ir.st"
    rendered_program_path = destination / "program.roundtrip.csv"
    rendered_comment_path = destination / "comments.roundtrip.csv"
    program_path = destination / "program.csv"
    comments_path = destination / "comments.csv"

    json_text = json.dumps(rendered_ladder, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    ir_path.write_text(json.dumps(program_ir, ensure_ascii=False, indent=2), encoding="utf-8")
    drawer = AdvancedSVGLadder()
    svg_path.write_text(drawer.generate_ladder(json_text), encoding="utf-8")
    st_text = render_plc_ir_to_st(program_ir)
    validate_st_traceability(program_ir, st_text)
    st_path.write_text(st_text, encoding="utf-8")
    if not generate_gx_works2_csv(
        program_ir,
        str(rendered_program_path),
        str(rendered_comment_path),
        infer_device_comments=False,
    ):
        raise GXCSVImportError("无法从回读后的PLC IR重新生成GX Works2 CSV")

    manager = CSVManager()
    rendered_program_hash = manager.program_semantic_sha256(rendered_program_path)
    rendered_comment_hash = manager.comments_semantic_sha256(rendered_comment_path)
    if rendered_program_hash != parsed.source_program_semantic_sha256:
        raise GXCSVImportError(
            "GX Works2程序包含当前项目模型无法无损往返的结构，已停止同步"
        )
    if rendered_comment_hash != parsed.source_comment_semantic_sha256:
        raise GXCSVImportError("GX Works2软元件注释无法无损往返，已停止同步")

    # Preserve the exact native exports as the version's interchange artifacts.
    shutil.copy2(Path(program_csv_path).resolve(), program_path)
    shutil.copy2(Path(comment_csv_path).resolve(), comments_path)
    rendered_program_path.unlink(missing_ok=True)
    rendered_comment_path.unlink(missing_ok=True)

    logic = program_ir.get("logic") or {}
    timing_root = program_ir.get("timing") or {}
    performance = timing_root.get("performance") or {}
    analysis = program_ir.get("analysis") or {}
    return {
        "target_mode": "ladder",
        "program_name": str(program_name or parsed.program_name or "MAIN"),
        "revision": int(revision),
        "plc_model": selected_model,
        "ir_schema_version": IR_SCHEMA_VERSION,
        "ir_sha256": canonical_sha256(program_ir),
        "ladder_sha256": program_ir["source"]["ladder_sha256"],
        "st_from_ir_sha256": __import__("hashlib").sha256(st_text.encode("utf-8")).hexdigest(),
        "st_renderer_schema_version": ST_RENDERER_SCHEMA_VERSION,
        "semantic_schema_version": SEMANTICS_SCHEMA_VERSION,
        "semantic_summary": {
            "requirements": list(logic.get("requirements") or []),
            "coverage": list(timing_root.get("coverage") or []),
            "state_machine_count": len(logic.get("state_machines") or []),
            "regions": [
                {
                    "code": item.get("code"),
                    "kind": item.get("kind"),
                    "network_count": len(item.get("network_refs") or []),
                }
                for item in logic.get("regions") or []
                if isinstance(item, Mapping)
            ],
        },
        "static_analysis_schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
        "static_analysis_summary": {
            "counts": dict(analysis.get("counts") or {}),
            "rules_checked": list(analysis.get("rules_checked") or []),
            "dependency_nodes": len((analysis.get("dependency_graph") or {}).get("nodes") or []),
            "dependency_edges": len((analysis.get("dependency_graph") or {}).get("device_edges") or []),
        },
        "timing_analysis_schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
        "timing_summary": {
            "profile": performance.get("profile"),
            "estimate": dict(performance.get("estimate") or {}),
            "scan_budget": dict(performance.get("scan_budget") or {}),
            "scan_monitor": dict(performance.get("scan_monitor") or {}),
        },
        "width": int(drawer.width),
        "height": int(drawer.height),
        "source_kind": "gxworks2_sync",
        "source_program_semantic_sha256": parsed.source_program_semantic_sha256,
        "source_comment_semantic_sha256": parsed.source_comment_semantic_sha256,
        "artifacts": {
            "json": json_path.name,
            "ir": ir_path.name,
            "svg": svg_path.name,
            "st_from_ir": st_path.name,
            "program_csv": program_path.name,
            "comment_csv": comments_path.name,
        },
        "validation": {
            "status": "passed",
            "messages": ["GX Works2程序与软元件注释已无损回读并通过往返校验"],
        },
    }


__all__ = [
    "GXCSVImportError",
    "ParsedGXProgram",
    "RawInstruction",
    "diff_gxworks2_programs",
    "inspect_gxworks2_csv",
    "locate_csv_source",
    "materialize_gxworks2_version",
    "parse_gxworks2_csv",
    "read_device_comments",
    "read_plc_info_target",
]
