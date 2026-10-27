
from tools.offline_inputs import private_path
import base64
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest
from hypothesis import given, strategies as st

from src.gxw.structured_pou import parse_structured_pou
from src.gxw.structured_pou_writer import (
    insert_series_contact_after,
    replace_node_symbol,
    serialize_structured_node,
    serialize_structured_pou,
    serialize_structured_wire,
)


FIXTURES = private_path('tests/fixtures')


def _fixture_program(filename: str, sample: str) -> bytes:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    return base64.b64decode(payload[sample]["program_pou_base64"])


@pytest.mark.parametrize(
    ("filename", "sample"),
    [
        ("gxw_structured_52.json", "52"),
        ("gxw_structured_54_58.json", "54"),
        ("gxw_structured_54_58.json", "56"),
        ("gxw_structured_54_58.json", "57"),
        ("gxw_structured_54_58.json", "58"),
        ("gxw_structured_67_71.json", "67"),
        ("gxw_structured_67_71.json", "71"),
        ("gxw_structured_72_75.json", "72"),
        ("gxw_structured_72_75.json", "74"),
        ("gxw_structured_72_75.json", "75"),
        ("gxw_structured_76.json", "76"),
        ("gxw_structured_77.json", "77"),
    ],
)
def test_structured_program_serializer_is_byte_perfect(filename, sample):
    raw = _fixture_program(filename, sample)
    program = parse_structured_pou(raw, logical_name="1.Program.pou")

    assert serialize_structured_pou(program) == raw


def _visible_native_fbd_source():
    witness = json.loads((FIXTURES / 'gxw_visible_ide_native.json').read_text(encoding='utf-8'))
    return base64.b64decode(witness['program_pou_base64']), witness['expected']


def test_visible_native_fbd_retains_observed_nonzero_trailer():
    raw, expected = _visible_native_fbd_source()
    program = parse_structured_pou(raw)
    assert [node.symbol for node in program.nodes] == expected['node_symbols']
    assert len(program.wires) == expected['wire_count']
    assert program.trailer == struct.pack('<I', expected['trailer_prefix']) + bytes(20)
    assert serialize_structured_pou(program) == raw


@given(st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789', min_size=1, max_size=40))
def test_visible_native_symbol_edits_preserve_opaque_trailer_and_other_nodes(symbol):
    raw, _ = _visible_native_fbd_source()
    program = parse_structured_pou(raw)
    target = next(node for node in program.nodes if node.symbol == 'InputValue')
    modified = replace_node_symbol(program, 'InputValue', symbol, node_offset=target.offset)
    rebuilt = serialize_structured_pou(modified)
    reopened = parse_structured_pou(rebuilt)
    assert reopened.trailer == raw[-24:]
    assert [node.symbol for node in reopened.nodes] == [
        symbol if node.offset == target.offset else node.symbol for node in program.nodes]
    assert serialize_structured_pou(reopened) == rebuilt


@given(st.binary(min_size=24, max_size=24).filter(lambda value: value not in (
    bytes(24), struct.pack('<I', 2) + bytes(20))))
def test_unobserved_structured_trailer_values_remain_rejected(trailer):
    from src.gxw.models import GXWFormatError
    raw, _ = _visible_native_fbd_source()
    with pytest.raises(GXWFormatError, match='trailer'):
        parse_structured_pou(raw[:-24] + trailer)


@pytest.mark.parametrize('index', range(615))
def test_original_fbd_corpus_unmodified_source_is_byte_identical(index):
    from tests.test_gxw_structured_reader import offline_fbd_source
    raw = offline_fbd_source(index)
    program = parse_structured_pou(raw, preserve_unsupported_records=True)
    assert serialize_structured_pou(program) == raw


def test_opaque_fixed_trailer_is_preserved_but_cannot_be_edited():
    from src.gxw.models import GXWFormatError
    from tests.test_gxw_structured_reader import offline_fbd_source
    raw = offline_fbd_source(41)
    program = parse_structured_pou(raw, preserve_unsupported_records=True)
    assert program.trailer[-4:] == b'\1\0\0\0'
    assert serialize_structured_pou(program) == raw
    with pytest.raises(GXWFormatError, match='trailer is immutable'):
        serialize_structured_pou(replace(program, trailer=bytes(24)))


def test_unknown_suffix_remains_inspectable_without_authorizing_body_changes():
    from src.gxw.models import GXWFormatError
    raw, _ = _visible_native_fbd_source()
    raw = raw[:-24] + struct.pack('<6I',0,0,0x12345678,0,0,0)
    program = parse_structured_pou(raw,preserve_unsupported_records=True)
    assert serialize_structured_pou(program) == raw
    with pytest.raises(GXWFormatError,match='exact no-op only'):
        serialize_structured_pou(replace_node_symbol(program,program.nodes[0].symbol,'CHANGED',node_offset=program.nodes[0].offset))


def test_unpaired_utf16_input_is_rejected_as_a_format_error():
    from src.gxw.models import GXWFormatError
    program=parse_structured_pou(_fixture_program('gxw_structured_52.json','52'))
    with pytest.raises(GXWFormatError,match='UTF-16'):
        serialize_structured_pou(replace_node_symbol(program,'X1','bad\ud800'))


def test_unchanged_nonterminated_node_string_retains_its_original_bytes_during_geometry_edits():
    from src.gxw.source_header import source_payload_offset
    # Alter only framing in the independently captured sample: a counted
    # string without NUL is already reader-supported and must remain exact.
    raw = bytearray(_fixture_program('gxw_structured_52.json', '52'))
    source = parse_structured_pou(bytes(raw))
    target = source.nodes[0]
    count = struct.unpack_from('<I', raw, target.offset + 12)[0]
    end = target.offset + 16 + count * 2
    del raw[end - 2:end]
    struct.pack_into('<I', raw, target.offset + 12, count - 1)
    struct.pack_into('<I', raw, target.offset, target.record_length - 2)
    struct.pack_into('<I', raw, source.blocks[0].offset, source.blocks[0].byte_length - 2)
    payload = source_payload_offset(raw)
    for offset in (payload + 1, payload + 5):
        struct.pack_into('<I', raw, offset, struct.unpack_from('<I', raw, offset)[0] - 2)
    parsed = parse_structured_pou(bytes(raw))
    assert serialize_structured_pou(parsed) == bytes(raw)
    node = parsed.nodes[0]
    moved = replace(node, bbox=replace(node.bbox, top=node.bbox.top + 1, bottom=node.bbox.bottom + 1))
    encoded = serialize_structured_node(moved)
    # String count and spelling remain exact; only the two bbox ordinates move.
    assert encoded[:end - target.offset - 2] == node.raw[:end - target.offset - 2]
    reopened = parse_structured_pou(serialize_structured_pou(replace(parsed, nodes=(moved, *parsed.nodes[1:]))))
    assert reopened.nodes[0].symbol == node.symbol
    assert reopened.nodes[1].raw == parsed.nodes[1].raw


def test_variable_symbols_preserve_native_network_labels_comments_and_other_records():
    from hypothesis import settings
    from tests.test_gxw_structured_reader import offline_fbd_source
    raw = offline_fbd_source(41)
    program = parse_structured_pou(raw, preserve_unsupported_records=True)
    target = next(node for node in program.nodes if node.kind.value in ('input', 'output', 'contact'))
    target_index = next(i for i, record in enumerate(program.iter_records()) if record.offset == target.offset)

    @settings(max_examples=24, deadline=None, derandomize=True, database=None)
    @given(st.text(alphabet='ABCDE_0123456789变量例😀', min_size=1, max_size=80))
    def check(symbol):
        saved = serialize_structured_pou(replace_node_symbol(program, target.symbol, symbol, node_offset=target.offset))
        reopened = parse_structured_pou(saved, preserve_unsupported_records=True)
        assert [block.raw_header[4:-8] for block in reopened.blocks] == [block.raw_header[4:-8] for block in program.blocks]
        assert [record.raw for i, record in enumerate(reopened.iter_records()) if i != target_index] == [
            record.raw for i, record in enumerate(program.iter_records()) if i != target_index]
        assert reopened.trailer == program.trailer
    check()


def test_variable_length_symbol_rebuild_updates_record_and_header_sizes():
    raw = _fixture_program("gxw_structured_52.json", "52")
    program = parse_structured_pou(raw, logical_name="1.Program.pou")
    original_first_record_length = program.nodes[0].record_length
    original_body_size = program.body_size

    modified = replace_node_symbol(program, "X1", "X100")
    rebuilt = serialize_structured_pou(modified)
    reparsed = parse_structured_pou(rebuilt, logical_name="1.Program.pou")

    assert len(rebuilt) == len(raw) + 4
    assert reparsed.nodes[0].symbol == "X100"
    assert reparsed.nodes[0].record_length == original_first_record_length + 4
    assert reparsed.nodes[1].offset == program.nodes[1].offset + 4
    assert reparsed.body_size == original_body_size + 4
    assert struct.unpack_from("<I", rebuilt, 0x37)[0] == reparsed.body_size + 12
    assert struct.unpack_from("<I", rebuilt, 0x3B)[0] == reparsed.body_size + 12
    assert struct.unpack_from("<I", rebuilt, 0x47)[0] == reparsed.body_size
    assert reparsed.record_count == program.record_count
    assert reparsed.canvas_height == program.canvas_height


def test_symbol_replacement_refuses_ambiguous_source_symbol():
    raw = _fixture_program("gxw_structured_52.json", "52")
    program = parse_structured_pou(raw, logical_name="1.Program.pou")

    first = program.nodes[0]
    third = program.nodes[2]
    ambiguous = replace(
        program,
        nodes=(first, program.nodes[1], replace(third, symbol="X1")),
    )

    with pytest.raises(ValueError, match="occurs in multiple nodes"):
        replace_node_symbol(ambiguous, "X1", "X100")


def _simple_x1_y1_baseline_from_sample_51():
    """Derive a byte-valid X1 -> Y1 source using the controlled sample-51 ABI.

    Sample 51 is the GX Works2-produced reference for X1 -> M1 -> Y1. Removing
    the middle node and merging its two horizontal conductors gives the same
    five-record topology used by the simple sample-48 experiment while keeping
    all header noise and unknown wire fields grounded in a real GX Works2 sample.
    """

    raw = _fixture_program("gxw_structured_49_51.json", "51")
    series = parse_structured_pou(raw, logical_name="1.Program.pou")
    x1, middle, y1 = series.nodes
    rail, left_feed, x1_to_middle, middle_to_y1 = series.wires

    direct = replace(x1_to_middle, end=middle_to_y1.end)
    removed_bytes = len(serialize_structured_node(middle)) + len(
        serialize_structured_wire(middle_to_y1)
    )
    simple = replace(
        series,
        nodes=(x1, y1),
        wires=(rail, left_feed, direct),
        record_count=series.record_count - 2,
        body_size=series.body_size - removed_bytes,
    )
    rebuilt = serialize_structured_pou(simple)
    reparsed = parse_structured_pou(rebuilt, logical_name="1.Program.pou")

    assert len(rebuilt) == 411
    assert reparsed.record_count == 5
    assert [node.symbol for node in reparsed.nodes] == ["X1", "Y1"]
    assert [(wire.start.x, wire.start.y, wire.end.x, wire.end.y) for wire in reparsed.wires] == [
        (1, 0, 1, 5),
        (1, 2, 6, 2),
        (8, 2, 23, 2),
    ]
    return reparsed, series


def test_insert_series_contact_reproduces_known_sample_51_topology():
    simple, gxworks_series = _simple_x1_y1_baseline_from_sample_51()

    modified = insert_series_contact_after(simple, "X1", "X2")
    rebuilt = serialize_structured_pou(modified)
    reparsed = parse_structured_pou(rebuilt, logical_name="1.Program.pou")

    assert len(rebuilt) == 535
    assert reparsed.record_count == 7
    assert reparsed.body_size == 440
    assert [node.symbol for node in reparsed.nodes] == ["X1", "X2", "Y1"]
    assert [
        (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)
        for node in reparsed.nodes
    ] == [
        (6, 1, 8, 3),
        (11, 1, 13, 3),
        (23, 1, 25, 3),
    ]
    assert [
        (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
        for wire in reparsed.wires
    ] == [
        (1, 0, 1, 5),
        (1, 2, 6, 2),
        (8, 2, 11, 2),
        (13, 2, 23, 2),
    ]

    # Stronger regression: the generated structure is byte-identical to the
    # GX Works2-created sample 51 after changing only M1 -> X2.
    expected = serialize_structured_pou(
        replace_node_symbol(gxworks_series, "M1", "X2")
    )
    assert rebuilt == expected


def test_insert_series_contact_shifts_a_tight_downstream_coil():
    simple, _ = _simple_x1_y1_baseline_from_sample_51()
    x1, y1 = simple.nodes
    rail, left_feed, direct = simple.wires

    # Model the tighter direct-rung layout seen in the real sample-48 project:
    # the downstream coil is too close for a contact at x=11..13. Moving the
    # coil and wire endpoint left keeps the binary ABI unchanged while exercising
    # the layout fallback.
    tight_y1 = replace(
        y1,
        bbox=replace(y1.bbox, left=9, right=11),
    )
    tight_direct = replace(direct, end=replace(direct.end, x=9))
    tight = replace(
        simple,
        nodes=(x1, tight_y1),
        wires=(rail, left_feed, tight_direct),
    )

    modified = insert_series_contact_after(tight, "X1", "X2")
    rebuilt = serialize_structured_pou(modified)
    reparsed = parse_structured_pou(rebuilt, logical_name="1.Program.pou")

    assert [node.symbol for node in reparsed.nodes] == ["X1", "X2", "Y1"]
    assert [
        (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom)
        for node in reparsed.nodes
    ] == [
        (6, 1, 8, 3),
        (11, 1, 13, 3),
        (14, 1, 16, 3),
    ]
    assert [
        (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
        for wire in reparsed.wires
    ] == [
        (1, 0, 1, 5),
        (1, 2, 6, 2),
        (8, 2, 11, 2),
        (13, 2, 14, 2),
    ]
