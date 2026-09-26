import copy
import itertools
import json

import pytest

from plc.condition_normalizer import normalize_shared_conditions
from plc.ir import lower_rung_instructions
from plc.validation import validate_ladder_candidate_structure


def contact(address, kind="NO"):
    return {"type": kind, "address": address}


def coil(address):
    return {"type": "COIL", "address": address}


def app(opcode, *operands):
    return {"type": "APP_INSTR", "opcode": opcode, "operands": list(operands)}


def branch(inputs, *outputs):
    return {"branch_id": 1, "y_offset_level": 0, "inputs": inputs, "outputs": list(outputs)}


def rung(number, *branches, shared=None, header=None):
    result = {"rung_id": number, "header_element": header,
              "shared_inputs": shared or [], "branches": list(branches)}
    for index, item in enumerate(result["branches"]):
        item["branch_id"] = index + 1
        item["y_offset_level"] = index
    return result


def ladder(*rungs):
    return {"device_comments": {}, "rungs": list(rungs)}


def assert_idempotent(original, **kwargs):
    saved = copy.deepcopy(original)
    normalized, summary = normalize_shared_conditions(original, **kwargs)
    assert original == saved
    json.dumps(summary)
    again, second_summary = normalize_shared_conditions(normalized, **kwargs)
    assert again == normalized
    assert second_summary["changes"] == []
    return normalized, summary


def test_removes_duplicate_series_and_shared_branch_conditions():
    source = ladder(rung(1, branch([contact("X1"), contact("X3"), contact("X1")], coil("Y2")),
                         shared=[contact("X1"), contact("X3"), contact("X1")]))
    result, summary = assert_idempotent(source)
    assert result["rungs"][0]["shared_inputs"] == [contact("X1"), contact("X3")]
    assert result["rungs"][0]["branches"][0]["inputs"] == []
    assert summary["changes"]
    validate_ladder_candidate_structure(result)


def test_extracts_only_ordered_common_prefix():
    source = ladder(rung(1,
        branch([contact("M0"), contact("X1")], coil("Y0")),
        branch([contact("M0"), contact("X3")], coil("Y2"))))
    result, summary = assert_idempotent(source)
    assert result["rungs"][0]["shared_inputs"] == [contact("M0")]
    assert [b["inputs"] for b in result["rungs"][0]["branches"]] == [[contact("X1")], [contact("X3")]]
    assert any(item["operation"] == "extract_common_prefix" for item in summary["changes"])
    validate_ladder_candidate_structure(result)


def test_merges_adjacent_equal_conditions_preserving_output_order_and_ids():
    source = ladder(
        rung(10, branch([contact("X1"), contact("X3")], coil("Y0"))),
        rung(20, branch([contact("X1"), contact("X3")], coil("Y2"))),
        rung(30, branch([contact("X1"), contact("X3")], coil("Y4"))))
    result, summary = assert_idempotent(source)
    assert [r["rung_id"] for r in result["rungs"]] == [10]
    assert [o["address"] for b in result["rungs"][0]["branches"] for o in b["outputs"]] == ["Y0", "Y2", "Y4"]
    assert all(not b["inputs"] for b in result["rungs"][0]["branches"])
    assert len([c for c in summary["changes"] if c["operation"] == "merge_adjacent_coils"]) == 2
    validate_ladder_candidate_structure(result)


@pytest.mark.parametrize("output", [app("SET", "M0"), app("RST", "M0"),
    app("UNKNOWN", "M0"), app("CALL", "P0"), app("MOV", "K0", "D0Z0"),
    {"type": "PLS", "address": "M0"}])
def test_stateful_or_unknown_output_is_a_hoisting_barrier(output):
    source = ladder(rung(1, branch([], output), branch([contact("M0")], coil("Y0")), shared=[contact("M0")]))
    result, summary = assert_idempotent(source)
    assert result == source
    assert summary["skipped"]


def test_shared_duplicate_after_coil_write_is_not_removed():
    source = ladder(rung(1, branch([], coil("M0")), branch([contact("M0", "NC")], coil("Y0")),
                         shared=[contact("M0", "NC")]))
    result, summary = assert_idempotent(source)
    assert result == source
    assert any(item["reason"] == "read_after_write" for item in summary["skipped"])


@pytest.mark.parametrize("condition", [contact("X0", "P"), contact("X0", "F"), contact("T0"),
    contact("M8013"), {"type": "COMPARE", "expression": "= D0Z0 K1"},
    {"type": "parallel_block", "branches": [[contact("X0")], [contact("X1")]]}])
def test_edges_state_indirect_and_parallel_inputs_are_barriers(condition):
    source = ladder(rung(1, branch([copy.deepcopy(condition), copy.deepcopy(condition)], coil("Y0"))),
                    rung(2, branch([copy.deepcopy(condition), copy.deepcopy(condition)], coil("Y1"))))
    result, summary = assert_idempotent(source)
    assert result == source
    assert summary["skipped"]


def test_scope_prevents_changes_and_merging_across_its_boundary():
    source = ladder(rung(1, branch([contact("X1"), contact("X1")], coil("Y0"))),
                    rung(2, branch([contact("X1"), contact("X1")], coil("Y2"))))
    result, summary = assert_idempotent(source, allowed_rung_ids={1})
    assert len(result["rungs"]) == 2
    assert result["rungs"][1] == source["rungs"][1]
    assert result["rungs"][0]["branches"][0]["inputs"] == [contact("X1")]
    unchanged, _ = normalize_shared_conditions(source, allowed_rung_ids=set())
    assert unchanged == source


def test_no_global_or_merge_or_nonadjacent_reordering():
    sources = [
        ladder(rung(1, branch([contact("X0")], coil("Y0"))), rung(2, branch([contact("X1")], coil("Y0")))),
        ladder(rung(1, branch([contact("X0")], coil("Y0"))),
               rung(2, branch([contact("Y0")], coil("M1"))), rung(3, branch([contact("X0")], coil("Y2")))),
        ladder(rung(1, branch([contact("M0")], app("RST", "M0"))), rung(2, branch([contact("M0")], coil("Y0")))),
    ]
    for source in sources:
        result, _ = assert_idempotent(source)
        assert result == source


def test_different_network_notes_survive_common_condition_merge():
    source = ladder(rung(1, branch([contact("X0")], coil("Y0"))),
                    rung(2, branch([contact("X0")], coil("Y1"))))
    source["rungs"][0]["debug_note"] = "First purpose"
    source["rungs"][1]["debug_note"] = "Second purpose"
    result, summary = assert_idempotent(source)
    assert len(result["rungs"]) == 1
    assert result["rungs"][0]["debug_note"] == "First purpose\nSecond purpose"
    assert summary["changes"][-1]["source_rung"] == source["rungs"][1]
    assert summary["changes"][-1]["previous_target_rung"] == source["rungs"][0]


def test_ordinary_address_aliases_still_detect_write_after_read_dependencies():
    source = ladder(rung(1, branch([], coil("M000")), branch([contact("M0", "NC")], coil("Y0")),
                         shared=[contact("M00", "NC")]))
    result, summary = assert_idempotent(source)
    assert result == source
    assert any(item["reason"] == "read_after_write" for item in summary["skipped"])


@pytest.mark.parametrize("value", [None, [], {"rungs": None}, {"rungs": [None]},
    {"rungs": [{"rung_id": 1, "branches": "unknown"}]}])
def test_malformed_or_unsupported_containers_are_left_for_existing_validator(value):
    result, summary = normalize_shared_conditions(value)
    assert result == value
    assert summary["skipped"]


def test_opaque_condition_and_invalid_scope_do_not_raise_or_mutate():
    source = ladder(rung(1, branch([{"type": ["opaque"]}], coil("Y0"))))
    result, summary = normalize_shared_conditions(source)
    assert result == source and summary["skipped"]
    for scope in ([[]], "1", {True}):
        result, summary = normalize_shared_conditions(source, allowed_rung_ids=scope)
        assert result == source and summary["skipped"]


def test_merge_does_not_hide_duplicate_rung_identity_from_api_validation():
    source = ladder(rung(1, branch([contact("X0")], coil("Y0"))),
                    rung(1, branch([contact("X0")], coil("Y1"))))
    result, summary = assert_idempotent(source)
    assert result == source
    assert summary["skipped"][0]["reason"] == "duplicate_rung_ids"


def scan(program, values, timers):
    """Independent small sequential VM, consuming the actual shared lowering."""
    values = dict(values)
    timers = dict(timers)
    writes = []

    def word(token):
        if token.startswith("K"):
            return int(token[1:])
        return values.get(token, 0)

    for network in program["rungs"]:
        acc, blocks, saved = None, [], []
        for instruction in lower_rung_instructions(network):
            op, args = instruction["op"], instruction["args"]
            base = next((prefix for prefix in ("AND", "OR", "LD") if op.startswith(prefix)), None)
            if op in {"ANB", "ORB"}:
                prior = blocks.pop()
                acc = (prior and acc) if op == "ANB" else (prior or acc)
            elif op == "MPS":
                saved.append(acc)
            elif op == "MRD":
                acc = saved[-1]
            elif op == "MPP":
                acc = saved.pop()
            elif base or op == "ANI":
                if op in {"LD", "LDI", "AND", "ANI", "OR", "ORI"}:
                    condition = bool(values.get(args[0], 0))
                    condition = not condition if op in {"LDI", "ANI", "ORI"} else condition
                    base = "AND" if op == "ANI" else base
                else:
                    left, right = map(word, args)
                    symbol = op[len(base):]
                    condition = {"=": left == right, "<>": left != right, ">": left > right,
                                 "<": left < right, "<=": left <= right, ">=": left >= right}[symbol]
                if base == "LD":
                    if acc is not None:
                        blocks.append(acc)
                    acc = condition
                elif base == "AND":
                    acc = acc and condition
                else:
                    acc = acc or condition
            elif op == "OUT":
                if args[0].startswith("T"):
                    timers[args[0]] = timers.get(args[0], 0) + 1 if acc else 0
                    value = int(timers[args[0]] >= word(args[1]))
                else:
                    value = int(bool(acc))
                values[args[0]] = value
                writes.append((args[0], value))
            elif op in {"SET", "RST", "MOV"}:
                if acc:
                    address, value = (args[1], word(args[0])) if op == "MOV" else (args[0], int(op == "SET"))
                    values[address] = value
                    writes.append((address, value))
            else:
                raise AssertionError("Test VM does not support " + op)
        assert not saved
    return values, timers, writes


def test_positive_and_dependency_cases_are_equivalent_on_every_scan():
    compare = {"type": "COMPARE", "expression": ">= D0 K1"}
    programs = [
        ladder(rung(1, branch([contact("X0"), contact("X0")], coil("Y0"))),
               rung(2, branch([contact("X0")], coil("Y2")))),
        ladder(rung(1, branch([contact("M0"), contact("X0")], coil("Y0")),
                       branch([contact("M0"), contact("X1", "NC")], coil("Y2")))),
        ladder(rung(1, branch([compare, copy.deepcopy(compare)], coil("Y0")))),
        ladder(rung(1, branch([], app("RST", "M0")), branch([contact("M0")], coil("Y0")), shared=[contact("M0")])),
        ladder(rung(1, branch([], coil("M0")), branch([contact("M0", "NC")], coil("Y0")), shared=[contact("M0", "NC")])),
        ladder(rung(1, branch([contact("X0"), contact("X0")], {"type": "TIMER", "address": "T0", "value": "K2"})),
               rung(2, branch([contact("T0")], coil("Y0")))),
    ]
    for source in programs:
        normalized, _ = assert_idempotent(source)
        for x0, x1, m0, y0, d0 in itertools.product((0, 1), (0, 1), (0, 1), (0, 1), (-1, 0, 1, 2)):
            left = right = {"X0": x0, "X1": x1, "M0": m0, "Y0": y0, "D0": d0}
            lt, rt = {}, {}
            for tick in range(5):
                for state in (left, right):
                    state.update(X0=x0 if tick in {0, 1, 4} else 1 - x0,
                                 X1=x1 if tick % 2 else 1 - x1, D0=d0 + tick % 2)
                left, lt, lw = scan(source, left, lt)
                right, rt, rw = scan(normalized, right, rt)
                assert (left, lt, lw) == (right, rt, rw)


def test_scan_oracle_detects_the_guard_and_last_write_counterexamples():
    shared_write = ladder(rung(1, branch([], coil("M0")),
                                branch([contact("M0", "NC")], coil("Y0")), shared=[contact("M0", "NC")]))
    wrongly_deduplicated = copy.deepcopy(shared_write)
    wrongly_deduplicated["rungs"][0]["branches"][1]["inputs"] = []
    assert scan(shared_write, {"M0": 0}, {})[0]["Y0"] == 0
    assert scan(wrongly_deduplicated, {"M0": 0}, {})[0]["Y0"] == 1

    reset_then_read = ladder(rung(1, branch([contact("M0")], app("RST", "M0"))),
                            rung(2, branch([contact("M0")], coil("Y0"))))
    wrongly_hoisted = ladder(rung(1, branch([], app("RST", "M0")), branch([], coil("Y0")), shared=[contact("M0")]))
    assert scan(reset_then_read, {"M0": 1}, {})[0]["Y0"] == 0
    assert scan(wrongly_hoisted, {"M0": 1}, {})[0]["Y0"] == 1

    duplicate_writer = ladder(rung(1, branch([contact("X0")], coil("Y0"))),
                              rung(2, branch([contact("X1")], coil("Y0"))))
    wrongly_or_merged = ladder(rung(1, branch([{"type": "parallel_block", "branches": [
        [contact("X0")], [contact("X1")]]}], coil("Y0"))))
    assert scan(duplicate_writer, {"X0": 1, "X1": 0}, {})[0]["Y0"] == 0
    assert scan(wrongly_or_merged, {"X0": 1, "X1": 0}, {})[0]["Y0"] == 1


@pytest.mark.parametrize("output", [app("MOV", "K0", "D0"),
    {"type": "TIMER", "address": "T0", "value": "K2"},
    {"type": "PLS", "address": "M2"}])
def test_known_outputs_only_block_predicates_in_their_effect_footprint(output):
    source = ladder(rung(1, branch([], output), branch([contact("M0")], coil("Y0")), shared=[contact("M0")]))
    result, summary = assert_idempotent(source)
    assert result["rungs"][0]["branches"][1]["inputs"] == []
    assert result["rungs"][0]["branches"][0]["outputs"] == [output]
    assert any(c["operation"] == "remove_duplicate_condition" for c in summary["changes"])


def test_adjacent_common_prefix_retains_distinct_suffixes_and_effect_order():
    source = ladder(rung(10, branch([contact("M0"), contact("X0")], coil("Y0"))),
                    rung(20, branch([contact("X1", "NC")], app("MOV", "K7", "D10")), header=contact("M0")),
                    rung(30, branch([contact("M0"), contact("X2")], coil("Y2"))))
    result, summary = assert_idempotent(source)
    assert len(result["rungs"]) == 1
    merged = result["rungs"][0]
    assert merged["shared_inputs"] == [contact("M0")]
    assert [b["inputs"] for b in merged["branches"]] == [[contact("X0")], [contact("X1", "NC")], [contact("X2")]]
    assert [b["outputs"] for b in merged["branches"]] == [r["branches"][0]["outputs"] for r in source["rungs"]]
    assert any(c["operation"] == "merge_adjacent_branches" for c in summary["changes"])
    validate_ladder_candidate_structure(result)


def test_parallel_suffix_is_not_expanded_or_moved_ahead_of_shared_guard():
    either = {"type": "parallel_block", "branches": [[contact("X1")], [contact("X2")]]}
    source = ladder(rung(1, branch([contact("M0"), either], coil("Y0"))),
                    rung(2, branch([contact("M0"), contact("X3")], coil("Y1"))))
    result, _ = assert_idempotent(source)
    assert result["rungs"][0]["shared_inputs"] == [contact("M0")]
    assert result["rungs"][0]["branches"][0]["inputs"] == [either]
    validate_ladder_candidate_structure(result)


@pytest.mark.parametrize("output", [app("DMOV", "K1", "D9"), app("BMOV", "D20", "D8", "K8"),
                                    app("FMOV", "K0", "D8", "K8"), app("ZRST", "D8", "D20")])
def test_word_and_range_writes_cannot_hide_a_guard_dependency(output):
    guard = {"type": "COMPARE", "expression": ">= D10 K1"}
    source = ladder(rung(1, branch([guard], output)), rung(2, branch([guard], coil("Y0"))))
    result, summary = assert_idempotent(source)
    assert result == source
    assert any(s["reason"] == "read_after_write" for s in summary["skipped"])


def test_shrinking_an_existing_shared_prefix_does_not_duplicate_its_evaluation():
    source = ladder(rung(1, branch([], coil("M1")), branch([], coil("Y0")), shared=[contact("M0"), contact("M1", "NC")]),
                    rung(2, branch([contact("M0")], coil("Y1"))))
    result, _ = assert_idempotent(source)
    assert result == source
    assert scan(result, {"M0": 1, "M1": 0}, {}) == scan(source, {"M0": 1, "M1": 0}, {})


def test_general_prefix_merging_preserves_multiscan_state_and_write_trace():
    parallel = {"type": "parallel_block", "branches": [[contact("X1")], [contact("X2", "NC")]]}
    programs = [
        ladder(rung(1, branch([contact("M0"), contact("X0")], coil("Y0"))),
               rung(2, branch([contact("M0"), parallel], app("MOV", "K2", "D10"))),
               rung(3, branch([contact("M0"), contact("Y0")], coil("Y1")))),
        ladder(rung(1, branch([contact("M0")], {"type": "TIMER", "address": "T0", "value": "K2"})),
               rung(2, branch([contact("M0"), contact("T0")], coil("Y1")))),
        ladder(rung(1, branch([contact("X0"), contact("M0")], app("RST", "M0"))),
               rung(2, branch([contact("X0"), contact("M0")], coil("Y0")))),
    ]
    for source in programs:
        normalized, _ = assert_idempotent(source)
        assert len(normalized["rungs"]) < len(source["rungs"])
        for bits in itertools.product((0, 1), repeat=5):
            left = dict(zip(("M0", "X0", "X1", "X2", "Y0"), bits))
            right = dict(left)
            lt, rt = {}, {}
            for tick in range(8):
                for state in (left, right):
                    state.update(X0=(bits[1] + tick) % 2, X1=(bits[2] + tick // 2) % 2)
                left, lt, lw = scan(source, left, lt)
                right, rt, rw = scan(normalized, right, rt)
                assert (left, lt, lw) == (right, rt, rw)


def test_candidate_preparation_uses_shared_normalization_without_model_repair():
    from plc.generation import prepare_ladder_candidate
    source = ladder(rung(10, branch([contact("M0"), contact("X0")], coil("Y0"))),
                    rung(20, branch([contact("M0"), contact("X1")], coil("Y1"))))
    result = prepare_ladder_candidate(source, plc_model="FX3U")
    assert len(result["ladder"]["rungs"]) == 1
    assert len(result["program_ir"]["networks"]) == 1
    assert result["normalization"]["changes"]


@pytest.mark.parametrize("field", ["debug_note", "label"])
def test_annotation_capacity_keeps_valid_candidates_valid_without_truncation(field):
    from plc.generation_contract import MAX_LABEL_LEN
    from plc.generation import prepare_ladder_candidate
    source = ladder(rung(1, branch([contact("M0")], coil("Y0"))),
                    rung(2, branch([contact("M0")], coil("Y1"))))
    for index, network in enumerate(source["rungs"]):
        text = ("A" if index == 0 else "B") * MAX_LABEL_LEN
        if field == "label":
            network["branches"][0]["inputs"][0]["label"] = text
        else:
            network[field] = text
    result, summary = assert_idempotent(source)
    assert result == source
    assert any(item["reason"] == "annotation_capacity" for item in summary["skipped"])
    assert prepare_ladder_candidate(source, plc_model="FX3U")["ladder"] == source


def test_local_factoring_preserves_distinct_short_condition_annotations():
    source = ladder(rung(1, branch([contact("M0") | {"label": "Run permit"}], coil("Y0")),
                             branch([contact("M0") | {"label": "Conveyor enable"}], coil("Y1"))))
    result, _ = assert_idempotent(source)
    assert result["rungs"][0]["shared_inputs"][0]["label"] == "Run permit\nConveyor enable"
    validate_ladder_candidate_structure(result)
