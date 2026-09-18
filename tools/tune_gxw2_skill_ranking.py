#!/usr/bin/env python3
"""Add narrow concept-routing metadata for gxw2-skill supporting chunks.

Phase 2 keeps gxw2-skill out of authoritative structured stores. This script
adds a small, idempotent ``skill_concept`` layer to ``entity_index`` so ST rules,
data-type guidance, and compatibility notes can enter the existing hybrid
candidate pool when a query names a relevant GX Works2 concept.

Only strong derived concepts are mirrored into the chunk ``entities`` search
field. Native importer entities are restored before each FTS5 rebuild
without changing chunk text, dense vectors, manual priority, or any Mitsubishi
official structured record. Dense embeddings therefore remain valid.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge.gxworks2_concepts import (
    CONCEPT_ROUTES, LEGACY_DERIVED_CONCEPTS, STRONG_CONCEPTS, TASK_SCOPE,
)


SOURCE_MANUAL_ID = "gxw2_skill_1_6_1"
ENTITY_TYPE = "skill_concept"


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=root / "resources" / "knowledge" / "fx3u_knowledge.sqlite",
    )
    parser.add_argument("--manual-id", default=SOURCE_MANUAL_ID)
    return parser.parse_args()


def _validate_schema(connection: sqlite3.Connection) -> None:
    required = {"chunks", "entity_index", "manuals", "chunks_fts", "meta"}
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(f"knowledge database missing: {', '.join(missing)}")


def _marker_matches(text: str, markers: tuple[str, ...]) -> bool:
    haystack = text.casefold()
    return any(marker.casefold() in haystack for marker in markers)


def _merge_entity_tokens(existing: str, concepts: set[str]) -> str:
    tokens = [token for token in str(existing or "").split() if token]
    seen = {token.casefold() for token in tokens}
    for concept in sorted(concepts):
        if concept.casefold() not in seen:
            tokens.append(concept)
            seen.add(concept.casefold())
    return " ".join(tokens)


def tune_database(database: Path, manual_id: str = SOURCE_MANUAL_ID) -> dict[str, int]:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(database)

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _validate_schema(connection)

        manual = connection.execute(
            "SELECT manual_type FROM manuals WHERE manual_id=?",
            (manual_id,),
        ).fetchone()
        if manual is None:
            raise RuntimeError(
                f"gxw2-skill source {manual_id!r} is not imported; "
                "run tools/import_gxw2_skill.py first"
            )
        if str(manual[0]) != "third_party_skill":
            raise RuntimeError(
                f"refusing to tune non-third-party source {manual_id!r}"
            )

        # Recover native importer entities before removing old derived rows.
        # entities_json is the immutable importer snapshot, so even a route
        # removed several versions ago cannot leave a stale lexical token.
        vocabulary_key = f"skill_concept_vocabulary:{manual_id}"
        vocabulary_row = connection.execute(
            "SELECT value FROM meta WHERE key=?", (vocabulary_key,),
        ).fetchone()
        vocabulary = set(LEGACY_DERIVED_CONCEPTS)
        vocabulary.update(concept for routes in CONCEPT_ROUTES.values() for concept in routes)
        if vocabulary_row:
            vocabulary.update(json.loads(vocabulary_row[0]))
        native_by_chunk: defaultdict[int, set[str]] = defaultdict(set)
        for chunk_id, entity, entity_type in connection.execute(
            "SELECT chunk_id,entity,entity_type FROM entity_index WHERE manual_id=?",
            (manual_id,),
        ):
            if entity_type == ENTITY_TYPE:
                vocabulary.add(str(entity))
            else:
                native_by_chunk[int(chunk_id)].add(str(entity))
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
        native_column = "entities_json" if "entities_json" in columns else "NULL"
        rows = connection.execute(
            f"SELECT id,chunk_type,text,plc_models,entities,{native_column} "
            "FROM chunks WHERE manual_id=? ORDER BY id", (manual_id,),
        ).fetchall()
        connection.execute(
            "DELETE FROM entity_index WHERE manual_id=? AND entity_type=?",
            (manual_id, ENTITY_TYPE),
        )

        inserted = 0
        routed_concepts: set[str] = set()
        per_type: defaultdict[str, int] = defaultdict(int)
        concepts_by_chunk: defaultdict[int, set[str]] = defaultdict(set)
        entities_by_chunk: dict[int, str] = {}

        derived_folded = {value.casefold() for value in vocabulary}
        for chunk_id, chunk_type, text, plc_models, existing_entities, native_json in rows:
            chunk_id = int(chunk_id)
            if native_json is not None:
                native = {str(item["entity"]) for item in json.loads(native_json)
                          if item.get("type") != ENTITY_TYPE}
            else:
                # Compatibility with older/minimal schemas: preserve native
                # index entries, strip the full historical injection vocabulary.
                native = {token for token in str(existing_entities or "").split()
                          if token.casefold() not in derived_folded}
                native.update(native_by_chunk[chunk_id])
            entities_by_chunk[chunk_id] = " ".join(sorted(native))
            routes = CONCEPT_ROUTES.get(str(chunk_type), {})
            for concept, markers in routes.items():
                if not _marker_matches(str(text or ""), markers):
                    continue
                connection.execute(
                    """
                    INSERT OR REPLACE INTO entity_index(
                        entity_norm,entity,entity_type,plc_models,task_types,
                        manual_id,chunk_id,occurrences
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        concept.casefold(),
                        concept,
                        ENTITY_TYPE,
                        str(plc_models or ""),
                        TASK_SCOPE[str(chunk_type)],
                        manual_id,
                        chunk_id,
                        1,
                    ),
                )
                inserted += 1
                routed_concepts.add(concept)
                per_type[str(chunk_type)] += 1
                concepts_by_chunk[chunk_id].add(concept)

        # Rebuild every source chunk, including chunks which lost all routes.
        # Weak qualified routes stay in entity_index only: FTS tokenization
        # would split a namespace and reintroduce bare PROGRAM/COMMENT words.
        for chunk_id, native_entities in entities_by_chunk.items():
            concepts = concepts_by_chunk[chunk_id].intersection(STRONG_CONCEPTS)
            merged = _merge_entity_tokens(native_entities, concepts)
            connection.execute("UPDATE chunks SET entities=? WHERE id=?", (merged, chunk_id))
        fts_chunks = sum(bool(concepts) for concepts in concepts_by_chunk.values())
        connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (vocabulary_key, json.dumps(sorted(vocabulary))),
        )

        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("external_source_gxw2_skill_routing", "scoped_concepts_native_entities_v3"),
        )
        connection.commit()

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")

    return {
        "entities": inserted,
        "concepts": len(routed_concepts),
        "fts_chunks": fts_chunks,
        "st_rule": per_type["st_rule"],
        "data_type": per_type["data_type"],
        "compatibility": per_type["compatibility"],
    }


def main() -> int:
    args = parse_args()
    stats = tune_database(args.database, args.manual_id)
    print(
        "gxw2-skill ranking routes added: "
        f"entities={stats['entities']} concepts={stats['concepts']} "
        f"fts_chunks={stats['fts_chunks']} st_rule={stats['st_rule']} "
        f"data_type={stats['data_type']} compatibility={stats['compatibility']}"
    )
    print("FTS5 rebuilt; dense embeddings remain valid and do not need rebuilding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
