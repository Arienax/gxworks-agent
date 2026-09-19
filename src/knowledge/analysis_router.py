"""Deterministic analysis routing; no model call and no acceptance policy."""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping
from typing import Callable

_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]*(?![A-Za-z0-9_])")
_DEVICE = re.compile(r"(?<![A-Za-z0-9_])(?:SM|SD|[XYMDTCSVZ])\d+(?![A-Za-z0-9_])", re.I)
_NEGATED = re.compile(
    r"(?:不要|不用|不使用|不采用|没有(?!(?:[^，,。；;\n]{0,24})(?:型号|参数|地址|信息))|无需|不需要|不涉及|不包含|不是|不比较|不讨论|禁止|without\b|do not\b|don't\b|no\b)"
    r"(?:(?!改用|改为|改成|而是|但是|\bbut\b|\binstead\b)[^，,。；;\n])*", re.I,
)
_OPEN = re.compile(
    r"(?:其他|其余|剩余|未定|开放)(?:的)?[^，,。；;\n]{0,20}(?:设计|方案|架构)|"
    r"比较|对比|重新设计|重做方案|替代方案|其他方案|其它方案|哪种(?:实现|方案|架构)|"
    r"(?:推荐|选择|优化|改进)[^，,。；;\n]{0,24}(?:方案|架构|实现)|"
    r"(?:方案|架构)[^，,。；;\n]{0,16}(?:更好|合适|怎么选)|"
    r"如何组织[^，,。；;\n]{0,12}架构|怎么选(?:方案|架构)|"
    r"\b(?:compare|alternatives?|redesign)\b", re.I,
)
_EXTRACT = re.compile(
    r"(?:只|仅)(?:需|要)?[^，,。；;\n]{0,12}(?:整理|提取|补充|抽取)|"
    r"(?:整理|提取|抽取)[^，,。；;\n]{0,16}(?:需求|规格|参数|json)|"
    r"按(?:照)?(?:上述|以下|这个|原|已确认|既定)[^，,。；;\n]{0,12}(?:方案|规格|实现)|"
    r"(?:保持|沿用)[^，,。；;\n]{0,12}(?:方案|架构)|"
    r"\bextract\b|\bdo not redesign\b|不要重新设计", re.I,
)
_FIXED = re.compile(r"(?:使用|采用|固定|必须|就用|改用|改为|用|use\b|using\b)[^，,。；;\n]*", re.I)
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


def route_analysis_request(user_request, confirmed_context=None, *, resolve_opcode: Callable | None = None):
    current = str(user_request or "")
    positive = _NEGATED.sub("", current)
    baseline = _NEGATED.sub("", _selected_text(confirmed_context))
    text = "\n".join(part for part in (positive, baseline) if part.strip())
    current_ops, opcodes, bases = [], [], set()
    if resolve_opcode is not None:
        for token in _TOKEN.finditer(text):
            raw_token = token.group()
            if (raw_token in {"to", "or", "and", "not", "out", "set", "for", "end", "in", "on", "off"}
                    and not re.match(r"\s+[KHDXYMTSCRVZ]\d+", text[token.end():], re.I)):
                continue
            resolution = resolve_opcode(raw_token)
            if resolution is None:
                continue
            opcode = token.group().upper()
            if opcode not in opcodes:
                opcodes.append(opcode)
            bases.add(str(getattr(resolution, "base_mnemonic", opcode)).upper())
            if token.start() < len(positive) and opcode not in current_ops:
                current_ops.append(opcode)
    selected = confirmed_context.get("selected_approach") if isinstance(confirmed_context, Mapping) else None
    has_selected = isinstance(selected, Mapping) and bool(selected)
    has_call = any(re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(op)}\s+[KHDXYMTSCRVZ]\d+(?![A-Za-z0-9_])",
        positive, re.I,
    ) for op in current_ops)
    fixed_opcode = any(
        any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(op)}(?![A-Za-z0-9_])", match.group(), re.I)
            for op in current_ops)
        for match in _FIXED.finditer(positive)
    )
    if _OPEN.search(positive):
        mode, reason = "design", "explicit_design_request"
    elif _EXTRACT.search(current) or has_call or fixed_opcode:
        mode, reason = "pinned", "explicit_implementation_or_extraction"
    elif has_selected:
        mode, reason = "pinned", "selected_approach_baseline"
    else:
        mode, reason = "design", "implementation_open"
    families = tuple(name for name, members in _FAMILIES.items() if bases & members)
    topics = []
    negated = "\n".join(match.group() for match in _NEGATED.finditer(current))
    if _VFD.search(positive) or (_VFD.search(baseline) and not _VFD.search(negated)):
        topics.append("vfd")
    if (families or _MOTION.search(text)) and not (_MOTION.search(negated) and not _MOTION.search(positive)):
        topics.append("motion")
    if re.search(r"模拟量|模拟输入|模拟输出|\banalog\b|4\s*-\s*20\s*mA|0\s*-\s*10\s*V", text, re.I):
        topics.append("analog")
    if re.search(r"高速计数|\bHSC\b|\bDHS(?:CS|CR|Z)\b", text, re.I):
        topics.append("hsc")
    if re.search(r"(?:多|三|两|二|四|\d+)泵|泵[^。\n]{0,12}轮换|\bpump\s+(?:rotation|alternation)\b", text, re.I):
        topics.append("pump")
    return AnalysisRoute(
        mode, reason, tuple(topics), families, tuple(opcodes),
        tuple(dict.fromkeys(item.upper() for item in _DEVICE.findall(text))), text,
    )
