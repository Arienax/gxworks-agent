import copy
import json

import pytest

from application.base import _structural_repair_payload, model_call
from application.field_repair import apply, plan
from plc.candidate_repair import RepairAssemblyError
from application.repair_policy import _restrict_opcode_schema
from plc.generation import prepare_ladder_candidate
from plc.validation import PLCJsonValidationError


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


def _parallel():
    return {
        "type": "parallel_block",
        "branches": [
            [{"type": "NO", "address": "X0", "label": None}],
            [{"type": "NO", "address": "M0", "label": None}],
        ],
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
    assert fixed["rungs"][0]["debug_note"] == "x" * 64


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
    assert rejected.value.reason == "invalid_ladder_structure"


def test_partial_mode_does_not_hijack_semantic_contract_repair():
    semantic_payload = {
        "repair_mode": "partial",
        "baseline_subset": _coil_rung(),
        "allowed_rung_ids": [1],
        "allowed_addresses": ["X0", "Y0"],
    }
    structural_payload = copy.deepcopy(semantic_payload)
    structural_payload["baseline_subset"]["rungs"][0]["shared_inputs"] = [copy.deepcopy(_parallel())]
    assert _structural_repair_payload(semantic_payload) is False
    assert _structural_repair_payload(structural_payload) is True


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


def test_whole_rung_structural_repair_may_relocate_same_behavior():
    parallel = _parallel()
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


def test_structural_repair_cannot_swap_condition_output_associations():
    parallel = _parallel()
    baseline = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1, "header_element": None,
            "shared_inputs": [copy.deepcopy(parallel)],
            "branches": [
                {"branch_id": 1, "y_offset_level": 0,
                 "inputs": [{"type": "NO", "address": "X2", "label": None}],
                 "outputs": [{"type": "COIL", "address": "Y0", "label": None}]},
                {"branch_id": 2, "y_offset_level": 1,
                 "inputs": [{"type": "NO", "address": "X3", "label": None}],
                 "outputs": [{"type": "COIL", "address": "Y1", "label": None}]},
            ],
        }],
    }
    repaired = copy.deepcopy(baseline["rungs"][0])
    repaired["shared_inputs"] = []
    for branch in repaired["branches"]:
        branch["inputs"].insert(0, copy.deepcopy(parallel))
    repaired["branches"][0]["outputs"], repaired["branches"][1]["outputs"] = (
        repaired["branches"][1]["outputs"], repaired["branches"][0]["outputs"]
    )
    partial = {"mode": "partial", "device_comments": {}, "delete_rung_ids": [], "rungs": [repaired]}
    with pytest.raises(PLCJsonValidationError, match="condition/output behavior"):
        prepare_ladder_candidate(
            partial, previous_ladder=baseline, repair_mode=True,
            allowed_rung_ids={1}, allowed_addresses={"X0", "M0", "X2", "X3", "Y0", "Y1"},
            task_type="contract_repair",
        )


def test_semantic_contract_repair_remains_allowed_on_valid_baseline():
    baseline = _coil_rung()
    changed = copy.deepcopy(baseline["rungs"][0])
    changed["branches"][0]["outputs"][0]["address"] = "Y1"
    partial = {"mode": "partial", "device_comments": {}, "delete_rung_ids": [], "rungs": [changed]}
    result = prepare_ladder_candidate(
        partial, previous_ladder=baseline, repair_mode=True,
        allowed_rung_ids={1}, allowed_addresses={"X0", "Y0", "Y1"},
        task_type="contract_repair",
    )
    assert result["ladder"]["rungs"][0]["branches"][0]["outputs"][0]["address"] == "Y1"
