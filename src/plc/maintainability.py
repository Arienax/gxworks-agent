"""Advisory capability selection and local review; never an acceptance gate."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
import re


SELECTION_POLICY_VERSION = "functional-capabilities-v2-convergent"
SELECTION_POLICY = """\n# Capability selection and maintainability
候选是按需取用的技术线索，不是逐项比较清单、白名单或已确认工艺要求。先遵守用户指定调用与既定方案；没有既定方案时，选定一个能满足全部需求且有技术依据的可行实现即可结束选型。
选定后只实现并核对当前方案，不为寻找更短、更高级或其他可能更好的实现重新比较。检查行为、数据类型、接口、扫描顺序、边沿、并发及停止/复位；局部错误在当前方案内修复。
仅当具体需求矛盾、型号或接口事实、验证反例证明当前结构不可行，或用户修订要求时，才重选受影响部分。没有新证据，不重新采用已因明确缺陷淘汰的方案。不得以简洁或预期测试范围为由省略需求、降低精度或静默缩小并发能力。
只核对当前实现需要的调用与依赖；未选候选不展开。随附资料保留来源与覆盖缺口，缺证据不等于禁用，也不编造查证或测试。工程函数/FB 必须匹配当前目录和接口；顺序需求不自动选择 SFC/STL。
真实关键缺参按当前流程的缺参协议处理，不通过更换算法或猜测现场事实绕过。选型在本次生成内完成，仅输出当前程序协议，无需额外方案报告或确认步骤。
"""


def _visible_st(source):
    """Mask comments/strings with stable offsets; no ST semantic parsing."""
    result, cursor, depth, quote = list(source), 0, 0, None
    while cursor < len(source):
        pair, char = source[cursor:cursor+2], source[cursor]
        length = 1
        if depth:
            if pair == '(*':
                depth += 1
                length = 2
            elif pair == '*)':
                depth -= 1
                length = 2
        elif quote:
            if char == '$' or pair == quote * 2:
                length = min(2, len(source)-cursor)
            elif char == quote:
                quote = None
        elif pair == '(*':
            depth, length = 1, 2
        elif pair == '//':
            end = source.find('\n', cursor)
            length = (end if end >= 0 else len(source))-cursor
        elif char in {'\"', "'"}:
            quote = char
        else:
            cursor += 1
            continue
        for i in range(cursor, cursor+length):
            if source[i] not in '\r\n':
                result[i] = ' '
        cursor += length
    return ''.join(result) if not depth and quote is None else None


def _review_conditions(ladder, model, result):
    from plc.condition_analysis import ConditionAnalysis
    from plc.condition_normalizer import normalize_shared_conditions
    analysis = ConditionAnalysis(model)
    condition_types, effects = Counter(), Counter()
    barriers = defaultdict(list)
    unknown_reads = unknown_writes = False

    def condition(element, location):
        nonlocal unknown_reads
        fact = analysis.condition(element)
        condition_types[fact.kind] += 1
        if fact.identity is None:
            barriers[fact.reason].append(location)
            unknown_reads |= fact.kind not in {"rising_edge", "falling_edge"}
        if isinstance(element, dict) and element.get("type") == "parallel_block":
            for arm_index, arm in enumerate(element.get("branches", [])):
                for index, child in enumerate(arm):
                    condition(child, {**location, "path": f'{location["path"]}.branches[{arm_index}][{index}]'})

    for rung in (ladder or {}).get("rungs", []):
        base = {"rung_id": rung.get("rung_id")}
        if rung.get("header_element"):
            condition(rung["header_element"], {**base, "path": "header_element"})
        for index, item in enumerate(rung.get("shared_inputs", [])):
            condition(item, {**base, "path": f"shared_inputs[{index}]"})
        for branch_index, branch in enumerate(rung.get("branches", [])):
            location = {**base, "branch_id": branch.get("branch_id")}
            for index, item in enumerate(branch.get("inputs", [])):
                condition(item, {**location, "path": f"branches[{branch_index}].inputs[{index}]"})
            for index, output in enumerate(branch.get("outputs", [])):
                effect = analysis.output(output)
                effects[effect.precision] += 1
                if effect.writes is None:
                    unknown_writes = True
                    barriers[effect.reason].append({**location, "path": f"branches[{branch_index}].outputs[{index}]"})
                elif effect.precision == "family":
                    barriers["conservative_write_extent"].append({**location, "path": f"branches[{branch_index}].outputs[{index}]"})
    _, report = normalize_shared_conditions(ladder, plc_model=model)
    stats = report.get("statistics", {})
    saving = stats.get("conditions_before", 0) - stats.get("conditions_after", 0)
    if saving:
        ids = sorted({n for row in report["changes"] for n in row["rung_ids"]})
        result["findings"].append({"code": "repeated_shared_conditions", "severity": "advisory",
            "message": f"发现可按现有条件归并规则减少的 {saving} 处条件重复，涉及连续网络或支路；本次审阅未修改程序，工艺行为仍需独立验收。",
            "locations": [{"rung_id": n} for n in ids], "replacement_verified": False,
            "conditions_reducible": saving})
    result["condition_review"] = {"target_model": model, "statistics": stats,
        "condition_types": dict(condition_types), "output_effect_precision": dict(effects),
        "barriers": [{"reason": reason, "locations": locations} for reason, locations in sorted(barriers.items())]}
    result["coverage"].extend([
        {"check": "shared_conditions_and_contiguous_groups", "status": "checked"},
        {"check": "condition_read_stability", "status": "unverified" if unknown_reads else "checked"},
        {"check": "output_effect_footprints", "status": "unverified" if unknown_writes else "checked"}])


def review_maintainability(program, *, target_mode="ladder", plc_model="FX3U"):
    """Report observable structure only. Repetition does not prove equivalence."""
    result = {"policy_version": SELECTION_POLICY_VERSION, "status": "local_only",
              "used_capabilities": [], "findings": [], "coverage": [],
              "behavior_verified": False, "model_calls": 0}
    calls = Counter()
    repeated = defaultdict(list)
    call_locations = defaultdict(list)
    if target_mode == "ladder":
        from plc.ir import is_plc_ir, ir_to_ladder
        ladder = ir_to_ladder(program) if is_plc_ir(program) else program
        for rung in (ladder or {}).get("rungs", []):
            guard = json.dumps([rung.get("shared_inputs"), rung.get("header_element")], sort_keys=True)
            for branch in rung.get("branches", []):
                branch_guard = guard + json.dumps(branch.get("inputs"), sort_keys=True)
                for output in branch.get("outputs", []):
                    name = str(output.get("opcode") or output.get("type") or "unknown")
                    calls[name] += 1
                    if name in {"MOV", "RST", "SET"}:
                        repeated[(branch_guard, name)].append({"rung_id": rung.get("rung_id"),
                                                              "branch_id": branch.get("branch_id"),
                                                              "operands": output.get("operands"),
                                                              "address": output.get("address")})
        result["coverage"] = [{"check": "capability_calls_and_repeated_writes", "status": "checked"},
                              {"check": "replacement_equivalence_and_process_behavior", "status": "unverified"}]
        _review_conditions(ladder, (program.get("plc", {}).get("cpu") if is_plc_ir(program) else None) or plc_model, result)
    elif target_mode == "fbd":
        connections = []
        connected = defaultdict(list)
        node_ids = {node.get("id") for node in (program or {}).get("nodes", [])}
        for index, wire in enumerate((program or {}).get("wires", []) or []):
            row = {"connection_index": index, **{key: deepcopy(wire[key]) for key in
                ("source_offset", "from", "to", "start", "end", "block") if key in wire}}
            named = all(isinstance(wire.get(key), str) and "." in wire[key]
                        and wire[key].split(".", 1)[0] in node_ids and wire[key].split(".", 1)[1]
                        for key in ("from", "to"))
            row["endpoint_binding"] = "explicit" if named else "unverified"
            connections.append(row)
            if named:
                for node_id in {wire[key].split(".", 1)[0] for key in ("from", "to")}:
                    connected[node_id].append(row)
        result["connection_inventory"] = connections
        for node in (program or {}).get("nodes", []):
            template = str(node.get("template") or "")
            if template.startswith(("function:", "function_block:")):
                calls[template] += 1
                location = {"node_id": node.get("id"),
                    "source_offset": node.get("source_offset"),
                    "position": [node.get("x"), node.get("y")] if 'x' in node or 'y' in node else None}
                if connected.get(node.get("id")):
                    location["connections"] = connected[node["id"]]
                call_locations[template].append(location)
        result["coverage"] = [{"check": "object_call_inventory", "status": "checked"},
                              {"check": "connection_inventory", "status": "checked"},
                              {"check": "explicit_connection_endpoints", "status": "unverified" if any(
                                  c["endpoint_binding"] != "explicit" for c in connections) else "checked"},
                              {"check": "graph_replacement_equivalence_and_process_behavior", "status": "unverified"}]
    else:
        code = str((program or {}).get("st_code") or "") if isinstance(program, dict) else str(program or "")
        # Lexical inventory only: strings/comments cannot manufacture a call.
        scrubbed = _visible_st(code)
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", scrubbed or ''):
            name = match.group(1)
            if name.upper() not in {"IF", "ELSIF", "WHILE", "CASE"}:
                calls[name] += 1
                call_locations[name].append({"line": code.count('\n', 0, match.start())+1,
                    "column": match.start()-code.rfind('\n', 0, match.start())})
        literal_writes = defaultdict(list)
        for match in re.finditer(
                r"(?mi)^[ \t]*([A-Za-z_]\w*(?:[ \t]*\[[^\]\r\n]+\])?)[ \t]*:="
                r"[ \t]*(TRUE|FALSE|K[+-]?\d+|H[0-9A-F]+|[+-]?\d+)[ \t]*;", scrubbed or ''):
            start = match.start(1)
            literal_writes[match.group(2).upper()].append({
                "line": code.count('\n', 0, start)+1,
                "column": start-code.rfind('\n', 0, start),
                "destination": match.group(1), "literal": match.group(2)})
        for literal, locations in literal_writes.items():
            if len(locations) >= 3:
                result["findings"].append({"code": "repeated_literal_assignments", "severity": "advisory",
                    "message": f"源码有 {len(locations)} 处 {literal} 字面赋值，可复核共同职责及封装方式；控制条件、类型和替换等价性尚未验证。",
                    "locations": locations, "control_conditions_verified": False, "replacement_verified": False})
        result["coverage"] = [{"check": "lexically_identifiable_calls", "status": "checked" if scrubbed is not None else "unverified"},
                              {"check": "line_initial_literal_assignments", "status": "checked" if scrubbed is not None else "unverified"},
                              {"check": "ST_types_control_flow_and_equivalence", "status": "unverified"}]
    result["used_capabilities"] = [{"name": name, "count": count} for name, count in sorted(calls.items())]
    for name, locations in call_locations.items():
        if len(locations) >= 3:
            result["findings"].append({"code": "repeated_calls", "severity": "advisory",
                "message": f"存在 {len(locations)} 次 {name} 调用，可复核共有职责和接口是否适合封装；调用重复不证明逻辑冗余或可安全替换。",
                "locations": locations, "replacement_verified": False})
    for (_guard, name), locations in repeated.items():
        if len(locations) >= 3:
            result["findings"].append({"code": "repeated_writes", "severity": "advisory",
                "message": f"同一可见条件下有 {len(locations)} 次 {name}，可复核是否存在合适的批量或封装能力；尚未证明可替换。",
                "locations": locations, "replacement_verified": False})
    return result
