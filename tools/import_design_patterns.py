#!/usr/bin/env python3
# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Import curated PLC control-architecture knowledge into the bundled SQLite index."""

from __future__ import annotations

import sys as _private_sys
from pathlib import Path as _PrivatePath
_private_sys.path.insert(0, str(_PrivatePath(__file__).resolve().parents[1]))
from tools.offline_inputs import private_path

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
ASCII_ENTITY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]{1,63}$")


def parse_args(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=private_path('resources/knowledge/design_patterns.json'),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=private_path('resources/knowledge/fx3u_knowledge.sqlite'),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=private_path('resources/knowledge/manifest.json'),
    )
    return parser.parse_args(argv)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cjk_bigrams(text: str) -> str:
    terms = []
    for match in CJK_RUN_RE.finditer(text):
        run = match.group(0)
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(terms)


def source_scope(values) -> str:
    return ",".join(str(value).strip() for value in values if str(value).strip())


def pattern_text(pattern: dict) -> str:
    keywords = "、".join(str(value) for value in pattern.get("keywords", []) if str(value).strip())
    entities = ", ".join(str(value) for value in pattern.get("entities", []) if str(value).strip())
    return "\n".join(
        part
        for part in (
            f"CONTROL ARCHITECTURE: {pattern['title']}",
            f"PATTERN_ID: {pattern['id']}",
            str(pattern.get("text") or "").strip(),
            f"检索关键词：{keywords}" if keywords else "",
            f"架构标识：{entities}" if entities else "",
        )
        if part
    )


def mark_dense_stale(connection: sqlite3.Connection) -> None:
    for key, value in {
        "vector_status": "stale",
        "vector_corpus_sha256": "",
    }.items():
        connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (key, value),
        )


def update_manifest(manifest_path: Path, database: Path, source_path: Path, source: dict, chunks: int) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = {
        "id": str(source["id"]),
        "title": str(source["title"]),
        "type": str(source.get("manual_type") or "curated_design"),
        "role": "analysis_support",
        "priority": int(source.get("priority", 90)),
        "plc_models": list(source.get("plc_models") or []),
        "task_types": list(source.get("task_types") or ["analysis"]),
        "chunks": int(chunks),
        "source_file": source_path.name,
        "source_sha256": sha256_file(source_path),
    }
    curated = [
        item
        for item in manifest.get("curated_sources", [])
        if item.get("id") != entry["id"]
    ]
    curated.append(entry)
    curated.sort(key=lambda item: str(item.get("id", "")))
    manifest["curated_sources"] = curated
    manifest.setdefault("stats", {})["design_chunks"] = int(chunks)
    manifest["database_bytes"] = database.stat().st_size
    manifest["database_sha256"] = sha256_file(database)
    retrieval = manifest.setdefault("retrieval", {})
    retrieval["dense_embeddings"] = False
    retrieval["vector_status"] = "stale_after_curated_design_import"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    source_path = args.source.expanduser().resolve()
    database = args.database.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    if not source_path.is_file() or not database.is_file() or not manifest_path.is_file():
        raise SystemExit("design source, knowledge database and manifest must exist")

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise SystemExit("unsupported design-pattern schema")
    source = dict(payload.get("source") or {})
    patterns = list(payload.get("patterns") or [])
    if not source.get("id") or not source.get("title") or not patterns:
        raise SystemExit("design-pattern source metadata or patterns are missing")

    manual_id = str(source["id"])
    title = str(source["title"])
    language = str(source.get("language") or "zh")
    manual_type = str(source.get("manual_type") or "curated_design")
    priority = int(source.get("priority", 90))
    plc_models = source_scope(source.get("plc_models") or ["FX3U"])
    task_types = source_scope(source.get("task_types") or ["analysis"])
    source_bytes = source_path.stat().st_size
    source_sha = sha256_file(source_path)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        required_tables = {"manuals", "chunks", "entity_index", "chunks_fts", "meta"}
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        if not required_tables.issubset(existing_tables):
            raise SystemExit("knowledge database schema is incompatible")

        # Idempotent replacement. Cascades remove the previous curated chunks
        # and their entity/vector rows without touching official manual data.
        connection.execute("DELETE FROM manuals WHERE manual_id=?", (manual_id,))
        connection.execute(
            """
            INSERT INTO manuals(
                manual_id,manual_number,revision,published,title,language,manual_type,
                plc_models,task_types,priority,source_file,source_bytes,source_sha256,
                official_url,pdf_pages
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                manual_id,
                "CURATED-DESIGN",
                str(payload.get("schema_version", 1)),
                "2026-09-11",
                title,
                language,
                manual_type,
                plc_models,
                task_types,
                priority,
                source_path.name,
                source_bytes,
                source_sha,
                "",
                0,
            ),
        )

        inserted = 0
        seen_ids = set()
        for block_index, pattern in enumerate(patterns, start=1):
            pattern_id = str(pattern.get("id") or "").strip()
            pattern_title = str(pattern.get("title") or "").strip()
            if not pattern_id or not pattern_title or pattern_id in seen_ids:
                raise SystemExit(f"invalid or duplicate design pattern id: {pattern_id!r}")
            seen_ids.add(pattern_id)
            text = pattern_text(pattern)
            encoded = text.encode("utf-8")
            text_sha = sha256_bytes(encoded)
            explicit_entities = [
                str(value).strip()
                for value in pattern.get("entities", [])
                if ASCII_ENTITY_RE.fullmatch(str(value).strip())
            ]
            entity_counts = Counter(value for value in explicit_entities)
            entities_json = [
                {
                    "entity": entity,
                    "type": "design_concept",
                    "occurrences": int(count),
                }
                for entity, count in sorted(entity_counts.items(), key=lambda item: item[0].casefold())
            ]
            cursor = connection.execute(
                """
                INSERT INTO chunks(
                    chunk_uid,manual_id,manual_number,revision,manual_title,manual_type,
                    manual_priority,language,plc_models,task_types,source_file,pdf_page,
                    pdf_page_end,printed_page,printed_page_end,chapter,section,outline_path,
                    section_key,chunk_type,instruction_opcode,fnc_number,block_index,
                    block_count,source_pages_json,page_char_count,page_text_sha256,text,
                    char_count,text_sha256,entities,entities_json,cjk_bigrams,fidelity_flags
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"design:{pattern_id}",
                    manual_id,
                    "CURATED-DESIGN",
                    "1",
                    title,
                    manual_type,
                    priority,
                    language,
                    plc_models,
                    task_types,
                    source_path.name,
                    0,
                    0,
                    "",
                    "",
                    "Control Architecture Design",
                    pattern_title,
                    f"Control Architecture Design > {pattern_title}",
                    f"design:{pattern_id}",
                    "design_pattern",
                    "",
                    "",
                    block_index,
                    len(patterns),
                    "[]",
                    len(text),
                    text_sha,
                    text,
                    len(text),
                    text_sha,
                    " ".join(sorted(entity_counts, key=str.casefold)),
                    json.dumps(entities_json, ensure_ascii=False, separators=(",", ":")),
                    cjk_bigrams(text),
                    "curated,design,analysis",
                ),
            )
            chunk_id = int(cursor.lastrowid)
            for entity, count in entity_counts.items():
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
                        "design_concept",
                        plc_models,
                        task_types,
                        manual_id,
                        chunk_id,
                        int(count),
                    ),
                )
            inserted += 1

        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        mark_dense_stale(connection)
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("knowledge database integrity check failed")
        actual = int(
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE manual_id=?",
                (manual_id,),
            ).fetchone()[0]
        )
        fts_count = int(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
        chunk_count = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        if actual != inserted or fts_count != chunk_count:
            raise SystemExit("curated design import verification failed")

    update_manifest(manifest_path, database, source_path, source, inserted)
    print(
        json.dumps(
            {
                "manual_id": manual_id,
                "chunks": inserted,
                "database_sha256": sha256_file(database),
                "dense_status": "stale",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
