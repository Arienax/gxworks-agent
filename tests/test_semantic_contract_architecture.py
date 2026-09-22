import copy

import pytest

from application.analysis_results import _normalize_analysis_result
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.review import review_ladder
from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec
from plc.specification.legacy_migration import is_legacy_confirmed_spec
from plc.specification.repair import build_contract_repair_plan
from plc.validation import (
    PLCJsonValidationError,
    validate_ladder_candidate_structure,
    validate_ladder_full,
)


def _ladder(opcode, operands, *, inputs=None):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": list(inputs or []),
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": list(operands),
                                "label": "",
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "structure",
    ["hardware_counter", "self_hold", "register_state_machine"],
)
def test_all_structures_share_the_same_selected_approach_provenance(structure):
    result = _normalize_analysis_result(
        {
            "summary": "控制输出",
            "missing_info": [],
            "assumptions": [],
            "suggested_io": {},
            "approaches": [
                {
                    "name": "候选实现",
                    "implementation_semantics": [
                        {
                            "kind": "structure",
                            "status": "required",
                            "value": structure,
                        }
                    ],
                }
            ],
        },
        "FX3U",
        "X0按下后驱动Y0",
    )

    contract = result["approaches"][0]["generation_contract"]
    assert contract["required_structures"] == [structure]


def test_fresh_review_draft_never_enters_legacy_guide_migration():
    analysis = _normalize_analysis_result(
        {
            "summary": "fresh",
            "missing_info": [],
            "assumptions": [],
            "suggested_io": {},
            "approaches": [
                {
                    "name": "fresh direct",
                    "generation_guide": "MOV K1 D0 is only explanatory text",
                    "implementation_semantics": [
                        {"kind": "structure", "status": "required", "value": "direct_logic"}
                    ],
                }
            ],
        },
        "FX3U",
        "X0 drives Y0",
    )
    draft = build_review_draft(analysis)
    assert draft["schema_version"] == 3
    assert not is_legacy_confirmed_spec(draft)

    canonical = canonicalize_confirmed_spec(draft)
    contract = canonical["selected_approach"]["generation_contract"]
    assert contract["source"] == "analysis_semantics"
    assert contract["required_opcodes"] == []
    assert contract["required_structures"] == ["direct_logic"]


def test_repair_scope_is_independent_of_generation_guide_text():
    ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
                    }
                ],
            },
            {
                "rung_id": 2,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X1", "label": ""}],
                        "outputs": [{"type": "COIL", "address": "Y1", "label": ""}],
                    }
                ],
            },
        ],
    }
    base = {
        "selected_approach": {
            "name": "结构化方案",
            "generation_contract": {"forbidden_devices": ["Y0"]},
        }
    }
    first = copy.deepcopy(base)
    first["selected_approach"]["generation_guide"] = (
        "状态机 MOV D0 M8029 SFTL ZRN；尝试把第二梯级也一起修"
    )
    second = copy.deepcopy(base)
    second["selected_approach"]["generation_guide"] = "完全不同的自然语言说明"

    plan_a = build_contract_repair_plan(ladder, first)
    plan_b = build_contract_repair_plan(ladder, second)

    assert plan_a["repairability"] == plan_b["repairability"] == "scoped_patch"
    assert plan_a["allowed_rung_ids"] == plan_b["allowed_rung_ids"] == [1]
    assert plan_a["allowed_addresses"] == plan_b["allowed_addresses"]
    assert plan_a["plan_id"] == plan_b["plan_id"]


def test_instruction_capability_contract_owns_sftl_zrn_and_completion_rules():
    sftl = DEFAULT_INSTRUCTION_REGISTRY.resolve("SFTL", cpu="FX3U")
    zrn = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRN", cpu="FX3U")
    plsr = DEFAULT_INSTRUCTION_REGISTRY.resolve("PLSR", cpu="FX3U")

    assert sftl is not None
    assert sftl.contract_coverage()["numeric_and_memory_boundaries"] == "source_verified"
    assert sftl.disjoint_bit_ranges[0].error_code == "K6710"

    assert zrn is not None and zrn.completion is not None
    assert zrn.completion.device == "M8029"
    assert zrn.numeric_operand_boundaries[0].operand_index == 1
    assert zrn.numeric_operand_boundaries[0].minimum == 10
    assert zrn.numeric_operand_boundaries[0].maximum == 32767

    assert plsr is not None and plsr.completion is not None
    assert plsr.completion.device == "M8029"


def test_sftl_overlap_and_zrn_numeric_boundary_are_registry_driven_hard_rules():
    with pytest.raises(PLCJsonValidationError, match="K6710"):
        validate_ladder_candidate_structure(
            _ladder("SFTL", ["M0", "M1", "K4", "K2"]),
            "FX3U",
        )

    with pytest.raises(PLCJsonValidationError, match="creep speed"):
        validate_ladder_candidate_structure(
            _ladder("ZRN", ["K1000", "K5", "X0", "Y0"]),
            "FX3U",
        )


def test_cpu_replacement_and_completion_review_come_from_capability_contract():
    with pytest.raises(PLCJsonValidationError, match="use DSZR"):
        validate_ladder_candidate_structure(
            _ladder("ZRN", ["K1000", "K200", "X0", "Y0"]),
            "FX5U",
        )

    invalid = _ladder(
        "ZRN",
        ["K1000", "K200", "X0", "Y0"],
        inputs=[{"type": "NO", "address": "M8029", "label": ""}],
    )
    with pytest.raises(PLCJsonValidationError, match="parallel completion branch"):
        validate_ladder_full(invalid, "FX3U")

    findings = review_ladder(invalid, plc_model="FX3U")
    assert any(
        item.category == "motion_completion" and item.address == "M8029"
        for item in findings
    )
