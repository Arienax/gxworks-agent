#!/usr/bin/env python3
"""Collect local source evidence for device records that resemble operand placeholders.

Diagnostic only: this tool never deletes or rewrites device/entity rows. It separates
concrete PLC address examples from operand-placeholder residue and intentionally keeps
mixed/ambiguous evidence visible for review.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

OPERAND_POSITION_RE = re.compile(r"^[SDNMP](?:\d{0,3})?$", re.I)
TOKEN_BOUNDARY = r"[A-Z0-9_]"
PLACEHOLDER_CONTEXT_RE = re.compile(
    r"operand|source\s+data|set\s+data|instruction\s+format|"
    r"source\s+operand|destination\s+operand|position|placeholder|"
    r"操作数|源数据|源操作数|形参|占位符",
    re.I,
)
EXAMPLE_CONTEXT_RE = re.compile(
    r"program\s+example|programming\s+example|example\s+program|"
    r"operation\s+example|sample\s+program|example:",
    re.I,
)
ADDRESS_CONTEXT_RE = re.compile(
    r"device\s+(?:number|address|range)|address|register|relay|"
    r"device\s+name|bit\s+device|word\s+device|state\s+relay|pointer|label",
    re.I,
)
STRUCTURED_CONTEXT_RE = re.compile(
    r"\[STRUCTURED INSTRUCTION RECORD\]|Set data|Applicable devices|Operand Type",
    re.I,
)


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def token_source_pattern(token: str) -> str:
    """Match normalized device tokens in source text, including PDF forms like `D 17`."""
    match = re.fullmatch(r"([A-Z]+)(\d+)", token, re.I)
    if not match:
        return re.escape(token)
    return rf"{re.escape(match.group(1))}\s*{re.escape(match.group(2))}"


def compact_excerpt(text: str, start: int, end: int, radius: int = 220) -> str:
    excerpt = text[max(0, start - radius):min(len(text), end + radius)].replace("\r", "")
    excerpt = re.sub(r"[ \t]+", " ", excerpt)
    excerpt = re.sub(r"\n{3,}", "\n\n", excerpt)
    return excerpt.strip()


def local_line(text: str, start: int, end: int) -> str:
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    if right < 0:
        right = len(text)
    return normalize(text[left:right])[:500]


def occurrence_signals(token: str, text: str, match: re.Match[str]) -> dict[str, bool]:
    window = text[max(0, match.start() - 220):min(len(text), match.end() + 260)]
    line = local_line(text, match.start(), match.end())
    token_pat = token_source_pattern(token)
    concrete_instruction_use = bool(
        re.search(
            rf"\b(?:FNC\s*\d+\s*)?[A-Z][A-Z0-9_]{{1,10}}(?:\([^\n)]*|\s+[^\n]{{0,100}})?{token_pat}(?!{TOKEN_BOUNDARY})",
            line,
            re.I,
        )
    )
    concrete_range_use = bool(
        re.search(
            rf"(?<!{TOKEN_BOUNDARY}){token_pat}\s*(?:~|-|–|—|to|through)\s*"
            rf"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])\s*\d+",
            window,
            re.I,
        )
        or re.search(
            rf"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])\s*\d+\s*"
            rf"(?:~|-|–|—|to|through)\s*{token_pat}(?!{TOKEN_BOUNDARY})",
            window,
            re.I,
        )
    )
    pointer_or_label_use = bool(
        token.startswith("P")
        and re.search(rf"\b(?:pointer|label|CJ|CALLP?|P)\b[^\n]{{0,100}}{token_pat}", window, re.I)
    )
    nesting_level_use = bool(
        token.startswith("N")
        and re.search(rf"\b(?:nest|nesting)\b[^\n]{{0,160}}{token_pat}|{token_pat}[^\n]{{0,160}}\b(?:nest|nesting)\b", window, re.I)
    )
    return {
        "placeholder_semantics": bool(PLACEHOLDER_CONTEXT_RE.search(window)),
        "example_semantics": bool(EXAMPLE_CONTEXT_RE.search(window)),
        "address_semantics": bool(ADDRESS_CONTEXT_RE.search(window)),
        "structured_semantics": bool(STRUCTURED_CONTEXT_RE.search(window)),
        "concrete_instruction_use": concrete_instruction_use,
        "concrete_range_use": concrete_range_use,
        "pointer_or_label_use": pointer_or_label_use,
        "nesting_level_use": nesting_level_use,
    }


def classify_signal_counts(counts: Counter[str]) -> str:
    real = sum(
        counts[name]
        for name in (
            "concrete_instruction_use",
            "concrete_range_use",
            "example_semantics",
            "pointer_or_label_use",
            "nesting_level_use",
        )
    )
    placeholder = counts["placeholder_semantics"]
    if real and not placeholder:
        return "real_device_example_likely"
    if placeholder and not real:
        return "operand_placeholder_likely"
    if real and placeholder:
        return "mixed_evidence"
    return "ambiguous"


def collect(connection: sqlite3.Connection) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    rows = connection.execute(
        "SELECT id,device_norm,device,record_type,occurrences FROM device_records ORDER BY id"
    ).fetchall()

    for row_id, device_norm, device, record_type, occurrences in rows:
        token = normalize(device).upper()
        if record_type != "device" or not OPERAND_POSITION_RE.fullmatch(token) or not re.search(r"\d", token):
            continue
        provenance = connection.execute(
            """
            SELECT e.chunk_id,e.occurrences,c.chunk_type,c.instruction_opcode,c.text,c.manual_id
            FROM entity_index e
            JOIN chunks c ON c.id=e.chunk_id
            WHERE e.entity_norm=? AND e.entity_type='device'
            ORDER BY CASE WHEN c.chunk_type='instruction' THEN 0 ELSE 1 END,
                     e.occurrences DESC,e.chunk_id
            """,
            (device_norm,),
        ).fetchall()
        if not provenance:
            continue
        instruction_occ = sum(int(item[1]) for item in provenance if item[2] == "instruction")
        total_occ = sum(int(item[1]) for item in provenance)
        structured_hits = sum(
            int(item[1])
            for item in provenance
            if item[2] == "instruction" and STRUCTURED_CONTEXT_RE.search(str(item[4] or ""))
        )
        if not total_occ or instruction_occ / total_occ < 0.75 or not structured_hits:
            continue

        token_re = re.compile(
            rf"(?<!{TOKEN_BOUNDARY}){token_source_pattern(token)}(?!{TOKEN_BOUNDARY})",
            re.I,
        )
        signal_counts: Counter[str] = Counter()
        snippets: list[dict[str, Any]] = []
        seen_snippets: set[tuple[int, str]] = set()
        occurrence_count = 0

        for chunk_id, entity_occurrences, chunk_type, opcode, text, manual_id in provenance:
            source = str(text or "")
            for match in token_re.finditer(source):
                occurrence_count += 1
                signals = occurrence_signals(token, source, match)
                for name, active in signals.items():
                    if active:
                        signal_counts[name] += 1
                line = local_line(source, match.start(), match.end())
                key = (int(chunk_id), line)
                if key in seen_snippets:
                    continue
                if len(snippets) < 12:
                    snippets.append(
                        {
                            "manual_id": str(manual_id),
                            "chunk_id": int(chunk_id),
                            "chunk_type": str(chunk_type),
                            "opcode": str(opcode or ""),
                            "entity_occurrences": int(entity_occurrences),
                            "source_spelling": match.group(0),
                            "line": line,
                            "excerpt": compact_excerpt(source, match.start(), match.end()),
                            "signals": signals,
                        }
                    )
                    seen_snippets.add(key)

        candidates.append(
            {
                "id": int(row_id),
                "device": token,
                "device_record_occurrences": int(occurrences),
                "instruction_occurrences": instruction_occ,
                "total_provenance_occurrences": total_occ,
                "structured_instruction_occurrences": structured_hits,
                "matched_text_occurrences": occurrence_count,
                "signal_counts": dict(sorted(signal_counts.items())),
                "suggested_class": classify_signal_counts(signal_counts),
                "snippets": snippets,
            }
        )

    class_counts = Counter(item["suggested_class"] for item in candidates)
    return {
        "candidate_count": len(candidates),
        "suggested_class_counts": dict(sorted(class_counts.items())),
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.database) as connection:
        payload = collect(connection)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("DEVICE_PLACEHOLDER_CANDIDATES", payload["candidate_count"])
    print("SUGGESTED_CLASSES", json.dumps(payload["suggested_class_counts"], ensure_ascii=False, sort_keys=True))
    for item in payload["candidates"]:
        print(json.dumps({
            "device": item["device"],
            "suggested_class": item["suggested_class"],
            "signal_counts": item["signal_counts"],
            "snippets": item["snippets"][:4],
        }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
