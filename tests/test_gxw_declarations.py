"""Native CSV declaration oracles, preservation, and transactional project edits."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import zipfile

import pytest

from src.gxw.container import CompoundFile
from src.gxw.declarations import edit_declarations, parse_declarations, serialize_declarations, serialize_label, parse_structure_declarations
from src.gxw.models import GXWFormatError
from src.gxw.project_resolver import GXWProjectResolver
from src.gxw.project_writer import build_gxw_project
from src.gxw.structured_pou import parse_structured_pou
from src.gxw.structured_pou_writer import replace_node_symbol


ROOT = Path(__file__).resolve().parents[1]
NATIVE = json.loads((ROOT / "tests/fixtures/gxw_declarations_20260910.json").read_text())
SIZED_STRINGS = json.loads((ROOT / "tests/fixtures/gxw_sized_string_declarations.json").read_text())
STRUCTURES = json.loads((ROOT / "tests/fixtures/gxw_structure_declarations_native.json").read_text(encoding="utf-8"))["cases"]


def document(case="dtypes", logical="1.Labels.lh"):
    return parse_declarations(bytes.fromhex(NATIVE[case][logical]), logical_name=logical)


def baseline(case):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-20260910.zip") as archive:
        return archive.read(case + ".gxw")


@pytest.mark.parametrize('collision', ['two-locals', 'two-globals', 'unknown-option', 'disabled-option'])
def test_source_scope_resolution_does_not_hide_duplicates_or_unverified_options(collision):
    from src.gxw.declarations import resolve_label
    local = edit_declarations(document('d0'), upserts=[{'name': 'SHARED', 'data_type': 'BOOL'}])
    global_ = edit_declarations(document('d0', 'Global1.gh'), upserts=[{'name': 'SHARED', 'data_type': 'BOOL'}])
    docs = {'1.Labels.lh': local, 'Global1.gh': global_}
    enabled = True
    if collision == 'two-locals':
        docs['1.Labels.lh'] = replace(local, rows=local.rows + (replace(local.rows[0], name='shared'),))
    elif collision == 'two-globals':
        docs['Global2.gh'] = replace(global_, logical_name='Global2.gh')
    else:
        enabled = None if collision == 'unknown-option' else False
    with pytest.raises(GXWFormatError, match='ambiguous'):
        resolve_label(docs, '1.Program.pou', 'shared', global_variable_hiding=enabled)


def test_scope_resolution_selects_the_program_local_table_and_retains_other_scopes():
    from src.gxw.declarations import resolve_label
    local = edit_declarations(document('d0'), upserts=[{'name': 'SHARED', 'data_type': 'BOOL'}])
    other = replace(local, logical_name='OTHER.Labels.lh')
    global_ = edit_declarations(document('d0', 'Global1.gh'), upserts=[{'name': 'SHARED', 'data_type': 'INT'}])
    docs = {'1.Labels.lh': local, 'OTHER.Labels.lh': other, 'Global1.gh': global_}
    assert resolve_label(docs, '1.Program.pou', 'shared', global_variable_hiding=True) == ('1.Labels.lh', local.rows[0])
    shared = next(row for row in global_.rows if row.name == 'SHARED')
    assert resolve_label(docs, 'MISSING.Program.pou', 'shared', global_variable_hiding=True) == ('Global1.gh', shared)
    assert resolve_label(docs, '1.Program.pou', 'missing', global_variable_hiding=True) is None
    assert docs['OTHER.Labels.lh'] is other and docs['Global1.gh'] is global_


def test_source_library_decoding_is_bounded_and_framing_preserves_every_byte():
    import base64
    from src.gxw.library_sources import decode_library_archive, parse_library_source, parse_library_declarations
    fixture = json.loads((ROOT / 'tests/fixtures/gxw_fbd_source_library.json').read_text())
    archive = base64.b64decode(fixture['archive_base64'])
    original = base64.b64decode(fixture['source_base64'])
    result = decode_library_archive(archive + b'opaque tail')
    assert result.decoded == original
    assert result.trailing_bytes == b'opaque tail'
    framed = parse_library_source(original)
    assert b''.join(region['raw'] for region in framed['regions']) == original
    rows = parse_library_declarations(framed['definitions'][0])
    assert not rows['gaps']
    assert [(row['name'], row['source_class'], row['data_type_raw']) for row in rows['rows']] == [
        ('SIGNAL', 'VAR_INPUT', 'BOOL'), ('RESULT', 'VAR_OUTPUT', 'BOOL'), ('STATE', 'VAR_IN_OUT', 'INT')]
    assert all(row['annotation_raw'] and row['raw'] == original[row['offset']:row['end']] for row in rows['rows'])
    with pytest.raises(ValueError, match='bound'):
        decode_library_archive(archive, maximum_output=len(original) - 1)
    with pytest.raises(ValueError, match='truncated'):
        decode_library_archive(archive[:-5])


@pytest.mark.parametrize("case", list(NATIVE))
@pytest.mark.parametrize("logical", ["1.Labels.lh", "Global1.gh"])
def test_native_tables_are_lossless(case, logical):
    doc = document(case, logical)
    assert serialize_declarations(doc) == doc.raw


def test_all_sixteen_native_type_records_can_be_written_from_empty_table():
    native = document()
    edits = [{"name": r.name, "data_type": r.data_type, "comment": r.comment,
              **({"kind": "function_block"} if r.type_code == 15 else {})} for r in native.rows]
    generated = edit_declarations(document("d0"), upserts=edits)
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in native.rows]
    assert generated.header == document("d0").header
    assert generated.trailer == document("d0").trailer


def test_constant_class_and_value_match_native_record():
    generated = edit_declarations(document(), upserts=[{
        "name": "item_1", "class_name": "VAR_CONSTANT", "initial_value": "TRUE"}])
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in document("dconst").rows]


@pytest.mark.parametrize("case", SIZED_STRINGS["cases"], ids=lambda c: c["name"])
def test_sized_string_edits_match_independently_saved_native_rows(case):
    empty = document("d0")
    header = bytearray(empty.header)
    struct.pack_into("<I", header, empty.count_offset, 1)
    source = parse_declarations(bytes(header) + bytes.fromhex(case["old_row_hex"]) + empty.trailer,
                                logical_name=empty.logical_name)
    changed = edit_declarations(source, upserts=[{"name": case["target"], "data_type": case["data_type"]}])
    assert serialize_label(changed.rows[0]) == bytes.fromhex(case["native_row_hex"])
    assert changed.header == source.header
    assert changed.trailer == source.trailer
    assert serialize_declarations(source) == source.raw


def test_native_global_variables_arrays_function_blocks_and_constant():
    native = document("dglobal", "Global1.gh")
    edits = [{"name": r.name, "data_type": r.data_type, "comment": r.comment,
              **({"kind": "function_block"} if r.type_code == 15 else {})} for r in native.rows[3:-1]]
    edits.append({"name": "global_constant", "data_type": "INT", "class_name": "VAR_GLOBAL_CONSTANT",
                  "initial_value": "42", "comment": "native constant"})
    generated = edit_declarations(document("d0", "Global1.gh"), upserts=edits)
    assert [serialize_label(r) for r in generated.rows] == [r.raw for r in native.rows]


def test_unknown_type_and_opaque_fields_survive_comment_and_name_edits():
    source = document()
    odd = replace(source.rows[0], data_type="VENDOR TYPE?", type_code=991, array_marker=7,
                  unknown_u32=345, unknown_text="opaque", type_reference="vendor", record_id=88)
    source = replace(source, rows=(odd,), trailer=b"opaque trailing bytes")
    edited = edit_declarations(source, upserts=[{"name": odd.name, "comment": "new"}],
                               renames={})
    assert edited.rows[0] == replace(odd, comment="new")
    assert edited.trailer == source.trailer
    renamed = edit_declarations(edited, renames={odd.name: "renamed"})
    assert renamed.rows[0] == replace(odd, comment="new", name="renamed")


@pytest.mark.parametrize("case", STRUCTURES, ids=lambda c: c["id"])
def test_structure_extensions_keep_following_rows_aligned_and_remain_read_only(case):
    base = document("dglobal", "Global1.gh")
    header = bytearray(base.header)
    struct.pack_into("<I", header, base.count_offset, 2)
    raw = bytes(header) + bytes.fromhex(case["row_hex"]) + base.rows[-1].raw + base.trailer
    source = parse_declarations(raw, logical_name=base.logical_name)
    assert serialize_declarations(source) == raw
    first, following = source.rows
    assert first.value_extension and following.raw == base.rows[-1].raw
    native = case["native_row"]
    for field in ("record_id", "name", "data_type", "class_code", "initial_value", "comment"):
        assert getattr(first, field) == native[field]
    # The scalar device fields do not silently become a complete description
    # of the native structure/array address list.
    assert (first.device, first.iec_address) == (("", "") if first.unknown_u32 == 1 else ("D100", "D101"))
    for changes in ({"remove": [first.name]}, {"renames": {first.name: "renamed"}},
                    {"upserts": [{"name": first.name, "comment": "new"}]}):
        with pytest.raises(GXWFormatError, match="read-only"):
            edit_declarations(source, **changes)
    other = edit_declarations(source, upserts=[{"name": following.name, "comment": "preserve structure"}])
    assert serialize_label(other.rows[0]) == first.raw
    if first.unknown_u32 == 2:
        # The independent native negative control fails OpenProjectEX2 after
        # treating this zero DWORD as a counted third address string.
        from src.gxw.declarations import _string
        extension = raw.index(first.value_extension)
        zero_word = extension + len(_string("D102")) + len(_string("D103"))
        malformed = raw[:zero_word] + _string("D106") + raw[zero_word+4:]
        with pytest.raises(GXWFormatError, match="unobserved structure array"):
            parse_declarations(malformed, logical_name=source.logical_name)


def test_function_header_return_type_and_description_move_the_label_rows():
    from src.gxw.declarations import _string

    base = document("d0")
    owner_bytes = _string(base.owner_name)
    owner_end = 54 + len(owner_bytes)
    # Generated boundary control, not an independent native acceptance claim.
    description = _string("Function description")
    raw = base.raw[:6] + description + base.raw[12:owner_end+14] + _string("BOOL") + base.raw[owner_end+20:]
    parsed = parse_declarations(raw, logical_name="Example.Labels\\Library.lnl")
    assert parsed.owner_name == base.owner_name and parsed.owner_return_type == "BOOL"
    assert parsed.count_offset == base.count_offset + len(description)-6 + 8
    assert serialize_declarations(parsed) == raw


@pytest.mark.parametrize('kind,status', [(0x1000000,0),(0x1000001,1),(0x1000002,1),(0x1000003,1),(0xabcdef,0x76543210)])
def test_owner_kind_and_status_remain_lossless_during_row_edits(kind,status):
    from src.gxw.declarations import _string
    base = document('d0')
    owner_end = 54 + len(_string(base.owner_name))
    raw = base.raw[:owner_end] + struct.pack('<II',kind,status) + base.raw[owner_end+8:]
    parsed = parse_declarations(raw,logical_name=base.logical_name)
    assert (parsed.owner_pou_type,parsed.owner_pou_status)==(kind,status)
    edited = edit_declarations(parsed,upserts=[{'name':'work','data_type':'WORD'}])
    assert edited.header == parsed.header
    assert serialize_declarations(parsed)==raw
    if kind not in (0x1000002,0x1000003):
        with pytest.raises(GXWFormatError,match='function or function-block'):
            edit_declarations(parsed,upserts=[{'name':'signal','data_type':'BOOL','class_name':'VAR_INPUT'}])
    else:
        formals = edit_declarations(parsed,upserts=[{'name':'signal','data_type':'BOOL','class_name':'VAR_INPUT'}])
        reread = parse_declarations(serialize_declarations(formals),logical_name=parsed.logical_name)
        assert next(row for row in reread.rows if row.name == 'signal').class_code == 3
        assert reread.header[:reread.count_offset] == parsed.header[:parsed.count_offset]


def test_structure_source_member_boundaries_are_not_ordinary_label_rows():
    from src.gxw.declarations import _string

    base = document("d0", "Global1.gh")
    member = b"".join((_string("flag"), _string("BOOL"), _string("FALSE"), _string(""),
                       struct.pack("<I", 7), _string("Generated member"), struct.pack("<II", 0, 1), _string("")))
    raw = base.raw[:54] + struct.pack("<I", 1) + member + b"opaque"
    parsed = parse_structure_declarations(raw, logical_name="Example\\Library.lns")
    assert parsed.members[0].name == "flag" and parsed.members[0].record_id == 7
    assert parsed.header + parsed.members[0].raw + parsed.trailer == parsed.reconstruct() == raw
    with pytest.raises(GXWFormatError):
        parse_structure_declarations(raw[:54] + struct.pack("<I", 0xffffffff) + raw[58:], logical_name=parsed.logical_name)


@pytest.mark.parametrize("edits", [
    {"renames": {"item_1": "item_2"}}, {"renames": {"absent": "new"}},
    {"upserts": [{"name": "bad name", "data_type": "BOOL"}]},
    {"upserts": [{"name": "new", "data_type": "NotAKnownScalar"}]},
    {"upserts": [{"name": "new", "data_type": "ARRAY [3..0] OF BOOL"}]},
    {"upserts": [{"name": "new", "data_type": "BOOL", "mystery": 1}]},
] + [{"upserts": [{"name": "new", "data_type": dtype}]} for dtype in (
    "STRING[0]", "STRING[256]", "STRING[-1]", "string[20]", "WSTRING[20]",
    "STRING[+20]", "ARRAY [0..1] OF STRING[256]",
)])
def test_invalid_edits_fail_without_mutating_source(edits):
    source = document()
    with pytest.raises(GXWFormatError):
        edit_declarations(source, **edits)
    assert serialize_declarations(source) == source.raw


def test_program_and_missing_fb_declarations_written_together():
    raw = baseline("b0")
    resolver = GXWProjectResolver(CompoundFile(raw))
    program = parse_structured_pou(resolver.read_logical_file("1.Program.pou"), logical_name="1.Program.pou")
    program = replace_node_symbol(program, "timer_a", "TON_LONG_INSTANCE_A")
    program = replace_node_symbol(program, "timer_b", "TON_LONG_INSTANCE_B")
    result = build_gxw_project(raw, program, sync_fb_declarations=True)
    after = GXWProjectResolver(CompoundFile(result.data))
    labels = parse_declarations(after.read_logical_file("1.Labels.lh"), logical_name="1.Labels.lh")
    assert [(r.name, r.type_reference) for r in labels.rows[-2:]] == [
        ("TON_LONG_INSTANCE_A", "TON"), ("TON_LONG_INSTANCE_B", "TON")]
    assert after.read_logical_file("Global1.gh") == resolver.read_logical_file("Global1.gh")
    assert {c["object"] for c in result.report["objects"]} == {"1.Program.pou", "1.Labels.lh"}
    assert build_gxw_project(result.data,
        parse_structured_pou(after.read_logical_file("1.Program.pou"), logical_name="1.Program.pou"),
        sync_fb_declarations=True).data == result.data


def test_declaration_only_write_grows_both_nested_layers():
    raw = baseline("fn")
    resolver = GXWProjectResolver(CompoundFile(raw))
    source = parse_declarations(resolver.read_logical_file("1.Labels.lh"), logical_name="1.Labels.lh")
    edited = edit_declarations(source, upserts=[{"name": f"value_{i}", "data_type": "BOOL",
        "comment": "Native layout declaration"} for i in range(2000)])
    result = build_gxw_project(raw, declarations={source.logical_name: edited})
    after = GXWProjectResolver(CompoundFile(result.data))
    assert len(parse_declarations(after.read_logical_file(source.logical_name), logical_name=source.logical_name).rows) == 2000
    assert after.read_logical_file("1.Program.pou") == resolver.read_logical_file("1.Program.pou")
    assert len(result.data) > len(raw)
    assert {x["layer"] for x in result.report["allocations"]} == {"outer", "nested"}
    with pytest.raises(GXWFormatError, match="stale"):
        build_gxw_project(result.data, declarations={source.logical_name: edited})
