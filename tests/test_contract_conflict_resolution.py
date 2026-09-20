from plc.specification.approach import (
    contract_definition_issues,
    normalize_generation_contract,
)


def test_explicit_required_opcode_overrides_inferred_forbidden_opcode():
    contract = normalize_generation_contract(
        {"required_opcodes": ["MOV"]},
        approach={"generation_guide": "禁止 MOV，使用其他方式"},
    )

    assert contract["required_opcodes"] == ["MOV"]
    assert "MOV" not in contract["forbidden_opcodes"]
    assert contract["source"] == "explicit"
    assert not contract.get("normalization_warnings")


def test_explicit_forbidden_opcode_overrides_inferred_required_opcode():
    contract = normalize_generation_contract(
        {"forbidden_opcodes": ["MOV"]},
        approach={"generation_guide": "使用 MOV 完成状态转移"},
    )

    assert contract["forbidden_opcodes"] == ["MOV"]
    assert "MOV" not in contract["required_opcodes"]


def test_explicit_self_conflict_remains_a_hard_definition_error():
    approach = {
        "name": "冲突方案",
        "generation_guide": "使用 MOV",
        "generation_contract": {
            "required_opcodes": ["MOV"],
            "forbidden_opcodes": ["MOV"],
        },
    }

    issues = contract_definition_issues(approach)

    assert any("同时被要求和禁止" in item and "MOV" in item for item in issues)


def test_inferred_self_conflict_is_neutralized_instead_of_blocking_confirmation():
    contract = normalize_generation_contract(
        None,
        approach={"generation_guide": "使用 MOV 完成转移；禁止 MOV"},
    )

    assert "MOV" not in contract["required_opcodes"]
    assert "MOV" not in contract["forbidden_opcodes"]
    assert any("已取消该歧义约束" in item for item in contract.get("normalization_warnings", []))


def test_any_of_group_prunes_explicitly_forbidden_candidates():
    contract = normalize_generation_contract(
        {
            "forbidden_opcodes": ["SET"],
            "any_of_opcode_groups": [["SET", "RST", "MOV"]],
        },
        approach={"generation_guide": ""},
    )

    assert contract["any_of_opcode_groups"] == [["RST", "MOV"]]


def test_any_of_group_is_removed_when_separately_required_member_satisfies_it():
    contract = normalize_generation_contract(
        {
            "required_opcodes": ["MOV"],
            "any_of_opcode_groups": [["MOV", "RST"]],
        },
        approach={"generation_guide": ""},
    )

    assert contract["any_of_opcode_groups"] == []


def test_explicit_any_of_group_fully_forbidden_is_unsatisfiable():
    approach = {
        "name": "不可满足方案",
        "generation_contract": {
            "forbidden_opcodes": ["SET", "RST"],
            "any_of_opcode_groups": [["SET", "RST"]],
        },
    }

    issues = contract_definition_issues(approach)

    assert any("任选组没有可用候选" in item for item in issues)


def test_explicit_any_of_group_wins_when_only_inferred_forbids_make_it_impossible():
    contract = normalize_generation_contract(
        {"any_of_opcode_groups": [["SET", "RST"]]},
        approach={"generation_guide": "禁止 SET；禁止 RST"},
    )

    assert contract["any_of_opcode_groups"] == [["SET", "RST"]]
    assert "SET" not in contract["forbidden_opcodes"]
    assert "RST" not in contract["forbidden_opcodes"]
    # No prose inference was applied, so there is no fabricated conflict warning.
    assert not contract.get("normalization_warnings")


def test_explicit_fields_can_coexist_with_inference_for_omitted_dimensions():
    contract = normalize_generation_contract(
        {"forbidden_opcodes": ["SET"]},
        approach={"generation_guide": "使用 D0 寄存器状态机并用 MOV 进行状态转移"},
    )

    assert contract["forbidden_opcodes"] == ["SET"]
    # A present partial contract is authoritative. Missing dimensions remain
    # empty; the selected guide is delivered separately as engineering context.
    assert contract["required_structures"] == []
    assert contract["required_opcodes"] == []


# Legacy/model vocabulary must not become an operator confirmation gate.
import copy
import pytest

from plc.specification.approach import (
    normalize_approach, contract_definition_warnings,
    validate_ladder_against_selected_approach,
)
from plc.specification.confirmed import (
    build_review_draft, canonicalize_confirmed_spec, validate_spec_draft,
)
from plc.specification.repair import structured_contract_violations, build_contract_repair_plan
from plc.generation_contract import generation_specification
from application.generation_support import public_generation_specification
from plc.hardware_profiles import ensure_hardware_questions, validate_hardware_spec

_OPAQUE = "输出触点与启动触点并联自锁，停止触点串联断开输出"


def _gate_spec(contract=None, guide=""):
    selected = {"approach_id": "direct", "name": "起保停", "generation_guide": guide}
    if contract is not None:
        selected["generation_contract"] = copy.deepcopy(contract)
    return {"plc_model": "FX3U", "selected_approach": selected,
            "approaches": [copy.deepcopy(selected)], "parameters": [], "io_table": []}


@pytest.mark.parametrize("field,value", [
    ("required_structures", [_OPAQUE]),
    ("forbidden_structures", ["不可同时写两个输出"]),
    ("any_of_structure_groups", [["direct_logic", _OPAQUE]]),
])
def test_unrecognized_structure_semantics_survive_confirmation_without_gate(field, value):
    spec = _gate_spec({field: value})
    original = copy.deepcopy(spec)
    issues = validate_spec_draft(spec)
    assert issues["errors"] == []
    assert any(i["code"] == "unverified_approach_contract" and i["blocking"] is False
               for i in issues["warnings"])
    canonical = canonicalize_confirmed_spec(spec)
    contract = canonical["selected_approach"]["generation_contract"]
    assert contract[field] == []
    assert contract["unverified_constraints"][field] == value
    assert canonicalize_confirmed_spec(canonical) == canonical
    assert spec == original
    projected = generation_specification(canonical)
    assert projected["selected_approach"]["generation_contract"]["unverified_constraints"][field] == value


def test_unknown_structure_from_fresh_agent_a_result_remains_nonblocking():
    analysis = {"approaches": [{"name": "输出自锁", "generation_contract": {
        "required_structures": [_OPAQUE]}}], "missing_info": [], "suggested_io": {}}
    normalized = ensure_hardware_questions(analysis, "FX3U", "起保停")
    draft = build_review_draft(normalized)
    assert validate_spec_draft(draft)["errors"] == []
    assert draft["selected_approach"]["generation_contract"]["unverified_constraints"]["required_structures"] == [_OPAQUE]


def test_mixed_any_of_group_does_not_become_a_mandatory_known_alternative():
    group = ["self_hold", "CustomStructureV1"]
    spec = _gate_spec({"any_of_structure_groups": [group], "forbidden_structures": ["self_hold"]})
    contract = normalize_approach(spec["selected_approach"])["generation_contract"]
    assert contract["any_of_structure_groups"] == []
    assert contract["unverified_constraints"]["any_of_structure_groups"] == [group]
    assert not contract_definition_issues(spec["selected_approach"])
    assert not structured_contract_violations({"rungs": []}, spec)


def test_known_disjunctions_and_constraints_still_bind_alongside_unknown_notes():
    spec = _gate_spec({"required_structures": ["self_hold", _OPAQUE],
                       "any_of_structure_groups": [["bit_state_machine", "register_state_machine"]]})
    issues = validate_ladder_against_selected_approach({"rungs": []}, spec)
    assert any("自保持" in message for message in issues)
    assert any("至少需要一种结构" in message for message in issues)
    assert all(_OPAQUE not in message for message in issues)
    violations = structured_contract_violations({"rungs": []}, spec)
    assert {v["kind"] for v in violations} == {"missing_structure", "missing_any_structure"}


@pytest.mark.parametrize("spelling", ["self_hold", "SELF_HOLD", "自保持回路"])
def test_exact_structure_names_and_display_labels_share_canonical_vocabulary(spelling):
    contract = normalize_generation_contract({"required_structures": [spelling]})
    assert contract["required_structures"] == ["self_hold"]
    assert not contract.get("unverified_constraints")


@pytest.mark.parametrize("raw", [None, {}, {"source": "inferred", "required_opcodes": ["MOV"]}])
def test_legacy_prose_inference_does_not_resurrect_hard_opcode_obligations(raw):
    spec = _gate_spec(raw, "直接逻辑；以前讨论过 MOV 或 SET/RST，并未指定必须使用")
    selected = normalize_approach(spec["selected_approach"])
    assert selected["generation_guide"] == spec["selected_approach"]["generation_guide"]
    assert selected["generation_contract"]["required_opcodes"] == []
    assert selected["generation_contract"].get("unverified_constraints")
    assert not validate_ladder_against_selected_approach({"rungs": []}, spec)
    assert not structured_contract_violations({"rungs": []}, spec)
    assert normalize_approach(selected) == selected


def test_explicit_constraints_cannot_be_disabled_by_model_enforce_flag():
    spec = _gate_spec({"required_opcodes": ["MOV"], "enforce": False})
    assert normalize_approach(spec["selected_approach"])["generation_contract"]["enforce"] is True
    assert any("MOV" in message for message in validate_ladder_against_selected_approach({"rungs": []}, spec))


def test_opaque_only_contract_does_not_request_repair_or_claim_full_verification():
    spec = _gate_spec({"required_structures": [_OPAQUE]})
    assert not validate_ladder_against_selected_approach({"rungs": []}, spec)
    assert not structured_contract_violations({"rungs": []}, spec)
    plan = build_contract_repair_plan({"rungs": []}, spec)
    assert plan["repairability"] == "not_needed"
    assert plan["verification_status"] == "partial"
    assert "未校验" in plan["reason"]
    assert "prompt" not in plan and "instruction" not in plan


def test_direct_generation_of_old_snapshot_projects_unknown_semantics_without_mutation():
    spec = _gate_spec({"required_structures": [_OPAQUE]})
    original = copy.deepcopy(spec)
    projected = public_generation_specification(spec)
    contract = projected["selected_approach"]["generation_contract"]
    assert contract["required_structures"] == []
    assert contract["unverified_constraints"]["required_structures"] == [_OPAQUE]
    assert spec == original


def test_unknown_metadata_does_not_open_the_generation_projection_allowlist():
    spec = _gate_spec({"required_structures": [_OPAQUE], "unverified_constraints": {
        "required_structures": ["AnotherOpaqueDetail"], "api_key": "DO_NOT_FORWARD"}})
    projected = public_generation_specification(spec)
    assert "DO_NOT_FORWARD" not in str(projected)
    assert projected["selected_approach"]["generation_contract"]["unverified_constraints"]["required_structures"] == ["AnotherOpaqueDetail", _OPAQUE]


@pytest.mark.parametrize("parameter,code", [
    ({"id": "duration", "name": "延时", "required": True, "value": ""}, "required_parameter_missing"),
    ({"id": "stop_input", "name": "停止输入：常开还是常闭？", "required": True, "value": "X1"}, "contact_type_missing"),
])
def test_unknown_structure_does_not_bypass_genuinely_missing_inputs(parameter, code):
    spec = _gate_spec({"required_structures": [_OPAQUE]})
    spec["parameters"] = [parameter]
    assert code in {i["code"] for i in validate_spec_draft(spec)["errors"]}


def test_explicit_known_self_contradiction_still_blocks_confirmation():
    spec = _gate_spec({"required_structures": ["SELF_HOLD", _OPAQUE], "forbidden_structures": ["self_hold"]})
    assert "invalid_approach_contract" in {i["code"] for i in validate_spec_draft(spec)["errors"]}


def test_candidate_bookkeeping_does_not_become_a_confirmation_gate_again():
    spec = _gate_spec({})
    spec["approaches"] *= 2
    spec["selected_approach"]["approach_id"] = "user-edited"
    assert validate_spec_draft(spec)["errors"] == []


def _vfd_spec(method, guide, output_type="", modules=""):
    spec = _gate_spec({}, guide)
    spec["hardware_requirements"] = {"hardware_dependent": True, "vfd": True}
    spec["parameters"] = [
        {"id": key, "name": key, "value": value}
        for key, value in (("control_method", method), ("output_type", output_type), ("modules", modules))
        if value
    ]
    return spec


def test_vfd_prose_mentions_do_not_overrule_explicit_interface_choice():
    spec = _vfd_spec("RS485通讯（Modbus）", "不再采用多段速；使用已确认的通信方式")
    result = validate_hardware_spec(spec)
    assert result["errors"] == []
    assert any(i["code"] == "control_method_approach_conflict" and not i["blocking"] for i in result["warnings"])


@pytest.mark.parametrize("method,output,modules,code", [
    ("高速脉冲频率给定", "继电器输出", "无", "pulse_output_conflict"),
    ("模拟量输出（0-10V）", "", "无", "analog_module_conflict"),
])
def test_real_hardware_contradictions_remain_blocking(method, output, modules, code):
    errors = validate_hardware_spec(_vfd_spec(method, "", output, modules))["errors"]
    assert code in {i["code"] for i in errors}


def test_http_confirmation_of_unknown_structure_is_local_and_preserves_semantics(tmp_path):
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    from application.workbench import WorkbenchService

    def no_model():
        pytest.fail("Confirming a specification must not invoke a model or repair call")

    origin = "http://127.0.0.1:8765"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", model_factory=no_model)
    app = create_app(service.store.base_dir, service=service, origin=origin, operator_token="operator")
    with TestClient(app, base_url=origin) as client:
        login = client.post("/api/session", json={"token": "operator"}, headers={"Origin": origin}).json()
        headers = {"Origin": origin, "X-CSRF-Token": login["csrf"]}
        pid = client.post("/api/projects", headers=headers, json={"name": "legacy-gate"}).json()["id"]
        path = f"/api/projects/{pid}/spec"
        result = client.put(path, headers=headers, json={"spec": _gate_spec({"required_structures": [_OPAQUE]}), "expected_hash": None}).json()
        assert result["valid"]
        assert result["spec"]["selected_approach"]["generation_contract"]["unverified_constraints"]["required_structures"] == [_OPAQUE]
        # The same route still rejects missing operator data; no broad bypass.
        incomplete = copy.deepcopy(result["spec"])
        incomplete["parameters"] = [{"id": "duration", "name": "延时", "value": "", "required": True}]
        refused = client.put(path, headers=headers, json={"spec": incomplete, "expected_hash": result["hash"]}).json()
        assert not refused["valid"]
        assert "required_parameter_missing" in {i["code"] for i in refused["issues"]["errors"]}


def test_legacy_inferred_impossible_opcode_group_is_not_a_stale_definition_error():
    spec = _gate_spec({"source": "inferred", "forbidden_opcodes": ["SET", "RST"],
                       "any_of_opcode_groups": [["SET", "RST"]]})
    assert not contract_definition_issues(spec["selected_approach"])
    selected = normalize_approach(spec["selected_approach"])
    assert selected["generation_contract"]["unverified_constraints"]["any_of_opcode_groups"] == [["SET", "RST"]]
    assert normalize_approach(selected) == selected


def test_fully_forbidden_explicit_known_structure_group_is_still_impossible():
    spec = _gate_spec({"forbidden_structures": ["self_hold", "set_reset_latch"],
                       "any_of_structure_groups": [["self_hold", "set_reset_latch"]]})
    assert any("任选组没有可用候选" in message for message in contract_definition_issues(spec["selected_approach"]))
