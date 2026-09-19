from __future__ import annotations

import copy
import json

from application.confirmed_generation_context import project_confirmed_specification
from application.context_compiler import (
    ContextCompiler, ContextCompilerInput, _trim_to_tokens, model_budget,
)
from knowledge.evidence import estimate_tokens
from plc.specification.provenance import (
    fingerprint, mark_request_absorbed, proposal_snapshot,
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
        engineering_context=specification.get("engineering_context", {}),
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
    assert report["semantic_curator_invoked"] is False


def test_small_context_high_utilization_stays_deterministic_and_flags_semantic_eligibility():
    compiled = compile(spec(note="工" * 110_000), window=128_000)
    report = compiled.budget_report
    assert report["context_utilization"] > .80
    assert report["compression_mode"] == "aggressive_deterministic"
    assert report["semantic_curator_eligible"] is True
    assert report["semantic_curator_invoked"] is False


def test_retrieval_priority_keeps_current_facts_ahead_of_old_requests_and_long_guide():
    compiled = compile(spec(guide="Long selected method " * 20_000))
    query = compiled.retrieval_packet["query"]
    assert "io_table[0].address: X1" in query
    assert "parameters[0].value: K30" in query
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


def test_proposal_evidence_relation_distinguishes_exact_and_modified_selection():
    exact_spec = spec()
    assert compile(exact_spec).provenance_receipt["evidence_relation"] == "exact"
    modified = copy.deepcopy(exact_spec)
    modified["selected_approach"]["generation_guide"] += " user edit"
    modified["engineering_context"]["confirmation"]["selected_sha256"] = fingerprint(
        proposal_snapshot(modified["selected_approach"]))
    assert compile(modified).provenance_receipt["evidence_relation"] == "modified_since_retrieval"


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


def test_aggressive_mode_actually_reduces_runtime_context():
    value = spec()
    value["engineering_context"]["analysis_evidence"] = {
        "stage": "analysis", "status": "retrieved",
        "records": [
            {"id": f"manual-{index}", "source": "manual", "section": "证据" * 400}
            for index in range(220)
        ],
    }
    compiled = compile(value, window=128_000)
    report = compiled.budget_report
    assert report["context_pressure"] in {"high", "critical"}
    assert report["compression_mode"] == "aggressive_deterministic"
    assert report["compiled_generation_payload_tokens"] < report["original_generation_payload_tokens"]
    assert report["compaction_saved_tokens"] > 0
    assert "analysis_evidence" not in compiled.generation_packet["confirmed_spec"]["engineering_context"]


def test_superseded_request_text_is_removed_but_id_is_preserved():
    persistent = mark_request_absorbed(
        spec(), "r1", ["io_table[0].address"], superseded_by="r2",
    )
    projected = project_confirmed_specification(persistent)
    compiled = compile(projected)
    rows = compiled.generation_packet["confirmed_spec"]["engineering_context"]["requests"]
    first = next(row for row in rows if row["id"] == "r1")
    assert "text" not in first
    assert first["runtime_text_status"] == "superseded_structured"
    assert first["absorbed_by_confirmed_fields"] == ["io_table[0].address"]
    assert compiled.budget_report["dropped"]["superseded_structured"] == 1


def test_unknown_context_window_is_not_treated_as_low_pressure():
    value = spec()
    compiled = ContextCompiler().compile(ContextCompilerInput(
        confirmed_spec=value,
        engineering_context=value["engineering_context"],
        selected_approach=value["selected_approach"],
        model_profile={},
        plc_model="FX3U",
        task_type="generate",
        generation_request="Generate.",
    ))
    assert compiled.budget_report["budget_confidence"] == "unknown"
    assert compiled.budget_report["context_pressure"] == "unknown"
    assert compiled.budget_report["compression_mode"] == "unknown_budget"
    assert compiled.budget_report["context_utilization"] is None


def test_request_override_controls_reserved_output_tokens():
    model = profile(128_000, output=32_768)
    model["generationDefaults"] = {"max_completion_tokens": 4096}
    model["requestOverrides"] = {"max_completion_tokens": 8192}
    budget = model_budget(model)
    assert budget["reserved_output_tokens"] == 8192


def test_budget_estimate_covers_serialized_generation_packet():
    compiled = compile(spec(note="保留当前工程事实"))
    serialized = json.dumps(
        compiled.generation_packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    assert compiled.budget_report["compiled_generation_payload_tokens"] == estimate_tokens(serialized)
    assert compiled.budget_report["estimated_input_tokens"] >= estimate_tokens(serialized)


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
    rows = compiled.generation_packet["confirmed_spec"]["engineering_context"]["requests"]
    assert [row["id"] for row in rows] == ["r1", "r2"]
    assert sum(1 for row in rows if row.get("text") == "停止后保持队列") == 1
    assert rows[1]["runtime_text_status"] == "duplicate_exact"
    assert rows[1]["duplicate_of"] == "r1"
