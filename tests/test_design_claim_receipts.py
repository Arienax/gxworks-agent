"""An inferred claim is recorded, never invented into evidence and never binding."""

import copy


def _analysis(**extra):
    base = {"summary": "按颜色分选来料",
            "approaches": [{"approach_id": "word", "name": "整字移位", "description": "整字移位",
                            "implementation_semantics": []}]}
    base.update(extra)
    return base


def test_inferred_claim_needs_no_user_span_but_stays_shaped():
    from plc.specification.explicit_constraint_claims import explicit_constraint_claim_details
    claim = {"operation": "forbid", "scope": "global", "inferred": True, "evidence": [],
             "target": {"kind": "device", "values": ["Y0", "Y1"]}}
    assert explicit_constraint_claim_details([claim]) == []
    # Without the flag the model still has to quote the request.
    unquoted = copy.deepcopy(claim)
    unquoted.pop("inferred")
    paths = [item["path"] for item in explicit_constraint_claim_details([unquoted])]
    assert paths == ["$.explicit_constraint_claims[0].evidence"]


def test_inferred_constraint_claim_is_reported_and_never_applied():
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims
    claim = {"operation": "forbid", "scope": "global", "inferred": True, "evidence": [],
             "target": {"kind": "device", "values": ["Y0"]}}
    receipt = compile_explicit_constraint_claims([claim], "来料按颜色分选。", "FX3U")
    assert receipt["operations"] == [] and receipt["accepted"] == []
    assert [row["reason"] for row in receipt["rejected"]] == ["inferred_claim_not_grounded"]
    assert receipt["rejected"][0]["claim"]["evidence"] == []


def test_inferred_execution_claim_is_reported_and_never_applied():
    from plc.execution_intent import compile_execution_intent_claims, execution_intent_claim_violations
    claim = {"inferred": True, "evidence": [],
             "trigger": {"kind": "transition", "source_devices": ["X0"], "from": 0, "to": 1},
             "effect": {"kind": "set", "devices": ["Y0"]}}
    assert execution_intent_claim_violations([claim]) == []
    receipt = compile_execution_intent_claims([claim], "来料按颜色分选。")
    assert receipt["accepted"] == [] and receipt["requirements"] == []
    assert [row["reason"] for row in receipt["rejected"]] == ["inferred_claim_not_grounded"]


def test_dropped_claims_survive_into_the_confirmed_spec_receipt():
    from plc.specification.confirmed import build_review_draft, validate_spec_draft
    rejected = [{"field": "explicit_constraint_claims", "reason": "inferred_claim_not_grounded",
                 "claim": {"operation": "forbid", "target": {"kind": "device", "values": ["Y0"]}}}]
    draft = build_review_draft(_analysis(decision_receipt={"dropped_claims": rejected}), None)
    assert draft["decision_receipt"]["dropped_claims"] == rejected
    issues = validate_spec_draft(draft, "FX3U")
    assert issues["errors"] == []


def test_inferred_claims_allow_omitted_evidence_in_validators_schema_and_repairs():
    from jsonschema import Draft202012Validator
    from application.analysis_repair import apply_analysis_repair, design_submission_schema, plan_analysis_repair
    from plc.execution_intent import execution_intent_claim_violations
    from plc.specification.explicit_constraint_claims import explicit_constraint_claim_details

    explicit_claim = {"operation": "forbid", "scope": "global", "inferred": True,
                      "target": {"kind": "device", "values": ["Y9"]}}
    exec_claim = {"inferred": True,
                  "trigger": {"kind": "transition", "source_devices": ["X9"], "from": 0, "to": 1},
                  "effect": {"kind": "set", "devices": ["Y9"]}}
    assert explicit_constraint_claim_details([explicit_claim]) == []
    assert execution_intent_claim_violations([exec_claim]) == []

    schema = design_submission_schema()
    submission = _analysis(explicit_constraint_claims=[explicit_claim],
                           execution_intent_claims=[exec_claim])
    Draft202012Validator(schema).validate(submission)

    # User text mentions Y0, so an ungrounded claim on Y0 enters repair_targets and can be marked inferred=True.
    requirement = "X0 启动后点亮 Y0。"
    ungrounded = {"operation": "forbid", "scope": "global", "evidence": ["禁止点亮 Y0"],
                  "target": {"kind": "device", "values": ["Y0"]}}
    plan = plan_analysis_repair(_analysis(explicit_constraint_claims=[ungrounded]), requirement, plc_model="FX3U")
    assert [t["path"] for t in plan["targets"]] == ["/explicit_constraint_claims/0"]
    repaired, _ = apply_analysis_repair(
        plan,
        {"repairs": [{"path": "/explicit_constraint_claims/0",
                      "value": [{"operation": "forbid", "scope": "global", "inferred": True,
                                 "target": {"kind": "device", "values": ["Y0"]}}]}]},
        requirement,
        plc_model="FX3U",
    )
    assert repaired["explicit_constraint_claims"][0]["inferred"] is True


def test_design_session_keeps_user_anchored_claims_for_repair_and_preserves_dropped_and_partial_confirm(tmp_path):
    import pytest
    from types import SimpleNamespace
    from agent_runtime.tool_errors import ToolPreconditionError
    from application.design_session import DesignSession
    from application.projects import public_spec_hash
    from application.workspace import atomic_json

    project_record = {"id": "p1", "plc_model": "FX3U", "target_mode": "ladder",
                      "confirmed_spec": None, "confirmed_decision_receipt_id": "dr-1"}

    class FakeProjects:
        def raw_project(self, pid):
            assert pid == "p1"
            return project_record

    def fake_set_spec(pid, draft, *, expected_hash):
        from plc.specification.confirmed import validate_spec_draft
        assert pid == "p1"
        issues = validate_spec_draft(draft, "FX3U")
        if issues["errors"]:
            return {"valid": False, "issues": issues}
        project_record["confirmed_spec"] = copy.deepcopy(draft)
        return {"valid": True, "hash": public_spec_hash(draft), "issues": issues}

    session = DesignSession(SimpleNamespace(state_dir=tmp_path, projects=FakeProjects(), set_spec=fake_set_spec))
    session.start("p1")
    with pytest.raises(ToolPreconditionError) as exc:
        session.guard_generation("p1", {})
    assert exc.value.code == "DESIGN_PENDING"
    assert {"tool": "get_generation_context", "arguments": {"workflow": "direct_generation"}} in exc.value.next_actions
    # Candidate tools themselves cannot silently bypass an unconfirmed Design state; escape must happen on get_generation_context.
    with pytest.raises(ToolPreconditionError) as exc_candidate:
        session.guard_generation("p1", {"workflow": "direct_generation", "user_requirement": "X0 启动 Y0。"},
                                 tool_name="create_program_candidate")
    assert exc_candidate.value.code == "DESIGN_PENDING"
    # Explicit direct_generation with verbatim requirement on get_generation_context escapes an unconfirmed Design state cleanly.
    session.guard_generation("p1", {"workflow": "direct_generation", "user_requirement": "X0 启动 Y0。"},
                             tool_name="get_generation_context")

    requirement = "X0 启动后点亮 Y0。"
    atomic_json(session._path("p1"), {
        "stage": "propose_design_plan",
        "analysis_id": "a1",
        "requirement": requirement,
        "contract_stage": "design",
        "base_hash": public_spec_hash(None),
        "analysis_evidence": {},
    })
    session.mark_read_complete("p1", "a1")
    assert session.is_read_complete("p1", "a1") is True

    # Unanchored Y9 claim is auto-dropped, while user-anchored Y0 claim stays in repair_targets.
    unanchored = {"operation": "forbid", "scope": "global", "evidence": ["禁止 Y9"],
                  "target": {"kind": "device", "values": ["Y9"]}}
    anchored_bad = {"operation": "forbid", "scope": "global", "evidence": ["不存在的原话 Y0"],
                    "target": {"kind": "device", "values": ["Y0"]}}
    step1 = session.propose("p1", {
        "analysis_id": "a1",
        "analysis": _analysis(
            explicit_constraint_claims=[unanchored, anchored_bad],
            missing_info=[
                {"id": "p1", "question": "延时多久", "required": True},
                {"id": "p2", "question": "断电后如何处理", "required": True},
            ],
        ),
    })
    assert step1["status"] == "repair_required"
    assert [t["path"] for t in step1["repair_targets"]] == ["/explicit_constraint_claims/0"]

    # Deleting a user-anchored claim is forbidden; marking it inferred=True while keeping Y0 succeeds and preserves dropped Y9!
    from application.analysis_results import AnalysisProtocolError
    with pytest.raises(AnalysisProtocolError):
        session.propose("p1", {
            "analysis_id": "a1",
            "repair_id": step1["repair_id"],
            "repairs": [{"path": "/explicit_constraint_claims/0", "value": []}],
        })
    step2 = session.propose("p1", {
        "analysis_id": "a1",
        "repair_id": step1["repair_id"],
        "repairs": [{"path": "/explicit_constraint_claims/0",
                     "value": [{"operation": "forbid", "scope": "global", "inferred": True,
                                "target": {"kind": "device", "values": ["Y0"]}}]}],
    })
    assert "draft_id" in step2
    dropped_values = [row["claim"]["target"]["values"] for row in step2["dropped_claims"]]
    assert ["Y9"] in dropped_values and ["Y0"] in dropped_values

    # Partial parameter answers persist on draft and keep confirm_design_plan as next_action.
    partial = session.confirm("p1", step2["draft_id"], "word", {"p1": "5s"})
    assert partial["confirmed"] is False
    assert partial["next_actions"] == [{"tool": "confirm_design_plan",
                                        "arguments": {"draft_id": step2["draft_id"], "approach_id": "word"}}]
    done = session.confirm("p1", step2["draft_id"], "word", {"p2": "清零"})
    assert done["confirmed"] is True
    params = {row["id"]: row["value"] for row in project_record["confirmed_spec"]["parameters"]}
    assert params == {"p1": "5s", "p2": "清零"}


def test_read_delivery_review_and_public_fragment_projection():
    from dataclasses import replace
    import pytest
    from application.instruction_fragments import executable_fragments, materialize_instruction_fragments
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages, received_read_envelope
    from agent_runtime.runtime import _model_tool_value
    from plc.instruction_definition import InstructionFactGroup
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionRegistry

    # 1. Public _model_tool_value strips _DEBUG_FIELDS (like source_verification) without breaking received_read_envelope or FRAG materialization.
    typ = {"kind": "int", "bits": 16, "signed": False}
    behavior = {"behavior": "range_copy", "source": "S", "destination": "D",
                "count": {"op": "parameter", "name": "N", "type": typ}, "element_type": typ,
                "max_count": 32, "read_mode": "snapshot", "required_state": {"M8024": False},
                "scope_limits": {"memory": "ordinary_D_word_regions_with_in_range_addresses"}}
    group = InstructionFactGroup("reviewed-copy", "effects.range_copy", behavior,
                                 "source_verified", scope={"models": ["FX3U"], "languages": ["ladder"]})
    base = DEFAULT_INSTRUCTION_REGISTRY.resolve_form("BMOV", cpu="FX3U").spec
    registry = InstructionRegistry([replace(base, native_operand_order=("S", "D", "N"),
                                            definition_facts=(group,), execution_form="continuous")])
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr("plc.instructions.DEFAULT_INSTRUCTION_REGISTRY", registry)
        plan = {"families": {"block_move": ["BMOV"]}}
        raw_fragments = executable_fragments(plan, plc_model="FX3U", target_mode="ladder")
        raw_env = {"ok": True, "tool": "get_generation_context",
                   "data": {"project_id": "p", "instruction_research": {**plan, "executable_fragments": raw_fragments}}}
        projected_env = _model_tool_value("get_generation_context", raw_env)
        store = ReadDeliveryStore()
        binding = ("local", "p", "v0")
        page = store.prepare(projected_env, {"user_requirement": "搬运数据", "instruction_details": True}, binding)
        checked_page = received_read_envelope("get_generation_context", page)
        assembled = assemble_read_pages([checked_page])
        assert assembled["instruction_research"]["executable_fragments"]
        lowered, receipt = materialize_instruction_fragments(
            {"r": [{"c": ["NO X0"], "b": [{"o": ["FRAG range-bmov D100 D200 K4"]}]}]},
            assembled["instruction_research"],
            plc_model="FX3U",
        )
        assert lowered["r"][-1]["b"][0]["o"] == ["BMOV D100 D200 K4"]
        assert receipt["receipts"][0]["fragment_id"] == "range-bmov"
    finally:
        mp.undo()

    # 2. ReadDeliveryStore.check_review ignores uncited search_plc_manual reads, matches BMOVP -> BMOV, and caches verified_review.
    store2 = ReadDeliveryStore(max_bytes=6000)
    b2 = ("local", "p2", "v0")
    gen_page = store2.prepare(
        {"ok": True, "tool": "get_generation_context", "data": {
            "project_id": "p2", "plc_model": "FX3U", "target_mode": "ladder",
            "coverage": {"targets": [{"kind": "instruction", "target": "BMOVP", "status": "complete", "corpus": "present"}]},
            "generation_input": {"catalog": [{"name": f"n{i}", "body": "x" * 900} for i in range(10)]},
            "instruction_research": {
                "families": {"copy": ["BMOV"]},
                "executable_fragments": [],
            },
        }},
        {"workflow": "direct_generation", "user_requirement": "搬运"},
        b2,
    )
    doc_id = gen_page["data"]["delivery"]["document_id"]
    assert not gen_page["data"]["delivery"]["complete"]
    while nxt := gen_page["data"]["delivery"]["next"]:
        # Accept cursor-only continuation as well as full continuation arguments.
        gen_page = store2.resume("get_generation_context", {"cursor": nxt["arguments"]["cursor"]}, b2)
    # Prepare an incomplete uncited manual search: should NOT block check_review when manual_documents == []!
    manual_page = store2.prepare(
        {"ok": True, "tool": "search_plc_manual", "data": {
            "language": "ladder",
            "results": [{"id": f"r{i}", "text": "chunk-" + ("x" * 900) + str(i)} for i in range(10)],
        }},
        {"query": "unrelated manual search"},
        b2,
    )
    assert not manual_page["data"]["delivery"]["complete"]
    checked_research = store2.check_review(b2, {
        "context_document": doc_id,
        "manual_documents": [],
        "selected": "Use BMOVP on rising edge.",
        "alternatives": [{"method": "MOV", "tradeoff": "Word by word"}],
    })
    assert checked_research["families"] == {"copy": ["BMOV"]}
    # Subsequent retry with review=None reuses verified_review even with an incomplete uncited manual doc!
    reused = store2.check_review(b2, None)
    assert reused["families"] == {"copy": ["BMOV"]}


def test_manual_search_cross_language_routing_and_public_coverage(monkeypatch):
    import json
    from agent_runtime.manual_tools import _language_coverage, _search_plc_manual
    from agent_runtime.tool_context import ToolContext

    # 1. Cross-language explicit query ("ST语言中...") in a Ladder project routes to ST instead of Ladder.
    st_packet = {"content_origin": "authored_fact", "language": "st", "sources": [{"manual_id": "m_st_secret"}], "value": {"facts": []}}
    monkeypatch.setattr("knowledge.retriever.retrieve_fact_aware_knowledge", lambda query, **kw: [
        {"id": "1", "manual_id": "m_st_secret", "chunk_type": "structured_fact", "_explicit_target": True,
         "fact_kind": "language_feature", "fact_target": "ST.Timer", "text": json.dumps(st_packet)},
    ])
    ctx = ToolContext(project={"id": "p", "plc_model": "FX3U", "target_mode": "ladder"})
    out = _search_plc_manual(ctx, {"query": "ST语言中 TON 定时器怎么调用"})
    assert out["language"] == "st"
    assert out["language_source"] == "query"
    lang_cov = out["coverage"]["language"]
    assert "manuals_by_language" not in lang_cov
    assert lang_cov["covered_languages"] == ["st"]

    # 2. Universal device facts without explicit language terms do not report false missing_languages.
    dev_packet = {"content_origin": "authored_fact", "sources": [{"manual_id": "m_dev"}], "value": {"facts": []}}
    cov = _language_coverage(
        [{"_explicit_target": True, "fact_kind": "device", "text": json.dumps(dev_packet)}],
        {"ladder"},
        debug=False,
        explicit_query_langs=frozenset(),
    )
    assert cov["missing_languages"] == []
    assert "manuals_by_language" not in cov
