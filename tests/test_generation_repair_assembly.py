"""Pure candidate assembly regression; no model, credentials or GX is used."""
import copy
import pytest
from plc.candidate_repair import (
    RepairAssemblyError, assemble_validation_repair, candidate_base,
    check_candidate_containers, materialize_partial, validation_diagnostic,
)
from plc.validation import PLCJsonValidationError, validate_ladder_full
from plc.generation_contract import ladder_response_schema


def contact(kind, address):
    return {"type": kind, "address": address}


def rung37():
    # The user-reported repair, with all three priority branches and MOV outputs.
    return {"rung_id": 37, "header_element": {"type": "BLOCK_INPUT", "expression": "= D10 K0"},
        "shared_inputs": [contact("NO", "M0"), contact("NC", "M11"), contact("NC", "M5")],
        "branches": [{"branch_id": i + 1, "y_offset_level": i,
            "inputs": [contact("NC", "M" + str(20 + j)) for j in range(i)] + [contact("NO", "M" + str(20 + i))],
            "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K" + str(i), "D21"]},
                        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K" + str(i), "D30"]},
                        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"]}]} for i in range(3)]}


def ladder40(*, invalid=True):
    rungs = [{"rung_id": i, "header_element": None, "shared_inputs": [],
              "branches": [{"branch_id": 1, "y_offset_level": 0,
                "inputs": [contact("NO", "X0")], "outputs": [{"type": "APP_INSTR", "opcode": "MOV",
                    "operands": ["K" + str(i), "D" + str(100 + i)]}]}]} for i in range(1, 41)]
    rungs[36] = rung37()
    if invalid:
        rungs[36]["shared_inputs"].append({"type": "parallel_block", "branches": [
            [contact("NO", "M20")], [contact("NO", "M21")], [contact("NO", "M22")]]})
    return {"device_comments": {"X0": "原始需求输入", "D21": "目标层", "D30": "服务指针"}, "rungs": rungs}


def partial37():
    return {"mode": "partial", "device_comments": {}, "rungs": [rung37()]}


def test_exact_reported_patch_preserves_other_39_rungs_and_comments():
    invalid = ladder40()
    snapshot = copy.deepcopy(invalid)
    with pytest.raises(PLCJsonValidationError, match=r'shared_inputs\[3\].type'):
        validate_ladder_full(invalid)
    fixed = assemble_validation_repair(invalid, partial37())
    validate_ladder_full(fixed)
    assert len(fixed["rungs"]) == 40
    assert fixed == ladder40(invalid=False)
    assert invalid == snapshot
    assert fixed["device_comments"] == invalid["device_comments"]


@pytest.mark.parametrize("kind", ["delete", "add", "duplicate", "bool", "drop_full", "reorder_full", "no_op"])
def test_automatic_repair_cannot_destroy_or_ambiguously_merge_candidate(kind):
    original, patch = ladder40(), partial37()
    if kind == "delete": patch["delete_rung_ids"] = [1]
    if kind == "add": patch["rungs"][0]["rung_id"] = 100
    if kind == "duplicate": patch["rungs"] *= 2
    if kind == "bool": patch["rungs"][0]["rung_id"] = True
    if kind == "drop_full": patch = {"device_comments": {}, "rungs": [rung37()]}
    if kind == "reorder_full": patch = ladder40(invalid=False); patch["rungs"].reverse()
    if kind == "no_op": patch = {"mode": "partial", "rungs": [], "device_comments": {}}
    with pytest.raises((RepairAssemblyError, PLCJsonValidationError)):
        assemble_validation_repair(original, patch)
    assert original == ladder40()


def test_user_edit_inserts_and_deletes_but_validation_repair_does_not():
    original = ladder40(invalid=False)
    new = {**rung37(), "rung_id": 41}
    edited = materialize_partial(original, {"mode": "partial", "rungs": [new], "delete_rung_ids": [37]})
    assert [r["rung_id"] for r in edited["rungs"]] == [i for i in range(1, 42) if i != 37]
    assert original == ladder40(invalid=False)


@pytest.mark.parametrize("value", [None, [], {"rungs": [1]}, {"rungs": [{"branches": [None]}]},
    {"rungs": [{"branches": [{"outputs": ["MOV"]}]}]}])
def test_malformed_containers_are_schema_errors_not_python_attribute_errors(value):
    with pytest.raises(RepairAssemblyError):
        check_candidate_containers(value)


def test_schema_source_and_public_location_are_precise():
    schema = ladder_response_schema()
    rung = schema["properties"]["rungs"]["items"]["properties"]
    assert "parallel_block" not in str(rung["shared_inputs"])
    assert "parallel_block" in str(rung["branches"])
    assert len(ladder_response_schema(allow_partial=True)["oneOf"]) == 2
    error = PLCJsonValidationError("$.rungs[36].shared_inputs[3].type: unknown type 'parallel_block'")
    assert validation_diagnostic(error) == {"path": "content$.rungs.36.shared_inputs.3.type", "reason": "invalid_shared_input"}
    assert "secret-sentinel" not in str(validation_diagnostic(PLCJsonValidationError("$.secret_sentinel: secret-sentinel")))
