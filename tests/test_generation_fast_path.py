import copy
import json

import pytest

from plc.ir import build_plc_ir, validate_plc_ir
from plc.validation import (
    PLCJsonValidationError,
    validate_ladder_candidate_structure,
    validate_ladder_full,
)


def _self_hold():
    return {
        "device_comments": {"X0": "启动", "X1": "停止", "Y0": "电机"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [
                            {
                                "type": "parallel_block",
                                "branches": [
                                    [{"type": "NO", "address": "X0", "label": "启动"}],
                                    [{"type": "NO", "address": "Y0", "label": "自锁"}],
                                ],
                            },
                            {"type": "NC", "address": "X1", "label": "停止"},
                        ],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "电机"}],
                    }
                ],
            }
        ],
    }


def test_structural_candidate_acceptance_does_not_enforce_selected_approach():
    ladder = _self_hold()
    spec = {
        "selected_approach": {
            "name": "必须使用 MOV",
            "generation_contract": {"required_opcodes": ["MOV"], "enforce": True},
        }
    }
    assert validate_ladder_candidate_structure(ladder, "FX3U") is ladder
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(ladder, "FX3U", spec)


def test_structural_candidate_acceptance_does_not_reject_duplicate_coil_style():
    ladder = _self_hold()
    duplicate = copy.deepcopy(ladder["rungs"][0])
    duplicate["rung_id"] = 2
    duplicate["branches"][0]["inputs"] = [{"type": "NO", "address": "X2", "label": "旁路"}]
    ladder["device_comments"]["X2"] = "旁路"
    ladder["rungs"].append(duplicate)
    assert validate_ladder_candidate_structure(ladder, "FX3U") is ladder
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(ladder, "FX3U")


def test_structural_candidate_acceptance_still_rejects_invalid_address():
    ladder = _self_hold()
    ladder["rungs"][0]["branches"][0]["inputs"][1]["address"] = "STOP"
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_candidate_structure(ladder, "FX3U")


def test_ir_consistency_can_be_checked_without_reinterpreting_ladder_semantics():
    ladder = _self_hold()
    duplicate = copy.deepcopy(ladder["rungs"][0])
    duplicate["rung_id"] = 2
    duplicate["branches"][0]["inputs"] = [{"type": "NO", "address": "X2", "label": "旁路"}]
    ladder["device_comments"]["X2"] = "旁路"
    ladder["rungs"].append(duplicate)
    validate_ladder_candidate_structure(ladder, "FX3U")
    program = build_plc_ir(ladder, plc_model="FX3U")
    assert validate_plc_ir(program, validate_ladder=False) is program
    with pytest.raises((PLCJsonValidationError, ValueError)):
        validate_plc_ir(program, validate_ladder=True)



def test_semantic_requirement_registry_covers_structure_and_core_user_constraints():
    from plc.specification.semantic_validation import validate_confirmed_semantics

    ladder = {
        "device_comments": {"X0": "input", "D10": "data"},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0"}],
                "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"]}],
            }],
        }],
    }
    spec = {"selected_approach": {
        "implementation_semantics": [
            {"kind": "structure", "status": "required", "value": "direct_logic"},
            {"kind": "structure", "status": "any_of", "values": ["direct_logic", "self_hold"]},
        ],
        "explicit_user_constraints": {
            "required_opcodes": ["MOV"],
            "required_devices": ["D10"],
            "instruction_instances": [
                {"opcode": "MOV", "operands": ["K1", "D10"]},
            ],
        },
    }}
    report = validate_confirmed_semantics(ladder, spec, "FX3U")
    assert report["status"] == "verified"
    assert len(report["requirements"]) == 5
    assert all(row["status"] == "verified" for row in report["checks"])


def test_explicit_user_constraint_violation_is_blocking_without_full_review_rules():
    from plc.specification.semantic_validation import (
        ConfirmedSemanticValidationError, validate_confirmed_semantics,
    )
    spec = {"selected_approach": {
        "implementation_semantics": [
            {"kind": "structure", "status": "required", "value": "direct_logic"},
        ],
        "explicit_user_constraints": {"required_opcodes": ["MOV"]},
    }}
    with pytest.raises(ConfirmedSemanticValidationError, match="user constraints"):
        validate_confirmed_semantics(_self_hold(), spec, "FX3U")


def test_compact_agent_skips_legacy_candidate_normalizers(monkeypatch):
    import plc.generation as generation
    from plc.generation import prepare_ladder_candidate

    def legacy_called(*_args, **_kwargs):
        pytest.fail("fresh compact Agent B candidate entered a legacy normalizer")

    monkeypatch.setattr(generation, "_normalize_legacy_blocks", legacy_called)
    monkeypatch.setattr(generation, "normalize_legacy_counter_outputs", legacy_called)
    monkeypatch.setattr(generation, "normalize_app_instr_out_outputs", legacy_called)

    result = prepare_ladder_candidate(
        _self_hold(),
        plc_model="FX3U",
        candidate_origin="compact_agent",
    )
    assert result["candidate_origin"] == "compact_agent"
    assert result["semantic_validation"]["status"] == "not_applicable"


def test_legacy_contract_without_implementation_semantics_remains_review_only():
    from plc.generation import prepare_ladder_candidate

    ladder = _self_hold()
    spec = {
        "selected_approach": {
            "name": "必须使用 MOV",
            "generation_contract": {
                "required_opcodes": ["MOV"],
                "enforce": True,
            },
        }
    }
    accepted_contract = prepare_ladder_candidate(
        ladder,
        plc_model="FX3U",
        confirmed_spec=spec,
        candidate_origin="compact_agent",
    )
    assert {
        row["check"]: row["status"]
        for row in accepted_contract["semantic_validation"]["checks"]
    }["selected_approach_contract"] == "deferred_to_review"

    duplicate = copy.deepcopy(ladder["rungs"][0])
    duplicate["rung_id"] = 2
    duplicate["branches"][0]["inputs"] = [{"type": "NO", "address": "X2"}]
    ladder["device_comments"]["X2"] = "旁路"
    ladder["rungs"].append(duplicate)
    # Duplicate-coil style remains a full-review concern; it is not promoted
    # back into confirmed-generation acceptance merely by this refactor.
    accepted = prepare_ladder_candidate(
        ladder,
        plc_model="FX3U",
        candidate_origin="compact_agent",
    )
    assert accepted["semantic_validation"]["status"] == "not_applicable"



def test_confirmed_agent_origin_has_one_core_owner():
    import inspect
    import application.generation as generation_workflow
    import application.generation_agent as generation_agent
    import plc.generation as generation
    from plc.generation import CONFIRMED_AGENT_ORIGIN

    assert CONFIRMED_AGENT_ORIGIN == "compact_agent"
    agent_source = inspect.getsource(generation_agent.generate_confirmed_ladder)
    assert "CandidateService" not in agent_source
    workflow_source = inspect.getsource(generation_workflow.GenerationWorkflow._run)
    assert "CONFIRMED_AGENT_ORIGIN" in workflow_source
    assert "direct_candidate is not None" in workflow_source
    source = inspect.getsource(generation.prepare_ladder_candidate)
    assert "candidate_origin == CONFIRMED_AGENT_ORIGIN" in source
    assert "candidate_origin != CONFIRMED_AGENT_ORIGIN" in source



def test_confirmed_generation_acceptance_capability_is_enforced():
    from tools.audit_capability_coverage import audit_coverage

    report = audit_coverage()
    states = {row["id"]: row["state"] for row in report["capabilities"]}
    assert states["confirmed_generation_acceptance"] == "enforced"



def test_required_self_hold_missing_role_fails_closed_without_label_inference():
    from plc.specification.semantic_validation import (
        ConfirmedSemanticValidationError,
        validate_confirmed_semantics,
    )

    spec = {
        "io_bindings": [
            {"role": "start", "kind": "X", "address": "X0", "active_level": 1},
            {"kind": "X", "address": "X1", "active_level": 0, "label": "停止按钮"},
            {"role": "output", "kind": "Y", "address": "Y0"},
        ],
        "io_table": [
            {"kind": "X", "address": "X0", "label": "启动"},
            {"kind": "X", "address": "X1", "label": "停止按钮"},
            {"kind": "Y", "address": "Y0", "label": "电机"},
        ],
        "selected_approach": {
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "self_hold"},
            ],
        },
    }
    with pytest.raises(ConfirmedSemanticValidationError, match="missing_roles"):
        validate_confirmed_semantics(_self_hold(), spec, plc_model="FX3U")


def test_required_self_hold_unsupported_shape_is_visible_but_not_a_style_gate():
    from plc.specification.semantic_validation import validate_confirmed_semantics

    spec = {
        "io_bindings": [
            {"role": "start", "kind": "X", "address": "X0", "active_level": 1},
            {"role": "stop", "kind": "X", "address": "X1", "active_level": 0},
            {"role": "output", "kind": "Y", "address": "Y0"},
        ],
        "io_table": [
            {"kind": "X", "address": "X0", "label": "启动"},
            {"kind": "X", "address": "X1", "label": "停止"},
            {"kind": "Y", "address": "Y0", "label": "电机"},
        ],
        "parameters": [{"id": "unrelated_process_parameter", "value": "keep"}],
        "selected_approach": {
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "self_hold"},
            ],
        },
    }
    report = validate_confirmed_semantics(_self_hold(), spec, plc_model="FX3U")
    assert report["status"] == "unresolved"
    row = next(item for item in report["checks"] if item["check"] == "direct_self_hold_truth_table")
    assert row["status"] == "unresolved"
    assert row["reason"] == "additional_parameters"

def test_confirmed_semantic_mismatch_is_not_a_format_repair_problem():
    from plc.specification.semantic_validation import (
        ConfirmedSemanticValidationError,
        validate_confirmed_semantics,
    )

    spec = {
        "io_bindings": [
            {"role": "start", "kind": "X", "address": "X0", "active_level": 1},
            {"role": "stop", "kind": "X", "address": "X1", "active_level": 0},
            {"role": "output", "kind": "Y", "address": "Y0"},
        ],
        "io_table": [
            {"kind": "X", "address": "X0", "label": "启动"},
            {"kind": "X", "address": "X1", "label": "停止"},
            {"kind": "Y", "address": "Y0", "label": "电机"},
        ],
        "selected_approach": {
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "self_hold"},
            ],
        },
    }
    with pytest.raises(ConfirmedSemanticValidationError):
        validate_confirmed_semantics(_self_hold(), spec, plc_model="FX3U")
