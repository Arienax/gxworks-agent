import copy

import pytest

from plc_generation import prepare_ladder_candidate
from plc_json_validator import PLCJsonValidationError


def _full_ladder(count=49):
    comments = {}
    rungs = []
    for index in range(count):
        suffix = format(index, "o")
        x_addr = f"X{suffix}"
        y_addr = f"Y{suffix}"
        comments[x_addr] = f"Input {index + 1}"
        comments[y_addr] = f"Output {index + 1}"
        rungs.append({
            "rung_id": index + 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": x_addr, "label": comments[x_addr]}],
                "outputs": [{"type": "COIL", "address": y_addr, "label": comments[y_addr]}],
            }],
        })
    return {"device_comments": comments, "rungs": rungs}


def test_explicit_repair_accepts_flattened_singleton_parallel_branches():
    """Provider may omit only the inner singleton arrays required by the contract."""
    baseline = _full_ladder()
    target = baseline["rungs"][43]
    target["shared_inputs"] = [{
        "type": "parallel_block",
        "branches": [
            [{"type": "NO", "address": "M0", "label": "Path A"}],
            [{"type": "NC", "address": "M1", "label": "Path B"}],
        ],
        "label": "invalid in shared_inputs",
    }]
    target["branches"][0]["inputs"] = []

    replacement = copy.deepcopy(target)
    replacement["shared_inputs"] = []
    replacement["branches"][0]["inputs"] = [{
        "type": "parallel_block",
        # Reported provider shape: one required array level is missing.
        "branches": [
            {"type": "NO", "address": "M0", "label": "Path A"},
            {"type": "NC", "address": "M1", "label": "Path B"},
        ],
        "label": "local parallel",
    }]
    partial = {
        "mode": "partial",
        "device_comments": {},
        "rungs": [replacement],
        "delete_rung_ids": [],
    }

    prepared = prepare_ladder_candidate(
        partial,
        plc_model="FX3U",
        previous_ladder=baseline,
        repair_mode=True,
        allowed_rung_ids={44},
        allowed_addresses={"M0", "M1", "Y53"},
        task_type="contract_repair",
    )

    ladder = prepared["ladder"]
    assert len(ladder["rungs"]) == 49
    repaired = ladder["rungs"][43]
    assert repaired["shared_inputs"] == []
    assert repaired["branches"][0]["inputs"][0]["branches"] == [
        [{"type": "NO", "address": "M0", "label": "Path A"}],
        [{"type": "NC", "address": "M1", "label": "Path B"}],
    ]
    assert ladder["rungs"][:43] == baseline["rungs"][:43]
    assert ladder["rungs"][44:] == baseline["rungs"][44:]
    assert any("parallel_block" in message for message in prepared["validation_messages"])


def test_parallel_normalization_does_not_accept_nested_parallel_blocks():
    candidate = _full_ladder(1)
    candidate["rungs"][0]["branches"][0]["inputs"] = [{
        "type": "parallel_block",
        "branches": [{
            "type": "parallel_block",
            "branches": [[{"type": "NO", "address": "M0"}]],
        }],
    }]

    with pytest.raises(PLCJsonValidationError):
        prepare_ladder_candidate(candidate, plc_model="FX3U")
