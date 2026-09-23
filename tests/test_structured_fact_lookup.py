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
    resolve_instruction_contract,
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


def test_broad_retrieval_uses_unweighted_rrf_without_plc_topic_boosts():
    import knowledge.core as knowledge_core

    broad = inspect.getsource(knowledge_core._retrieve_uncached)
    structured = inspect.getsource(knowledge_core._structured_references)

    assert "_RRF_K" in broad
    assert "1.0 / (_RRF_K + signal[\"rank\"] + 1.0)" in broad
    for legacy in (
        "positioning_query", "timer_query", "timer_device_section",
        "task_boost", "alias_scores", "_base_score", "score +=", "score -=",
    ):
        assert legacy not in broad
        assert legacy not in structured


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
    assert all(row["instruction_step_width"]["known"] is True for row in rows)
    assert all("STEP_WIDTH:" in row["text"] for row in rows)


def test_every_promoted_fx3u_contract_uses_registry_owner():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics

    promoted = [
        opcode
        for opcode in generation_app_instr_mnemonics("FX3U")
        if DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX3U").verified_fields
    ]
    assert len(promoted) > 200
    for opcode in promoted:
        expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu="FX3U")
        actual = resolve_instruction_contract(
            {"opcode": opcode, "base_opcode": expected["base_mnemonic"]},
            plc_model="FX3U",
        )
        assert actual == expected, opcode


@pytest.mark.parametrize("opcode", ["MOV", "WSFL", "DRVA"])
def test_structured_instruction_record_merges_registry_contract(opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    _bundled_index()
    rows = resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu="FX3U")
    assert rows[0]["instruction_contract"] == expected
    assert "INSTRUCTION_CONTRACT:" in rows[0]["text"]
    assert rows[0]["instruction_contract"]["contract_level"] == "signature_verified"


def test_structured_contract_prompt_view_is_compact_but_metadata_keeps_sources():
    rows = resolve_instruction_records(
        [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    row = rows[0]
    assert row["instruction_contract"].get("sources")
    contract_line = next(
        line for line in row["text"].splitlines()
        if line.startswith("INSTRUCTION_CONTRACT:")
    )
    assert '"verified_fields"' in contract_line
    assert '"operand_annotations"' in contract_line
    assert '"sources"' not in contract_line


def test_instruction_instance_contract_keeps_exact_operands_and_source():
    target = {
        "opcode": "SFTL",
        "base_opcode": "SFTL",
        "operands": ["M10", "M100", "K128", "K1"],
        "instance_source": "generation_contract",
    }
    contract = resolve_instruction_contract(target, plc_model="FX3U")
    assert contract["confirmed_operands"] == target["operands"]
    assert contract["instance_source"] == "generation_contract"
    assert contract["native_operand_order"] == ["S", "D", "N1", "N2"]
    assert {"arity", "operand_order", "form_identity"} <= set(contract["verified_fields"])


def test_local_instruction_fallback_keeps_contract_and_step_width_provenance_separate():
    rows = resolve_instruction_records(
        [{"opcode": "RST", "base_opcode": "RST", "operands": ["D10"]}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    row = rows[0]
    assert row["source"] == "local_structured_instruction_owners"
    assert row["instruction_contract"]["opcode"] == "RST"
    assert isinstance(row["instruction_contract"].get("sources"), list)
    assert row["instruction_step_width"]["steps"] == 3
    assert row["instruction_step_width"]["source"] in {"exact_native_form", "operand_rule", "native_observation"}


def test_fx3u_contract_promotions_do_not_leak_into_fx5u_structured_facts():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    expected = DEFAULT_INSTRUCTION_REGISTRY.describe_contract("MOV", cpu="FX5U")
    actual = resolve_instruction_contract({"opcode": "MOV"}, plc_model="FX5U")
    assert actual == expected
    assert actual["verified_fields"] == []
    assert actual["contract_level"] != "signature_verified"


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
        if opcode == "RST":
            assert rows[0]["manual_id"] == "structured_instruction_registry"
            assert rows[0]["source"] == "local_structured_instruction_owners"
            assert rows[0]["instruction_contract"]["opcode"] == "RST"


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


@pytest.mark.parametrize(
    "opcode,manual_id",
    [
        ("DRVA", "fx3_positioning_k"),
        ("DRVI", "fx3_positioning_k"),
        ("DVIT", "fx3_positioning_k"),
        ("PLSV", "fx3_positioning_k"),
        ("ZRN", "fx3_positioning_k"),
        ("TBL", "fx3_programming_r"),
    ],
)
def test_instruction_source_authority_contract_is_data_backed(opcode, manual_id):
    from knowledge.source_authority import (
        authoritative_instruction_manual,
        instruction_source_authority,
    )

    _bundled_index()
    authority = instruction_source_authority(opcode, "FX3U")
    assert authority is not None
    assert authority["manual_id"] == manual_id
    assert authoritative_instruction_manual(opcode, "FX3U") == manual_id

    rows = resolve_instruction_records(
        [{"opcode": opcode, "base_opcode": opcode}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert rows
    assert {row["manual_id"] for row in rows} == {manual_id}
    assert {
        row.get("instruction_source_authority_status") for row in rows
    } == {"authoritative"}


def test_broad_and_direct_instruction_lookup_share_source_authority_owner():
    import knowledge.core as knowledge_core

    source_text = inspect.getsource(knowledge_core)
    assert "audited_instruction_precedence" not in source_text
    assert "authoritative_instruction_manual" in source_text


def test_instruction_source_authority_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["instruction_source_authority"] == "enforced"


def test_device_target_extractor_covers_special_indexed_families():
    from knowledge.analysis_router import route_analysis_request

    route = route_analysis_request(
        "TS0 TC1 CS2 CC3 ER4 R5 P6 I7 SM8 SD9 X10 Y11 M12 D13 V14 Z15"
    )
    assert set(route.devices) == {
        "TS0", "TC1", "CS2", "CC3", "ER4", "R5", "P6", "I7",
        "SM8", "SD9", "X10", "Y11", "M12", "D13", "V14", "Z15",
    }


def test_runtime_device_target_prefixes_cover_bundled_device_index():
    from knowledge.analysis_router import route_analysis_request
    from plc.device_identity import DEVICE_PREFIXES

    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        indexed = {
            str(row[0] or "").upper()
            for row in connection.execute(
                "SELECT DISTINCT prefix FROM device_records "
                "WHERE chunk_id IS NOT NULL AND record_type='device' AND prefix <> ''"
            )
        }

    # N is instruction nesting/operand syntax and is intentionally cleaned out,
    # not a runtime device target. Any other new indexed family must be routed.
    indexed.discard("N")
    assert indexed
    assert indexed <= set(DEVICE_PREFIXES), sorted(
        indexed - set(DEVICE_PREFIXES)
    )
    for prefix in sorted(indexed):
        device = prefix + "0"
        assert device in route_analysis_request(device).devices


def test_structured_fact_targets_carry_extended_device_families():
    targets = structured_fact_targets("查 TS0 TC1 CS2 CC3 ER4 R5 P6 I7")
    assert set(targets["devices"]) == {
        "TS0", "TC1", "CS2", "CC3", "ER4", "R5", "P6", "I7",
    }


def test_router_and_core_share_the_same_device_token_owner():
    import knowledge.analysis_router as analysis_router
    import knowledge.core as knowledge_core
    from plc.device_identity import DEVICE_TOKEN_RE

    assert analysis_router._DEVICE is DEVICE_TOKEN_RE
    assert knowledge_core._DEVICE_RE is DEVICE_TOKEN_RE


def test_runtime_device_vocabulary_covers_actual_index_prefixes():
    from knowledge.analysis_router import route_analysis_request
    from plc.device_identity import DEVICE_PREFIXES

    path = _bundled_index()
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT prefix, MIN(device) FROM device_records "
            "WHERE record_type='device' AND chunk_id IS NOT NULL "
            "GROUP BY prefix ORDER BY prefix"
        ).fetchall()

    assert rows
    actual_prefixes = {str(prefix or "").upper() for prefix, _device in rows if prefix}
    # N rows in the source corpus are operand/nesting placeholders and are
    # intentionally not promoted to runtime device targets.
    assert actual_prefixes - {"N"} <= set(DEVICE_PREFIXES)
    assert "N" not in DEVICE_PREFIXES

    for prefix, device in rows:
        prefix = str(prefix or "").upper()
        if not prefix or prefix == "N":
            continue
        token = str(device or "").upper()
        route = route_analysis_request(f"查 {token} 的软元件定义")
        assert token in route.devices, (prefix, token, route.devices)


def test_device_family_target_coverage_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["device_family_target_coverage"] == "enforced"


def test_router_and_core_share_device_token_owner():
    import knowledge.analysis_router as analysis_router
    import knowledge.core as knowledge_core
    from plc.device_identity import DEVICE_TOKEN_RE

    assert analysis_router._DEVICE is DEVICE_TOKEN_RE
    assert knowledge_core._DEVICE_RE is DEVICE_TOKEN_RE


def test_runtime_device_target_vocabulary_covers_extended_families():
    from knowledge.analysis_router import route_analysis_request

    text = "ER10 SM8000 SD0 TS0 TC0 CS0 CC0 R10 P1 I2"
    route = route_analysis_request(text)
    assert set(route.devices) == {
        "ER10", "SM8000", "SD0", "TS0", "TC0", "CS0", "CC0",
        "R10", "P1", "I2",
    }


def test_runtime_device_target_vocabulary_rejects_constants_and_placeholders():
    from knowledge.analysis_router import route_analysis_request

    route = route_analysis_request("MOV K10 D0，常数 H100、浮点 E1、操作数 N1")
    assert route.devices == ("D0",)


def test_structured_fact_targets_use_full_runtime_device_vocabulary():
    targets = structured_fact_targets(
        "ER10 SM8000 SD0 TS0 TC0 CS0 CC0 R10 P1 I2 K10 H100 E1 N1"
    )
    assert set(targets["devices"]) == {
        "ER10", "SM8000", "SD0", "TS0", "TC0", "CS0", "CC0",
        "R10", "P1", "I2",
    }


def test_exact_device_is_resolved_from_device_records():
    _bundled_index()
    rows = resolve_device_records(["M8029"], plc_model="FX3U", task_type="analysis")
    assert rows
    assert all(row["structured_lookup"] is True for row in rows)
    assert all(row["structured_fact_kind"] == "device" for row in rows)
    assert {row["structured_fact_target"] for row in rows} == {"M8029"}


def test_device_context_uses_generic_fact_coverage():
    _bundled_index()
    import knowledge.retriever as retriever

    context = retriever.build_knowledge_context(
        KnowledgeQuery("M8029"), plc_model="FX3U",
        task_type="analysis", top_k=5, char_budget=7000,
    )
    report = context.manifest["fact_coverage"]
    requirement = next(
        row for row in report["requirements"]
        if row["kind"] == "device"
        and row["target"] == "M8029"
        and row["dimension"] == "definition"
    )
    assert requirement["status"] == "candidate_evidence"
    assert requirement["source_ids"]


def test_device_lookup_uses_canonical_device_identity_owner():
    _bundled_index()

    canonical = resolve_device_records(
        ["M8029"], plc_model="FX3U", task_type="analysis",
    )
    aliased = resolve_device_records(
        ["M08029"], plc_model="FX3U", task_type="analysis",
    )

    assert canonical
    assert aliased
    assert [row["id"] for row in aliased] == [row["id"] for row in canonical]
    assert {row["structured_fact_target"] for row in aliased} == {"M8029"}
    assert {row["structured_fact_requested_target"] for row in aliased} == {"M08029"}


def test_device_aliases_do_not_duplicate_structured_records():
    _bundled_index()
    rows = resolve_device_records(
        ["M08029", "M8029"],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["structured_fact_target"] for row in rows} == {"M8029"}


def test_device_canonical_identity_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["device_canonical_identity"] == "enforced"


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
    assert rows[0]["structured_text_compacted"] is True
    assert rows[0]["instruction_contract"]["opcode"] == "SFTL"
    assert "INSTRUCTION_CONTRACT:" in rows[0]["text"]


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


def test_compact_row_view_does_not_mutate_full_manual_backed_record():
    from knowledge.structured_facts import compact_structured_fact_record

    rows = resolve_instruction_records(
        [{"opcode": "SFTL", "base_opcode": "SFTL"}],
        plc_model="FX3U",
        task_type="analysis",
    )
    assert rows
    full = rows[0]
    compact = compact_structured_fact_record(full)
    assert compact is not full
    assert compact["instruction_contract"] == full["instruction_contract"]
    assert len(compact["text"]) < len(full["text"])
    assert "INSTRUCTION_CONTRACT:" in compact["text"]
    assert full["text"].startswith("[STRUCTURED INSTRUCTION RECORD]")


def test_instruction_contract_delivery_has_one_structured_owner():
    import application.compact_protocol as compact_protocol
    import application.confirmed_generation_context as confirmed_context
    import knowledge.structured_facts as structured_facts

    assert hasattr(structured_facts, "resolve_instruction_contract")
    assert not hasattr(confirmed_context, "selected_instruction_capability_prompt")
    assert compact_protocol.compact_capability_prompt(
        "FX3U",
        {"selected_approach": {"generation_contract": {"required_opcodes": ["MOV"]}}},
    ) == ""



def test_source_subquery_prefilters_instruction_chunks_before_candidate_limit():
    from knowledge.scope import source_subquery

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE chunks (id TEXT, manual_type TEXT, chunk_type TEXT, manual_id TEXT)"
        )
        connection.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?)",
            [
                ("or", "programming", "instruction", "manual"),
                ("general", "programming", "prose", "manual"),
            ],
        )
        schema = {
            "chunks": {
                "name": "chunks",
                "columns": ("id", "manual_type", "chunk_type", "manual_id"),
            }
        }

        sql, values = source_subquery(connection, schema, ("fact",))
        assert {row[0] for row in connection.execute(sql, values)} == {"or", "general"}

        sql, values = source_subquery(
            connection, schema, ("fact",), exclude_chunk_types=("instruction",),
        )
        assert [row[0] for row in connection.execute(sql, values)] == ["general"]
    finally:
        connection.close()


def test_exact_instruction_residual_broad_retrieval_uses_instruction_prefilter(monkeypatch):
    _bundled_index()
    import knowledge.retriever as retriever

    targets = {
        "version": "structured-facts-v2-contract-merged",
        "instructions": [{
            "opcode": "SFTL",
            "base_opcode": "SFTL",
            "operands": ["M100", "M200", "K20", "K1"],
            "instance_source": "generation_contract",
        }],
        "devices": [],
        "errors": [],
    }
    query = KnowledgeQuery(
        "SFTL M100 M200 K20 K1 timer scan cycle",
        precompiled=True,
        metadata={"structured_fact_targets": targets},
    )
    calls = []

    def broad_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs.get("exclude_chunk_types") == ("instruction",)
        return []

    monkeypatch.setattr(retriever, "retrieve_knowledge", broad_lookup)
    context = retriever.build_knowledge_context(
        query, plc_model="FX3U", task_type="generate", top_k=5, char_budget=7000,
    )
    assert len(calls) == 1
    receipt = context.manifest["structured_facts"]
    assert receipt["residual_retrieval"] is True
    assert receipt["residual_pre_filters"] == {
        "exclude_chunk_types": ["instruction"],
    }



def test_retrieval_scorers_have_no_hand_tuned_score_accumulators():
    import ast
    import inspect
    import knowledge.core as knowledge_core

    source = inspect.getsource(knowledge_core)
    tree = ast.parse(source)

    named_score_assignments = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            else:
                targets = [node.target]
            if any(isinstance(target, ast.Name) and target.id == "score" for target in targets):
                named_score_assignments.append(getattr(node, "lineno", 0))

    assert named_score_assignments == []
    assert "base_score" not in source
    assert "score +=" not in source and "score -=" not in source
    assert "_query_is_timer_semantics" not in source
    assert "_timer_debug_case_matches_query" not in source
    assert "positioning_query" not in source
    assert "timer_query" not in source
    assert "_RRF_K" in source
