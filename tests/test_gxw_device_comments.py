"""Device-comment tables checked against frozen native GXW/CSV controls."""
from dataclasses import replace
from functools import lru_cache
from collections import Counter
import csv
import io
import json
import struct
import zipfile

from hypothesis import example, given, settings, strategies as st
import pytest

from gxw.container import CompoundFile
from gxw.device_comments import edit_device_comments, parse_device_comments, serialize_device_comments
from gxw.models import GXWFormatError
from gxw.project_resolver import GXWProjectResolver
from plc.device_identity import canonical_device
from tools.offline_inputs import private_path


@lru_cache(maxsize=None)
def native_control(which="before"):
    # These CSVs were exported by GX Works2, independently of the QCD decoder.
    archive = private_path("research/" + "evidence/gxworks2-native-send-20261011.zip")
    with zipfile.ZipFile(archive) as source:
        project = {"before": "navigation/navigation.gxw", "after": "navigation/empty.gxw"}[which]
        resolver = GXWProjectResolver(CompoundFile(source.read(project)))
        raw = resolver.read_logical_file("COMMENT.qcd")
        table = list(csv.reader(io.StringIO(source.read(f"navigation/comments-{which}.csv").decode("utf-16")),
                                delimiter="\t"))
    return raw, {canonical_device(row[0]): row[1] for row in table[2:] if row}


@lru_cache(maxsize=1)
def original_qcd_corpus():
    fixture = json.loads(private_path("tests/fixtures/gxw_offline_qcd_corpus.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(private_path("research/" + "evidence/gxw-offline-qcd-corpus-20261021.zip")) as archive:
        return fixture, {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize("index", range(356))
def test_original_comment_corpus_counted_boundaries_and_exact_noop(index):
    fixture, archive = original_qcd_corpus()
    case = fixture["tables"][index]
    raw = archive[case["member"]]
    document = parse_device_comments(raw, logical_name=case["source"], plc_model=case["cpu"])
    assert len(document.ranges) == case["ranges"]
    main = tuple(row for group in document.ranges for row in group.rows)
    assert len(main) == case["row_count"]
    assert Counter(row.device_code for row in main) == {int(key): value for key, value in case["device_counts"].items()}
    assert 50 + struct.unpack_from("<I", document.header, 50)[0] == case["primary_end"]
    assert [(row.device_code, row.index, row.bit_index, row.offset, len(row.raw)) for row in document.bit_rows] == [
        tuple(row) for row in case["bit_rows"]]
    assert not document.trailer
    assert document.bit_extension_supported
    assert serialize_device_comments(document) == raw


def native_comment_cases():
    return original_qcd_corpus()[0]["native_controls"]


@pytest.mark.parametrize("case", native_comment_cases(), ids=lambda case: case["case"])
def test_typed_native_set_get_save_and_cold_comment_names_are_independent_oracles(case):
    _, archive = original_qcd_corpus()
    raw = archive[case["member"]]
    document = parse_device_comments(raw, plc_model=case["cpu"])
    assert serialize_device_comments(document) == raw
    for member in [case["results_member"], *([case["cold_results_member"]] if "cold_results_member" in case else [])]:
        observation = json.loads(archive[member])
        restored = {row["address"]: row["text"] for row in observation.get("restored", [])}
        for row in observation["rows"]:
            if row.get("get_hresult") == row.get("get_code") == 0:
                address = row["address"] + (f".{row['bit']:X}" if "bit" in row else "")
                assert document.comments.get(address, "") == restored.get(address, row["text"])
                assert row["canary_intact"]


@pytest.mark.parametrize("case", native_comment_cases(), ids=lambda case: case["case"])
def test_new_comment_encoding_matches_independently_saved_native_row_bytes(case):
    _, archive = original_qcd_corpus()
    observation = json.loads(archive[case["results_member"]])
    expected = {}
    for row in observation["rows"]:
        if row.get("set_hresult") == row.get("set_code") == row.get("get_hresult") == row.get("get_code") == 0:
            address = row["address"] + (f".{row['bit']:X}" if "bit" in row else "")
            expected[address] = row["text"]
    expected.update({row["address"]: row["text"] for row in observation.get("restored", [])})
    before, comments = native_control()
    empty = serialize_device_comments(edit_device_comments(parse_device_comments(before), dict.fromkeys(comments)))
    result = edit_device_comments(parse_device_comments(empty, plc_model=case["cpu"]), expected)
    saved = serialize_device_comments(result)
    reread = parse_device_comments(saved, plc_model=case["cpu"])
    assert reread.comments == expected
    native_raw = archive[case["member"]]
    # Independently counted original spans bind payloads without using this
    # decoder to construct the expected row boundaries or encoded bytes.
    native_spans = {(code, index, None): native_raw[offset:offset + size]
                    for code, index, offset, size in case["rows"]}
    native_spans.update({(code, index, bit): native_raw[offset:offset + size]
                         for code, index, bit, offset, size in case["bit_rows"]})
    for row in reread.rows:
        assert row.raw == native_spans[row.identity]
    assert not reread.write_blockers


@pytest.mark.parametrize("which", ["before", "after"])
@pytest.mark.parametrize("cpu", ["FX3U", "FX3U/FX3UC"])
def test_native_exported_comment_csv_is_an_independent_decode_oracle(which, cpu):
    raw, comments = native_control(which)
    document = parse_device_comments(raw, plc_model=cpu)
    assert document.comments == comments
    assert document.plc_model == cpu
    assert len(document.rows) == len(comments)
    assert not document.write_blockers
    assert serialize_device_comments(document) == raw


def test_full_range_insert_delete_and_text_changes_match_native_saved_body():
    before, first_comments = native_control("before")
    after, second_comments = native_control("after")
    updates = {address: second_comments.get(address) for address in first_comments.keys() | second_comments.keys()}
    saved = serialize_device_comments(edit_device_comments(parse_device_comments(before), updates,
                                                          expected=first_comments))
    # The independent native save changes the timestamp. The complete serialized
    # range directory, row metadata, counts and strings must nevertheless match.
    assert saved[:28] == after[:28]
    assert saved[44:] == after[44:]
    assert parse_device_comments(saved).comments == second_comments
    assert parse_device_comments(before).comments == first_comments


def test_delete_all_rows_matches_the_independent_native_empty_table():
    before, comments = native_control()
    saved = serialize_device_comments(edit_device_comments(parse_device_comments(before), dict.fromkeys(comments)))
    with zipfile.ZipFile(private_path("research/" + "evidence/gxw-token-native-20260919.zip")) as source:
        empty = GXWProjectResolver(CompoundFile(source.read("samples/empty.gxw"))).read_logical_file("COMMENT.qcd")
    assert saved[44:] == empty[44:]
    assert parse_device_comments(saved).comments == {}


@settings(max_examples=40, deadline=None, derandomize=True, database=None)
@given(text=st.text(alphabet="ABC中文入力\r\n\té😀", max_size=150))
@example(text="")
@example(text="中文注释" * 40)
def test_text_edits_preserve_every_other_native_row_and_revert_byte_exactly(text):
    raw, original_comments = native_control()
    document = parse_device_comments(raw)
    target = next(row for row in document.rows if row.address == "X0")
    edited = edit_device_comments(document, {"x000": text}, expected={"X0": original_comments["X0"]})
    saved = serialize_device_comments(edited)
    reread = parse_device_comments(saved)
    assert reread.comments == {**original_comments, "X0": text}
    assert saved[:50] == raw[:50]
    assert saved[54:target.offset] == raw[54:target.offset]
    target_after = next(row for row in reread.rows if row.address == "X0")
    assert saved[target_after.offset + len(target_after.raw):] == raw[target.offset + len(target.raw):]
    assert [(row.address, row.raw) for row in reread.rows if row.address != "X0"] == [
        (row.address, row.raw) for row in document.rows if row.address != "X0"]
    assert serialize_device_comments(edit_device_comments(edited, {"X0": text})) == saved
    assert serialize_device_comments(edit_device_comments(edited, {"X0": original_comments["X0"]})) == raw


@settings(max_examples=24, deadline=None, derandomize=True, database=None)
@given(index=st.integers(min_value=100, max_value=120),
       text=st.text(alphabet="ABC中文😀", min_size=1, max_size=50))
def test_insert_delete_existing_table_rows_preserve_other_addresses(index, text):
    raw, original_comments = native_control()
    document = parse_device_comments(raw)
    address = f"M{index}"
    inserted = edit_device_comments(document, {address: text}, expected={address: None})
    saved = serialize_device_comments(inserted)
    assert parse_device_comments(saved).comments == {**original_comments, address: text}
    assert [(row.device_code, row.index, row.raw) for row in inserted.rows if row.address != address] == [
        (row.device_code, row.index, row.raw) for row in document.rows]
    restored = parse_device_comments(serialize_device_comments(edit_device_comments(inserted, {address: None})))
    assert restored.comments == original_comments
    # Rebuilding an affected device directory does not rewrite retained rows.
    assert {row.address: row.raw for row in restored.rows} == {row.address: row.raw for row in document.rows}


def test_new_device_class_and_contiguous_addresses_form_valid_ranges():
    raw, original_comments = native_control()
    document = edit_device_comments(parse_device_comments(raw), {"S0": "Ready", "S1": "Run", "S3": "Done"})
    reread = parse_device_comments(serialize_device_comments(document))
    assert reread.comments == {**original_comments, "S0": "Ready", "S1": "Run", "S3": "Done"}
    assert [(group.first_index, len(group.rows)) for group in reread.ranges if group.device_code == 0x98] == [(0, 2), (3, 1)]


def test_comment_addresses_use_core_cpu_radix_and_do_not_accept_invalid_fx_digits():
    raw, _ = native_control()
    document = edit_device_comments(parse_device_comments(raw), {"X010": "Octal eight"})
    row = next(row for row in document.rows if row.address == "X10")
    assert row.index == 8
    assert parse_device_comments(serialize_device_comments(document)).comments["X10"] == "Octal eight"
    with pytest.raises(GXWFormatError, match="address"):
        edit_device_comments(document, {"X008": "invalid FX I/O"})


def test_source_subset_missing_coverage_never_discards_existing_device_rows():
    raw, comments = native_control()
    document = parse_device_comments(raw, plc_model="FX3U/FX3UC")
    assert document.comments == comments
    # Preserve the source's X/Y rows; timer numbering remains outside this
    # menu's reviewed address subset and must not block comment storage.
    changed = edit_device_comments(document, {"X000": "中文", "Y000": None}, expected={"X0": comments["X0"]})
    assert parse_device_comments(serialize_device_comments(changed), plc_model=document.plc_model).comments == {
        **{key: value for key, value in comments.items() if key != "Y0"}, "X0": "中文"}
    inserted = edit_device_comments(document, {"T002": "annotation with unverified hardware coverage"})
    reread = parse_device_comments(serialize_device_comments(inserted), plc_model=document.plc_model)
    assert reread.comments["T2"] == "annotation with unverified hardware coverage"
    from plc.validation import device_address_status, parse_device_address
    assert parse_device_address("X2", document.plc_model) == ('X', 2)
    assert parse_device_address("T2", document.plc_model) is None
    assert device_address_status("T2", document.plc_model) == 'unverified'


@pytest.mark.parametrize('cpu', ['FX3U', 'fx3u', 'FX3U/FX3UC', 'fx3u/fx3uc',
                                  'FX3UC', 'fx3uc', 'Q03UDV', 'q03udv'])
def test_comment_storage_does_not_extend_known_hardware_limits(cpu):
    raw, _ = native_control()
    document = parse_device_comments(raw, plc_model=cpu)
    with pytest.raises(GXWFormatError, match="coverage"):
        edit_device_comments(document, {"M4294967295": "outside known hardware limits"})
    from plc.validation import parse_device_address, ValidationProfileUnavailableError
    no_core = parse_device_comments(raw, plc_model="Q13UDV")
    inserted = edit_device_comments(no_core, {"M32768": "stored format identity"})
    assert parse_device_comments(serialize_device_comments(inserted), plc_model="Q13UDV").comments["M32768"] == "stored format identity"
    with pytest.raises(ValidationProfileUnavailableError):
        parse_device_address("M32768", "Q13UDV")
    assert no_core.address_validation == "unverified"
    # A missing namespace is a gap in Core's hardware checks, while its proved
    # annotation identity can be stored. Known ranges above still reject.
    stored = edit_device_comments(document, {"P1": "annotation only"})
    assert stored.address_validation == "unverified"


def test_known_qcd_numeric_identity_uses_q_cpu_hexadecimal_spelling():
    raw = bytearray(native_control()[0])
    struct.pack_into("<I", raw, 62, 14)
    document = parse_device_comments(bytes(raw), plc_model="Q03UDV")
    assert [row.address for row in document.rows[:2]] == ["XE", "XF"]
    saved = serialize_device_comments(edit_device_comments(document, {"X000F": "hex identity"}))
    assert parse_device_comments(saved, plc_model="Q03UDV").comments["XF"] == "hex identity"


def test_stale_source_alias_conflict_and_invalid_text_fail_without_changing_source():
    raw, comments = native_control()
    document = parse_device_comments(raw)
    for updates, expected in [({"X0": "changed"}, {"X000": "stale"}),
                              ({"X0": "changed"}, {"M100": "must exist"}),
                              ({"X0": "one", "x000": "two"}, None),
                              ({"X0": "with\0NUL"}, None),
                              ({"X0": "\ud800"}, None),
                              ({"K0": "constant is not a comment device"}, None)]:
        with pytest.raises(GXWFormatError):
            edit_device_comments(document, updates, expected=expected)
        assert serialize_device_comments(document) == raw
        assert document.comments == comments


def test_empty_text_keeps_row_while_none_removes_it():
    raw, comments = native_control()
    empty = edit_device_comments(parse_device_comments(raw), {"X0": ""})
    assert parse_device_comments(serialize_device_comments(empty)).comments == {**comments, "X0": ""}
    removed = edit_device_comments(empty, {"X0": None})
    assert "X0" not in parse_device_comments(serialize_device_comments(removed)).comments
    assert serialize_device_comments(edit_device_comments(removed, {"X0": None})) == serialize_device_comments(removed)


@settings(max_examples=24, deadline=None, derandomize=True, database=None)
@given(metadata=st.binary(min_size=4, max_size=4))
def test_opaque_row_metadata_is_retained_and_cannot_be_edited(metadata):
    raw, _ = native_control()
    first = parse_device_comments(raw).rows[0]
    raw = raw[:first.offset] + metadata + raw[first.offset + 4:]
    document = parse_device_comments(raw)
    assert serialize_device_comments(document) == raw
    changed = parse_device_comments(serialize_device_comments(edit_device_comments(document, {"D0": "Other change"})))
    assert changed.rows[0].raw == document.rows[0].raw
    if metadata != b"\0" * 4:
        with pytest.raises(GXWFormatError, match="metadata"):
            edit_device_comments(document, {"X0": "changed"})
        with pytest.raises(GXWFormatError, match="metadata"):
            edit_device_comments(document, {"X0": None})


def test_unknown_device_code_is_lossless_without_becoming_an_editable_address():
    raw, _ = native_control()
    modified = bytearray(raw)
    struct.pack_into("<H", modified, 60, 0x7FFE)
    document = parse_device_comments(bytes(modified))
    assert document.rows[0].address is None
    assert not document.rows[0].editable
    assert serialize_device_comments(document) == bytes(modified)
    updated = edit_device_comments(document, {"D0": "Known address"})
    reread = parse_device_comments(serialize_device_comments(updated))
    assert [(row.device_code, row.index, row.raw) for row in reread.rows[:2]] == [
        (row.device_code, row.index, row.raw) for row in document.rows[:2]]
    with pytest.raises(GXWFormatError, match="opaque"):
        serialize_device_comments(replace(document, ranges=document.ranges[1:]))


@settings(max_examples=20, deadline=None, derandomize=True, database=None)
@given(trailer=st.binary(min_size=1, max_size=50))
def test_unknown_trailer_preserves_noop_and_rejects_unproven_offset_changes(trailer):
    raw = bytearray(native_control()[0] + trailer)
    struct.pack_into("<I", raw, 50, len(raw) - 50)
    document = parse_device_comments(bytes(raw))
    assert document.trailer == trailer
    assert document.write_blockers == ("unverified_qcd_trailer",)
    assert serialize_device_comments(document) == bytes(raw)
    with pytest.raises(GXWFormatError, match="trailer"):
        edit_device_comments(document, {"X0": "changed"})


@pytest.mark.parametrize("kind", ["signature", "size", "marker", "directory", "row_count", "overlap", "text_count", "terminator", "nul", "utf16"])
def test_malformed_comment_tables_fail_at_their_declared_boundary(kind):
    raw = bytearray(native_control()[0])
    document = parse_device_comments(bytes(raw))
    row = document.rows[0]
    if kind == "signature":
        raw[0] ^= 1
    elif kind == "size":
        struct.pack_into("<I", raw, 50, len(raw))
    elif kind == "marker":
        raw[56] ^= 1
    elif kind == "directory":
        struct.pack_into("<H", raw, 58, 0xFFFF)
    elif kind == "row_count":
        struct.pack_into("<I", raw, 66, 0xFFFFFFFF)
    elif kind == "overlap":
        raw[70:80] = raw[60:70]
    elif kind == "text_count":
        struct.pack_into("<I", raw, row.offset + 4, 0xFFFFFFFF)
    elif kind == "terminator":
        raw[row.offset + len(row.raw) - 2] = 1
    elif kind == "nul":
        raw[row.offset + 8:row.offset + 10] = b"\0\0"
    else:
        raw[row.offset + 8:row.offset + 10] = b"\0\xd8"
    with pytest.raises(GXWFormatError):
        parse_device_comments(bytes(raw))


def test_direct_dataclass_replacement_cannot_change_source_metadata_or_identity():
    document = parse_device_comments(native_control()[0])
    group, row = document.ranges[0], document.rows[0]
    for altered in [replace(row, address="Y0"), replace(row, index=8), replace(row, raw=b"forged"),
                    replace(row, metadata=b"1234"), replace(row, offset=0)]:
        forged = replace(document, ranges=(replace(group, rows=(altered, *group.rows[1:])), *document.ranges[1:]))
        with pytest.raises(GXWFormatError):
            serialize_device_comments(forged)
    with pytest.raises(GXWFormatError, match="envelope"):
        serialize_device_comments(replace(document, header=b"x" + document.header[1:]))


@pytest.mark.parametrize("index", [index for index, case in enumerate(original_qcd_corpus()[0]["tables"])
                                  if case["bit_rows"]])
def test_original_word_bit_extension_and_main_changes_preserve_other_raw_rows(index):
    fixture, archive = original_qcd_corpus()
    case = fixture["tables"][index]
    raw = archive[case["member"]]
    document = parse_device_comments(raw, plc_model=case["cpu"])
    main = next(row for group in document.ranges for row in group.rows if row.editable)
    bit = next(row for row in document.bit_rows if row.editable)
    text = "变长注释_既有主表及逐位表_😀\n" * 30
    edited = edit_device_comments(document, {main.address: text, bit.address: text},
                                  expected={main.address: main.text, bit.address: bit.text})
    saved = serialize_device_comments(edited)
    reread = parse_device_comments(saved, plc_model=case["cpu"])
    assert reread.comments == {**document.comments, main.address: text, bit.address: text}
    assert reread.header[:50] == document.header[:50]
    assert reread.header[54:58] == document.header[54:58]
    changed = {main.identity, bit.identity}
    assert {row.identity: row.raw for row in reread.rows if row.identity not in changed} == {
        row.identity: row.raw for row in document.rows if row.identity not in changed}
    assert serialize_device_comments(edit_device_comments(edited, {main.address: main.text, bit.address: bit.text})) == raw


def test_original_sfc_transition_comment_uses_independently_confirmed_block_identity():
    fixture, archive = original_qcd_corpus()
    case = next(case for case in fixture["tables"] if "64665" in case["device_counts"])
    raw = archive[case["member"]]
    document = parse_device_comments(raw, plc_model=case["cpu"])
    assert document.comments == {"BL0\\TR3": "trDatOk"}
    updated = edit_device_comments(document, {"BL0\\TR3": "transition changed", "BL1\\TR3": "another block"})
    reread = parse_device_comments(serialize_device_comments(updated), plc_model=case["cpu"])
    assert reread.comments == {"BL0\\TR3": "transition changed", "BL1\\TR3": "another block"}
    assert [(row.device_code, row.index) for row in reread.rows] == [(0xFC99, 3), (0xFC99, 0x10003)]
    assert document.address_validation == "unverified"


def test_legacy_comment_option_normalization_matches_the_independent_native_save():
    fixture, archive = original_qcd_corpus()
    case = next(case for case in fixture["tables"] if case["path"].endswith("cmz269\\-plc-gxworks2-\\fenjian.gxw"))
    control = next(case for case in fixture["native_controls"] if case["case"] == "qcd-devices-fx00ffshort-v2")
    raw, native = archive[case["member"]], archive[control["member"]]
    assert raw[54:56] == b"\0\xff" and native[54:56] == b"\0\0"
    document = parse_device_comments(raw, plc_model=case["cpu"])
    assert not document.write_blockers and document.comments == {"BL1": "2"}
    expected = json.loads(archive[control["results_member"]])["rows"][0]["text"]
    edited = edit_device_comments(document, {"BL1": expected}, expected={"BL1": "2"})
    saved = serialize_device_comments(edited)
    assert saved[:50] == raw[:50]
    assert saved[44:] == native[44:]
    assert serialize_device_comments(edit_device_comments(edited, {"BL1": "2"})) == raw


@pytest.mark.parametrize("index", [index for index, case in enumerate(original_qcd_corpus()[0]["tables"])
                                  if original_qcd_corpus()[1][case["member"]][54:56] == b"\0\xff"])
def test_original_legacy_comment_option_edits_keep_every_other_row(index):
    fixture, archive = original_qcd_corpus()
    case = fixture["tables"][index]
    raw = archive[case["member"]]
    document = parse_device_comments(raw, plc_model=case["cpu"])
    assert not document.write_blockers
    target = next((row.address for row in document.rows if row.editable), "M0")
    edited = edit_device_comments(document, {target: "legacy annotation updated"})
    saved = serialize_device_comments(edited)
    reread = parse_device_comments(saved, plc_model=case["cpu"])
    assert saved[54:56] == b"\0\0"
    assert reread.comments == {**document.comments, target: "legacy annotation updated"}
    assert {row.identity: row.raw for row in reread.rows if row.address != target} == {
        row.identity: row.raw for row in document.rows if row.address != target}


@settings(max_examples=24, deadline=None, derandomize=True, database=None)
@given(options=st.binary(min_size=2, max_size=2).filter(lambda value: value not in (b"\0\0", b"\0\xff")))
def test_unproved_comment_header_options_stay_lossless_and_read_only(options):
    raw = bytearray(native_control()[0]); raw[54:56] = options
    document = parse_device_comments(bytes(raw))
    assert document.write_blockers == ("unverified_qcd_header_options",)
    assert serialize_device_comments(document) == bytes(raw)
    with pytest.raises(GXWFormatError, match="header_options"):
        edit_device_comments(document, {"X0": "changed"})


def test_unknown_word_bit_device_rows_are_local_preserve_only_objects():
    case = native_comment_cases()[0]
    _, archive = original_qcd_corpus()
    raw = bytearray(archive[case["member"]])
    struct.pack_into("<I", raw, case["primary_end"] + 12, 0x7FFE)
    document = parse_device_comments(bytes(raw), plc_model=case["cpu"])
    assert document.bit_rows and all(row.address is None and not row.editable for row in document.bit_rows)
    assert serialize_device_comments(document) == bytes(raw)
    changed = edit_device_comments(document, {"M10": "known main", "D10.7": "known bit"})
    reread = parse_device_comments(serialize_device_comments(changed), plc_model=case["cpu"])
    assert [(row.identity, row.raw) for row in reread.bit_rows if row.device_code == 0x7FFE] == [
        (row.identity, row.raw) for row in document.bit_rows]
    with pytest.raises(GXWFormatError, match="opaque"):
        serialize_device_comments(replace(document, bit_rows=document.bit_rows[1:]))


@settings(max_examples=32, deadline=None, derandomize=True, database=None)
@given(text=st.text(alphabet="AB中文日本語é😀\r\n\t", max_size=250),
       bit=st.integers(min_value=1, max_value=14), extra_word=st.integers(min_value=11, max_value=20))
def test_word_bit_unicode_insert_delete_sequences_retain_native_opaque_bytes_and_original_spans(text, bit, extra_word):
    case = native_comment_cases()[0]
    _, archive = original_qcd_corpus()
    raw = archive[case["member"]]
    original = parse_device_comments(raw, plc_model=case["cpu"])
    first = f"D10.{bit:X}"
    second = f"D{extra_word}.F"
    edited = edit_device_comments(original, {"D10.0": text, first: text, second: text},
                                  expected={first: None, second: None})
    reread = parse_device_comments(serialize_device_comments(edited), plc_model=case["cpu"])
    assert reread.comments == {**original.comments, "D10.0": text, first: text, second: text}
    assert {row.identity: row.raw for row in reread.rows if row.address != "D10.0" and row.address not in {first, second}} == {
        row.identity: row.raw for row in original.rows if row.address != "D10.0"}
    restored = edit_device_comments(edited, {"D10.0": original.comments["D10.0"], first: None, second: None})
    assert serialize_device_comments(restored) == raw
    # Deleting an original identity and restoring it remains source-bound.
    removed = edit_device_comments(original, {"D10.0": None, "M10": None})
    assert serialize_device_comments(edit_device_comments(removed, {
        "D10.0": original.comments["D10.0"], "M10": original.comments["M10"]})) == raw


@pytest.mark.parametrize("updates,expected", [({"D10.0": "one", "d0010.00": "two"}, None),
    ({"D10.F": "changed"}, {"D10.F": "stale"}), ({"D10.10": "invalid bit"}, None),
    ({"M10.0": "unproved bit-device comment"}, None), ({"U10000\\G0": "overflow"}, None),
    ({"U1\\G65536": "overflow"}, None), ({"J65536\\B0": "overflow"}, None),
    ({"J1\\W10000": "overflow"}, None), ({"BL1\\M10": "native rejected composite"}, None),
    ({"BL65536\\TR0": "overflow"}, None), ({"BL1\\TR65536": "overflow"}, None),
    ({"D10.F": "a" * 65535}, None)])
def test_composite_and_bit_comment_invalid_or_stale_edits_are_atomic(updates, expected):
    case = native_comment_cases()[0]
    _, archive = original_qcd_corpus()
    raw = archive[case["member"]]
    document = parse_device_comments(raw, plc_model=case["cpu"])
    with pytest.raises(GXWFormatError):
        edit_device_comments(document, updates, expected=expected)
    assert serialize_device_comments(document) == raw


@pytest.mark.parametrize("kind", ["size", "flags", "version", "groups", "words", "bits", "bit_index", "chars", "terminator"])
def test_unknown_or_malformed_independent_extension_does_not_hide_the_main_table(kind):
    case = native_comment_cases()[0]
    _, archive = original_qcd_corpus()
    raw = bytearray(archive[case["member"]])
    start = case["primary_end"]
    positions = {"size": (0, "I", 0xFFFFFFFF), "flags": (4, "I", 1), "version": (8, "H", 2),
                 "groups": (10, "H", 0xFFFF), "words": (16, "I", 0xFFFFFFFF),
                 "bits": (24, "H", 17), "bit_index": (26, "H", 16), "chars": (28, "H", 0xFFFF)}
    if kind == "terminator":
        raw[-2] = 1
    else:
        offset, fmt, value = positions[kind]
        struct.pack_into("<" + fmt, raw, start + offset, value)
    document = parse_device_comments(bytes(raw), plc_model=case["cpu"])
    assert len(document.comments) == case["row_count"]
    assert document.write_blockers == ("unverified_qcd_extension",)
    assert serialize_device_comments(document) == bytes(raw)
    with pytest.raises(GXWFormatError, match="extension"):
        edit_device_comments(document, {"M10": "changed"})
