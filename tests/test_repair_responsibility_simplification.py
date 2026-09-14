import copy
import json

import pytest

from application.base import model_call
from application.field_repair import apply, plan
from application.generation_repair import RepairAssemblyError
from application.repair_policy import _restrict_opcode_schema
from plc_generation import prepare_ladder_candidate
from plc_json_validator import PLCJsonValidationError


def _coil_rung():
    return {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [{"type": "NO", "address": "X0", "label": None}],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0, "inputs": [],
                "outputs": [{"type": "COIL", "address": "Y0", "label": None}],
            }],
        }],
    }


def test_model_call_never_invokes_provider_for_deterministic_field_patch():
    base = _coil_rung()
    base["rungs"][0]["debug_note"] = "x" * 100
    repair = plan(base, [{"path": "content$.rungs.0.debug_note", "reason": "field_too_long"}])
    payload = {
        "repair_mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "target": repair["target"],
    }
    called = []

    def remote(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("deterministic field repair must not call the provider")

    _reasoning, raw = model_call(remote, payload, "offline", "low", mode="field_patch")
    assert called == []
    fixed = apply(base, json.loads(raw), repair)
    assert fixed["rungs"][0]["debug_note"] is None


def test_semantic_opcode_error_is_blocked_without_provider_or_rung_fallback():
    base = _coil_rung()
    base["rungs"][0]["branches"][0]["outputs"] = [{
        "type": "APP_INSTR", "opcode": "NOT_A_REAL_OPCODE",
        "operands": ["D0", "D1"], "label": None,
    }]
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }])
    assert repair["target"]["strategy"] == "blocked"
    payload = {
        "repair_mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "target": repair["target"],
    }
    called = []

    def remote(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("opcode repair must not be guessed by a model")

    _reasoning, raw = model_call(remote, payload, "offline", "low", mode="field_patch")
    assert called == []
    with pytest.raises(RepairAssemblyError) as rejected:
        apply(base, json.loads(raw), repair)
    assert rejected.value.reason == "repair_scope_violation"


def test_structural_repair_schema_freezes_opcode_to_baseline_values():
    native = {
        "json_schema": {"schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["APP_INSTR"]},
                "opcode": {"type": "string", "enum": ["MOV", "ADD", "SUB"]},
            },
        }}
    }
    restricted = _restrict_opcode_schema(copy.deepcopy(native), {"MOV"})
    assert restricted["json_schema"]["schema"]["properties"]["opcode"]["enum"] == ["MOV"]


def test_whole_rung_structural_repair_may_relocate_same_semantic_leaves():
    parallel = {
        "type": "parallel_block",
        "branches": [
            [{"type": "NO", "address": "X0", "label": None}],
            [{"type": "NO", "address": "M0", "label": None}],
        ],
    }
    baseline = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [copy.deepcopy(parallel)],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0, "inputs": [],
                "outputs": [{"type": "COIL", "address": "Y0", "label": None}],
            }],
        }],
    }
    partial = {
        "mode": "partial", "device_comments": {}, "delete_rung_ids": [],
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [copy.deepcopy(parallel)],
                "outputs": [{"type": "COIL", "address": "Y0", "label": None}],
            }],
        }],
    }
    result = prepare_ladder_candidate(
        partial, previous_ladder=baseline, repair_mode=True,
        allowed_rung_ids={1}, allowed_addresses={"X0", "M0", "Y0"},
        task_type="contract_repair",
    )
    assert result["ladder"]["rungs"][0]["shared_inputs"] == []


def test_whole_rung_structural_repair_cannot_change_opcode_or_other_semantic_tokens():
    baseline = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [{"type": "NO", "address": "X0", "label": None}],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0, "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR", "opcode": "MOV",
                    "operands": ["D0", "D1"], "label": None,
                }],
            }],
        }],
    }
    partial = {
        "mode": "partial", "device_comments": {}, "delete_rung_ids": [],
        "rungs": [copy.deepcopy(baseline["rungs"][0])],
    }
    partial["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "ADD"
    with pytest.raises(PLCJsonValidationError, match="out-of-scope semantic tokens"):
        prepare_ladder_candidate(
            partial, previous_ladder=baseline, repair_mode=True,
            allowed_rung_ids={1}, allowed_addresses={"X0", "D0", "D1"},
            task_type="contract_repair",
        )
