"""Stored compiler artifacts checked against archived native observations."""
import base64
import importlib
import json
from pathlib import Path
import struct
import zipfile

import pytest

from gxw.compiler_debug import parse_compiler_debug
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
