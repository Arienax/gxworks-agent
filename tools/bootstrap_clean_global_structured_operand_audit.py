#!/usr/bin/env python3
"""One-shot cleanup of structured operand rows flagged by the generic auditor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
MANIFEST = ROOT / "resources" / "knowledge" / "manifest.json"
BUILDER_PATH = ROOT / "tools" / "build_fx3u_knowledge_v3.py"
AUDIT_PATH = ROOT / "tools" / "audit_structured_instruction_backfill.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
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


def main() -> int:
    builder = load_module("fx_builder_global_cleanup", BUILDER_PATH)
    audit = load_module("operand_audit_global_cleanup", AUDIT_PATH)

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id,manual_id,opcode,opcode_norm,source_pages_json,operands_json,restrictions_json,chunk_id,title FROM instructions ORDER BY id"
    ).fetchall()

    selected = []
    for raw in rows:
        item = dict(raw)
        item["source_pages_json"] = json.loads(item["source_pages_json"] or "[]")
        item["operands_json"] = json.loads(item["operands_json"] or "[]")
        item["restrictions_json"] = json.loads(item["restrictions_json"] or "[]")
        if not item["operands_json"]:
            continue
        findings = audit.audit_record(connection, item)
        actionable = [
            finding for finding in findings
            if finding["severity"] == "error" or finding["code"] == "operand_has_no_semantics"
        ]
        if actionable:
            selected.append((item, actionable))

    repaired = []
    unresolved = []
    for row, before_findings in selected:
        pages = page_objects(connection, row["manual_id"], row["source_pages_json"])
        new_operands = builder.parse_operand_schema(pages, row["opcode"])
        probe = dict(row)
        probe["operands_json"] = new_operands
        after_findings = audit.audit_record(connection, probe)
        actionable_after = [
            finding for finding in after_findings
            if finding["severity"] == "error" or finding["code"] == "operand_has_no_semantics"
        ]
        if actionable_after:
            unresolved.append({
                "manual_id": row["manual_id"],
                "opcode": row["opcode"],
                "before": before_findings,
                "after": actionable_after,
                "reparsed": new_operands,
            })
            continue
        connection.execute(
            "UPDATE instructions SET operands_json=? WHERE id=?",
            (json.dumps(new_operands, ensure_ascii=False, separators=(",", ":")), row["id"]),
        )
        repaired.append({
            "manual_id": row["manual_id"],
            "opcode": row["opcode"],
            "before": before_findings,
            "reparsed": new_operands,
        })

    connection.commit()
    connection.close()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["database_bytes"] = DB.stat().st_size
    manifest["database_sha256"] = hashlib.sha256(DB.read_bytes()).hexdigest()
    manifest["structured_instruction_global_cleanup"] = {
        "method": "generic auditor-selected reparse from authoritative page tables",
        "selected_rows": len(selected),
        "repaired_rows": len(repaired),
        "unresolved_rows": len(unresolved),
        "repaired": [
            {"manual_id": item["manual_id"], "opcode": item["opcode"]}
            for item in repaired
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"repaired": repaired, "unresolved": unresolved}, ensure_ascii=False, indent=2))
    if unresolved:
        raise SystemExit(f"{len(unresolved)} structured operand rows remain unresolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
