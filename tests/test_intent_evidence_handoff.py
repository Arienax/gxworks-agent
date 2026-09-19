"""Source handoff across A/review/RAG/B/tools/repair/delivery, without live models."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.analysis_results import _normalize_analysis_result, attach_analysis_evidence
from application.confirmed_generation_context import build_confirmed_generation_context, project_confirmed_specification
from application.generation_support import _build_knowledge_query
from knowledge.evidence import KnowledgeContext, context_manifest, evidence_record
from plc.specification.approach import normalize_approach, normalize_generation_contract
from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec
from plc.specification.provenance import confirm_context, fingerprint, retrieval_projection
from shared.context_policy import context_policy_scope

CASES = json.loads((Path(__file__).parent / "fixtures/call_chain/intent_cases.json").read_text())["cases"]


def _evidence(stage="generate", marker="manual-source"):
    row = {"id": marker, "source": "Synthetic engineering manual", "manual_type": "programming",
           "manual_id": "fixture", "page": "42", "text": "Fixture fact, not a verified recipe."}
    return KnowledgeContext("[KNOWLEDGE " + marker + "]\nFixture fact\n[/KNOWLEDGE]",
                            {"stage": stage, "status": "retrieved", "records": [evidence_record(row)]})


def _analysis(case=CASES[0]):
    # Forged metadata must be replaced from the actual caller, not trusted.
    raw = {"summary": "工件控制", "missing_info": [], "assumptions": [],
           "suggested_io": {"X": {"X0": "启动"}, "Y": {"Y0": "输出"}},
           "engineering_context": {"requests": [{"id": "forged", "source": "user_request", "text": "FORGED_REQUEST"}]},
           "approaches": [{"approach_id": case["id"], "name": case["id"], "description": "候选数据表示",
                           "generation_guide": case["guide"],
                           "generation_contract": {"required_opcodes": [], "forbidden_opcodes": [case["forbidden"]],
                                                   "required_devices": case["required_devices"]}},
                          {"approach_id": "unselected", "name": "ALTERNATIVE_ONLY", "generation_guide": "ALTERNATIVE_ONLY",
                           "generation_contract": {"required_opcodes": []}}]}
    return _normalize_analysis_result(raw, "FX3U", case["request"])


def _confirmed(case=CASES[0]):
    analysis = attach_analysis_evidence(_analysis(case), _evidence("analysis", "analysis-source"),
                                        knowledge_builder=lambda *a, **kw: _evidence("candidate", "candidate-source"))
    return confirm_context(canonicalize_confirmed_spec(build_review_draft(analysis)))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_representation_intent_survives_without_becoming_an_opcode_constraint(case):
    spec = _confirmed(case)
    before = copy.deepcopy(spec)
    calls = []
    def retrieve(request, **kwargs):
        calls.append(kwargs)
        return _evidence()
    context = build_confirmed_generation_context(spec, "FX3U", user_requirement="UNCONFIRMED_INPUT",
                                                 knowledge_builder=retrieve)
    assert spec == before
    assert context.confirmed_spec["selected_approach"]["generation_guide"] == case["guide"]
    assert context.confirmed_spec["engineering_context"]["requests"][0]["text"] == case["request"]
    assert context.generation_contract["required_opcodes"] == []
    assert context.generation_contract["forbidden_opcodes"] == []
    assert context.confirmed_spec["selected_approach"]["implementation_preferences"]["forbidden_opcodes"] == [case["forbidden"]]
    serialized = json.dumps(context.to_dict(), ensure_ascii=False)
    assert "FORGED_REQUEST" not in serialized and "ALTERNATIVE_ONLY" not in serialized
    assert "UNCONFIRMED_INPUT" not in serialized
    assert context.handoff["confirmation"]["selected_origin"] == "model_proposal"
    assert context.handoff["generation_evidence"]["records"][0]["id"] == "manual-source"
    assert len(calls) == 1
    assert canonicalize_confirmed_spec(spec) == spec
    assert confirm_context(spec) == spec
    exported = context.to_dict()
    exported["confirmed_spec"]["engineering_context"]["requests"][0]["text"] = "changed"
    assert context.confirmed_spec["engineering_context"]["requests"][0]["text"] == case["request"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_explicit_partial_contract_never_fills_omitted_fields_from_guide(case):
    contract = normalize_generation_contract({"required_devices": case["required_devices"]},
                                            approach={"generation_guide": case["guide"]})
    assert contract["required_opcodes"] == []
    assert contract["forbidden_opcodes"] == []
    assert contract["required_structures"] == []
    assert normalize_generation_contract(contract, approach={"generation_guide": "INC D90"}) == contract


def test_legacy_inference_stays_compatible_and_keeps_inferred_origin():
    approach = normalize_approach({"name": "旧方案", "generation_guide": "MOV K1 D0 更新状态"})
    assert approach["generation_contract"]["required_opcodes"] == []
    assert "MOV" in approach["generation_contract"]["unverified_constraints"]["required_opcodes"]
    assert approach["generation_guide"] == "MOV K1 D0 更新状态"
    assert approach["generation_contract"]["source"] == "inferred"
    assert normalize_approach(approach)["generation_contract"]["source"] == "inferred"
    legacy = build_confirmed_generation_context({"summary": "legacy", "selected_approach": approach}, "FX3U",
                                              knowledge_builder=lambda *a, **kw: "")
    assert legacy.handoff["origin_status"] == "legacy_unrecorded"
    assert legacy.handoff["request_ids"] == []


def test_confirmation_distinguishes_user_modified_plan_and_keeps_original_origin_record():
    draft = build_review_draft(_analysis())
    assert draft["engineering_context"]["confirmation"]["status"] == "draft"
    original_hash = draft["engineering_context"]["proposals"][0]["proposal_sha256"]
    draft["selected_approach"]["generation_guide"] = "USER_APPROVED_CHANGE"
    draft["io_table"][0]["address"] = "X2"
    spec = confirm_context(canonicalize_confirmed_spec(draft))
    assert spec["engineering_context"]["confirmation"]["selected_origin"] == "user_modified_proposal"
    assert spec["engineering_context"]["proposals"][0]["proposal_sha256"] == original_hash
    projected = project_confirmed_specification(spec)
    assert projected["io_table"][0]["address"] == "X2"
    assert projected["selected_approach"]["generation_guide"] == "USER_APPROVED_CHANGE"


def test_new_analysis_retains_original_request_but_resets_confirmation():
    first = _confirmed()
    newer = _normalize_analysis_result({"summary": "修订", "approaches": [], "suggested_io": {}},
                                       "FX3U", "暂停现在也需要清空队列。", first)
    draft = build_review_draft(newer, first)
    assert [r["text"] for r in draft["engineering_context"]["requests"]] == [CASES[0]["request"], "暂停现在也需要清空队列。"]
    assert draft["engineering_context"]["confirmation"]["status"] == "draft"
    assert first["engineering_context"]["confirmation"]["status"] == "confirmed"


def test_generation_query_has_selected_semantics_not_forbidden_arrays_or_alternatives():
    spec = _confirmed(CASES[2])
    query = _build_knowledge_query(retrieval_projection(spec))
    assert CASES[2]["guide"] in query
    assert CASES[2]["request"] in query
    assert CASES[2]["forbidden"] not in query and "ALTERNATIVE_ONLY" not in query
    assert "proposal_sha256" not in query and "candidate-source" not in query


def test_query_preserves_late_intent_numeric_values_and_reports_global_truncation():
    original = "需要保持工件对应关系。" * 100 + "LATE_ENGINEERING_REQUIREMENT"
    query = _build_knowledge_query(original, {"value": 0, "delay_ms": 2000})
    assert query.endswith("0\n2000") and "LATE_ENGINEERING_REQUIREMENT" in query
    assert not query.truncated
    bounded = _build_knowledge_query("START_MARKER" + "x" * 40000 + "END_MARKER", char_limit=2000)
    assert len(bounded) <= 2000 and bounded.truncated
    assert "START_MARKER" in bounded and "END_MARKER" in bounded


def test_fact_lane_is_not_starved_by_large_design_reference(monkeypatch):
    import knowledge.retriever as retriever
    calls = []
    def facts(*a, **kw):
        calls.append(kw)
        return [{"id": f"fact-{i}", "source": "manual", "text": "fact", "manual_type": "programming"} for i in range(3)]
    monkeypatch.setattr(retriever, "retrieve_knowledge", facts)
    monkeypatch.setattr(retriever, "retrieve_design_knowledge", lambda *a, **kw: [
        {"id": "large-design", "source": "curated", "manual_type": "curated_design", "text": "D" * 3000}])
    context = retriever.build_knowledge_context("FX3U", task_type="analysis", top_k=4, char_budget=1500, include_design=True)
    assert 3 <= calls[0]["top_k"] <= retriever._MAX_TOP_K
    assert all(f"fact-{i}" in context for i in range(3))
    assert "large-design" not in context
    assert len(context) <= 1500 and context.manifest["status"] == "budget_limited"
    assert context.manifest["omitted_ids"] == ["large-design"]
    assert {r["id"] for r in context.manifest["records"]} == {f"fact-{i}" for i in range(3)}


@pytest.mark.parametrize("status", ["unavailable", "empty_or_unavailable", "excluded", "budget_limited"])
def test_missing_or_bounded_evidence_is_not_a_contract_error(status):
    spec = _confirmed()
    context = build_confirmed_generation_context(spec, "FX3U", knowledge_builder=lambda *a, **kw:
        KnowledgeContext("", {"stage": "generate", "status": status, "records": []}))
    assert context.handoff["generation_evidence"]["status"] == status
    assert context.generation_contract["required_opcodes"] == []


def test_analysis_evidence_is_bound_after_model_output_and_candidate_lookup_is_bounded():
    result = _analysis()
    calls = []
    def builder(*a, **kw):
        calls.append(kw["confirmed_context"]["selected_approach"]["approach_id"])
        return _evidence("generate", calls[-1])
    result = attach_analysis_evidence(result, _evidence("analysis"), knowledge_builder=builder)
    assert calls == [CASES[0]["id"], "unselected"]
    assert result["engineering_context"]["proposals"][0]["evidence"]["stage"] == "candidate"
    assert result["engineering_context"]["proposals"][1]["evidence"]["records"][0]["id"] == "unselected"
    selected = project_confirmed_specification(build_review_draft(result))
    assert len(selected["engineering_context"]["proposals"]) == 1


def test_one_model_call_receives_original_intent_selected_plan_and_source_manifest(tmp_path, monkeypatch):
    from application.generation import GenerationWorkflow, GenerationRequest, GenerationDependencies
    import application.generation_agent as agent
    from test_generation_agent_boundary import OneShotProvider
    provider = OneShotProvider()
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **kw: _evidence())
    spec = _confirmed(CASES[2])
    metadata = GenerationWorkflow(GenerationRequest(user_input="UNCONFIRMED_TEXT", confirmed_context=spec,
        conversation_history=[{"role": "assistant", "content": "ANALYSIS_REASONING_SENTINEL"}],
        model_name="offline-one-shot"), tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1
    prompt = "\n".join(m.content for m in provider.requests[0].messages)
    assert CASES[2]["request"] in prompt and CASES[2]["guide"] in prompt
    assert "ALTERNATIVE_ONLY" not in prompt and "ANALYSIS_REASONING_SENTINEL" not in prompt
    assert "UNCONFIRMED_TEXT" not in prompt
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation_profile"] == "generation_structural"
    assert metadata["generation_handoff"]["confirmed_spec_sha256"] == fingerprint(spec)
    assert metadata["generation_handoff"]["generation_evidence"]["records"][0]["id"] == "manual-source"
    assert all((tmp_path / name).exists() for name in metadata["artifacts"].values())


def test_full_wire_and_mcp_share_projection_and_do_not_truncate_tool_json(monkeypatch):
    import application.model_api as api
    import application.generation_context as generation_context
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from agent_runtime.runtime import InProcessToolRuntime
    from agent_runtime.messages import ToolCall
    spec = _confirmed(CASES[2])
    spec["engineering_context"]["requests"][0]["text"] += " preserved intent " * 1800
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **kw: _evidence())
    monkeypatch.setattr(generation_context, "_build_knowledge_context", lambda *a, **kw: _evidence())
    receipts = []
    messages, _, _ = api._prepare_api_call("LOOSE_RAW_TEXT", None, None, "ladder", confirmed_context=spec,
        conversation_history=[{"role": "assistant", "content": "PRIVATE_ANALYSIS"}], on_generation_context=receipts.append)
    assert "PRIVATE_ANALYSIS" not in json.dumps(messages) and "LOOSE_RAW_TEXT" not in json.dumps(messages)
    context = build_tool_context({"id": "fixture", "plc_model": "FX3U", "target_mode": "ladder", "confirmed_spec": spec})
    result = InProcessToolRuntime(build_default_tool_registry()).invoke(
        ToolCall(id="context", name="get_generation_context", arguments={}), context)
    assert not result.is_error
    assert len(result.content) > 18000
    payload = json.loads(result.content)
    data = payload.get("data", payload)
    assert data["confirmed_spec"] == project_confirmed_specification(spec)
    assert data["generation_handoff"] == receipts[0]
    assert "ALTERNATIVE_ONLY" not in result.content


def test_private_fields_and_credential_text_are_not_reintroduced():
    spec = _confirmed()
    spec["selected_approach"]["reasoning_content"] = "HIDDEN_REASONING"
    spec["engineering_context"]["requests"][0]["api_key"] = "SECRET_FIELD"
    spec["engineering_context"]["requests"][0]["text"] = "保留意图 api_key=sk-secret-fixture path C:\\Users\\private\\file"
    projected = project_confirmed_specification(spec)
    text = json.dumps(projected, ensure_ascii=False)
    assert "HIDDEN_REASONING" not in text and "SECRET_FIELD" not in text and "sk-secret-fixture" not in text
    assert "保留意图" in text and "private\\\\file" not in text


def test_repair_plan_carries_existing_contract_and_refs_without_changing_scope():
    from plc.specification.repair import build_contract_repair_plan
    from test_generation_agent_boundary import _ladder
    spec = _confirmed(CASES[2])
    spec["selected_approach"]["generation_contract"]["required_opcodes"] = ["MOV"]
    spec["selected_approach"]["generation_contract"]["required_devices"] = ["Y0"]
    plan = build_contract_repair_plan(_ladder(), spec)
    assert plan["repairability"] == "scoped_patch"
    assert plan["allowed_rung_ids"] == [1]
    assert plan["allowed_addresses"] == ["X0", "Y0"]
    assert '"required_opcodes": ["MOV"]' in plan["prompt"]
    assert plan["source_handoff"]["request_ids"] == [spec["engineering_context"]["requests"][0]["id"]]
    assert CASES[2]["request"] not in plan["prompt"]  # no unrelated raw request replay in repair
    assert "ALTERNATIVE_ONLY" not in plan["prompt"]


def test_confirmation_generation_save_and_delivery_preserve_same_receipt(tmp_path, monkeypatch):
    from application.delivery import delivery_summary
    from application.workbench import WorkbenchService
    from fastapi.testclient import TestClient
    from test_web_api import _Provider, ORIGIN, _app, _login, _complete
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **kw: _evidence())
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (_Provider(), {"model": "offline"}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", json={"name": "Handoff"}, headers=headers).json()["id"]
        draft = build_review_draft(_analysis(CASES[2]))
        service.store.set_confirmed_spec(pid, draft)
        confirmed = service.store.get_project(pid)["confirmed_spec"]
        assert confirmed["engineering_context"]["confirmation"]["status"] == "confirmed"
        _, output = _complete(client, service, client.post("/api/jobs", headers=headers,
            json={"project_id": pid, "kind": "generation", "request_id": "handoff-generation", "text": "Generate", "response_language": "en"}))
        assert output["status"] == "saved"
        version = service.store.get_version(pid, output["version_id"])
        assert version["confirmed_spec_snapshot"] == confirmed
        assert version["generation_handoff"]["confirmed_spec_sha256"] == version["confirmed_spec_hash"]
        receipt = copy.deepcopy(version["generation_handoff"])
        newer = copy.deepcopy(confirmed)
        newer["user_notes"] = "NEW_SPEC_NOT_FOR_OLD_VERSION"
        service.store.set_confirmed_spec(pid, newer)
        delivered = delivery_summary(service, pid, version["id"])
        assert delivered["generation_handoff"] == receipt
        assert "NEW_SPEC_NOT_FOR_OLD_VERSION" not in delivered["markdown"]
        assert "manual\\-source" in delivered["markdown"]
        assert "不证明每条需求已实现" in delivered["markdown"]
        assert delivered["validation_profile"] == "generation_structural"


def test_external_context_receipt_survives_candidate_save_without_claiming_model_use(tmp_path, monkeypatch):
    import application.generation_context as gc
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from application.proposals import ProposalService
    from application.workspace import WorkspaceWriterLock
    from storage.session import SessionStore
    from test_generation_agent_boundary import _ladder
    monkeypatch.setattr(gc, "_build_knowledge_context", lambda *a, **kw: _evidence())
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("External handoff")
    store.set_confirmed_spec(project["id"], _confirmed())
    context = build_tool_context(store.get_project(project["id"]))
    registry = build_default_tool_registry()
    returned = registry.call("get_generation_context", {}, context)["data"]
    candidate = registry.call("create_program_candidate", {
        "ladder": _ladder(), "generation_context_id": returned["generation_context_id"],
    }, context)
    assert candidate["ok"] and candidate["status"] == "confirmation_required"
    receipt = candidate["data"]["generation_handoff"]
    assert receipt["generation_evidence"] == returned["generation_handoff"]["generation_evidence"]
    assert receipt["external_model_use"] == "not_observed"
    lock = WorkspaceWriterLock(store.base_dir, tmp_path / "locks").acquire()
    try:
        service = ProposalService(store, tmp_path / "state", lock)
        proposal = service.create("accept_local", project["id"], candidate["data"]["pending_action"])
        accepted = service.accept(proposal["id"])
        version = store.get_version(project["id"], accepted["result"]["version_id"])
        assert version["generation_handoff"] == receipt
        assert version["validation_profile"] == "generation_structural"
    finally:
        lock.release()


@pytest.mark.parametrize("missing", ["omitted", "unknown", "stale"])
def test_external_missing_or_stale_receipt_is_a_trace_gap_not_a_generation_gate(monkeypatch, missing):
    import application.generation_context as gc
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from test_generation_agent_boundary import _ladder
    monkeypatch.setattr(gc, "_build_knowledge_context", lambda *a, **kw: _evidence())
    project = {"id": "fixture", "plc_model": "FX3U", "target_mode": "ladder", "confirmed_spec": _confirmed()}
    context = build_tool_context(project)
    registry = build_default_tool_registry()
    known = registry.call("get_generation_context", {}, context)["data"]["generation_context_id"]
    arguments = {"ladder": _ladder()}
    if missing == "unknown":
        arguments["generation_context_id"] = "not-issued"
    if missing == "stale":
        arguments["generation_context_id"] = known
        project["confirmed_spec"]["user_notes"] = "New confirmation"
        context = build_tool_context(project)
    result = registry.call("create_program_candidate", arguments, context)
    assert result["ok"] and result["status"] == "confirmation_required"
    assert result["data"]["generation_handoff"]["generation_evidence"]["status"] == "not_recorded"
    assert result["data"]["validation_profile"] == "generation_structural"


def test_receipt_cache_is_bounded_and_detached():
    from application.generation_receipts import GenerationReceiptCache
    cache = GenerationReceiptCache(capacity=1)
    source = {"value": [1]}
    one = cache.remember("binding", source)
    source["value"].append(2)
    assert cache.resolve(one, "binding") == {"value": [1]}
    assert cache.resolve(one, "another-version") is None
    cache.resolve(one, "binding")["value"].append(3)
    assert cache.resolve(one, "binding") == {"value": [1]}
    cache.remember("binding", {"new": True})
    assert cache.resolve(one, "binding") is None


@pytest.mark.parametrize("receipt_file", ["present", "absent", "damaged"])
def test_rejected_candidate_keeps_generation_origin_without_fresh_retrieval(tmp_path, receipt_file):
    from application.jobs import JobManager
    from application.workspace import WorkspaceWriterLock
    from plc.candidate_repair import GenerationValidationError
    from storage.session import SessionStore
    from test_rejected_generation_delivery import BROKEN_COMPACT
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("Rejected source handoff")
    store.set_confirmed_spec(project["id"], _confirmed())
    frozen = store.get_project(project["id"])
    receipt = build_confirmed_generation_context(frozen["confirmed_spec"], "FX3U",
        knowledge_builder=lambda *a, **kw: _evidence()).handoff
    state = tmp_path / "state"
    lock = WorkspaceWriterLock(store.base_dir, tmp_path / "locks").acquire()
    manager = JobManager(state, lock, max_workers=1)
    try:
        def worker(ctx):
            staging = state / "staging" / ctx.job_id
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "repair_candidate.json").write_text(BROKEN_COMPACT, encoding="utf-8")
            if receipt_file != "absent":
                (staging / "generation_handoff.json").write_text(
                    json.dumps(receipt) if receipt_file == "present" else "{broken", encoding="utf-8")
            raise GenerationValidationError([ValueError("fixture error")], attempts=0, max_attempts=0,
                                            language="zh-CN", stop_reason="final_validation")
        submitted = manager.submit("generation", {"project_id": project["id"], "project": frozen,
            "version": None, "version_id": None, "program_ir": None}, worker)
        manager._futures[submitted["id"]].result(timeout=10)
        result = manager.get(submitted["id"])["result"]
        assert result["status"] == "saved_invalid"
        saved = store.get_version(project["id"], result["version_id"])
        handoff = saved["generation_handoff"]
        assert handoff["confirmed_spec_sha256"] == saved["confirmed_spec_hash"]
        if receipt_file == "present":
            assert handoff["generation_evidence"] == receipt["generation_evidence"]
        else:
            assert handoff["generation_evidence"]["status"] == "not_recorded"
        assert saved["validation"]["status"] == "invalid_candidate"
        assert saved["validation_profile"] == "generation_structural"
    finally:
        manager.shutdown(wait=True)
        lock.release()


def test_optional_legacy_source_fields_do_not_become_required_inputs():
    spec = {"summary": "legacy", "engineering_context": {"requests": None, "proposals": None,
            "analysis_evidence": None, "confirmation": None}, "selected_approach": None}
    context = build_confirmed_generation_context(confirm_context(spec), "FX3U", knowledge_builder=lambda *a, **kw: "")
    assert context.confirmed_spec["engineering_context"]["requests"] == []
    assert context.handoff["request_ids"] == []


def test_context_receipt_hashes_the_sanitized_text_actually_given_to_generation():
    from knowledge.evidence import text_sha256
    raw = _evidence()
    raw = KnowledgeContext(str(raw) + " api_key=private-fixture C:/private/manual.txt", raw.manifest)
    before = copy.deepcopy(raw.manifest)
    context = build_confirmed_generation_context(_confirmed(), "FX3U", knowledge_builder=lambda *a, **kw: raw)
    evidence = context.handoff["generation_evidence"]
    assert "private-fixture" not in context.knowledge_context
    assert "C:/private" not in context.knowledge_context
    assert evidence["context_sha256"] == text_sha256(context.knowledge_context)
    assert evidence["context_sha256"] != text_sha256(raw)
    assert evidence["records"] == before["records"]  # Source identity is not rewritten.
    assert raw.manifest == before


def test_optional_receipt_binding_uses_public_engineering_snapshot_not_private_objects(monkeypatch):
    import application.generation_context as gc
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from test_generation_agent_boundary import _ladder
    monkeypatch.setattr(gc, "_build_knowledge_context", lambda *a, **kw: _evidence())
    spec = _confirmed()
    spec["parameters"].append({"name": "delay", "value": "K10", "widget": object()})
    spec["selected_approach"]["provider"] = object()
    context = build_tool_context({"id": "private-objects", "plc_model": "FX3U", "confirmed_spec": spec})
    registry = build_default_tool_registry()
    result = registry.call("get_generation_context", {}, context)
    assert result["ok"], result
    public = result["data"]
    assert public["generation_context_id"]
    assert "widget" not in json.dumps(public) and "provider" not in json.dumps(public)
    # Serialization-only host metadata must not invalidate the exposed snapshot.
    from application.confirmed_generation_context import project_confirmed_specification
    clean_context = build_tool_context({"id": "private-objects", "plc_model": "FX3U",
                                       "confirmed_spec": project_confirmed_specification(spec)})
    candidate = registry.call("create_program_candidate", {
        "ladder": _ladder(), "generation_context_id": public["generation_context_id"],
    }, clean_context)
    assert candidate["ok"], candidate
    assert candidate["data"]["generation_handoff"]["external_context_id"] == public["generation_context_id"]


@pytest.mark.parametrize("change", ["project", "version", "program", "parameter", "guide"])
def test_receipt_cannot_cross_a_changed_engineering_binding(monkeypatch, change):
    import application.generation_context as gc
    from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
    from test_generation_agent_boundary import _ladder
    monkeypatch.setattr(gc, "_build_knowledge_context", lambda *a, **kw: _evidence())
    project = {"id": "bound-receipt", "plc_model": "FX3U", "confirmed_spec": _confirmed()}
    program = _ladder()
    registry = build_default_tool_registry()
    context = build_tool_context(project, ladder=program)
    issued = registry.call("get_generation_context", {}, context)["data"]
    version = None
    if change == "project":
        project["id"] = "other-project"
    elif change == "version":
        version = {"id": "other-version", "confirmed_spec_snapshot": copy.deepcopy(project["confirmed_spec"])}
    elif change == "program":
        program["device_comments"]["Y0"] = "Changed output purpose"
    elif change == "parameter":
        project["confirmed_spec"]["parameters"].append({"name": "delay", "value": "K20"})
    else:
        project["confirmed_spec"]["selected_approach"]["generation_guide"] += " 明确修订"
    changed = build_tool_context(project, version=version, ladder=program)
    candidate = registry.call("create_program_candidate", {
        "ladder": _ladder(), "generation_context_id": issued["generation_context_id"],
    }, changed)
    assert candidate["ok"] and candidate["status"] == "confirmation_required", candidate
    receipt = candidate["data"]["generation_handoff"]
    assert receipt["generation_evidence"]["status"] == "not_recorded"
    assert "external_context_id" not in receipt
