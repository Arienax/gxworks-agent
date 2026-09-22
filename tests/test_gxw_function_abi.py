import base64
import json
from pathlib import Path

from src.gxw.connectivity import build_connectivity_graph
from src.gxw.models import NodeKind, Point
from src.gxw.structured_pou import parse_structured_pou


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gxw_structured_65_66.json"


def _program(sample: int):
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))[str(sample)]
    data = base64.b64decode(fixture["program_pou_base64"])
    return parse_structured_pou(data, logical_name=fixture["logical_name"])


def _node(program, symbol):
    matches = [node for node in program.nodes if node.symbol == symbol]
    assert len(matches) == 1
    return matches[0]


def test_sample_65_mov_eno_connects_to_add_en():
    program = _program(65)
    graph = build_connectivity_graph(program)
    mov = _node(program, "MOV")
    add = _node(program, "ADD_E-2")

    assert all(node.symbol != "?" for node in program.nodes)
    assert [port.port_kind_code for port in add.ports] == [3, 3, 3, 0, 2]
    assert mov.port_point(2) == Point(35, 5)
    assert add.port_point(0) == Point(45, 11)
    assert [(wire.start.x, wire.start.y, wire.end.x, wire.end.y) for wire in program.wires[-2:]] == [
        (35, 5, 45, 5),
        (45, 5, 45, 11),
    ]
    assert graph.ports_connected(mov.offset, 2, add.offset, 0)
    assert not graph.ports_connected(mov.offset, 2, add.offset, 3)


def test_sample_66_add_e_symbol_suffix_tracks_input_arity():
    program = _program(66)
    add = _node(program, "ADD_E-3")

    assert add.kind == NodeKind.FUNCTION
    assert len(add.ports) == 6
    assert [port.port_kind_code for port in add.ports] == [3, 3, 3, 3, 0, 2]
    assert [add.port_point(index) for index in range(6)] == [
        Point(45, 11),
        Point(45, 12),
        Point(45, 13),
        Point(45, 14),
        Point(51, 11),
        Point(51, 12),
    ]
    assert program.canvas_height == 17


# Samples 67-71 extend the same native function ABI contract.
_FIXTURE_67_71_PATH = Path(__file__).parent / "fixtures" / "gxw_structured_67_71.json"


def _program_67_71(sample: int):
    fixture = json.loads(_FIXTURE_67_71_PATH.read_text(encoding="utf-8"))[str(sample)]
    data = base64.b64decode(fixture["program_pou_base64"])
    return parse_structured_pou(data, logical_name=fixture["logical_name"])


def _function_67_71(program):
    matches = [node for node in program.nodes if node.kind == NodeKind.FUNCTION]
    assert len(matches) == 1
    return matches[0]


def test_samples_67_68_and_e_suffix_tracks_extensible_input_arity():
    two = _function_67_71(_program_67_71(67))
    three = _function_67_71(_program_67_71(68))

    assert two.symbol == "AND_E-2"
    assert three.symbol == "AND_E-3"
    assert [port.port_kind_code for port in two.ports] == [3, 3, 3, 0, 2]
    assert [port.port_kind_code for port in three.ports] == [3, 3, 3, 3, 0, 2]
    assert two.record_length == 138
    assert three.record_length == 154


def test_sample_68_growth_is_one_port_descriptor_plus_one_input_terminal():
    p67 = _program_67_71(67)
    p68 = _program_67_71(68)

    assert len(p68.raw) - len(p67.raw) == 80
    assert p68.record_count - p67.record_count == 1
    d4 = [node for node in p68.nodes if node.symbol == "D4"]
    assert len(d4) == 1
    assert d4[0].kind == NodeKind.INPUT
    assert d4[0].record_length == 64


def test_sample_69_fixed_two_input_div_e_has_no_arity_suffix():
    function = _function_67_71(_program_67_71(69))

    assert function.symbol == "DIV_E"
    assert len(function.ports) == 5
    assert [port.port_kind_code for port in function.ports] == [3, 3, 3, 0, 2]


def test_samples_70_71_plain_vs_enabled_abs_port_abi():
    plain = _function_67_71(_program_67_71(70))
    enabled = _function_67_71(_program_67_71(71))

    assert plain.symbol == "ABS"
    assert enabled.symbol == "ABS_E"
    assert [port.port_kind_code for port in plain.ports] == [3, 2]
    assert [port.port_kind_code for port in enabled.ports] == [3, 3, 0, 2]


def test_samples_67_71_continue_header_height_observations():
    for sample in range(67, 72):
        program = _program_67_71(sample)
        assert program.canvas_height == 17
        assert program.body_size == len(program.raw) - 0x5F
