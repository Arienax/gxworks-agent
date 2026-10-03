"""Instruction fact assembly, source integrity and delivered evidence accounting.

Synthetic text below tests retrieval mechanics, not Mitsubishi instruction facts.
Actual-index sentinels check source selection, not generated-program correctness.
"""
import copy
import json
import sqlite3
from itertools import permutations

import pytest

from knowledge import core
from knowledge.evidence import KnowledgeQuery
from knowledge.instruction_facts import (
    _conflicts_with_verified_operand_order, _is_target, _manual_fact_gaps,
    _operand_gap_details, _pack_target, _related_units, _units,
    _visual_operand_sequence, delivered_fact_report, included_knowledge_ids,
    instruction_fact_targets, retrieve_instruction_facts,
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


def test_gap_directed_manual_packing_drops_already_covered_operand_only_units():
    rows = [
        source(
            "operation",
            text="[PAGE 1 PROSE]\nOperation copies the source into the destination.",
        ),
        source(
            "operands",
            text="[TABLE page=1]\nOperand | Description\nS | source\nD | destination",
        ),
    ]
    output = _pack_target(rows, 2500, needed_categories={"operation"})
    text = "\n".join(row["text"] for row in output)
    assert "Operation copies" in text
    assert "Operand | Description" not in text


def test_verified_order_visual_conflict_detector_ignores_concrete_examples():
    expected = ["S1", "S2", "D"]
    visual = (
        "[PAGE 1 PROSE]\n"
        "TADD D 10 D 20 D 30\n"
        "S1 [GLYPH-F0A0] S2 [GLYPH-F0A0] D [GLYPH-F0A0]\n"
        "(D10,D11,D12)+(D20,D21,D22)\n"
    )
    assert _visual_operand_sequence(visual, "TADD", expected) == expected
    record = {
        "instruction_opcode": "TADD",
        "target_applicability": {
            "operand_order_status": "source_verified",
            "native_operand_order": expected,
        },
    }
    assert not _conflicts_with_verified_operand_order(record, visual)


@pytest.mark.parametrize(
    ("opcode", "expected", "visual", "sequence"),
    [
        (
            "CRC", ["S", "D", "N"],
            "[PAGE 1 PROSE]\nCRC n\nM8161\nD [GLYPH-F0A0]S [GLYPH-F0A0]\nS\n",
            ["N", "D", "S"],
        ),
        (
            "DFMOV", ["S", "D", "N"],
            "[PAGE 1 PROSE]\nDFMOV nS [GLYPH-F0A0] D [GLYPH-F0A0]\n+1,+1\n",
            ["N", "S", "D"],
        ),
        (
            "SFTRP", ["S", "D", "N1", "N2"],
            "[PAGE 1 PROSE]\nSFTRP n1 n2D [GLYPH-F0A0]S [GLYPH-F0A0]\nBefore\n",
            ["N1", "N2", "D", "S"],
        ),
    ],
)
def test_verified_order_visual_conflict_detector_finds_flattened_diagrams(
    opcode, expected, visual, sequence,
):
    assert _visual_operand_sequence(visual, opcode, expected) == sequence
    record = {
        "instruction_opcode": opcode,
        "target_applicability": {
            "operand_order_status": "source_verified",
            "native_operand_order": expected,
        },
    }
    assert _conflicts_with_verified_operand_order(record, visual)


def test_verified_order_visual_conflict_detector_ignores_prose_mnemonic_collision():
    text = (
        "[PAGE 1 PROSE]\n"
        "The dead band is adjusted by S2 [GLYPH-F0A0] before S1 [GLYPH-F0A0].\n"
    )
    assert _visual_operand_sequence(
        text, "BAND", ["S1", "S2", "S3", "D"]
    ) == []


def test_verified_order_filter_applies_to_related_manual_units():
    rows = [
        source(
            "safe", opcode="CRC",
            text="[PAGE 1 PROSE]\nOperation calculates a CRC from source data.\n",
        ),
        source(
            "visual", opcode="CRC",
            text=(
                "[PAGE 1 PROSE]\nCRC n\nM8161\n"
                "D [GLYPH-F0A0]S [GLYPH-F0A0]\nS\n"
            ),
        ),
    ]
    output = _pack_target(
        rows,
        4000,
        needed_categories={"operands", "operation"},
        verified_operand_order=["S", "D", "N"],
        verified_opcodes=("CRC",),
    )
    rendered = "\n".join(row["text"] for row in output)
    assert "Operation calculates a CRC" in rendered
    assert "CRC n" not in rendered


def test_embedded_pdf_layout_residue_is_not_a_second_operand_order():
    text = (
        "[PAGE 687 PROSE]\n"
        "Operation reads the source operand and transfers the result to the destination.\n"
        "Inverter station number and channel requirements are described here.\n"
        "FNC 270\n"
        "IVCK\n"
        "9 steps IVCK\n"
        "[GLYPH-F0BE]\n"
        "[GLYPH-F0BE][GLYPH-F0BE]\n"
        "FNC270\n"
        "IVCK n\n"
        "S1 [GLYPH-F0A0] S2 [GLYPH-F0A0] D [GLYPH-F0A0]\n"
    )
    output = _pack_target(
        [source("ivck", opcode="IVCK", text=text)],
        4000,
        needed_categories={"operands", "operation"},
    )
    rendered = "\n".join(row["text"] for row in output)
    assert "Operation reads the source operand" in rendered
    assert "IVCK n" not in rendered
    assert all(
        not (
            span["start"] <= text.index("FNC 270") < span["end"]
        )
        for row in output
        for span in row["source_spans"]
    )


def test_operand_gap_tracking_is_slot_and_facet_granular():
    record = {
        "operand_slots": [
            {
                "position": 1, "symbol": "S1", "name": "source",
                "role_status": "source_verified",
                "data_type_status": "source_verified",
                "symbol_status": "source_verified",
                "device_class_status": "source_verified",
            },
            {
                "position": 2, "symbol": "D", "name": "destination",
                "role_status": "source_verified",
                "data_type_status": "source_verified",
                "symbol_status": "source_verified",
                "device_class_status": "unresolved",
            },
        ],
        "target_applicability": {"boundary_status": "source_verified"},
    }
    assert _operand_gap_details(record) == [
        {"position": 1, "facet": "purpose", "status": "unresolved", "symbol": "S1", "name": "source"},
        {"position": 2, "facet": "device_classes", "status": "unresolved", "symbol": "D", "name": "destination"},
        {"position": 2, "facet": "purpose", "status": "unresolved", "symbol": "D", "name": "destination"},
    ]
    assert _manual_fact_gaps(record) == frozenset({
        "operands", "operation", "execution",
    })


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
    monkeypatch.setattr(facts, "_completion_sources", lambda *a: [])
    calls = []

    def resolve(targets, **kwargs):
        calls.append((copy.deepcopy(targets), kwargs))
        return [source()]

    def broad_lookup(*args, **kwargs):
        pytest.fail("explicit instruction facts must not call broad retrieval")

    blocks, report = retrieve_instruction_facts(
        "MOV", plc_model="FX3U", task_type="generate", char_budget=2000,
        retrieve=broad_lookup, resolver=resolve,
    )
    assert calls == [([{"opcode": "MOV", "base_opcode": "MOV"}],
                      {"plc_model": "FX3U", "task_type": "generate"})]
    assert report["retrieval_mode"] == "structured_direct"
    assert report["queries"] == []
    assert report["lookups"][0]["source"] == "instructions.opcode_norm"
    included = delivered_fact_report(report, [blocks[0]["id"]])
    assert included["verification"] == "not_performed"
    assert {row["status"] for row in included["facts"]} == {"candidate_evidence", "unresolved"}
    omitted = delivered_fact_report(report, [])
    assert {row["status"] for row in omitted["facts"]} == {"budget_omitted", "unresolved"}
    assert not any(row.get("included") for row in omitted["records"])
    assert included["coverage_version"] == "fact-coverage-v1"


def test_generic_fact_coverage_accounts_for_instruction_device_and_error():
    from knowledge.fact_coverage import build_fact_coverage, reconcile_fact_coverage

    targets = {
        "instructions": [{"opcode": "MOV", "base_opcode": "MOV"}],
        "devices": ["M8029"],
        "errors": ["1234H"],
    }
    records = [
        {"id": "instruction", "fact_kind": "instruction", "fact_target": "MOV",
         "fact_dimensions": ["operands"], "text": "MOV operands"},
        {"id": "device", "fact_kind": "device", "fact_target": "M8029",
         "fact_dimensions": ["definition"], "text": "M8029 definition"},
        {"id": "error", "fact_kind": "error", "fact_target": "1234H",
         "fact_dimensions": ["definition"], "text": "1234H definition"},
    ]
    report = build_fact_coverage(
        targets, records, ["instruction", "device"],
        instruction_questions={"operands": "operand semantics"},
    )
    by_kind = {
        (row["kind"], row["target"], row["dimension"]): row
        for row in report["requirements"]
    }
    assert by_kind[("instruction", "MOV", "operands")]["status"] == "candidate_evidence"
    assert by_kind[("device", "M8029", "definition")]["status"] == "candidate_evidence"
    assert by_kind[("error", "1234H", "definition")]["status"] == "budget_omitted"
    reconciled = reconcile_fact_coverage(report, ["error"])
    assert {row["status"] for row in reconciled["requirements"]} == {
        "candidate_evidence", "budget_omitted",
    }


def test_fact_coverage_core_is_domain_agnostic():
    from knowledge.fact_coverage import build_coverage

    report = build_coverage(
        [{
            "id": "module:FX3U-4AD:buffer_memory",
            "kind": "module",
            "target": "FX3U-4AD",
            "dimension": "buffer_memory",
        }],
        [{
            "id": "module-source",
            "fact_kind": "module",
            "fact_target": "FX3U-4AD",
            "fact_dimensions": ["buffer_memory"],
            "text": "buffer memory definition",
        }],
        ["module-source"],
    )
    assert report["requirements"] == [{
        "id": "module:FX3U-4AD:buffer_memory",
        "kind": "module",
        "target": "FX3U-4AD",
        "dimension": "buffer_memory",
        "candidate_source_ids": ["module-source"],
        "source_ids": ["module-source"],
        "status": "candidate_evidence",
    }]


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
    generic = context.manifest["fact_coverage"]
    assert generic["version"] == "fact-coverage-v1"
    assert any(
        row["kind"] == "instruction"
        and row["target"] == opcode
        and row["status"] == "candidate_evidence"
        for row in generic["requirements"]
    )
    assert "Operand Type" in context
    assert not any(row.get("manual_type") == "third_party_skill" for row in context.manifest["records"])
    if opcode in {"SFTL", "WSFL"}:
        assert "each operation cycle" in context
    if opcode == "MOV":
        assert "$MOV / Character String" not in context
    assert set(included_knowledge_ids(context, report["records"])) == {r["id"] for r in report["records"] if r["included"]}


def test_ivck_verified_order_does_not_compete_with_flattened_page_layout():
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    query = KnowledgeQuery(
        "IVCK",
        precompiled=True,
        metadata={"instruction_fact_mode": "targeted"},
    )
    context = build_knowledge_context(
        query,
        plc_model="FX3U",
        task_type="generate",
        char_budget=24000,
        top_k=8,
    )
    report = context.manifest["instruction_facts"]
    lookup = next(row for row in report["lookups"] if row["opcode"] == "IVCK")
    assert "operand_order" not in {
        row["facet"] for row in lookup["operand_gap_details"]
    }
    assert '"symbol":"S1"' in context
    assert '"symbol":"S2"' in context
    assert '"symbol":"D"' in context
    assert '"symbol":"N"' in context
    assert "IVCK n\nS1" not in context


def test_verified_role_type_order_do_not_close_unverified_device_class_gap():
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    query = KnowledgeQuery(
        "DADDP",
        precompiled=True,
        metadata={"instruction_fact_mode": "targeted"},
    )
    context = build_knowledge_context(
        query,
        plc_model="FX3U",
        task_type="generate",
        char_budget=24000,
        top_k=8,
    )
    report = context.manifest["instruction_facts"]
    lookup = next(row for row in report["lookups"] if row["opcode"] == "DADDP")
    assert "operands" in lookup["manual_gaps"]
    assert "operands" not in lookup["structured_dimensions"]
    gaps = lookup["operand_gap_details"]
    assert gaps
    assert {row["facet"] for row in gaps} == {"device_classes", "purpose"}
    assert {row["position"] for row in gaps} == {1, 2, 3}

    included = [row for row in report["records"] if row.get("included")]
    assert included
    assert any(
        "operands" in (row.get("candidate_fact_categories") or ())
        for row in included
    )


_USAGE_ROWS = (
    ("S", "New data source", "Bit"),
    ("D", "Head of affected data", "Bit"),
    ("N1", "Affected length in bits", "16-bit binary"),
    ("N2", "Number of bits moved per operation*1", "16-bit binary"),
)


def _usage_source(rows=_USAGE_ROWS, *, identity="usage", suffix=""):
    """Synthetic table; the frozen symbol-to-description pairs are the oracle."""
    from plc.instruction_semantics import bind_operand_slots
    result = source(identity, opcode="SFTL", text=(
        "[TABLE page=12]\nOperand Type | Description | Data Type\n"
        + "\n".join(" | ".join(row) for row in rows)
        + "\n\n*1. Applies only while enabled.\n" + suffix
    ))
    result["operand_semantics"] = {
        "opcode": "SFTL", "operand_role_status": "source_verified", "operand_type_status": "source_verified",
        "operands": [{"name": symbol, "role": "read", "data_type": data_type} for symbol, _, data_type in _USAGE_ROWS],
    }
    result["target_applicability"] = {
        "opcode": "SFTL", "target_model": "FX3U", "native_operand_order": ["S", "D", "N1", "N2"],
        "operand_order_status": "source_verified", "device_class_status": "source_verified",
    }
    result["operand_slots"] = bind_operand_slots(
        result["operand_semantics"], result["target_applicability"], ["M0", "M100", "K16", "K2"],
    )
    return result


@pytest.mark.parametrize("rows", list(permutations(_USAGE_ROWS)))
def test_operand_usage_binding_is_invariant_under_all_table_row_permutations(rows):
    original = _usage_source(rows)
    before = copy.deepcopy(original)
    gaps = _operand_gap_details(original)
    packed = _pack_target(
        [original], 12000, needed_categories={"operands"}, operand_gap_details=gaps,
        verified_operand_order=["S", "D", "N1", "N2"], structured_owner=original,
    )
    assert len(packed) == 1
    row = packed[0]
    slots = row["operand_slots"]
    assert [slot["symbol"] for slot in slots] == ["S", "D", "N1", "N2"]
    assert [slot["value"] for slot in slots] == ["M0", "M100", "K16", "K2"]
    assert [slot["usage_facts"][0]["value"] for slot in slots] == [item[1] for item in _USAGE_ROWS]
    assert {slot["purpose_status"] for slot in slots} == {"candidate_evidence"}
    for binding in row["operand_evidence_bindings"]:
        span = binding["source"]["row_span"]
        assert original["text"][span["start"]:span["end"]] == " | ".join(_USAGE_ROWS[binding["position"] - 1])
        assert binding["source"]["pdf_page"] == 12
        assert binding["source"]["target_model"] == "FX3U"
    assert original["text"].rstrip() in row["text"]
    assert "*1. Applies only while enabled." in row["text"]
    assert len(_operand_gap_details(row)) == 4  # candidate delivery never certifies usage
    assert original == before


def test_operand_usage_evidence_covers_only_its_slot_and_surviving_block(monkeypatch):
    import knowledge.instruction_facts as facts
    from knowledge.fact_coverage import build_fact_coverage, reconcile_fact_coverage
    monkeypatch.setattr(facts, "_related_units", lambda *a: [])
    monkeypatch.setattr(facts, "_completion_sources", lambda *a: [])
    original = _usage_source((_USAGE_ROWS[1],))
    target = {"opcode": "SFTL", "base_opcode": "SFTL", "operands": ["M0", "M100", "K16", "K2"]}
    blocks, report = retrieve_instruction_facts(
        "", targets=[target], plc_model="FX3U", task_type="generate", char_budget=12000,
        resolver=lambda *a, **k: [original],
    )
    included = delivered_fact_report(report, [blocks[0]["id"]])
    assert [(item["position"], item["status"]) for item in included["operand_facts"]] == [
        (1, "unresolved"), (2, "candidate_evidence"), (3, "unresolved"), (4, "unresolved"),
    ]
    generic = build_fact_coverage(
        {"instructions": [target]}, blocks, [blocks[0]["id"]],
        instruction_questions=report["questions"], extra_requirements=report["operand_requirements"],
    )
    omitted = reconcile_fact_coverage(generic, [])
    assert next(item for item in omitted["requirements"] if item["dimension"] == "operand:2:purpose")["status"] == "budget_omitted"
    assert next(item for item in omitted["requirements"] if item["dimension"] == "operand:1:purpose")["status"] == "unresolved"
    other_instance = copy.deepcopy(blocks)
    other_instance[0]["instruction_instance"]["operands"][2] = "K8"
    wrong = build_fact_coverage(
        {"instructions": [target]}, other_instance, [blocks[0]["id"]],
        extra_requirements=report["operand_requirements"],
    )
    assert {item["status"] for item in wrong["requirements"] if "facet" in item} == {"unresolved"}


def test_final_context_receipt_distinguishes_bound_omitted_and_unknown_usage(monkeypatch):
    import knowledge.instruction_facts as facts
    import knowledge.structured_facts as structured
    from application.confirmed_generation_context import build_confirmed_generation_context
    monkeypatch.setattr(facts, "_related_units", lambda *a: [])
    monkeypatch.setattr(facts, "_completion_sources", lambda *a: [])
    sources = [_usage_source((_USAGE_ROWS[0],), identity="first"), _usage_source((_USAGE_ROWS[1],), identity="second")]
    monkeypatch.setattr(structured, "resolve_instruction_records", lambda *a, **k: copy.deepcopy(sources))
    spec = {"summary": "Synthetic binding delivery", "selected_approach": {"generation_contract": {
        "required_opcodes": ["SFTL"], "instruction_instances": [{"opcode": "SFTL", "operands": ["M0", "M100", "K16", "K2"]}],
    }}}
    context = build_confirmed_generation_context(
        spec, "FX3U", knowledge_builder=lambda query, **k: build_knowledge_context(query, char_budget=24000, top_k=1),
        wire_renderer=lambda _spec, evidence, request, _program, _checkpoint, _history: {
            "messages": [{"role": "system", "content": evidence}, {"role": "user", "content": request}],
        },
    )
    assert [(item["position"], item["status"]) for item in context.handoff["instruction_facts"]["operand_facts"] if item["opcode"] == "SFTL"] == [
        (1, "candidate_evidence"), (2, "budget_omitted"), (3, "unresolved"), (4, "unresolved"),
    ]
    assert "New data source" in context.wire_packet["messages"][0]["content"]
    assert "Head of affected data" not in context.wire_packet["messages"][0]["content"]
    generic = context.handoff["fact_coverage"]["requirements"]
    assert {item["dimension"]: item["status"] for item in generic if "facet" in item and item["target"] == "SFTL"} == {
        "operand:1:purpose": "candidate_evidence", "operand:2:purpose": "budget_omitted",
        "operand:3:purpose": "unresolved", "operand:4:purpose": "unresolved",
    }


@pytest.mark.parametrize("damage", ["duplicate_row", "merged_symbols", "unverified_order", "duplicate_native_symbol", "type_as_description"])
def test_ambiguous_or_missing_operand_usage_is_not_bound(damage):
    row = _USAGE_ROWS[0]
    rows = [row]
    if damage == "duplicate_row":
        rows.append(("S", "Another meaning", "Bit"))
    elif damage == "merged_symbols":
        rows = [("S | D", row[1], row[2])]
    elif damage == "type_as_description":
        rows = [("S", "16-bit binary", "-F")]
    original = _usage_source(rows)
    order = ["S", "D", "N1", "N2"]
    if damage == "unverified_order":
        order = None
        original["target_applicability"]["operand_order_status"] = "declared_unverified"
    elif damage == "duplicate_native_symbol":
        order = ["S", "S", "N1", "N2"]
    packed = _pack_target(
        [original], 12000, operand_gap_details=_operand_gap_details(original),
        verified_operand_order=order, structured_owner=original,
    )
    assert not any(item.get("operand_evidence_bindings") for item in packed)
    assert all(slot["purpose_status"] == "unresolved" for item in packed for slot in item["operand_slots"])


@pytest.mark.parametrize("hints,expected", [
    ([{"position": "D", "description": "Head of affected data"}], [2]),
    ([{"position": "D", "description": "Different description"}], []),
    ([{"position": "D", "description": "Head of affected data"}, {"position": "S", "description": "Head of affected data"}], []),
])
def test_missing_table_symbol_uses_only_unambiguous_source_backed_description(hints, expected):
    original = _usage_source([("<blank>", "Head of affected data", "Bit")])
    original["manual_operand_rows"] = hints
    packed = _pack_target(
        [original], 12000, operand_gap_details=_operand_gap_details(original),
        verified_operand_order=["S", "D", "N1", "N2"], structured_owner=original,
    )
    assert [binding["position"] for item in packed for binding in item["operand_evidence_bindings"]] == expected


def test_operand_table_can_be_recovered_without_delivering_conflicting_layout():
    original = _usage_source()
    table = original["text"].split("\n", 1)[1]
    original["text"] = (
        "[PAGE 12 LAYOUT]\nFNC 35\nSFTL n1 n2D [GLYPH-F0A0]S [GLYPH-F0A0]\n"
        + table + "\n3. Applicable devices\nUnrelated device table"
    )
    packed = _pack_target(
        [original], 12000, operand_gap_details=_operand_gap_details(original),
        verified_operand_order=["S", "D", "N1", "N2"], verified_opcodes=["SFTL"], structured_owner=original,
    )
    assert [binding["position"] for item in packed for binding in item["operand_evidence_bindings"]] == [1, 2, 3, 4]
    assert "SFTL n1 n2D" not in packed[0]["text"]
    assert "*1. Applies only while enabled." in packed[0]["text"]
    assert "Unrelated device table" not in packed[0]["text"]


@pytest.mark.parametrize("opcode,position,snippet", [
    ("SFTL", 3, "Bit length of the shift data"),
    ("SFTL", 4, "Number of bits to be shifted leftward"),
    ("WSFL", 3, "Word data length of the shift data"),
    ("WSFL", 4, "Number of words to be shifted leftward"),
    ("BMOV", 3, "Number of transferred points"),
    ("CMP", 3, "Head bit device number to which comparison result is output"),
])
def test_bundled_operand_usage_returns_to_original_manual_rows(opcode, position, snippet):
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    context = build_knowledge_context(KnowledgeQuery(opcode, precompiled=True), char_budget=24000, top_k=8)
    report = context.manifest["instruction_facts"]
    bindings = [b for r in report["records"] if r.get("included") for b in r.get("operand_evidence_bindings", [])]
    binding = next(b for b in bindings if b["position"] == position and b["facet"] == "purpose")
    assert snippet in binding["value"]
    evidence = binding["source"]
    assert evidence["manual_id"] == "fx3_programming_r"
    assert evidence["revision"] == "R"
    assert evidence["offset_basis"] == "chunks.text"
    with sqlite3.connect(core._index_path().resolve().as_uri() + "?mode=ro", uri=True) as connection:
        original = connection.execute("SELECT text FROM chunks WHERE id=?", (evidence["id"],)).fetchone()[0]
    span = evidence["row_span"]
    assert binding["value"] in original[span["start"]:span["end"]]
    assert binding["status"] == "candidate_evidence"
    requirement = next(r for r in context.manifest["fact_coverage"]["requirements"] if r["dimension"] == f"operand:{position}:purpose")
    assert requirement["status"] == "candidate_evidence"


def test_generation_packer_delivers_structured_contract_with_manual_evidence():
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")
    spec = {"selected_approach": {"generation_contract": {
        "required_opcodes": ["WSFL"],
        "instruction_instances": [{
            "opcode": "WSFL",
            "operands": ["D0", "D10", "K56", "K1"],
        }],
    }}}
    from application.confirmed_generation_context import build_confirmed_generation_context
    context = build_confirmed_generation_context(spec, "FX3U")
    assert "OPERAND_SEMANTICS:" in context.knowledge_context
    assert "INSTRUCTION_CONTRACT:" not in context.knowledge_context
    assert "STEP_WIDTH:" not in context.knowledge_context
    report = context.handoff["instruction_facts"]
    record = next(row for row in report["records"] if row.get("instruction_contract"))
    assert record["instruction_contract"]["native_operand_order"] == ["S", "D", "N1", "N2"]
    assert record["instruction_contract"]["confirmed_operands"] == ["D0", "D10", "K56", "K1"]
    assert [slot["value"] for slot in record["operand_slots"]] == ["D0", "D10", "K56", "K1"]
    assert record["target_applicability"]["target_model"] == "FX3U"
    assert record["instruction_step_width"]["steps"] == 9


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
    generic = context.handoff["fact_coverage"]
    assert generic["version"] == "fact-coverage-v1"
    assert any(row["source_ids"] for row in generic["requirements"])


@pytest.mark.parametrize("targets", [None, []])
def test_empty_instruction_targets_never_promote_broad_hits(targets):
    candidates = [source(name, name) for name in ("MOV", "PLSY", "PRUN")]
    before = copy.deepcopy(candidates)
    def unexpected_lookup(*args, **kwargs):
        pytest.fail("broad candidate rank must not manufacture an instruction target")
    blocks, report = retrieve_instruction_facts(
        "启动停止自保持", plc_model="FX3U", task_type="generate", char_budget=7000,
        targets=targets, candidates=candidates, retrieve=unexpected_lookup,
    )
    assert blocks == [] and report["targets"] == [] and report["queries"] == []
    assert report["reason"] == "no_instruction_target"
    assert report["target_source"] == ("provided_targets" if targets is not None else "query_references")
    assert candidates == before


@pytest.mark.parametrize("with_role", [False, True])
def test_resolved_generic_io_does_not_open_instruction_retrieval(with_role, monkeypatch):
    from application.context_compiler import ContextCompiler, ContextCompilerInput
    from application.generation_context import _build_knowledge_context
    import knowledge.retriever as retriever
    roles = [("start", "X", "X0"), ("stop", "X", "X1"), ("output", "Y", "Y0")]
    spec = {
        "parameters": [{"id": role, "name": "已确认接线", "value": address} for role, _, address in roles],
        "io_bindings": [{"source_parameter_id": role, "kind": kind, "address": address,
                         **({"role": role} if with_role else {})} for role, kind, address in roles],
        "io_table": [{"kind": kind, "address": address, "label": role} for role, kind, address in roles],
        "selected_approach": {"name": "停止优先自保持"},
    }
    before = copy.deepcopy(spec)
    compiled = ContextCompiler().compile(ContextCompilerInput(confirmed_spec=spec))
    assert compiled.generation_packet["confirmed_spec"] == {**spec, "schema_version": 4}
    assert spec == before
    query = KnowledgeQuery(compiled.retrieval_packet["query"], precompiled=True,
                           metadata={"instruction_fact_mode": "targeted", "instruction_fact_targets": []})
    assert not any(address in query for _, _, address in roles)
    monkeypatch.setattr(retriever, "build_knowledge_context",
                        lambda *a, **k: pytest.fail("settled I/O is not a manual fact question"))
    assert not _build_knowledge_context(query, plc_model="FX3U", confirmed_context=spec)


@pytest.mark.parametrize("question", ["MOV K1 D0", "查询 X0 的输入响应时间", "查证 FX3U-4AD 缓冲存储器"])
def test_explicit_lookup_survives_settled_io_projection(question):
    from application.context_compiler import ContextCompiler, ContextCompilerInput
    from knowledge.analysis_router import has_generation_fact_target
    spec = {"io_bindings": [{"kind": "X", "address": "X0", "source_parameter_id": "start"}],
            "io_table": [{"kind": "X", "address": "X0"}],
            "parameters": [{"id": "start", "name": "启动接线", "value": "X0"},
                           {"id": "timer", "name": "定时时间", "value": "5 秒"}]}
    before = copy.deepcopy(spec)
    compiled = ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=spec, task_type="edit", generation_request=question))
    query = compiled.retrieval_packet["query"]
    assert question in query and "5 秒" in query and has_generation_fact_target(query)
    assert compiled.generation_packet["confirmed_spec"] == {**spec, "schema_version": 4}
    assert spec == before



def test_capability_manifest_tracks_instruction_migration_gaps():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["instruction_step_width"] == "enforced"
    assert states["instruction_contract_promotion"] == "enforced"
    assert states["confirmed_instruction_instances"] == "enforced"
    assert states["instruction_source_authority"] == "enforced"
    assert states["fact_coverage_delivery"] == "enforced"
