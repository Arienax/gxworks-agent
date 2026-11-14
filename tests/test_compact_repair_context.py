"""Repair presentation must preserve the staged program, not reinterpret it."""
import copy
import json

import pytest
from hypothesis import given, settings, strategies as st

from application.compact_protocol import compact_repair_rungs, expand_compact_partial


def element(kind, **fields):
    return {"type": kind, **fields}


inputs = st.one_of(
    st.builds(element, st.sampled_from(["NO", "NC", "P", "F"]),
              address=st.sampled_from(["X0", "M12", "SM400"])),
    st.builds(element, st.just("COMPARE"), expression=st.sampled_from([
        ">= D0 K1", "< D10 K32767", "= D1Z0 K0"])),
)
branch_inputs = st.one_of(inputs, st.builds(element, st.just("parallel_block"),
    branches=st.lists(st.lists(inputs, min_size=1, max_size=3), min_size=1, max_size=3)))
outputs = st.sampled_from([
    element("COIL", address="Y0"), element("PLS", address="M10"), element("PLF", address="M11"),
    element("TIMER", address="T0", value="K10"), element("COUNTER", address="C0", value="D2"),
    element("APP_INSTR", opcode="SET", operands=["M20"]),
    element("APP_INSTR", opcode="RST", operands=["M20"]),
    element("APP_INSTR", opcode="DMOV", operands=["K65536", "D100"]),
    element("APP_INSTR", opcode="MOV", operands=["D0Z0", "D1Z1"]),
])


@st.composite
def canonical_rungs(draw):
    rows = []
    for identity in draw(st.lists(st.integers(1, 500), min_size=1, max_size=5, unique=True)):
        branches = []
        for index in range(draw(st.integers(1, 4))):
            branches.append({"branch_id": index + 1, "y_offset_level": index,
                             "inputs": draw(st.lists(branch_inputs, max_size=3)),
                             "outputs": copy.deepcopy(draw(st.lists(outputs, min_size=1, max_size=4)))})
        rows.append({"rung_id": identity, "header_element": None,
                     "shared_inputs": draw(st.lists(inputs, max_size=3)), "branches": branches})
    return rows


@given(canonical_rungs())
@settings(max_examples=100, deadline=None)
def test_short_context_roundtrips_all_values_and_preserves_source(rows):
    before = copy.deepcopy(rows)
    short = compact_repair_rungs(rows)
    assert short is not None
    restored = expand_compact_partial({"mode": "partial", "r": short,
                                       "device_comments": [], "delete_rung_ids": []})
    assert restored["rungs"] == before
    assert rows == before
    assert len(json.dumps(short)) < len(json.dumps(rows))


@pytest.mark.parametrize("extra", ["element_metadata", "rung_metadata", "layout", "branch_id", "header", "unsupported_compare"])
def test_unsupported_information_is_not_silently_dropped(extra):
    rows = [{"rung_id": 8, "header_element": None, "shared_inputs": [], "branches": [
        {"branch_id": 1, "y_offset_level": 0, "inputs": [], "outputs": [{"type": "COIL", "address": "Y0"}]}]}]
    if extra == "element_metadata":
        rows[0]["branches"][0]["outputs"][0]["annotation"] = "retain"
    elif extra == "rung_metadata":
        rows[0]["comment"] = "retain"
    elif extra == "layout":
        rows[0]["branches"][0]["y_offset_level"] = 4
    elif extra == "branch_id":
        rows[0]["branches"][0]["branch_id"] = 9
    elif extra == "header":
        rows[0]["header_element"] = element("NO", address="X0")
    else:
        rows[0]["branches"][0]["inputs"] = [element("COMPARE", expression="D>= D0 K65536")]
    before = copy.deepcopy(rows)
    assert compact_repair_rungs(rows) is None
    assert rows == before


@pytest.mark.parametrize("phase", ["direct", "generate", "edit"])
def test_second_repair_only_adds_changed_context_and_keeps_budget(phase):
    from types import SimpleNamespace
    from application.ladder_model_session import LadderModelSession

    rows = [{"rung_id": 8, "header_element": None, "shared_inputs": [], "branches": [
        {"branch_id": 1, "y_offset_level": 0, "inputs": [], "outputs": [
            element("APP_INSTR", opcode="MOV", operands=[f"D{i}", f"D{i+100}"]) for i in range(20)]}]}]
    candidate = SimpleNamespace(issues=[{"category": "behavior", "rung_ids": [8]}],
                                repair_base={"rungs": rows}, scope={8})
    session = LadderModelSession(phase=phase, candidate=candidate)
    first = session._repair()
    original = copy.deepcopy(first)
    candidate.repair_base = copy.deepcopy(candidate.repair_base)
    candidate.repair_base["rungs"][0]["branches"][0]["outputs"][0]["operands"][0] = "D50"
    second = session._repair()
    assert second["baseline_changes"] == {"set": {"/0/b/0/o/0": "APP MOV D50 D100"}}
    assert "baseline_rungs" not in second
    assert second["allowed_rung_ids"] == [8] and second["diagnostics"] == first["diagnostics"]
    assert second["attempt"] == second["max_attempts"] == 2
    assert first == original
    assert session.last_repair["baseline_rungs"][0]["b"][0]["o"][0] == "APP MOV D50 D100"
    candidate.issues = [{"category": "behavior", "rung_ids": [8], "actual": "still incorrect"}]
    assert session._repair() is None


def test_context_delta_keeps_full_context_when_it_is_smaller_or_format_differs():
    from application.ladder_model_session import _repair_context_delta

    repair = {"action": "partial", "baseline_format": "compact_ladder/1.3",
              "baseline_rungs": [{"rung_id": 1, "c": [], "b": []}], "instruction": "fix"}
    assert _repair_context_delta(repair, copy.deepcopy(repair)) == repair
    other_format = {**repair, "baseline_format": "ladder_v1"}
    assert _repair_context_delta(repair, other_format) == repair
