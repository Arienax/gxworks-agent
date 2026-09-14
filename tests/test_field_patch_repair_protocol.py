import copy

import pytest

from application.field_repair import (
    apply, base_sha256, deterministic_response, plan,
)
from application.generation_repair import RepairAssemblyError


def _base():
    return {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [{"type": "NO", "address": "X0", "label": None}],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR",
                    "opcode": "MOV",
                    "operands": ["D0", "D1"],
                    "label": None,
                }],
            }],
        }],
    }


def _payload(repair):
    return {
        "repair_mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "target": copy.deepcopy(repair["target"]),
    }


def test_opcode_failure_is_blocked_instead_of_guessing_a_mnemonic():
    base = _base()
    base["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "NOT_A_REAL_OPCODE"
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    assert repair["mode"] == "field_patch"
    assert repair["target"]["path"] == "/rungs/0/branches/0/outputs/0/opcode"
    assert repair["target"]["strategy"] == "blocked"
    assert "enum" not in repair["target"]["value_schema"]
    response = deterministic_response(_payload(repair))
    with pytest.raises(RepairAssemblyError) as rejected:
        apply(base, response, repair)
    assert rejected.value.reason == "repair_scope_violation"


def test_long_debug_note_is_removed_deterministically():
    base = _base()
    original = copy.deepcopy(base)
    base["rungs"][0]["debug_note"] = "x" * 100
    repair = plan(base, [{
        "path": "content$.rungs.0.debug_note",
        "reason": "field_too_long",
    }], "FX3U")
    assert repair["target"]["strategy"] == "deterministic"
    assert repair["target"]["deterministic_value"] is None
    assert repair["target"]["value_schema"]["maxLength"] == 64
    response = deterministic_response(_payload(repair))
    result = apply(base, response, repair)
    assert result["rungs"][0]["debug_note"] is None
    expected = copy.deepcopy(original)
    expected["rungs"][0]["debug_note"] = None
    assert result == expected


def test_branch_identity_fields_are_recomputed_from_position():
    base = _base()
    base["rungs"][0]["branches"][0]["branch_id"] = "bad"
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.branch_id",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    assert repair["target"]["strategy"] == "deterministic"
    assert repair["target"]["deterministic_value"] == 1
    result = apply(base, deterministic_response(_payload(repair)), repair)
    assert result["rungs"][0]["branches"][0]["branch_id"] == 1


def test_true_container_error_is_the_only_whole_rung_fallback_class():
    base = _base()
    base["rungs"][0]["shared_inputs"] = [{
        "type": "parallel_block",
        "branches": [[{"type": "NO", "address": "X0", "label": None}]],
    }]
    repair = plan(base, [{
        "path": "content$.rungs.0.shared_inputs.0.type",
        "reason": "invalid_shared_input",
    }], "FX3U")
    assert repair is None


def test_field_patch_rejects_wrong_path_or_baseline():
    base = _base()
    base["rungs"][0]["debug_note"] = "x" * 100
    repair = plan(base, [{
        "path": "content$.rungs.0.debug_note",
        "reason": "field_too_long",
    }], "FX3U")
    response = deterministic_response(_payload(repair))
    response["patches"][0]["path"] = "/rungs/0/rung_id"
    with pytest.raises(RepairAssemblyError):
        apply(base, response, repair)
    response = deterministic_response(_payload(repair))
    response["base_sha256"] = "0" * 64
    with pytest.raises(RepairAssemblyError):
        apply(base, response, repair)
    assert repair["base_sha256"] == base_sha256(base)
