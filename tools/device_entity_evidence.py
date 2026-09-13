#!/usr/bin/env python3
"""Shared provenance-aware evidence rules for PLC device entities.

The rules are intentionally conservative: a token is considered a concrete
PLC address/pointer when the local source text shows an actual instruction,
range, table/example, pointer/label, or explicit capacity/use context. Merely
appearing in an instruction chunk or near an operand table is not enough.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable

TOKEN_BOUNDARY = r"[A-Z0-9_]"
PLACEHOLDER_CONTEXT_RE = re.compile(
    r"operand|source\s+data|set\s+data|instruction\s+format|"
    r"source\s+operand|destination\s+operand|position|placeholder|"
    r"操作数|源数据|源操作数|形参|占位符",
    re.I,
)
EXAMPLE_CONTEXT_RE = re.compile(
    r"program\s+example|programming\s+example|example\s+program|"
    r"operation\s+example|calculation\s+example|control\s+example|"
    r"sample\s+program|example:",
    re.I,
)
ADDRESS_CONTEXT_RE = re.compile(
    r"device\s+(?:number|address|range)|address|register|relay|"
    r"device\s+name|bit\s+device|word\s+device|state\s+relay|"
    r"pointer|label",
    re.I,
)
STRUCTURED_CONTEXT_RE = re.compile(
    r"\[STRUCTURED INSTRUCTION RECORD\]|Set data|Applicable devices|Operand Type",
    re.I,
)


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def token_pattern(token: str) -> re.Pattern[str]:
    match = re.fullmatch(r"([A-Z]+)(\d+)(?:\.(\d+))?", token.upper())
    if not match:
        return re.compile(rf"(?<!{TOKEN_BOUNDARY}){re.escape(token)}(?!{TOKEN_BOUNDARY})", re.I)
    prefix, number, bit = match.groups()
    suffix = rf"\s*\.\s*{re.escape(bit)}" if bit is not None else ""
    return re.compile(
        rf"(?<!{TOKEN_BOUNDARY}){re.escape(prefix)}\s*{re.escape(number)}{suffix}(?!{TOKEN_BOUNDARY})",
        re.I,
    )


def local_line(text: str, start: int, end: int) -> str:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    if right < 0:
        right = len(text)
    return normalize(text[left:right])[:600]


def compact_excerpt(text: str, start: int, end: int, radius: int = 220) -> str:
    excerpt = text[max(0, start - radius):min(len(text), end + radius)].replace("\r", "")
    excerpt = re.sub(r"[ \t]+", " ", excerpt)
    excerpt = re.sub(r"\n{3,}", "\n\n", excerpt)
    return excerpt.strip()


def occurrence_signals(token: str, text: str, match: re.Match[str]) -> dict[str, bool]:
    token = token.upper()
    prefix_match = re.match(r"[A-Z]+", token)
    prefix = prefix_match.group(0) if prefix_match else ""
    window = text[max(0, match.start() - 220):min(len(text), match.end() + 260)]
    line = local_line(text, match.start(), match.end())
    spelling = match.group(0)
    flexible = token_pattern(token).pattern

    concrete_instruction_use = bool(
        re.search(
            rf"\b(?:LDI?|OUT|SET|RST|MOV|DMOV|BMOV|FMOV|ZRST|CJ|CALLP?|"
            rf"WXOR|WAND|WOR|FLT|BIN|DEDIV|DEMUL|DEBCD|DINT|DECO|ENCO|"
            rf"PRUN|DPRUN|FINS|FINSP|TRD|D?HTOS|ABSD|DABSD|FROM|TO)\b"
            rf"[^\n]{{0,140}}{flexible}",
            line,
            re.I,
        )
    )
    concrete_range_use = bool(
        re.search(
            rf"{flexible}\s*(?:~|-|–|—|to|through)\s*"
            rf"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPI])\s*\d+",
            window,
            re.I,
        )
        or re.search(
            rf"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPI])\s*\d+\s*"
            rf"(?:~|-|–|—|to|through)\s*{flexible}",
            window,
            re.I,
        )
    )
    pointer_or_label_use = bool(
        prefix == "P"
        and (
            re.search(rf"\b(?:pointer|label)\b[^\n]{{0,100}}{flexible}", window, re.I)
            or re.search(rf"{flexible}\s*:", line, re.I)
            or re.search(rf"\b(?:CJ|CALLP?)\b[^\n]{{0,80}}{flexible}", line, re.I)
        )
    )
    table_or_pair_use = bool(
        ("|" in line and not re.search(r"\bsteps?\b", line, re.I))
        or re.search(rf"[,(]\s*{flexible}(?:\s*[,)]|\s*$)", line, re.I)
        or re.search(rf"{flexible}\s*[,)]", line, re.I)
    )
    explicit_capacity_use = bool(
        re.search(rf"\bup\s+to\s*(?:\n|\s)*{flexible}", window, re.I)
        or re.search(rf"{flexible}[^\n]{{0,80}}\b(?:turns?|becomes?|reset|written|stored)\b", window, re.I)
    )
    layout_step_fusion = bool(
        prefix == "D"
        and re.search(r"^D\s+\d+", spelling, re.I)
        and re.match(r"\s+steps?\b", text[match.end():match.end() + 24], re.I)
    )

    return {
        "placeholder_semantics": bool(PLACEHOLDER_CONTEXT_RE.search(window)),
        "example_semantics": bool(EXAMPLE_CONTEXT_RE.search(window)),
        "address_semantics": bool(ADDRESS_CONTEXT_RE.search(window)),
        "structured_semantics": bool(STRUCTURED_CONTEXT_RE.search(window)),
        "concrete_instruction_use": concrete_instruction_use,
        "concrete_range_use": concrete_range_use,
        "pointer_or_label_use": pointer_or_label_use,
        "table_or_pair_use": table_or_pair_use,
        "explicit_capacity_use": explicit_capacity_use,
        "layout_step_fusion": layout_step_fusion,
    }


def classify_provenance(
    token: str,
    provenance: Iterable[tuple[Any, ...]],
    *,
    text_index: int,
    chunk_id_index: int = 0,
    opcode_index: int | None = None,
    max_samples: int = 4,
) -> dict[str, Any]:
    """Classify one normalized token from chunk provenance rows.

    `provenance` may contain any row shape as long as the caller supplies the
    text column index. The result does not mutate the database.
    """
    pattern = token_pattern(token)
    counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    matched_occurrences = 0

    for row in provenance:
        text = str(row[text_index] or "")
        for match in pattern.finditer(text):
            matched_occurrences += 1
            signals = occurrence_signals(token, text, match)
            for name, active in signals.items():
                if active:
                    counts[name] += 1
            if len(samples) < max_samples:
                item: dict[str, Any] = {
                    "chunk_id": int(row[chunk_id_index]),
                    "line": local_line(text, match.start(), match.end()),
                    "source_spelling": match.group(0),
                    "signals": signals,
                }
                if opcode_index is not None:
                    item["opcode"] = str(row[opcode_index] or "")
                samples.append(item)

    concrete_count = sum(
        counts[name]
        for name in (
            "concrete_instruction_use",
            "concrete_range_use",
            "pointer_or_label_use",
            "table_or_pair_use",
            "explicit_capacity_use",
        )
    )
    layout_only = bool(counts["layout_step_fusion"] and concrete_count == 0)
    placeholder_only = bool(counts["placeholder_semantics"] and concrete_count == 0)
    if layout_only:
        classification = "layout_artifact_likely"
    elif concrete_count:
        classification = "real_device_example_likely"
    elif placeholder_only:
        classification = "operand_placeholder_likely"
    else:
        classification = "ambiguous"

    return {
        "classification": classification,
        "matched_occurrences": matched_occurrences,
        "signal_counts": dict(sorted(counts.items())),
        "samples": samples,
    }
