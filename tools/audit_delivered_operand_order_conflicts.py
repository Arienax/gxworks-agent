#!/usr/bin/env python3
"""Audit operand-order contradictions that actually survive into Agent-B evidence."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge.evidence import KnowledgeQuery
from knowledge.retriever import build_knowledge_context

LEDGER = ROOT / "resources/instructions/mitsubishi/fx3u_contract_promotions.json"
TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:[SDNMsdnm]\d{0,3}|n)(?![A-Za-z0-9_])")


def _tokens(value, expected):
    value = re.sub(r"(?i)(?<![A-Z0-9])n(?=[SDM]\d)", "n ", str(value or ""))
    expected_set = set(expected)
    result = []
    for raw in TOKEN.findall(value):
        token = raw.upper()
        if token in expected_set and token not in result:
            result.append(token)
    return result


def _high_conflicts(text, opcode, expected):
    boundary = r"[A-Za-z0-9_.$@+<>!=\-]"
    pattern = re.compile(
        r"(?<!%s)%s(?!%s)" % (boundary, re.escape(opcode), boundary),
        re.I,
    )
    glyph_operand = re.compile(
        r"(?i)([SDM]\d{0,3})\s*(?=\[GLYPH-[0-9A-F]+\])"
    )
    operand = re.compile(r"(?i)(?<![A-Z0-9])([SDMN]\d{0,3})(?![A-Z0-9])")
    fused_count = re.compile(
        r"(?i)(?<![A-Z0-9])(n\d{0,3})(?=[SDM]\d{0,3}\s*\[GLYPH-[0-9A-F]+\])"
    )
    expected_set = set(expected)
    lines = str(text or "").splitlines()
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    out = []
    for match in pattern.finditer(str(text or "")):
        line_index = max(
            (i for i, off in enumerate(offsets) if off <= match.start()),
            default=0,
        )
        line = lines[line_index] if lines else ""
        relative = match.start() - offsets[line_index] if offsets else 0
        prefix = line[:relative]
        if prefix.strip() and not re.search(r"(?:FNC\s*\d+|input)\s*$", prefix, re.I):
            continue
        region_lines = lines[line_index:line_index + 4]
        region = "\n".join(region_lines)
        if "[GLYPH-" not in region:
            continue

        positions = []
        for item in glyph_operand.finditer(region):
            positions.append((item.start(1), item.group(1).upper()))
        for item in fused_count.finditer(region):
            positions.append((item.start(1), item.group(1).upper()))
        tail = line[relative + len(opcode):]
        for item in operand.finditer(tail):
            token = item.group(1).upper()
            if token.startswith("N"):
                positions.append((item.start(1), token))
        running = len(region_lines[0]) + 1 if region_lines else 0
        for short_line in region_lines[1:]:
            stripped = short_line.strip()
            if re.fullmatch(r"(?i)[SDMN]\d{0,3}", stripped):
                positions.append((running + short_line.find(stripped), stripped.upper()))
            running += len(short_line) + 1

        sequence = []
        for _position, token in sorted(positions):
            if token in expected_set and token not in sequence:
                sequence.append(token)
        if len(sequence) < 2:
            continue
        indexes = [expected.index(token) for token in sequence]
        if not any(left >= right for left, right in zip(indexes, indexes[1:])):
            continue

        marker = ""
        for i in range(line_index, -1, -1):
            if lines[i].startswith("[PAGE ") or lines[i].startswith("[TABLE "):
                marker = lines[i]
                break
            if lines[i].startswith("[KNOWLEDGE ") or lines[i].startswith("[/KNOWLEDGE]"):
                break
        out.append({
            "sequence": sequence,
            "marker": marker,
            "excerpt": "\n".join(lines[line_index:line_index + 8])[:900],
        })
    return out


def build_report():
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = []
    for entry in ledger["entries"]:
        expected = [str(v).upper() for v in entry["native_order"]]
        for opcode in entry["forms"]:
            query = KnowledgeQuery(
                opcode,
                precompiled=True,
                metadata={"instruction_fact_mode": "targeted"},
            )
            context = build_knowledge_context(
                query,
                plc_model="FX3U",
                task_type="generate",
                char_budget=24000,
                top_k=8,
            )
            conflicts = _high_conflicts(str(context), opcode, expected)
            if conflicts:
                rows.append({
                    "opcode": opcode,
                    "expected": expected,
                    "conflicts": conflicts,
                })
    return {
        "schema_version": 1,
        "promoted_forms": sum(len(entry["forms"]) for entry in ledger["entries"]),
        "delivered_conflict_forms": len(rows),
        "delivered_conflict_form_names": [row["opcode"] for row in rows],
        "rows": rows,
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
