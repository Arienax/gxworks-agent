"""Deterministic PLC scan-cycle and state-machine semantic analysis.

The legacy ladder JSON remains the interchange format used by the model and GX
Works2 renderer.  This module derives a machine-readable Logic IR from that
ladder without changing rung order or execution behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SEMANTICS_SCHEMA_VERSION = 1
SUPPORTED_EXECUTION_SEMANTICS = (
    "LEVEL",
    "RISING_EDGE",
    "FALLING_EDGE",
    "FIRST_SCAN",
    "CYCLIC",
    "INTERRUPT",
)

LOGIC_REGIONS = (
    ("A", "SAFETY", "Safety"),
    ("B", "STATE_TRANSITION", "State Transition"),
    ("C", "STATE_OUTPUT", "State Output"),
    ("D", "ALARM", "Alarm"),
    ("E", "HMI", "HMI"),
    ("F", "DIAGNOSTICS", "Diagnostics"),
    ("G", "CONTROL", "Control"),
)

_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S)\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)
_STATE_COMPARE_PATTERNS = (
    re.compile(r"^\s*(?:=|==)\s*(D\d+)\s+K(-?\d+)\s*$", re.I),
    re.compile(r"^\s*(D\d+)\s*(?:=|==)\s*K(-?\d+)\s*$", re.I),
)
_K_CONSTANT_RE = re.compile(r"^K(-?\d+)$", re.I)
_PERIOD_RE = re.compile(
    r"(?:每隔|每|周期(?:为|是)?|间隔(?:为|是)?)\s*"
    r"(\d+(?:\.\d+)?)\s*(ms|毫秒|秒|s|分钟|min)",
    re.I,
)
_PULSE_WIDTH_RE = re.compile(
    r"(?:输入\s*)?(?:脉冲宽度|脉冲宽|脉宽|高电平(?:宽度|持续时间)|"
    r"pulse\s*width|high\s*level\s*(?:width|duration)|on\s*time)"
    r"\s*(?:为|是|=|约|大约|最短|最小|至少)?\s*"
    r"(\d+(?:\.\d+)?)\s*(us|μs|µs|微秒|ms|毫秒|s|秒)",
    re.I,
)

_RISING_PATTERNS = (
    re.compile(r"上升沿|正沿|rising\s*edge|one[- ]?shot", re.I),
    re.compile(r"(?:每次|每当).{0,28}(?:按下|触发|到位|检测到|来料|接通)", re.I),
    re.compile(r"(?:按下|触发|到位|检测到|来料|接通).{0,12}(?:一次|瞬间)", re.I),
)
_FALLING_PATTERNS = (
    re.compile(r"下降沿|负沿|falling\s*edge", re.I),
    re.compile(r"(?:每次|每当).{0,28}(?:松开|断开|释放|变为OFF)", re.I),
    re.compile(r"(?:松开|断开|释放).{0,12}(?:一次|瞬间)", re.I),
)
_FIRST_SCAN_PATTERNS = (
    re.compile(r"首(?:次)?扫描|首扫|first\s*scan", re.I),
    re.compile(r"(?:上电|开机|进入\s*RUN|RUN后|启动时).{0,20}(?:初始化|初始|默认|清零|装载)", re.I),
    re.compile(r"(?:初始化|默认参数).{0,20}(?:上电|开机|一次|首扫)", re.I),
)
_CYCLIC_PATTERNS = (
    re.compile(r"每隔|周期执行|定期执行|循环执行|固定周期|cyclic|periodic", re.I),
    _PERIOD_RE,
)
_INTERRUPT_PATTERNS = (
    re.compile(r"中断(?:程序|任务|输入|处理|事件)?|interrupt", re.I),
)
_LEVEL_PATTERNS = (
    re.compile(r"电平|持续|只要|保持期间|当.{0,30}时|level", re.I),
)

_SAFETY_RE = re.compile(r"急停|安全|联锁|互锁|过载|停止许可|e[- ]?stop|safety|interlock", re.I)
_ALARM_RE = re.compile(r"报警|告警|故障|异常|alarm|fault", re.I)
_HMI_RE = re.compile(r"HMI|触摸屏|上位机|人机界面", re.I)
_DIAGNOSTICS_RE = re.compile(r"诊断|扫描监控|看门狗|状态字|错误码|diagnostic|watchdog", re.I)
_INITIALIZATION_RE = re.compile(r"初始化|初始值|默认值|默认参数|开机赋值|上电赋值|清零", re.I)


def _canonical_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _device_tokens(value: Any) -> List[str]:
    return [match.group(0).upper() for match in _DEVICE_TOKEN_RE.finditer(str(value or ""))]


def _first_scan_devices(plc_model: str) -> set[str]:
    return (
        {"SM402", "SM8002"}
        if str(plc_model or "").upper().startswith("FX5")
        else {"M8002"}
    )


def _always_on_devices(plc_model: str) -> set[str]:
    return (
        {"SM400", "SM8000"}
        if str(plc_model or "").upper().startswith("FX5")
        else {"M8000"}
    )


def _clock_periods(plc_model: str) -> Dict[str, float]:
    if str(plc_model or "").upper().startswith("FX5"):
        return {
            "SM409": 10.0,
            "SM410": 100.0,
            "SM411": 200.0,
            "SM412": 1000.0,
            "SM413": 2000.0,
            "SM414": 60000.0,
        }
    return {
        "M8011": 10.0,
        "M8012": 100.0,
        "M8013": 1000.0,
        "M8014": 60000.0,
    }


def _sentence_fragments(text: str) -> Iterable[str]:
    for fragment in re.split(r"[。！？!?；;\n]+", str(text or "")):
        fragment = _normalized_text(fragment)
        if fragment:
            yield fragment


def _period_ms(fragment: str) -> Optional[float]:
    match = _PERIOD_RE.search(fragment)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"秒", "s"}:
        value *= 1000.0
    elif unit in {"分钟", "min"}:
        value *= 60000.0
    return value


def _pulse_width_ms(fragment: str) -> Optional[float]:
    """Return an explicitly stated pulse width in milliseconds.

    The marker is deliberately mandatory.  A generic ``2 ms`` timer or cyclic
    period is not evidence that a physical input pulse is only 2 ms wide.
    """

    match = _PULSE_WIDTH_RE.search(fragment)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"us", "μs", "µs", "微秒"}:
        value /= 1000.0
    elif unit in {"s", "秒"}:
        value *= 1000.0
    return value if value > 0 else None


def _intent_devices(fragment: str, semantic: str) -> List[str]:
    """Return trigger devices, excluding action destinations in the sentence."""

    semantic_markers = {
        "RISING_EDGE": ("按下", "触发", "到位", "检测到", "来料", "接通", "上升沿", "正沿", "脉冲", "脉宽"),
        "FALLING_EDGE": ("松开", "断开", "释放", "下降沿", "负沿", "变为off", "脉冲", "脉宽"),
        "INTERRUPT": ("中断", "interrupt"),
        "LEVEL": ("电平", "持续", "只要", "保持", "当"),
    }
    if semantic in {"FIRST_SCAN", "CYCLIC"}:
        return []
    markers = semantic_markers.get(semantic, ())
    lowered = fragment.casefold()
    device_matches = list(_DEVICE_TOKEN_RE.finditer(fragment))
    all_devices = [match.group(0).upper() for match in device_matches]

    # Bind devices from the condition phrase that contains the semantic marker.
    # This deliberately stops at common condition/action separators so that a
    # request such as ``每次按下 X0 一次，INC D0`` binds X0, not D0.
    nearby: List[str] = []
    clause_boundaries = "，,。；;！？!?\n"
    action_separators = ("时", "后", "则", "然后", "→", "->")
    for marker in markers:
        marker = marker.casefold()
        start = 0
        while True:
            marker_at = lowered.find(marker, start)
            if marker_at < 0:
                break
            marker_end = marker_at + len(marker)
            left = max((lowered.rfind(ch, 0, marker_at) for ch in clause_boundaries), default=-1) + 1
            right_candidates = [
                pos
                for ch in clause_boundaries
                if (pos := lowered.find(ch, marker_end)) >= 0
            ]
            for separator in action_separators:
                pos = lowered.find(separator, marker_end)
                if pos >= 0:
                    right_candidates.append(pos)
            right = min(right_candidates, default=len(fragment))
            for match in device_matches:
                if match.start() >= left and match.end() <= right:
                    nearby.append(match.group(0).upper())
            start = marker_end
    if nearby:
        return list(dict.fromkeys(nearby))

    # If prose has no explicit separator, select only the closest device to the
    # marker. This covers compact forms such as ``X0上升沿`` without capturing
    # a later MOV/INC destination.
    marker_positions = [
        (pos, pos + len(marker))
        for marker in markers
        for pos in [lowered.find(marker.casefold())]
        if pos >= 0
    ]
    if marker_positions and device_matches:
        def distance(match: re.Match[str]) -> int:
            return min(
                max(marker_start - match.end(), match.start() - marker_end, 0)
                for marker_start, marker_end in marker_positions
            )

        closest = min(distance(match) for match in device_matches)
        if closest <= 12:
            return list(
                dict.fromkeys(
                    match.group(0).upper()
                    for match in device_matches
                    if distance(match) == closest
                )
            )
    # For an explicit edge/interrupt phrase with only one mentioned device, it
    # is unambiguously the source. With multiple devices, fail open on matching
    # rather than binding the action target as a trigger.
    return list(dict.fromkeys(all_devices)) if len(set(all_devices)) == 1 else []


def normalize_semantic_requirements(value: Any) -> List[Dict[str, Any]]:
    """Normalize persisted or inferred requirements into a stable shape."""

    if isinstance(value, Mapping):
        value = value.get("requirements", [])
    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    seen = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        semantic = str(raw.get("semantic") or raw.get("type") or "").upper()
        if semantic not in SUPPORTED_EXECUTION_SEMANTICS:
            continue
        devices = sorted(set(_device_tokens(" ".join(map(str, raw.get("devices") or [])))))
        record: Dict[str, Any] = {
            "semantic": semantic,
            "devices": devices,
            "evidence": _normalized_text(raw.get("evidence"))[:500],
            "source": str(raw.get("source") or "requirement").strip()[:80],
            "strict": bool(raw.get("strict", semantic in {"RISING_EDGE", "FALLING_EDGE", "FIRST_SCAN"})),
        }
        period = raw.get("period_ms")
        if period is not None:
            try:
                period = float(period)
            except (TypeError, ValueError):
                period = None
            if period is not None and period > 0:
                record["period_ms"] = period
        pulse_width = raw.get("pulse_width_ms", raw.get("minimum_pulse_width_ms"))
        if pulse_width is not None:
            try:
                pulse_width = float(pulse_width)
            except (TypeError, ValueError):
                pulse_width = None
            if pulse_width is not None and pulse_width > 0:
                record["pulse_width_ms"] = pulse_width
        key = (
            semantic,
            tuple(devices),
            record.get("period_ms"),
            record.get("pulse_width_ms"),
            record["evidence"].casefold(),
        )
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def infer_semantic_requirements(text: Any, source: str = "requirement") -> List[Dict[str, Any]]:
    """Extract explicit timing intent without using an LLM."""

    requirements: List[Dict[str, Any]] = []
    for fragment in _sentence_fragments(str(text or "")):
        semantic_hits: List[str] = []
        pulse_width = _pulse_width_ms(fragment)
        if any(pattern.search(fragment) for pattern in _RISING_PATTERNS):
            semantic_hits.append("RISING_EDGE")
        if any(pattern.search(fragment) for pattern in _FALLING_PATTERNS):
            semantic_hits.append("FALLING_EDGE")
        if any(pattern.search(fragment) for pattern in _FIRST_SCAN_PATTERNS):
            semantic_hits.append("FIRST_SCAN")
        if any(pattern.search(fragment) for pattern in _CYCLIC_PATTERNS):
            semantic_hits.append("CYCLIC")
        if any(pattern.search(fragment) for pattern in _INTERRUPT_PATTERNS):
            semantic_hits.append("INTERRUPT")
        # An explicitly dimensioned physical input pulse is a sampling
        # constraint even if the prose omits the word "edge".  Do not apply
        # this inference to Y/M/D or to a bare duration.
        if pulse_width is not None and any(
            device.startswith("X") for device in _device_tokens(fragment)
        ) and not any(
            item in semantic_hits for item in {"RISING_EDGE", "FALLING_EDGE"}
        ):
            semantic_hits.append("RISING_EDGE")
        if not semantic_hits and any(pattern.search(fragment) for pattern in _LEVEL_PATTERNS):
            semantic_hits.append("LEVEL")
        for semantic in semantic_hits:
            devices = _intent_devices(fragment, semantic)
            record: Dict[str, Any] = {
                "semantic": semantic,
                "devices": devices,
                "evidence": fragment[:500],
                "source": source,
                "strict": semantic in {"RISING_EDGE", "FALLING_EDGE", "FIRST_SCAN"},
            }
            period = _period_ms(fragment) if semantic == "CYCLIC" else None
            if period is not None:
                record["period_ms"] = period
            if pulse_width is not None and semantic in {
                "RISING_EDGE",
                "FALLING_EDGE",
                "INTERRUPT",
            }:
                record["pulse_width_ms"] = pulse_width
            requirements.append(record)
    return normalize_semantic_requirements(requirements)


def semantic_requirements_from_spec(
    confirmed_spec: Optional[Mapping[str, Any]],
    requirement_text: Any = "",
) -> List[Dict[str, Any]]:
    """Combine the immutable confirmed intent with the current request text."""

    values: List[Dict[str, Any]] = []
    if isinstance(confirmed_spec, Mapping):
        values.extend(
            normalize_semantic_requirements(
                confirmed_spec.get("execution_semantics") or []
            )
        )
        semantic_text_parts = [
            confirmed_spec.get("summary", ""),
            confirmed_spec.get("user_notes", ""),
        ]
        approach = confirmed_spec.get("selected_approach")
        if isinstance(approach, Mapping):
            semantic_text_parts.extend(
                [
                    approach.get("name", ""),
                    approach.get("description", ""),
                    approach.get("generation_guide", ""),
                ]
            )
        values.extend(
            infer_semantic_requirements(
                "\n".join(str(item or "") for item in semantic_text_parts),
                source="confirmed_spec",
            )
        )
    values.extend(infer_semantic_requirements(requirement_text, source="current_request"))
    return normalize_semantic_requirements(values)


def _walk_inputs(elements: Sequence[Mapping[str, Any]], base_path: str):
    for index, element in enumerate(elements or []):
        if not isinstance(element, Mapping):
            continue
        path = f"{base_path}[{index}]"
        if str(element.get("type") or "") == "parallel_block":
            for branch_index, branch in enumerate(element.get("branches") or []):
                yield from _walk_inputs(
                    branch,
                    f"{path}.branches[{branch_index}]",
                )
        else:
            yield element, path


def _rung_inputs(rung: Mapping[str, Any]):
    header = rung.get("header_element")
    if isinstance(header, Mapping):
        yield header, "header_element"
    yield from _walk_inputs(rung.get("shared_inputs") or [], "shared_inputs")
    for branch_index, branch in enumerate(rung.get("branches") or []):
        if isinstance(branch, Mapping):
            yield from _walk_inputs(
                branch.get("inputs") or [],
                f"branches[{branch_index}].inputs",
            )


def _input_semantic(element: Mapping[str, Any], plc_model: str) -> Tuple[str, Dict[str, Any]]:
    kind = str(element.get("type") or "").upper()
    address = str(element.get("address") or "").upper()
    details: Dict[str, Any] = {}
    if address:
        details["device"] = address
    if address in _first_scan_devices(plc_model):
        return "FIRST_SCAN", details
    clock_period = _clock_periods(plc_model).get(address)
    if clock_period is not None:
        details["period_ms"] = clock_period
        return "CYCLIC", details
    if kind in {"P", "RISING"}:
        return "RISING_EDGE", details
    if kind in {"F", "FALLING"}:
        return "FALLING_EDGE", details
    text = " ".join(
        (
            str(element.get("label") or ""),
            str(element.get("expression") or ""),
        )
    )
    if any(pattern.search(text) for pattern in _INTERRUPT_PATTERNS):
        details["declared_by"] = "label"
        return "INTERRUPT", details
    return "LEVEL", details


def _parse_state_compare(element: Any) -> Optional[Tuple[str, int]]:
    if not isinstance(element, Mapping):
        return None
    if str(element.get("type") or "").upper() not in {"BLOCK_INPUT", "COMPARE"}:
        return None
    expression = str(element.get("expression") or "")
    for pattern in _STATE_COMPARE_PATTERNS:
        match = pattern.fullmatch(expression)
        if match:
            return match.group(1).upper(), int(match.group(2))
    return None


def _output_parts(output: Mapping[str, Any]) -> Tuple[str, List[str]]:
    output_type = str(output.get("type") or "").upper()
    if output_type == "APP_INSTR":
        return str(output.get("opcode") or "").upper(), [
            str(value).strip().upper() for value in (output.get("operands") or [])
        ]
    if output_type == "BLOCK_OUTPUT":
        parts = str(output.get("expression") or "").strip().upper().split()
        return (parts[0], parts[1:]) if parts else ("", [])
    if output_type in {"COIL", "PLS", "PLF", "TIMER", "COUNTER"}:
        args = [str(output.get("address") or "").upper()]
        if output_type in {"TIMER", "COUNTER"}:
            args.append(str(output.get("value") or "").upper())
        return output_type, args
    return output_type, []


def _state_write(output: Mapping[str, Any]) -> Optional[Tuple[str, int]]:
    opcode, operands = _output_parts(output)
    if opcode not in {"MOV", "DMOV"} or len(operands) < 2:
        return None
    value = _K_CONSTANT_RE.fullmatch(operands[0])
    register = re.fullmatch(r"D\d+", operands[1], re.I)
    if not value or not register:
        return None
    return register.group(0).upper(), int(value.group(1))


def _network_text(network: Mapping[str, Any]) -> str:
    rung = network.get("ladder") or {}
    parts = [str(network.get("comment") or ""), str(rung.get("debug_note") or "")]
    for element, _path in _rung_inputs(rung):
        parts.extend(
            [
                str(element.get("label") or ""),
                str(element.get("address") or ""),
                str(element.get("expression") or ""),
            ]
        )
    for branch in rung.get("branches") or []:
        if not isinstance(branch, Mapping):
            continue
        for output in branch.get("outputs") or []:
            if not isinstance(output, Mapping):
                continue
            parts.extend(
                [
                    str(output.get("label") or ""),
                    str(output.get("address") or ""),
                    str(output.get("opcode") or ""),
                    " ".join(map(str, output.get("operands") or [])),
                ]
            )
    return " ".join(parts)


def _network_execution(network: Mapping[str, Any], plc_model: str) -> Dict[str, Any]:
    rung = network.get("ladder") or {}
    triggers = []
    seen = set()
    for element, path in _rung_inputs(rung):
        semantic, details = _input_semantic(element, plc_model)
        record = {"semantic": semantic, "path": path, **details}
        key = (semantic, path, details.get("device"), details.get("period_ms"))
        if key not in seen:
            seen.add(key)
            triggers.append(record)
    if not triggers:
        triggers.append({"semantic": "LEVEL", "path": "$constant_true"})
    semantics = []
    for semantic in SUPPORTED_EXECUTION_SEMANTICS:
        if any(item["semantic"] == semantic for item in triggers):
            semantics.append(semantic)
    execution_context = (
        "INTERRUPT"
        if "INTERRUPT" in semantics
        else "CYCLIC"
    )
    return {
        "execution_context": execution_context,
        "semantics": semantics,
        "triggers": triggers,
    }


def _state_machine_candidates(networks: Sequence[Mapping[str, Any]]):
    compares: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    writes: Dict[str, Dict[int, List[Dict[str, Any]]]] = {}
    for network in networks:
        rung = network.get("ladder") or {}
        header_state = _parse_state_compare(rung.get("header_element"))
        if header_state:
            register, value = header_state
            compares.setdefault(register, {}).setdefault(value, []).append(
                {
                    "network": network.get("id"),
                    "path": "header_element",
                    "label": str((rung.get("header_element") or {}).get("label") or ""),
                }
            )
        for branch_index, branch in enumerate(rung.get("branches") or []):
            if not isinstance(branch, Mapping):
                continue
            for output_index, output in enumerate(branch.get("outputs") or []):
                if not isinstance(output, Mapping):
                    continue
                state_write = _state_write(output)
                if state_write:
                    register, value = state_write
                    writes.setdefault(register, {}).setdefault(value, []).append(
                        {
                            "network": network.get("id"),
                            "path": f"branches[{branch_index}].outputs[{output_index}]",
                            "branch_index": branch_index,
                            "label": str(output.get("label") or ""),
                            "source_state": (
                                header_state[1]
                                if header_state and header_state[0] == register
                                else None
                            ),
                        }
                    )
    candidates = []
    for register in sorted(set(compares) | set(writes)):
        compare_values = set(compares.get(register, {}))
        write_values = set(writes.get(register, {}))
        if len(compare_values) >= 2 and len(write_values) >= 2:
            candidates.append((register, compares.get(register, {}), writes.get(register, {})))
    return candidates


def _branch_condition_summary(
    network: Mapping[str, Any], branch_index: int, plc_model: str
) -> Dict[str, Any]:
    rung = network.get("ladder") or {}
    branches = rung.get("branches") or []
    branch = branches[branch_index] if 0 <= branch_index < len(branches) else {}
    elements = list(_walk_inputs(rung.get("shared_inputs") or [], "shared_inputs"))
    elements.extend(
        _walk_inputs(
            branch.get("inputs") or [],
            f"branches[{branch_index}].inputs",
        )
    )
    conditions = []
    semantics = []
    devices = []
    for element, path in elements:
        semantic, details = _input_semantic(element, plc_model)
        semantics.append(semantic)
        devices.extend(_device_tokens(element.get("address") or element.get("expression") or ""))
        conditions.append(
            {
                "path": path,
                "type": str(element.get("type") or ""),
                "address": str(element.get("address") or "").upper(),
                "expression": str(element.get("expression") or ""),
                "label": str(element.get("label") or ""),
                "semantic": semantic,
                **({"period_ms": details["period_ms"]} if "period_ms" in details else {}),
            }
        )
    return {
        "conditions": conditions,
        "devices": sorted(set(devices)),
        "semantics": [
            item for item in SUPPORTED_EXECUTION_SEMANTICS if item in semantics
        ] or ["LEVEL"],
    }


def _build_state_machines(
    networks: Sequence[Mapping[str, Any]], plc_model: str
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    by_id = {str(item.get("id")): item for item in networks}
    state_machines = []
    action_regions: Dict[str, List[Dict[str, str]]] = {
        "STATE_TRANSITION": [],
        "STATE_OUTPUT": [],
    }
    for register, compares, writes in _state_machine_candidates(networks):
        values = sorted(set(compares) | set(writes))
        states = []
        for value in values:
            compare_refs = compares.get(value, [])
            labels = [str(ref.get("label") or "") for ref in compare_refs]
            label = next((item for item in labels if item), f"STATE_{value}")
            states.append(
                {
                    "value": value,
                    "name": label[:120],
                    "network_refs": sorted(
                        {str(ref.get("network")) for ref in compare_refs}
                    ),
                }
            )

        transitions = []
        initialization = []
        for target_value, refs in sorted(writes.items()):
            for ref in refs:
                network_id = str(ref.get("network") or "")
                network = by_id.get(network_id, {})
                path = str(ref.get("path") or "")
                action_regions["STATE_TRANSITION"].append(
                    {"network": network_id, "path": path}
                )
                from_value = ref.get("source_state")
                condition = _branch_condition_summary(
                    network,
                    int(ref.get("branch_index", 0)),
                    plc_model,
                )
                if "FIRST_SCAN" in (network.get("execution") or {}).get("semantics", []):
                    initialization.append(
                        {
                            "target_state": target_value,
                            "network": network_id,
                            "path": path,
                        }
                    )
                else:
                    transitions.append(
                        {
                            "id": f"{register}_{from_value if from_value is not None else 'ANY'}_{target_value}_{network_id}_{len(transitions) + 1}",
                            "from": from_value,
                            "to": target_value,
                            "network": network_id,
                            "path": path,
                            "label": str(ref.get("label") or "")[:160],
                            **condition,
                        }
                    )

        state_outputs = []
        transition_paths = {
            (item["network"], item["path"])
            for item in action_regions["STATE_TRANSITION"]
        }
        state_values = set(compares)
        for network in networks:
            rung = network.get("ladder") or {}
            header_state = _parse_state_compare(rung.get("header_element"))
            if not header_state or header_state[0] != register:
                continue
            for branch_index, branch in enumerate(rung.get("branches") or []):
                if not isinstance(branch, Mapping):
                    continue
                for output_index, output in enumerate(branch.get("outputs") or []):
                    path = f"branches[{branch_index}].outputs[{output_index}]"
                    ref_key = (str(network.get("id")), path)
                    if ref_key in transition_paths:
                        continue
                    opcode, operands = _output_parts(output)
                    action = {
                        "state": header_state[1],
                        "network": str(network.get("id")),
                        "path": path,
                        "op": opcode,
                        "args": operands,
                        "label": str(output.get("label") or "")[:160],
                    }
                    state_outputs.append(action)
                    action_regions["STATE_OUTPUT"].append(
                        {"network": action["network"], "path": path}
                    )

        transition_networks = {item["network"] for item in transitions}
        output_networks = {item["network"] for item in state_outputs}
        state_machines.append(
            {
                "id": f"SM_{register}",
                "type": "register_state_machine",
                "state_register": register,
                "states": states,
                "initialization": initialization,
                "transitions": transitions,
                "state_outputs": state_outputs,
                "separation": {
                    "logical_regions_separate": True,
                    "mixed_physical_networks": sorted(
                        transition_networks.intersection(output_networks)
                    ),
                },
                "unreachable_state_candidates": sorted(
                    state_values
                    - {item["to"] for item in transitions}
                    - {item["target_state"] for item in initialization}
                ),
                "dead_end_state_candidates": sorted(
                    value
                    for value in state_values
                    if not any(item.get("from") == value for item in transitions)
                ),
            }
        )
    for key in action_regions:
        unique = []
        seen = set()
        for ref in action_regions[key]:
            marker = (ref["network"], ref["path"])
            if marker not in seen:
                seen.add(marker)
                unique.append(ref)
        action_regions[key] = unique
    return state_machines, action_regions


def _all_output_refs(network: Mapping[str, Any]):
    rung = network.get("ladder") or {}
    for branch_index, branch in enumerate(rung.get("branches") or []):
        if not isinstance(branch, Mapping):
            continue
        for output_index, _output in enumerate(branch.get("outputs") or []):
            yield {
                "network": str(network.get("id") or ""),
                "path": f"branches[{branch_index}].outputs[{output_index}]",
            }


def _build_regions(
    networks: Sequence[Mapping[str, Any]],
    state_action_regions: Mapping[str, Sequence[Mapping[str, str]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    refs: Dict[str, List[Dict[str, str]]] = {
        kind: [] for _code, kind, _label in LOGIC_REGIONS
    }
    refs["STATE_TRANSITION"].extend(
        copy.deepcopy(list(state_action_regions.get("STATE_TRANSITION") or []))
    )
    refs["STATE_OUTPUT"].extend(
        copy.deepcopy(list(state_action_regions.get("STATE_OUTPUT") or []))
    )
    assigned_action_refs = {
        (item.get("network"), item.get("path"))
        for kind in ("STATE_TRANSITION", "STATE_OUTPUT")
        for item in refs[kind]
    }
    network_regions: Dict[str, List[str]] = {}
    for network in networks:
        network_id = str(network.get("id") or "")
        text = _network_text(network)
        text_regions = []
        if _SAFETY_RE.search(text):
            text_regions.append("SAFETY")
        if _ALARM_RE.search(text):
            text_regions.append("ALARM")
        if _HMI_RE.search(text):
            text_regions.append("HMI")
        if _DIAGNOSTICS_RE.search(text):
            text_regions.append("DIAGNOSTICS")
        for action_ref in _all_output_refs(network):
            key = (action_ref["network"], action_ref["path"])
            if key in assigned_action_refs:
                continue
            region = text_regions[0] if text_regions else "CONTROL"
            refs[region].append(action_ref)
        present = {
            kind
            for kind, items in refs.items()
            if any(item.get("network") == network_id for item in items)
        }
        network_regions[network_id] = [
            kind for _code, kind, _label in LOGIC_REGIONS if kind in present
        ] or ["CONTROL"]

    regions = []
    for code, kind, label in LOGIC_REGIONS:
        unique = []
        seen = set()
        for item in refs[kind]:
            marker = (str(item.get("network") or ""), str(item.get("path") or ""))
            if marker not in seen:
                seen.add(marker)
                unique.append({"network": marker[0], "path": marker[1]})
        regions.append(
            {
                "code": code,
                "kind": kind,
                "label": label,
                "action_refs": unique,
                "network_refs": sorted({item["network"] for item in unique}),
            }
        )
    return regions, network_regions


def _semantic_coverage(
    requirements: Sequence[Mapping[str, Any]],
    networks: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    coverage = []
    for index, requirement in enumerate(requirements):
        semantic = str(requirement.get("semantic") or "")
        devices = set(requirement.get("devices") or [])
        candidates = []
        for network in networks:
            execution = network.get("execution") or {}
            if semantic not in (execution.get("semantics") or []):
                continue
            network_devices = {
                str(trigger.get("device") or "")
                for trigger in execution.get("triggers") or []
                if trigger.get("device")
            }
            if devices and not devices.intersection(network_devices):
                continue
            candidates.append(str(network.get("id") or ""))
        if candidates:
            status = "satisfied"
            message = "已在程序中找到匹配的扫描语义。"
        elif semantic == "INTERRUPT":
            status = "unsupported_in_main_ladder"
            message = "当前 MAIN 梯形图没有可验证的中断任务元数据。"
        else:
            status = "unresolved"
            message = "未在当前程序中找到匹配的扫描语义。"
        coverage.append(
            {
                "requirement_id": f"SEM{index + 1:03d}",
                "semantic": semantic,
                "devices": sorted(devices),
                "strict": bool(requirement.get("strict")),
                "status": status,
                "network_refs": candidates,
                "message": message,
                **(
                    {"pulse_width_ms": requirement["pulse_width_ms"]}
                    if requirement.get("pulse_width_ms") is not None
                    else {}
                ),
                **(
                    {"period_ms": requirement["period_ms"]}
                    if requirement.get("period_ms") is not None
                    else {}
                ),
            }
        )
    return coverage


def _initialization_analysis(
    networks: Sequence[Mapping[str, Any]], plc_model: str
) -> List[Dict[str, Any]]:
    records = []
    always_on = _always_on_devices(plc_model)
    for network in networks:
        text = _network_text(network)
        if not _INITIALIZATION_RE.search(text):
            continue
        execution = network.get("execution") or {}
        trigger_devices = {
            str(item.get("device") or "") for item in execution.get("triggers") or []
        }
        if "FIRST_SCAN" in (execution.get("semantics") or []):
            status = "first_scan"
        elif trigger_devices.intersection(always_on):
            status = "continuous_overwrite_risk"
        else:
            status = "conditional_initialization"
        records.append(
            {
                "network": str(network.get("id") or ""),
                "status": status,
                "trigger_devices": sorted(trigger_devices),
                "writes": list(network.get("writes") or []),
            }
        )
    return records


def analyze_program_semantics(
    networks: Sequence[Mapping[str, Any]],
    *,
    plc_model: str = "FX3U",
    requirements: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Annotate networks and return deterministic Logic IR + timing summary."""

    normalized_requirements = normalize_semantic_requirements(list(requirements or []))
    annotated = [copy.deepcopy(dict(network)) for network in networks]
    for network in annotated:
        network["execution"] = _network_execution(network, plc_model)
    state_machines, state_action_regions = _build_state_machines(annotated, plc_model)
    regions, network_regions = _build_regions(annotated, state_action_regions)
    for network in annotated:
        network["regions"] = network_regions.get(str(network.get("id")), ["CONTROL"])

    edge_triggers = []
    first_scan_networks = []
    cyclic_sources = []
    interrupt_networks = []
    for network in annotated:
        execution = network["execution"]
        if "FIRST_SCAN" in execution["semantics"]:
            first_scan_networks.append(str(network.get("id")))
        if "INTERRUPT" in execution["semantics"]:
            interrupt_networks.append(str(network.get("id")))
        for trigger in execution["triggers"]:
            semantic = trigger["semantic"]
            if semantic in {"RISING_EDGE", "FALLING_EDGE"} and trigger.get("device"):
                edge_triggers.append(
                    {
                        "network": str(network.get("id")),
                        "device": trigger["device"],
                        "edge": "rising" if semantic == "RISING_EDGE" else "falling",
                    }
                )
            if semantic == "CYCLIC":
                cyclic_sources.append(
                    {
                        "network": str(network.get("id")),
                        "device": trigger.get("device"),
                        "period_ms": trigger.get("period_ms"),
                        "path": trigger["path"],
                    }
                )

    timers: Dict[str, Dict[str, Any]] = {}
    counters: Dict[str, Dict[str, Any]] = {}
    for network in annotated:
        rung = network.get("ladder") or {}
        for branch in rung.get("branches") or []:
            if not isinstance(branch, Mapping):
                continue
            for output in branch.get("outputs") or []:
                if not isinstance(output, Mapping):
                    continue
                output_type = str(output.get("type") or "").upper()
                address = str(output.get("address") or "").upper()
                record = {
                    "network": str(network.get("id")),
                    "preset": str(output.get("value") or "K0").upper(),
                }
                if output_type == "TIMER" and address:
                    timers[address] = record
                elif output_type == "COUNTER" and address:
                    counters[address] = record

    coverage = _semantic_coverage(normalized_requirements, annotated)
    initialization = _initialization_analysis(annotated, plc_model)
    timing = {
        "schema_version": SEMANTICS_SCHEMA_VERSION,
        "scan_model": "cyclic",
        "supported_semantics": list(SUPPORTED_EXECUTION_SEMANTICS),
        "network_semantics": [
            {
                "network": str(network.get("id")),
                **copy.deepcopy(network["execution"]),
            }
            for network in annotated
        ],
        "first_scan_networks": first_scan_networks,
        "edge_triggers": edge_triggers,
        "cyclic_sources": cyclic_sources,
        "interrupt_networks": interrupt_networks,
        "timers": timers,
        "counters": counters,
        "initialization": initialization,
        "requirements": copy.deepcopy(normalized_requirements),
        "coverage": coverage,
    }
    logic = {
        "schema_version": SEMANTICS_SCHEMA_VERSION,
        "execution_model": "plc_scan_cycle",
        "requirements": copy.deepcopy(normalized_requirements),
        "requirements_sha256": _canonical_hash(normalized_requirements),
        "regions": regions,
        "state_machines": state_machines,
    }
    return {"networks": annotated, "timing": timing, "logic": logic}


def strict_semantic_gaps(program_or_analysis: Mapping[str, Any]) -> List[Dict[str, Any]]:
    timing = (
        program_or_analysis.get("timing")
        if isinstance(program_or_analysis.get("timing"), Mapping)
        else program_or_analysis
    )
    return [
        copy.deepcopy(dict(item))
        for item in (timing.get("coverage") or [])
        if isinstance(item, Mapping)
        and item.get("strict")
        and item.get("status") != "satisfied"
    ]


__all__ = [
    "LOGIC_REGIONS",
    "SEMANTICS_SCHEMA_VERSION",
    "SUPPORTED_EXECUTION_SEMANTICS",
    "analyze_program_semantics",
    "infer_semantic_requirements",
    "normalize_semantic_requirements",
    "semantic_requirements_from_spec",
    "strict_semantic_gaps",
]
