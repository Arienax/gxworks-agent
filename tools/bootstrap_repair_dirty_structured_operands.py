#!/usr/bin/env python3
"""One-shot generic cleanup for malformed structured instruction operand rows."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
BUILDER = ROOT / "tools" / "build_fx3u_knowledge_v3.py"
MANIFEST = ROOT / "resources" / "knowledge" / "manifest.json"


def dirty_reasons(operands):
    reasons = []
    positions = []
    for index, item in enumerate(operands if isinstance(operands, list) else []):
        if not isinstance(item, dict):
            reasons.append((index, "operand_not_object"))
            continue
        position = str(item.get("position") or "").strip().upper()
        description = str(item.get("description") or "").strip()
        positions.append(position)
        if position == "X":
            reasons.append((index, "device_header_as_operand"))
        if description.casefold() in {"<blank>", "blank"}:
            reasons.append((index, "blank_description"))
        if description and re.fullmatch(
            r"(?:(?:\[GLYPH-[0-9A-F]+\]|\(cid:\d+\))\d*\s*)+",
            description,
            flags=re.I,
        ):
            reasons.append((index, "glyph_only_description"))
        if description and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", description) and len(description) <= 3:
            reasons.append((index, "table_token_description"))
    duplicates = {value for value in positions if value and positions.count(value) > 1}
    if duplicates:
        reasons.append((-1, "duplicate_positions:" + ",".join(sorted(duplicates))))
    return reasons


def load_builder():
    spec = importlib.util.spec_from_file_location("fx_builder_dirty_cleanup", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def page_objects(connection, manual_id, source_pages):
    pages = []
    for page_number in source_pages[:4]:
        page = connection.execute(
            "SELECT clean_text,layout_text,compact_layout FROM page_artifacts WHERE manual_id=? AND pdf_page=?",
            (manual_id, page_number),
        ).fetchone()
        if page is None:
            continue
        tables = []
        for table in connection.execute(
            "SELECT table_index,rows_json,table_text FROM tables WHERE manual_id=? AND pdf_page=? ORDER BY table_index",
            (manual_id, page_number),
        ).fetchall():
            tables.append({
                "index": table[0],
                "rows": json.loads(table[1] or "[]"),
                "text": table[2] or "",
            })
        pages.append(SimpleNamespace(
            clean_text=page[0] or "",
            layout_text=page[1] or "",
            compact_layout=page[2] or "",
            tables=tables,
        ))
    return pages


def main():
    builder = load_builder()
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id,manual_id,opcode,source_pages_json,operands_json FROM instructions ORDER BY id"
    ).fetchall()

    dirty_before = []
    repaired = []
    unresolved = []
    for row in rows:
        old = json.loads(row["operands_json"] or "[]")
        reasons = dirty_reasons(old)
        if not reasons:
            continue
        dirty_before.append({
            "id": row["id"], "manual_id": row["manual_id"], "opcode": row["opcode"],
            "reasons": reasons, "old": old,
        })
        pages = page_objects(
            connection,
            str(row["manual_id"]),
            json.loads(row["source_pages_json"] or "[]"),
        )
        new = builder.parse_operand_schema(pages, str(row["opcode"]))
        new_reasons = dirty_reasons(new)
        if new_reasons:
            unresolved.append({
                "id": row["id"], "manual_id": row["manual_id"], "opcode": row["opcode"],
                "reasons": new_reasons, "new": new,
            })
            continue
        connection.execute(
            "UPDATE instructions SET operands_json=? WHERE id=?",
            (json.dumps(new, ensure_ascii=False, separators=(",", ":")), row["id"]),
        )
        repaired.append({
            "id": row["id"], "manual_id": row["manual_id"], "opcode": row["opcode"],
            "old_reasons": reasons, "new": new,
        })

    connection.commit()
    connection.close()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["database_bytes"] = DB.stat().st_size
    manifest["database_sha256"] = hashlib.sha256(DB.read_bytes()).hexdigest()
    manifest["structured_instruction_dirty_cleanup"] = {
        "method": "generic reparse of malformed structured operand rows from authoritative page tables",
        "dirty_rows_before": len(dirty_before),
        "repaired_rows": len(repaired),
        "unresolved_rows": len(unresolved),
        "repaired": [
            {"manual_id": item["manual_id"], "opcode": item["opcode"]}
            for item in repaired
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "dirty_rows_before": dirty_before,
        "repaired": repaired,
        "unresolved": unresolved,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if unresolved:
        raise SystemExit(f"{len(unresolved)} dirty structured operand rows remain unresolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
