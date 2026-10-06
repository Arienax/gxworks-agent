import copy
import itertools
import json

import pytest
from hypothesis import given, settings, strategies as st

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


@pytest.mark.parametrize("address,model,stable", [
    ("T0", "FX3U", True), ("T245", "FX3U", True), ("T246", "FX3U", False),
    ("C0", "FX3U", True), ("C99", "FX3U", True), ("C200", "FX3U", False),
    ("T1000", "FX5U", True), ("C0", "FX5U", False),
    ("M8000", "FX3U", True), ("M8013", "FX3U", False),
    ("M8000", "FX5U", False), ("SM400", "FX5U", True), ("SM402", "FX5U", False),
])
def test_condition_stability_uses_exact_cpu_runtime_facts(address, model, stable):
    from plc.condition_analysis import ConditionAnalysis
    assert (ConditionAnalysis(model).condition(contact(address)).identity is not None) == stable


def test_all_input_and_output_tags_are_classified_including_legacy_forms():
    from plc.condition_analysis import ConditionAnalysis
    from plc.validation import VALID_INPUT_TYPES, VALID_OUTPUT_TYPES
    analysis = ConditionAnalysis()
    inputs = {kind: contact("X0", kind) for kind in ("NO", "NC", "P", "RISING", "F", "FALLING")}
    inputs.update(COMPARE={"type": "COMPARE", "expression": "= D0 K1"},
                  BLOCK_INPUT={"type": "BLOCK_INPUT", "expression": "D0 == K1"},
                  parallel_block={"type": "parallel_block", "branches": [[contact("X0")], [contact("X1")]]})
    outputs = {kind: {"type": kind, "address": "M0"} for kind in ("COIL", "PLS", "PLF")}
    outputs.update(TIMER={"type": "TIMER", "address": "T0", "value": "K2"},
                   COUNTER={"type": "COUNTER", "address": "C0", "value": "K2"},
                   APP_INSTR=app("MOV", "K1", "D0"),
                   BLOCK_OUTPUT={"type": "BLOCK_OUTPUT", "expression": "MOV K1 D0"})
    assert set(inputs) == VALID_INPUT_TYPES and set(outputs) == VALID_OUTPUT_TYPES
    for value in inputs.values():
        classified = analysis.condition(value)
        assert classified.identity is not None or classified.reason == "edge_evaluation_site"
    assert analysis.condition(inputs["COMPARE"]).identity == analysis.condition(inputs["BLOCK_INPUT"]).identity
    assert all(analysis.output(value).writes is not None for value in outputs.values())


@pytest.mark.parametrize("output,writes,precision", [
    (app("MOV", "K0", "D9"), {"D9"}, "definition"),
    (app("DMOV", "K0", "D9"), {"D9", "D10"}, "definition"),
    (app("CMP", "D0", "K2", "M9"), {"M9", "M10", "M11"}, "definition"),
    (app("BMOV", "D0", "D9", "K3"), {"D*"}, "family"),
    (app("ZRST", "M9", "M11"), {"M9", "M10", "M11"}, "exact"),
    (app("BMOV", "D0", "D9", "D20"), {"D*"}, "family"),
    (app("MOV", "D0", "D9Z0"), None, "unknown"),
    (app("CALL", "P0"), None, "unknown"),
    (app("MPS"), None, "unknown"),
    (app("UNLISTED", "M0"), None, "unknown"),
    (coil("M8034"), None, "unknown"),
    (app("RST", "D8030"), None, "unknown"),
])
def test_effects_use_existing_definitions_or_explicit_conservative_fallback(output, writes, precision):
    from plc.condition_analysis import ConditionAnalysis
    result = ConditionAnalysis().output(output)
    assert result.writes == writes and result.precision == precision
    if writes is None:
        assert result.reason


def test_late_shorter_prefix_cannot_expand_an_already_factored_subgroup():
    source = ladder(*(rung(i, branch([contact("X0"), contact("M0"), contact(f"X{i}")], coil(f"Y{i}")))
                      for i in range(1, 5)), rung(5, branch([contact("X0"), contact("X5")], coil("Y5"))))
    result, report = assert_idempotent(source)
    assert len(result["rungs"]) == 2
    assert result["rungs"][0]["shared_inputs"] == [contact("X0"), contact("M0")]
    assert report["statistics"] == {"conditions_before": 14, "conditions_after": 8,
                                    "networks_before": 5, "networks_after": 2}


def test_subgroups_within_one_rung_are_factored_without_changing_other_source_ids():
    source = ladder(rung(10, *(branch([contact("M0"), contact(f"X{i}")], coil(f"Y{i}")) for i in range(4)),
                         branch([contact("X4")], coil("Y4")), shared=[contact("X0")]),
                    rung(20, branch([contact("X1")], coil("Y5"))))
    result, report = assert_idempotent(source)
    assert [r["rung_id"] for r in result["rungs"]] == [10, 21, 20]
    assert result["rungs"][-1] == source["rungs"][-1]
    assert result["rungs"][0]["shared_inputs"] == [contact("X0"), contact("M0")]
    assert any(c["operation"] == "factor_branch_subgroup" for c in report["changes"])
    validate_ladder_candidate_structure(result)


def test_scoped_subgroups_keep_network_ids_and_pass_existing_scope_enforcement():
    from plc.candidate_service import CandidateService
    from plc.change_scope import enforce_change_scope
    from plc.ir import build_plc_ir
    source = ladder(rung(10, *(branch([contact("M0"), contact(f"X{i}")], coil(f"Y{i}")) for i in range(4)),
                         branch([contact("X4")], coil("Y4")), shared=[contact("X0")]),
                    rung(20, branch([contact("X1")], coil("Y5"))))
    original = build_plc_ir(source)
    patch = {"mode": "partial", "rungs": [copy.deepcopy(source["rungs"][0])]}
    patch["rungs"][0]["branches"][1]["inputs"].append(contact("X1"))
    prepared = CandidateService().prepare(patch, previous_ladder=source)
    result = prepared["ladder"]
    assert [r["rung_id"] for r in result["rungs"]] == [10, 20]
    assert result["rungs"][-1] == source["rungs"][-1]
    enforce_change_scope(original, prepared["program_ir"], {"network_ids": ["N0010"]})


def test_pure_parallel_arms_factor_and_deduplicate_but_remain_valid_protocol():
    either = {"type": "parallel_block", "branches": [
        [contact("X0"), contact("X1")], [contact("X0"), contact("X2")]]}
    source = ladder(rung(1, branch([either, copy.deepcopy(either)], coil("Y0"))))
    result, _ = assert_idempotent(source)
    assert result["rungs"][0]["branches"][0]["inputs"] == [contact("X0"), {
        "type": "parallel_block", "branches": [[contact("X1")], [contact("X2")]]}]
    validate_ladder_candidate_structure(result)


def test_parallel_intersection_handles_different_positions_and_keeps_absorbed_device_notes():
    source = ladder(rung(1, branch([{"type": "parallel_block", "branches": [
        [contact("X0"), contact("M0")], [contact("X1"), contact("M0")]]}], coil("Y0"))))
    result, _ = assert_idempotent(source)
    assert result["rungs"][0]["branches"][0]["inputs"] == [contact("M0"), {
        "type": "parallel_block", "branches": [[contact("X0")], [contact("X1")]]}]
    annotated = ladder(rung(1, branch([{"type": "parallel_block", "branches": [
        [contact("X0")], [contact("X0"), {**contact("X1"), "label": "Separate device purpose"}]]}], coil("Y0"))))
    result, report = assert_idempotent(annotated)
    assert result == annotated
    assert any(s["reason"] == "annotated_absorption" for s in report["skipped"])


@pytest.mark.parametrize("kind", ["P", "RISING", "F", "FALLING"])
def test_shared_edge_stays_single_while_pure_common_tail_is_extracted(kind):
    source = ladder(rung(1, branch([contact("M0"), contact("X1")], coil("Y0")),
        branch([contact("M0"), contact("X2")], coil("Y1")), header=contact("X0", kind)))
    result, _ = assert_idempotent(source)
    assert len(result["rungs"]) == 1 and result["rungs"][0]["header_element"] == contact("X0", kind)
    assert result["rungs"][0]["shared_inputs"] == [contact("M0")]


def test_pure_conjunction_can_share_conditions_in_different_positions():
    source = ladder(rung(1, branch([contact("X0"), contact("M0")], coil("Y0"))),
                    rung(2, branch([contact("X1"), contact("M0")], coil("Y1"))))
    result, _ = assert_idempotent(source)
    assert len(result["rungs"]) == 1
    assert result["rungs"][0]["shared_inputs"] == [contact("M0")]
    assert [b["inputs"] for b in result["rungs"][0]["branches"]] == [[contact("X0")], [contact("X1")]]


@given(st.lists(st.sets(st.sampled_from(["X0", "X1", "M0", "T0"]), min_size=1), min_size=2, max_size=7))
@settings(max_examples=60, deadline=None, derandomize=True)
def test_partition_cost_matches_independent_exhaustive_contiguous_partition_oracle(paths):
    source = ladder(*(rung(i + 1, branch([contact(a) for a in sorted(path)], coil(f"Y{i}"))) for i, path in enumerate(paths)))
    result, report = assert_idempotent(source)
    costs = []
    for cuts in itertools.product([False, True], repeat=len(paths) - 1):
        ends = [i + 1 for i, cut in enumerate(cuts) if cut] + [len(paths)]
        start, total = 0, 0
        for end in ends:
            common = set.intersection(*paths[start:end])
            if end - start > 1 and not common:
                break
            total += sum(map(len, paths[start:end])) - (end - start - 1) * len(common)
            start = end
        else:
            costs.append((total, len(ends)))
    assert (report["statistics"]["conditions_after"], len(result["rungs"])) == min(costs)


_plain_condition = st.one_of(
    st.builds(contact, st.sampled_from(["X0", "X1", "M0", "M1", "T0", "C0", "Y0"]), st.sampled_from(["NO", "NC"])),
    st.builds(lambda op, address, n: {"type": "COMPARE", "expression": f"{op} {address} K{n}"},
              st.sampled_from(["=", ">=", "<"]), st.sampled_from(["D0", "T0", "C0"]), st.integers(0, 3)))
_condition = st.one_of(_plain_condition,
    st.builds(contact, st.sampled_from(["X0", "M0"]), st.sampled_from(["P", "F"])),
    st.builds(lambda arms: {"type": "parallel_block", "branches": arms},
              st.lists(st.lists(_plain_condition, min_size=1, max_size=3), min_size=1, max_size=3)))
_output = st.one_of(
    st.builds(coil, st.sampled_from(["Y0", "Y1", "M0", "M1"])),
    st.builds(lambda op, address: app(op, address), st.sampled_from(["SET", "RST"]), st.sampled_from(["M0", "M1"])),
    st.just(app("MOV", "K1", "D0")),
    st.sampled_from([{"type": "TIMER", "address": "T0", "value": "K2"},
                    {"type": "COUNTER", "address": "C0", "value": "K2"},
                    {"type": "PLS", "address": "M0"}, {"type": "PLF", "address": "M1"}]))


@st.composite
def sequential_programs(draw):
    result = []
    for index in range(draw(st.integers(1, 5))):
        shared = draw(st.lists(_condition, max_size=2).filter(lambda items: all(x["type"] != "parallel_block" for x in items)))
        branches = []
        for _ in range(draw(st.integers(1, 4))):
            branches.append(branch(draw(st.lists(_condition, max_size=4)), *draw(st.lists(_output, min_size=1, max_size=2))))
        result.append(rung(index + 1, *branches, shared=shared))
    return ladder(*result)


@given(sequential_programs(), st.lists(st.tuples(st.booleans(), st.booleans(), st.integers(0, 3)), min_size=3, max_size=8))
@settings(max_examples=250, deadline=None, derandomize=True)
def test_generated_normalizations_preserve_every_scan_and_write_order(source, frames):
    result, report = assert_idempotent(source)
    assert report["statistics"]["conditions_after"] <= report["statistics"]["conditions_before"]
    validate_ladder_candidate_structure(result)
    left = right = {"M0": 1, "M1": 0, "T0": 0, "C0": 0}
    lt, rt = {}, {}
    for x0, x1, d0 in frames:
        left, right = dict(left, X0=x0, X1=x1, D0=d0), dict(right, X0=x0, X1=x1, D0=d0)
        left, lt, lw = scan(source, left, lt)
        right, rt, rw = scan(result, right, rt)
        assert (left, lt, lw) == (right, rt, rw)


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


@pytest.mark.parametrize("condition", [contact("X0", "P"), contact("X0", "F"), contact("C200"),
    contact("M8013"), {"type": "COMPARE", "expression": "= D0Z0 K1"},
    {"type": "parallel_block", "branches": [[contact("X0", "P")], [contact("X1")]]}])
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
    edge_index = 0

    def word(token):
        if token.startswith("K"):
            return int(token[1:])
        if token.startswith("T") or token.startswith("C"):
            return timers.get(token, 0)
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
                if op in {"LDP", "LDF", "ANDP", "ANDF", "ORP", "ORF"}:
                    key = ("edge", edge_index)
                    edge_index += 1
                    current, previous = bool(values.get(args[0], 0)), timers.get(key, False)
                    condition = current and not previous if op.endswith("P") else previous and not current
                    timers[key] = current
                elif op in {"LD", "LDI", "AND", "ANI", "OR", "ORI"}:
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
                elif args[0].startswith("C"):
                    key = ("counter", args[0])
                    count = timers.get(args[0], 0)
                    if acc and not timers.get(key) and count < word(args[1]):
                        count += 1
                    timers[key], timers[args[0]] = bool(acc), count
                    value = int(count >= word(args[1]))
                else:
                    value = int(bool(acc))
                values[args[0]] = value
                writes.append((args[0], value))
            elif op in {"SET", "RST", "MOV"}:
                if acc:
                    address, value = (args[1], word(args[0])) if op == "MOV" else (args[0], int(op == "SET"))
                    values[address] = value
                    if op == "RST" and address.startswith(("T", "C")):
                        timers[address] = 0
                    writes.append((address, value))
            elif op in {"PLS", "PLF"}:
                key = ("edge", edge_index)
                edge_index += 1
                current, previous = bool(acc), timers.get(key, False)
                value = int(current and not previous if op == "PLS" else previous and not current)
                timers[key] = current
                values[args[0]] = value
                writes.append((args[0], value))
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
