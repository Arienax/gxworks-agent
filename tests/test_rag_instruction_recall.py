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
    # This is the actual search_plc_manual budget, including complete citations.
    results = retriever.retrieve_knowledge(query, top_k=4, char_budget=6500)
    assert results
    first = results[0]
    assert first["manual_number"] == "JY997D16601"
    assert first["section"].split(" > ")[-1].startswith(section + " ")
    assert first["match_type"] == "manual_instruction"
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
    monkeypatch.setattr(retriever, "_index_path", lambda: path)
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()
    yield path
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()
    retriever._sync_core_hooks()


def test_section_recall_needs_no_structured_or_fts_instruction_rows(small_instruction_index):
    results = retriever.retrieve_knowledge("AND<>", char_budget=6500)
    assert [item["id"] for item in results] == ["909"]
    results = retriever.retrieve_knowledge("MPS MRD MPP", char_budget=6500)
    assert [item["id"] for item in results] == ["507"]


def test_section_recall_preserves_model_task_noise_and_budget(small_instruction_index):
    assert retriever.retrieve_knowledge("MPS", task_type="review") == []
    assert retriever.retrieve_knowledge("MPS", plc_model="FX3G")[0]["id"] == "508"
    assert retriever.retrieve_knowledge("MPS", char_budget=1) == []
    for query in ["MPSuffix", "MPS_history", "please revise this program", "weather tomorrow"]:
        assert retriever.retrieve_knowledge(query) == []


def test_bundled_short_instruction_does_not_leak_into_other_plc_family():
    assert retriever.retrieve_knowledge("MPS MRD MPP", plc_model="FX5U", char_budget=6500) == []
