#!/usr/bin/env python3
"""Header-aware audit for structured error_records against extracted PDF tables.

This audit does not trust row 0 to be the header. It discovers the best header
row among the first rows of every source table, maps semantic columns, and then
checks whether the stored error record agrees with the exact source row.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def cell_eq(a: Any, b: Any) -> bool:
    aa, bb = norm(a).casefold(), norm(b).casefold()
    return aa == bb or (aa and bb and (aa in bb or bb in aa))


def code_tokens(value: Any) -> set[str]:
    text = norm(value).upper()
    return set(re.findall(r"(?<![A-Z0-9])(?:0X)?([0-9A-F]{4})(?:H)?(?![A-Z0-9])", text))


def classify_header(cell: Any) -> str:
    text = norm(cell).casefold()
    if re.search(r"\b(?:error|fault)\s*(?:code|no\.?|number)\b", text):
        return "code"
    if re.search(r"\b(?:corrective\s*action|action|countermeasure|remedy|solution)\b", text):
        return "action"
    if re.search(r"\b(?:cause|possible\s*cause|reason)\b", text):
        return "cause"
    if re.search(r"\b(?:message|description|error\s*(?:content|description|name)|fault\s*(?:content|description|name)|detail)\b", text):
        return "message"
    return ""


def best_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    best_index, best_map, best_score = -1, {}, -1
    for idx, row in enumerate(rows[:8]):
        mapping: dict[str, int] = {}
        for col, cell in enumerate(row):
            kind = classify_header(cell)
            if kind and kind not in mapping:
                mapping[kind] = col
        score = len(mapping) + (3 if "code" in mapping else 0)
        if score > best_score:
            best_index, best_map, best_score = idx, mapping, score
    return best_index, best_map


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    con = sqlite3.connect(args.database)
    records = con.execute(
        """SELECT id,error_code,message,cause,corrective_action,raw_text,
                  manual_id,pdf_page,table_index,row_index
           FROM error_records WHERE table_index > 0
           ORDER BY manual_id,pdf_page,table_index,row_index,id"""
    ).fetchall()
    issues: list[dict[str, Any]] = []
    table_cache: dict[tuple[str, int, int], tuple[list[list[Any]], int, dict[str, int]]] = {}
    aligned = 0
    tables_with_semantic_header: set[tuple[str, int, int]] = set()
    for rec in records:
        rid, code, message, cause, action, raw_text, manual, page, table_index, row_index = rec
        key = (str(manual), int(page), int(table_index))
        if key not in table_cache:
            row = con.execute(
                "SELECT rows_json FROM tables WHERE manual_id=? AND pdf_page=? AND table_index=?",
                key,
            ).fetchone()
            if not row:
                table_cache[key] = ([], -1, {})
            else:
                rows = json.loads(row[0] or "[]")
                hidx, hmap = best_header(rows)
                table_cache[key] = (rows, hidx, hmap)
        rows, header_index, header_map = table_cache[key]
        identity = {"id": int(rid), "manual_id": str(manual), "pdf_page": int(page), "table_index": int(table_index), "row_index": int(row_index), "error_code": str(code)}
        if not rows:
            issues.append({"severity":"error","code":"missing_source_table",**identity}); continue
        if header_map.get("code") is None:
            issues.append({"severity":"warning","code":"no_semantic_error_header", "header_index":header_index, "header_map":header_map, **identity}); continue
        tables_with_semantic_header.add(key)
        expected_index = int(row_index) - 1
        if expected_index < 0 or expected_index >= len(rows):
            issues.append({"severity":"error","code":"row_index_out_of_bounds","row_count":len(rows),**identity}); continue
        source_row = [norm(v) for v in rows[expected_index]]
        code_col = header_map["code"]
        source_code_cell = source_row[code_col] if code_col < len(source_row) else ""
        bare = str(code).upper().removesuffix("H")
        if bare not in code_tokens(source_code_cell):
            # Search the table to diagnose an offset rather than immediately
            # assuming the code itself is bad.
            matching_rows = []
            for idx, candidate in enumerate(rows):
                c = norm(candidate[code_col]) if code_col < len(candidate) else ""
                if bare in code_tokens(c):
                    matching_rows.append(idx)
            issues.append({
                "severity":"error", "code":"error_code_row_misaligned",
                "header_index":header_index, "header_map":header_map,
                "source_row":source_row, "source_code_cell":source_code_cell,
                "matching_row_indexes_zero_based":matching_rows,
                **identity,
            })
            continue
        aligned += 1
        for field, stored, kind in (("message",message,"message"),("cause",cause,"cause"),("corrective_action",action,"action")):
            col = header_map.get(kind)
            if col is None or col >= len(source_row):
                continue
            expected = source_row[col]
            if expected and norm(stored) and not cell_eq(stored, expected):
                issues.append({
                    "severity":"error", "code":"error_field_column_misaligned",
                    "field":field, "stored":norm(stored), "expected_cell":expected,
                    "header_index":header_index, "header_map":header_map,
                    "source_row":source_row, **identity,
                })
            elif expected and not norm(stored):
                issues.append({
                    "severity":"warning", "code":"structured_error_field_missing",
                    "field":field, "expected_cell":expected,
                    "header_index":header_index, "header_map":header_map,
                    **identity,
                })
        if norm(raw_text) and not all(fragment in norm(raw_text) for fragment in source_row if fragment):
            issues.append({"severity":"warning","code":"raw_text_not_full_source_row","source_row":source_row,**identity})
    con.close()
    severity = Counter(i["severity"] for i in issues)
    codes = Counter(i["code"] for i in issues)
    report = {
        "table_backed_records": len(records),
        "aligned_records": aligned,
        "semantic_header_tables": len(tables_with_semantic_header),
        "issue_severity_counts": dict(sorted(severity.items())),
        "issue_code_counts": dict(sorted(codes.items())),
        "issues": issues,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
