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
    lines = str(text or "").splitlines()
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1
    arity = len(expected)
    out = []
    for match in pattern.finditer(str(text or "")):
        line_index = max((i for i, off in enumerate(offsets) if off <= match.start()), default=0)
        # Only source-rendered page/layout/table material can conflict with the
        # structured lane. Ignore the structured line itself and ordinary metadata.
        marker = ""
        for i in range(line_index, -1, -1):
            if lines[i].startswith("[PAGE ") or lines[i].startswith("[TABLE "):
                marker = lines[i]
                break
            if lines[i].startswith("[KNOWLEDGE ") or lines[i].startswith("[/KNOWLEDGE]"):
                break
        if not marker:
            continue
        immediate = "\n".join(lines[line_index:line_index + 4])
        same_line = lines[line_index][match.start() - offsets[line_index]:]
        glyphs = immediate.count("[GLYPH-")
        after = immediate[immediate.upper().find(opcode.upper()) + len(opcode):]
        seq = _tokens(after, expected)
        same = _tokens(same_line[len(opcode):], expected)
        tiers = []
        if same and same[0] != expected[0] and glyphs >= 1:
            tiers.append("inline_prefix_conflict")
        if glyphs >= 2 and len(seq) == arity and set(seq) == set(expected) and seq != expected:
            tiers.append("complete_diagram_conflict")
        if tiers:
            out.append({
                "tiers": tiers,
                "sequence": seq or same,
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
