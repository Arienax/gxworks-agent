"""Route PLC facts; analysis mode is an explicit caller choice, never inferred."""
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
    positive = _NEGATED.sub("", current)
    baseline = _NEGATED.sub("", _selected_text(confirmed_context))
    text = "\n".join(part for part in (positive, baseline) if part.strip())
    opcodes, bases = [], set()
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


_GENERATION_FACT_TOPIC = re.compile(
    r"手册|查证|查阅|查询指令|错误码|编译错误|缓冲存储器|"
    r"定时|计时|计数|闪烁|时钟|寄存器|索引|移位|数组|算术|比较|通信|通讯|"
    r"\b(?:timer|counter|clock|register|shift|arithmetic|modbus|rs-?485|"
    r"manual|datasheet|documentation|VAR_IN_OUT|ABI)\b|"
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
    route = route_analysis_request(query, resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form)
    return bool(route.opcodes or route.devices or route.topics
                or _GENERATION_FACT_TOPIC.search(str(query or "")))
