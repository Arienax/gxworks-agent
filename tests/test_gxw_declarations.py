"""Native CSV declaration oracles, preservation, and transactional project edits."""

from tools.offline_inputs import private_path
from dataclasses import replace
from functools import lru_cache
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
NATIVE = json.loads((private_path('tests/fixtures/gxw_declarations_20260910.json')).read_text())
SIZED_STRINGS = json.loads((private_path('tests/fixtures/gxw_sized_string_declarations.json')).read_text())
STRUCTURES = json.loads((private_path('tests/fixtures/gxw_structure_declarations_native.json')).read_text(encoding="utf-8"))["cases"]
CREATED_STRUCTURES = json.loads((private_path('tests/fixtures/gxw_created_structure_instance_native.json')).read_text(encoding="utf-8"))["cases"]
ARRAY_BOUNDS = json.loads((private_path('tests/fixtures/gxw_array_bound_declarations_native.json')).read_text(encoding="utf-8"))["cases"]
FLAT_STRUCTURES = json.loads((private_path('tests/fixtures/gxw_flat_project_structures_native.json')).read_text(encoding="utf-8"))["cases"]
ROOT_ARRAYS = json.loads((private_path('tests/fixtures/gxw_flat_project_structures_native.json')).read_text(encoding="utf-8"))["root_array_controls"]["cases"]
IMPORTED_LIBRARY = json.loads((private_path('tests/fixtures/gxw_imported_library_declarations_native.json')).read_text(encoding="utf-8"))["cases"]


def document(case="dtypes", logical="1.Labels.lh"):
    return parse_declarations(bytes.fromhex(NATIVE[case][logical]), logical_name=logical)


@lru_cache(maxsize=1)
def native_sfc_labels():
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.project_metadata import logical_mapping

    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-sfc-application-graph-20261013.zip')) as archive:
        outer = validate_cfb_streams(archive.read('retention-application/input.gxw'))
    nested = validate_cfb_streams(outer['_hdb'])
    return nested[logical_mapping(outer['projectdatalist.xml'])['Block.Labels.lh']]


@pytest.mark.parametrize('block_number', [0, 1, 15])
def test_sfc_block_number_precedes_properties_and_variable_count(block_number):
    raw = bytearray(native_sfc_labels())
    # Native SFCBlock2 derived metadata starts at 90 in this frozen owner.
    struct.pack_into('<I', raw, 90, block_number)
    document = parse_declarations(bytes(raw), logical_name='Block.Labels.lh', source_kind=240)
    assert document.owner_name == 'Block'
    assert document.owner_sfc_block_number == block_number
    assert document.count_offset == 130 and document.rows == ()
    assert document.trailer == bytes(raw[134:])
    assert serialize_declarations(document) == bytes(raw)


def test_sfc_declaration_edits_preserve_block_properties_and_unknown_tail():
    from hypothesis import given, settings, strategies as st

    original = native_sfc_labels()

    def counted(value):
        data = (value + '\0').encode('utf-16le')
        return struct.pack('<I', len(data) // 2) + data

    @settings(max_examples=24, derandomize=True, database=None, deadline=None)
    @given(block=st.integers(min_value=0, max_value=15),
           properties=st.lists(st.text(alphabet='MXY01234567_\u4e2d\u6587', max_size=12), min_size=6, max_size=6))
    def check(block, properties):
        derived = struct.pack('<I', block) + b''.join(counted(value) for value in properties)
        raw = original[:90] + derived + struct.pack('<I', 0) + original[134:]
        parsed = parse_declarations(raw, logical_name='Block.Labels.lh', source_kind=240)
        assert parsed.rows == () and parsed.owner_sfc_block_number == block
        edited = edit_declarations(parsed, upserts=[{'name': 'Enabled', 'data_type': 'BOOL'}])
        saved = serialize_declarations(edited)
        assert saved[90:90 + len(derived)] == derived
        reread = parse_declarations(saved, logical_name='Block.Labels.lh', source_kind=240)
        assert [(row.name, row.data_type) for row in reread.rows] == [('Enabled', 'BOOL')]
        assert reread.owner_sfc_block_number == block and reread.trailer == original[134:]
        assert serialize_declarations(reread) == saved

    check()


@pytest.mark.parametrize('end', [90, 93, 96, 128, 133])
def test_truncated_sfc_derived_declaration_metadata_is_not_a_variable_table(end):
    with pytest.raises(GXWFormatError):
        parse_declarations(native_sfc_labels()[:end], logical_name='Block.Labels.lh', source_kind=240)


def baseline(case):
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-20260910.zip')) as archive:
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
    fixture = json.loads((private_path('tests/fixtures/gxw_fbd_source_library.json')).read_text())
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


def array_bound_document(case, *following):
    header = bytearray.fromhex(case["header_hex"])
    struct.pack_into("<I", header, case["count_offset"], 1 + len(following))
    raw = bytes(header) + bytes.fromhex(case["old_row_hex"]) + b"".join(following) + bytes.fromhex(case["trailer_hex"])
    return parse_declarations(raw, logical_name=case["logical_name"])


@pytest.mark.parametrize("case", ARRAY_BOUNDS, ids=lambda c: c["name"])
def test_array_bound_edits_match_original_native_saved_rows(case):
    source = array_bound_document(case)
    edit = {"name": case["target"], "data_type": case["data_type"]}
    if case["name"] == "caller-length":
        edit["initial_value"] = case["initial_value"]
    changed = edit_declarations(source, upserts=[edit])
    saved = serialize_declarations(changed)
    reread = parse_declarations(saved, logical_name=source.logical_name)
    assert serialize_label(reread.rows[0]) == bytes.fromhex(case["native_row_hex"])
    assert changed.header == source.header and changed.trailer == source.trailer
    assert serialize_declarations(source) == source.raw


def test_generated_array_bound_edit_sequences_only_change_requested_bytes():
    from hypothesis import given, settings, strategies as st

    def replace_string(raw, index, value):
        # Independent counted UTF-16 splice over frozen native bytes. The
        # expected result does not call the declaration editor/serializer.
        start = 0
        for _ in range(index):
            start += 4 + 2 * struct.unpack_from("<I", raw, start)[0]
        end = start + 4 + 2 * struct.unpack_from("<I", raw, start)[0]
        encoded = (value + "\0").encode("utf-16le")
        return raw[:start] + struct.pack("<I", len(encoded) // 2) + encoded + raw[end:]

    @settings(max_examples=80, deadline=None, derandomize=True, database=None)
    @given(case=st.sampled_from(ARRAY_BOUNDS),
           lower_bounds=st.lists(st.tuples(st.integers(-40, 40), st.integers(-40, 40)), min_size=1, max_size=12),
           initial_override=st.booleans())
    def check(case, lower_bounds, initial_override):
        # This is a Core preservation contract, not a native acceptance claim
        # for the generated bounds. The untouched row is also native data.
        untouched = document().rows[-1].raw
        source = array_bound_document(case, untouched)
        current = source
        expected = bytes.fromhex(case["old_row_hex"])
        for lowers in lower_bounds:
            bounds = ",".join(f"{lower}..{lower + length - 1}" for lower, length in zip(lowers, case["dimension_lengths"]))
            data_type = f"ARRAY [{bounds}] OF BOOL"
            edit = {"name": case["target"], "data_type": data_type}
            expected = replace_string(expected, 1, data_type)
            if initial_override:
                edit["initial_value"] = "[2(TRUE)]"
                # Skip the class/device/address/opaque DWORD before the two
                # value strings; their sizes come from the frozen row.
                row_name_size = 4 + 2 * struct.unpack_from("<I", expected, 0)[0]
                type_end = row_name_size + 4 + 2 * struct.unpack_from("<I", expected, row_name_size)[0]
                value_start = type_end + 4
                for _ in range(2):
                    value_start += 4 + 2 * struct.unpack_from("<I", expected, value_start)[0]
                value_start += 4
                expected = expected[:value_start] + replace_string(expected[value_start:], 0, edit["initial_value"])
            current = edit_declarations(current, upserts=[edit])
            saved = serialize_declarations(current)
            assert saved == source.header + expected + untouched + source.trailer
            current = parse_declarations(saved, logical_name=source.logical_name)
        assert serialize_declarations(source) == source.raw

    check()


@pytest.mark.parametrize("old_type,new_type,kind,code,marker,initial", [
    ("ARRAY [0..1] OF BOOL", "BOOL", None, 1, 0, "FALSE"),
    ("ARRAY [0..1] OF BOOL", "ARRAY [0..1] OF INT", None, 2, 1, "[2(0)]"),
    ("ARRAY [0..1] OF BOOL", "ARRAY [0..1,0..1] OF BOOL", None, 1, 1, "[4(FALSE)]"),
    ("ARRAY [0..1,0..1] OF BOOL", "ARRAY [0..1] OF BOOL", None, 1, 1, "[2(FALSE)]"),
    ("ARRAY [0..1] OF STRING[20]", "ARRAY [0..1] OF STRING[21]", None, 8, 1, "[2('')]"),
    ("ARRAY [0..1] OF BOOL", "ARRAY [1..2] OF BOOL", "variable", 1, 1, "[2(FALSE)]"),
])
def test_array_type_transitions_rebuild_dependent_fields(old_type, new_type, kind, code, marker, initial):
    source = array_bound_document(ARRAY_BOUNDS[0])
    source = replace(source, rows=(replace(source.rows[0], data_type=old_type, initial_value=""),))
    edit = {"name": source.rows[0].name, "data_type": new_type}
    if kind is not None:
        edit["kind"] = kind
    row = edit_declarations(source, upserts=[edit]).rows[0]
    assert (row.type_code, row.array_marker, row.initial_value, row.type_reference) == (code, marker, initial, "")


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


@pytest.mark.parametrize("case", STRUCTURES + CREATED_STRUCTURES, ids=lambda c: c["id"])
def test_structure_extensions_keep_following_rows_aligned_during_bound_edits(case):
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
    for field in ("array_marker", "type_code", "type_reference"):
        if field in native:
            assert getattr(first, field) == native[field]
    # The scalar device fields do not silently become a complete description
    # of the native structure/array address list.
    assert (first.device, first.iec_address) == (("", "") if first.unknown_u32 == 1 else ("D100", "D101"))
    for changes in ({"renames": {first.name: "renamed"}},
                    {"upserts": [{"name": first.name, "comment": "new"}]}):
        edited = edit_declarations(source, **changes)
        reread = parse_declarations(serialize_declarations(edited), logical_name=source.logical_name)
        assert reread.rows[0].value_extension == first.value_extension
        assert reread.rows[0].offset == first.offset
        assert reread.rows[1].raw == following.raw
    removed = edit_declarations(source, remove=[first.name])
    assert [row.raw for row in removed.rows] == [following.raw]
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


@pytest.mark.parametrize("changes", [
    {"data_type": "WORD"}, {"data_type": "ARRAY [0..1] OF stRemoteRegBasic"},
    {"class_code": 9}, {"array_marker": 1}, {"type_code": 1}, {"type_reference": "unknown"},
])
def test_created_structure_cache_does_not_accept_unobserved_extension_shapes(changes):
    base = document("dglobal", "Global1.gh")
    header = bytearray(base.header)
    struct.pack_into("<I", header, base.count_offset, 2)
    raw = bytes(header) + bytes.fromhex(CREATED_STRUCTURES[0]["row_hex"]) + base.rows[-1].raw + base.trailer
    native = parse_declarations(raw, logical_name=base.logical_name)
    changed = replace(native.rows[0], **changes)
    with pytest.raises(GXWFormatError, match="address extension outside"):
        parse_declarations(bytes(header) + serialize_label(changed) + base.rows[-1].raw + base.trailer,
                           logical_name=base.logical_name)


def test_created_global_structure_cache_does_not_certify_a_local_declaration():
    base = document("d0")
    header = bytearray(base.header)
    struct.pack_into("<I", header, base.count_offset, 1)
    raw = bytes(header) + bytes.fromhex(CREATED_STRUCTURES[0]["row_hex"]) + base.trailer
    with pytest.raises(GXWFormatError, match="address extension outside"):
        parse_declarations(raw, logical_name=base.logical_name)


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


@pytest.mark.parametrize('case', FLAT_STRUCTURES, ids=lambda case: case['cpu'])
def test_native_created_project_members_follow_original_typed_getters_with_zero_caches(case):
    import base64
    source = case['structure']
    raw = base64.b64decode(source['raw_base64'])
    document = parse_structure_declarations(raw, logical_name=source['logical'])
    assert document.header + b''.join(member.raw for member in document.members) + document.trailer == raw
    assert document.reconstruct() == raw
    assert [(member.name, member.data_type, member.initial_value, member.record_id)
            for member in document.members] == [(row['name'], row['data_type'], row['initial_value'], row['line'])
                                                for row in case['native_members']]
    assert all((member.array_marker, member.type_code, member.type_reference) == (0, 0, '')
               for member in document.members)
    # Cold Build accepts these fields without populating the type/array caches.
    assert case['compile'] == 'completed_accepted' and case['check'] == 'incomplete'
    assert case['PLC_execution'] == 'not_requested'
    for name, value in case['declarations'].items():
        declaration = parse_declarations(base64.b64decode(value), logical_name=name)
        assert serialize_declarations(declaration) == declaration.raw


@pytest.mark.parametrize('case', ROOT_ARRAYS, ids=lambda case: case['name'])
def test_native_root_structure_array_parameters_keep_raw_caches_and_following_rows(case):
    import base64
    from src.gxw.declarations import _Reader
    source = parse_declarations(base64.b64decode(case['declarations'][case['root_table']]), logical_name=case['root_table'])
    root = next(row for row in source.rows if row.name == case['root_name'])
    native = case['native_parameters']
    assert root.data_type == native['actual_type'] and root.unknown_u32 == native['actual_mode'] == 2
    assert (root.array_marker, root.type_code, root.type_reference) == (0, 0, '')
    extension = _Reader(root.value_extension)
    assert [extension.string(), extension.string(), extension.uint(), extension.string(), extension.string()] == [
        native[key] for key in ('word_address', 'word_offset', 'word_use_bit', 'bit_address', 'bit_offset')]
    assert extension.offset == len(root.value_extension)
    assert serialize_declarations(source) == source.raw
    for changes in ({'renames': {root.name: 'renamed_array'}},
                    {'upserts': [{'name': root.name, 'comment': 'array edit'}]}):
        edited = edit_declarations(source, **changes)
        assert edited.rows[0].value_extension == source.rows[0].value_extension
        assert [row.raw for row in edited.rows[1:]] == [row.raw for row in source.rows[1:]]
    removed = edit_declarations(source, remove=[root.name])
    assert [row.raw for row in removed.rows] == [row.raw for row in source.rows if row.name != root.name]
    edited = edit_declarations(source, upserts=[{'name': 'ALARM_GLOBAL', 'comment': 'other label edit'}])
    reread = parse_declarations(serialize_declarations(edited), logical_name=source.logical_name)
    assert next(row for row in reread.rows if row.name == root.name).raw == root.raw
    assert len(reread.rows) == len(source.rows)


@pytest.mark.parametrize('case', ROOT_ARRAYS, ids=lambda case: case['name'])
def test_structure_array_address_edits_match_independent_native_fields(case):
    import base64
    from src.gxw.declarations import structure_address_fields
    source = parse_declarations(base64.b64decode(case['declarations'][case['root_table']]), logical_name=case['root_table'])
    root = next(row for row in source.rows if row.name == case['root_name'])
    expected = {key: case['native_parameters'][key] for key in (
        'word_address', 'word_offset', 'word_use_bit', 'bit_address', 'bit_offset')}
    changed = edit_declarations(source, upserts=[dict(name=root.name, structure_array_addresses=dict(
        word_address='D1000', word_offset='1', word_use_bit=1, bit_address='M1000', bit_offset='1'))])
    restored = edit_declarations(changed, upserts=[dict(name=root.name, structure_array_addresses=expected)])
    assert structure_address_fields(restored.rows[next(i for i, row in enumerate(source.rows) if row.name == root.name)]) == expected
    assert serialize_declarations(restored) == source.raw
    assert [(row.raw, row.offset) for row in changed.rows] == [(row.raw, row.offset) for row in source.rows]
    for fields in ({'word_use_bit': 2}, {'word_offset': '-1'}, {'extra': ''}):
        with pytest.raises(GXWFormatError):
            edit_declarations(source, upserts=[dict(name=root.name, structure_array_addresses=fields)])


def test_scalar_structure_member_addresses_match_independent_native_pairs():
    from src.gxw.declarations import _string, structure_address_fields
    case = CREATED_STRUCTURES[0]
    base = document('dglobal', 'Global1.gh')
    raw = bytes.fromhex(case['row_hex'])
    source = replace(base, rows=(parse_declarations(
        base.header[:base.count_offset] + struct.pack('<I', 1) + raw + base.trailer,
        logical_name=base.logical_name).rows[0],))
    root = source.rows[0]
    # These positional names model a caller-supplied bound structure layout;
    # values come independently from native Set/GetStructData1880 controls.
    names = ('member1', 'member2', 'member3', 'member4')
    expected = {name: dict(zip(('device', 'iec_address'), pair, strict=True)) for name, pair in
                zip(names, case['original_context']['original_device_pairs'], strict=True)}
    assert structure_address_fields(root, members=names) == expected
    edited = edit_declarations(source, upserts=[dict(name=root.name,
        member_devices={'member2': dict(device='D700', iec_address='%MW0.700')})],
        structure_members={root.data_type.casefold(): names})
    assert structure_address_fields(edited.rows[0], members=names)['member2'] == dict(device='D700', iec_address='%MW0.700')
    assert edited.rows[0].value_extension.replace(_string('D700') + _string('%MW0.700'),
        _string(expected['member2']['device']) + _string(expected['member2']['iec_address'])) == root.value_extension
    restored = edit_declarations(edited, upserts=[dict(name=root.name, member_devices=expected)],
        structure_members={root.data_type.casefold(): names})
    assert serialize_label(restored.rows[0]) == raw
    with pytest.raises(GXWFormatError, match='member'):
        edit_declarations(source, upserts=[dict(name=root.name, member_devices={'foreign': 'D0'})],
            structure_members={root.data_type.casefold(): names})


def test_declaration_order_matches_native_swapped_formals_without_rebinding_row_identity():
    import base64
    from src.gxw.callable_sources import interface
    cases = json.loads(private_path('tests/fixtures/gxw_formal_order_native.json').read_text(encoding='utf-8'))['cases']
    original, swapped = (next(case for case in cases if case['name'] == name) for name in ('ordered', 'swapped'))
    source = parse_declarations(base64.b64decode(original['declarations_base64']), logical_name=original['logical_name'])
    expected = parse_declarations(base64.b64decode(swapped['declarations_base64']), logical_name=swapped['logical_name'])
    order = [row.name for row in sorted(expected.rows, key=lambda row: row.record_id)]
    edited = edit_declarations(source, order=order)
    assert {row.name: serialize_label(row) for row in edited.rows} == {row.name: row.raw for row in expected.rows}
    old_by_name = {row.name: row for row in source.rows}
    assert all((row.raw, row.offset) == (old_by_name[row.name].raw, old_by_name[row.name].offset) for row in edited.rows)
    _, outputs = interface(dict(name=source.owner_name, source_kind='project_labels', declarations=edited, return_type_raw=None))
    assert [row['name'] for row in outputs] == swapped['outputs']
    renamed = edit_declarations(source, renames={'Z_First_Output': 'RenamedOutput'},
        order=[name if name != 'Z_First_Output' else 'RenamedOutput' for name in order])
    row = next(row for row in renamed.rows if row.name == 'RenamedOutput')
    assert (row.offset, row.raw) == (old_by_name['Z_First_Output'].offset, old_by_name['Z_First_Output'].raw)


def test_declaration_rename_reorder_sequences_keep_native_rows_bound_and_undo_exactly():
    import base64
    from hypothesis import given, settings, strategies as st
    case = json.loads(private_path('tests/fixtures/gxw_formal_order_native.json').read_text(encoding='utf-8'))['cases'][0]
    source = parse_declarations(base64.b64decode(case['declarations_base64']), logical_name=case['logical_name'])
    names = tuple(row.name for row in source.rows)
    old = next(row for row in source.rows if row.name == 'Z_First_Output')

    @settings(max_examples=24, deadline=None, database=None, derandomize=True)
    @given(order=st.permutations(names), suffix=st.text(alphabet='AB012_', min_size=1, max_size=8),
           comment=st.text(alphabet='ABC中文😀\r\n', max_size=40))
    def check(order, suffix, comment):
        name = 'Renamed_' + suffix
        selected = [name if value == old.name else value for value in order]
        edited = edit_declarations(source, renames={old.name: name}, order=selected,
            upserts=[dict(name=name, comment=comment)])
        before = {row.name: row for row in source.rows}
        assert [(row.name, row.raw, row.offset) for row in edited.rows] == [
            (value, before[old.name if value == name else value].raw,
             before[old.name if value == name else value].offset) for value in selected]
        reread = parse_declarations(serialize_declarations(edited), logical_name=source.logical_name)
        assert [row.name for row in sorted(reread.rows, key=lambda row: row.record_id)] == selected
        assert next(row for row in reread.rows if row.name == name).comment == comment
        restored = edit_declarations(edited, renames={name: old.name}, order=names,
            upserts=[dict(name=old.name, comment=old.comment)])
        assert serialize_declarations(restored) == source.raw
    check()


def test_simultaneous_declaration_renames_bind_original_rows_before_namespace_changes():
    source = document()
    first, second = source.rows[:2]
    edited = edit_declarations(source, renames={first.name: second.name, second.name: first.name})
    assert edited.rows[:2] == (replace(first, name=second.name), replace(second, name=first.name))
    replaced = edit_declarations(source, renames={first.name: second.name}, remove=[second.name])
    assert replaced.rows[0] == replace(first, name=second.name)
    assert all(row.raw != second.raw for row in replaced.rows)
    with pytest.raises(GXWFormatError, match='conflicting'):
        edit_declarations(source, renames={first.name: 'Other'}, remove=[first.name])


@pytest.mark.parametrize('changes', [
    {'data_type': 'WORD'}, {'data_type': 'ARRAY[0..1] OF BOOL'}, {'data_type': 'UnknownStruct'},
    {'data_type': 'ARRAY[0..1,0..1] OF UnknownStruct'}, {'data_type': 'ARRAY[2..1] OF UnknownStruct'},
    {'data_type': 'ARRAY[-2147483649..0] OF UnknownStruct'},
    {'data_type': 'ARRAY[0..2147483648] OF UnknownStruct'},
    {'data_type': 'ARRAY[0..' + '9' * 5000 + '] OF UnknownStruct'},
    {'class_code': 9}, {'array_marker': 1}, {'type_code': 1}, {'type_reference': 'unverified'},
])
def test_root_structure_array_zero_cache_does_not_accept_unobserved_source_shapes(changes):
    import base64
    case = ROOT_ARRAYS[0]
    source = parse_declarations(base64.b64decode(case['declarations'][case['root_table']]), logical_name=case['root_table'])
    changed = replace(source, rows=tuple(replace(row, **changes) if row.name == case['root_name'] else row for row in source.rows))
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(serialize_declarations(changed), logical_name=source.logical_name)
    assert serialize_declarations(source) == source.raw


@pytest.mark.parametrize('flag', [2, 0xffffffff])
def test_root_structure_array_rejects_unknown_word_bit_flag_without_shifting_rows(flag):
    import base64
    from src.gxw.declarations import _string
    case = ROOT_ARRAYS[0]
    source = parse_declarations(base64.b64decode(case['declarations'][case['root_table']]), logical_name=case['root_table'])
    root = next(row for row in source.rows if row.name == case['root_name'])
    native = case['native_parameters']
    flag_offset = len(_string(native['word_address'])) + len(_string(native['word_offset']))
    extension = root.value_extension[:flag_offset] + struct.pack('<I', flag) + root.value_extension[flag_offset+4:]
    changed = replace(source, rows=tuple(replace(row, value_extension=extension) if row.name == root.name else row for row in source.rows))
    with pytest.raises(GXWFormatError, match='unobserved structure array address variant'):
        parse_declarations(serialize_declarations(changed), logical_name=source.logical_name)


def test_created_root_structure_array_does_not_certify_local_declaration_storage():
    import base64
    case = ROOT_ARRAYS[0]
    global_ = parse_declarations(base64.b64decode(case['declarations'][case['root_table']]), logical_name=case['root_table'])
    root = next(row for row in global_.rows if row.name == case['root_name'])
    local = document('d0')
    header = bytearray(local.header)
    struct.pack_into('<I', header, local.count_offset, 1)
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(bytes(header) + root.raw + local.trailer, logical_name=local.logical_name)


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


def imported_library_document():
    case = IMPORTED_LIBRARY['imported']
    return parse_declarations(bytes.fromhex(case['raw_hex']), logical_name=case['logical_name'])


def test_original_retain_class_rows_follow_the_native_formatter_and_keep_bound_bytes():
    import base64
    from src.gxw.declarations import CLASS_CODES
    fixture = json.loads(private_path('tests/fixtures/gxw_retain_declarations_native.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(private_path(fixture['archive'])) as archive:
        raw = archive.read(fixture['table_member'])
    document = parse_declarations(raw, logical_name=fixture['table'])
    assert CLASS_CODES['VAR_RETAIN'] == fixture['native_static']['value'] == 10
    actual = {row.name: row for row in document.rows}
    for expected in fixture['rows']:
        row = actual[expected['name']]
        assert row.class_code == 10 and row.data_type == expected['data_type']
        assert serialize_label(row) == base64.b64decode(expected['raw_base64'])
    changed = edit_declarations(document, renames={'iCounter': 'RetainedCounter'}, upserts=[
        dict(name='RetainedCounter', class_name='VAR_RETAIN', initial_value='17', comment='保留变量存储编辑'),
        dict(name='NewRetained', class_name='VAR_RETAIN', data_type='INT', initial_value='3')])
    saved = serialize_declarations(changed)
    after = parse_declarations(saved, logical_name=fixture['table'])
    by_name = {row.name: row for row in after.rows}
    assert (by_name['RetainedCounter'].class_code, by_name['RetainedCounter'].initial_value) == (10, '17')
    assert (by_name['NewRetained'].class_code, by_name['NewRetained'].data_type) == (10, 'INT')
    assert next(row for row in changed.rows if row.name == 'RetainedCounter').raw == actual['iCounter'].raw
    assert next(row for row in changed.rows if row.name == 'RetainedCounter').offset == actual['iCounter'].offset
    assert all(serialize_label(row) == serialize_label(actual[row.name])
               for row in after.rows if row.name in actual and row.name != 'iCounter')


def created_project_inout_document():
    case = json.loads((private_path('tests/fixtures/gxw_created_project_inout_native.json')).read_text(encoding='utf-8'))
    saved = case['created']
    return parse_declarations(bytes.fromhex(saved['raw_hex']), logical_name=saved['logical_name'])


def test_native_created_project_inout_matches_original_getters_and_source_interface():
    case = json.loads((private_path('tests/fixtures/gxw_created_project_inout_native.json')).read_text(encoding='utf-8'))
    source = case['source']
    before = parse_declarations(bytes.fromhex(source['raw_hex']), logical_name=source['logical_name'])
    after = created_project_inout_document()
    assert serialize_declarations(after) == bytes.fromhex(case['created']['raw_hex'])
    assert after.owner_name == before.owner_name == 'CCIEFBasicStart'
    assert after.owner_pou_type == before.owner_pou_type == 0x1000002
    assert len(after.rows) == len(before.rows) == len(case['native_getters']) == 9
    saved_rows = {row.name: row for row in after.rows}
    getters = {row['name']: row for row in case['native_getters']}
    assert list(saved_rows) == list(getters)
    for old in before.rows:
        new, native = saved_rows[old.name], getters[old.name]
        for field in ('name', 'data_type', 'class_code', 'initial_value', 'comment'):
            assert getattr(old, field) == getattr(new, field) == native[field]
        assert old.record_id == new.record_id == native['line']
        assert new.unknown_u32 == native['struct_mode']
    axis = after.rows[0]
    assert (axis.type_code, axis.type_reference, axis.class_code) == (0, '', 5)
    assert (before.rows[0].type_code, before.rows[0].type_reference) == (9, 'AXIS_REF_JEC')
    assert axis.value_extension == before.rows[0].value_extension
    assert case['native_getters'][0]['devices'] == [['', '']] * 5
    renamed = edit_declarations(after, renames={'Axis': 'OtherAxis'})
    assert renamed.rows[0] == replace(after.rows[0], name='OtherAxis')
    assert renamed.rows[0].raw == after.rows[0].raw


@pytest.mark.parametrize('changes', [
    {'data_type': 'BOOL'}, {'data_type': 'ARRAY [0..1] OF AXIS_REF_JEC'},
    {'class_code': 1}, {'class_code': 8},
    {'array_marker': 1}, {'type_code': 100}, {'type_reference': 'AXIS_REF_JEC'},
])
def test_created_project_inout_cache_does_not_certify_other_shapes(changes):
    source = created_project_inout_document()
    altered = replace(source, rows=(replace(source.rows[0], **changes), *source.rows[1:]))
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(serialize_declarations(altered), logical_name=source.logical_name)


def test_created_project_inout_cache_requires_project_fb_ownership():
    source = created_project_inout_document()
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(source.raw, logical_name='CCIEFBasicStart.Variables.lnl')
    from src.gxw.declarations import _Reader
    from src.gxw.source_header import source_payload_offset
    reader = _Reader(source.raw, source_payload_offset(source.raw))
    reader.string()
    raw = bytearray(source.raw)
    struct.pack_into('<I', raw, reader.offset, 0x1000000)
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(bytes(raw), logical_name=source.logical_name)


def test_native_imported_library_inout_retains_address_extension_and_following_rows():
    case = IMPORTED_LIBRARY['source']
    before = parse_declarations(bytes.fromhex(case['raw_hex']), logical_name=case['logical_name'])
    after = imported_library_document()
    assert serialize_declarations(after) == bytes.fromhex(IMPORTED_LIBRARY['imported']['raw_hex'])
    assert after.owner_name == before.owner_name == 'MC_Power_JEC'
    assert len(after.rows) == len(before.rows) == 22
    old, new = before.rows[0], after.rows[0]
    assert (old.type_code, old.type_reference) == (9, 'AXIS_REF_JEC')
    assert (new.type_code, new.type_reference) == (100, '')
    assert (new.name, new.class_code, new.data_type) == ('Axis', 5, 'AXIS_REF_JEC')
    assert new.value_extension == old.value_extension and len(new.value_extension) == 64
    # Native library import also reorders private variables by their saved
    # record number. Compare each retained row by its declared name.
    assert {row.name: row.raw for row in after.rows[1:]} == {row.name: row.raw for row in before.rows[1:]}
    renamed = edit_declarations(after, renames={'Axis': 'OtherAxis'})
    assert renamed.rows[0] == replace(new, name='OtherAxis')
    assert [row.raw for row in renamed.rows[1:]] == [row.raw for row in after.rows[1:]]


@pytest.mark.parametrize('changes', [
    {'data_type': 'WORD'}, {'data_type': 'ARRAY [0..1] OF AXIS_REF_JEC'},
    {'class_code': 8}, {'array_marker': 1},
    {'type_code': 101}, {'type_reference': 'AXIS_REF_JEC'},
])
def test_imported_library_cache_does_not_certify_other_storage_shapes(changes):
    source = imported_library_document()
    altered = replace(source, rows=(replace(source.rows[0], **changes), *source.rows[1:]))
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(serialize_declarations(altered), logical_name=source.logical_name)


def test_imported_library_cache_does_not_certify_project_local_storage():
    source = imported_library_document()
    with pytest.raises(GXWFormatError, match='address extension outside'):
        parse_declarations(source.raw, logical_name='MC_Power_JEC.Variables.lh')


@pytest.mark.parametrize('case', json.loads(private_path('tests/fixtures/gxw_structure_class_edits_native.json').read_text(encoding='utf-8'))['cases'],
                         ids=lambda case: case['id'])
def test_existing_structure_formal_direction_edit_matches_native_saved_zero_and_library_caches(case):
    import base64
    before = base64.b64decode(case['before_raw_base64'])
    independently_saved = base64.b64decode(case['after_raw_base64'])
    source = parse_declarations(before, logical_name=case['before_table'])
    native = parse_declarations(independently_saved, logical_name=case['after_table'])
    changed = edit_declarations(source, upserts=[dict(name='Axis', class_name='VAR_INPUT')])
    assert serialize_declarations(changed) == independently_saved
    old = next(row for row in source.rows if row.name == 'Axis')
    current = next(row for row in changed.rows if row.name == 'Axis')
    saved = next(row for row in native.rows if row.name == 'Axis')
    assert (old.class_code, current.class_code, saved.class_code) == (5, 3, 3)
    assert old.type_code == current.type_code == saved.type_code == case['type_cache']
    assert old.value_extension == current.value_extension == saved.value_extension
    assert (current.raw, current.offset) == (old.raw, old.offset)
    assert serialize_declarations(edit_declarations(changed, upserts=[dict(name='Axis', class_name='VAR_IN_OUT')])) == before


@pytest.mark.parametrize('load_document', [imported_library_document, created_project_inout_document])
def test_imported_library_cache_preserves_other_rows_during_scalar_edits(load_document):
    from hypothesis import given, settings, strategies as st

    source = load_document()

    @settings(max_examples=24, derandomize=True, database=None, deadline=None)
    @given(comment=st.text(alphabet='ABC xyz012\u4e2d\u6587\U0001f527', max_size=48),
           suffix=st.text(alphabet='AB012_', min_size=1, max_size=10))
    def check(comment, suffix):
        name = 'Enable_' + suffix
        renamed = edit_declarations(source, renames={'Enable': name})
        renamed = parse_declarations(serialize_declarations(renamed), logical_name=source.logical_name)
        edited = edit_declarations(renamed, upserts=[{'name': name, 'comment': comment}])
        reread = parse_declarations(serialize_declarations(edited), logical_name=source.logical_name)
        assert reread.rows[1].name == name and reread.rows[1].comment == comment
        assert reread.rows[0].raw == source.rows[0].raw
        assert [row.raw for row in reread.rows[2:]] == [row.raw for row in source.rows[2:]]
        assert reread.trailer == source.trailer

    check()
