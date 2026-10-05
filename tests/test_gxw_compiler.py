"""Stored compiler artifacts checked against archived native observations."""
import base64
import importlib
import json
from pathlib import Path
import struct
import zipfile

import pytest

from gxw.compiler_debug import compiler_st_source_points, parse_compiler_debug
from gxw.compiler_storage import parse_compiler_index, parse_compiler_link_map
from gxw.compiler_tables import parse_compiler_tables
from gxw.container import CompoundFile
from gxw.models import GXWFormatError
from gxw.token_pou import parse_token_fragment, parse_token_region

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "research/evidence/gxw-structured-compiler-20260920.zip"
COMPONENT_ARCHIVE = ROOT / "research/evidence/gxw-compiler-components-20260920.zip"
SYMBOL_ARCHIVE = ROOT / "research/evidence/gxw-compiler-symbols-20260920.zip"
REPLAY_ARCHIVE = ROOT / "research/evidence/gxw-compiler-replay-20260920.zip"
CALLSITE_INPUT_CASES = json.loads(
    (ROOT / "tests/fixtures/gxw_callsite_inputs.json").read_text(encoding="utf-8")
)["cases"]
GRAPH_INTERFACE_CASES = json.loads(
    (ROOT / "tests/fixtures/gxw_callable_ports.json").read_text(encoding="utf-8")
)["interfaces"]
ARRAY_REFERENCE_CASES = json.loads(
    (ROOT / "tests/fixtures/gxw_array_references_native.json").read_text(encoding="utf-8")
)["cases"]


def _native_reference_graph(case):
    """Restore the fixture's sparse graph, retaining native record identities."""
    from gxw.compiler_tables import CompilerTable, CompilerTableRecord, CompilerTables
    tables = []
    for row in case["tables"]:
        records = tuple(CompilerTableRecord(r["absolute_offset"], r["table_offset"],
                        base64.b64decode(r["raw_base64"])) for r in row["records"])
        # This buffer supplies the original table extent for range checks.
        # Unselected regions are omitted; it is not a reconstructed CGTable.
        raw = bytearray(struct.pack("<I", row["payload_size"]) + bytes(row["payload_size"]))
        for record in records:
            start = 4 + record.table_offset
            raw[start:start + len(record.raw)] = record.raw
        tables.append(CompilerTable(row["index"], row["absolute_offset"], bytes(raw), records))
    return CompilerTables(b"", tuple(tables))


@pytest.mark.parametrize("case", GRAPH_INTERFACE_CASES, ids=lambda row: row["case"] + "/" + row["symbol"])
def test_graph_formals_match_native_descriptor_observations(case, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    project = importlib.import_module("probe_gxw_library_sources").observed_graph_formals
    from gxw.lossless import sha256

    raw = base64.b64decode(case["declaration_base64"])
    assert sha256(raw) == case["declaration_sha256"]
    actual = project(raw, input_port_count=case["input_count"])
    assert actual["handling"] == "observed-graph-formals" and not actual["gaps"]
    assert [{k: row[k] for k in ("name", "class_code", "type_code")} for row in actual["formals"]] == case["expected"]
    # Matching the native interface does not establish successful compilation,
    # writeback destinations or equivalence of changed port flags.
    assert actual["execution_effects"] == "not inferred"
    assert base64.b64decode(actual["raw_base64"]) == raw


@pytest.mark.parametrize("control", ["truncated", "wrong-input-count", "missing-extensible-count"])
def test_graph_interface_gaps_retain_input_without_guessing(control, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    project = importlib.import_module("probe_gxw_library_sources").observed_graph_formals
    case = next(c for c in GRAPH_INTERFACE_CASES if c["symbol"] == "ADD_E-3")
    raw = base64.b64decode(case["declaration_base64"])
    count = case["input_count"]
    if control == "truncated":
        raw = raw[:-1]
    elif control == "wrong-input-count":
        count = 0
    else:
        count = None
    result = project(raw, input_port_count=count)
    assert result["handling"] == "opaque-preserved" and result["gaps"]
    assert result["formals"] == []
    assert base64.b64decode(result["raw_base64"]) == raw


@pytest.mark.parametrize("case", CALLSITE_INPUT_CASES, ids=lambda case: case["id"])
def test_repeated_fb_inputs_use_their_own_caller_code(case, monkeypatch):
    """Native FB body cross-checks freeze a destination independent of final CGTable state."""
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("probe_gxw_callsite_inputs").compare_callsite_inputs
    from gxw.lossless import sha256

    fragment = case["input_fragment"]
    assert sha256(base64.b64decode(fragment["raw_base64"])) == fragment["sha256"]
    for group in case["native_groups"]:
        assert sha256(base64.b64decode(group["raw_base64"])) == group["sha256"]
    result = compare(fragment["records"], case["ports"], case["native_caller"], case["final_allocations"])
    assert result["handling"] == "observed-callsite-input-lowering" and not result["gaps"]
    assert {b["port"]: b["operand"] for b in result["bindings"]} == case["expected_bindings"]
    assert {b["port"]: b["mode"] for b in result["bindings"]} == case["expected_modes"]
    assert case["observed_body_groups"]["exact"] == case["observed_body_groups"]["source_groups"] == 163
    # Final allocations are metadata only: later calls may replace their
    # address and representation, including compact allocations versus tag 7.
    without_final = compare(fragment["records"], case["ports"], case["native_caller"])
    assert {b["port"]: b["operand"] for b in without_final["bindings"]} == case["expected_bindings"]
    wrong = compare(fragment["records"], case["ports"], case["wrong_caller"], case["final_allocations"])
    assert wrong["handling"] == "opaque-preserved" and wrong["gaps"]
    assert wrong["source_records"] == fragment["records"]
    assert wrong["native_records"] == case["wrong_caller"]


@pytest.mark.parametrize("change", ["unknown_instruction", "wrong_port_type", "extra_native_group"])
def test_callsite_input_unknown_shapes_remain_opaque(change, monkeypatch):
    from copy import deepcopy
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("probe_gxw_callsite_inputs").compare_callsite_inputs
    case = deepcopy(CALLSITE_INPUT_CASES[0])
    source, ports, native = case["input_fragment"]["records"], case["ports"], case["native_caller"]
    if change == "unknown_instruction":
        source[1]["op"] = "UNOBSERVED"
    elif change == "wrong_port_type":
        next(p for p in ports if p["name"] == "iEn")["type_marker"] = "W"
    else:
        native += [{"kind": "instruction", "op": "LD", "args": ["X0"]},
                   {"kind": "instruction", "op": "OUT", "args": ["M999"]}]
    result = compare(source, ports, native)
    assert result["handling"] == "opaque-preserved" and result["gaps"]
    assert result["source_records"] == source and result["native_records"] == native


def test_native_link_empty_name_placeholder_is_preserved():
    with zipfile.ZipFile(REPLAY_ARCHIVE) as archive:
        raw = archive.read("two-programs-1/1-2-chained/MAIN.map")
    mapping = parse_compiler_link_map(raw)
    assert mapping.reconstruct() == raw
    assert [entry.name_bytes for entry in mapping.entries] == [b"1.1", b"2.2", b"", b"END"]
    placeholder = mapping.entries[2]
    assert len(placeholder.raw) == 44 and placeholder.fields[0] == 0xFFFFFFFD
    assert placeholder.token_length == placeholder.step_count == 0
    assert mapping.opaque_tail
    # Empty link names do not relax the separate Index framing contract.
    with pytest.raises(GXWFormatError):
        parse_compiler_index(struct.pack("<4I", 1, 1, 4, 0))
    malformed = bytearray(raw)
    struct.pack_into("<I", malformed, placeholder.offset + 40, 1)
    with pytest.raises(GXWFormatError):
        parse_compiler_link_map(malformed)


def test_native_source_prediction_matches_real_gxw_patch_compile():
    from gxw.lossless import patch_structured_symbol_equal_size, sha256
    with zipfile.ZipFile(REPLAY_ARCHIVE) as archive:
        prefix = "gui-mutated-compiler-project/"
        before = archive.read(prefix + "baseline-before-patch.gxw")
        expected = archive.read(prefix + "patched-before-native.gxw")
        result = patch_structured_symbol_equal_size(before, expected_sha256=sha256(before),
            logical_name="1.Program.pou", node_offset=219, old_symbol="X1", new_symbol="X3")
        assert result.data == expected
        baseline_code = archive.read("offline-replay-5/pcode-0-1.bin")
        assert baseline_code == archive.read("gui-native-compiler-files/MAIN.qpg")
        predicted = archive.read("source-mutations-1/input-x3/pcode-0-1.bin")
        assert predicted == archive.read(prefix + "native-compiled/MAIN.qpg")
        assert len(predicted) == 420
        assert [i for i, (a, b) in enumerate(zip(baseline_code, predicted)) if a != b] == [5]
        observed = json.loads(archive.read(prefix + "native-compiled/plan.json"))
        prediction = json.loads(archive.read("source-mutations-1/input-x3/plan.json"))
        def calls(plan):
            return [{k: value for k, value in unit.items() if k != "event_id"} for unit in plan["compile"]]
        assert len(calls(observed)) == 11 and calls(observed) == calls(prediction)
        # The final END compilation must receive the updated allocation table.
        for kind in ("bool", "int", "mixed"):
            chained = archive.read(f"named-variables-1/{kind}-chained/symbols-after-20.bin")
            reset = archive.read(f"named-variables-1/{kind}-reset/symbols-after-20.bin")
            a, b = parse_compiler_tables(chained), parse_compiler_tables(reset)
            assert all(any(a.component_at(r.table_offset).user_info) for r in a.tables[2].records)
            assert all(not any(b.component_at(r.table_offset).user_info) for r in b.tables[2].records)
            assert archive.read(f"named-variables-1/{kind}-chained/pcode-0-1.bin") == archive.read(f"named-variables-1/{kind}-reset/pcode-0-1.bin")


def test_compiler_fragments_need_no_end_but_complete_programs_still_do():
    fragment = bytes.fromhex("056a010705")  # native SRET fragment
    raw = b"prefix" + fragment + b"suffix"
    parsed = parse_token_fragment(raw, 6, len(fragment))
    assert parsed.body == fragment and parsed.reconstruct() == raw
    assert parsed.tokens[0].offset == 6
    with pytest.raises(GXWFormatError):
        parse_token_region(raw, 6, len(fragment))
    assert parse_token_fragment(b"", 0, 0).reconstruct() == b""
    with pytest.raises(GXWFormatError):
        parse_token_fragment(raw, 6, len(fragment) - 1)


def test_compiler_link_membership_keeps_renamed_unlinked_aliases(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    inspect = importlib.import_module("inspect_gxw_compiler").inspect_storage
    with zipfile.ZipFile(ARCHIVE) as z:
        scan = json.loads(z.read("artifact-scan/compiler-storage.json"))
        native = json.loads(z.read("public-scan/compiler-storage.json"))["fragments"]
        checked = 0
        for storage in scan["storages"]:
            raw = base64.b64decode(storage["raw_base64"])
            actual, _ = inspect(raw)
            assert actual["index_replay"] and actual["maps"] == storage["maps"]
            cfb = CompoundFile(raw)
            index = parse_compiler_index(cfb.read_stream("Index"))
            assert index.reconstruct() == index.raw
            for mapping in actual["maps"]:
                assert mapping["complete_fragment_partition"] and mapping["reconstructed_code_matches"]
                entry = next(e for e in index.entries if e.display_name("cp936") == mapping["name"])
                table = parse_compiler_link_map(cfb.read_stream(entry.stream_name))
                assert table.reconstruct() == table.raw and table.opaque_tail
                for row in mapping["rows"]:
                    if row["token_length"] and not row["unlinked"]:
                        observation = native[row["body_sha256"]]
                        assert observation["native_readings_agree"]
                        assert observation["native_machinecode"]["native_value"] == row["step_count"]
                        checked += 1
        assert checked == 109
        renamed = next(s for s in scan["storages"] if s["storage_sha256"] ==
                       "facbf55e9c60027a55090fdbaf00ce3ac33f1fb559f32ad99110958dac86d751")
        rows = renamed["maps"][0]["rows"]
        old = next(r for r in rows if r.get("name") == "1.1.TON_A")
        new = next(r for r in rows if r.get("name") == "1.1.AUTO_TON_A")
        assert old["unlinked"] and not new["unlinked"]
        assert old["body_sha256"] == new["body_sha256"]
        assert old["token_offset"] == new["token_offset"]


def test_compiler_tables_match_native_names_offsets_instances_and_replay(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("native_gxw_compiler").compare_table_dump
    with zipfile.ZipFile(ARCHIVE) as z:
        observations = json.loads(z.read("native-table-corpus/report.json"))
        components = instances = 0
        for case in observations:
            prefix = f"native-table-corpus/{case['id']}/"
            raw = z.read(prefix + "CGTable.dat")
            tables = parse_compiler_tables(raw)
            assert len(tables.tables) == 14 and tables.reconstruct() == raw
            assert z.read(prefix + "native-replay.bin") == raw
            actual = compare(raw, z.read(prefix + "native-table-dump.txt"))
            assert actual["pous_equal"] and actual["components_equal"] and actual["instance_refs_equal"]
            components += actual["components"]
            instances += actual["instances"]
        assert len(observations) == 12 and components == 1345 and instances == 38
        tables = parse_compiler_tables(z.read("CGTable.dat"))
        main = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                    if tables.pou_at(r.table_offset).name_bytes == b"1")
        references = {c.name_bytes: c.instance_pou_offset for c in tables.components(main)}
        a, b = (tables.pou_at(references[k]) for k in (b"TON_A", b"TON_B"))
        assert a.name_bytes == b.name_bytes == b"TON"
        assert a.record.table_offset != b.record.table_offset
        assert a.component_start != b.component_start


def test_compiler_table_unknown_records_survive_and_bad_references_fail():
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read("CGTable.dat")
    assert raw[:4] == b"\0" * 4
    opaque = struct.pack("<II", 7, 3) + b"xyz" + raw[4:]
    parsed = parse_compiler_tables(opaque)
    assert parsed.tables[0].records[0].payload == b"xyz"
    assert parsed.reconstruct() == opaque
    with pytest.raises(GXWFormatError, match="record boundary"):
        parsed.pou_at(1)
    for malformed in (raw[:-1], raw + b"tail", struct.pack("<I", len(raw)) + raw[4:]):
        with pytest.raises(GXWFormatError):
            parse_compiler_tables(malformed)
    invalid_range = bytearray(raw)
    # Use the unshifted source table's first POU and its component start field.
    original = parse_compiler_tables(raw).pou_at(0)
    field = original.record.offset + 4 + 1 + len(original.name_bytes) + 4
    struct.pack_into("<I", invalid_range, field, 1)
    bad = parse_compiler_tables(invalid_range)
    with pytest.raises(GXWFormatError, match="record-bounded"):
        bad.components(bad.pou_at(0))


def test_compiler_debug_keeps_offset_tables_unlinked_elements_and_unknown_tail():
    with zipfile.ZipFile(ARCHIVE) as z:
        artifacts = json.loads(z.read("artifact-scan/compiler-storage.json"))["compiler_artifacts"]
        cases = [base64.b64decode(a["raw_base64"]) for a in artifacts.values() if a["kind"] == "DebugInformation2.dat"]
        assert len(cases) == 15
        for raw in cases:
            parsed = parse_compiler_debug(raw)
            assert parsed.reconstruct() == raw
            assert all(t.sentinel[0] == -1 for t in parsed.offset_tables)
        raw = z.read("DebugInformation2.dat")
        parsed = parse_compiler_debug(raw)
        assert len(parsed.elements) == len(parsed.offset_tables) == 4
        assert parsed.tail_offset == 746 and len(parsed.opaque_tail) == 407
        counter = next(e for e in parsed.elements if e.names[3] == b"1.counter_a")
        assert counter.linked_step_start == counter.linked_step_end == -1
        assert parsed.offset_tables[counter.offset_table_index].rows
        assert parse_compiler_debug(raw + b"unknown-extension").reconstruct() == raw + b"unknown-extension"
        bad = bytearray(raw)
        struct.pack_into("<I", bad, parsed.elements[0].offset + len(parsed.elements[0].raw) - 4, 99)
        with pytest.raises(GXWFormatError, match="missing offset table"):
            parse_compiler_debug(bad)
        with pytest.raises(GXWFormatError):
            parse_compiler_debug(raw[:parsed.tail_offset - 1])


def _native_st_debug():
    fixture = json.loads((ROOT / "tests/fixtures/gxw_st_source_points_native.json").read_text(encoding="utf-8"))
    raw = base64.b64decode(fixture["debug_base64"])
    return fixture, raw, parse_compiler_debug(raw)


def test_st_source_points_match_native_reads_prefix_counts_and_saved_contexts():
    fixture, raw, debug = _native_st_debug()
    points = compiler_st_source_points(debug)
    assert len(points) == 45
    actual = [dict(element_index=debug.elements.index(p.element), row_index=p.row_index,
                   source_line=p.source_line, compiled_step_start=p.compiled_step_start,
                   context_names=[name.decode("ascii") for name in p.context_names],
                   resource=p.element.resource_bytes.decode("ascii")) for p in points]
    assert actual == fixture["expected"]
    assert debug.reconstruct() == raw
    assert all(p.row == p.table.rows[p.row_index] for p in points)


def test_st_same_type_instances_and_overlapping_caller_points_remain_separate():
    _, _, debug = _native_st_debug()
    points = compiler_st_source_points(debug)
    first = [p for p in points if p.context_names[4] == b"Keypad_1"]
    second = [p for p in points if p.context_names[4] == b"Keypad_2"]
    assert len(first) == len(second) == 18
    assert {p.definition_name_bytes for p in first + second} == {b"Keypad"}
    assert [p.source_line for p in first] == [p.source_line for p in second]
    assert first[0].compiled_step_start == 31
    assert second[0].compiled_step_start == 116
    # Caller points include an expansion while its own points remain distinct.
    caller = next(p for p in points if not p.context_names[4] and p.source_line == 8)
    assert caller.compiled_step_start == first[0].compiled_step_start
    # A stored endpoint reaches the next expansion; it is not statement ownership.
    assert first[-1].source_line == 31
    assert first[-1].compiled_step_end == second[0].compiled_step_start


@pytest.mark.parametrize("change", ["table-language", "negative-line", "negative-step", "reversed-steps", "network-row", "row-kind"])
def test_unsupported_st_coordinate_views_preserve_the_debug_bytes(change):
    _, raw, debug = _native_st_debug()
    damaged = bytearray(raw)
    table = debug.offset_tables[0]
    row_offset = table.offset + 8
    if change == "table-language":
        offset, value = table.offset, 1
    else:
        field, value = {"negative-line": (0, -2), "negative-step": (1, -1),
                        "reversed-steps": (2, -1), "network-row": (3, 1),
                        "row-kind": (4, 14)}[change]
        offset = row_offset + field * 4
    struct.pack_into("<i", damaged, offset, value)
    reparsed = parse_compiler_debug(bytes(damaged))
    assert reparsed.reconstruct() == bytes(damaged)
    with pytest.raises(GXWFormatError):
        compiler_st_source_points(reparsed)
    assert reparsed.reconstruct() == bytes(damaged)


def test_st_coordinates_keep_uninterpreted_row_fields():
    _, raw, debug = _native_st_debug()
    changed = bytearray(raw)
    struct.pack_into("<i", changed, debug.offset_tables[0].offset + 8 + 20, 12345)
    reparsed = parse_compiler_debug(bytes(changed))
    point = compiler_st_source_points(reparsed)[0]
    assert point.row[5] == 12345
    assert point.source_line == point.compiled_step_start == 0
    assert reparsed.reconstruct() == bytes(changed)


def test_compiler_component_fields_match_native_reads_and_global_indirection(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("native_gxw_compiler").compare_component_reads
    with zipfile.ZipFile(COMPONENT_ARCHIVE) as z:
        count = 0
        for i in range(12):
            prefix = f"native-component-corpus/{i}/"
            raw = z.read(prefix + "CGTable.dat")
            rows = [json.loads(s) for s in z.read(prefix + "native-components.jsonl").splitlines()]
            result = compare(raw, rows)
            assert result["component_reads_complete"] and not result["component_field_mismatches"]
            assert z.read(prefix + "native-replay.bin") == raw
            count += result["component_count"]
        assert count == 1345
        raw = z.read("external-type-control/CGTable.dat")
        rows = [json.loads(s) for s in z.read("external-type-control/native-components.jsonl").splitlines()]
        tables = parse_compiler_tables(raw)
        component = tables.component_at(0)
        assert component.global_offset == 0 and component.type_code is None
        assert tables.component_type(component) == 2
        assert compare(raw, rows)["component_field_mismatches"] == []
        assert not compare(raw, rows[:-1])["component_reads_complete"]
        user = bytearray(base64.b64decode(rows[0]["user_info_base64"]))
        user[0] ^= 1
        rows[0]["user_info_base64"] = base64.b64encode(user).decode()
        assert compare(raw, rows)["component_field_mismatches"] == [{"offset": 0, "field": "user_info"}]


def test_global_fb_instances_match_native_references_and_keep_distinct_paths(monkeypatch):
    from gxw.compiler_symbols import compiler_symbol_paths
    from gxw.lossless import sha256
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    oracle = importlib.import_module("native_gxw_compiler")
    fixture = json.loads((ROOT / "tests/fixtures/gxw_global_instances.json").read_text(encoding="utf-8"))
    artifacts = {name: base64.b64decode(value["base64"]) for name, value in fixture["artifacts"].items()}
    for name, raw in artifacts.items():
        assert len(raw) == fixture["artifacts"][name]["bytes"]
        assert sha256(raw) == fixture["artifacts"][name]["sha256"]
    raw = artifacts["CGTable.dat"]
    assert raw == artifacts["native-replay.bin"]
    tables = parse_compiler_tables(raw)
    assert tables.reconstruct() == raw
    rows = [json.loads(line) for line in artifacts["native-components.jsonl"].splitlines()]
    assert not oracle.compare_component_reads(raw, rows)["component_field_mismatches"]
    comparison = oracle.compare_table_dump(raw, artifacts["native-table-dump.txt"])
    assert comparison["pous_equal"] and comparison["components_equal"] and comparison["instance_refs_equal"]
    assert (comparison["components"], comparison["instances"]) == (76, 2)
    globals_pou = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                       if tables.pou_at(r.table_offset).name_bytes == b"@GLOBALS")
    references = {c.name_bytes: tables.component_instance_pou_offset(c) for c in tables.components(globals_pou)}
    assert references == {b"GlobalInstance": 0, b"GlobalOther": 65}
    native = {row["requested_offset"]: base64.b64decode(row["record_base64"]) for row in rows}
    for component in tables.components(globals_pou):
        assert component.type_code is None and component.global_offset is not None
        assert tables.component_instance_pou_offset(component) == struct.unpack_from("<I", native[component.record.table_offset], 0x111)[0]
    paths = compiler_symbol_paths(tables, globals_pou.record.table_offset)
    assert len(paths) == 76
    members = {p.names[1]: p for p in paths if p.names[-1] == b"rPrm"}
    assert set(members) == {b"GlobalInstance", b"GlobalOther"}
    assert members[b"GlobalInstance"].symbol.pou_offset == 0
    assert members[b"GlobalOther"].symbol.pou_offset == 65
    assert members[b"GlobalInstance"].symbol.assignment.iec_address != members[b"GlobalOther"].symbol.assignment.iec_address


@pytest.mark.parametrize("reference,error", [(1, "record boundary"), (130, "cycle")])
def test_global_fb_reference_corruption_remains_visible(reference, error):
    from gxw.compiler_symbols import compiler_symbol_paths
    fixture = json.loads((ROOT / "tests/fixtures/gxw_global_instances.json").read_text(encoding="utf-8"))
    raw = bytearray(base64.b64decode(fixture["artifacts"]["CGTable.dat"]["base64"]))
    tables = parse_compiler_tables(raw)
    global_record = tables.tables[3].records[0]
    name, _ = global_record.named_fields()
    struct.pack_into("<I", raw, global_record.offset + 5 + len(name) + 8, reference)
    changed = parse_compiler_tables(raw)
    assert changed.reconstruct() == raw
    with pytest.raises(GXWFormatError, match=error):
        compiler_symbol_paths(changed, 130)


def test_compiler_assignment_native_text_does_not_turn_zero_into_x0():
    from gxw.compiler_assignment import parse_compiler_assignment
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        rows = [json.loads(s) for s in z.read("native-assignment-corpus/outputs.jsonl").splitlines()]
    decoded = unassigned = 0
    for row in rows:
        raw = base64.b64decode(row["input_base64"])
        actual = parse_compiler_assignment(raw)
        assert actual.raw == base64.b64decode(row["after_base64"]) == raw
        native = base64.b64decode(row["output_base64"]).decode("ascii")
        if actual.status == "unassigned":
            assert not any(raw) and native == "%IX0"
            assert actual.fx_operand is None and actual.iec_address is None
            unassigned += 1
        else:
            assert actual.status == "decoded" and actual.iec_address == native
            decoded += 1
    assert (decoded, unassigned) == (1205, 140)
    unknown = bytearray.fromhex("2405e603000002000000") + bytearray(16)
    unknown[-1] = 1
    assert parse_compiler_assignment(unknown).status == "opaque"
    with pytest.raises(GXWFormatError):
        parse_compiler_assignment(bytes(25))


def test_inline_assignments_match_native_operands_without_inventing_allocations():
    from gxw.compiler_assignment import parse_compiler_assignment
    fixture = json.loads((ROOT / "tests/fixtures/gxw_inline_assignments_native.json").read_text(encoding="utf-8"))
    original_constants = original_devices = 0
    for row in fixture["cases"]:
        raw = base64.b64decode(row["input_base64"])
        actual = parse_compiler_assignment(raw)
        assert actual.raw == raw
        native_operand = base64.b64decode(row["operand_base64"]).decode("ascii")
        native_address = base64.b64decode(row["output_base64"]).decode("ascii")
        after = base64.b64decode(row["after_base64"])
        # Native uppercases its private copy; parsing preserves original bytes.
        assert after[:21] == raw[:21] and after[22:] == raw[22:]
        assert chr(after[21]) == chr(raw[21]).upper()
        if actual.status == "opaque":
            assert row["origin"] == "prospective-number-controls"
            assert actual.operand is None and actual.iec_address is None
            continue
        assert actual.status == "decoded" and actual.operand == native_operand
        assert actual.iec_address == (native_address or None)
        assert actual.reserved_count is None
        if actual.constant_value is not None:
            assert actual.role == "constant"
            assert actual.device_family is None and actual.number is None
            assert actual.fx_operand is None and not native_address
            original_constants += row["origin"] == "original-stored-cache"
        else:
            assert actual.role == "device_reference"
            if actual.device_family == "W":
                assert actual.fx_operand is None
                assert actual.operand == f"W{actual.number:X}"
            else:
                assert actual.fx_operand == native_operand
            original_devices += row["origin"] == "original-stored-cache"
    assert (original_constants, original_devices) == (34, 31)


@pytest.mark.parametrize("offset", [1, 8, 13, 20, 22, 25])
def test_inline_assignment_unknown_modifiers_stay_opaque(offset):
    from gxw.compiler_assignment import parse_compiler_assignment
    raw = bytearray.fromhex("070000000000000000f91c000000000000000000004d00000000")
    raw[offset] = 1
    actual = parse_compiler_assignment(raw)
    assert actual.raw == raw and actual.status == "opaque"
    assert actual.operand is None and actual.iec_address is None
    assert actual.constant_value is None


@pytest.mark.parametrize("case", ARRAY_REFERENCE_CASES, ids=lambda row: row["case"])
def test_array_member_addresses_match_saved_native_compiler_references(case):
    from gxw.compiler_symbols import compiler_array_reference
    tables = _native_reference_graph(case)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    for row in case["references"]:
        actual = compiler_array_reference(tables, row["component_offset"], tuple(row["indices"]),
            member_offset=row["member_offset"], member_indices=tuple(row["member_indices"]))
        assert actual.operand == row["expected_operand"]
        assert actual.iec_address == row["expected_iec_address"]
        assert actual.type_pou_offset == row["expected_type_pou_offset"]
        assert actual.primitive_type == row["expected_primitive_type"]
        assert actual.primitive_width == row["expected_primitive_width"]
        assert actual.family_stride == row["expected_family_stride"]
        assert actual.base_assignment.raw == (actual.member or actual.component).user_info
        assert actual.descriptor.record.raw == tables.tables[13].record_at(actual.descriptor.record.table_offset).raw
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


@pytest.mark.parametrize("invalid", ["below-lower", "above-upper", "wrong-rank", "non-integer", "scalar-member-index"])
def test_array_member_reference_rejects_invalid_coordinates_without_rewriting_cache(invalid):
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/Member-endpoints"))
    tables = _native_reference_graph(case)
    row = case["references"][0]
    descriptor = tables.array_at(tables.component_array_offset(tables.component_at(row["component_offset"])))
    indices = tuple(row["indices"])
    member_indices = ()
    if invalid == "below-lower":
        indices = (descriptor.dimensions[0].lower - 1,)
    elif invalid == "above-upper":
        indices = (descriptor.dimensions[0].upper + 1,)
    elif invalid == "wrong-rank":
        indices += (0,)
    elif invalid == "non-integer":
        indices = (True,)
    else:
        member_indices = (0,)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    with pytest.raises(GXWFormatError):
        compiler_array_reference(tables, row["component_offset"], indices,
                                 member_offset=row["member_offset"], member_indices=member_indices)
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


def test_array_member_reference_preserves_same_named_type_instance_identity():
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/Second-alarm"))
    tables = _native_reference_graph(case)
    first, second = case["references"][:2]
    assert first["expected_type_pou_offset"] != second["expected_type_pou_offset"]
    assert tables.pou_at(first["expected_type_pou_offset"]).name_bytes == tables.pou_at(second["expected_type_pou_offset"]).name_bytes
    with pytest.raises(GXWFormatError, match="does not belong"):
        compiler_array_reference(tables, first["component_offset"], tuple(first["indices"]),
                                 member_offset=second["member_offset"])


@pytest.mark.parametrize("parameter", [14, 32768])
def test_string_array_reference_refuses_inconsistent_or_unmeasured_extent(parameter):
    from gxw.compiler_symbols import compiler_array_reference
    case = next(c for c in ARRAY_REFERENCE_CASES if c["case"].endswith("/outer12-inner5-conditional"))
    case = json.loads(json.dumps(case))
    row = next(r for r in case["references"] if r["text"] == "probeArray[0]")
    tables = _native_reference_graph(case)
    offset = tables.component_array_offset(tables.component_at(row["component_offset"]))
    descriptor = next(r for r in case["tables"][13]["records"] if r["table_offset"] == offset)
    raw = bytearray(base64.b64decode(descriptor["raw_base64"]))
    struct.pack_into("<I", raw, 8, parameter)
    descriptor["raw_base64"] = base64.b64encode(raw).decode()
    tables = _native_reference_graph(case)
    before = tuple(record.raw for table in tables.tables for record in table.records)
    with pytest.raises(GXWFormatError):
        compiler_array_reference(tables, row["component_offset"], tuple(row["indices"]))
    assert before == tuple(record.raw for table in tables.tables for record in table.records)


def test_compiler_address_and_array_values_match_native_objects(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    oracle = importlib.import_module("native_gxw_compiler")
    addresses = arrays = references = 0
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        for i in range(12):
            prefix = f"native-array-corpus/{i}/"
            raw = z.read(prefix + "CGTable.dat")
            assert raw == z.read(prefix + "native-replay.bin")
            tables = parse_compiler_tables(raw)
            for kind, compare in (("addresses", oracle.compare_address_reads), ("arrays", oracle.compare_array_reads)):
                rows = [json.loads(s) for s in z.read(prefix + f"native-{kind}.jsonl").splitlines()]
                result = compare(raw, rows)
                assert result["address_reads_complete" if kind == "addresses" else "array_reads_complete"]
                assert not result["address_field_mismatches" if kind == "addresses" else "array_field_mismatches"]
                if kind == "addresses":
                    addresses += len(rows)
                else:
                    arrays += len(rows)
            for record in tables.tables[2].records:
                component = tables.component_at(record.table_offset)
                offset = tables.component_array_offset(component)
                if offset is not None:
                    array = tables.array_at(offset)
                    assert array.element_type == 2 and array.total_count == 4
                    assert array.dimensions[0].lower == 0 and array.dimensions[0].upper == 3
                    assert array.count_matches_dimensions
                    references += 1
            for record in tables.tables[4].records:
                address = tables.address_at(record.table_offset)
                if not address.name_bytes:
                    assert address.iec_address_bytes is None
        assert (addresses, arrays, references) == (23, 16, 16)


def test_compiler_array_native_acceptance_preserves_inconsistent_counts(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    compare = importlib.import_module("native_gxw_compiler").compare_array_reads
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        prefix = "native-array-controls/"
        raw = z.read(prefix + "CGTable.dat")
        rows = [json.loads(s) for s in z.read(prefix + "native-arrays.jsonl").splitlines()]
        controls = json.loads(z.read(prefix + "controls.json"))["controls"]
        assert raw == z.read(prefix + "native-replay.bin")
        assert compare(raw, rows) == {"array_reads_complete": True, "array_count": 15, "array_field_mismatches": []}
    tables = parse_compiler_tables(raw)
    cases = {c["name"]: tables.array_at(c["offset"]) for c in controls}
    assert len(cases) == 13
    assert all(a.type_padding == b"\xde\xad\xbe" for a in cases.values())
    assert [(d.lower, d.upper) for d in cases["mixed-two-dimensions"].dimensions] == [(-5, -2), (10, 12)]
    assert cases["numeric-kind-3"].dimensions[0].lower == -70000
    assert cases["numeric-kind-6"].total_count == 4000000000
    assert cases["inconsistent-total"].total_count == 99
    assert cases["inconsistent-total"].dimensions[0].extent == 4
    assert not cases["inconsistent-total"].count_matches_dimensions
    assert not cases["negative-extent"].count_matches_dimensions
    assert cases["negative-extent"].dimensions[0].upper is None
    assert not cases["no-dimensions"].count_matches_dimensions
    for name in ("element-parameter-15", "element-parameter-52", "element-parameter-53"):
        assert cases[name].element_parameter == 0x12345678
    mutated = bytearray(raw)
    first = tables.tables[13].records[0]
    mutated[first.offset + 4 + 5] = 15
    framed = parse_compiler_tables(mutated)
    assert framed.reconstruct() == mutated
    with pytest.raises(GXWFormatError, match="numeric encoding"):
        framed.array_at(first.table_offset)


def test_compiler_symbol_paths_and_timer_aliases_survive_instruction_links():
    from gxw.compiler_symbols import compiler_symbols, compiler_symbol_paths, compiler_operand_occurrences
    from gxw.token_listing import decode_token_program
    with zipfile.ZipFile(SYMBOL_ARCHIVE) as z:
        scan = json.loads(z.read("symbol-array-scan/compiler-storage.json"))
    match = next(x for x in scan["symbol_cross_references"] if x["storage_sha256"] ==
                 "73912bb8b92bf6fea01c3ee00d3cde8bac7d436a06384a91cd66d5951b45ce81")
    raw = base64.b64decode(scan["compiler_artifacts"][match["table_sha256"]]["raw_base64"])
    tables = parse_compiler_tables(raw)
    root = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
                if tables.pou_at(r.table_offset).name_bytes == b"1")
    paths = compiler_symbol_paths(tables, root.record.table_offset)
    path_names = {p.symbol.component.record.table_offset: b".".join(p.names) for p in paths}
    body = base64.b64decode(scan["fragments"][match["code_sha256"]]["body_base64"])
    listing = decode_token_program(parse_token_fragment(body, 0, len(body)))
    occurrences = compiler_operand_occurrences(compiler_symbols(tables), listing)
    assert len(occurrences) == 43 and sum(bool(x.component_offsets) for x in occurrences) == 28
    coil = next(x for x in occurrences if x.step == 78 and x.text == "T199")
    assert {path_names[offset] for offset in coil.component_offsets} == {
        b"1.TON_A.T_FB.Coil", b"1.TON_A.T_FB.ValueIn", b"1.TON_A.T_FB.ValueOut", b"1.TON_A.T_FB.Status"}
    with pytest.raises(GXWFormatError, match="expansion exceeds limit"):
        compiler_symbol_paths(tables, root.record.table_offset, limit=1)
    # A corrupt self-reference must fail instead of expanding indefinitely.
    child = next(c for c in tables.components(root) if c.instance_pou_offset is not None)
    field = child.record.offset + 4 + 1 + len(child.name_bytes) + 12
    cyclic = bytearray(raw)
    struct.pack_into("<I", cyclic, field, root.record.table_offset)
    with pytest.raises(GXWFormatError, match="cycle"):
        compiler_symbol_paths(parse_compiler_tables(cyclic), root.record.table_offset)


def test_source_declarations_expose_cached_fb_type_until_native_recompile():
    from dataclasses import replace
    from gxw.compiler_symbols import compiler_declaration_bindings
    from gxw.declarations import parse_declarations
    from gxw.lossless import inspect_project, sha256

    def bind(project):
        image = inspect_project(project)
        source = next(s for s in image.streams if s.logical_name == "1.Labels.lh")
        cache = next(s for s in image.streams if s.logical_name == "CGTable.dat")
        doc = parse_declarations(source.raw, logical_name=source.logical_name)
        tables = parse_compiler_tables(cache.raw)
        return tables, doc, compiler_declaration_bindings(tables, doc)

    # Existing native GUI compile evidence: TOF source declaration with the
    # old TON compiler cache; successful native compile produces a TOF cache.
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-20260910.zip") as z:
        before, after = z.read("t2.gxw"), z.read("t2c.gxw")
    assert sha256(before) == "ff7ed97c1275913beca2764e0429d33cb25013283bf34cd81da34ce9b9cd8e24"
    assert sha256(after) == "2b3c0b6bb2565be28aa2cf1826ff251dc1ed0105d227dc84a5606952b106afc9"
    tables, doc, old = bind(before)
    _, _, new = bind(after)
    a = next(b for b in old if b.name == "TON_A")
    b = next(b for b in new if b.name == "TON_A")
    assert a.declared_type == b.declared_type == "TOF"
    assert a.status == "instance-type-differs" and a.candidates[0].instance_type_name == b"TON"
    assert b.status == "instance-type-agrees" and b.candidates[0].instance_type_name == b"TOF"
    assert next(b for b in new if b.name == "elapsed_a").status == "name-match"
    # Multiple compiled instances of one source FB must remain alternatives.
    ambiguous = replace(doc, owner_name="TON", rows=(replace(doc.rows[0], name="IN", type_reference=""),))
    result = compiler_declaration_bindings(tables, ambiguous)[0]
    assert result.status == "ambiguous-compiled-owner"
    assert len(result.owner_offsets) == len(result.candidates) == 2


def test_source_added_declarations_bind_only_after_archived_native_compile():
    from gxw.compiler_symbols import compiler_declaration_bindings
    from gxw.declarations import parse_declarations
    from gxw.lossless import inspect_project
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-generation-20260910.zip") as z:
        cases = [z.read(name) for name in ("auto_decl.gxw", "auto_decl_compile.gxw")]
    states = []
    for raw in cases:
        image = inspect_project(raw)
        source = next(s for s in image.streams if s.logical_name == "1.Labels.lh")
        cache = next(s for s in image.streams if s.logical_name == "CGTable.dat")
        rows = compiler_declaration_bindings(parse_compiler_tables(cache.raw),
            parse_declarations(source.raw, logical_name=source.logical_name))
        generated = [b for b in rows if b.name.startswith("generated_")]
        assert len(generated) == 1000
        states.append({b.status for b in generated})
    assert states == [{"no-compiled-component"}, {"name-match"}]


@pytest.mark.parametrize('name', [
    *(cpu+suffix for cpu in ('FX3U-FX3UC','Q03UDV','L02') for suffix in ('-current','-duplicate-output')),
    'Q03UDV-current-compiler-stale-workspace', 'FX3G-duplicate-coil-legacy',
    'FX3G-duplicate-coil-fresh', 'SFC-empty-task-cache',
])
def test_current_code_checks_preserve_stale_reads_incomplete_diagnostics_and_empty_tasks(monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import analyze_check
    with zipfile.ZipFile(ROOT/'research/evidence/gxw-fbd-application-v2-20261002.zip') as archive:
        case = next(row for row in json.loads(archive.read('check-witnesses.json')) if row['case']==name)
    generated = {tuple(row['id']): tuple(base64.b64decode(b) for b in row['channels_base64'])
                 for row in case['generated']}
    result = analyze_check(case['native_events'],case['observations'],generated,selection=case['selection'])
    assert result['compilation']['completed']
    assert result['checker_resources']['correspondence'] == case['expected']['correspondence']
    assert result['public_check']['status'] == case['expected']['public_check']
    assert result['diagnostic_projection']['status'] == case['expected']['projection']
    assert result['current_source_check'] == 'not_established'
    if name.endswith('-duplicate-output'):
        assert result['targets']['backend_completed'] == result['targets']['planned']
        assert any(d['kind']==2 for d in result['diagnostic_projection']['backend_diagnostics'])
        assert not result['diagnostic_projection']['public_diagnostics']
        assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
        assert not result['public_check']['completed']
    if 'stale' in name or name.endswith('-legacy'):
        assert result['public_check']['status']=='passed'
        assert any(r['status']=='stale' for r in result['checker_resources']['reads'])
    if name=='SFC-empty-task-cache':
        assert result['compilation']['returned_resources']==1
        assert base64.b64decode(case['selection']['task_base64'])[-4:]==b'\0'*4
        assert case['selection']['empty_task'] and not case['selection']['selected_sources']


@pytest.mark.parametrize('owned,traced,completed,errors,expected', [
    ('current', 'not_observed', True, [], True),
    ('stale', 'current', True, [], False),
    ('unresolved', 'current', True, [], False),
    ('partial_targets', 'not_observed', True, [], False),
    ('current', 'stale', True, [], False),
    ('current', 'not_observed', False, [], False),
    ('current', 'not_observed', True, ['failed independent read'], False),
])
def test_native_check_code_guard_keeps_stale_conflicting_and_incomplete_evidence(
        monkeypatch, owned, traced, completed, errors, expected):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import has_current_native_check_code
    evidence = {'owned_check_inputs': {'correspondence': owned},
        'checker_resources': {'correspondence': traced, 'observation_errors': errors},
        'raw_backend_check': {'completed': completed}}
    assert has_current_native_check_code(evidence) is expected


def native_check_input_snapshots(body, padding=b''):
    from copy import deepcopy
    # Captured ECCompiler 15.50 task header: outer=64, inner=4, mode=1.
    # Synthetic opaque padding varies framing independently of the primary
    # code. These tests concern byte binding, not native code acceptance.
    header = bytes.fromhex('40002201' + '20' * 32 +
        '0402d100030301080403000000000004071000ffffffffffffffffff0400ffff')
    assert len(header) == 68
    first = bytearray(header[:64])
    first.extend(padding)
    first[:2] = len(first).to_bytes(2, 'little')
    header = bytes(first) + header[64:]
    full = header + body
    submitted = {'operation': 'OwnedCheckInput', 'target': [1] + [0] * 11,
        'stage': 'submitted', 'mask': 0x7fffffff, 'manager': 'owned-task',
        'compiler_member_offset': 12, 'manager_records_offset': 32,
        'provenance': 'original ProcessManager.ProgramCheck owned code copy; read-only; no interception',
        'rows': [{'resource': 'MAIN', 'bytes': len(full), 'file': 'submitted.bin', 'tag': -1,
            'framing': {'reader_module': 'ECCompiler_IEC.dll', 'reader_version': '15.50',
                'reader_rva': 0x39f30, 'code': 0, 'mode': 1, 'body_offset': len(header),
                'body_bytes': len(body), 'first_length': len(first), 'second_length': 4,
                'provenance': 'original checker envelope helper; optional tables omitted; read-only'}}]}
    completed = deepcopy(submitted)
    completed['stage'] = 'completed'
    completed['rows'][0]['file'] = 'completed.bin'
    return submitted, completed, {'generated': {'MAIN': body},
        'snapshots': {'submitted.bin': full, 'completed.bin': full},
        'native_version': '1.635.0.1', 'checker_version': '15.50'}


def native_validation_observation():
    """Synthetic collector transport over a frozen source, not native acceptance."""
    from copy import deepcopy
    from gxw.object_model import default_baseline
    from gxw.container_writer import validate_cfb_streams
    raw = default_baseline()
    _, _, source = native_source_chain_inputs()
    selected = deepcopy(source['selections'][0])
    selected['stage'] = 'before-build'
    selected['name'] = selected['source_object']['name'] = '1'
    selected['source_object']['owner']['read_name']['name'] = '1'
    body = source['snapshots']['LEAF-body.bin']
    submitted, completed, code = native_check_input_snapshots(b'current compiled code')
    snapshots = {**code['snapshots'], 'imported-hdb.bin': validate_cfb_streams(raw)['_hdb'],
                 'generated.bin': b'current compiled code', 'second.bin': b'', 'third.bin': b'',
                 'published.bin': b'current compiled code'}
    target = submitted['target']
    def facts(identity, kind, name):
        return {'id': identity, 'read_type': {'hresult': 0, 'code': 0, 'data_type': kind},
                'read_name': {'hresult': 0, 'code': 0, 'name': name}}
    resource = facts(target, 8, 'MAIN')
    events = [selected, {'operation': 'NativeTaskSelection', 'resource': resource,
        'tasks': [{'task': facts([30] + [0] * 11, 10, 'TASK'),
                   'programs': [facts([31] + [0] * 11, 12, '1')]}],
        'provenance': 'original Workspace collection, child, type, name and parent reads before compilation'}]
    for stage in ('before-build', 'after-build', 'after-check'):
        filename = stage + '.bin'; snapshots[filename] = body
        if stage != 'before-build':
            events.append({**deepcopy(selected), 'stage': stage})
        events.append({**source['reads'][0], 'file': filename, 'stage': stage})
    events.extend({'operation': 'OwnedBackendModule', 'name': name, 'version': '1.635.0.1'}
        for name in ('DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll', 'DZDataABS_SICConverter_IEC.dll'))
    diagnostic = {'kind': 2, 'code': 0x050c9300, 'step': 28, 'library': {'text': ''},
                  'name': {'text': 'MAIN'}, 'arguments': [{'text': 'Y0'}]}
    events.extend([
        {'operation': 'OwnedBackendModule', 'name': 'ECCompiler_IEC.dll', 'version': '15.50'},
        {'operation': 'Compiler.Build', 'hresult': 0, 'code': 0},
        {'operation': 'Progress', 'poll': 1, 'hresult': 0, 'code': 0, 'percent': 100, 'count': 0},
        {'operation': 'CompileRawReports', 'poll': 1, 'percent': 100, 'reports': []},
        {'operation': 'Resource', 'name': 'MAIN', 'index': 0, 'channels': [
            {'channel': i, 'bytes': len(snapshots[name]), 'file': name}
            for i, name in enumerate(('generated.bin', 'second.bin', 'third.bin'))]},
        {'operation': 'Workspace.UpdatePCodeBeforeProgramCheck', 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckTargetOrder', 'targets': [target]},
        {'operation': 'NativeObject', 'id': target, 'name': 'MAIN', 'name_hresult': 0, 'name_code': 0},
        {'operation': 'PublishedResourceCodeRead', 'target': target, 'name': 'MAIN',
         'hresult': 0, 'code': 0, 'sizes': [len(snapshots['published.bin']), 0, 0]},
        {'operation': 'PublishedResourceCodeCorrespondence', 'target': target, 'resource': resource, 'status': 'current',
         'channels': [{'channel': i, 'published_size': len(snapshots[name]), 'published_snapshot': name}
                      for i, name in enumerate(('published.bin', 'second.bin', 'third.bin'))]},
        {'operation': 'ProgramCheckTarget', 'id': target},
        {'operation': 'Compiler.ProgramCheck', 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckReportContext', 'target': target, 'path': 'owned-native-backend'}, submitted,
        {'operation': 'ProgramCheckRawProgress', 'poll': 1, 'percent': 100, 'count': 2, 'hresult': 0, 'code': 0},
        {'operation': 'ProgramCheckRawReports', 'poll': 1, 'percent': 100, 'reports': [diagnostic,
            {'kind': 1, 'code': 0x23, 'name': {'text': 'MAIN'}, 'arguments': [{'text': '1'}, {'text': '0'}]}]},
        completed,
        {'operation': 'NativeDiagnosticLocation', 'target_resource': 'MAIN',
         'original': {'poll': 1, 'report_index': 0, 'kind': 2, 'code': 0x050c9300, 'resource': 'MAIN', 'step': 28},
         'source_location': {'resource': 'MAIN', 'code_step': 28, 'hresult': 0, 'code': 0,
             'location': {'library': '', 'pou': '1', 'program_kind': 208, 'network': 1, 'start_step': 1},
             'source_object': selected['source_object']}},
        {'operation': 'ProgramCheckTargetOutcome', 'target': target, 'backend_check': 'completed-rejected'},
        {'operation': 'ProgramCheckPollingFinished', 'targets': 1}])
    # Retain the collector's actual ordering around compilation and checking.
    for stage, before_operation in [('after-build', 'Resource'), ('after-check', 'ProgramCheckPollingFinished')]:
        read = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == stage)
        selection = next(row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == stage)
        events.remove(read); events.remove(selection)
        index = next(i for i, row in enumerate(events) if row.get('operation') == before_operation)
        events[index:index] = [selection, read]
    return raw, {'protocol_version': 1, 'status': 'observed', 'native_version': '1.635.0.1', 'cpu': 'FX3U/FX3UC',
                 'validation': {'project': source['project_id'], 'events': events}}, snapshots


@pytest.mark.parametrize('damage', ['missing-task-selection', 'unbound-task-program', 'empty-task',
    'missing-source-phase', 'changed-global-declarations', 'missing-generated-buffer', 'missing-compile-reports',
    'wrong-end-count', 'missing-check-start', 'unstarted-target', 'conversion-failed-cached-output', 'source-read-before-check'])
def test_native_validation_keeps_failed_or_partial_stages_distinct(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['native_source_locations']['status'] == 'completed'
    assert result['public_check'] == {'status': 'incomplete', 'completed': False}
    assert not result['diagnostic_projection']['empty_public_list_means_no_errors']
    events = observation['validation']['events']
    event = lambda operation: next(row for row in events if row.get('operation') == operation)
    if damage == 'missing-task-selection':
        events.remove(event('NativeTaskSelection'))
    elif damage == 'unbound-task-program':
        event('NativeTaskSelection')['tasks'][0]['programs'][0]['read_name']['name'] = 'UNSELECTED'
    elif damage == 'empty-task':
        event('NativeTaskSelection')['tasks'][0]['programs'] = []
    elif damage == 'missing-source-phase':
        del snapshots['before-build.bin']
    elif damage == 'changed-global-declarations':
        from gxw.container_writer import replace_project_stream, validate_cfb_streams
        from gxw.project_metadata import logical_mapping
        mapping = logical_mapping(validate_cfb_streams(raw)['projectdatalist.xml'])
        key = mapping['Global1.gh']
        original = validate_cfb_streams(snapshots['imported-hdb.bin'])[key]
        snapshots['imported-hdb.bin'], _ = replace_project_stream(snapshots['imported-hdb.bin'], key, original[:-1] + bytes([original[-1] ^ 1]))
    elif damage == 'missing-generated-buffer':
        del snapshots['generated.bin']
    elif damage == 'missing-compile-reports':
        events.remove(event('CompileRawReports'))
    elif damage == 'wrong-end-count':
        event('ProgramCheckRawReports')['reports'][-1]['arguments'][0]['text'] = '2'
    elif damage == 'missing-check-start':
        events.remove(event('Compiler.ProgramCheck'))
    elif damage == 'unstarted-target':
        event('ProgramCheckTargetOrder')['targets'].append([99] + [0] * 11)
    elif damage == 'source-read-before-check':
        read = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'after-check')
        events.remove(read)
        events.insert(0, read)
    else:
        event('Progress')['count'] = 1
        event('CompileRawReports')['reports'] = [{'kind': 1, 'code': 0x20}]
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'not_established'
    if damage == 'conversion-failed-cached-output':
        assert result['compilation']['acceptance'] == 'rejected'
        assert result['generated_resources']


def test_native_validation_ignores_stored_match_flags_when_original_buffers_change():
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import project_native_validation
    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.sampled_from(['before-build.bin', 'after-build.bin', 'after-check.bin',
                           'generated.bin', 'published.bin', 'submitted.bin', 'completed.bin']),
           st.integers(min_value=0, max_value=100000))
    def check(filename, index):
        raw, observation, snapshots = native_validation_observation()
        original = snapshots[filename]
        offset = index % len(original)
        snapshots[filename] = original[:offset] + bytes([original[offset] ^ 1]) + original[offset + 1:]
        result = project_native_validation(raw, observation, snapshots)
        assert result['current_raw_source_check'] == 'not_established'
        assert not result['public_check']['completed']
    check()


def test_native_validation_missing_location_preserves_the_original_error():
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    observation['validation']['events'] = [row for row in observation['validation']['events']
        if row.get('operation') != 'NativeDiagnosticLocation']
    result = project_native_validation(raw, observation, snapshots)
    assert result['current_raw_source_check'] == 'established'
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert result['native_source_locations']['status'] == 'partial_or_unresolved'
    assert result['raw_diagnostics'][0]['original']['code'] == 0x050c9300
    assert result['raw_diagnostics'][0]['source_projection']['status'] == 'unresolved'


def native_reference_observation():
    """Synthetic reference/range transport; acceptance still comes from native runs."""
    raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    reference = {'library': '', 'source': '1', 'program_kind': 208, 'network': 1,
                 'name': 'Y0', 'resource': 'MAIN', 'task': 'TASK', 'instance': '1'}
    records = {'operation': 'NativeSourceReferences', 'hresult': 0, 'code': 0,
        'query': 'all-references', 'declared': 0, 'plural': 0, 'symbol': '', 'scope': '',
        'count': 1, 'rows': [reference],
        'provenance': 'original CreateProgramAnalysis3 and GetProgramAnalysis3; current compilation; 88-byte public records'}
    # These are ordinary informational analysis messages, not conversion failure.
    reports = [{'kind': 1, 'code': code} for code in (0x19, 0x20)]
    analysis = [
        {'operation': 'Compiler.CreateProgramAnalysis3', 'hresult': 0, 'code': 0},
        {'operation': 'NativeReferenceProgress', 'poll': 1, 'hresult': 0, 'code': 0, 'count': 2, 'percent': 100},
        {'operation': 'NativeReferenceRawReports', 'poll': 1, 'percent': 100, 'reports': reports},
        {'operation': 'Compiler.GetProgramAnalysis3', 'hresult': 0, 'code': 0, 'count': 1}, records]
    index = next(i for i, row in enumerate(events) if row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck')
    events[index:index] = analysis
    query = next(row['source_location'] for row in events if row.get('operation') == 'NativeDiagnosticLocation')
    query['location'].update(step_count=-1, element_id=-1)
    query['instance_ranges'] = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': 28,
        'candidates': [{'original_reference': reference, 'hresult': 0, 'code': 0,
            'query': {'library': '', 'pou': '1', 'program_kind': 208, 'network': 1,
                      'start_step': 1, 'step_count': -1, 'element_id': -1},
            'range': {'resource': 'MAIN', 'start_step': 28, 'step_count': 1, 'timestamp': 0}}]}
    return raw, observation, snapshots


def native_lexical_observation():
    """Synthetic prefix transport over independently recorded FX3U IL text."""
    from gxw.native_diagnostics import native_code_reader_scope
    case = json.loads((ROOT/'tests/fixtures/gxw_fx3g_lexical_native.json').read_text(encoding='utf8'))['cases'][0]
    body = bytes.fromhex(case['body_hex'])
    output = base64.b64decode(case['native']['520']['output_base64'])
    scope = native_code_reader_scope('FX3U/FX3UC')
    # This frozen MOV has a five-step header followed by K1 and R0 tokens.
    read = {**scope, 'operation': 'NativeCodeLexicalRead', 'resource': 'MAIN',
        'body_bytes': 13, 'provided_bytes': 14, 'consumed_bytes': 13,
        'input_file': 'native-input.bin', 'output_file': 'native-output.bin', 'output_bytes': len(output),
        'calls': {'object_new': True, 'open': 0, 'set_version': None, 'decode': 0, 'close': 0},
        'prefixes': [{'input_bytes': offset, 'return_code': 0, 'steps': 5} for offset in (5, 9, 13)],
        'completed': True,
        'provenance': 'original ChangePToILcode and GetStepSize; retained generated primary bytes after check; not execution'}
    events = [{'operation': 'OwnedBackendModule', 'name': scope['module'], 'version': scope['version']},
              {'operation': 'ProgramCheckRawProgress', 'percent': 100, 'hresult': 0, 'code': 0}, read]
    return events, {'MAIN': (body, b'', b'')}, {'native-input.bin': body+b'\0', 'native-output.bin': output}


@pytest.mark.parametrize('damage', ['missing-prefix', 'wrong-prefix-step', 'duplicate-prefix', 'wrong-native-text',
    'missing-output', 'changed-input', 'partial-decode', 'wrong-version', 'wrong-cpu', 'boolean-code',
    'before-check', 'failed-reader', 'opaque-text', 'multiple-readers'])
def test_native_lexical_binding_keeps_incomplete_or_conflicting_reads_unresolved(damage):
    from gxw.native_diagnostics import bind_native_code_lexical_read
    events, generated, snapshots = native_lexical_observation()
    bind = lambda: bind_native_code_lexical_read(events, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)[0]
    actual = bind()
    assert actual['status'] == 'current'
    assert actual['records'] == [{'record_index': 0, 'native_step': 0, 'source_offset': 0,
                                 'source_end': 13, 'op': 'MOV', 'args': ['K1', 'R0']}]
    read = events[-1]
    if damage == 'missing-prefix':
        read['prefixes'].pop()
    elif damage == 'wrong-prefix-step':
        read['prefixes'][-1]['steps'] += 1
    elif damage == 'duplicate-prefix':
        read['prefixes'][-1] = dict(read['prefixes'][0])
    elif damage == 'wrong-native-text':
        snapshots['native-output.bin'] = snapshots['native-output.bin'].replace(b'R0', b'R1')
    elif damage == 'missing-output':
        del snapshots['native-output.bin']
    elif damage == 'changed-input':
        snapshots['native-input.bin'] = b'?' + snapshots['native-input.bin'][1:]
    elif damage == 'partial-decode':
        read['consumed_bytes'] -= 1
    elif damage == 'wrong-version':
        events[0]['version'] = '15.32'
    elif damage == 'wrong-cpu':
        read['native_cpu'] = 521
    elif damage == 'boolean-code':
        read['calls']['decode'] = False
    elif damage == 'before-check':
        events.remove(read); events.insert(0, read)
    elif damage == 'failed-reader':
        read['calls']['close'] = 1
    elif damage == 'opaque-text':
        snapshots['native-output.bin'] = snapshots['native-output.bin'].replace(b'R0', b'\x81\x30')
    else:
        events.append(dict(read))
    actual = bind()
    assert actual['status'] == 'unresolved' and not actual['records']
    assert not actual['public_check_promoted']


@pytest.mark.parametrize('cpu,native_cpu', [('Q03UDV', 209), ('FX3U/FX3UC', 520),
                                         ('Q02', None), ('FX3G', None), ('FX0N', None)])
def test_native_lexical_reader_scope_is_bound_to_exact_observed_cpu_menu(cpu, native_cpu):
    from gxw.native_diagnostics import native_code_reader_scope
    scope = native_code_reader_scope(cpu)
    assert (scope['native_cpu'] if scope else None) == native_cpu
    if scope:
        assert scope['scope_cpu'] == cpu


def test_native_lexical_binding_never_accepts_changed_input_or_missing_prefixes():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_code_lexical_read

    @settings(max_examples=40, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=13), st.integers(min_value=0, max_value=2))
    def check(position, prefix):
        events, generated, snapshots = native_lexical_observation()
        original = bind_native_code_lexical_read(events, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)
        assert original[0]['status'] == 'current'
        altered = bytearray(snapshots['native-input.bin']); altered[position] ^= 1
        changed = {**snapshots, 'native-input.bin': bytes(altered)}
        assert bind_native_code_lexical_read(events, generated, changed, cpu='FX3U/FX3UC', codepage=936)[0]['status'] == 'unresolved'
        partial = deepcopy(events); del partial[-1]['prefixes'][prefix]
        assert bind_native_code_lexical_read(partial, generated, snapshots, cpu='FX3U/FX3UC', codepage=936)[0]['status'] == 'unresolved'
    check()


def fbd_output_projection_inputs():
    """Two synthetic FBs write the same address through separate current ports."""
    query = {'hresult': 0, 'code': 0, 'resource': 'MAIN', 'code_step': 1,
             'location': {'library': '', 'pou': 'FBD_MAIN', 'program_kind': 208, 'network': 1}}
    diagnostic = {'kind': 2, 'code': 0x050c9300, 'name': {'text': 'MAIN'}, 'step': 1, 'arguments': [{'text': 'Y00'}]}
    records = [{'record_index': i, 'native_step': i, 'source_offset': i*8, 'source_end': (i+1)*8,
                'op': 'LD' if i % 2 == 0 else 'OUT', 'args': [f'M{100+i//2}' if i%2 == 0 else 'Y0']}
               for i in range(4)]
    model = {'cpu': 'Q03UDV', 'schema_version': 2, 'unknown_record_count': 0,
             'program': 'FBD_MAIN.Program.pou', 'nodes': [], 'wires': []}
    references = []
    for i, symbol in enumerate(('FIRST', 'SECOND')):
        y, offset = i*4, 100+i*100
        model['nodes'].extend([
            {'id': f'n{offset}', 'source_offset': offset, 'symbol': symbol, 'template': 'function_block:TEST_FB',
             'x': 0, 'y': y, 'width': 2, 'height': 3,
             'ports': [{'name': 'RESULT', 'formal_name': 'RESULT', 'class_code': 4, 'data_type': 'BOOL',
                        'side': 'out', 'x': 2, 'y': 1, 'negated': False}]},
            {'id': f'n{offset+50}', 'source_offset': offset+50, 'symbol': 'Y0', 'template': 'output',
             'x': 2, 'y': y, 'width': 2, 'height': 2, 'ports': [{'x': 0, 'y': 1, 'negated': False}]}])
        base = {'library': '', 'source': 'FBD_MAIN', 'program_kind': 208, 'network': 1, 'resource': 'MAIN',
                'task': 'TASK', 'instance': 'FBD_MAIN', 'array_data_type': 0, 'left': 0, 'top': y, 'right': 2, 'bottom': y+3}
        references.extend([
            {**base, 'name': symbol+'.RESULT', 'address': f'M{100+i}', 'attribute': 1, 'class_code': 4, 'data_type': 1},
            {**base, 'name': symbol, 'type': 'TEST_FB', 'attribute': 1, 'class_code': 1},
            {**base, 'name': 'Y0', 'address': 'Y00', 'attribute': 2, 'class_code': 0, 'data_type': 0,
             'left': 2, 'right': 4, 'bottom': y+2}])
    return query, diagnostic, records, references, model


def test_fbd_output_projection_keeps_instance_and_port_identity_under_permutations():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import correlate_fbd_bool_output

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=0, max_value=1), st.permutations(range(6)), st.permutations(range(4)),
           st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ_', min_size=1, max_size=24))
    def check(selected, order, node_order, renamed):
        query, diagnostic, records, references, model = fbd_output_projection_inputs()
        step = selected*2+1; query['code_step'] = diagnostic['step'] = step
        source = model['nodes'][selected*2]; source['symbol'] = renamed
        references[selected*3]['name'] = renamed+'.RESULT'; references[selected*3+1]['name'] = renamed
        # A same-address, same-geometry reference in another instance is not this call.
        foreign = {**references[selected*3], 'task': 'OTHER_TASK', 'instance': 'FOREIGN'}
        shuffled = [references[i] for i in order] + [foreign]
        model['nodes'] = [model['nodes'][i] for i in node_order]
        project = lambda rows=shuffled: correlate_fbd_bool_output(query, diagnostic, records, rows, model,
            selected_pou='FBD_MAIN', instance=('TASK', 'FBD_MAIN'))
        actual = project()
        assert actual['status'] == 'uniquely_correlated'
        assert actual['source']['object_id'] == f'n{100+selected*100}'
        assert actual['source']['formal'] == 'RESULT' and actual['source']['side'] == 'out'
        assert actual['source']['instance'] == renamed
        assert actual['output']['object_id'] == f'n{150+selected*100}'
        assert not actual['public_check_promoted']
        # Conflicting current facts cannot be resolved by reference/node order.
        assert project(shuffled+[deepcopy(references[selected*3])])['status'] == 'unresolved'
        source['ports'][0]['class_code'] = 5
        assert project()['status'] == 'unresolved'
    check()


def test_fbd_output_projection_uses_network_membership_with_coincident_nodes_and_empty_blocks():
    from copy import deepcopy
    from dataclasses import replace
    from hypothesis import given, settings, strategies as st
    from gxw.connectivity import ConnectivityGraph, ConnectivityNet, PortRef
    from gxw.models import NodeKind, Point
    from gxw.native_diagnostics import correlate_fbd_bool_output

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=2, max_value=5), st.data())
    def check(count, data):
        selected = data.draw(st.integers(min_value=0, max_value=count-1))
        # The empty block occupies an ordinal but has no code, nodes or nets.
        order = data.draw(st.permutations(range(count+1)))
        query, diagnostic, _, original_refs, original_model = fbd_output_projection_inputs()
        model = {**original_model, 'blocks': [{} for _ in order], 'nodes': [], 'wires': []}
        records, references, nets = [], [], []
        for identity in range(count):
            block = order.index(identity)
            offset = 100+identity*200
            source, output = deepcopy(original_model['nodes'][:2])
            source.update(id=f'n{offset}', source_offset=offset, symbol=f'STAGE_{identity}', y=0, block=block)
            output.update(id=f'n{offset+50}', source_offset=offset+50, x=4, y=0, block=block)
            model['nodes'].extend([source, output])
            model['wires'].append({'source_offset': offset+75, 'block': block, 'start': [2,1], 'end': [4,1]})
            member, call, terminal = deepcopy(original_refs[:3])
            for row in (member, call, terminal):
                row.update(network=block+1, top=0)
            member.update(name=source['symbol']+'.RESULT', address=f'M{100+identity}', bottom=3)
            call.update(name=source['symbol'], bottom=3)
            terminal.update(left=4, right=6, bottom=2)
            references.extend([member, call, terminal])
            records.extend([
                {'record_index': identity*2, 'native_step': identity*2, 'source_offset': identity*16,
                 'source_end': identity*16+8, 'op': 'LD', 'args': [member['address']]},
                {'record_index': identity*2+1, 'native_step': identity*2+1, 'source_offset': identity*16+8,
                 'source_end': identity*16+16, 'op': 'OUT', 'args': ['Y0']}])
            nets.append(ConnectivityNet(identity, (
                PortRef(offset, 0, NodeKind.FUNCTION_BLOCK, source['symbol'], 0, Point(2,1)),
                PortRef(offset+50, 0, NodeKind.OUTPUT, 'Y0', 3, Point(4,1))), (offset+75,), block))
        query['location']['network'] = order.index(selected)+1
        query['code_step'] = diagnostic['step'] = selected*2+1
        model['nodes'] = list(data.draw(st.permutations(model['nodes'])))
        model['wires'] = list(data.draw(st.permutations(model['wires'])))
        references = list(data.draw(st.permutations(references)))
        connectivity = ConnectivityGraph(model['program'], tuple(data.draw(st.permutations(nets))))
        project = lambda graph=connectivity: correlate_fbd_bool_output(query, diagnostic, records, references,
            model, selected_pou='FBD_MAIN', instance=('TASK', 'FBD_MAIN'), connectivity=graph)
        actual = project()
        assert actual['status'] == 'uniquely_correlated'
        assert actual['source']['object_id'] == f'n{100+selected*200}'
        assert actual['output']['object_id'] == f'n{150+selected*200}'
        assert actual['network'] == order.index(selected)+1
        assert actual['connection']['block_index'] == order.index(selected)
        assert not actual['public_check_promoted']
        # Source, native reference and topology membership must agree. A valid
        # membership in another block is still not this diagnostic's source.
        foreign = order.index(count)
        chosen = next(node for node in model['nodes'] if node['id'] == actual['source']['object_id'])
        chosen['block'] = foreign
        assert project()['status'] == 'unresolved'
        chosen['block'] = order.index(selected)
        wire = next(w for w in model['wires'] if w['source_offset'] == 175+selected*200)
        wire['block'] = foreign
        assert project()['status'] == 'unresolved'
        wire['block'] = order.index(selected)
        damaged = replace(connectivity, nets=tuple(replace(net, block_index=foreign) if net.index == selected else net
                                                  for net in connectivity.nets))
        assert project(damaged)['status'] == 'unresolved'
        query['location']['network'] = foreign+1
        assert project()['status'] == 'unresolved'
        query['location']['network'] = order.index(selected)+1
        model['cpu'] = 'FX3U/FX3UC'
        assert project()['status'] == 'unresolved'  # Multi-network FX scope has no native control yet.
    check()


def test_native_compile_positions_keep_in_out_formal_ambiguity_and_current_writeback_direction():
    from dataclasses import replace
    from hypothesis import given, settings, strategies as st
    from gxw.connectivity import ConnectivityGraph, ConnectivityNet, PortRef
    from gxw.models import NodeKind, Point
    from gxw.native_diagnostics import correlate_native_compile_position

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=5), st.integers(min_value=0, max_value=64),
           st.integers(min_value=0, max_value=64), st.data())
    def check(network, x, y, data):
        # The native controls identify a FB by network/bbox plus formal name,
        # or identify its writeback terminal by network/bbox without that name.
        model = {'cpu': 'Q03UDV', 'schema_version': 2, 'program': 'FBD_MAIN.Program.pou',
                 'unknown_record_count': 0, 'blocks': [{} for _ in range(network+1)], 'nodes': [], 'wires': []}
        for block in range(network+1):
            offset = 100+block*200
            model['nodes'].extend([
                {'id': f'n{offset}', 'source_offset': offset, 'block': block, 'symbol': 'STAGE_'+str(block),
                 'template': 'function_block:TEST_FB', 'x': x, 'y': y, 'width': 2, 'height': 3,
                 'ports': [{'name': side+'.STATE', 'formal_name': 'STATE', 'side': side, 'class_code': 5,
                            'data_type': 'WORD', 'x': px, 'y': 1} for side, px in [('in',0), ('out',2)]]},
                {'id': f'n{offset+50}', 'source_offset': offset+50, 'block': block, 'symbol': 'Y1',
                 'template': 'output', 'x': x+2, 'y': y, 'width': 2, 'height': 2,
                 'ports': [{'name': 'IN', 'x': 0, 'y': 1}]}])
        model['nodes'] = list(data.draw(st.permutations(model['nodes'])))
        report = {'kind': 2, 'code': 0x500c2025, 'instance_kind': 6, 'library': {'text': ''},
            'name': {'text': 'FBD_MAIN'}, 'instance': {'text': 'FBD_MAIN'}, 'program_kind': 208,
            'network': network, 'step': -1, 'left': x, 'top': y, 'right': x+2, 'bottom': y+3,
            'arguments': [{'text': 'STATE'}]}
        project = lambda r=report, graph=None: correlate_native_compile_position(r, cpu='Q03UDV', pou='FBD_MAIN',
            program_kind=208, model=model, connectivity=graph)
        actual = project(); graph = actual['graph_projection']
        offset = 100+(network-1)*200
        assert actual['status'] == 'source-resolved' and actual['instance_resolution'] == 'not_available_in_compile_report'
        assert graph['object']['object_id'] == f'n{offset}'
        assert graph['port_association']['status'] == 'ambiguous'
        assert {(p['port_name'], p['side']) for p in graph['port_association']['candidates']} == {
            ('in.STATE','in'), ('out.STATE','out')}
        # Original report coordinates select the terminal. The current Core
        # connectivity associates it with out.STATE; no error-code side rule.
        writeback = {**report, 'code': 0x500c2017, 'left': x+2, 'right': x+4, 'bottom': y+2, 'arguments': []}
        net = ConnectivityNet(0, (
            PortRef(offset, 1, NodeKind.FUNCTION_BLOCK, 'STAGE_'+str(network-1), 0, Point(x+2,y+1)),
            PortRef(offset+50, 0, NodeKind.OUTPUT, 'Y1', 3, Point(x+2,y+1))), (), network-1)
        connectivity = ConnectivityGraph(model['program'], (net,))
        resolved = project(writeback, connectivity)
        association = resolved['graph_projection']['port_association']
        assert association['status'] == 'uniquely_correlated'
        assert association['candidates'][0]['port_name'] == 'out.STATE'
        assert not resolved['public_check_promoted']
        # Public reports carry an original native body ID. Names, network
        # and coordinates cannot substitute for that identity.
        body_id = [1, *data.draw(st.lists(st.integers(min_value=0, max_value=0xffffffff), min_size=11, max_size=11))]
        public = {**writeback, 'report_interface': 'public-compiler', 'record_size': 100,
                  'source_object_id': body_id}
        public.pop('library')  # There is no library string in the public record.
        public_project = lambda r=public, identity=body_id: correlate_native_compile_position(r, cpu='Q03UDV',
            pou='FBD_MAIN', program_kind=208, model=model, connectivity=connectivity, source_body_id=identity)
        assert public_project()['graph_projection'] == resolved['graph_projection']
        changed_id = list(body_id); index = data.draw(st.integers(min_value=0, max_value=11))
        changed_id[index] = (changed_id[index]+1) & 0xffffffff
        assert public_project({**public, 'source_object_id': changed_id})['status'] == 'unresolved'
        assert public_project(identity=changed_id)['status'] == 'unresolved'
        assert public_project(identity=None)['status'] == 'unresolved'
        assert public_project({**public, 'record_size': 68})['status'] == 'unresolved'
        damaged = replace(connectivity, nets=(replace(net, block_index=network),))
        assert project(writeback, damaged)['graph_projection']['port_association']['status'] == 'unresolved'
        wrong_endpoint = replace(net.ports[0], port_index=0, point=Point(x,y+1))
        damaged = replace(connectivity, nets=(replace(net, ports=(wrong_endpoint,net.ports[1])),))
        assert project(writeback, damaged)['graph_projection']['port_association']['status'] == 'unresolved'
        assert project({**report, 'instance_kind': 3})['status'] == 'unresolved'
        # Another current object at the same location is genuinely ambiguous.
        chosen = next(n for n in model['nodes'] if n['id'] == f'n{offset}')
        model['nodes'].append({**chosen, 'id': 'duplicate', 'source_offset': 9999})
        assert project()['graph_projection']['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('damage', ['conversion-failure', 'missing-record', 'duplicate-poll',
                                  'mixed-interfaces', 'missing-layout'])
def test_native_validation_public_compile_conversion_is_separate_from_compile_and_check(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    events = observation['validation']['events']
    poll = next(row for row in events if row.get('operation') == 'Progress')
    record = next(row for row in events if row.get('operation') == 'CompileRawReports')
    poll['report_interface'] = 'public-compiler'
    record.update(operation='CompilePublicReports', report_interface='public-compiler', record_size=100)
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['acceptance'] == 'accepted'
    assert actual['compilation']['diagnostic_conversion'] == {'status': 'completed', 'failures': []}
    assert actual['compilation']['source_context']['status'] == 'current'
    assert actual['raw_backend_check']['status'] == 'completed-rejected'
    assert not actual['public_check']['completed']
    if damage == 'conversion-failure':
        # A later empty successful poll must not erase an earlier public
        # conversion error or turn it into an ordinary source rejection.
        poll['poll'] = record['poll'] = 2
        events.insert(events.index(poll), {**poll, 'poll': 1, 'hresult': -2147467259,
            'code': 0x2d010025, 'percent': 90, 'count': 1})
    elif damage == 'missing-record':
        events.remove(record)
    elif damage == 'duplicate-poll':
        events.insert(events.index(poll), deepcopy(poll))
    elif damage == 'mixed-interfaces':
        record['operation'] = 'CompileRawReports'
    else:
        del record['record_size']
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['acceptance'] == 'not_established'
    assert actual['compilation']['diagnostics_complete'] is False
    assert actual['compilation']['source_context']['status'] == 'unresolved'
    assert actual['compilation']['diagnostic_conversion']['status'] == (
        'failed' if damage == 'conversion-failure' else 'incomplete')
    assert actual['current_raw_source_check'] == 'not_established'


@pytest.mark.parametrize('damage', ['missing-before', 'missing-after', 'stale-before', 'stale-after',
                                  'read-order', 'configuration', 'partial-reports', 'body-identity'])
def test_native_compile_source_context_requires_both_original_phase_reads_and_complete_reports(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_validation_observation()
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['source_context']['status'] == 'current'
    events = observation['validation']['events']
    before = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'before-build')
    after = next(row for row in events if row.get('operation') == 'NativeBodyRead' and row.get('stage') == 'after-build')
    if damage == 'missing-before':
        events.remove(before)
    elif damage == 'missing-after':
        events.remove(after)
    elif damage.startswith('stale-'):
        row = before if damage == 'stale-before' else after
        snapshots[row['file']] = snapshots[row['file']][:-1] + b'!'
    elif damage == 'read-order':
        events.remove(before); events.append(before)
    elif damage == 'configuration':
        snapshots['imported-hdb.bin'] = b'unrelated configuration'
    elif damage == 'partial-reports':
        events.remove(next(row for row in events if row.get('operation') == 'CompileRawReports'))
    else:
        selection = next(row for row in events if row.get('operation') == 'NativeBodySelection' and row.get('stage') == 'after-build')
        identity = deepcopy(selection['source_object']); selection['source_object'] = identity
        changed = list(identity['lookup']['id']); changed[-1] += 1
        identity['lookup']['id'] = identity['body']['id'] = changed
        after['body'] = changed
    actual = project_native_validation(raw, observation, snapshots)
    assert actual['compilation']['source_context']['status'] == 'unresolved'
    assert not actual['compilation']['source_context']['public_check_promoted']


def test_native_compile_st_position_retains_source_line_without_inventing_expanded_instance():
    from gxw.native_diagnostics import correlate_native_compile_position
    report = {'kind': 2, 'code': 0x500c1200, 'instance_kind': 6, 'library': {'text': ''},
              'name': {'text': 'INNER_FB'}, 'instance': {'text': 'INNER_FB'}, 'program_kind': 193,
              'network': -1, 'step': 1, 'left': -1, 'top': 1, 'right': -1, 'bottom': -1}
    project = lambda row=report, cpu='Q03UDV': correlate_native_compile_position(row, cpu=cpu,
        pou='INNER_FB', program_kind=193, text='\nRESULT :? SIGNAL;')
    actual = project()
    assert actual['status'] == 'source-resolved'
    assert actual['source']['zero_based_line'] == 1 and actual['source']['text'] == 'RESULT :? SIGNAL;'
    assert actual['instance_resolution'] == 'not_available_in_compile_report'
    assert 'instance' not in actual and not actual['public_check_promoted']
    assert project({**report, 'step': 0})['status'] == 'unresolved'
    assert project({**report, 'top': 2, 'step': 2})['status'] == 'unresolved'
    assert project(cpu='FX3U/FX3UC')['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['two-outputs', 'array-producer', 'unknown-record', 'multiple-blocks',
                                  'wrong-network', 'negated-port', 'broken-code-adjacency',
                                  'missing-producer-name', 'missing-producer-position'])
def test_fbd_output_projection_retains_ambiguity_and_unobserved_boundaries(damage):
    from copy import deepcopy
    from gxw.native_diagnostics import correlate_fbd_bool_output
    query, diagnostic, records, references, model = fbd_output_projection_inputs()
    if damage == 'two-outputs':
        duplicate = {**deepcopy(model['nodes'][1]), 'id': 'n999', 'source_offset': 999}
        model['nodes'].append(duplicate)
    elif damage == 'array-producer':
        references[0]['array_data_type'] = 1
    elif damage == 'unknown-record':
        model['unknown_record_count'] = 1
    elif damage == 'multiple-blocks':
        model['blocks'] = [{}, {}]
    elif damage == 'wrong-network':
        query['location']['network'] = 2
    elif damage == 'negated-port':
        model['nodes'][0]['ports'][0]['negated'] = True
    elif damage == 'broken-code-adjacency':
        records[1]['source_offset'] += 1
    elif damage == 'missing-producer-name':
        del references[0]['name']
    else:
        del references[0]['left']
    actual = correlate_fbd_bool_output(query, diagnostic, records, references, model, selected_pou='FBD_MAIN',
                                       instance=('TASK', 'FBD_MAIN'))
    assert actual['status'] == 'unresolved' and not actual['public_check_promoted']
    if damage == 'two-outputs':
        assert actual['source_status'] == 'uniquely_correlated'
        assert actual['output_status'] == 'ambiguous' and set(actual['output_candidates']) == {'n150', 'n999'}


@pytest.mark.parametrize('damage', ['missing-analysis', 'missing-reports', 'partial-analysis', 'rejected-analysis',
    'truncated-rows', 'query-failure', 'old-analysis-order', 'duplicate-analysis', 'conflicting-version',
    'missing-instance-range', 'foreign-source-range', 'stale-check-code'])
def test_native_validation_instance_locations_require_current_complete_original_analysis(damage):
    from gxw.native_diagnostics import project_native_validation
    raw, observation, snapshots = native_reference_observation()
    result = project_native_validation(raw, observation, snapshots)
    assert result['source_references']['status'] == 'current'
    assert result['native_instance_locations']['status'] == 'completed'
    assert result['raw_diagnostics'][0]['source_projection']['instance_projection']['instance'] == '1'
    assert result['raw_diagnostics'][0]['graph_projection']['status'] == 'unresolved'
    events = observation['validation']['events']
    event = lambda operation: next(row for row in events if row.get('operation') == operation)
    if damage == 'missing-analysis':
        events.remove(event('Compiler.CreateProgramAnalysis3'))
    elif damage == 'missing-reports':
        events.remove(event('NativeReferenceRawReports'))
    elif damage == 'partial-analysis':
        event('NativeReferenceProgress')['percent'] = event('NativeReferenceRawReports')['percent'] = 99
    elif damage == 'rejected-analysis':
        event('NativeReferenceRawReports')['reports'][0]['kind'] = 2
    elif damage == 'truncated-rows':
        event('NativeSourceReferences')['rows'] = []
    elif damage == 'query-failure':
        event('Compiler.GetProgramAnalysis3')['hresult'] = -2147467259
    elif damage == 'old-analysis-order':
        row = event('Compiler.CreateProgramAnalysis3'); events.remove(row); events.insert(0, row)
    elif damage == 'duplicate-analysis':
        events.append(dict(event('NativeSourceReferences')))
    elif damage == 'conflicting-version':
        events.append({'operation': 'OwnedBackendModule', 'name': 'DZDataABS_Compiler_IEC.dll', 'version': 'unobserved'})
    elif damage in ('missing-instance-range', 'foreign-source-range'):
        query = event('NativeDiagnosticLocation')['source_location']['instance_ranges']
        if damage == 'missing-instance-range':
            query['candidates'] = []
        else:
            query['candidates'][0]['query']['pou'] = 'FOREIGN'
    else:
        snapshots['submitted.bin'] = snapshots['submitted.bin'][:-1] + b'!'
    result = project_native_validation(raw, observation, snapshots)
    assert result['native_instance_locations']['status'] != 'completed'
    assert result['raw_diagnostics'][0]['source_projection'].get('instance_projection', {}).get('status') != 'instance-resolved'
    assert result['raw_diagnostics'][0]['original']['code'] == 0x050c9300
    assert result['raw_backend_check']['status'] == 'completed-rejected'
    assert not result['public_check']['completed']


def test_native_instance_ranges_preserve_identity_under_order_and_code_position_changes():
    from copy import deepcopy
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_instance_interval

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(st.sampled_from([193, 208]), st.integers(min_value=0, max_value=100000),
           st.integers(min_value=1, max_value=1024), st.integers(min_value=0, max_value=3),
           st.permutations(range(8)))
    @example(193, 0, 1, 0, list(reversed(range(8))))
    def check(kind, shift, size, selected, order):
        location = {'program_kind': kind, 'network': -1 if kind == 193 else 1,
                    'start_step': 3 if kind == 193 else 1, 'step_count': -1, 'element_id': -1}
        step = shift + selected * size
        query = {'query': {'resource': 'MAIN', 'start_step': step}, 'location': location}
        refs, candidates = [], []
        for index in range(8):
            path = ('CALLER_A' if index < 4 else 'INACTIVE') + '.MODULE_' + str(index) + '.INNER'
            row = {'task': 'TASK', 'instance': path}
            refs.append(row)
            value = {'resource': 'MAIN', 'start_step': shift + index * size, 'step_count': size, 'timestamp': 0}
            if index >= 4:
                value = {'resource': None, 'start_step': -1, 'step_count': -1, 'timestamp': -1}
            candidates.append({'original_reference': row, 'hresult': 0, 'code': 0, 'range': value,
                'query': {'library': '', 'pou': path, **location, 'step_count': 1 if kind == 193 else -1}})
        evidence = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': step,
                    'candidates': [candidates[index] for index in order]}
        result = bind_native_instance_interval(query, list(reversed(refs)), evidence)
        assert result['interval']['name'] == 'TASK.' + refs[selected]['instance']
        assert result['interval']['step_count'] == size
        # Omitting even a nonexecuting declaration makes the selection incomplete.
        for missing in range(8):
            partial = {**evidence, 'candidates': [row for row in evidence['candidates'] if row is not candidates[missing]]}
            assert bind_native_instance_interval(query, refs, partial)['interval'] is None
        ambiguous = deepcopy(evidence)
        other = next(row for row in ambiguous['candidates'] if row['original_reference'] == refs[(selected + 1) % 4])
        other['range'] = dict(candidates[selected]['range'])
        assert bind_native_instance_interval(query, refs, ambiguous)['interval'] is None
    check()


def test_native_st_call_chain_uses_explicit_calls_and_current_caller_offsets():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import correlate_st_diagnostic

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.integers(min_value=1, max_value=4), st.integers(min_value=0, max_value=1),
           st.integers(min_value=1, max_value=10000), st.integers(min_value=1, max_value=5), st.data())
    def check(depth, selected, current_offset, block_count, data):
        references, leaf_refs, nodes, paths = [], [], [], []
        texts = {'INNER_FB': '\n\n\nY1 := SIGNAL;'}
        network = data.draw(st.integers(min_value=1, max_value=block_count))
        for caller_index, caller in enumerate(['FBD_LEFT', 'FBD_RIGHT']):
            parent_instance = caller
            task = 'TASK_' + str(caller_index)
            for level in range(depth + 1):
                parent_pou = caller if level == 0 else 'WRAPPER_' + str(level - 1)
                child_pou = 'WRAPPER_' + str(level) if level < depth else 'INNER_FB'
                member = 'SHARED_STAGE' if level == 0 else 'NESTED_' + str(level)
                row = {'library': '', 'resource': 'MAIN', 'task': task, 'instance': parent_instance,
                    'name': member, 'type': child_pou, 'source': parent_pou, 'class_code': 1, 'attribute': 1,
                    'program_kind': 208 if level == 0 else 193, 'network': network if level == 0 else -1,
                    'left': 8, 'top': 9 if level == 0 else 0, 'right': 13, 'bottom': 14}
                references.append(row)
                if level:
                    texts[parent_pou] = member + '();'
                parent_instance += '.' + member
            paths.append(parent_instance)
            leaf = {'library': '', 'resource': 'MAIN', 'task': task, 'instance': parent_instance,
                    'name': 'Y1', 'source': 'INNER_FB', 'attribute': 2, 'program_kind': 193, 'top': 3}
            references.append(leaf); leaf_refs.append(leaf)
            nodes.append({'id': 'current-' + caller, 'source_offset': current_offset + caller_index,
                          'symbol': 'SHARED_STAGE', 'template': 'function_block:WRAPPER_0',
                          'x': 8, 'y': 9, 'width': 5, 'height': 5, 'block': network-1})
        # Both callers deliberately share the same symbol, FB type and geometry.
        # Their tasks, explicit call references and native ranges distinguish them.
        query = {'hresult': 0, 'code': 0, 'query': {'resource': 'MAIN', 'start_step': 20 + selected * 10},
            'location': {'library': '', 'pou': 'INNER_FB', 'program_kind': 193,
                         'network': -1, 'start_step': 3, 'step_count': -1, 'element_id': -1}}
        ranges = {'status': 'completed', 'resource': 'MAIN', 'diagnostic_step': query['query']['start_step'],
            'candidates': [{'original_reference': row, 'hresult': 0, 'code': 0,
                'query': {'library': '', 'pou': row['instance'], 'program_kind': 193,
                          'network': -1, 'start_step': 3, 'step_count': 1, 'element_id': -1},
                'range': {'resource': 'MAIN', 'start_step': 20 + index * 10, 'step_count': 3, 'timestamp': 0}}
                for index, row in enumerate(leaf_refs)]}
        references = list(data.draw(st.permutations(references)))
        caller = ['FBD_LEFT', 'FBD_RIGHT'][selected]
        caller_nodes = [nodes[selected]] + [{**nodes[selected], 'id': 'foreign-block-'+str(block),
                        'source_offset': current_offset+100+block, 'block': block}
                        for block in range(block_count) if block != network-1]
        caller_nodes = list(data.draw(st.permutations(caller_nodes)))
        actual = correlate_st_diagnostic(query, [], references, texts, caller_nodes,
                                         caller_pou=caller, native_ranges=ranges, caller_block_count=block_count)
        assert actual['status'] == 'uniquely_correlated'
        assert actual['instance'] == paths[selected]
        assert len(actual['call_chain']) == depth + 1
        assert actual['caller']['object_id'] == 'current-' + caller
        assert actual['caller']['source_offset'] == current_offset + selected
        # One missing intermediate call cannot be inferred by splitting the path.
        for call in actual['call_chain']:
            partial = [row for row in references if row is not call['native_reference']]
            assert correlate_st_diagnostic(query, [], partial, texts, caller_nodes,
                caller_pou=caller, native_ranges=ranges, caller_block_count=block_count)['status'] == 'unresolved'
        # A matching cached link interval must not override incomplete original API evidence.
        partial_ranges = deepcopy(ranges); partial_ranges['candidates'].pop()
        assert correlate_st_diagnostic(query, [actual['compiled_interval']], references, texts, caller_nodes,
            caller_pou=caller, native_ranges=partial_ranges, caller_block_count=block_count)['status'] == 'unresolved'
        assert correlate_st_diagnostic(query, [], references, texts, [nodes[1 - selected]],
            caller_pou=['FBD_LEFT', 'FBD_RIGHT'][1 - selected], native_ranges=ranges,
            caller_block_count=block_count)['status'] == 'unresolved'
        if block_count > 1:
            del nodes[selected]['block']
            assert correlate_st_diagnostic(query, [], references, texts, caller_nodes, caller_pou=caller,
                native_ranges=ranges, caller_block_count=block_count)['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('partial', [False, True])
def test_native_validation_transport_reads_copies_before_removing_its_workspace(tmp_path, monkeypatch, partial):
    import types
    import gxworks2.workspace_adapter as adapter
    raw, observation, snapshots = native_validation_observation()
    if partial:
        observation['status'] = 'failed'
        observation['message'] = 'native_check_incomplete_timeout'
        observation['validation']['events'] = [row for row in observation['validation']['events']
            if row.get('operation') not in ('ProgramCheckRawReports', 'ProgramCheckPollingFinished')]
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    roots, cleaned = [], []
    class Process:
        pid = 123
        returncode = 1 if partial else 0
        def __init__(self, arguments, **kwargs):
            assert arguments == [str(executable.resolve())]
            roots.append(Path(kwargs['cwd']))
        def communicate(self, payload, timeout):
            request = json.loads(payload)
            assert request['operation'] == 'validate_project'
            assert (roots[-1] / 'input.gxw').read_bytes() == raw
            assert request['source']['programs'][0]['name'] == '1'
            for name, data in snapshots.items():
                (roots[-1] / name).write_bytes(data)
            return json.dumps(observation), ''
        def poll(self):
            return self.returncode
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter.subprocess, 'Popen', Process)
    monkeypatch.setattr(adapter, '_clean_native_temp', lambda pid, token: cleaned.append((pid, token)))
    result = adapter.NativeWorkspaceValidation(executable, tmp_path).validate(raw)
    assert cleaned and cleaned[0][0] == 123 and len(cleaned[0][1]) == 32
    assert not roots[0].exists()
    assert result['adapter']['exit_code'] == (1 if partial else 0)
    assert result['compilation']['acceptance'] == 'accepted'
    assert result['raw_backend_check']['completed'] is not partial
    assert result['current_raw_source_check'] == ('not_established' if partial else 'established')


def test_native_validation_transport_stops_timed_out_child_before_native_cleanup(tmp_path, monkeypatch):
    import types
    import gxworks2.workspace_adapter as adapter
    from gxw.object_model import default_baseline
    executable = tmp_path / 'adapter.exe'; executable.write_bytes(b'transport double')
    lifecycle = []
    class Process:
        pid = 456
        returncode = None
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, *args, **kwargs):
            if self.returncode is None:
                raise adapter.subprocess.TimeoutExpired('isolated child', 1)
            lifecycle.append('drained')
            return '', ''
        def kill(self):
            lifecycle.append('stopped')
            self.returncode = 1
        def poll(self):
            return self.returncode
    monkeypatch.setattr(adapter, 'os', types.SimpleNamespace(name='nt', environ=adapter.os.environ))
    monkeypatch.setattr(adapter.subprocess, 'Popen', Process)
    monkeypatch.setattr(adapter, '_clean_native_temp', lambda pid, token: lifecycle.append('owned-cleanup'))
    with pytest.raises(adapter.WorkspaceValidationError, match='超时'):
        adapter.NativeWorkspaceValidation(executable, tmp_path).validate(default_baseline(), timeout=1)
    assert lifecycle == ['stopped', 'drained', 'owned-cleanup']


@pytest.mark.parametrize('damage', ['missing-copy', 'different-target', 'different-resource',
                                  'different-framing', 'different-version', 'different-header'])
def test_native_check_input_binding_refuses_missing_or_unbound_task_copies(damage):
    from gxw.native_diagnostics import bind_native_check_input
    submitted, completed, evidence = native_check_input_snapshots(b'original code')
    assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'current'
    if damage == 'missing-copy':
        del evidence['snapshots']['completed.bin']
    elif damage == 'different-target':
        completed['target'][0] = 2
    elif damage == 'different-resource':
        completed['rows'][0]['resource'] = 'OTHER'
    elif damage == 'different-framing':
        completed['rows'][0]['framing']['body_offset'] = 69
    elif damage == 'different-version':
        evidence['checker_version'] = '15.51'
    else:
        full = bytearray(evidence['snapshots']['completed.bin'])
        full[4] ^= 1
        evidence['snapshots']['completed.bin'] = bytes(full)
    assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'unresolved'


def test_native_check_input_binding_never_promotes_changed_generated_or_task_bytes():
    from hypothesis import example, given, settings, strategies as st
    from gxw.native_diagnostics import bind_native_check_input

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.binary(min_size=1, max_size=128), st.integers(min_value=0, max_value=127),
           st.binary(max_size=32))
    @example(b'\0', 0, b'')
    def check(body, index, padding):
        submitted, completed, evidence = native_check_input_snapshots(body, padding)
        assert bind_native_check_input(submitted, completed, **evidence)['status'] == 'current'
        position = index % len(body)
        changed = bytearray(body)
        changed[position] ^= 1
        evidence['generated']['MAIN'] = bytes(changed)
        stale = bind_native_check_input(submitted, completed, **evidence)
        assert stale['status'] == 'stale' and stale['task_copy_stable']
        assert not stale['public_check_promoted']
        evidence['generated']['MAIN'] = body
        full = bytearray(evidence['snapshots']['completed.bin'])
        full[completed['rows'][0]['framing']['body_offset'] + position] ^= 1
        evidence['snapshots']['completed.bin'] = bytes(full)
        unstable = bind_native_check_input(submitted, completed, **evidence)
        assert unstable['status'] == 'unresolved' and not unstable['task_copy_stable']

    check()


def test_native_source_snapshot_requires_the_last_original_read_buffer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import bind_native_source_snapshot
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    body = workspace_body(source.raw)
    identity = [1] + [0] * 11
    before = {'operation': 'NativeBodyRead', 'body': identity, 'bytes': len(body),
        'file': 'before.bin',
        'provenance': 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'}
    after = {**before, 'bytes': len(body) + 2, 'file': 'after.bin', 'stored_match': True}
    snapshots = {'before.bin': body, 'after.bin': body + b'\0\0'}
    result = bind_native_source_snapshot(source.raw, body_id=identity,
        reads=[before, after], snapshots=snapshots)
    assert result['status'] == 'stale' and result['native_snapshot'] == 'after.bin'
    assert result['source_body_bytes'] + 2 == result['native_body_bytes']
    assert bind_native_source_snapshot(source.raw, body_id=identity,
        reads=[before, after], snapshots={'before.bin': body})['status'] == 'unresolved'
    assert bind_native_source_snapshot(source.raw, body_id=[0] * 12,
        reads=[before], snapshots=snapshots)['status'] == 'unresolved'


def test_native_source_snapshot_binding_isolated_from_other_body_reads(monkeypatch):
    hypothesis = pytest.importorskip('hypothesis')
    from hypothesis import strategies as st
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import bind_native_source_snapshot
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    body = workspace_body(source.raw)
    identity, other = [1] + [0] * 11, [2] + [0] * 11

    def read(filename, raw, selected):
        return {'operation': 'NativeBodyRead', 'body': selected, 'bytes': len(raw), 'file': filename,
            'provenance': 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'}

    @hypothesis.settings(max_examples=80, deadline=None, derandomize=True)
    @hypothesis.given(st.lists(st.binary(max_size=64), max_size=16), st.binary(min_size=1, max_size=64))
    @hypothesis.example([], b'\0\0')
    def check(unrelated, extra):
        snapshots = {'current.bin': body}
        reads = [read('current.bin', body, identity)]
        for index, raw in enumerate(unrelated):
            filename = f'other-{index}.bin'
            snapshots[filename] = raw
            reads.append(read(filename, raw, other))
        result = bind_native_source_snapshot(source.raw, body_id=identity, reads=reads, snapshots=snapshots)
        assert result['status'] == 'current' and result['native_snapshot'] == 'current.bin'
        # Later unrelated reads cannot revive the stale selected-body snapshot.
        changed = body + extra
        snapshots['changed.bin'] = changed
        reads.insert(1, read('changed.bin', changed, identity))
        result = bind_native_source_snapshot(source.raw, body_id=identity, reads=reads, snapshots=snapshots)
        assert result['status'] == 'stale' and result['native_snapshot'] == 'changed.bin'
        del snapshots['changed.bin']
        assert bind_native_source_snapshot(source.raw, body_id=identity,
            reads=reads, snapshots=snapshots)['status'] == 'unresolved'

    check()


def native_source_chain_inputs():
    from src.gxw.object_model import default_baseline, read_project
    from src.gxw.native_write import workspace_body
    source, _, _ = read_project(default_baseline())
    project = [9] + [0] * 11
    names = ['LEAF', 'PARENT', 'CALLER']
    programs, selections, reads, snapshots = {}, [], [], {}
    body = workspace_body(source.raw)
    for index, name in enumerate(names, 1):
        owner_id, body_id = [1, index] + [0] * 10, [2, index] + [0] * 10
        source_object = {'status': 'verified-source-object', 'parent': project,
            'lookup_type': 32, 'name': name,
            'lookup': {'hresult': 0, 'code': 0, 'id': body_id},
            'owner_lookup': {'lookup_type': 26, 'hresult': 0, 'code': 0, 'id': owner_id},
            'body_parent': {'hresult': 0, 'code': 0, 'id': owner_id},
            'body': {'id': body_id, 'read_type': {'hresult': 0, 'code': 0, 'data_type': 32},
                     'read_name': {'hresult': 0, 'code': 0, 'name': 'Program'}},
            'owner': {'id': owner_id, 'read_type': {'hresult': 0, 'code': 0, 'data_type': 26},
                      'read_name': {'hresult': 0, 'code': 0, 'name': name}},
            'language': {'hresult': 0, 'code': 0, 'object_id': owner_id, 'value': 208, 'expected': 208}}
        selections.append({'operation': 'NativeBodySelection', 'name': name,
                           'program_kind': 208, 'source_object': source_object})
        filename = name + '-body.bin'
        reads.append({'operation': 'NativeBodyRead', 'body': body_id, 'bytes': len(body), 'file': filename,
            'provenance': 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'})
        programs[name], snapshots[filename] = source.raw, body
    return programs, names, dict(project_id=project, native_version='1.635.0.1',
                                 selections=selections, reads=reads, snapshots=snapshots)


def test_public_source_name_collision_cannot_promote_a_body_or_generated_code_position():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.native_diagnostics import compare_public_source_diagnostic

    @settings(max_examples=60, deadline=None, derandomize=True)
    @given(st.permutations(['LEAF', 'PARENT', 'CALLER']), st.integers(min_value=0, max_value=2),
           st.sampled_from([193, 208]), st.integers(min_value=0, max_value=4096))
    def check(names, actual_index, language, step):
        _, _, evidence = native_source_chain_inputs()
        sources = {row['name']: deepcopy(row['source_object']) for row in evidence['selections']}
        resource, actual_pou = names[0], names[actual_index]
        source = sources[actual_pou]
        source['language'].update(value=language, expected=language)
        query = {'resource': resource, 'code_step': step, 'hresult': 0, 'code': 0,
            'location': {'library': '', 'pou': actual_pou, 'program_kind': language, 'network': 1,
                         'start_step': 1, 'step_count': -1, 'element_id': 0, 'action_transition_present': False},
            'source_object': source}
        diagnostic = {'kind': 2, 'code': 0x050c9300, 'step': step,
                      'library': {'text': ''}, 'name': {'text': resource}}
        public = {'kind': 2, 'code': diagnostic['code'], 'name': resource, 'instance': resource,
            'object_id': sources[resource]['body']['id'], 'program_kind': 1, 'step': step,
            'network': -1, 'left': -1, 'top': step, 'right': -1, 'bottom': -1}
        compare = lambda p=public, q=query: compare_public_source_diagnostic(p, q, diagnostic,
            project_id=evidence['project_id'], native_version=evidence['native_version'])
        result = compare()
        assert result['status'] == ('source-consistent' if actual_pou == resource else 'source-conflict')
        assert result['native_source_projection']['source']['pou'] == actual_pou
        assert result['public_position_binding'] == {'status': 'different-representation',
            'public_program_kind': 1, 'source_program_kind': language, 'direct_source_mapping_allowed': False}
        assert not result['public_check_promoted'] and result['original_public_diagnostic'] == public
        # Matching both body and language still supplies no independent
        # proof that these public coordinates are current source positions.
        matching = {**public, 'object_id': source['body']['id'], 'program_kind': language}
        assert compare(matching)['public_position_binding']['status'] == 'not_established'
        assert not compare(matching)['public_position_binding']['direct_source_mapping_allowed']
        assert compare({**public, 'step': step+1})['status'] == 'unresolved'
        assert compare(q={**query, 'code': 0x2d010025})['status'] == 'unresolved'
    check()


@pytest.mark.parametrize('damage', [None, 'adapter', 'compiler', 'workspace', 'missing_adapter', 'missing_compiler'])
def test_saved_public_source_audit_keeps_version_binding_with_workspace_reads(tmp_path, monkeypatch, damage):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import analyze_directory
    _, _, evidence = native_source_chain_inputs()
    sources = {row['name']: row['source_object'] for row in evidence['selections']}
    query = {'resource': 'PARENT', 'code_step': 28, 'hresult': 0, 'code': 0,
        'location': {'library': '', 'pou': 'LEAF', 'program_kind': 208, 'network': 1,
                     'start_step': 1, 'step_count': -1, 'element_id': 0, 'action_transition_present': False},
        'source_object': sources['LEAF']}
    public = {'kind': 2, 'code': 0x050c9300, 'name': 'PARENT', 'instance': 'PARENT',
        'object_id': sources['PARENT']['body']['id'], 'program_kind': 1, 'step': 28,
        'network': -1, 'left': -1, 'top': 28, 'right': -1, 'bottom': -1}
    modules = {'adapter': 'DZDataABS_CompilerAdapter.dll', 'compiler': 'DZDataABS_Compiler_IEC.dll',
               'workspace': 'DZDataABS_Workspace.dll'}
    events = [{'operation': 'ProjectID', 'words': evidence['project_id']}]
    events.extend({'operation': 'OwnedBackendModule', 'name': name,
                   'version': 'unobserved' if damage == key else '1.635.0.1'}
                  for key, name in modules.items() if damage != 'missing_' + key)
    (tmp_path/'native-events.jsonl').write_text('\n'.join(json.dumps(row) for row in events), encoding='utf-8')
    (tmp_path/'public-source-diagnostic-audit.json').write_text(json.dumps([{
        'audit': {'original_public_diagnostic': public}, 'source_query': query,
        'raw_observation': {'kind': public['kind'], 'code': public['code'], 'step': 28,
                            'library_hex': '', 'name_hex': b'PARENT'.hex()}}]), encoding='utf-8')
    result = analyze_directory(tmp_path)
    audit = result['diagnostic_projection']['source_audits'][0]
    assert result['diagnostic_projection']['source_body_binding'] == (
        'conflicted' if damage is None else 'partial_or_unresolved')
    assert audit['status'] == ('source-conflict' if damage is None else 'unresolved')
    assert not audit['public_check_promoted'] and not result['public_check']['completed']
    if damage is None:
        assert audit['native_source_projection']['source']['pou'] == 'LEAF'
        assert audit['public_position_binding']['status'] == 'different-representation'


@pytest.mark.parametrize('missing', ['LEAF', 'PARENT', 'CALLER'])
def test_native_source_chain_cannot_replace_a_missing_body_with_identical_other_sources(monkeypatch, missing):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import bind_native_source_chain
    programs, names, evidence = native_source_chain_inputs()
    result = bind_native_source_chain(programs, names, **evidence)
    assert result['status'] == 'current' and len(result['sources']) == 3
    # All bodies contain identical bytes, but each belongs to a different POU.
    del evidence['snapshots'][missing + '-body.bin']
    result = bind_native_source_chain(programs, names, **evidence)
    assert result['status'] == 'unresolved'
    assert next(row for row in result['sources'] if row['pou'] == missing)['status'] == 'unresolved'
    assert bind_native_source_chain(programs, [], **evidence)['status'] == 'unresolved'


@pytest.mark.parametrize('damage', ['owner_name', 'parent', 'language', 'latest_selection', 'version'])
def test_native_source_chain_requires_original_identity_for_every_caller(monkeypatch, damage):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import bind_native_source_chain
    programs, names, evidence = native_source_chain_inputs()
    parent = evidence['selections'][1]
    source = parent['source_object']
    if damage == 'owner_name':
        source['owner']['read_name']['name'] = 'UNRELATED'
    elif damage == 'parent':
        source['body_parent']['id'] = evidence['selections'][2]['source_object']['owner_lookup']['id']
    elif damage == 'language':
        source['language']['value'] = 193
    elif damage == 'latest_selection':
        evidence['selections'].append({'operation': 'NativeBodySelection', 'name': 'PARENT'})
    else:
        evidence['native_version'] = 'unobserved'
    assert bind_native_source_chain(programs, names, **evidence)['status'] == 'unresolved'


def test_native_source_chain_status_is_independent_of_required_order_and_unrelated_reads(monkeypatch):
    from hypothesis import example, given, settings, strategies as st
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import bind_native_source_chain

    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.permutations(['LEAF', 'PARENT', 'CALLER']), st.sampled_from(['LEAF', 'PARENT', 'CALLER']),
           st.binary(min_size=1, max_size=32), st.lists(st.binary(max_size=32), max_size=8))
    @example(['CALLER', 'PARENT', 'LEAF'], 'PARENT', b'\0', [b''])
    def check(order, changed, extra, unrelated):
        programs, _, evidence = native_source_chain_inputs()
        read = next(row for row in evidence['reads'] if row['file'] == changed + '-body.bin')
        data = evidence['snapshots'][read['file']] + extra
        latest = {**read, 'bytes': len(data), 'file': 'changed.bin'}
        evidence['reads'].append(latest)
        evidence['snapshots']['changed.bin'] = data
        for index, raw in enumerate(unrelated):
            filename = 'unrelated-' + str(index) + '.bin'
            evidence['reads'].append({**read, 'body': [99, index] + [0] * 10,
                                      'file': filename, 'bytes': len(raw)})
            evidence['snapshots'][filename] = raw
        result = bind_native_source_chain(programs, order, **evidence)
        assert result['status'] == 'stale'
        assert next(row for row in result['sources'] if row['pou'] == changed)['native_snapshot'] == 'changed.bin'
        del evidence['snapshots']['changed.bin']
        assert bind_native_source_chain(programs, order, **evidence)['status'] == 'unresolved'

    check()


def diagnostic_source_witnesses():
    with zipfile.ZipFile(ROOT/'research/evidence/gxw-native-diagnostic-sources-20261002.zip') as archive:
        return json.loads(archive.read('cpu-source-witnesses.json'))


@pytest.mark.parametrize('case', diagnostic_source_witnesses(), ids=lambda case: case['cpu'])
def test_native_fb_diagnostics_correlate_only_unique_current_instance_ranges(monkeypatch, case):
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import correlate_st_diagnostic
    result = case['phase_result']
    references = result['source_references']['queries'][0]['rows']
    actual = [correlate_st_diagnostic(query, result['link_witness']['rows'], references,
        case['source_texts'], case['caller_nodes']) for query in result['diagnostic_locations']]
    assert actual == case['projections']
    assert all(row['status'] == case['expected_status'] and not row['public_check_promoted'] for row in actual)
    phases = case['phase_classification']
    assert phases['checker_resources']['correspondence'] == 'current'
    assert phases['public_check']['status'] == 'incomplete'
    assert phases['diagnostic_projection']['status'] == 'failed'
    assert phases['current_source_check'] == 'not_established'
    if case['cpu'] == 'FX0N':
        assert all(row['source_instance_candidates'] == [
            'FBD_MATRIX.APP_MODULE_STAGE_A', 'FBD_MATRIX.APP_MODULE_STAGE_B'] for row in actual)
        assert all(row['compiled_interval_name'] == 'TASK_MATRIX.FBD_MATRIX' for row in actual)
    else:
        assert {row['instance'] for row in actual} == {
            'FBD_MATRIX.APP_MODULE_STAGE_A', 'FBD_MATRIX.APP_MODULE_STAGE_B'}
        assert all(row['source']['text'] == 'Y1 := SIGNAL;' for row in actual)


@pytest.mark.parametrize('damage', ['native_failure', 'empty_location', 'library', 'coarse_fbd',
    'duplicate_interval', 'range_boundary', 'duplicate_reference', 'missing_object'])
def test_diagnostic_source_mapping_keeps_missing_and_ambiguous_evidence_unresolved(monkeypatch, damage):
    from copy import deepcopy
    monkeypatch.syspath_prepend(str(ROOT/'research'))
    from program_check_evidence import correlate_st_diagnostic
    case = diagnostic_source_witnesses()[0]
    result = case['phase_result']
    query = deepcopy(result['diagnostic_locations'][0])
    links = deepcopy(result['link_witness']['rows'])
    references = deepcopy(result['source_references']['queries'][0]['rows'])
    nodes = deepcopy(case['caller_nodes'])
    if damage == 'native_failure':
        query['hresult'] = -2147467259
    elif damage == 'empty_location':
        query['location'] = None
    elif damage == 'library':
        query['location']['library'] = 'UNRELATED_LIBRARY'
    elif damage == 'coarse_fbd':
        query['location']['program_kind'] = 208
    elif damage in ('duplicate_interval', 'range_boundary'):
        interval = next(row for row in links if row['name'] == 'TASK_MATRIX.FBD_MATRIX.APP_MODULE_STAGE_A')
        if damage == 'duplicate_interval':
            links.append(deepcopy(interval))
        else:
            query['query']['start_step'] = interval['step_start'] + interval['step_count']
            links = [interval]
    elif damage == 'duplicate_reference':
        reference = next(row for row in references if row['name'] == 'Y1' and row['instance'] == 'FBD_MATRIX.APP_MODULE_STAGE_A')
        references.append(deepcopy(reference))
    else:
        nodes = []
    actual = correlate_st_diagnostic(query, links, references, case['source_texts'], nodes)
    assert actual['status'] == 'unresolved' and not actual['public_check_promoted']


def test_native_debug_range_rejection_keeps_original_error_and_does_not_replace_public_check():
    with zipfile.ZipFile(ROOT/'research/evidence/gxw-native-diagnostic-sources-20261002.zip') as archive:
        cases = json.loads(archive.read('native-debug-boundary.json'))
    assert len(cases) == 3
    for case in cases:
        assert case['returncode'] == 0 and case['public_check']
        assert len(case['debug_observations']) == 12
        assert all(row['hresult'] == -2147467259 and row['code'] == 0x50010006
            and row['initialized'] and row['count'] == 0 and not row['ranges_present']
            for row in case['debug_observations'])
