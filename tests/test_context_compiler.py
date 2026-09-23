from __future__ import annotations

import copy
import json

from application.confirmed_generation_context import project_confirmed_specification
from application.context_compiler import (
    ContextCompiler, ContextCompilerInput, _trim_to_tokens, model_budget,
)
from knowledge.evidence import estimate_tokens
from plc.specification.provenance import (
    fingerprint, mark_request_absorbed, proposal_snapshot, intent_context,
)


def profile(window, output=32768):
    return {
        "capabilityContract": {
            "capabilities": {
                "context_window": {"status": "supported", "source": "catalog", "value": window},
            },
            "parameters": {
                "max_completion_tokens": {
                    "status": "supported", "source": "catalog",
                    "domain": {"minimum": 1, "maximum": output},
                }
            },
        }
    }


def spec(*, note="", guide="Use the selected sequence method.", requests=None):
    requests = requests or [{"id": "r1", "source": "user_request", "text": "Start was X0"},
                            {"id": "r2", "source": "user_request", "text": "Start is now X1"}]
    selected = {
        "approach_id": "a1", "name": "Sequence", "description": "Selected architecture",
        "generation_guide": guide,
        "generation_contract": {"required_structures": ["state_sequence"], "required_opcodes": []},
        "implementation_preferences": {"required_devices": ["D10"], "enforce": False},
    }
    proposal_hash = fingerprint(proposal_snapshot(selected))
    return {
        "summary": "Confirmed sequence",
        "user_notes": note,
        "io_table": [{"kind": "X", "address": "X1", "label": "Start"},
                     {"kind": "Y", "address": "Y0", "label": "Run"}],
        "parameters": [{"name": "delay", "value": "K30"}],
        "selected_approach": selected,
        "engineering_context": {
            "schema_version": 1,
            "requests": requests,
            "proposals": [{"approach_id": "a1", "proposal_sha256": proposal_hash,
                           "source": "model_proposal", "request_ids": [r["id"] for r in requests],
                           "evidence": {"stage": "candidate", "status": "retrieved", "records": []}}],
            "analysis_evidence": {"stage": "analysis", "status": "not_recorded", "records": []},
            "confirmation": {"status": "confirmed", "approach_id": "a1",
                             "selected_sha256": proposal_hash, "selected_origin": "model_proposal",
                             "request_ids": [r["id"] for r in requests]},
        },
    }


def compile(specification, window=1_000_000, evidence=""):
    return ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=specification,
        intent_context=intent_context(specification),
        selected_approach=specification.get("selected_approach", {}),
        model_profile=profile(window), plc_model="FX3U", task_type="generate",
        generation_request="Generate from the confirmed specification.",
    ), evidence_text=evidence)


def test_large_context_low_utilization_only_deduplicates():
    compiled = compile(spec(note="工" * 60_000), window=1_000_000)
    report = compiled.budget_report
    assert report["model_context_window"] == 1_000_000
    assert report["context_utilization"] < .35
    assert report["compression_mode"] == "dedupe_only"
    assert report["context_pressure"] == "low"
    assert report["checkpoint_compaction_required"] is False


def test_small_context_high_utilization_marks_checkpoint_compaction_required():
    compiled = compile(spec(note="工" * 110_000), window=128_000)
    report = compiled.budget_report
    assert report["context_utilization"] > .80
    assert report["compression_mode"] == "dedupe_only"
    assert report["checkpoint_compaction_required"] is True


def test_retrieval_priority_keeps_current_facts_ahead_of_old_requests_and_long_guide():
    compiled = compile(spec(guide="Long selected method " * 20_000))
    query = compiled.retrieval_packet["query"]
    assert "X1" in query.splitlines()
    assert "K30" in query.splitlines()
    assert "io_table[" not in query and "parameters[" not in query
    assert "P0" not in query and "P1" not in query  # priorities are metadata, not PLC-looking query text
    if "Start was X0" in query:
        assert query.index("X1") < query.index("Start was X0")
    assert compiled.retrieval_packet["estimated_tokens"] <= compiled.budget_report["retrieval_query_token_budget"] + 32


def test_duplicate_evidence_is_injected_once_but_source_state_is_not_mutated():
    original = spec()
    before = copy.deepcopy(original)
    block = (
        'Reference role: technical_reference\n'
        '[KNOWLEDGE {"id":"manual-A","source":"manual"}]\n'
        'timer fact\n\nsecond paragraph\n[/KNOWLEDGE]'
    )
    compiled = compile(original, evidence="\n\n".join([block] * 5))
    assert compiled.generation_packet["evidence"].count('"id":"manual-A"') == 1
    assert compiled.budget_report["dropped"]["duplicate_evidence_blocks"] == 4
    assert original == before


def test_compiler_receipt_does_not_reclassify_historical_candidate_evidence():
    value = spec()
    value["engineering_context"]["proposals"][0]["evidence"]["records"] = [{"id": "OLD_CANDIDATE_SOURCE"}]
    compiled = compile(value)
    assert compiled.provenance_receipt["audit_policy"] == "decision_receipt_reference_only"
    assert "OLD_CANDIDATE_SOURCE" not in json.dumps(compiled.to_dict())
    assert "evidence_relation" not in compiled.provenance_receipt


def test_model_budget_reserves_output_and_keeps_retrieval_separate():
    budget = model_budget(profile(1_047_576, output=32_768))
    assert budget["context_window"] == 1_047_576
    assert budget["reserved_output_tokens"] == 32_768
    assert budget["usable_input_tokens"] < budget["context_window"]
    assert budget["retrieval_query_token_budget"] <= 24_000
    assert budget["rag_evidence_token_budget"] <= 32_000



def test_chinese_query_never_exceeds_token_budget():
    text, cut = _trim_to_tokens("顺序控制与队列保持" * 5000, 100)
    assert cut is True
    assert estimate_tokens(text) <= 100
    compiled = compile(spec(guide="顺序控制与队列保持" * 20000))
    assert compiled.retrieval_packet["estimated_tokens"] <= compiled.budget_report["retrieval_query_token_budget"]


def test_historical_audit_is_not_a_budget_dependent_runtime_input():
    value = spec()
    value["engineering_context"]["analysis_evidence"] = {
        "records": [{"id": "OLD_ANALYSIS_SOURCE", "section": "证据" * 400} for _ in range(220)],
    }
    for window in (128_000, 1_000_000):
        compiled = compile(value, window=window)
        assert "OLD_ANALYSIS_SOURCE" not in json.dumps(compiled.to_dict())
        assert "engineering_context" not in compiled.generation_packet["confirmed_spec"]
        assert compiled.budget_report["context_pressure"] == "low"
        assert compiled.generation_packet["confirmed_spec"]["intent_context"]["requests"] == value["engineering_context"]["requests"]


def test_superseded_request_text_is_removed_but_id_is_preserved():
    persistent = mark_request_absorbed(
        spec(), "r1", ["io_table[0].address"], superseded_by="r2",
    )
    projected = project_confirmed_specification(persistent)
    compiled = compile(projected)
    rows = compiled.generation_packet["confirmed_spec"]["intent_context"]["requests"]
    first = next(row for row in rows if row["id"] == "r1")
    assert "text" not in first
    assert first["runtime_text_status"] == "superseded_structured"
    assert first["absorbed_by_confirmed_fields"] == ["io_table[0].address"]
    assert compiled.budget_report["dropped"]["superseded_structured"] == 1


def test_unknown_context_window_is_not_treated_as_low_pressure():
    value = spec()
    compiled = ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=value,
        intent_context=intent_context(value),
        selected_approach=value["selected_approach"],
        model_profile={},
        plc_model="FX3U",
        task_type="generate",
        generation_request="Generate.",
    ))
    assert compiled.budget_report["budget_confidence"] == "unknown"
    assert compiled.budget_report["context_pressure"] == "unknown"
    assert compiled.budget_report["compression_mode"] == "dedupe_only"
    assert compiled.budget_report["context_utilization"] is None


def test_known_zero_usable_budget_is_not_treated_as_unknown():
    model = profile(12_000, output=8192)
    budget = model_budget(model)
    assert budget["budget_confidence"] == "known"
    assert budget["budget_state"] == "unusable"
    assert budget["usable_input_tokens"] == 0
    assert budget["retrieval_query_token_budget"] == 0
    assert budget["rag_evidence_token_budget"] == 0

    value = spec()
    compiled = ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=value,
        intent_context=intent_context(value),
        selected_approach=value["selected_approach"],
        model_profile=model,
        plc_model="FX3U",
        task_type="generate",
        generation_request="Generate.",
    ))
    report = compiled.budget_report
    assert report["budget_confidence"] == "known"
    assert report["budget_state"] == "unusable"
    assert report["context_pressure"] == "critical"
    assert report["compression_mode"] == "dedupe_only"
    assert report["pre_compaction_context_utilization"] is None
    assert report["context_utilization"] is None
    assert report["budget_exceeded_after_compaction"] is True
    assert report["checkpoint_compaction_required"] is True
    assert compiled.retrieval_packet["query"] == ""
    assert compiled.retrieval_packet["estimated_tokens"] == 0


def test_user_selection_controls_reserved_output_tokens():
    """The saved user selection owns the output reservation.

    `generationDefaults` / `requestOverrides` are retired fields: they are read
    only by the legacy migration layer, so a profile carrying them must not
    change the reservation. Only a canonical v3 user selection does.
    """
    model = profile(128_000, output=32_768)
    model["generationDefaults"] = {"max_completion_tokens": 4096}
    model["requestOverrides"] = {"max_completion_tokens": 8192}
    assert model_budget(model)["reserved_output_tokens"] == 32_768

    model["userModelSettings"] = {
        "parameters": {"max_completion_tokens": {"mode": "value", "value": 8192}},
    }
    assert model_budget(model)["reserved_output_tokens"] == 8192


def test_budget_estimate_covers_serialized_generation_packet():
    compiled = compile(spec(note="保留当前工程事实"))
    serialized = json.dumps(
        compiled.generation_packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    assert compiled.budget_report["compiled_generation_payload_tokens"] == estimate_tokens(serialized)
    assert compiled.budget_report["estimated_input_tokens"] >= estimate_tokens(serialized)


def test_generation_budget_uses_the_application_wire_messages_when_renderer_is_bound():
    from application.generation_wire import (
        render_wire_messages, wire_sha256, wire_token_estimate,
    )

    value = spec(note="保留当前工程事实")
    compiler = ContextCompiler()
    compiled = compiler.compile(ContextCompilerInput(
        confirmed_spec=value,
        intent_context=intent_context(value),
        selected_approach=value["selected_approach"],
        model_profile=profile(128_000),
        plc_model="FX3U",
        task_type="generate",
        generation_request="Generate.",
        wire_renderer=lambda runtime, evidence, request, _program, _checkpoint, _history: {
            "messages": render_wire_messages(
                "SYSTEM\n" + json.dumps(runtime, ensure_ascii=False, separators=(",", ":"))
                + "\nEVIDENCE\n" + evidence,
                [{"role": "user", "content": request}],
            )
        },
    ), evidence_text="FACT")

    assert compiled.budget_report["budget_basis"] == "application_wire_messages"
    assert compiled.budget_report["compiled_budget_payload_tokens"] == wire_token_estimate(
        compiled.wire_packet
    )
    assert compiled.provenance_receipt["wire_sha256"] == wire_sha256(
        compiled.wire_packet
    )
    assert compiled.budget_report["estimated_input_tokens"] == (
        wire_token_estimate(compiled.wire_packet)
        + compiled.budget_report["protocol_overhead_tokens"]
    )


def test_evidence_dedup_preserves_knowledge_block_boundaries():
    a = (
        'Reference role: technical_reference\n'
        '[KNOWLEDGE {"id":"A","source":"manual-a"}]\n'
        'shared paragraph\n\nunique A\n[/KNOWLEDGE]'
    )
    b = (
        'Reference role: technical_reference\n'
        '[KNOWLEDGE {"id":"B","source":"manual-b"}]\n'
        'shared paragraph\n\nunique B\n[/KNOWLEDGE]'
    )
    compiled = compile(spec(), evidence="\n\n".join([a, b, a]))
    text = compiled.generation_packet["evidence"]
    assert text.count('"id":"A"') == 1
    assert text.count('"id":"B"') == 1
    assert "unique A" in text and "unique B" in text
    assert compiled.budget_report["dropped"]["duplicate_evidence_blocks"] == 1


def test_exact_duplicate_request_text_is_sent_once_but_ids_survive():
    value = spec(requests=[
        {"id": "r1", "source": "user_request", "text": "停止后保持队列"},
        {"id": "r2", "source": "user_request", "text": "停止后保持队列"},
    ])
    compiled = compile(value)
    rows = compiled.generation_packet["confirmed_spec"]["intent_context"]["requests"]
    assert [row["id"] for row in rows] == ["r1", "r2"]
    assert sum(1 for row in rows if row.get("text") == "停止后保持队列") == 1
    assert rows[1]["runtime_text_status"] == "duplicate_exact"
    assert rows[1]["duplicate_of"] == "r1"
