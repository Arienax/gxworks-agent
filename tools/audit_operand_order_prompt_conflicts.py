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


def _tokens(value: str, expected: list[str]) -> list[str]:
    # PDF layout can collapse adjacent visual cells (for example nS1).
    value = re.sub(r"(?i)(?<![A-Z0-9])n(?=[SDM]\\d)", "n ", value)
    expected_set = set(expected)
    result = []
    for raw in TOKEN.findall(value):
        token = _symbol(raw)
        if token in expected_set and token not in result:
            result.append(token)
    return result


def _candidate_sequences(text: str, opcode: str, expected: list[str]):
    boundary = r"[A-Za-z0-9_.$@+<>!=\\-]"
    pattern = re.compile(
        r"(?<!%s)%s(?!%s)" % (boundary, re.escape(opcode), boundary),
        re.I,
    )
    arity = len(expected)
    for marker, unit in _units(text):
        mode = "table" if marker.startswith("[TABLE") else (
            "layout" if "LAYOUT" in marker or "LADDER/DIAGRAM" in marker else
            "prose" if "PROSE" in marker else "other"
        )
        lines = unit.splitlines()
        offsets = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line) + 1
        for match in pattern.finditer(unit):
            line_index = max(
                (index for index, offset in enumerate(offsets) if offset <= match.start()),
                default=0,
            )
            immediate = "\\n".join(lines[line_index:line_index + 4])
            same_line = lines[line_index][match.start() - offsets[line_index]:]
            glyphs = immediate.count("[GLYPH-")
            immediate_tokens = _tokens(immediate[immediate.upper().find(opcode.upper()) + len(opcode):], expected)
            same_tokens = _tokens(same_line[len(opcode):], expected)

            tiers = []
            if same_tokens and same_tokens[0] != expected[0] and glyphs >= 1:
                tiers.append("inline_prefix_conflict")
            if (
                glyphs >= 2
                and len(immediate_tokens) == arity
                and set(immediate_tokens) == set(expected)
                and immediate_tokens != expected
            ):
                tiers.append("complete_diagram_conflict")

            # Broad diagnostic tier: useful for finding other flattened visual
            # sequences, but not counted as a confirmed prompt contradiction.
            tail = unit[match.end():match.end() + 420]
            broad_tokens = _tokens(tail, expected)
            indexes = [expected.index(token) for token in broad_tokens]
            broad_conflict = (
                len(broad_tokens) >= 2
                and any(left >= right for left, right in zip(indexes, indexes[1:]))
                and (
                    bool(VISUAL.search(tail))
                    or (
                        len([line for line in tail.splitlines()[:14] if line.strip()]) >= 3
                        and sum(len(line.strip()) <= 48 for line in tail.splitlines()[:14] if line.strip()) >= 3
                    )
                )
            )
            if broad_conflict:
                tiers.append("broad_visual_conflict")
            if not tiers:
                continue

            excerpt = "\\n".join(lines[line_index:line_index + 8])
            yield {
                "mode": mode,
                "sequence": immediate_tokens or broad_tokens,
                "tiers": sorted(set(tiers)),
                "excerpt": excerpt[:900].replace("\\u0000", ""),
            }


def build_report(database=DB, ledger_path=LEDGER):
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    conflicts = []
    missing_entries = []
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for entry_index, entry in enumerate(ledger["entries"]):
            expected = [str(value).upper() for value in entry["native_order"]]
            forms = [str(value).upper() for value in entry["forms"]]
            placeholders = ",".join("?" for _ in forms)
            rows = connection.execute(
                f"""
                SELECT DISTINCT c.id,c.text,c.pdf_page,c.manual_id
                FROM instructions i
                JOIN chunks c ON CAST(c.id AS TEXT)=CAST(i.chunk_id AS TEXT)
                WHERE UPPER(i.opcode_norm) IN ({placeholders}) AND c.manual_id=?
                ORDER BY c.pdf_page,c.id
                """,
                (*forms, NATIVE),
            ).fetchall()
            if not rows:
                missing_entries.append({"entry": entry_index, "forms": forms})
                continue
            seen = set()
            for row in rows:
                for opcode in forms:
                    for candidate in _candidate_sequences(
                        str(row["text"] or ""), opcode, expected
                    ):
                        marker = (
                            opcode, tuple(candidate["sequence"]),
                            tuple(candidate["tiers"]), row["id"], candidate["excerpt"],
                        )
                        if marker in seen:
                            continue
                        seen.add(marker)
                        conflicts.append({
                            "entry": entry_index,
                            "forms": forms,
                            "opcode": opcode,
                            "expected": expected,
                            "native_page": entry["native_page"],
                            "chunk_id": str(row["id"]),
                            "chunk_page": row["pdf_page"],
                            **candidate,
                        })

    high = [
        row for row in conflicts
        if {"inline_prefix_conflict", "complete_diagram_conflict"} & set(row["tiers"])
    ]
    high_entries = sorted({row["entry"] for row in high})
    high_forms = sorted({
        form for row in high for form in row["forms"]
    })
    inline_forms = sorted({
        form for row in high
        if "inline_prefix_conflict" in row["tiers"]
        for form in row["forms"]
    })
    complete_forms = sorted({
        form for row in high
        if "complete_diagram_conflict" in row["tiers"]
        for form in row["forms"]
    })
    broad_forms = sorted({
        form for row in conflicts
        if "broad_visual_conflict" in row["tiers"]
        for form in row["forms"]
    })
    return {
        "schema_version": 2,
        "promoted_forms": sum(len(entry["forms"]) for entry in ledger["entries"]),
        "ledger_entries": len(ledger["entries"]),
        "high_conflict_entries": len(high_entries),
        "high_conflict_forms": len(high_forms),
        "inline_prefix_conflict_forms": len(inline_forms),
        "complete_diagram_conflict_forms": len(complete_forms),
        "broad_candidate_forms": len(broad_forms),
        "high_conflict_form_names": high_forms,
        "inline_prefix_form_names": inline_forms,
        "complete_diagram_form_names": complete_forms,
        "broad_candidate_form_names": broad_forms,
        "missing_entries": missing_entries,
        "high_conflicts": high,
    }


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
