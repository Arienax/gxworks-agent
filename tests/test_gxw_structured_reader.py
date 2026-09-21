import base64

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
