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
