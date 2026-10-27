import json
from pathlib import Path

import pytest

from plc.specification.repair import (
    build_contract_repair_plan,
    patch_device_addresses,
    structured_contract_violations,
)
from plc.validation import (
    ApproachContractValidationError,
    PLCJsonValidationError,
    should_auto_repair_validation_error,
    validate_ladder_full,
)


def _state_candidate():
    return {
        "device_comments": {
            "M8002": "首扫",
            "D0": "步骤状态寄存器",
            "X0": "启动",
            "Y0": "输出",
        },
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "M8002", "label": "首扫"}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "临时输出"}],
                    }
                ],
            },
            {
                "rung_id": 2,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K1",
                    "label": "步骤1",
                },
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": "启动"}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "输出"}],
                    }
                ],
            },
        ],
    }


def _state_spec():
    return {
        "summary": "用 D0 做两步顺序控制",
        "selected_approach": {
            "name": "D寄存器步进状态机",
            "description": "D0 保存当前状态",
            "generation_guide": "M8002 首扫用 MOV K1 D0 初始化；各步骤用 MOV Kn D0 完成状态转移",
            "generation_contract": {
                "required_opcodes": ["MOV"],
                "forbidden_opcodes": [],
                "required_devices": ["M8002", "D0"],
                "forbidden_devices": [],
                "required_structures": [
                    "register_state_machine",
                    "state_initialization",
                    "state_comparison",
                    "state_transition",
                ],
                "forbidden_structures": ["bit_state_machine"],
                "any_of_opcode_groups": [],
                "any_of_structure_groups": [],
                "enforce": True,
            },
        },
    }


def test_contract_violations_are_structured():
    violations = structured_contract_violations(_state_candidate(), _state_spec())
    assert any(
        item["kind"] == "missing_opcode" and item["value"] == "MOV"
        for item in violations
    )
    assert all(item.get("violation_id") for item in violations)


def test_state_machine_repair_is_scoped_and_rejects_dummy_opcode_strategy():
    plan = build_contract_repair_plan(
        _state_candidate(), _state_spec(), plc_model="FX3U"
    )
    assert plan["repairability"] == "scoped_patch"
    assert plan["allowed_rung_ids"] == [1, 2]
    assert {"D0", "M8002"}.issubset(set(plan["allowed_addresses"]))
    assert 'mode 必须为 "partial"' in plan["prompt"]
    assert "M8000+MOV" in plan["prompt"]
    assert "死代码" in plan["prompt"]


def test_bare_required_opcode_without_semantic_anchor_is_not_auto_repaired():
    ladder = {
        "device_comments": {"X0": "启动", "Y0": "输出"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": "启动"}],
                        "outputs": [{"type": "COIL", "address": "Y0", "label": "输出"}],
                    }
                ],
            }
        ],
    }
    spec = {
        "selected_approach": {
            "name": "只写了必用 MOV 的不完整方案",
            "generation_guide": "",
            "generation_contract": {
                "required_opcodes": ["MOV"],
                "forbidden_opcodes": [],
                "required_devices": [],
                "forbidden_devices": [],
                "required_structures": [],
                "forbidden_structures": [],
                "any_of_opcode_groups": [],
                "any_of_structure_groups": [],
                "enforce": True,
            },
        }
    }
    plan = build_contract_repair_plan(ladder, spec)
    assert plan["repairability"] == "needs_user_context"
    assert "prompt" not in plan
    assert plan["allowed_rung_ids"] == []


def test_partial_patch_device_scan_covers_rungs_and_comments():
    partial = {
        "mode": "partial",
        "device_comments": {"D0": "状态"},
        "rungs": [
            {
                "rung_id": 2,
                "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K1"},
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0"}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["K2", "D0"],
                                "label": "状态转移",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    assert patch_device_addresses(partial) == {"D0", "X0"}


def test_plan_is_json_serializable_for_audit_metadata():
    plan = build_contract_repair_plan(_state_candidate(), _state_spec())
    json.dumps(plan, ensure_ascii=False)




def test_indexed_devices_are_enforced_by_patch_scope():
    partial = {
        "mode": "partial",
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0"}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["D0", "D100Z0"],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    addresses = patch_device_addresses(partial)
    assert {"X0", "D0", "D100Z0", "D100", "Z0"}.issubset(addresses)


def test_contract_repair_router_gets_repair_safety_bundles():
    source = Path('src/knowledge/patterns.py').read_text(encoding="utf-8")
    assert '"contract_repair"' in source
    assert '"repair", "debug_fix", "contract_repair"' in source


# Repairability policy belongs to the contract-repair owner: planner output and
# validator repairability must stay aligned.
def _direct_ladder():
    return {
        "device_comments": {"X0": "启动", "Y0": "输出"},
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
                            {"type": "NO", "address": "X0", "label": "启动"}
                        ],
                        "outputs": [
                            {"type": "COIL", "address": "Y0", "label": "输出"}
                        ],
                    }
                ],
            }
        ],
    }


def test_missing_required_opcode_is_contract_mismatch_not_auto_repairable():
    spec = {
        "selected_approach": {
            "name": "MOV方案",
            "generation_guide": "使用 MOV 完成方案",
            "generation_contract": {
                "required_opcodes": ["MOV"],
                "enforce": True,
            },
        }
    }

    with pytest.raises(ApproachContractValidationError) as captured:
        validate_ladder_full(_direct_ladder(), "FX3U", spec)

    error = captured.value
    assert isinstance(error, PLCJsonValidationError)
    assert "缺少方案必用指令 MOV" in str(error)
    assert error.repair_policy == "manual"
    assert should_auto_repair_validation_error(error) is False


def test_normal_plc_validation_errors_remain_auto_repairable():
    error = PLCJsonValidationError("syntactic validation failure")
    assert should_auto_repair_validation_error(error) is True
