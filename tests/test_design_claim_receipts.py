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
