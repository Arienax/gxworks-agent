"""Deterministic PLC static analysis and device dependency graph.

This module records facts separately from findings.  A read/write dependency
is useful for debugging but is not automatically an error.  Findings are only
created where the program or an explicit confirmed contract supplies enough
evidence; this keeps ordinary SET/RST distribution and HMI-owned data valid.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from plc.ir import analyze_instruction_access


STATIC_ANALYSIS_SCHEMA_VERSION = 1

RULE_IDS = (
    "MULTIPLE_WRITER",
    "LATCH_WITHOUT_RESET",
    "MUTEX_NOT_ENFORCED",
    "SCAN_ORDER_DEPENDENCY",
    "SAME_SCAN_READ_BEFORE_WRITE",
    "EDGE_MISUSE",
    "INIT_VALUE_OVERWRITE_WARNING",
    "TIMER_CANNOT_COMPLETE",
    "UNREACHABLE_STATE",
    "DEAD_END_STATE",
    "SCAN_BUDGET_WARNING",
    "PULSE_LOSS_WARNING",
)

_DEVICE_RE = re.compile(r"^(SM|SD|X|Y|M|D|T|C|S|V|Z)(\d+)$", re.I)
_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S|V|Z)\d+(?![A-Z0-9_])",
    re.I,
)
_NETWORK_RE = re.compile(r"^N(?:_|0*)(\d+)$", re.I)
_BRANCH_OUTPUT_RE = re.compile(r"branches\[(\d+)\]\.outputs\[(\d+)\]")
_EXTERNAL_OWNER_RE = re.compile(
    r"HMI|触摸屏|上位机|SCADA|外部|通信|监视|显示|读取|硬接线|"
    r"operator\s*panel|external|communication|monitor|display|read[- ]?only|hardwired",
    re.I,
)
_TERMINAL_RE = re.compile(
    r"结束|完成|终止|待机|等待人工|故障|报警|END|DONE|COMPLETE|TERMINAL|FAULT|ALARM",
    re.I,
)
_SAME_SCAN_RE = re.compile(r"同一扫描|本扫描|本周期立即|同周期立即|same[- ]?scan", re.I)


def _device(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if _DEVICE_RE.fullmatch(text) else ""


def _devices(value: Any) -> List[str]:
    return [match.group(0).upper() for match in _DEVICE_TOKEN_RE.finditer(str(value or ""))]


def _network_key(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"N\d+", text, re.I):
        return "N" + text[1:].zfill(4)
    if isinstance(value, int) or text.isdigit():
        return f"N{int(value):04d}"
    return text


def _number_with_unit(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    text = str(value or "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", text, re.I)
    if not match:
        return None
    number = float(match.group(1))
    if str(match.group(2) or "").lower() in {"s", "秒"}:
        number *= 1000.0
    return number if number > 0 else None


def _pair(value: Any, source: str = "confirmed_spec") -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        values = value.get("devices") or value.get("outputs") or value.get("pair")
        if values is None:
            values = [value.get("left"), value.get("right")]
        pair_source = str(value.get("source") or source)
    else:
        values = value
        pair_source = source
    if isinstance(values, str):
        devices = _devices(values)
    elif isinstance(values, Sequence):
        devices = [_device(item) for item in values]
        devices = [item for item in devices if item]
    else:
        devices = []
    devices = list(dict.fromkeys(devices))
    if len(devices) != 2 or any(not item.startswith("Y") for item in devices):
        return None
    return {"devices": sorted(devices), "source": pair_source}


def _normalize_expectation(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    device = _device(value.get("device") or value.get("address"))
    reader = _network_key(value.get("reader_network") or value.get("reader"))
    writer = _network_key(value.get("writer_network") or value.get("writer"))
    if not device or not reader:
        return None
    result = {"device": device, "reader_network": reader}
    if writer:
        result["writer_network"] = writer
    result["source"] = str(value.get("source") or "confirmed_spec")
    return result


def _parameter_map(spec: Mapping[str, Any]) -> Dict[str, Any]:
    result = {}
    for item in spec.get("parameters") or []:
        if not isinstance(item, Mapping):
            continue
        for key in (item.get("id"), item.get("name")):
            text = str(key or "").strip().casefold()
            if text:
                result[text] = item.get("value")
    return result


def normalize_analysis_config(
    value: Optional[Mapping[str, Any]] = None,
    *,
    confirmed_spec: Optional[Mapping[str, Any]] = None,
    devices: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize explicit contracts and conservatively infer one direction pair."""

    raw: Dict[str, Any] = {}
    if isinstance(value, Mapping):
        raw.update(copy.deepcopy(dict(value)))
    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    contracts = spec.get("logic_contracts") if isinstance(spec.get("logic_contracts"), Mapping) else {}

    mutex_values = raw.get("mutex")
    if mutex_values is None:
        mutex_values = spec.get("mutex", contracts.get("mutex", []))
    if isinstance(mutex_values, Mapping) or isinstance(mutex_values, str):
        mutex_values = [mutex_values]
    mutex_pairs = []
    seen_pairs = set()
    for item in mutex_values or []:
        record = _pair(item)
        if record and tuple(record["devices"]) not in seen_pairs:
            seen_pairs.add(tuple(record["devices"]))
            mutex_pairs.append(record)

    # Infer only an unambiguous single complementary Y pair.  This does not
    # invent a safety input or an output; it recognizes addresses already in
    # the confirmed map/program comments.
    labels = {}
    for address, record in (devices or {}).items():
        if not str(address).upper().startswith("Y") or not isinstance(record, Mapping):
            continue
        text = " ".join(
            (
                str(record.get("comment") or ""),
                str((record.get("io") or {}).get("label") or "")
                if isinstance(record.get("io"), Mapping)
                else "",
            )
        ).strip()
        if text:
            labels[str(address).upper()] = text
    for left_token, right_token in (
        ("正转", "反转"),
        ("forward", "reverse"),
        ("伸出", "缩回"),
        ("前进", "后退"),
    ):
        left = [address for address, label in labels.items() if left_token.casefold() in label.casefold()]
        right = [address for address, label in labels.items() if right_token.casefold() in label.casefold()]
        if len(left) == len(right) == 1 and left[0] != right[0]:
            key = tuple(sorted((left[0], right[0])))
            if key not in seen_pairs:
                seen_pairs.add(key)
                mutex_pairs.append({"devices": list(key), "source": "unambiguous_labels"})

    expectation_values = raw.get("same_scan_expectations")
    if expectation_values is None:
        expectation_values = spec.get(
            "same_scan_expectations", contracts.get("same_scan_expectations", [])
        )
    if isinstance(expectation_values, Mapping):
        expectation_values = [expectation_values]
    expectations = []
    seen_expectations = set()
    for item in expectation_values or []:
        record = _normalize_expectation(item)
        if not record:
            continue
        key = (
            record["device"],
            record["reader_network"],
            record.get("writer_network", ""),
        )
        if key not in seen_expectations:
            seen_expectations.add(key)
            expectations.append(record)

    terminal_values = raw.get("terminal_states")
    if terminal_values is None:
        terminal_values = spec.get("terminal_states", contracts.get("terminal_states", {}))
    terminal_states: Dict[str, List[int]] = {}
    if isinstance(terminal_values, Mapping):
        for register, values in terminal_values.items():
            address = _device(register)
            if not address.startswith("D"):
                continue
            if not isinstance(values, (list, tuple, set)):
                values = [values]
            parsed = []
            for state in values:
                try:
                    parsed.append(int(state))
                except (TypeError, ValueError):
                    pass
            if parsed:
                terminal_states[address] = sorted(set(parsed))

    timing_raw = raw.get("timing") if isinstance(raw.get("timing"), Mapping) else {}
    spec_timing = spec.get("timing") if isinstance(spec.get("timing"), Mapping) else {}
    parameters = _parameter_map(spec)
    budget = _number_with_unit(
        timing_raw.get("scan_budget_ms")
        or raw.get("scan_budget_ms")
        or spec_timing.get("scan_budget_ms")
        or spec.get("scan_budget_ms")
        or parameters.get("scan_budget_ms")
        or parameters.get("目标控制周期")
        or parameters.get("扫描预算")
    )
    warning = _number_with_unit(
        timing_raw.get("scan_warning_ms")
        or raw.get("scan_warning_ms")
        or spec_timing.get("scan_warning_ms")
        or spec.get("scan_warning_ms")
        or parameters.get("scan_warning_ms")
    ) or 15.0
    allocation = (
        timing_raw.get("allocation")
        or spec_timing.get("allocation")
        or raw.get("scan_allocation")
        or {}
    )
    if not isinstance(allocation, Mapping):
        allocation = {}

    return {
        "mutex": sorted(mutex_pairs, key=lambda item: tuple(item["devices"])),
        "same_scan_expectations": sorted(
            expectations,
            key=lambda item: (
                item["device"], item["reader_network"], item.get("writer_network", "")
            ),
        ),
        "terminal_states": dict(sorted(terminal_states.items())),
        "timing": {
            "scan_budget_ms": budget,
            "scan_warning_ms": warning,
            "allocation": copy.deepcopy(dict(allocation)),
        },
    }


def _writer_index(networks: Sequence[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for network in networks or []:
        for instruction in network.get("instructions") or []:
            if not isinstance(instruction, Mapping):
                continue
            reads, writes = analyze_instruction_access(
                instruction.get("op"), instruction.get("args") or []
            )
            for address in writes:
                raw_path = str(instruction.get("path") or "")
                full_path = (
                    f"$.rungs[{int(network.get('order') or 0)}].{raw_path}"
                    if raw_path
                    else f"$.rungs[{int(network.get('order') or 0)}]"
                )
                result.setdefault(address, []).append(
                    {
                        "network": str(network.get("id") or ""),
                        "order": int(network.get("order") or 0),
                        "rung_id": network.get("rung_id"),
                        "path": full_path,
                        "op": str(instruction.get("op") or "").upper(),
                        "reads": reads,
                    }
                )
    return {address: rows for address, rows in sorted(result.items())}


def build_device_dependency_graph(
    networks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build device and scan-order edges without assigning blame."""

    writers = _writer_index(networks)
    readers: Dict[str, List[Dict[str, Any]]] = {}
    direct_edges = {}
    nodes = set()
    for network in networks or []:
        network_id = str(network.get("id") or "")
        order = int(network.get("order") or 0)
        rung_id = network.get("rung_id")
        reads = list(network.get("reads") or [])
        writes = list(network.get("writes") or [])
        nodes.update(reads)
        nodes.update(writes)
        for address in reads:
            readers.setdefault(address, []).append(
                {"network": network_id, "order": order, "rung_id": rung_id}
            )
        for source in reads:
            for target in writes:
                key = (source, target, network_id)
                direct_edges[key] = {
                    "from": source,
                    "to": target,
                    "network": network_id,
                    "rung_id": rung_id,
                    "feedback": source == target,
                    "precision": "conservative_network_scope",
                }

    scan_edges = []
    seen_scan = set()
    for address in sorted(set(writers).intersection(readers)):
        for writer in writers[address]:
            for reader in readers[address]:
                if writer["network"] == reader["network"]:
                    relation = "within_network"
                elif writer["order"] < reader["order"]:
                    relation = "same_scan_write_before_read"
                else:
                    relation = "read_before_later_write"
                key = (address, writer["network"], reader["network"], relation)
                if key in seen_scan:
                    continue
                seen_scan.add(key)
                scan_edges.append(
                    {
                        "device": address,
                        "writer_network": writer["network"],
                        "writer_order": writer["order"],
                        "reader_network": reader["network"],
                        "reader_order": reader["order"],
                        "relation": relation,
                    }
                )

    forward: Dict[str, List[str]] = {}
    reverse: Dict[str, List[str]] = {}
    for edge in direct_edges.values():
        forward.setdefault(edge["from"], []).append(edge["to"])
        reverse.setdefault(edge["to"], []).append(edge["from"])
    for mapping in (forward, reverse):
        for address, targets in list(mapping.items()):
            mapping[address] = sorted(set(targets))

    return {
        "schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
        "precision": "conservative_network_scope",
        "nodes": sorted(nodes),
        "device_edges": sorted(
            direct_edges.values(),
            key=lambda item: (item["from"], item["to"], item["network"]),
        ),
        "forward": dict(sorted(forward.items())),
        "reverse": dict(sorted(reverse.items())),
        "writers": writers,
        "readers": {address: rows for address, rows in sorted(readers.items())},
        "network_edges": sorted(
            scan_edges,
            key=lambda item: (
                item["device"], item["writer_order"], item["reader_order"]
            ),
        ),
    }


def _finding(
    code: str,
    severity: str,
    message: str,
    suggestion: str,
    *,
    addresses: Sequence[str] = (),
    networks: Sequence[str] = (),
    rung_ids: Sequence[Any] = (),
    paths: Sequence[str] = (),
    evidence: Sequence[str] = (),
    confidence: str = "high",
) -> Dict[str, Any]:
    valid_rungs = []
    for value in rung_ids:
        if isinstance(value, bool):
            continue
        try:
            valid_rungs.append(int(value))
        except (TypeError, ValueError):
            pass
    return {
        "code": code,
        "category": code.lower(),
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
        "addresses": sorted(set(addresses)),
        "network_refs": sorted(set(networks)),
        "rung_ids": sorted(set(valid_rungs)),
        "json_paths": sorted(set(paths)),
        "evidence": list(dict.fromkeys(str(item) for item in evidence if str(item))),
        "confidence": confidence,
        "fixable": False,
    }


def _flatten_inputs(elements: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for element in elements or []:
        if not isinstance(element, Mapping):
            continue
        if str(element.get("type") or "") == "parallel_block":
            for branch in element.get("branches") or []:
                yield from _flatten_inputs(branch)
        else:
            yield element


def _series_nc_for_output(network: Mapping[str, Any], path: str, other: str) -> bool:
    rung = network.get("ladder") or {}
    candidates = []
    header = rung.get("header_element")
    if isinstance(header, Mapping):
        candidates.append(header)
    # A nested parallel block is not globally true, so only direct shared and
    # branch inputs can prove an interlock.
    candidates.extend(
        item
        for item in (rung.get("shared_inputs") or [])
        if isinstance(item, Mapping) and item.get("type") != "parallel_block"
    )
    match = _BRANCH_OUTPUT_RE.search(path)
    if match:
        index = int(match.group(1))
        branches = rung.get("branches") or []
        if index < len(branches) and isinstance(branches[index], Mapping):
            candidates.extend(
                item
                for item in (branches[index].get("inputs") or [])
                if isinstance(item, Mapping) and item.get("type") != "parallel_block"
            )
    return any(
        str(item.get("type") or "").upper() == "NC"
        and _device(item.get("address")) == other
        for item in candidates
    )


def _timer_findings(networks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for network in networks or []:
        rung = network.get("ladder") or {}
        shared = list(_flatten_inputs(rung.get("shared_inputs") or []))
        for branch_index, branch in enumerate(rung.get("branches") or []):
            if not isinstance(branch, Mapping):
                continue
            inputs = shared + list(_flatten_inputs(branch.get("inputs") or []))
            edge_only = bool(inputs) and all(
                str(item.get("type") or "").upper()
                in {"P", "RISING", "F", "FALLING"}
                for item in inputs
            )
            if not edge_only:
                continue
            for output_index, output in enumerate(branch.get("outputs") or []):
                if not isinstance(output, Mapping) or str(output.get("type") or "").upper() != "TIMER":
                    continue
                preset = str(output.get("value") or "K0").upper()
                match = re.fullmatch(r"K(-?\d+)", preset)
                if match and int(match.group(1)) <= 0:
                    continue
                address = _device(output.get("address"))
                path = f"$.rungs[{int(network.get('order') or 0)}].branches[{branch_index}].outputs[{output_index}]"
                findings.append(
                    _finding(
                        "TIMER_CANNOT_COMPLETE",
                        "warning",
                        f"{address} 只由单扫描边沿脉冲使能，非零定时值通常无法累计完成。",
                        "增加可保持到定时完成的使能条件，或用状态位保持定时阶段。",
                        addresses=[address],
                        networks=[str(network.get("id") or "")],
                        rung_ids=[network.get("rung_id")],
                        paths=[path],
                        evidence=[f"edge-only enable, preset={preset}"],
                    )
                )
    return findings


def analyze_static_program(
    networks: Sequence[Mapping[str, Any]],
    *,
    devices: Optional[Mapping[str, Any]] = None,
    timing: Optional[Mapping[str, Any]] = None,
    logic: Optional[Mapping[str, Any]] = None,
    config: Optional[Mapping[str, Any]] = None,
    plc_model: str = "FX3U",
) -> Dict[str, Any]:
    """Run all deterministic P4 rules over a canonical network list."""

    normalized_config = normalize_analysis_config(config, devices=devices)
    graph = build_device_dependency_graph(networks)
    writers = graph["writers"]
    by_id = {str(item.get("id") or ""): item for item in networks or []}
    findings: List[Dict[str, Any]] = []

    for address, rows in writers.items():
        if not address.startswith(("Y", "M")) or len(rows) < 2:
            continue
        kinds = {row["op"] for row in rows}
        if kinds <= {"SET", "RST"}:
            continue
        severity = "error" if kinds == {"OUT"} else "warning"
        findings.append(
            _finding(
                "MULTIPLE_WRITER",
                severity,
                f"{address} 有 {len(rows)} 个写入点，写入方式包括 {', '.join(sorted(kinds))}。",
                "普通线圈集中为一个输出所有者；保持位保留清晰的 SET/RST 优先级。",
                addresses=[address],
                networks=[row["network"] for row in rows],
                rung_ids=[row["rung_id"] for row in rows],
                paths=[row["path"] for row in rows],
                evidence=[f"{row['network']}:{row['op']}" for row in rows],
            )
        )

    for address, rows in writers.items():
        sets = [row for row in rows if row["op"] == "SET"]
        resets = [row for row in rows if row["op"] == "RST"]
        if not sets or resets:
            continue
        context = " ".join(
            [str((devices or {}).get(address, {}).get("comment") or "")]
            + [
                json.dumps(by_id.get(row["network"], {}).get("ladder") or {}, ensure_ascii=False)
                for row in sets
            ]
        )
        if _EXTERNAL_OWNER_RE.search(context):
            continue
        findings.append(
            _finding(
                "LATCH_WITHOUT_RESET",
                "warning" if address.startswith("Y") else "info",
                f"{address} 在当前程序中有 SET，但没有找到 RST。",
                "若由 HMI、通信或其他程序复位，请在注释/契约中注明；否则补全复位与优先级。",
                addresses=[address],
                networks=[row["network"] for row in sets],
                rung_ids=[row["rung_id"] for row in sets],
                paths=[row["path"] for row in sets],
                evidence=[f"{row['network']}:SET" for row in sets],
            )
        )

    mutex_results = []
    for contract in normalized_config["mutex"]:
        left, right = contract["devices"]
        missing = []
        checked = []
        for target, other in ((left, right), (right, left)):
            target_rows = writers.get(target, [])
            for row in target_rows:
                network = by_id.get(row["network"], {})
                protected = row["op"] == "OUT" and _series_nc_for_output(
                    network, row["path"], other
                )
                checked.append(
                    {
                        "target": target,
                        "other": other,
                        "network": row["network"],
                        "path": row["path"],
                        "writer": row["op"],
                        "protected": protected,
                    }
                )
                if not protected:
                    missing.append(checked[-1])
        status = "not_applicable" if not checked else "satisfied" if not missing else "unresolved"
        mutex_results.append({**copy.deepcopy(contract), "status": status, "checks": checked})
        if missing:
            findings.append(
                _finding(
                    "MUTEX_NOT_ENFORCED",
                    "warning",
                    f"互斥输出 {left}/{right} 未在所有写入路径上证明对方为串联常闭条件。",
                    "为普通方向线圈串联对方 NC；若为保持输出，明确设计并验证复位/切换状态机。",
                    addresses=[left, right],
                    networks=[item["network"] for item in missing],
                    rung_ids=[by_id.get(item["network"], {}).get("rung_id") for item in missing],
                    paths=[item["path"] for item in missing],
                    evidence=[f"{item['network']}:{item['target']}:{item['writer']}" for item in missing],
                )
            )

    expectation_results = []
    for expectation in normalized_config["same_scan_expectations"]:
        device = expectation["device"]
        reader_id = expectation["reader_network"]
        writer_id = expectation.get("writer_network")
        reader = by_id.get(reader_id)
        candidate_writers = [
            row for row in writers.get(device, []) if not writer_id or row["network"] == writer_id
        ]
        risks = []
        if reader:
            for writer in candidate_writers:
                if int(reader.get("order") or 0) < writer["order"]:
                    risks.append(writer)
        status = "unresolved" if risks else "satisfied" if reader and candidate_writers else "not_found"
        expectation_results.append({**copy.deepcopy(expectation), "status": status})
        if risks:
            findings.append(
                _finding(
                    "SAME_SCAN_READ_BEFORE_WRITE",
                    "warning",
                    f"{reader_id} 在写入 {device} 的网络之前读取它，不能得到本扫描后续写入的新值。",
                    "调整网络顺序，或明确该读取应使用上一扫描值。",
                    addresses=[device],
                    networks=[reader_id] + [row["network"] for row in risks],
                    rung_ids=[reader.get("rung_id")] + [row["rung_id"] for row in risks],
                    paths=[row["path"] for row in risks],
                    evidence=[f"reader order={reader.get('order')}, writer order={row['order']}" for row in risks],
                )
            )

    for coverage in (timing or {}).get("coverage") or []:
        if not isinstance(coverage, Mapping) or coverage.get("status") == "satisfied":
            continue
        semantic = str(coverage.get("semantic") or "")
        if semantic in {"RISING_EDGE", "FALLING_EDGE"}:
            code = "EDGE_MISUSE"
        elif semantic == "FIRST_SCAN":
            code = "INIT_VALUE_OVERWRITE_WARNING"
        else:
            continue
        strict = bool(coverage.get("strict"))
        findings.append(
            _finding(
                code,
                "error" if strict else "warning",
                f"已确认的 {semantic} 扫描语义未在程序中找到匹配实现。",
                "按已确认语义使用边沿、首扫或相应执行源，不要用普通电平触点替代。",
                addresses=list(coverage.get("devices") or []),
                networks=list(coverage.get("network_refs") or []),
                evidence=[str(coverage.get("message") or "semantic coverage unresolved")],
            )
        )

    for record in (timing or {}).get("initialization") or []:
        if not isinstance(record, Mapping) or record.get("status") != "continuous_overwrite_risk":
            continue
        network_id = str(record.get("network") or "")
        network = by_id.get(network_id, {})
        findings.append(
            _finding(
                "INIT_VALUE_OVERWRITE_WARNING",
                "warning",
                f"{network_id} 的初始化写入由运行常通条件驱动，会在每一扫描覆盖目标值。",
                "若参数运行中可修改，改用型号对应首扫脉冲；若确需持续强制，请明确注释。",
                addresses=list(record.get("writes") or []),
                networks=[network_id],
                rung_ids=[network.get("rung_id")],
                evidence=["trigger=" + ",".join(record.get("trigger_devices") or [])],
            )
        )

    findings.extend(_timer_findings(networks))

    configured_terminal = normalized_config["terminal_states"]
    state_results = []
    for machine in (logic or {}).get("state_machines") or []:
        if not isinstance(machine, Mapping):
            continue
        register = str(machine.get("state_register") or "")
        names = {
            int(item.get("value")): str(item.get("name") or "")
            for item in machine.get("states") or []
            if isinstance(item, Mapping) and item.get("value") is not None
        }
        raw_dead_end = set(machine.get("dead_end_state_candidates") or [])
        terminal = set(configured_terminal.get(register, [])) | {
            value
            for value, name in names.items()
            if value in raw_dead_end and _TERMINAL_RE.search(name)
        }
        unreachable = list(machine.get("unreachable_state_candidates") or [])
        dead_end = [
            value
            for value in raw_dead_end
            if value not in terminal
        ]
        state_results.append(
            {
                "state_machine": str(machine.get("id") or ""),
                "state_register": register,
                "unreachable_states": unreachable,
                "dead_end_states": dead_end,
                "terminal_states": sorted(terminal),
            }
        )
        if unreachable:
            findings.append(
                _finding(
                    "UNREACHABLE_STATE",
                    "warning",
                    f"状态机 {register} 的状态 {unreachable} 没有初始化或转移入口。",
                    "补充到达转移，或删除未使用状态。",
                    addresses=[register],
                    evidence=[f"unreachable={unreachable}"],
                )
            )
        if dead_end:
            findings.append(
                _finding(
                    "DEAD_END_STATE",
                    "info",
                    f"状态机 {register} 的状态 {dead_end} 没有退出转移，也未标记为终态。",
                    "确认它们是终态并在规格中标记，或补充退出/复位转移。",
                    addresses=[register],
                    evidence=[f"dead_end={dead_end}"],
                )
            )

    performance = (timing or {}).get("performance") or {}
    budget = performance.get("scan_budget") if isinstance(performance, Mapping) else {}
    if isinstance(budget, Mapping) and budget.get("status") == "exceeded":
        findings.append(
            _finding(
                "SCAN_BUDGET_WARNING",
                "warning",
                f"估算最坏扫描时间 {budget.get('estimated_worst_ms')} ms 超过预算 {budget.get('budget_ms')} ms。",
                "优先检查高耗时通信、块处理、循环和定位指令，并用 D8010-D8012 实测确认。",
                evidence=[
                    f"profile={performance.get('profile')}",
                    f"coverage={(performance.get('estimate') or {}).get('instruction_coverage')}",
                ],
                confidence="medium",
            )
        )

    for assessment in (timing or {}).get("pulse_capture_assessments") or []:
        if not isinstance(assessment, Mapping) or assessment.get("status") != "pulse_loss_risk":
            continue
        devices = list(assessment.get("devices") or [])
        findings.append(
            _finding(
                "PULSE_LOSS_WARNING",
                "warning",
                f"输入 {', '.join(devices)} 的明确脉宽 {assessment.get('pulse_width_ms')} ms "
                f"不大于扫描比较上界 {assessment.get('comparison_bound_ms')} ms，脉冲可能完整落在两次采样之间。",
                (
                    "校验 GX Works2 中断任务配置并实测扫描时间，或采用对应型号支持的高速输入/硬件捕获；"
                    "不要仅依赖普通扫描触点或软件边沿指令。"
                ),
                addresses=devices,
                networks=list(assessment.get("network_refs") or []),
                evidence=[
                    f"pulse_width_ms={assessment.get('pulse_width_ms')}",
                    f"estimated_worst_scan_ms={assessment.get('estimated_worst_scan_ms')}",
                    f"scan_budget_ms={assessment.get('scan_budget_ms')}",
                    "comparison_basis=" + ",".join(assessment.get("comparison_basis") or []),
                    f"instruction_coverage={assessment.get('instruction_coverage')}",
                    f"interrupt_decision={assessment.get('decision')}",
                ],
                confidence="medium",
            )
        )

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    findings.sort(
        key=lambda item: (
            severity_rank.get(item["severity"], 9),
            item["code"],
            tuple(item["addresses"]),
            tuple(item["network_refs"]),
        )
    )
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in findings:
        counts[item["severity"]] += 1
    return {
        "schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
        "plc_model": str(plc_model or "").upper(),
        "config": normalized_config,
        "rules_checked": list(RULE_IDS),
        "dependency_graph": graph,
        "mutex": mutex_results,
        "same_scan_expectations": expectation_results,
        "state_analysis": state_results,
        "findings": findings,
        "counts": counts,
    }


def trace_upstream(
    analysis_or_graph: Mapping[str, Any], target: str, *, max_depth: int = 8
) -> Dict[str, Any]:
    """Return a bounded reverse dependency subgraph for Debug/Patch agents."""

    graph = analysis_or_graph.get("dependency_graph") or analysis_or_graph
    reverse = graph.get("reverse") if isinstance(graph, Mapping) else {}
    edges = graph.get("device_edges") if isinstance(graph, Mapping) else []
    target = _device(target)
    if not target:
        raise ValueError("target must be a PLC device address")
    depth_limit = max(0, min(int(max_depth), 32))
    visited = {target}
    frontier = {target}
    layers = [{"depth": 0, "devices": [target]}]
    for depth in range(1, depth_limit + 1):
        next_frontier = {
            source
            for current in frontier
            for source in (reverse.get(current, []) if isinstance(reverse, Mapping) else [])
            if source not in visited
        }
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
        layers.append({"depth": depth, "devices": sorted(frontier)})
    selected_edges = [
        copy.deepcopy(dict(edge))
        for edge in edges or []
        if isinstance(edge, Mapping)
        and edge.get("from") in visited
        and edge.get("to") in visited
    ]
    roots = sorted(
        address
        for address in visited
        if not set(reverse.get(address, []) if isinstance(reverse, Mapping) else []).intersection(visited)
    )
    return {
        "target": target,
        "max_depth": depth_limit,
        "devices": sorted(visited),
        "roots": roots,
        "layers": layers,
        "edges": selected_edges,
    }


__all__ = [
    "RULE_IDS",
    "STATIC_ANALYSIS_SCHEMA_VERSION",
    "analyze_static_program",
    "build_device_dependency_graph",
    "normalize_analysis_config",
    "trace_upstream",
]
