import copy

import pytest

from application.field_repair import apply, base_sha256, plan
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
                    "opcode": "NOT_A_REAL_OPCODE",
                    "operands": ["D0", "D1"],
                    "label": None,
                }],
            }],
        }],
    }


def test_opcode_failure_plans_one_json_pointer_field():
    base = _base()
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    assert repair["mode"] == "field_patch"
    assert repair["base_sha256"] == base_sha256(base)
    assert repair["target"]["path"] == "/rungs/0/branches/0/outputs/0/opcode"
    assert repair["target"]["context"]["operands"] == ["D0", "D1"]
    assert "MOV" in repair["target"]["value_schema"]["enum"]
    assert "NOT_A_REAL_OPCODE" not in repair["target"]["value_schema"]["enum"]


def test_field_patch_changes_only_authorized_scalar():
    base = _base()
    original = copy.deepcopy(base)
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    response = {
        "schema_version": 1,
        "mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "patches": [{"path": repair["target"]["path"], "value": "MOV"}],
    }
    result = apply(base, response, repair)
    assert base == original
    assert result["rungs"][0]["branches"][0]["outputs"][0]["opcode"] == "MOV"
    expected = copy.deepcopy(original)
    expected["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = "MOV"
    assert result == expected


def test_field_patch_rejects_wrong_path_or_baseline():
    base = _base()
    repair = plan(base, [{
        "path": "content$.rungs.0.branches.0.outputs.0.opcode",
        "reason": "invalid_ladder_structure",
    }], "FX3U")
    response = {
        "schema_version": 1,
        "mode": "field_patch",
        "base_sha256": repair["base_sha256"],
        "patches": [{"path": "/rungs/0/rung_id", "value": "MOV"}],
    }
    with pytest.raises(RepairAssemblyError):
        apply(base, response, repair)
    response["patches"][0] = {"path": repair["target"]["path"], "value": "MOV"}
    response["base_sha256"] = "0" * 64
    with pytest.raises(RepairAssemblyError):
        apply(base, response, repair)


def test_long_debug_note_uses_field_patch_instead_of_rung_replacement():
    base = _base()
    base["rungs"][0]["debug_note"] = "x" * 100
    repair = plan(base, [{
        "path": "content$.rungs.0.debug_note",
        "reason": "field_too_long",
    }], "FX3U")
    assert repair["target"]["path"] == "/rungs/0/debug_note"
    assert repair["target"]["value_schema"]["maxLength"] == 64
