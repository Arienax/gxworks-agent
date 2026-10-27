from pathlib import Path
import sqlite3

from tools.import_gxw2_skill import (
    SourceSpec,
    discover_documents,
    import_source,
)


def _create_schema(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            PRAGMA user_version=3;

            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE manuals (
                manual_id TEXT PRIMARY KEY,
                manual_number TEXT NOT NULL,
                revision TEXT NOT NULL,
                published TEXT NOT NULL,
                title TEXT NOT NULL,
                language TEXT NOT NULL,
                manual_type TEXT NOT NULL,
                plc_models TEXT NOT NULL,
                task_types TEXT NOT NULL,
                priority INTEGER NOT NULL,
                source_file TEXT NOT NULL,
                source_bytes INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL,
                official_url TEXT NOT NULL,
                pdf_pages INTEGER NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                chunk_uid TEXT NOT NULL UNIQUE,
                manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
                manual_number TEXT NOT NULL,
                revision TEXT NOT NULL,
                manual_title TEXT NOT NULL,
                manual_type TEXT NOT NULL,
                manual_priority INTEGER NOT NULL,
                language TEXT NOT NULL,
                plc_models TEXT NOT NULL,
                task_types TEXT NOT NULL,
                source_file TEXT NOT NULL,
                pdf_page INTEGER NOT NULL,
                pdf_page_end INTEGER NOT NULL,
                printed_page TEXT NOT NULL,
                printed_page_end TEXT NOT NULL,
                chapter TEXT NOT NULL,
                section TEXT NOT NULL,
                outline_path TEXT NOT NULL,
                section_key TEXT NOT NULL,
                chunk_type TEXT NOT NULL,
                instruction_opcode TEXT NOT NULL,
                fnc_number TEXT NOT NULL,
                block_index INTEGER NOT NULL,
                block_count INTEGER NOT NULL,
                source_pages_json TEXT NOT NULL,
                page_char_count INTEGER NOT NULL,
                page_text_sha256 TEXT NOT NULL,
                text TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                text_sha256 TEXT NOT NULL,
                entities TEXT NOT NULL,
                entities_json TEXT NOT NULL,
                cjk_bigrams TEXT NOT NULL,
                fidelity_flags TEXT NOT NULL,
                UNIQUE(manual_id, section_key, block_index)
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

            CREATE TABLE instructions (
                id INTEGER PRIMARY KEY,
                opcode TEXT NOT NULL,
                opcode_norm TEXT NOT NULL,
                fnc_number TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                variants_json TEXT NOT NULL,
                operands_json TEXT NOT NULL,
                completion_flags_json TEXT NOT NULL,
                restrictions_json TEXT NOT NULL,
                manual_id TEXT NOT NULL REFERENCES manuals(manual_id) ON DELETE CASCADE,
                manual_number TEXT NOT NULL,
                revision TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                source_pages_json TEXT NOT NULL,
                chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
                UNIQUE(opcode_norm, manual_id)
            );

            CREATE TABLE instruction_aliases (
                alias_norm TEXT NOT NULL,
                alias TEXT NOT NULL,
                alias_type TEXT NOT NULL,
                instruction_id INTEGER NOT NULL REFERENCES instructions(id) ON DELETE CASCADE,
                chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
                PRIMARY KEY(alias_norm, instruction_id)
            ) WITHOUT ROWID;

            CREATE TABLE vector_embeddings (
                chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                vector_norm REAL NOT NULL,
                content_sha256 TEXT NOT NULL,
                PRIMARY KEY(chunk_id, model)
            ) WITHOUT ROWID;

            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                text,
                cjk_bigrams,
                entities,
                chapter,
                section,
                chunk_type,
                instruction_opcode,
                manual_title,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 0'
            );
            """
        )


def _make_source(tmp_path: Path) -> Path:
    root = tmp_path / "gxw2-skill" / "skills" / "gxw2-st"
    (root / "references" / "DB").mkdir(parents=True)
    (root / "examples").mkdir()

    (root / "references" / "DB" / "MOV.md").write_text(
        "# MOV\n\nTransfers one word from S1 to D. Example: `MOV(D0, D10);`.\n",
        encoding="utf-8",
    )
    (root / "references" / "DB" / "00_Instruction_List.md").write_text(
        "# Instruction index\n\nMOV | transfer | MOV.md\n",
        encoding="utf-8",
    )
    (root / "references" / "devices.md").write_text(
        "# Devices\n\nM8002 is referenced here as a startup-related special relay.\n",
        encoding="utf-8",
    )
    (root / "examples" / "01-move.iecst").write_text(
        "D10 := D0;\n",
        encoding="utf-8",
    )
    (root / "examples" / "01-move.csv").write_text(
        "Name\tDevice\tType\r\nStart\tX0\tBOOL\r\nMotor\tY0\tBOOL\r\n",
        encoding="utf-16",
    )
    return root


def _spec() -> SourceSpec:
    return SourceSpec(
        id="gxw2_skill_test",
        repository="Serhioromano/gxw2-skill",
        commit="505e63ac75e452f9d48d0b7842464157ee66a853",
        version="1.6.1",
        title="gxw2-skill test",
        language="en",
        plc_models="FX3U,FX3G,FX3S,FX5U",
        priority=52,
        license="MIT",
        homepage="https://github.com/Serhioromano/gxw2-skill",
        archive_url="unused",
        root="skills/gxw2-st",
        published="2026-08-20",
    )


def test_gxw2_skill_import_adds_supporting_chunks_without_structured_instruction(tmp_path):
    database = tmp_path / "knowledge.sqlite"
    _create_schema(database)
    source_root = _make_source(tmp_path)
    documents = discover_documents(source_root)

    stats = import_source(database, _spec(), documents)

    assert stats["documents"] == 5
    assert stats["chunks"] == 4
    assert stats["instructions"] == 0

    csv_document = next(
        document for document in documents if document.relative_path.endswith("01-move.csv")
    )
    assert "Start\tX0\tBOOL" in csv_document.text
    assert "\x00" not in csv_document.text

    with sqlite3.connect(database) as connection:
        manual = connection.execute(
            "SELECT manual_type,priority,revision FROM manuals WHERE manual_id=?",
            ("gxw2_skill_test",),
        ).fetchone()
        assert manual == ("third_party_skill", 52, "1.6.1")

        assert (
            connection.execute(
                "SELECT COUNT(*) FROM instructions WHERE manual_id=?",
                ("gxw2_skill_test",),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM instruction_aliases"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks "
                "WHERE manual_id=? AND chunk_type='skill_instruction' "
                "AND instruction_opcode='MOV'",
                ("gxw2_skill_test",),
            ).fetchone()[0]
            >= 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_file LIKE '%00_Instruction_List.md'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'MOV'"
            ).fetchone()[0]
            >= 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM entity_index "
                "WHERE entity_norm='m8002' AND entity_type='device'"
            ).fetchone()[0]
            >= 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM entity_index "
                "WHERE entity_norm='s1' AND entity_type='operand_placeholder'"
            ).fetchone()[0]
            >= 1
        )
        assert (
            connection.execute(
                "SELECT value FROM meta WHERE key='external_source_gxw2_skill_role'"
            ).fetchone()[0]
            == "supporting"
        )
        assert (
            connection.execute(
                "SELECT value FROM meta WHERE key='vector_status'"
            ).fetchone()[0]
            == "stale"
        )


def test_gxw2_skill_import_is_idempotent(tmp_path):
    database = tmp_path / "knowledge.sqlite"
    _create_schema(database)
    documents = discover_documents(_make_source(tmp_path))
    spec = _spec()

    first = import_source(database, spec, documents)
    second = import_source(database, spec, documents)

    assert first["chunks"] == second["chunks"]
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM manuals WHERE manual_id=?", (spec.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE manual_id=?", (spec.id,)
            ).fetchone()[0]
            == second["chunks"]
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM instructions WHERE manual_id=?", (spec.id,)
            ).fetchone()[0]
            == 0
        )
