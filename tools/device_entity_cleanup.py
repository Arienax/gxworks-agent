#!/usr/bin/env python3
"""Semantic cleanup for device-like entities extracted from PLC manuals.

The PDF manuals contain several strings that are lexically indistinguishable
from PLC device addresses after whitespace/case normalization:

* ``n 512`` is an operand/count constraint, not device ``N512``.
* ``N 4`` can be an MC/MCR nesting level, not a device.
* ``D | 17 steps | DABSD`` can flatten to ``D 17 steps`` and look like D17.

This module fixes those cases after generic entity extraction.  It deliberately
does not maintain a blacklist of concrete tokens, so real examples such as
D307, M599, P10, P11 and P12 remain ordinary device entities.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Iterable

EntityCounter = Counter[tuple[str, str]]

# N is used by Mitsubishi documentation for operand/count variables and MC/MCR
# nesting levels. It is not one of the device families published by the builder.
_N_DEVICE_RE = re.compile(r"^N\d+(?:\.\d+)?$", re.I)
_N_DEVICE_RANGE_RE = re.compile(r"^N\d+-N\d+$", re.I)
_LOWER_N_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])n\s+(\d{1,4})(?![A-Za-z0-9_])")
_UPPER_N_VALUE_RE = re.compile(r"(?<![A-Za-z0-9_])N\s*(\d{1,3})(?![A-Za-z0-9_])")

# A device designator in the left column followed by an instruction step count
# is a layout artifact, e.g. ``D | 17 steps | DABSD`` -> ``D 17 steps DABSD``.
_D_STEP_COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9_])D[ \t\r\n]+(\d{1,3})[ \t\r\n]+steps\b",
)

_NESTING_CONTEXT_RE = re.compile(
    r"\b(?:MC|MCR)\b|nest(?:ing)?\s+level|master\s+control",
    re.I,
)


def _decrement(counter: EntityCounter, key: tuple[str, str], amount: int = 1) -> None:
    current = int(counter.get(key, 0))
    if current <= amount:
        counter.pop(key, None)
    else:
        counter[key] = current - amount


def _window(text: str, start: int, end: int, radius: int = 180) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)]


def _is_nesting_level(text: str, match: re.Match[str]) -> bool:
    context = _window(text, match.start(), match.end())
    if _NESTING_CONTEXT_RE.search(context):
        return True
    # Lists such as N0 -> N1 -> ... -> N7 are also unambiguously nesting syntax.
    nearby_n_values = _UPPER_N_VALUE_RE.findall(context)
    return len(nearby_n_values) >= 3


def sanitize_device_like_entities(
    text: str,
    chunk_type: str,
    entities: Iterable[tuple[tuple[str, str], int]] | EntityCounter,
) -> EntityCounter:
    """Return a cleaned copy of extracted entity counts.

    Only semantic false positives are changed.  Concrete D/M/P device examples
    are retained even when they occur inside instruction chunks that also contain
    operand-definition prose.
    """
    cleaned: EntityCounter = Counter(dict(entities))

    # N<number> is never emitted as a PLC device.  Reconstruct useful semantics
    # from source spelling instead of trusting the case-insensitive generic regex.
    for key in list(cleaned):
        entity, kind = key
        if kind == "device" and _N_DEVICE_RE.fullmatch(entity):
            cleaned.pop(key, None)
        elif kind == "device_range" and _N_DEVICE_RANGE_RE.fullmatch(entity):
            cleaned.pop(key, None)

    if chunk_type == "instruction":
        for match in _LOWER_N_VALUE_RE.finditer(text):
            token = f"N{match.group(1)}"
            cleaned[(token, "operand_placeholder")] += 1

    for match in _UPPER_N_VALUE_RE.finditer(text):
        if _is_nesting_level(text, match):
            token = f"N{match.group(1)}"
            cleaned[(token, "nesting_level")] += 1

    # Remove only occurrences whose source spelling explicitly says "steps".
    # This addresses D17 from the ABSD/DABSD instruction-size table without
    # touching real D17 occurrences elsewhere in the same or other chunks.
    for match in _D_STEP_COUNT_RE.finditer(text):
        token = f"D{match.group(1)}"
        _decrement(cleaned, (token, "device"), 1)

    return cleaned
