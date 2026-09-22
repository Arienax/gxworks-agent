"""Architecture tests for exact PLC facts outside the broad RAG scorer."""
import ast
import inspect
import sqlite3

import pytest

from knowledge import core
from knowledge.evidence import KnowledgeQuery
from knowledge.structured_facts import (
    resolve_device_records,
    resolve_error_records,
    resolve_instruction_records,
    resolve_instruction_step_width,
    structured_fact_targets,
    without_structured_targets,
)


def _bundled_index():
    path = core._index_path()
    if core._index_identity(path)[0] == "missing":
        pytest.skip("Bundled knowledge index is not installed")
    return path


def test_direct_fact_module_has_no_broad_retriever_calls():
    import knowledge.structured_facts as facts

    tree = ast.parse(inspect.getsource(facts))
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    assert "retrieve_knowledge" not in calls
    assert "dense_search" not in calls
    assert "_fts_references" not in calls


@pytest.mark.parametrize("opcode", ["SFTL", "ZRN", "MOV"])
def test_exact_instruction_is_resolved_from_structured_table(opcode):
    _bundled_index()
    targets = [{"opcode": opcode, "base_opcode": opcode}]
    rows = resolve_instruction_records(targets, plc_model="FX3U", task_type="generate")
    assert rows
    assert all(row["structured_lookup"] is True for row in rows)
    assert all(row["structured_fact_kind"] == "instruction" for row in rows)
    assert any(str(row.get("instruction_opcode") or "").upper() == opcode for row in rows)
    assert all(row["match_type"] == "structured_direct" for row in rows)


def test_structured_step_width_uses_shared_owner_for_instruction_instance():
    _bundled_index()
    cases = [
        ("RST", ["D10"], 3),
        ("RST", ["T16"], 2),
        ("RST", ["M2"], 1),
        ("SFTL", ["M10", "M100", "K56", "K1"], 9),
    ]
    for opcode, operands, expected in cases:
        rows = resolve_instruction_records(
            [{"opcode": opcode, "base_opcode": opcode, "operands": operands}],
            plc_model="FX3U",
            task_type="generate",
        )
        assert rows, opcode
        fact = rows[0]["instruction_step_width"]
        assert fact["known"] is True
        assert fact["steps"] == expected
        assert fact["resolution"] == "instruction_instance"
        assert fact["operands"] == operands
        assert f"STEP_WIDTH: {expected} program step(s)" in rows[0]["text"]


def test_opcode_only_fixed_width_is_exposed_without_inventing_operands():
    fact = resolve_instruction_step_width(
        {"opcode": "SFTL", "base_opcode": "SFTL"},
        plc_model="FX3U",
    )
    assert fact["known"] is True
    assert fact["steps"] == 9
    assert fact["resolution"] == "fixed_mnemonic"
    assert fact["operand_arity"] == 4
    assert fact["operands"] == []


def test_operand_dependent_opcode_without_operands_stays_unresolved():
    fact = resolve_instruction_step_width(
        {"opcode": "RST", "base_opcode": "RST"},
        plc_model="FX3U",
    )
    assert fact["known"] is False
    assert fact["steps"] is None
    assert fact["resolution"] == "requires_operands"
    assert "operand-dependent" in fact["reason"]


def test_step_width_does_not_borrow_fx3u_catalogue_for_other_cpu():
    fact = resolve_instruction_step_width(
        {"opcode": "SFTL", "base_opcode": "SFTL"},
        plc_model="FX5U",
    )
    assert fact["known"] is False
    assert fact["steps"] is None
    assert fact["resolution"] == "requires_operands"


@pytest.mark.parametrize("opcode", ["DRVI", "ZRN"])
def test_motion_instruction_direct_lookup_prefers_positioning_manual(opcode):
    _bundled_index()
    rows = resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    assert rows[0]["manual_id"] == "fx3_positioning_k"


def test_exact_device_is_resolved_from_device_records():
    _bundled_index()
    rows = resolve_device_records(["M8029"], plc_model="FX3U", task_type="analysis")
    assert rows
    assert all(row["structured_lookup"] is True for row in rows)
    assert all(row["structured_fact_kind"] == "device" for row in rows)
    assert {row["structured_fact_target"] for row in rows} == {"M8029"}


def test_exact_error_is_resolved_from_error_records():
    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT error_code FROM error_records WHERE error_code_norm <> '0000' "
            "AND chunk_id IS NOT NULL ORDER BY pdf_page,id LIMIT 1"
        ).fetchone()
    assert row is not None
    code = str(row[0])
    rows = resolve_error_records([code], plc_model="FX3U", task_type="debug")
    assert rows
    assert all(item["structured_lookup"] is True for item in rows)
    assert all(item["structured_fact_kind"] == "error" for item in rows)


def test_fact_aware_row_api_keeps_exact_instruction_out_of_broad_retrieval(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    def broad_lookup(*args, **kwargs):
        pytest.fail("exact-only SFTL manual lookup must not enter broad RAG")

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    rows = retriever.retrieve_fact_aware_knowledge(
        "SFTL", plc_model="FX3U", task_type="analysis", top_k=5, char_budget=7000,
    )
    assert rows
    assert rows[0]["structured_lookup"] is True
    assert rows[0]["structured_fact_kind"] == "instruction"
    assert rows[0]["structured_fact_target"] == "SFTL"


def test_application_runtime_cannot_bypass_fact_aware_retrieval():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ("src/application", "src/agent_runtime", "src/integrations"):
        for source_file in (root / folder).rglob("*.py"):
            source_text = source_file.read_text(encoding="utf-8")
            if "from knowledge.retriever import retrieve_knowledge" in source_text:
                offenders.append(source_file.relative_to(root).as_posix())
    assert offenders == []


def test_agent_manual_search_uses_fact_aware_entry_point():
    from agent_runtime import plc_tools

    source_text = inspect.getsource(plc_tools._search_plc_manual)
    assert "retrieve_fact_aware_knowledge" in source_text
    assert "retrieve_knowledge(" not in source_text


def test_targeted_generation_context_does_not_call_broad_retriever(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    def broad_lookup(*args, **kwargs):
        pytest.fail("explicit SFTL fact lookup must not enter broad RAG")

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    targets = {
        "version": "structured-facts-v1",
        "instructions": [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        "devices": [],
        "errors": [],
    }
    query = KnowledgeQuery(
        "SFTL",
        precompiled=True,
        metadata={
            "structured_fact_mode": "direct",
            "structured_fact_targets": targets,
            "instruction_fact_mode": "targeted",
            "instruction_fact_targets": targets["instructions"],
        },
    )
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="generate", top_k=5, char_budget=7000,
    )
    assert context
    assert context.manifest["structured_facts"]["residual_retrieval"] is False
    assert context.manifest["instruction_facts"]["retrieval_mode"] == "structured_direct"
    assert "SFTL" in context


def test_explicit_targets_are_removed_before_residual_retrieval():
    targets = {
        "instructions": [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        "devices": ["M8029"],
        "errors": ["1234H"],
    }
    residual = without_structured_targets(
        "FX3U SFTL 配合 M8029，错误码 1234H 时查手册", targets,
    )
    assert "SFTL" not in residual
    assert "M8029" not in residual
    assert "1234H" not in residual
    assert "查手册" in residual


def test_settled_io_bindings_do_not_become_structured_fact_targets():
    spec = {
        "io_bindings": [
            {"kind": "X", "address": "X0", "source_parameter_id": "start"},
            {"kind": "Y", "address": "Y0", "source_parameter_id": "motor"},
        ],
        "io_table": [
            {"kind": "X", "address": "X0"},
            {"kind": "Y", "address": "Y0"},
        ],
        "selected_approach": {
            "generation_contract": {
                "required_opcodes": ["WSFL"],
                "required_devices": ["M0"],
            }
        },
    }
    targets = structured_fact_targets("", spec)
    assert targets["instructions"][0]["opcode"] == "WSFL"
    assert targets["devices"] == []
    assert targets["errors"] == []


def test_confirmed_generation_does_not_lookup_required_devices_as_manual_facts():
    _bundled_index()
    from application.confirmed_generation_context import build_confirmed_generation_context

    spec = {
        "summary": "字寄存器队列",
        "selected_approach": {
            "generation_contract": {
                "required_opcodes": ["WSFL"],
                "required_devices": ["M0", "D10", "D20"],
            }
        },
    }
    context = build_confirmed_generation_context(spec, "FX3U")
    receipt = context.handoff["structured_facts"]
    assert receipt["targets"]["instructions"][0]["opcode"] == "WSFL"
    assert receipt["targets"]["devices"] == []
    assert receipt["targets"]["errors"] == []


def test_confirmed_targets_include_selected_opcode_without_broad_search_text():
    spec = {"selected_approach": {"generation_contract": {"required_opcodes": ["WSFL"]}}}
    targets = structured_fact_targets("", spec)
    assert targets["instructions"][0]["opcode"] == "WSFL"
    assert targets["devices"] == []
    assert targets["errors"] == []
