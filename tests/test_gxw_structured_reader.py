import base64
from functools import lru_cache
import json
import struct
import zipfile

import pytest

from tools.offline_inputs import private_path

from src.gxw.models import NodeKind
from src.gxw.structured_pou import parse_structured_pou


POU_52 = base64.b64decode(
    "AQAAAAAAAQAAAAAAAQAKAAAAAAACAAAAAQAAAAEAAAAAAOoHCQAAAAYAAAAJAAsAAAABAAAA0PABAADwAQAAAQAAAAEAAADkAQAAAQAAAAAAAQAAAAAABgAAAAgAAABQAAAAAQAAAAMAAAADAAAAWAAxAAAAAQAAAAAABgAAAAEAAAAIAAAAAwAAAAIAAAAQAAAAAwAAAAAAAAABAAAAEAAAAAIAAAACAAAAAQAAAFAAAAABAAAABQAAAAMAAABZADEAAAABAAAAAAAWAAAAAQAAABgAAAADAAAAAgAAABAAAAADAAAAAAAAAAEAAAAQAAAAAAAAAAIAAAABAAAAUAAAAAEAAAADAAAAAwAAAFgAMgAAAAEAAAAAAAYAAAAEAAAACAAAAAYAAAACAAAAEAAAAAMAAAAAAAAAAQAAABAAAAACAAAAAgAAAAEAAAAsAAAAAgAAAAAAAAABAAAAAAABAAAAAAABAAAAAAAAAAEAAAAGAAAAAAAAACwAAAACAAAAAAAAAAEAAAAAAAEAAAAAAAEAAAACAAAABgAAAAIAAAAAAAAALAAAAAIAAAAAAAAAAQAAAAAAAQAAAAAABgAAAAIAAAAGAAAABQAAAAAAAAAsAAAAAgAAAAAAAAABAAAAAAABAAAAAAAIAAAAAgAAAAgAAAAFAAAAAAAAACwAAAACAAAAAAAAAAEAAAAAAAEAAAAAAAgAAAACAAAAFgAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)

POU_53 = base64.b64decode(
    "AQAAAAAAAQAAAAAAAQAKAAAAAAACAAAAAQAAAAEAAAAAAOoHCQAAAAYAAAAUAA4AAAABAAAA0OoBAADqAQAAAQAAAAEAAADeAQAAAQAAAAAAAQAAAAAABgAAAAcAAAByAAAAAQAAAAEAAAAEAAAATQBPAFYAAAABAAAAAAAWAAAAAAAAAB0AAAAEAAAABAAAABAAAAADAAAAAAAAAAIAAAAQAAAAAwAAAAAAAAADAAAAEAAAAAAAAAAHAAAAAgAAABAAAAACAAAABwAAAAMAAABQAAAAAQAAAAMAAAADAAAAWAAxAAAAAQAAAAAABgAAAAEAAAAIAAAAAwAAAAIAAAAQAAAAAwAAAAAAAAABAAAAEAAAAAIAAAACAAAAAQAAAEAAAAABAAAADQAAAAMAAAAxADAAAAABAAAAAAAUAAAAAgAAABYAAAAEAAAAAQAAABAAAAACAAAAAgAAAAEAAABAAAAAAQAAAA4AAAADAAAARAAxAAAAAQAAAAAAHQAAAAIAAAAfAAAABAAAAAEAAAAQAAAAAwAAAAAAAAABAAAALAAAAAIAAAAAAAAAAQAAAAAAAQAAAAAAAQAAAAAAAAABAAAABgAAAAAAAAAsAAAAAgAAAAAAAAABAAAAAAABAAAAAAABAAAAAgAAAAYAAAACAAAAAAAAACwAAAACAAAAAAAAAAEAAAAAAAEAAAAAAAgAAAACAAAAFgAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def test_parse_structured_parallel_sample_52():
    program = parse_structured_pou(POU_52, logical_name="1.Program.pou")

    assert program.record_count == 8
    assert program.canvas_height == 6
    assert [
        (
            node.kind,
            node.symbol,
            (node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom),
        )
        for node in program.nodes
    ] == [
        (NodeKind.CONTACT, "X1", (6, 1, 8, 3)),
        (NodeKind.COIL, "Y1", (22, 1, 24, 3)),
        (NodeKind.CONTACT, "X2", (6, 4, 8, 6)),
    ]
    assert [
        (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
        for wire in program.wires
    ] == [
        (1, 0, 1, 6),
        (1, 2, 6, 2),
        (6, 2, 6, 5),
        (8, 2, 8, 5),
        (8, 2, 22, 2),
    ]


def test_parse_structured_mov_sample_53():
    program = parse_structured_pou(POU_53, logical_name="1.Program.pou")

    assert program.record_count == 7
    assert [(node.kind, node.symbol, len(node.ports)) for node in program.nodes] == [
        (NodeKind.FUNCTION, "MOV", 4),
        (NodeKind.CONTACT, "X1", 2),
        (NodeKind.INPUT, "10", 1),
        (NodeKind.OUTPUT, "D1", 1),
    ]
    function = program.nodes[0]
    assert (
        function.bbox.left,
        function.bbox.top,
        function.bbox.right,
        function.bbox.bottom,
    ) == (22, 0, 29, 4)


def test_decoder_cli_retains_program_selection_without_qt_or_writes(tmp_path, capsys):
    from gxw.decoder import main
    from gxw.object_model import default_baseline
    source = tmp_path / "sample.gxw"
    source.write_bytes(default_baseline())
    before = source.read_bytes()
    assert main([str(source), "--list-programs"]) == 0
    assert "1.Program.pou" in capsys.readouterr().out
    assert main([str(source), "--program", "1.Program.pou"]) == 0
    assert "Program 1.Program.pou:" in capsys.readouterr().out
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]


@lru_cache(maxsize=1)
def offline_fbd_corpus():
    """Original author bytes; expected network counts use independent framing."""
    return json.loads(private_path('tests/fixtures/gxw_offline_fbd_corpus.json').read_text(encoding='utf-8'))


def offline_fbd_source(index):
    entry = offline_fbd_corpus()['sources'][index]
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-offline-fbd-corpus-20261021.zip')) as archive:
        return archive.read(entry['member'])


def offline_fbd_project(index):
    entry = next(row for row in offline_fbd_corpus()['project_cases'] if row['index'] == index)
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-offline-fbd-corpus-20261021.zip')) as archive:
        return archive.read(entry['member']), entry


@pytest.mark.parametrize('index', range(615))
def test_original_fbd_corpus_bounds_named_commented_networks_and_opaque_records(index):
    expected = offline_fbd_corpus()['sources'][index]
    program = parse_structured_pou(offline_fbd_source(index), logical_name=expected['source'],
                                   preserve_unsupported_records=True)
    assert len(program.blocks) == expected['blocks']
    assert program.record_count == expected['records']
    assert [len(block.raw_header) for block in program.blocks] == expected['headers']
    assert sum(len(list(records)) for _, records in program.block_records()) == expected['records']
    assert len(program.trailer) == 24


@pytest.mark.parametrize('damage', ['first_count', 'second_count', 'terminator', 'records', 'size'])
def test_network_string_preservation_never_relaxes_framing(damage):
    from src.gxw.models import GXWFormatError
    raw = bytearray(offline_fbd_source(41))
    program = parse_structured_pou(bytes(raw), preserve_unsupported_records=True)
    block = next(block for block in program.blocks if len(block.raw_header) > 24)
    first_count = struct.unpack_from('<I', raw, block.offset + 4)[0]
    second = block.offset + 8 + first_count * 2
    if damage == 'first_count': struct.pack_into('<I', raw, block.offset + 4, 0x7fffffff)
    elif damage == 'second_count': struct.pack_into('<I', raw, second, 0x7fffffff)
    elif damage == 'terminator': raw[second - 2:second] = b'XX'
    elif damage == 'records': struct.pack_into('<I', raw, block.offset + len(block.raw_header) - 4, 0x7fffffff)
    else: struct.pack_into('<I', raw, block.offset, len(block.raw_header) - 1)
    with pytest.raises(GXWFormatError):
        parse_structured_pou(bytes(raw), preserve_unsupported_records=True)
