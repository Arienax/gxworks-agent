"""Instruction recall must use the official corpus without changing its chunks."""

import sqlite3
from pathlib import Path

import pytest

import knowledge.retriever as retriever
import knowledge.core as core
from shared.paths import resource_path


@pytest.mark.parametrize("opcode", ["AND<>", "AND=", "AND>=", "LD<=", "OR>"])
def test_comparison_mnemonic_keeps_its_operator(opcode):
    assert core._exact_terms(opcode) == [opcode]


@pytest.mark.parametrize(
    "query,alias,expected",
    [("AND<>", "AND", False), ("AND>=", "AND>", False),
     ("AND=", "AND=", True), ("AND<> D0 K1", "AND<>", True),
     ("CAND<>", "AND<>", False), ("AND<>suffix", "AND<>", False),
     ("MPS MRD MPP", "MPS", True), ("FNC 236", "FNC 236", True)],
)
def test_instruction_aliases_match_whole_mnemonics(query, alias, expected):
    assert core._alias_occurs(query, alias) is expected


@pytest.mark.parametrize(
    "query,section",
    [("AND<>", "28.2"), ("AND=", "28.2"), ("AND>=", "28.2"),
     ("MPS", "7.8"), ("MRD", "7.8"), ("MPP", "7.8"),
     ("MPS MRD MPP", "7.8"),
     ("FX3U 普通梯形图中 MPS MRD MPP 指令如何保存和恢复分支逻辑", "7.8")],
)
def test_bundled_official_instruction_recall(query, section):
    # Explicit instruction identities use the fact-aware path used by manual search.
    results = retriever.retrieve_fact_aware_knowledge(query, top_k=4, char_budget=6500)
    assert results
    first = results[0]
    assert first["manual_number"] == "JY997D16601"
    assert first["section"].split(" > ")[-1].startswith(section + " ")
    assert first["match_type"] == "structured_direct"
    if section == "7.8":
        assert "7.8 MPS" in first["text"]
    else:
        assert "AND" in first["text"]
    assert sum(len(core._format_result_block(item)) for item in results) + 2 * (len(results) - 1) <= 6500
    path = Path(resource_path("knowledge/fx3u_knowledge.sqlite"))
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        assert first["text"] == db.execute("SELECT text FROM chunks WHERE id=?", (first["id"],)).fetchone()[0]


@pytest.fixture
def small_instruction_index(tmp_path, monkeypatch):
    path = tmp_path / "manual.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE chunks (id INTEGER PRIMARY KEY, section TEXT, text TEXT,
                manual_number TEXT, manual_type TEXT, plc_models TEXT, task_types TEXT);
            CREATE TABLE instruction_aliases (alias_norm TEXT, alias TEXT,
                alias_type TEXT, chunk_id INTEGER);
        """)
        db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", [
            (701, "15. AND / Logical product", "AND ordinary logical product", "OTHER", "structured_function", "FX3U", "*"),
            (909, "42. AND=, >, <, < >, <=, >= / Comparison", "AND<> D0 K1. AND= D0 K1.", "OFFICIAL", "programming", "FX3U", "*"),
            (507, "12. MPS, MRD, MPP", "MPS stores; MRD reads; MPP pops the stack.", "OFFICIAL", "programming", "FX3U", "generate"),
            (508, "12. MPS, MRD, MPP", "MPS another model", "WRONG_MODEL", "programming", "FX3G", "generate"),
            (509, "12. MPS, MRD, MPP", "MPS support prose", "SKILL", "third_party_skill", "FX3U", "*"),
            (510, "12. MPS, MRD, MPP", "unrelated body missing the requested opcode", "DAMAGED", "programming", "FX3U", "*"),
        ])
        db.execute("INSERT INTO instruction_aliases VALUES ('AND','AND','opcode',701)")
    monkeypatch.setattr(core, "_index_path", lambda: path)
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()
    yield path
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()


def test_broad_retrieval_does_not_use_instruction_alias_table_as_fact_owner(small_instruction_index):
    # With no FTS/entity/dense index, instruction_aliases alone must not turn the
    # broad retriever into an exact instruction resolver.
    assert retriever.retrieve_knowledge("AND<>", char_budget=6500) == []
    assert retriever.retrieve_knowledge("MPS MRD MPP", char_budget=6500) == []


def test_broad_retrieval_still_respects_scope_noise_and_budget(small_instruction_index):
    assert retriever.retrieve_knowledge("MPS", task_type="review") == []
    assert retriever.retrieve_knowledge("MPS", plc_model="FX3G") == []
    assert retriever.retrieve_knowledge("MPS", char_budget=1) == []
    for query in ["MPSuffix", "MPS_history", "please revise this program", "weather tomorrow"]:
        assert retriever.retrieve_knowledge(query) == []


def test_bundled_short_instruction_does_not_leak_into_other_plc_family():
    assert retriever.retrieve_knowledge("MPS MRD MPP", plc_model="FX5U", char_budget=6500) == []


def test_scoped_index_override_does_not_leak_or_get_overwritten(tmp_path, monkeypatch):
    from knowledge.structured_facts import resolve_instruction_records

    original_path = core._index_path
    database = tmp_path / "scoped.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT)")
    with monkeypatch.context() as scoped:
        scoped.setattr(core, "_index_path", lambda: database)
        assert retriever.retrieve_knowledge("MPS", char_budget=6500) == []
        # A facade must never mirror stale aliases over the configured Core.
        assert core._index_path() == database
    assert core._index_path is original_path
    records = resolve_instruction_records(["MPS"], plc_model="FX3U")
    assert any(row.get("manual_number") == "JY997D16601" for row in records)
    assert all(row.get("structured_lookup") for row in records)


def test_declared_natural_language_aliases_resolve_outside_broad_ranking():
    from knowledge.structured_facts import structured_fact_targets

    targets = structured_fact_targets("FX3U 批量清零连续 M 软元件区间应该使用什么指令？")
    reset = next(row for row in targets["instructions"] if row["opcode"] == "ZRST")
    assert reset["target_resolution"] == "declared_manual_alias"
    assert reset["requested_alias"] == "批量清零"
    assert all(row["opcode"] != "ZRST" for row in structured_fact_targets("FX3U 不需要批量清零。") ["instructions"])
    assert structured_fact_targets("please revise this program")["instructions"] == []


def test_section_fallback_keeps_manual_bytes_and_instance_step_facts():
    from knowledge.structured_facts import resolve_instruction_records, compact_structured_fact_record

    full = resolve_instruction_records([{"opcode": "RST", "operands": ["D10"]}])[0]
    assert full["instruction_lookup_basis"] == "official_section_heading"
    assert "STEP_WIDTH: 3 program step(s)" in full["text"]
    public = compact_structured_fact_record(full)
    assert public["instruction_step_width"]["steps"] == 3
    assert public["instruction_contract"]["opcode"] == "RST"
    with sqlite3.connect(resource_path("knowledge/fx3u_knowledge.sqlite").resolve().as_uri()+"?mode=ro", uri=True) as db:
        assert public["text"] == db.execute("SELECT text FROM chunks WHERE id=?", (full["original_id"],)).fetchone()[0]


@pytest.mark.parametrize("index_state", ["missing", "lfs_pointer", "corrupt"])
def test_explicit_targets_survive_unavailable_optional_alias_index(tmp_path, monkeypatch, index_state):
    from knowledge.structured_facts import structured_fact_targets

    query = "FX3U MOV D0 D1; M8013; error code 6706"
    expected = structured_fact_targets(query)
    path = tmp_path / "manual.sqlite"
    if index_state == "lfs_pointer":
        path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 123456\n")
    elif index_state == "corrupt":
        path.write_bytes(b"SQLite format 3\0" + b"broken database page" * 32)
    with monkeypatch.context() as scoped:
        scoped.setattr(core, "_index_path", lambda: path)
        core._close_thread_connection()
        try:
            actual = structured_fact_targets(query)
            assert actual == expected
            assert any(row["opcode"] == "MOV" for row in actual["instructions"])
            assert "M8013" in actual["devices"]
            assert actual["errors"] == ["6706", "6706H"]
            assert not structured_fact_targets("批量清零")["instructions"]
        finally:
            core._close_thread_connection()
    # A failed optional lookup must not poison a later valid index lookup.
    restored = structured_fact_targets("批量清零")
    assert any(row["opcode"] == "ZRST" for row in restored["instructions"])


@pytest.mark.parametrize("error", [PermissionError("denied"), sqlite3.OperationalError("unavailable")])
def test_alias_enrichment_ignores_only_storage_failures(monkeypatch, error):
    import knowledge.structured_facts as facts

    def unavailable(query):
        raise error

    monkeypatch.setattr(facts, "_declared_instruction_alias_targets", unavailable)
    targets = facts.structured_fact_targets("FX3U MOV D0 D1")
    assert any(row["opcode"] == "MOV" for row in targets["instructions"])


def test_alias_enrichment_does_not_hide_programming_errors(monkeypatch):
    import knowledge.structured_facts as facts

    def broken(query):
        raise RuntimeError("invalid alias implementation")

    monkeypatch.setattr(facts, "_declared_instruction_alias_targets", broken)
    with pytest.raises(RuntimeError, match="invalid alias implementation"):
        facts.structured_fact_targets("FX3U MOV D0 D1")
