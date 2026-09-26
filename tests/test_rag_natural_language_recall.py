"""Natural engineering questions must not become unrelated opcode lookups."""
import sqlite3

import pytest

import knowledge.retriever as retriever
import knowledge.core as core
from shared.paths import resource_path


PROSE_QUERIES = [
    "GX Works timer format K units for 0.1s base",
    "Mitsubishi PLC ladder comparator for D register equals range 1,2,3 etc",
    "FX3U timer value K for one second T1",
    "FX3U timer instruction for one second T1",
]


@pytest.mark.parametrize("query", PROSE_QUERIES)
def test_english_preposition_does_not_receive_exact_instruction_priority(query):
    assert "FOR" not in core._exact_terms(query)
    results = retriever.retrieve_knowledge(query, top_k=4, char_budget=6500)
    assert all(item.get("instruction_opcode") != "FOR" for item in results)
    assert all(item.get("matched_entity") != "FOR" for item in results)


@pytest.mark.parametrize("query", ["FOR", "for", "FX3U FOR NEXT loop instruction",
    "FX3U for instruction K10", "FX3U for/next loop", "FX3U `for` instruction", "FX3U for循环"])
def test_explicit_loop_instruction_remains_discoverable(query):
    results = retriever.retrieve_fact_aware_knowledge(query, top_k=4, char_budget=6500)
    assert results
    assert results[0].get("instruction_opcode") in {"FOR", "NEXT"}
    assert results[0].get("manual_type") != "third_party_skill"


@pytest.mark.parametrize("query", [PROSE_QUERIES[0], PROSE_QUERIES[2], "FX3U T20 preset 2.5 seconds",
    "FX3U 定时器 T7 的 K 值和时间基准"])
def test_timer_preset_question_includes_model_range_evidence_within_tool_budget(query):
    results = retriever.retrieve_fact_aware_knowledge(query, plc_model="FX3U", task_type="generate", top_k=4, char_budget=6500)
    assert results
    first = results[0]
    assert "Numbers of timers" in first["section"]
    assert "T0 to T199" in first["text"] and "100 ms" in first["text"]
    assert "FX3U" in first["text"]
    assert first["manual_type"] in {"programming", "structured_device"}
    cost = sum(len(core._format_result_block(item)) for item in results) + 2 * (len(results) - 1)
    assert cost <= 6500
    with sqlite3.connect(resource_path("knowledge/fx3u_knowledge.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
        assert first["text"] == db.execute("SELECT text FROM chunks WHERE id=?", (first["id"],)).fetchone()[0]


def test_time_base_route_does_not_replace_clock_or_reset_questions():
    for query in ("FX3U M8013 clock 1 second flashing", "FX3U timer T3 reset input off"):
        results = retriever.retrieve_knowledge(query, top_k=4, char_budget=6500)
        assert results
        assert "Numbers of timers" not in results[0]["section"]
    assert retriever.retrieve_knowledge(PROSE_QUERIES[2], plc_model="FX5U", top_k=4, char_budget=6500) == []


@pytest.fixture
def timer_range_index(tmp_path, monkeypatch):
    path = tmp_path / "manual.sqlite"
    rows = [
        (811, "2.5.1 Numbers of timers", "FX3U timer T10 to T80 uses 25 ms pulses", "OFFICIAL", "structured_device", "FX3U", "generate"),
        (812, "2.5.1 Numbers of timers", "FX3U T10 to T80 listed without time units", "MISSING_UNITS", "structured_device", "FX3U", "generate"),
        (813, "2.5.1 Numbers of timers", "FX3U timer examples use 25 ms pulses", "MISSING_RANGE", "structured_device", "FX3U", "generate"),
        (814, "2.5.1 Numbers of timers", "SOURCE: FX3U manual\n[PAGE 8 PROSE]\nFX3G timer T10 to T80 uses 25 ms pulses", "OTHER_MODEL_BODY", "structured_device", "FX3U", "generate"),
        (815, "2.5.1 Numbers of timers", "FX3U timer T10 to T80 uses 25 ms pulses", "OTHER_SCOPE", "structured_device", "FX3G", "generate"),
        (816, "2.5.1 Numbers of timers", "FX3U timer T10 to T80 uses 25 ms pulses", "SUPPORTING", "third_party_skill", "FX3U", "generate"),
    ]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, section TEXT, text TEXT, manual_number TEXT, manual_type TEXT, plc_models TEXT, task_types TEXT)")
        db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(core, "_index_path", lambda: path)
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()
    yield
    retriever._close_thread_connection()
    retriever._retrieve_cached.cache_clear()


def test_range_recall_requires_evidence_body_and_preserves_model_task_budget(timer_range_index):
    query = "FX3U timer K units"
    results = retriever.retrieve_knowledge(query, top_k=4, char_budget=6500)
    assert [item["manual_number"] for item in results] == ["OFFICIAL"]
    assert results[0]["text"] == "FX3U timer T10 to T80 uses 25 ms pulses"
    assert retriever.retrieve_knowledge(query, task_type="debug") == []
    assert retriever.retrieve_knowledge(query, char_budget=1) == []
    assert retriever.retrieve_knowledge("FX3U timer reset") == []


def test_explicit_property_lookup_uses_scope_and_body_before_broad_recall(timer_range_index):
    results = retriever.retrieve_fact_aware_knowledge("FX3U timer T20 K units", task_type="generate", char_budget=6500)
    assert results and results[0]["manual_number"] == "OFFICIAL"
    assert results[0]["device_lookup_basis"] == "official_property_section"
    assert results[0]["fact_dimensions"] == ["range", "time_base"]
    assert not retriever.retrieve_fact_aware_knowledge("FX3U timer T9999 K units", task_type="debug")
    assert not retriever.retrieve_fact_aware_knowledge("FX3U timer T20 K units", task_type="generate", char_budget=1)


def test_device_definition_lookup_does_not_prefer_incidental_instruction_mentions():
    from knowledge.structured_facts import resolve_device_records

    rows = resolve_device_records(["M8013"], plc_model="FX3U", task_type="generate")
    assert rows and all(row["device_lookup_basis"] == "official_definition_heading" for row in rows)
    assert all("Internal clock" in row["section"] for row in rows)
    assert any("M8013" in row["text"] for row in rows)
