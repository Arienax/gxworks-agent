# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Route PLC facts; analysis mode is an explicit caller choice, never inferred."""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping
from typing import Callable
from plc.device_identity import REFERENCE_DEVICE_TOKEN_RE as DEVICE_TOKEN_RE, reference_device_tokens as device_tokens

_TOKEN = re.compile(r"(?<![A-Za-z0-9_.$])[A-Za-z$][A-Za-z0-9_$]*(?:\.[A-Za-z0-9_$]+)*(?![A-Za-z0-9_$])")

_DEVICE = DEVICE_TOKEN_RE
_NEGATED = re.compile(
    r"(?:不要|不用|不能|不得|不添加|不增加|不使用|不采用|不进行|不修改|不改变|不刷新|不读取|不写入|不复位|不清零|没有(?!(?:[^，,。；;\n]{0,24})(?:型号|参数|地址|信息))|无需|不需要|不涉及|不包含|不是|不比较|不讨论|禁止|without\b|do not\b|don't\b|no\b)"
    r"(?:(?!改用|改为|改成|而是|但是|\bbut\b|\binstead\b)[^，,。；;\n])*", re.I,
)


def positive_requirement_text(value):
    """Text that requests capabilities; prohibitions remain in the full spec."""
    return _NEGATED.sub("", str(value or ""))


def generation_fact_query(query, specification, plc_model):
    """Separate settled program wiring from explicit technical lookup needs.

    Only the retrieval view changes. The original request and bindings still
    reach generation intact. Special/unknown devices and technical questions
    retain their addresses, including a later question about a bound device.
    """
    from plc.device_identity import canonical_reference_device
    from plc.device_policy import device_address, DevicePolicyError

    # Disambiguate while the subject (for example D0) is still present.
    query = instruction_reference_text(str(query or ""))
    spec = specification if isinstance(specification, Mapping) else {}
    settled = set()
    for row in [*(spec.get("io_bindings") or ()), *(spec.get("io_table") or ())]:
        if not isinstance(row, Mapping) or row.get("kind") not in {"X", "Y", "M", "D"}:
            continue
        address = canonical_reference_device(str(row.get("address") or "").upper())
        if row["kind"] in {"X", "Y"}:
            settled.add(address)
            continue
        try:
            # Reuse Core's distinction between ordinary and CPU-owned regions;
            # this read-only classification does not perform or authorize I/O.
            device_address(address, plc_model, access="stimulus" if row["kind"] == "X" else "reset")
        except (DevicePolicyError, ValueError):
            continue
        settled.add(address)
    if not settled:
        return str(query or "")
    lookup = re.compile(
        r"[?？]|查阅|查询|查证|手册|含义|定义|范围|限制|支持|能否|是否|为什么|如何|怎么|"
        r"断电|掉电|保持型|特殊|高速|脉冲|时基|时间基准|分辨率|"
        r"\b(?:manual|datasheet|definition|meaning|range|limit\w*|support\w*|"
        r"can|does|why|how|retentive|latched|special|pulse|resolution)\b", re.I,
    )
    def project(match):
        part = match[0]
        return part if lookup.search(part) else _DEVICE.sub(
            lambda m: " " if canonical_reference_device(m[0].upper()) in settled else m[0], part,
        )
    return re.sub(r"[^。；;?？\n]+[。；;?？]?", project, str(query or ""))


def instruction_reference_text(text):
    """Mask language/state labels before registry and manual-alias lookup."""
    def home_reference(match):
        before, after = text[:match.start()], text[match.end():]
        clause = re.split(r"[，,。；;\n]", before)[-1]
        tail = re.split(r"[，,。；;\n]", after)[0]
        named = (text.strip() == match[0]
                 or re.match(r"\s*(?:指令|instruction\b)", after, re.I)
                 or re.search(r"(?:指令|查询|查阅|解释)\s*$", clause)
                 or (before[-1:] in ('`', '"', "'") and after[:1] == before[-1:]))
        motion = re.search(r"轴|伺服|步进|电机|机械|原点|限位|\b(?:axis|servo|motor|DOG|homing)\b",
                           clause + tail, re.I)
        data_subject = re.search(r"(?:[DMRTC]\d+|寄存器|计数值|当前值|结果|信号|状态|编码)\s*(?:会|已|需|需要|自动|重新|必须|先)?\s*$",
                                 clause, re.I)
        return match[0] if named or motion and not data_subject else ' ' * len(match[0])
    # A numeric/state return to zero is not a mechanical homing request.
    # Keep explicit instruction lookups and local motion subjects, including
    # mixed requests containing both a register reset and an axis operation.
    text = re.sub(r"回零", home_reference, text)
    def logical_label(match):
        before, after = text[:match.start()], text[match.end():]
        clause = re.split(r"[，,。；;\n]", before)[-1]
        tail = re.split(r"[，,。；;\n]", after)[0]
        concept = re.search(r"(?:窗口|条件|逻辑|布尔|组合)\s*$", clause)
        instruction = (re.match(r"\s*(?:指令|instruction\b|[XYMTCS]\d+)", after, re.I)
                       or re.search(r"查询|查阅|解释|手册|怎么|如何|[?？]", clause + tail))
        return ' ' * len(match[0]) if concept and not instruction else match[0]
    text = re.sub(r"\b(?:OR|AND|NOT)\b", logical_label, text)
    def mask(match):
        before, after = text[:match.start()], text[match.end():]
        quoted = bool(before and before[-1] in '`"\'' and after[:1] == before[-1])
        explicit = (text.strip().upper() == match[0].upper() or quoted
                    or re.match(r"\s*(?:指令|instruction\b|\()", after, re.I)
                    or re.search(r"(?:指令|instruction)\s*$", before, re.I))
        return match[0] if explicit else ' ' * len(match[0])
    return re.sub(r"(?<![A-Za-z0-9_])(?:ST|RUN|STOP)(?![A-Za-z0-9_])", mask, text, flags=re.I)


def device_reference_text(text):
    """Mask CPU mentions only in model context, preserving device references."""
    from plc.target_support import target_cpu_names
    names = sorted(target_cpu_names(), key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_])", re.I)
    def mask(match):
        before, after = text[:match.start()], text[match.end():]
        named = ((not before.strip() and re.match(r"\s*(?:[，,。.:：]|的|中|上|使用|高速计数|内置|[A-Za-z]+(?:[/ -][A-Za-z]+){0,3}\s+(?:definition|purpose|meaning)|[A-Z]{1,2}\s+(?:device|软元件)\b)", after, re.I))
                 or re.search(r"(?:CPU|PLC|型号|model)\s*[:：为是=]?\s*$", before, re.I)
                 or re.match(r"\s*(?:CPU|PLC|型号|手册|使用内置)", after, re.I)
                 or re.search(r"(?:在|on|with|for)\s*$", before, re.I))
        explicit_device = bool(re.match(r"\s*(?:接点|触点|继电器|软元件|地址|contact|relay|device|coil)", after, re.I))
        return " " * len(match[0]) if named and not explicit_device else match[0]
    return pattern.sub(mask, text)
_MOTION = re.compile(
    r"伺服|步进电机|步进驱动|运动控制|定位轴|高速脉冲输出|回原点|回零|"
    r"\b(?:servo|stepper|positioning|homing)\b|\bmotion\s+control\b", re.I,
)
_VFD = re.compile(r"变频器|多段速|频率给定|模拟调速|\b(?:vfd|inverter|stf|rh|rm|rl)\b", re.I)
_FAMILIES = {
    "pulse": {"PLSY", "DPLSY", "PLSV", "DPLSV"},
    "position": {"DRVI", "DDRVI", "DRVA", "DDRVA"},
    "zero_return": {"ZRN"},
    "dog_search": {"DSZR"},
    "interrupt_position": {"DVIT"},
}


def _scalars(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _DEVICE.fullmatch(str(key)):
                yield str(key)
            yield from _scalars(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _scalars(child)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield str(value)


def _selected_text(spec):
    if not isinstance(spec, Mapping):
        return str(spec or "")
    selected = spec.get("selected_approach")
    selected = selected if isinstance(selected, Mapping) else {}
    contract = selected.get("generation_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    values = [selected.get(key) for key in ("name", "description", "generation_guide")]
    values += [contract.get(key) for key in ("required_opcodes", "required_devices", "required_structures")]
    values += [spec.get(key) for key in ("parameters", "io_table", "io_bindings", "user_notes", "execution_semantics")]
    return "\n".join(dict.fromkeys(_scalars(values)))


@dataclass(frozen=True)
class AnalysisRoute:
    mode: str
    reason: str
    topics: tuple[str, ...]
    motion_families: tuple[str, ...]
    opcodes: tuple[str, ...]
    devices: tuple[str, ...]
    query_text: str

    @property
    def include_design(self):
        return self.mode == "design"


def route_analysis_request(user_request, confirmed_context=None, *, analysis_mode="direct",
                           resolve_opcode: Callable | None = None):
    """Only the supplied mode can enable design; pinned is a Direct substate."""
    current = str(user_request or "")
    positive = positive_requirement_text(current)
    baseline = positive_requirement_text(_selected_text(confirmed_context))
    text = "\n".join(part for part in (positive, baseline) if part.strip())
    opcodes, bases = [], set()
    if resolve_opcode is not None:
        for token in _TOKEN.finditer(instruction_reference_text(text)):
            raw_token = token.group()
            if raw_token.upper() == "END" and re.match(r"\s*(?:处理|processing)", text[token.end():], re.I):
                if not re.search(r"(?:查询|解释|是什么|查阅|lookup|explain|what)", current, re.I):
                    continue
            if raw_token in {"to", "or", "and", "not", "out", "set", "for", "end", "in", "on", "off", "hour"}:
                # Common English words need explicit instruction context. This
                # applies to every ambiguous mnemonic, not one opcode family.
                tail = text[token.end():]
                quoted = token.start() > 0 and text[token.start()-1] in "`\"'" and tail[:1] == text[token.start()-1]
                qualified = re.match(r"[ \t]*(?:instruction\b|指令|循环|/[ \t]*[A-Za-z]+\b)", tail, re.I)
                # Flattened spec fields are separated by newlines. The next
                # I/O row cannot become an operand of an English purpose word.
                operand = re.match(r"[ \t]+[KHDXYMTSCRVZ][+-]?\d+", tail, re.I)
                if not (text.strip() == raw_token or quoted or qualified or operand):
                    continue
            resolution = resolve_opcode(raw_token)
            if resolution is None:
                continue
            opcode = token.group().upper()
            if opcode not in opcodes:
                opcodes.append(opcode)
            bases.add(str(getattr(resolution, "base_mnemonic", opcode)).upper())
    selected = confirmed_context.get("selected_approach") if isinstance(confirmed_context, Mapping) else None
    has_selected = isinstance(selected, Mapping) and bool(selected)
    # No keyword/complexity/missing-parameter heuristic may open design.
    # A selected baseline only refines Direct; it cannot override Design.
    if analysis_mode == "design":
        mode, reason = "design", "user_selected_design"
    elif has_selected:
        mode, reason = "pinned", "direct_selected_approach_baseline"
    else:
        mode, reason = "direct", "direct_single_implementation"
    families = tuple(name for name, members in _FAMILIES.items() if bases & members)
    topics = []
    negated = "\n".join(match.group() for match in _NEGATED.finditer(current))
    if _VFD.search(positive) or (_VFD.search(baseline) and not _VFD.search(negated)):
        topics.append("vfd")
    if (families or _MOTION.search(instruction_reference_text(text))) and not (_MOTION.search(negated) and not _MOTION.search(positive)):
        topics.append("motion")
    if re.search(r"模拟量|模拟输入|模拟输出|\banalog\b|4\s*-\s*20\s*mA|0\s*-\s*10\s*V", text, re.I):
        topics.append("analog")
    if re.search(r"高速计数|\bHSC\b|\bDHS(?:CS|CR|Z)\b", text, re.I):
        topics.append("hsc")
    if re.search(r"泵[^。\n]{0,12}(?:轮换|交替)|(?:轮换|交替)[^。\n]{0,12}泵|\bpump\s+(?:rotation|alternation)\b", text, re.I):
        topics.append("pump")
    return AnalysisRoute(
        mode, reason, tuple(topics), families, tuple(opcodes),
        tuple(dict.fromkeys(device_tokens(device_reference_text(text)))), text,
    )


_GENERATION_FACT_TOPIC = re.compile(
    r"手册|查证|查阅|查询指令|错误码|编译错误|缓冲存储器|"
    r"定时|计时|计数|闪烁|时钟|首次扫描|首扫描|初始脉冲|特殊继电器|寄存器|索引|移位|数组|算术|比较|通信|通讯|"
    r"\b(?:timer|counter|clock|register|shift|arithmetic|modbus|rs-?485|"
    r"manual|datasheet|documentation|VAR_IN_OUT|ABI)\b|first[ -]?scan|initial\s+pulse|"
    r"\b(?:FX[0-9A-Z]+|Q[0-9A-Z]+|L[0-9A-Z]+)-[0-9A-Z-]+", re.I,
)


def has_generation_fact_target(query):
    """Select retrieval work only, never analysis mode or program acceptance.

    A resolved wiring form is not a request for every manual mentioning its
    ordinary I/O. The compiler has already separated those facts from the query.
    Keep explicit lookups, instruction/device references and technical families;
    absent lookup targets mean empty evidence, not an unsupported program.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    route = route_analysis_request(query, resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_fact_form)
    if (route.opcodes or route.devices or route.topics
            or _GENERATION_FACT_TOPIC.search(str(query or ""))):
        return True
    from knowledge.structured_facts import structured_fact_targets
    return bool(structured_fact_targets(query).get('language_features'))
