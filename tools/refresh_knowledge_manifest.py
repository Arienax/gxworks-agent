#!/usr/bin/env python3
"""Refresh manifest fields derived directly from the schema-v3 SQLite database.

This intentionally preserves source-manual metadata, dense-model metadata and
published benchmark results unless a caller explicitly updates those elsewhere.
It is intended for semantic/entity migrations that do not rebuild source chunks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--migration-name", default="device_entity_semantic_cleanup_v1")
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with sqlite3.connect(database) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity.lower() != "ok" or fk:
            raise RuntimeError(f"database integrity={integrity} foreign_keys={fk[:5]}")

        counts = {
            table: table_count(connection, table)
            for table in (
                "manuals",
                "page_artifacts",
                "chunks",
                "chunks_fts",
                "entity_index",
                "instructions",
                "instruction_aliases",
                "device_records",
                "error_records",
                "debug_cases",
                "vector_embeddings",
            )
        }

        table_rows = table_count(connection, "table_records") if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='table_records'"
        ).fetchone() else int(manifest.get("stats", {}).get("tables", 0))

    manifest["database_bytes"] = database.stat().st_size
    manifest["database_sha256"] = sha256_file(database)

    manifest.setdefault("stats", {})["entities"] = counts["entity_index"]
    manifest["stats"]["manual_chunks"] = counts["chunks"] - int(
        manifest.get("external_sources", [{}])[0].get("chunks", 0)
        if manifest.get("external_sources") else 0
    ) - int(manifest.get("stats", {}).get("design_chunks", 0))
    manifest["stats"]["tables"] = table_rows

    manifest.setdefault("structured", {})["devices"] = counts["device_records"]
    manifest["structured"]["instructions"] = counts["instructions"]
    manifest["structured"]["errors"] = counts["error_records"]
    manifest["structured"]["debug_cases"] = counts["debug_cases"]

    verification = manifest.setdefault("verification", {})
    verification["counts"] = counts
    verification["integrity_check"] = integrity
    verification["foreign_key_violations"] = len(fk)

    migrations = manifest.setdefault("migrations", [])
    entry = {
        "name": str(args.migration_name),
        "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_sha256": manifest["database_sha256"],
        "device_records": counts["device_records"],
        "entity_index": counts["entity_index"],
    }
    # Replace a previous record for the same migration instead of accumulating
    # duplicate metadata when validating or rerunning the same branch migration.
    migrations[:] = [item for item in migrations if item.get("name") != entry["name"]]
    migrations.append(entry)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "database_bytes": manifest["database_bytes"],
        "database_sha256": manifest["database_sha256"],
        "counts": counts,
        "integrity_check": integrity,
        "foreign_key_violations": len(fk),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
