from pathlib import Path
import json
import sqlite3

import pytest
import knowledge.retriever as retriever
from knowledge.gxworks2_concepts import query_skill_concepts
from tools import tune_gxw2_skill_ranking as tuner
from tools.tune_gxw2_skill_ranking import tune_database


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
            CREATE TABLE manuals (
                manual_id TEXT PRIMARY KEY,
                manual_type TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
                chunk_type TEXT NOT NULL,
                text TEXT NOT NULL,
                plc_models TEXT NOT NULL,
                entities TEXT NOT NULL DEFAULT '',
                entities_json TEXT
            );
            CREATE TABLE entity_index (
                entity_norm TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                plc_models TEXT NOT NULL,
                task_types TEXT NOT NULL,
                manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
                chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                occurrences INTEGER NOT NULL,
                PRIMARY KEY(entity_norm, entity_type, chunk_id)
            ) WITHOUT ROWID;
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                text,
                entities,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 0'
            );
            """
        )
        connection.execute(
            "INSERT INTO manuals(manual_id,manual_type) VALUES(?,?)",
            ("gxw2_skill_1_6_1", "third_party_skill"),
        )
        connection.execute(
            "INSERT INTO manuals(manual_id,manual_type) VALUES(?,?)",
            ("official", "programming"),
        )
        connection.executemany(
            "INSERT INTO chunks(id,manual_id,chunk_type,text,plc_models,entities) VALUES(?,?,?,?,?,?)",
            [
                (
                    1,
                    "gxw2_skill_1_6_1",
                    "st_rule",
                    "No CONTINUE. Comment Style uses block comments. VAR_IN_OUT is unsupported. 3-Program Structure uses PRG_MAIN. FB instances are declared separately.",
                    "FX3U,FX3G,FX3S",
                    "",
                ),
                (
                    2,
                    "gxw2_skill_1_6_1",
                    "data_type",
                    "Unsupported Types include LREAL and WSTRING. Memory Consumption: DINT DWORD REAL.",
                    "FX3U,FX3G,FX3S",
                    "",
                ),
                (
                    3,
                    "gxw2_skill_1_6_1",
                    "compatibility",
                    "Feature Matrix STRING. Device Ranges FX3S. GX Works 2 vs GX Works 3.",
                    "FX3U,FX3G,FX3S",
                    "",
                ),
                (
                    4,
                    "official",
                    "instruction",
                    "CONTINUE STRING FX3S GX Works 3",
                    "FX3U",
                    "MOV",
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO entity_index(
                entity_norm,entity,entity_type,plc_models,task_types,
                manual_id,chunk_id,occurrences
            ) VALUES('mov','MOV','instruction','FX3U','*','official',4,1)
            """
        )
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        connection.commit()


def test_tune_database_adds_scoped_supporting_concepts_and_is_idempotent(tmp_path):
    database = tmp_path / "knowledge.sqlite"
    _create_database(database)

    first = tune_database(database)
    second = tune_database(database)

    assert first == second
    assert first["entities"] > 0
    assert first["fts_chunks"] == 3
    assert first["st_rule"] > 0
    assert first["data_type"] > 0
    assert first["compatibility"] > 0

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT entity_norm,chunk_id,task_types
            FROM entity_index
            WHERE manual_id='gxw2_skill_1_6_1' AND entity_type='skill_concept'
            ORDER BY entity_norm,chunk_id
            """
        ).fetchall()
        assert ("continue", 1, "st,generate,edit") in rows
        assert ("gxw2_program_structure", 1, "st,generate,edit") in rows
        assert ("lreal", 2, "st,generate,edit,analysis") in rows
        assert ("fx3s", 3, "st,generate,analysis") in rows
        assert ("works3", 3, "st,generate,analysis") in rows

        # Derived concepts are also mirrored into the FTS lexical metadata.
        st_entities = connection.execute(
            "SELECT entities FROM chunks WHERE id=1"
        ).fetchone()[0]
        assert "CONTINUE" in st_entities.split()
        assert "PROGRAM" not in st_entities.split()
        assert "GXW2_PROGRAM_STRUCTURE" not in st_entities.split()
        assert not any(row[0] == "program" for row in rows)

        # The derived routing layer must not mutate official structured evidence.
        official = connection.execute(
            "SELECT entity,entity_type FROM entity_index WHERE manual_id='official'"
        ).fetchall()
        assert official == [("MOV", "instruction")]
        assert connection.execute(
            "SELECT entities FROM chunks WHERE id=4"
        ).fetchone()[0] == "MOV"

        assert connection.execute(
            "SELECT value FROM meta WHERE key='external_source_gxw2_skill_routing'"
        ).fetchone()[0] == "scoped_concepts_native_entities_v3"


def test_tuner_removes_legacy_orphan_tokens_and_preserves_native_snapshot(tmp_path, monkeypatch):
    database = tmp_path / "knowledge.sqlite"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE chunks SET entities=?, entities_json=? WHERE id=1",
            ("PROGRAM TEXT COMMENT REMOVED_CONCEPT RS", json.dumps([
                {"entity": "RS", "type": "instruction", "occurrences": 1},
            ])),
        )
        original = connection.execute("SELECT id,text,entities_json FROM chunks ORDER BY id").fetchall()
    tune_database(database)
    # Even after every route is removed, no derived tokens survive in any
    # formerly routed chunk, including tokens with no old entity_index row.
    monkeypatch.setattr(tuner, "CONCEPT_ROUTES", {})
    tune_database(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT entities FROM chunks WHERE id=1").fetchone()[0] == "RS"
        assert connection.execute("SELECT entities FROM chunks WHERE id IN (2,3)").fetchall() == [("",), ("",)]
        assert connection.execute("SELECT COUNT(*) FROM entity_index WHERE entity_type='skill_concept'").fetchone()[0] == 0
        assert connection.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'REMOVED_CONCEPT'").fetchall() == []
        assert connection.execute("SELECT id,text,entities_json FROM chunks ORDER BY id").fetchall() == original
        first_rows = connection.execute("SELECT id,entities FROM chunks ORDER BY id").fetchall()
    tune_database(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id,entities FROM chunks ORDER BY id").fetchall() == first_rows


def test_legacy_schema_cleanup_preserves_native_collision_and_removed_route(tmp_path, monkeypatch):
    database = tmp_path / "knowledge.sqlite"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE chunks SET entities='PROGRAM RS KEEP' WHERE id=1")
        connection.execute("INSERT INTO entity_index VALUES('rs','RS','instruction','FX3U','*','gxw2_skill_1_6_1',1,1)")
    tune_database(database)
    monkeypatch.setattr(tuner, "CONCEPT_ROUTES", {})
    tune_database(database)
    with sqlite3.connect(database) as connection:
        assert set(connection.execute("SELECT entities FROM chunks WHERE id=1").fetchone()[0].split()) == {"RS", "KEEP"}


def test_tuner_refuses_official_source(tmp_path):
    database = tmp_path / "knowledge.sqlite"
    _create_database(database)
    before = database.read_bytes()
    with pytest.raises(RuntimeError, match="non-third-party"):
        tune_database(database, "official")
    assert database.read_bytes() == before



def test_weak_concepts_do_not_expand_generic_requests():
    assert retriever._query_has_gxw2_skill_concept("please revise this program") is False
    assert retriever._query_has_gxw2_skill_concept("check the output") is False
    assert retriever._query_has_gxw2_skill_concept("memory usage") is False


def test_weak_concepts_expand_when_gxworks_context_is_explicit():
    assert retriever._query_has_gxw2_skill_concept("FX3U GX Works2 ST program structure") is True
    assert retriever._query_has_gxw2_skill_concept("GX Works2 STRING support") is True


def test_strong_skill_concepts_expand_without_generic_context():
    assert retriever._query_has_gxw2_skill_concept("VAR_IN_OUT supported?") is True
    assert retriever._query_has_gxw2_skill_concept("INT_TO_REAL_E return value") is True


@pytest.mark.parametrize("query", [
    "please revise this program", "check the output", "memory usage",
    "new/delete a comment", "real time text processing", "case range label instance",
])
def test_generic_queries_have_no_derived_route(query):
    assert query_skill_concepts(query, "edit") == []


@pytest.mark.parametrize(("query", "concept"), [
    ("GX Works2 ST 注释应该用 // 吗", "GXW2_COMMENT_STYLE"),
    ("GX Works2 ST CASE 范围标签", "GXW2_CASE_LABELS"),
    ("GX Works2 FB、FUN 文件命名规则", "GXW2_POU_NAMING"),
    ("FX3G Structured Text STRING support", "GXW2_STRING_SUPPORT"),
    ("GX Works2 ST SR/RS 双稳态功能块", "GXW2_SR_RS"),
    ("GX Works2 ST __NEW", "__NEW"),
    ("GX Works2 ST __DELETE", "__DELETE"),
])
def test_qualified_routes_recognize_topics_and_preserve_identifiers(query, concept):
    assert concept in query_skill_concepts(query, "st")
    assert query_skill_concepts(query, "debug") == []


def test_rs_serial_instruction_and_structured_text_alone_do_not_route_to_rules():
    assert "GXW2_SR_RS" not in query_skill_concepts("FX3U RS serial communication", "st")
    assert query_skill_concepts("GX Works2 Structured Text", "st") == []
    assert "GXW2_POU_NAMING" not in query_skill_concepts("GX Works2 ST CASE 命名状态标签", "st")


def test_skill_concept_expands_recall_without_post_rrf_score_mutation(monkeypatch):
    calls = []

    def retrieve(query, **kwargs):
        calls.append(kwargs)
        return [{
            "id": "rule",
            "manual_type": "third_party_skill",
            "chunk_type": "st_rule",
            "retrieval_signals": ["entity", "vector"],
            "matched_entity": "CONTINUE",
            "score": 0.03125,
            "text": "Use IF/ELSE",
        }]

    monkeypatch.setattr(retriever._core, "_retrieve_knowledge", retrieve)
    result = retriever.retrieve_knowledge(
        "CONTINUE", task_type="st", top_k=5, char_budget=1000,
    )
    assert calls[0]["top_k"] == 40
    assert calls[0]["char_budget"] > 160000
    assert result[0]["score"] == 0.03125
    assert "gxw2_supporting_boost" not in result[0]
    assert "gxw2_supporting_slot" not in result[0]
