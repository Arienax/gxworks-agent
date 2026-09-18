"""Evidence-bound planning for generation-contract repair.

The generation contract is a validator, not a repair recipe.  This module turns
contract mismatches into structured violations and only produces an LLM repair
request when the existing program and confirmed specification provide enough
local evidence to bound the change.  The LLM is never asked to invent a
semantic use for a merely-required opcode.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from plc.specification.approach import inspect_ladder_features, normalize_approach


_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SM|SD|TS|TC|CS|CC|[XYMSTCDRVZ])\d+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_INDEXED_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<base>(?:SM|SD|[XYMSTCDR])\d+)(?P<index>[VZ]\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_STATE_COMPARE_RE = re.compile(
    r"(?:^|\s)(?:=|==)\s*(D\d+)\s*K[+-]?\d+(?:\s|$)",
    re.IGNORECASE,
)
_STATE_WORDS_RE = re.compile(
    r"状态|步骤|阶段|工步|待机|就绪|运行态|完成态|state|step",
    re.IGNORECASE,
)


def _selected_approach(confirmed_spec):
    if not isinstance(confirmed_spec, Mapping):
        return {}
    selected = confirmed_spec.get("selected_approach")
    if not isinstance(selected, Mapping) or not selected:
        return {}
    return normalize_approach(selected)


def _contract(confirmed_spec):
    approach = _selected_approach(confirmed_spec)
    return approach, approach.get("generation_contract") or {}


def structured_contract_violations(ladder, confirmed_spec):
    """Return machine-readable violations instead of prose-only issue strings."""

    approach, contract = _contract(confirmed_spec)
    if not approach or not contract.get("enforce", True):
        return []

    features = inspect_ladder_features(ladder or {})
    opcodes = set(features.get("opcodes") or [])
    devices = set(features.get("devices") or [])
    structures = set(features.get("structures") or [])
    violations = []

    def add(kind, field, value, **extra):
        item = {
            "violation_id": f"contract-{len(violations) + 1:03d}",
            "kind": kind,
            "field": field,
            "value": value,
        }
        item.update(extra)
        violations.append(item)

    for opcode in sorted(set(contract.get("required_opcodes") or []) - opcodes):
        add("missing_opcode", "required_opcodes", opcode)
    for opcode in sorted(set(contract.get("forbidden_opcodes") or []) & opcodes):
        add("forbidden_opcode", "forbidden_opcodes", opcode)
    for device in sorted(set(contract.get("required_devices") or []) - devices):
        add("missing_device", "required_devices", device)
    for device in sorted(set(contract.get("forbidden_devices") or []) & devices):
        add("forbidden_device", "forbidden_devices", device)
    for structure in sorted(
        set(contract.get("required_structures") or []) - structures
    ):
        add("missing_structure", "required_structures", structure)
    for structure in sorted(
        set(contract.get("forbidden_structures") or []) & structures
    ):
        add("forbidden_structure", "forbidden_structures", structure)

    for group in contract.get("any_of_opcode_groups") or []:
        normalized = [str(item).strip().upper() for item in group if str(item).strip()]
        if normalized and not (set(normalized) & opcodes):
            add("missing_any_opcode", "any_of_opcode_groups", normalized)
    for group in contract.get("any_of_structure_groups") or []:
        normalized = [str(item).strip() for item in group if str(item).strip()]
        if normalized and not (set(normalized) & structures):
            add("missing_any_structure", "any_of_structure_groups", normalized)

    return violations


def _devices_in_value(value):
    text = str(value or "")
    devices = {item.upper() for item in _DEVICE_TOKEN_RE.findall(text)}
    for match in _INDEXED_DEVICE_TOKEN_RE.finditer(text):
        base = match.group("base").upper()
        index = match.group("index").upper()
        devices.update({base + index, base, index})
    return devices


def _walk_elements(elements):
    for element in elements or []:
        if not isinstance(element, Mapping):
            continue
        yield element
        if str(element.get("type") or "").casefold() == "parallel_block":
            for branch in element.get("branches") or []:
                yield from _walk_elements(branch)


def _rung_devices(rung):
    devices = set()
    if not isinstance(rung, Mapping):
        return devices
    header = rung.get("header_element")
    elements = [header] if isinstance(header, Mapping) else []
    for branch in rung.get("branches") or []:
        if not isinstance(branch, Mapping):
            continue
        elements.extend(_walk_elements(branch.get("inputs") or []))
        elements.extend(branch.get("outputs") or [])
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        devices.update(_devices_in_value(element.get("address")))
        devices.update(_devices_in_value(element.get("expression")))
        for operand in element.get("operands") or []:
            devices.update(_devices_in_value(operand))
    return devices


def patch_device_addresses(partial):
    """Return every device referenced by a proposed partial patch."""

    addresses = {
        str(item).strip().upper()
        for item in ((partial or {}).get("device_comments") or {})
        if str(item).strip()
    }
    for rung in (partial or {}).get("rungs") or []:
        addresses.update(_rung_devices(rung))
    return addresses


def _rung_text(rung):
    try:
        return json.dumps(rung, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(rung)


def _scope_for_contract(ladder, confirmed_spec, violations, features):
    """Infer a conservative existing-rung scope from program evidence.

    Returning an empty scope means the semantic location is not known well
    enough for automatic repair.  A required opcode alone is intentionally not
    enough evidence.
    """

    approach, contract = _contract(confirmed_spec)
    guide = " ".join(
        str(approach.get(key) or "")
        for key in ("name", "description", "generation_guide")
    )
    required_devices = {
        str(item).strip().upper()
        for item in contract.get("required_devices") or []
        if str(item).strip()
    }
    required_structures = set(contract.get("required_structures") or [])
    missing_values = {
        str(v.get("value") or "").upper()
        for v in violations
        if v.get("kind") in {"missing_opcode", "missing_device", "missing_structure"}
        and not isinstance(v.get("value"), list)
    }

    state_registers = set(features.get("state_registers") or [])
    state_registers.update(
        item for item in required_devices if re.fullmatch(r"D\d+", item)
    )
    state_semantics = bool(
        required_structures
        & {
            "register_state_machine",
            "state_initialization",
            "state_comparison",
            "state_transition",
        }
    ) or bool(re.search(r"状态机|状态转移|步进状态|MOV\s+K\d+\s+D\d+", guide, re.I))

    pulse_semantics = bool(
        required_structures & {"pulse_positioning"}
        or {"PLSY", "PLSV", "DRVI", "DRVA", "ZRN", "DSZR", "DVIT"}
        & missing_values
    )
    analog_semantics = "analog_control" in required_structures
    serial_semantics = "serial_communication" in required_structures

    allowed = set()
    reasons = {}
    all_rungs = [r for r in (ladder or {}).get("rungs") or [] if isinstance(r, Mapping)]
    comments = {
        str(addr).upper(): str(label)
        for addr, label in ((ladder or {}).get("device_comments") or {}).items()
    }

    for rung in all_rungs:
        rid = rung.get("rung_id")
        if rid is None:
            continue
        rid = int(rid)
        devices = _rung_devices(rung)
        text = _rung_text(rung)
        why = []

        if required_devices & devices:
            why.append("包含 generation_contract 指定软元件")
        if state_semantics:
            if state_registers & devices:
                why.append("包含状态寄存器")
            if _STATE_COMPARE_RE.search(text):
                why.append("包含状态比较")
            if {"M8002", "SM402", "SM8002"} & devices:
                why.append("包含首扫初始化触点")
            if any(_STATE_WORDS_RE.search(comments.get(d, "")) for d in devices):
                why.append("包含已标注的状态软元件")
        if pulse_semantics and (
            re.search(r"PLSY|PLSV|DRVI|DRVA|ZRN|DSZR|DVIT|M8029", text, re.I)
        ):
            why.append("包含定位/完成逻辑")
        if analog_semantics and (
            re.search(r"\b(?:TO|DTO|FROM|DFROM|RD3A|WR3A)\b|D82[6-9]\d|M82[6-9]\d", text, re.I)
        ):
            why.append("包含模拟量访问逻辑")
        if serial_semantics and re.search(r"\b(?:RS2?|ADPRW)\b", text, re.I):
            why.append("包含串行通信逻辑")

        if why:
            allowed.add(rid)
            reasons[rid] = list(dict.fromkeys(why))

    fallback_scope = False
    if not allowed:
        # A small-program fallback is permitted only when the contract contains
        # semantic anchors beyond a naked opcode token.  It is still restricted
        # to existing rungs and existing device addresses.
        has_semantic_anchor = bool(required_devices or required_structures)
        if has_semantic_anchor and len(all_rungs) <= 6:
            allowed = {int(r["rung_id"]) for r in all_rungs if r.get("rung_id") is not None}
            reasons = {rid: ["小型程序且已有结构/软元件语义锚点"] for rid in allowed}
            fallback_scope = True

    return sorted(allowed), reasons, fallback_scope


def _violation_text(item):
    value = item.get("value")
    if isinstance(value, list):
        value = " / ".join(value)
    labels = {
        "missing_opcode": "缺少必用指令",
        "forbidden_opcode": "使用了禁用指令",
        "missing_device": "缺少指定软元件",
        "forbidden_device": "使用了禁用软元件",
        "missing_structure": "缺少必用结构",
        "forbidden_structure": "使用了禁用结构",
        "missing_any_opcode": "未满足指令任选组",
        "missing_any_structure": "未满足结构任选组",
    }
    return f"{labels.get(item.get('kind'), item.get('kind'))}: {value}"


def build_contract_repair_plan(
    ladder,
    confirmed_spec,
    *,
    plc_model="FX3U",
    mismatch=None,
):
    """Build a bounded repair plan or refuse to let the model guess."""

    approach, contract = _contract(confirmed_spec)
    violations = structured_contract_violations(ladder, confirmed_spec)
    if not violations:
        return {
            "plan_id": "contract-repair-none",
            "repairability": "not_needed",
            "approach_name": approach.get("name") or "已选方案",
            "violations": [],
            "reason": "当前程序已经满足 generation_contract",
        }

    features = inspect_ladder_features(ladder or {})
    allowed_rung_ids, scope_reasons, fallback_scope = _scope_for_contract(
        ladder, confirmed_spec, violations, features
    )
    # Device scope follows the selected evidence rungs rather than the
    # entire program.  This prevents the model from borrowing an unrelated
    # address merely because that address exists elsewhere in the project.
    existing_addresses = set()
    allowed_rung_id_set = set(allowed_rung_ids)
    for rung in (ladder or {}).get("rungs") or []:
        if not isinstance(rung, Mapping):
            continue
        if rung.get("rung_id") is None or int(rung.get("rung_id")) not in allowed_rung_id_set:
            continue
        existing_addresses.update(_rung_devices(rung))
    existing_addresses.update(
        str(item).strip().upper()
        for item in contract.get("required_devices") or []
        if str(item).strip()
    )
    allowed_addresses = sorted(existing_addresses)

    if not allowed_rung_ids:
        return {
            "plan_id": "contract-repair-needs-context",
            "repairability": "needs_user_context",
            "approach_name": approach.get("name") or "已选方案",
            "violations": violations,
            "allowed_rung_ids": [],
            "allowed_addresses": allowed_addresses,
            "scope_reasons": {},
            "fallback_scope": False,
            "reason": (
                "当前 generation_contract 只说明了缺失特征，"
                "但无法从已确认规格和现有程序确定应该修改哪些梯级。"
            ),
        }

    serializable = {
        "approach": {
            "name": approach.get("name") or "已选方案",
            "description": approach.get("description") or "",
            "generation_guide": approach.get("generation_guide") or "",
            "generation_contract": contract,
        },
        "violations": violations,
        "allowed_rung_ids": allowed_rung_ids,
        "allowed_addresses": allowed_addresses,
    }
    digest = hashlib.sha256(
        json.dumps(serializable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    plan_id = f"contract-repair-{digest}"

    violation_lines = "\n".join(f"- {_violation_text(item)}" for item in violations)
    scope_lines = "\n".join(
        f"- rung {rid}: {'、'.join(scope_reasons.get(rid) or ['相关梯级'])}"
        for rid in allowed_rung_ids
    )
    prompt = f"""# Contract repair task
你不是在重新生成 PLC 程序，而是在修复一个已存在版本的 generation_contract 违例。
只返回合法的 partial ladder JSON，不要解释，不要 Markdown。

目标 PLC：{plc_model}
已选方案：{approach.get('name') or '已选方案'}
方案说明：{approach.get('description') or ''}
方案生成要点：{approach.get('generation_guide') or ''}

待修复违例：
{violation_lines}

允许修改的既有梯级：
{scope_lines}

允许引用的软元件：
{', '.join(allowed_addresses) or '无'}

硬边界：
1. mode 必须为 "partial"；只能返回上述 rung_id，禁止删除既有梯级。
2. 禁止引用允许清单以外的任何新软元件；不得改变 I/O 分配、定时器/计数器参数或无关输出逻辑。
3. 禁止为了通过 required_opcodes 检查而添加无实际作用的 M8000+MOV、无用 SET/RST、死代码或永远不会影响控制结果的指令。
4. 必用指令必须真正承担 generation_guide 中指定的控制语义；禁用指令必须以同一已确认语义的合法实现替换，而不是机械删除。
5. 保持允许范围以外的所有梯级完全不变。不要重新分析需求，不要切换实现方案。
6. 修复结果必须同时通过普通 PLC 硬校验和完整 generation_contract 校验。
""".strip()

    return {
        "plan_id": plan_id,
        "repairability": "scoped_patch",
        "approach_name": approach.get("name") or "已选方案",
        "violations": violations,
        "allowed_rung_ids": allowed_rung_ids,
        "allowed_addresses": allowed_addresses,
        "scope_reasons": scope_reasons,
        "fallback_scope": fallback_scope,
        "baseline_features": features,
        "prompt": prompt,
    }


def format_contract_repair_plan(plan):
    """Render a compact user-facing review of the deterministic plan."""

    lines = [f"修复计划：{plan.get('plan_id', '')}"]
    violations = plan.get("violations") or []
    if violations:
        lines.append("待修复：")
        lines.extend(f"  • {_violation_text(item)}" for item in violations)
    rungs = plan.get("allowed_rung_ids") or []
    if rungs:
        lines.append("允许修改梯级：" + ", ".join(map(str, rungs)))
    addresses = plan.get("allowed_addresses") or []
    if addresses:
        shown = addresses[:30]
        suffix = f" …另有 {len(addresses) - len(shown)} 个" if len(addresses) > len(shown) else ""
        lines.append("允许引用软元件：" + ", ".join(shown) + suffix)
    if plan.get("fallback_scope"):
        lines.append("范围来源：小型程序保守回退（仍只允许既有梯级和既有软元件）")
    else:
        lines.append("范围来源：现有程序/确认规格中的语义证据")
    return "\n".join(lines)


__all__ = [
    "structured_contract_violations",
    "build_contract_repair_plan",
    "format_contract_repair_plan",
    "patch_device_addresses",
]
