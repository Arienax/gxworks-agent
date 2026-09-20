"""Instruction fact assembly, source integrity and delivered evidence accounting.

Synthetic text below tests retrieval mechanics, not Mitsubishi instruction facts.
Actual-index sentinels check source selection, not generated-program correctness.
"""
import copy
import json
import sqlite3

import pytest

from knowledge import core
from knowledge.evidence import KnowledgeQuery
from knowledge.instruction_facts import (
    _is_target, _pack_target, _related_units, _units, delivered_fact_report,
    included_knowledge_ids, instruction_fact_targets, retrieve_instruction_facts,
)
from knowledge.retriever import build_knowledge_context


def source(identity="one", opcode="MOV", text="Source operand and destination word. Operation copies data."):
    return {"id": identity, "source": "Synthetic manual", "manual_id": "fixture", "manual_number": "TEST",
            "revision": "1", "section": opcode + " instruction", "manual_type": "programming",
            "chunk_type": "instruction", "instruction_opcode": opcode, "page": 1, "text": text}


@pytest.mark.parametrize("opcode", ["SFTL", "WSFL", "MOV", "BMOV", "CMP", "DRVI", "$MOV"])
def test_targets_follow_catalogue_not_a_shift_recipe(opcode):
    spec = {"selected_approach": {"generation_contract": {"required_opcodes": [opcode]}}}
    before = copy.deepcopy(spec)
    targets = instruction_fact_targets("", spec)
    assert opcode in [target["opcode"] for target in targets]
    assert spec == before
    if opcode == "$MOV":
        assert "MOV" not in [target["opcode"] for target in targets]


def test_named_selected_description_is_a_target_not_a_new_design():
    assert instruction_fact_targets("", {"selected_approach": {"description": "Use WSFL."}})[0]["opcode"] == "WSFL"
    assert instruction_fact_targets("不要 SFTL，只用 MOV K1 D0")[0]["opcode"] == "MOV"


def test_instruction_mention_and_punctuation_are_not_exact_definitions():
    target = {"opcode": "MOV", "base_opcode": "MOV"}
    wrong = source(opcode="", text="MOV is discussed elsewhere")
    wrong["section"] = "$MOV / Character String Transfer"
    assert not _is_target(wrong, target)
    wrong["section"] = "General programming"
    assert not _is_target(wrong, target)
    wrong["instruction_opcode"] = "MOV"
    wrong["manual_type"] = "third_party_skill"
    assert not _is_target(wrong, target)


def test_whole_table_and_offsets_are_preserved():
    text = "[TABLE page=1]\nOperand | Description\n\nA | source\nB | destination\n"
    assert list(_units(text)) == [(0, len(text))]
    result = _pack_target([source(text=text)], 2000)[0]
    assert text.rstrip() in result["text"]
    assert result["source_spans"] == [{"start": 0, "end": len(text)}]
    assert _pack_target([source(text=text)], 50) == []  # never cut table rows


def test_pack_across_definition_table_and_caution_without_duplicate_layout():
    rows = [source("definition", text="[PAGE 1 PROSE]\nOperation transfers a word.\n\n[PAGE 1 LAYOUT]\nDUPLICATE LAYOUT"),
            source("table", text="[TABLE page=1]\nOperand | Description\nS | source\nD | destination"),
            source("caution", text="[PAGE 2 PROSE]\nExecution occurs each scan; range is limited.")]
    output = _pack_target(rows, 2500)
    text = "\n".join(row["text"] for row in output)
    assert all(value in text for value in ("Operation transfers", "Operand | Description", "each scan"))
    assert "DUPLICATE LAYOUT" not in text
    assert sum(len(core._format_result_block(row))+40 for row in output) <= 2500
    altered_revision = source("other", text="FOREIGN REVISION range")
    altered_revision["revision"] = "2"
    assert "FOREIGN REVISION" not in str(_pack_target([rows[0], altered_revision], 2500))


def test_source_expansion_obeys_revision_model_and_section(tmp_path, monkeypatch):
    db = tmp_path / "index.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE chunks (id TEXT, manual_id TEXT, revision TEXT, section TEXT, text TEXT, manual_type TEXT, plc_models TEXT)")
        connection.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", [
            ("1", "fixture", "1", "MOV instruction", "operand word", "programming", "FX3U"),
            ("2", "fixture", "1", "MOV instruction", "range limit", "programming", "FX3U"),
            ("3", "fixture", "2", "MOV instruction", "wrong revision", "programming", "FX3U"),
            ("4", "fixture", "1", "MOV instruction", "wrong CPU", "programming", "FX5U"),
            ("5", "fixture", "1", "CMP instruction", "wrong section", "programming", "FX3U"),
        ])
    monkeypatch.setattr(core, "_index_path", lambda: db)
    try:
        assert {row["id"] for row in _related_units(source("1"), "FX3U", "generate")} == {"1", "2"}
    finally:
        core._close_thread_connection()


def test_coverage_is_candidate_only_and_tracks_actual_delivery(monkeypatch):
    import knowledge.instruction_facts as facts
    monkeypatch.setattr(facts, "_related_units", lambda *a: [])
    calls = []
    def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        return [source()]
    blocks, report = retrieve_instruction_facts("MOV", plc_model="FX3U", task_type="generate",
                                                char_budget=2000, retrieve=retrieve)
    assert len(calls) == 1 and "operand" in calls[0][0] and "execution" in calls[0][0]
    included = delivered_fact_report(report, [blocks[0]["id"]])
    assert included["verification"] == "not_performed"
    assert {row["status"] for row in included["facts"]} == {"candidate_evidence", "unresolved"}
    omitted = delivered_fact_report(report, [])
    assert {row["status"] for row in omitted["facts"]} == {"budget_omitted", "unresolved"}
    assert not any(row.get("included") for row in omitted["records"])


def test_truncated_or_changed_blocks_never_count_as_delivered():
    row = _pack_target([source()], 2000)[0]
    block = core._format_result_block(row)
    assert included_knowledge_ids(block, [row]) == [row["id"]]
    assert included_knowledge_ids(block.replace("word", "changed"), [row]) == []
    assert included_knowledge_ids(block.rsplit("[/KNOWLEDGE]", 1)[0], [row]) == []
    broken = '[KNOWLEDGE {"id":"broken"}]\npartial\n' + block
    assert included_knowledge_ids(broken, [row]) == [row["id"]]


@pytest.mark.parametrize("opcode", ["SFTL", "WSFL", "MOV", "BMOV", "CMP"])
def test_bundled_index_delivers_instruction_definitions_inside_existing_budget(opcode):
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    query = KnowledgeQuery(opcode, precompiled=True, metadata={"instruction_fact_mode": "targeted"})
    context = build_knowledge_context(query, char_budget=7000, top_k=5)
    assert len(context) <= 7000
    report = context.manifest["instruction_facts"]
    assert report["verification"] == "not_performed" and report["records"]
    assert "Operand Type" in context
    assert not any(row.get("manual_type") == "third_party_skill" for row in context.manifest["records"])
    if opcode in {"SFTL", "WSFL"}:
        assert "each operation cycle" in context
    if opcode == "MOV":
        assert "$MOV / Character String" not in context
    assert set(included_knowledge_ids(context, report["records"])) == {r["id"] for r in report["records"] if r["included"]}


def test_shared_generation_handoff_keeps_delivered_fact_report():
    from application.confirmed_generation_context import build_confirmed_generation_context
    spec = {"summary": "字寄存器队列", "selected_approach": {
        "generation_contract": {"required_opcodes": ["WSFL"]}}}
    before = copy.deepcopy(spec)
    context = build_confirmed_generation_context(spec, "FX3U")
    assert spec == before
    report = context.handoff["instruction_facts"]
    assert report["verification"] == "not_performed"
    assert set(included_knowledge_ids(context.knowledge_context, report["records"])) == {
        row["id"] for row in report["records"] if row["included"]}
    assert report["facts"] and any(row["source_ids"] for row in report["facts"])
