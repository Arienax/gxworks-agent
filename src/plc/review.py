"""Advisory, model-aware ladder workflow review rules.

Hard schema and instruction failures belong in :mod:`plc_json_validator`.
Rules here describe review evidence and never block version persistence.
"""

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import normalize_plc_model, parse_device_address


@dataclass
class ReviewFinding:
    severity: str
    category: str
    address: Optional[str]
    message: str
    suggestion: str
    json_path: Optional[str] = None
    rung_ids: List[int] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    source: str = "local"
    confidence: str = "high"
    fixable: bool = False
    fix_instruction: str = ""
    safety_related: bool = False

    def to_dict(self):
        return asdict(self)


WRITE_OPCODES = {"SET", "RST", "MOV", "DMOV", "ZRST"}
BIT_OWNER_RE = re.compile(r"^(?:Y|M)\d+$", re.IGNORECASE)
STATE_REGISTER_RE = re.compile(r"^D\d+$", re.IGNORECASE)
DEVICE_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S)\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)
CONSTANT_RE = re.compile(r"^K(-?\d+)$", re.IGNORECASE)
FAULT_LABEL_RE = re.compile(
    r"故障|报警|异常|过载|急停|安全门|限位|fault|alarm|error|overload|"
    r"emergency|e[- ]?stop|safety|limit",
    re.IGNORECASE,
)
SAFETY_LABEL_RE = re.compile(
    r"急停|安全门|限位|安全回路|emergency|e[- ]?stop|safety|limit",
    re.IGNORECASE,
)
NON_PROGRAM_OWNER_RE = re.compile(
    r"HMI|触摸屏|上位机|SCADA|外部|监视|显示|读取|备用|预留|硬接线|"
    r"hmi|operator\s*panel|external|monitor|display|read[- ]?only|"
    r"spare|reserved|hardwired",
    re.IGNORECASE,
)
NON_STATE_REGISTER_RE = re.compile(
    r"配方|设定|参数|速度|温度|压力|累计|计数值|命令字|HMI|触摸屏|上位机|"
    r"recipe|setpoint|parameter|speed|temperature|pressure|total|command",
    re.IGNORECASE,
)
TERMINAL_STATE_RE = re.compile(
    r"结束|完成|终止|待机|等待人工|故障|报警|end|done|complete|terminal|fault|alarm",
    re.IGNORECASE,
)
CYCLIC_TIMER_RE = re.compile(
    r"闪烁|闪灯|周期|循环闪|振荡|时钟|方波|脉冲发生|"
    r"blink|flash|oscillat|clock|square\s*wave|toggle",
    re.IGNORECASE,
)
CATEGORY_LABELS = {
    "model_compatibility": "型号与地址兼容",
    "confirmed_io": "确认 I/O 一致性",
    "output_ownership": "输出所有权",
    "set_reset_ownership": "SET/RST 归属",
    "set_reset_toggle": "SET/RST 同扫描翻转",
    "state_ownership": "状态寄存器写入",
    "state_initialization": "状态初始化",
    "state_transition": "状态跳转",
    "state_reset": "状态复位",
    "timer_reset": "定时器复位路径",
    "timer_path": "定时器路径",
    "timer_oscillator": "定时器振荡路径",
    "counter_path": "计数器路径",
    "counter_reset": "计数器复位路径",
    "motion_completion": "运动完成信号",
    "alarm_logic": "故障/报警逻辑",
    "online_observation": "在线观测可读性",
}

SEVERITY_LABELS = {
    "error": "错误",
    "warning": "警告",
    "info": "提示",
}


def review_ladder(data, confirmed_spec=None, plc_model="FX3U", request=None):
    """Return advisory findings for a ladder dictionary.

    The original one-argument API remains valid and defaults to FX3U.
    """

    if not isinstance(data, dict):
        return []
    model = normalize_plc_model(plc_model)
    rungs = data.get("rungs", [])
    if not isinstance(rungs, list):
        return []
    findings = []
    findings.extend(_review_model_compatibility(data, model))
    findings.extend(_review_confirmed_io(data, confirmed_spec))
    findings.extend(_review_output_ownership(rungs))
    findings.extend(_review_set_reset_ownership(data))
    findings.extend(_review_same_scan_set_reset_toggle(rungs))
    findings.extend(_review_state_machine(data, model))
    findings.extend(_review_timer_and_counter_paths(data))
    findings.extend(_review_instruction_completion(rungs, model))
    findings.extend(_review_alarm_logic(data, confirmed_spec))
    findings.extend(_review_online_observations(data, request))
    return _deduplicate_findings(findings)


def _iter_elements(data):
    for rung_idx, rung in enumerate(data.get("rungs", []) or []):
        if not isinstance(rung, dict):
            continue
        header = rung.get("header_element")
        if isinstance(header, dict):
            yield rung_idx, rung, None, None, header, f"$.rungs[{rung_idx}].header_element"
        for shared_idx, element in enumerate(rung.get("shared_inputs", []) or []):
            if isinstance(element, dict):
                yield (
                    rung_idx,
                    rung,
                    None,
                    None,
                    element,
                    f"$.rungs[{rung_idx}].shared_inputs[{shared_idx}]",
                )
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            if not isinstance(branch, dict):
                continue
            for collection in ("inputs", "outputs"):
                for element_idx, element in enumerate(branch.get(collection, []) or []):
                    if not isinstance(element, dict):
                        continue
                    path = (
                        f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                        f".{collection}[{element_idx}]"
                    )
                    yield rung_idx, rung, branch_idx, branch, element, path
                    if element.get("type") == "parallel_block":
                        for nested_branch_idx, nested in enumerate(element.get("branches", []) or []):
                            for nested_idx, nested_element in enumerate(nested or []):
                                if isinstance(nested_element, dict):
                                    yield (
                                        rung_idx,
                                        rung,
                                        branch_idx,
                                        branch,
                                        nested_element,
                                        f"{path}.branches[{nested_branch_idx}][{nested_idx}]",
                                    )


def _element_devices(element):
    address = element.get("address")
    if isinstance(address, str) and address:
        yield address.upper()
    expression = element.get("expression")
    if isinstance(expression, str):
        for match in DEVICE_RE.finditer(expression):
            yield match.group(0).upper()
    for operand in element.get("operands", []) or []:
        if isinstance(operand, str):
            match = DEVICE_RE.fullmatch(operand.strip())
            if match:
                yield match.group(0).upper()


def _all_used_devices(data):
    devices = set()
    for _, _, _, _, element, _ in _iter_elements(data):
        devices.update(_element_devices(element))
    return devices


def _review_model_compatibility(data, plc_model):
    findings = []
    for _, rung, _, _, element, path in _iter_elements(data):
        for address in _element_devices(element):
            if parse_device_address(address, plc_model) is not None:
                continue
            findings.append(
                ReviewFinding(
                    # This rule mirrors hard validation but is still an advisory
                    # report item. A successfully persisted version must not be
                    # presented as newly invalid by the post-generation review.
                    severity="warning",
                    category="model_compatibility",
                    address=address,
                    json_path=path,
                    rung_ids=[rung.get("rung_id")],
                    message=f"{address} 与所选 {plc_model} 的软元件或编址规则不兼容。",
                    suggestion="按所选 PLC 型号核对地址前缀、X/Y 进制和软元件范围。",
                    evidence=[f"{path} 使用 {address}"],
                )
            )
        opcode = str(element.get("opcode", "")).strip().upper()
        if opcode:
            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model)
            if spec is not None and not spec.supports_cpu(plc_model):
                replacement = spec.replacement_for_cpu(plc_model)
                suggestion = (
                    f"使用 {plc_model} 的 {replacement} 并重新核对操作数。"
                    if replacement
                    else f"改用 instruction capability contract 中支持 {plc_model} 的指令。"
                )
                findings.append(
                    ReviewFinding(
                        severity="warning",
                        category="model_compatibility",
                        address=None,
                        json_path=path,
                        rung_ids=[rung.get("rung_id")],
                        message=f"{plc_model} 不支持 {opcode} 指令。",
                        suggestion=suggestion,
                        evidence=[f"{path}.opcode = {opcode}"],
                    )
                )
    return findings


def _confirmed_io_rows(confirmed_spec):
    if not isinstance(confirmed_spec, Mapping):
        return []
    rows = confirmed_spec.get("io_table")
    return rows if isinstance(rows, list) else []


def _review_confirmed_io(data, confirmed_spec):
    rows = _confirmed_io_rows(confirmed_spec)
    if not rows:
        return []
    used = _all_used_devices(data)
    comments = {
        str(address).upper(): str(label)
        for address, label in (data.get("device_comments") or {}).items()
    }
    findings = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        address = str(row.get("address") or "").strip().upper()
        if not address or address in used:
            continue
        label = str(row.get("label") or comments.get(address) or "").strip()
        prefix_match = re.match(r"^(SM|SD|[XYMTCDS])", address)
        prefix = prefix_match.group(1) if prefix_match else ""
        # Internal registers, counters and relays are commonly consumed by an
        # HMI, SCADA, another task or an external communication peer.  Their
        # absence as a ladder contact is not proof that the confirmed
        # allocation was omitted.  Keep this rule for physical X/Y points only.
        if prefix not in {"X", "Y"}:
            continue
        if NON_PROGRAM_OWNER_RE.search(label):
            continue
        # Alarm/safety points have a more specific rule below.  Reporting both
        # confirmed_io and alarm_logic for one unused address only duplicates
        # the same root cause.
        if FAULT_LABEL_RE.search(label):
            continue
        findings.append(
            ReviewFinding(
                severity="warning",
                category="confirmed_io",
                address=address,
                message=f"已确认 I/O {address}{'（' + label + '）' if label else ''} 未在程序逻辑中使用。",
                suggestion="确认该信号是否遗漏；若规格已变更，应先更新并重新确认规格。",
                evidence=[f"confirmed_spec.io_table 声明 {address}，梯形图未引用"],
                confidence="high",
            )
        )
    return findings


def _output_descriptor(output):
    output_type = str(output.get("type", "")).upper()
    if output_type == "COIL":
        return "COIL", [str(output.get("address", "")).upper()]
    if output_type not in {"APP_INSTR", "BLOCK_OUTPUT"}:
        return "", []
    opcode = str(output.get("opcode", "")).upper()
    operands = output.get("operands", []) or []
    if output_type == "BLOCK_OUTPUT" and not opcode:
        parts = str(output.get("expression", "")).split()
        opcode = parts[0].upper() if parts else ""
        operands = parts[1:]
    return opcode, [str(item).upper() for item in operands]


def _bit_writes(output):
    opcode, operands = _output_descriptor(output)
    if opcode == "COIL":
        address = operands[0] if operands else ""
        if BIT_OWNER_RE.fullmatch(address):
            yield address, "COIL"
        return
    if opcode not in WRITE_OPCODES:
        return
    if opcode in {"MOV", "DMOV"}:
        targets = operands[1:2]
    elif opcode == "ZRST":
        targets = operands[:2]
    else:
        targets = operands[:1]
    for operand in targets:
        if BIT_OWNER_RE.fullmatch(operand):
            yield operand, opcode


def _review_output_ownership(rungs):
    writers = {}
    for rung_idx, rung in enumerate(rungs):
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                for address, writer_type in _bit_writes(output):
                    writers.setdefault(address, []).append(
                        {
                            "writer_type": writer_type,
                            "rung_id": rung.get("rung_id"),
                            "json_path": (
                                f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                                f".outputs[{output_idx}]"
                            ),
                        }
                    )
    findings = []
    for address, entries in sorted(writers.items()):
        if len(entries) < 2:
            continue
        rung_ids = {entry["rung_id"] for entry in entries}
        writer_types = {entry["writer_type"] for entry in entries}
        # SET/RST is the normal Mitsubishi solution for one held bit with
        # several independent set and reset conditions.  Unlike duplicate
        # COILs, spreading pure SET/RST writers across rungs is not itself a
        # conflict; scan order and reset priority are design semantics, not a
        # reason to reject the ownership pattern wholesale.
        if writer_types <= {"SET", "RST"}:
            continue
        if writer_types == {"COIL"}:
            # Duplicate COIL is already a hard validation error.
            continue
        findings.append(
            ReviewFinding(
                severity="warning",
                category="output_ownership",
                address=address,
                json_path=entries[0]["json_path"],
                rung_ids=sorted(item for item in rung_ids if isinstance(item, int)),
                message=(
                    f"{address} 在 {len(rung_ids)} 个梯级中被 {len(entries)} 处写入，"
                    f"写入方式包括 {', '.join(sorted(writer_types))}。"
                ),
                suggestion=(
                    "普通输出应集中为一个 COIL；保持型输出应集中 SET/RST 归属并说明复位优先级。"
                ),
                evidence=[entry["json_path"] for entry in entries],
            )
        )
    return findings


def _review_set_reset_ownership(data):
    rungs = data.get("rungs", []) or []
    comments = {
        str(address).upper(): str(label)
        for address, label in (data.get("device_comments") or {}).items()
    }
    ownership = {}
    for rung_idx, rung in enumerate(rungs):
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                opcode, operands = _output_descriptor(output)
                if opcode not in {"SET", "RST"} or not operands:
                    continue
                address = operands[0]
                if not BIT_OWNER_RE.fullmatch(address):
                    continue
                ownership.setdefault(address, {}).setdefault(opcode, []).append(
                    {
                        "rung_id": rung.get("rung_id"),
                        "path": (
                            f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                            f".outputs[{output_idx}]"
                        ),
                        "context": " ".join(
                            (
                                str(rung.get("debug_note", "")),
                                str(output.get("label", "")),
                                comments.get(address, ""),
                            )
                        ),
                    }
                )
    findings = []
    for address, operations in sorted(ownership.items()):
        sets = operations.get("SET", [])
        resets = operations.get("RST", [])
        if sets and resets:
            continue
        present = "SET" if sets else "RST"
        entries = sets or resets
        if any(NON_PROGRAM_OWNER_RE.search(item["context"]) for item in entries):
            continue
        severity = "warning" if address.startswith("Y") else "info"
        findings.append(
            ReviewFinding(
                severity=severity,
                category="set_reset_ownership",
                address=address,
                json_path=entries[0]["path"],
                rung_ids=sorted(
                    {
                        item["rung_id"]
                        for item in entries
                        if isinstance(item["rung_id"], int)
                    }
                ),
                message=(
                    f"当前版本只找到 {address} 的 {present} 写入，未在本程序中找到"
                    f"配对的 {'RST' if sets else 'SET'}。"
                ),
                suggestion=(
                    "若另一侧由 HMI、通信或其他程序负责，请在注释中注明；"
                    "否则确认保持位的置位、复位与扫描优先级。"
                ),
                evidence=[item["path"] for item in entries],
            )
        )
    return findings


def _state_writes_and_compares(rungs):
    writes = {}
    compares = {}
    for rung_idx, rung in enumerate(rungs):
        elements = []
        if isinstance(rung.get("header_element"), dict):
            elements.append((rung["header_element"], f"$.rungs[{rung_idx}].header_element"))
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            for input_idx, element in enumerate(branch.get("inputs", []) or []):
                elements.append(
                    (
                        element,
                        f"$.rungs[{rung_idx}].branches[{branch_idx}].inputs[{input_idx}]",
                    )
                )
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                opcode, operands = _output_descriptor(output)
                if opcode in {"MOV", "DMOV"} and len(operands) >= 2:
                    destination = operands[1]
                    if STATE_REGISTER_RE.fullmatch(destination):
                        value_match = CONSTANT_RE.fullmatch(operands[0])
                        value = int(value_match.group(1)) if value_match else None
                        path = f"$.rungs[{rung_idx}].branches[{branch_idx}].outputs[{output_idx}]"
                        labels = " ".join(
                            str(item.get("label", ""))
                            for item in (
                                (rung.get("shared_inputs", []) or [])
                                + (branch.get("inputs", []) or [])
                                + [output]
                            )
                            if isinstance(item, dict)
                        )
                        labels = " ".join((str(rung.get("debug_note", "")), labels))
                        writes.setdefault(destination, []).append(
                            {
                                "value": value,
                                "rung_id": rung.get("rung_id"),
                                "path": path,
                                "labels": labels,
                                "rung": rung,
                            }
                        )
        for element, path in elements:
            expression = str(element.get("expression", ""))
            match = re.search(
                r"(?:=|==)\s*(D\d+)\s+K(-?\d+)", expression, re.IGNORECASE
            )
            if not match:
                match = re.search(
                    r"(D\d+)\s*(?:=|==)\s*K(-?\d+)",
                    expression,
                    re.IGNORECASE,
                )
            if match:
                register, raw_value = match.group(1).upper(), match.group(2)
                compares.setdefault(register, []).append(
                    {
                        "value": int(raw_value),
                        "rung_id": rung.get("rung_id"),
                        "path": path,
                    }
                )
    return writes, compares


def _rung_has_initial_pulse(rung, plc_model):
    valid = {"M8002"} if plc_model == "FX3U" else {"SM8002", "SM402"}
    candidates = list(rung.get("shared_inputs", []) or [])
    header = rung.get("header_element")
    if isinstance(header, dict):
        candidates.append(header)
    for branch in rung.get("branches", []) or []:
        candidates.extend(branch.get("inputs", []) or [])
    for element in candidates:
        if isinstance(element, dict) and str(element.get("address", "")).upper() in valid:
            return True
    return False


def _review_state_machine(data, plc_model):
    rungs = data.get("rungs", []) or []
    comments = {
        str(address).upper(): str(label)
        for address, label in (data.get("device_comments") or {}).items()
    }
    writes, compares = _state_writes_and_compares(rungs)
    findings = []
    for address in sorted(set(writes) | set(compares)):
        write_entries = writes.get(address, [])
        compare_entries = compares.get(address, [])
        written_values = {
            item["value"] for item in write_entries if item["value"] is not None
        }
        compared_values = {item["value"] for item in compare_entries}
        # Multiple writes alone do not make a register a state machine: recipe
        # numbers, speed presets, commands and HMI settings use the same shape.
        # Require both several constant state writes and several state compares
        # before applying state-machine-specific checks.
        if len(written_values) < 2 or len(compared_values) < 2:
            continue
        context = " ".join(
            [comments.get(address, "")]
            + [str(item.get("labels", "")) for item in write_entries]
        )
        if NON_PROGRAM_OWNER_RE.search(context) or NON_STATE_REGISTER_RE.search(context):
            continue
        rung_ids = sorted(
            {
                item["rung_id"]
                for item in write_entries + compare_entries
                if isinstance(item.get("rung_id"), int)
            }
        )
        initialized = any(
            _rung_has_initial_pulse(item["rung"], plc_model) for item in write_entries
        )
        if not initialized:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    category="state_initialization",
                    address=address,
                    json_path=(write_entries or compare_entries)[0]["path"],
                    rung_ids=rung_ids,
                    message=f"状态寄存器 {address} 未找到 {plc_model} 首扫脉冲初始化路径。",
                    suggestion="在首扫脉冲中明确写入初始状态，并核对掉电保持策略。",
                    evidence=["多状态写入/比较存在，但没有型号对应的首扫触点"],
                )
            )
        unhandled = []
        for value in sorted(written_values - compared_values):
            labels = " ".join(
                str(item.get("labels", ""))
                for item in write_entries
                if item.get("value") == value
            )
            if not TERMINAL_STATE_RE.search(labels):
                unhandled.append(value)
        # A single unhandled value is commonly an intentional terminal state.
        # Only several unlabelled targets are strong enough to raise a finding,
        # and keep it informational because external/HMI transitions may exist.
        if len(unhandled) > 1:
            findings.append(
                ReviewFinding(
                    severity="info",
                    category="state_transition",
                    address=address,
                    json_path=write_entries[0]["path"] if write_entries else compare_entries[0]["path"],
                    rung_ids=rung_ids,
                    message=f"{address} 的状态值 {unhandled} 未找到对应的状态处理梯级。",
                    suggestion="确认这些值是否为终态、外部处理状态，或确实遗漏了处理逻辑。",
                    evidence=[f"写入值={sorted(written_values)}，比较值={sorted(compared_values)}"],
                )
            )
    return findings


def _flatten_review_inputs(elements):
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "parallel_block":
            for branch in element.get("branches", []) or []:
                yield from _flatten_review_inputs(branch)
        else:
            yield element


def _review_same_scan_set_reset_toggle(rungs):
    findings = []
    for rung_idx, rung in enumerate(rungs or []):
        shared_inputs = list(_flatten_review_inputs(rung.get("shared_inputs", []) or []))
        has_shared_edge = any(
            str(item.get("type", "")).upper() in {"P", "RISING", "F", "FALLING"}
            for item in shared_inputs
        )
        toggle_text = str(rung.get("debug_note", "") or "")
        guarded_sets = {}
        guarded_resets = {}
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            branch_inputs = list(_flatten_review_inputs(branch.get("inputs", []) or []))
            toggle_text += " " + " ".join(
                str(item.get("label", "") or "") for item in branch_inputs
            )
            guards = {
                (
                    str(item.get("type", "")).upper(),
                    str(item.get("address", "")).upper(),
                )
                for item in branch_inputs
            }
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                toggle_text += " " + str(output.get("label", "") or "")
                if str(output.get("type", "")).upper() != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "")).upper()
                operands = output.get("operands", []) or []
                if opcode not in {"SET", "RST"} or not operands:
                    continue
                address = str(operands[0]).upper()
                entry = {
                    "rung_id": rung.get("rung_id"),
                    "path": f"$.rungs[{rung_idx}].branches[{branch_idx}].outputs[{output_idx}]",
                }
                if opcode == "SET" and ("NC", address) in guards:
                    guarded_sets[address] = entry
                if opcode == "RST" and ("NO", address) in guards:
                    guarded_resets[address] = entry
        toggle_intent = bool(
            re.search(
                r"翻转|交替|切换|toggle|alternate|\bALT\b",
                toggle_text,
                re.IGNORECASE,
            )
        )
        for address in sorted(set(guarded_sets).intersection(guarded_resets)):
            if not (has_shared_edge or toggle_intent):
                continue
            evidence = [guarded_sets[address]["path"], guarded_resets[address]["path"]]
            findings.append(
                ReviewFinding(
                    severity="error",
                    category="set_reset_toggle",
                    address=address,
                    json_path=evidence[0],
                    rung_ids=[rung.get("rung_id")]
                    if isinstance(rung.get("rung_id"), int)
                    else [],
                    message=(
                        f"{address} 在同一触发下用互补触点分别 SET/RST；SET 后后续"
                        "分支可能立即看到新值并在同一扫描 RST，不能可靠翻转。"
                    ),
                    suggestion=(
                        "改为两个相位各自拥有定时器/状态转换，或使用当前型号和"
                        "校验器明确支持的翻转指令。"
                    ),
                    evidence=evidence,
                )
            )
    return findings


def _timer_review_text(data, timer_address):
    comments = data.get("device_comments", {}) or {}
    parts = [str(comments.get(timer_address, ""))]
    for rung in data.get("rungs", []) or []:
        shared = list(rung.get("shared_inputs", []) or [])
        for branch in rung.get("branches", []) or []:
            inputs = list(
                _flatten_review_inputs(shared + list(branch.get("inputs", []) or []))
            )
            outputs = list(branch.get("outputs", []) or [])
            mentions = any(
                str(item.get("address", "")).upper() == timer_address
                for item in inputs + outputs
            )
            if not mentions:
                continue
            parts.append(str(rung.get("debug_note", "") or ""))
            for item in inputs + outputs:
                parts.append(str(item.get("label", "") or ""))
                address = str(item.get("address", "")).upper()
                if address:
                    parts.append(str(comments.get(address, "")))
    return " ".join(parts)


def _review_timer_and_counter_paths(data):
    rungs = data.get("rungs", []) or []
    devices = {}
    for rung_idx, rung in enumerate(rungs):
        shared_inputs = rung.get("shared_inputs", []) or []
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            inputs = shared_inputs + (branch.get("inputs", []) or [])
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                output_type = str(output.get("type", "")).upper()
                path = f"$.rungs[{rung_idx}].branches[{branch_idx}].outputs[{output_idx}]"
                if output_type in {"TIMER", "COUNTER"}:
                    address = str(output.get("address", "")).upper()
                    input_elements = list(_flatten_review_inputs(inputs))
                    is_counter = output_type == "COUNTER" or address.startswith("C")
                    devices.setdefault(address, []).append(
                        {
                            "rung_id": rung.get("rung_id"),
                            "path": path,
                            "is_counter": is_counter,
                            "edge_only": bool(input_elements) and all(
                                str(item.get("type", "")).upper()
                                in {"P", "RISING", "F", "FALLING"}
                                for item in input_elements
                            ),
                            "always_on_only": (
                                rung.get("header_element") is None
                                and len(input_elements) == 1
                                and str(input_elements[0].get("type", "")).upper() == "NO"
                                and str(input_elements[0].get("address", "")).upper()
                                in {"M8000", "SM8000"}
                            ),
                            "cyclic_intent": bool(
                                CYCLIC_TIMER_RE.search(_timer_review_text(data, address))
                            ),
                        }
                    )
    findings = []
    for address, entries in sorted(devices.items()):
        is_counter = all(item["is_counter"] for item in entries)
        category = "counter_path" if is_counter else "timer_path"
        if len(entries) > 1:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    category=category,
                    address=address,
                    json_path=entries[0]["path"],
                    rung_ids=sorted(
                        {
                            item["rung_id"]
                            for item in entries
                            if isinstance(item["rung_id"], int)
                        }
                    ),
                    message=f"{address} 在多个位置被定义为{'计数器' if is_counter else '定时器'}输出。",
                    suggestion="同一 T/C 软元件应保留一个明确的输出定义；合并使能条件或改用不同地址。",
                    evidence=[item["path"] for item in entries],
                )
            )
        # A counter is normally driven by pulses or edge events.  The enable
        # must stay true only for timers; applying that rule to C devices turns
        # the standard P/F counter pattern into a false warning.
        if not is_counter and any(item["edge_only"] for item in entries):
            findings.append(
                ReviewFinding(
                    severity="warning",
                    category="timer_path",
                    address=address,
                    json_path=entries[0]["path"],
                    rung_ids=[item["rung_id"] for item in entries if isinstance(item["rung_id"], int)],
                    message=f"{address} 仅由边沿触点驱动，条件可能无法保持到定时完成。",
                    suggestion="为定时器增加保持条件，或改为持续成立的使能逻辑。",
                    evidence=[item["path"] for item in entries if item["edge_only"]],
                )
            )
        if not is_counter and any(
            item["always_on_only"] and item["cyclic_intent"] for item in entries
        ):
            invalid_entries = [
                item
                for item in entries
                if item["always_on_only"] and item["cyclic_intent"]
            ]
            findings.append(
                ReviewFinding(
                    severity="error",
                    category="timer_oscillator",
                    address=address,
                    json_path=invalid_entries[0]["path"],
                    rung_ids=[
                        item["rung_id"]
                        for item in invalid_entries
                        if isinstance(item["rung_id"], int)
                    ],
                    message=(
                        f"{address} 仅由运行常通触点持续驱动；到时后触点会保持，"
                        "不能形成周期闪烁。"
                    ),
                    suggestion=(
                        "周期匹配时直接使用该型号已定义的时钟继电器；否则建立"
                        "能明确断开定时器使能的振荡/复位路径。"
                    ),
                    evidence=[item["path"] for item in invalid_entries],
                )
            )
    return findings


def _declared_completion_semantics(output, plc_model):
    if not isinstance(output, Mapping) or output.get("type") != "APP_INSTR":
        return None
    opcode = str(output.get("opcode", "") or "").strip().upper()
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model)
    return spec.completion if spec is not None else None


def _review_instruction_completion(rungs, plc_model):
    """Review placement from per-instruction completion ownership metadata."""

    findings = []
    for rung_idx, rung in enumerate(rungs):
        branches = rung.get("branches", []) or []
        owners = {}
        for branch_idx, branch in enumerate(branches):
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                completion = _declared_completion_semantics(output, plc_model)
                if completion is None:
                    continue
                owners.setdefault(completion.device, []).append(
                    (
                        branch_idx,
                        f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                        f".outputs[{output_idx}]",
                    )
                )

        for completion_device, owner_rows in sorted(owners.items()):
            owner_branches = {branch_idx for branch_idx, _ in owner_rows}
            owner_paths = [path for _, path in owner_rows]
            invalid_paths = []

            for branch_idx, branch in enumerate(branches):
                if branch_idx not in owner_branches:
                    continue
                for input_idx, element in enumerate(branch.get("inputs", []) or []):
                    if str(element.get("address", "")).upper() == completion_device:
                        invalid_paths.append(
                            f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                            f".inputs[{input_idx}]"
                        )

            for shared_idx, element in enumerate(rung.get("shared_inputs", []) or []):
                if str(element.get("address", "")).upper() == completion_device:
                    invalid_paths.append(
                        f"$.rungs[{rung_idx}].shared_inputs[{shared_idx}]"
                    )
            header = rung.get("header_element")
            if (
                isinstance(header, Mapping)
                and str(header.get("address", "")).upper() == completion_device
            ):
                invalid_paths.append(f"$.rungs[{rung_idx}].header_element")

            if invalid_paths:
                findings.append(
                    ReviewFinding(
                        severity="warning",
                        category="motion_completion",
                        address=completion_device,
                        json_path=invalid_paths[0],
                        rung_ids=(
                            [rung.get("rung_id")]
                            if isinstance(rung.get("rung_id"), int)
                            else []
                        ),
                        message=(
                            f"{completion_device} 与其 owning instruction 串联"
                            "或放在公共使能位置。"
                        ),
                        suggestion=(
                            "若使用该 completion flag，应按 capability contract "
                            "放在同一梯级的独立并联完成分支；若已确认采用其他完成"
                            "策略，则不要添加该 flag。"
                        ),
                        evidence=owner_paths + invalid_paths,
                    )
                )
    return findings

def _review_alarm_logic(data, confirmed_spec):
    used = _all_used_devices(data)
    comments = {
        str(address).upper(): str(label)
        for address, label in (data.get("device_comments") or {}).items()
    }
    declared = {}
    for row in _confirmed_io_rows(confirmed_spec):
        if not isinstance(row, Mapping):
            continue
        address = str(row.get("address") or "").upper()
        label = str(row.get("label") or "")
        if address and FAULT_LABEL_RE.search(label):
            declared.setdefault(address, []).append(label)
    for address, label in comments.items():
        if FAULT_LABEL_RE.search(label):
            declared.setdefault(address, []).append(label)
    findings = []
    for address, labels in declared.items():
        if address in used:
            continue
        unique_labels = list(dict.fromkeys(item for item in labels if item))
        label = " / ".join(unique_labels)
        safety = bool(SAFETY_LABEL_RE.search(label))
        findings.append(
            ReviewFinding(
                # Missing safety/alarm usage is serious engineering evidence,
                # but the reviewer cannot prove the wiring intent. Keep it a
                # non-blocking warning; only deterministic hard validation may
                # label a successfully generated version as erroneous.
                severity="warning",
                category="alarm_logic",
                address=address,
                message=f"已声明的{'安全' if safety else '故障/报警'}信号 {address}（{label}）未进入程序逻辑。",
                suggestion="人工核对检测、停机、锁存、确认与复位链路；安全信号不得自动修复。",
                evidence=[f"规格或注释声明 {address}={label}，梯形图未引用"],
                safety_related=safety,
                fixable=False,
            )
        )
    return findings


def _request_observations(request):
    if not isinstance(request, Mapping):
        return []
    values = request.get("observations")
    if values is None:
        values = request.get("observed_values", request.get("观测地址/值/时刻"))
    if isinstance(values, Mapping):
        values = [dict(values)]
    if not isinstance(values, list):
        values = [values] if values else []
    result = []
    for item in values:
        if isinstance(item, Mapping):
            address = str(item.get("address") or item.get("device") or "").upper()
        else:
            match = DEVICE_RE.search(str(item))
            address = match.group(0).upper() if match else ""
        if address:
            result.append(address)
    return result


def _review_online_observations(data, request):
    observations = _request_observations(request)
    if not observations:
        return []
    used = _all_used_devices(data)
    findings = []
    for address in observations:
        if address in used:
            continue
        findings.append(
            ReviewFinding(
                severity="info",
                category="online_observation",
                address=address,
                message=f"手工观测地址 {address} 未在所选版本中引用。",
                suggestion="确认观测值是否来自正确版本、正确 PLC 和正确时刻。",
                evidence=[f"调试请求包含 {address}，基础 JSON 未引用"],
                confidence="high",
            )
        )
    return findings


def _deduplicate_findings(findings):
    result = []
    seen = set()
    for finding in findings:
        key = (
            finding.category,
            finding.address,
            finding.json_path,
            finding.message,
        )
        if key not in seen:
            result.append(finding)
            seen.add(key)
    return result


def findings_to_dicts(findings):
    return [
        finding.to_dict() if hasattr(finding, "to_dict") else dict(finding)
        for finding in findings
    ]


def format_findings(findings):
    if not findings:
        return ["评审建议：暂无"]
    lines = []
    for finding in findings:
        payload = finding if isinstance(finding, dict) else finding.to_dict()
        prefix = SEVERITY_LABELS.get(
            payload.get("severity", "info"),
            str(payload.get("severity", "info")).upper(),
        )
        category = CATEGORY_LABELS.get(
            payload.get("category", "review"), payload.get("category", "review")
        )
        address = payload.get("address") or "-"
        lines.append(
            f"[{prefix}] {category} {address}: "
            f"{payload.get('message', '')} 建议：{payload.get('suggestion', '')}"
        )
    return lines


__all__ = [
    "ReviewFinding",
    "findings_to_dicts",
    "format_findings",
    "review_ladder",
]
