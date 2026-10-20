"""Explicit fact-query routing; these predicates never affect broad RRF."""
from __future__ import annotations

import re
import copy
from collections.abc import Mapping

from knowledge.text_matching import normalize_text, PRODUCT_TERM_RE


def positioning_targets(query, *, plc_model=None, confirmed_spec=None):
    """Describe a wiring fact need with its actual CPU and hardware context."""
    if not query_is_direction_output_assignment(query):
        return []
    metadata = getattr(query, "metadata", {})
    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    hardware = (metadata.get("hardware_context") if isinstance(metadata, Mapping) else None)
    if hardware is None:
        hardware = spec.get("hardware_context")
    products = list(dict.fromkeys(match[0].upper() for match in PRODUCT_TERM_RE.finditer(str(query))))
    return [{"target": "DIRECTION_OUTPUT_ASSIGNMENT", "plc_model": str(plc_model or "").upper(),
             "hardware_context": copy.deepcopy(hardware), "products": products}]


def query_is_positioning(query):
    """Recognize motion queries without confusing a step state machine with a stepper."""

    normalized = normalize_text(query)
    return bool(
        re.search(
            r"(?<![A-Za-z0-9_])(?:DRVI|DRVA|ZRN|DSZR|DVIT|PLSY|DPLSY|PLSV)"
            r"(?![A-Za-z0-9_])|"
            r"(?<![A-Za-z0-9_])(?:M8029|M8336|D834[0-9]|D835[0-9])"
            r"(?![A-Za-z0-9_])|"
            r"FX\d[A-Z0-9]*(?:-[A-Z0-9]+)+|"
            r"position(?:ing)?|pulse output|zero return|home return|servo|stepper|"
            r"\u5b9a\u4f4d|\u8109\u51b2|\u539f\u70b9|\u56de\u96f6|"
            r"\u4f3a\u670d|\u6b65\u8fdb\u7535\u673a|\u6b65\u8fdb\u9a71\u52a8\u5668",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def query_is_direction_output_assignment(query):
    """Recognize pulse/direction wiring assignment questions in either language."""

    normalized = normalize_text(query)
    direction = re.search(
        r"方向(?:输出|信号)|direction\s+(?:signal|output)", normalized, re.IGNORECASE,
    )
    assignment = re.search(
        r"固定|配对|映射|任意|指定|分配|可以用|assign|arbitrary|fixed|pair|any\s+output|must",
        normalized, re.IGNORECASE,
    )
    motion = query_is_positioning(normalized) or re.search(
        r"(?<![A-Za-z0-9_])Y\d+(?![A-Za-z0-9_])", normalized, re.IGNORECASE,
    )
    return bool(direction and assignment and motion)


def query_is_clock_semantics(query):
    normalized = normalize_text(query)
    return bool(
        re.search(
            r"(?<![A-Za-z0-9_])M801[1-4](?![A-Za-z0-9_])|"
            r"时钟|闪烁|振荡|方波|\bclock\b|\bblink\w*\b|\bflash\w*\b|"
            r"\boscillat\w*\b|square\s*wave",
            normalized,
            re.IGNORECASE,
        )
    )


def query_is_timer_preset(query):
    """Scope device-range evidence to timer settings, units, and time-base questions."""
    normalized = normalize_text(query)
    timer = re.search(r"\btimers?\b|\bT\d+\b|定时器|计时器|时基", normalized, re.IGNORECASE)
    setting = re.search(
        r"\bK\d*\b|\bpreset\b|\btime\s*base\b|\bresolution\b|\bunits?\b|"
        r"\btimer\s+(?:numbers?|ranges?)\b|时基|时间基准|分辨率|预置值|设定值|定时器编号",
        normalized, re.IGNORECASE,
    )
    return bool(timer and setting and not query_is_clock_semantics(normalized))


def timer_range_evidence(text, plc_model):
    """Require model, timer range and time units in the original evidence body."""
    body = re.split(r"\n\[(?:PAGE|TABLE)\b", str(text or ""), maxsplit=1)[-1]
    model = normalize_text(plc_model)
    if not model:
        return False
    model_pattern = r"\s*".join(re.escape(character) for character in model)
    return bool(
        re.search(r"(?<![A-Za-z0-9])" + model_pattern + r"(?![A-Za-z0-9])", body, re.IGNORECASE)
        and re.search(r"\bT\d+\s*(?:to|[-–～]|至)\s*T\d+\b", body, re.IGNORECASE)
        and re.search(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?)\b", body, re.IGNORECASE)
    )
