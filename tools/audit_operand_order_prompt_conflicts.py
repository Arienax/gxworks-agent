#!/usr/bin/env python3
"""Audit RAG-visible operand-order contradictions for promoted FX3U forms.

This is intentionally separate from signature promotion. Native operand order is
owned by the geometric Set data extraction. The question here is whether the
same authoritative source chunk also exposes a flattened visual/layout sequence
that a model can reasonably read as a different operand order.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "resources/knowledge/fx3u_knowledge.sqlite"
LEDGER = ROOT / "resources/instructions/mitsubishi/fx3u_contract_promotions.json"
NATIVE = "fx3_programming_r"
TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:[SDNMsdnm]\d{0,3}|n)(?![A-Za-z0-9_])")
MARKER = re.compile(r"(?m)^\[(PAGE|TABLE)[^\n]*")
VISUAL = re.compile(r"\[GLYPH-[0-9A-F]+\]|(?:→|←|↔|->|<-)", re.I)


def _symbol(value: str) -> str:
    return str(value).upper()


def _units(text: str):
    matches = list(MARKER.finditer(text))
    starts = [match.start() for match in matches]
    if not starts:
        yield "", text
        return
    if starts[0] > 0:
        yield "", text[:starts[0]]
    for index, match in enumerate(matches):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        yield match.group(0), text[match.start():end]


def _candidate_sequences(text: str, opcode: str, expected: list[str]):
    boundary = r"[A-Za-z0-9_.$@+<>!=\-]"
    pattern = re.compile(
        r"(?<!%s)%s(?!%s)" % (boundary, re.escape(opcode), boundary),
        re.I,
    )
    expected_set = set(expected)
    arity = len(expected)
    for marker, unit in _units(text):
        mode = "table" if marker.startswith("[TABLE") else (
            "layout" if "LAYOUT" in marker or "LADDER/DIAGRAM" in marker else
            "prose" if "PROSE" in marker else "other"
        )
        for match in pattern.finditer(unit):
            # The problematic source is a flattened instruction graphic, not
            # ordinary prose mentioning one operand. Keep the window local.
            tail = unit[match.end():match.end() + 420]
            visual = bool(VISUAL.search(tail))
            short_lines = [line.strip() for line in tail.splitlines()[:14] if line.strip()]
            layoutish = visual or (
                len(short_lines) >= 3
                and sum(len(line) <= 48 for line in short_lines) >= 3
            )
            if not layoutish:
                continue

            tokens = [_symbol(value) for value in TOKEN.findall(tail)]
            # Keep first occurrence of each expected placeholder. The promoted
            # native signatures are unique-symbol sequences by construction.
            sequence = []
            for token in tokens:
                if token in expected_set and token not in sequence:
                    sequence.append(token)
                if len(sequence) == arity:
                    break
            if len(sequence) < 2:
                continue

            hard = len(sequence) == arity and set(sequence) == expected_set and sequence != expected
            # Partial visual order is suspicious when its relative order cannot
            # occur as a subsequence of the verified native order.
            indexes = [expected.index(token) for token in sequence if token in expected_set]
            partial_conflict = any(left >= right for left, right in zip(indexes, indexes[1:]))
            if not hard and not partial_conflict:
                continue

            excerpt = unit[match.start():match.end() + 420]
            yield {
                "mode": mode,
                "sequence": sequence,
                "hard": hard,
                "excerpt": excerpt[:700].replace("\u0000", ""),
            }


def build_report(database=DB, ledger_path=LEDGER):
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    promoted = []
    for entry in ledger["entries"]:
        for form in entry["forms"]:
            promoted.append({
                "opcode": form,
                "expected": [str(value).upper() for value in entry["native_order"]],
                "native_page": entry["native_page"],
            })

    conflicts = []
    missing = []
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for item in promoted:
            rows = connection.execute(
                """
                SELECT c.id,c.text,c.pdf_page,c.manual_id
                FROM instructions i
                JOIN chunks c ON CAST(c.id AS TEXT)=CAST(i.chunk_id AS TEXT)
                WHERE UPPER(i.opcode_norm)=? AND c.manual_id=?
                ORDER BY CASE WHEN c.pdf_page=? THEN 0 ELSE 1 END,c.pdf_page,c.id
                """,
                (item["opcode"], NATIVE, item["native_page"]),
            ).fetchall()
            if not rows:
                missing.append(item)
                continue
            seen = set()
            for row in rows:
                for candidate in _candidate_sequences(
                    str(row["text"] or ""), item["opcode"], item["expected"]
                ):
                    marker = (
                        item["opcode"], tuple(candidate["sequence"]),
                        row["id"], candidate["excerpt"],
                    )
                    if marker in seen:
                        continue
                    seen.add(marker)
                    conflicts.append({
                        **item,
                        "chunk_id": str(row["id"]),
                        "chunk_page": row["pdf_page"],
                        **candidate,
                    })

    hard_forms = sorted({row["opcode"] for row in conflicts if row["hard"]})
    all_forms = sorted({row["opcode"] for row in conflicts})
    return {
        "schema_version": 1,
        "promoted_forms": len(promoted),
        "ledger_entries": len(ledger["entries"]),
        "forms_with_rag_visible_order_conflict": len(all_forms),
        "forms_with_complete_alternative_order": len(hard_forms),
        "conflict_forms": all_forms,
        "hard_conflict_forms": hard_forms,
        "missing_forms": [row["opcode"] for row in missing],
        "conflicts": conflicts,
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
