#!/usr/bin/env python3
"""Reclassify device-like entity false positives in an existing schema-v3 DB.

This is the migration companion to ``build_fx3u_knowledge.py``.  It is useful
when the original source PDFs are not present locally: chunk text already stored
in SQLite contains enough evidence to reclassify the known semantic cases.

The migration is semantic and idempotent.  It does not contain a blacklist of
specific addresses.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from device_entity_cleanup import sanitize_device_like_entities

DEVICE_FAMILIES = {
    "X": "Input relay",
    "Y": "Output relay",
    "M": "Auxiliary relay",
    "S": "State relay",
    "T": "Timer",
    "C": "Counter",
    "D": "Data register",
    "R": "Extension register or file register",
    "ER": "Extension file register",
    "V": "Index register",
    "Z": "Index register",
    "P": "Branch pointer",
    "I": "Interrupt pointer",
}


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _entity_counter(connection: sqlite3.Connection, chunk_id: int) -> Counter[tuple[str, str]]:
    return Counter(
        {
            (str(entity), str(entity_type)): int(occurrences)
            for entity, entity_type, occurrences in connection.execute(
                "SELECT entity,entity_type,occurrences FROM entity_index WHERE chunk_id=?",
                (chunk_id,),
            )
        }
    )


def _chunk_metadata(connection: sqlite3.Connection, chunk_id: int) -> tuple[str, str, str]:
    row = connection.execute(
        "SELECT manual_id,plc_models,task_types FROM chunks WHERE id=?",
        (chunk_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing chunk {chunk_id}")
    return str(row[0]), str(row[1]), str(row[2])


def _rewrite_changed_entities(
    connection: sqlite3.Connection,
    chunk_id: int,
    before: Counter[tuple[str, str]],
    after: Counter[tuple[str, str]],
) -> int:
    changed = 0
    manual_id, plc_models, task_types = _chunk_metadata(connection, chunk_id)
    for key in sorted(set(before) | set(after)):
        old_count = int(before.get(key, 0))
        new_count = int(after.get(key, 0))
        if old_count == new_count:
            continue
        entity, entity_type = key
        connection.execute(
            "DELETE FROM entity_index WHERE chunk_id=? AND entity=? AND entity_type=?",
            (chunk_id, entity, entity_type),
        )
        if new_count > 0:
            connection.execute(
                """
                INSERT INTO entity_index(
                    entity_norm,entity,entity_type,plc_models,task_types,
                    manual_id,chunk_id,occurrences
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    entity.casefold(),
                    entity,
                    entity_type,
                    plc_models,
                    task_types,
                    manual_id,
                    chunk_id,
                    new_count,
                ),
            )
        changed += 1
    return changed


def _refresh_chunk_entity_cache(connection: sqlite3.Connection, chunk_id: int) -> None:
    rows = connection.execute(
        "SELECT entity,entity_type,occurrences FROM entity_index WHERE chunk_id=? "
        "ORDER BY entity_norm,entity_type",
        (chunk_id,),
    ).fetchall()
    tokens = sorted({str(entity) for entity, _kind, _count in rows})
    payload = [
        {"entity": str(entity), "type": str(kind), "occurrences": int(count)}
        for entity, kind, count in rows
    ]
    connection.execute(
        "UPDATE chunks SET entities=?,entities_json=? WHERE id=?",
        (
            " ".join(tokens),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            chunk_id,
        ),
    )


def _rebuild_device_records(connection: sqlite3.Connection) -> int:
    connection.execute("DELETE FROM device_records")
    rows = connection.execute(
        """
        SELECT e.entity_norm, MIN(e.entity), e.entity_type, SUM(e.occurrences),
               MIN(e.chunk_id), GROUP_CONCAT(DISTINCT e.manual_id), MIN(c.section)
        FROM entity_index e
        JOIN chunks c ON c.id=e.chunk_id
        WHERE e.entity_type IN ('device','device_range')
        GROUP BY e.entity_norm,e.entity_type
        ORDER BY e.entity_norm
        """
    ).fetchall()
    inserted = 0
    for entity_norm, entity, entity_type, occurrences, chunk_id, manuals, section in rows:
        prefix_match = re.match(r"(?:ER|SM|SD|TS|TC|CS|CC|[A-Z]+)", str(entity), re.I)
        prefix = prefix_match.group(0).upper() if prefix_match else ""
        description = normalize(section or DEVICE_FAMILIES.get(prefix, ""))
        source_manuals = sorted(set(str(manuals or "").split(",")))
        connection.execute(
            """
            INSERT INTO device_records(
                device_norm,device,prefix,record_type,description,occurrences,
                plc_models,source_manuals_json,chunk_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                entity_norm,
                entity,
                prefix,
                entity_type,
                description,
                int(occurrences),
                "FX3S,FX3G,FX3GC,FX3U,FX3UC",
                json.dumps(source_manuals, separators=(",", ":")),
                chunk_id,
            ),
        )
        inserted += 1

    for prefix, description in DEVICE_FAMILIES.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO device_records(
                device_norm,device,prefix,record_type,description,occurrences,
                plc_models,source_manuals_json,chunk_id
            ) VALUES(?,?,?,?,?,0,?,?,NULL)
            """,
            (
                f"family:{prefix.casefold()}",
                prefix,
                prefix,
                "device_family",
                description,
                "FX3S,FX3G,FX3GC,FX3U,FX3UC",
                "[]",
            ),
        )
        inserted += 1
    return inserted


def migrate(connection: sqlite3.Connection) -> dict[str, Any]:
    affected_chunks = 0
    changed_entity_rows = 0
    change_log: list[dict[str, Any]] = []

    candidate_chunk_ids = [
        int(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT e.chunk_id
            FROM entity_index e
            WHERE e.entity_type IN ('device','device_range')
              AND (UPPER(e.entity) GLOB 'N[0-9]*' OR UPPER(e.entity) GLOB 'D[0-9]*')
            ORDER BY e.chunk_id
            """
        )
    ]

    for chunk_id in candidate_chunk_ids:
        row = connection.execute(
            "SELECT text,chunk_type,instruction_opcode FROM chunks WHERE id=?",
            (chunk_id,),
        ).fetchone()
        if not row:
            continue
        text, chunk_type, opcode = str(row[0] or ""), str(row[1] or ""), str(row[2] or "")
        before = _entity_counter(connection, chunk_id)
        after = sanitize_device_like_entities(text, chunk_type, before)
        if before == after:
            continue
        changed = _rewrite_changed_entities(connection, chunk_id, before, after)
        _refresh_chunk_entity_cache(connection, chunk_id)
        affected_chunks += 1
        changed_entity_rows += changed
        diff = []
        for key in sorted(set(before) | set(after)):
            if before.get(key, 0) != after.get(key, 0):
                diff.append(
                    {
                        "entity": key[0],
                        "type": key[1],
                        "before": int(before.get(key, 0)),
                        "after": int(after.get(key, 0)),
                    }
                )
        change_log.append(
            {"chunk_id": chunk_id, "opcode": opcode, "changes": diff}
        )

    device_records = _rebuild_device_records(connection)
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    fk = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity.lower() != "ok" or fk:
        raise RuntimeError(f"post-migration integrity={integrity} foreign_keys={fk[:5]}")

    return {
        "affected_chunks": affected_chunks,
        "changed_entity_rows": changed_entity_rows,
        "device_records": device_records,
        "integrity_check": integrity,
        "foreign_key_violations": len(fk),
        "changes": change_log,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    database = args.database.expanduser().resolve()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        report = migrate(connection)
        connection.commit()
    if args.report:
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({k: v for k, v in report.items() if k != "changes"}, ensure_ascii=False, sort_keys=True))
    for item in report["changes"]:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
