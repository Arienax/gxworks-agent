
from tools.offline_inputs import private_path
import base64
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from src.gxw import (
    FunctionBlockCategory,
    GXWFormatError,
    NodeKind,
    SemanticFunctionBlock,
    SemanticFunctionBlockPort,
    SemanticPortRole,
    TerminalRole,
    build_connectivity_graph,
    build_semantic_model,
    describe_program,
    parse_structured_pou,
)


_FIXTURE_DIR = private_path('tests/fixtures')
_FIXTURE_PATHS = (
    _FIXTURE_DIR / "gxw_structured_72_75.json",
    _FIXTURE_DIR / "gxw_structured_76.json",
    _FIXTURE_DIR / "gxw_structured_77.json",
)


def _fixture(sample):
    for path in _FIXTURE_PATHS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(sample) in payload:
            return payload[str(sample)]
    raise KeyError(sample)


def _program(sample):
    fixture = _fixture(sample)
    return parse_structured_pou(
        base64.b64decode(fixture["program_pou_base64"]),
        logical_name=fixture["logical_name"],
    )


def _model(sample):
    return build_semantic_model(_program(sample))


def _replace_block(program, **changes):
    """Synthetic cases below exercise unknown interfaces, not new format evidence."""
    return replace(program, nodes=(replace(program.nodes[0], **changes), *program.nodes[1:]))


@pytest.mark.parametrize("sample,instance,type_name,length,codes", [
    (72, "timer_a", "TON", 128, [1, 1, 0, 0]),
    (73, "timer_b", "TON", 128, [1, 1, 0, 0]),
    (74, "timer_a", "TON_E", 164, [1, 1, 1, 0, 0, 0]),
    (75, "counter_a", "CTU", 148, [1, 1, 1, 0, 0]),
    (77, "counter_a", "CTU_E", 184, [1, 1, 1, 1, 0, 0, 0]),
])
def test_real_fb_records_have_two_strings_and_no_ordinary_node_flags(sample, instance, type_name, length, codes):
    program = _program(sample)
    block = program.nodes[0]
    assert block.kind == NodeKind.FUNCTION_BLOCK
    assert block.kind_code == 0x02
    assert block.instance_name == block.symbol == instance
    assert block.type_name == type_name
    assert block.record_length == length == len(block.raw)
    assert block.object_flag is None and block.reserved is None
    assert [port.port_kind_code for port in block.ports] == codes
    assert all(node.object_flag == 1 and node.reserved == 0 for node in program.nodes[1:])
    assert all(node.type_name is None and node.instance_name is None for node in program.nodes[1:])
    assert program.unknown_records == ()
    assert f"FunctionBlock {instance}: {type_name} @ {block.bbox}" in describe_program(program)


def test_samples_72_73_instance_rename_changes_one_record_byte_and_keeps_interface_and_nets():
    original = _program(72)
    renamed = _program(73)
    original_node, renamed_node = original.nodes[0], renamed.nodes[0]
    assert [
        (index, left, right)
        for index, (left, right) in enumerate(zip(original_node.raw, renamed_node.raw))
        if left != right
    ] == [(28, ord("a"), ord("b"))]
    assert original.nodes[1:] == renamed.nodes[1:]
    assert original.wires == renamed.wires
    before, = build_semantic_model(original).function_blocks
    after, = build_semantic_model(renamed).function_blocks
    assert (before.instance_name, after.instance_name) == ("timer_a", "timer_b")
    assert before.type_name == after.type_name == "TON"
    assert before.ports == after.ports


def test_sample_72_ton_preserves_both_data_outputs_without_inventing_eno():
    model = _model(72)
    block, = model.function_blocks
    assert isinstance(block, SemanticFunctionBlock)
    assert all(isinstance(port, SemanticFunctionBlockPort) for port in block.ports)
    assert block.type_known
    assert block.category == FunctionBlockCategory.TIMER
    assert model.timers == (block,)
    assert model.counters == ()
    assert block.enable_in is None and block.enable_out is None
    assert not block.has_enable_interface
    assert [port.formal_name for port in block.data_inputs] == ["IN", "PT"]
    assert [port.formal_name for port in block.data_outputs] == ["Q", "ET"]
    assert {
        port.formal_name: port.terminal_symbols for port in block.ports
    } == {"IN": ("X1",), "PT": ("T#1s",), "Q": ("Y1",), "ET": ("elapsed_time",)}
    assert block.port_named("ENO") is None
    assert model.functions == model.unmodeled_nodes == model.issues == ()


def test_sample_74_ton_e_distinguishes_eno_q_and_et_despite_identical_raw_output_codes():
    block, = _model(74).function_blocks
    assert block.has_enable_interface
    assert block.enable_in == block.port_named("EN")
    assert block.enable_out == block.port_named("ENO")
    assert block.enable_in.terminal_symbols == ("X0",)
    assert block.enable_out.terminal_symbols == ("M0",)
    assert block.port_named("IN").terminal_symbols == ("X1",)
    assert block.port_named("PT").terminal_symbols == ("T#1s",)
    assert block.port_named("Q").terminal_symbols == ("Y1",)
    assert block.port_named("ET").terminal_symbols == ("elapsed_time",)
    assert [port.role for port in block.ports] == [
        SemanticPortRole.ENABLE_IN, SemanticPortRole.DATA_IN, SemanticPortRole.DATA_IN,
        SemanticPortRole.ENABLE_OUT, SemanticPortRole.DATA_OUT, SemanticPortRole.DATA_OUT,
    ]
    assert [port.port_kind_code for port in block.ports[3:]] == [0, 0, 0]
    assert len({port.net_index for port in block.ports[3:]}) == 3


def test_sample_75_ctu_preserves_actual_t1_sink_without_classifying_it_as_a_timer():
    model = _model(75)
    block, = model.function_blocks
    assert (block.instance_name, block.type_name) == ("counter_a", "CTU")
    assert block.category == FunctionBlockCategory.COUNTER
    assert model.counters == (block,) and model.timers == ()
    assert not block.has_enable_interface
    assert {
        port.formal_name: port.terminal_symbols for port in block.ports
    } == {"CU": ("X1",), "RESET": ("X2",), "PV": ("5",), "Q": ("T1",), "CV": ("D0",)}
    terminal = next(terminal for terminal in model.terminals if terminal.symbol == "T1")
    assert terminal.role == TerminalRole.SINK
    assert terminal.net_index == block.port_named("Q").net_index


def test_sample_76_same_type_instances_keep_separate_identity_and_terminal_nets():
    program = _program(76)
    model = build_semantic_model(program)
    first, second = model.function_blocks
    assert [block.instance_name for block in model.function_blocks] == ["timer_a", "timer_b"]
    assert first.type_name == second.type_name == "TON"
    assert first.node_offset != second.node_offset
    assert model.timers == (first, second)
    assert model.counters == ()
    expected_bindings = (
        {"IN": ("X1",), "PT": ("T#1s",), "Q": ("Y1",), "ET": ("elapsed_a",)},
        {"IN": ("X2",), "PT": ("T#2s",), "Q": ("Y2",), "ET": ("elapsed_b",)},
    )
    for block, expected in zip(model.function_blocks, expected_bindings):
        assert block.type_known and not block.has_enable_interface
        assert {port.formal_name: port.terminal_symbols for port in block.ports} == expected
        assert [port.port_kind_code for port in block.ports] == [1, 1, 0, 0]
        assert all(port.block_offset == block.node_offset for port in block.ports)
    first_nets = {port.net_index for port in first.ports}
    second_nets = {port.net_index for port in second.ports}
    assert len(first_nets) == len(second_nets) == 4
    assert first_nets.isdisjoint(second_nets)
    first_terminals = {offset for port in first.ports for offset in port.terminal_node_offsets}
    second_terminals = {offset for port in second.ports for offset in port.terminal_node_offsets}
    assert len(first_terminals) == len(second_terminals) == 4
    assert first_terminals.isdisjoint(second_terminals)
    assert program.unknown_records == ()
    assert model.functions == model.unmodeled_nodes == model.issues == ()


def test_sample_77_ctu_e_adds_en_eno_and_preserves_ctu_data_bindings():
    model = _model(77)
    block, = model.function_blocks
    ordinary, = _model(75).function_blocks
    assert (block.instance_name, block.type_name) == ("counter_a", "CTU_E")
    assert block.type_known and block.category == FunctionBlockCategory.COUNTER
    assert model.counters == (block,) and model.timers == ()
    assert block.has_enable_interface
    assert block.enable_in == block.port_named("EN")
    assert block.enable_out == block.port_named("ENO")
    assert block.enable_in.terminal_symbols == ("X0",)
    assert block.enable_out.terminal_symbols == ("M0",)
    assert [port.formal_name for port in block.data_inputs] == ["CU", "RESET", "PV"]
    assert [port.formal_name for port in block.data_outputs] == ["Q", "CV"]
    expected_data = {"CU": ("X1",), "RESET": ("X2",), "PV": ("5",), "Q": ("T1",), "CV": ("D0",)}
    assert {port.formal_name: port.terminal_symbols for port in ordinary.ports} == expected_data
    assert {
        port.formal_name: port.terminal_symbols for port in block.data_inputs + block.data_outputs
    } == expected_data
    assert [port.role for port in block.ports] == [
        SemanticPortRole.ENABLE_IN, SemanticPortRole.DATA_IN, SemanticPortRole.DATA_IN,
        SemanticPortRole.DATA_IN, SemanticPortRole.ENABLE_OUT, SemanticPortRole.DATA_OUT,
        SemanticPortRole.DATA_OUT,
    ]
    assert [port.port_kind_code for port in block.ports[4:]] == [0, 0, 0]
    assert len({port.net_index for port in block.ports}) == 7
    terminal = next(terminal for terminal in model.terminals if terminal.symbol == "T1")
    assert terminal.role == TerminalRole.SINK
    assert terminal.net_index == block.port_named("Q").net_index
    assert model.functions == model.unmodeled_nodes == model.issues == ()


@pytest.mark.parametrize("sample", [72, 73, 74, 75, 76, 77])
def test_real_fb_ports_use_supplied_nets_and_keep_raw_ports_and_terminal_offsets(sample):
    program = _program(sample)
    original_graph = build_connectivity_graph(program)
    graph = replace(original_graph, nets=tuple(replace(net, index=net.index + 100) for net in original_graph.nets))
    model = build_semantic_model(program, connectivity=graph)
    nodes_by_offset = {node.offset: node for node in program.nodes}
    for block in model.function_blocks:
        node = nodes_by_offset[block.node_offset]
        for port in block.ports:
            net = graph.net_for_port(block.node_offset, port.port_index)
            assert port.block_offset == block.node_offset
            assert port.net_index == net.index
            assert port.port_kind_code == node.ports[port.port_index].port_kind_code
            assert port.point == node.port_point(port.port_index)
            assert port.terminal_node_offsets == tuple(
                ref.node_offset for ref in net.ports if ref.node_kind in {NodeKind.INPUT, NodeKind.OUTPUT}
            )
    assert model.functions == model.unmodeled_nodes == model.issues == ()
    assert build_connectivity_graph(program) == original_graph


@pytest.mark.parametrize("type_name", ["FOO", "TON_E-2", "FOO_E", None])
def test_synthetic_unregistered_fb_types_keep_identity_and_ports_without_function_inference(type_name):
    program = _replace_block(_program(74), type_name=type_name, symbol="TON")
    model = build_semantic_model(program)
    block, = model.function_blocks
    assert block.instance_name == "TON" and block.type_name == type_name
    assert not block.type_known
    assert block.category == FunctionBlockCategory.UNKNOWN
    assert len(block.ports) == 6
    assert all(port.role == SemanticPortRole.UNKNOWN and port.formal_name is None for port in block.ports)
    assert block.enable_out is None
    assert block.ports[3].terminal_symbols == ("M0",)
    assert [issue.code for issue in model.issues] == ["unknown_function_block_type"]
    assert model.functions == model.unmodeled_nodes == ()


def test_synthetic_ctu_e_type_with_ton_e_interface_does_not_guess_port_names():
    model = build_semantic_model(_replace_block(_program(74), type_name="CTU_E"))
    block, = model.function_blocks
    assert block.type_known and block.category == FunctionBlockCategory.COUNTER
    assert len(block.ports) == 6
    assert all(port.role == SemanticPortRole.UNKNOWN and port.formal_name is None for port in block.ports)
    assert not block.has_enable_interface
    assert block.ports[3].terminal_symbols == ("M0",)
    assert [issue.code for issue in model.issues] == ["function_block_port_count"]
    assert model.functions == model.unmodeled_nodes == ()


def test_fb_registry_can_be_disabled_without_falling_back_to_function_rules():
    model = build_semantic_model(_program(72), function_block_registry={})
    assert not model.function_blocks[0].type_known
    assert model.timers == ()
    assert [issue.code for issue in model.issues] == ["unknown_function_block_type"]


def test_synthetic_reordered_fb_ports_are_named_by_type_and_geometry():
    program = _program(74)
    model = build_semantic_model(_replace_block(program, ports=tuple(reversed(program.nodes[0].ports))))
    block, = model.function_blocks
    assert block.port_named("EN").port_index == 5
    assert block.port_named("ENO").port_index == 2
    assert block.port_named("Q").port_index == 1
    original, = _model(74).function_blocks
    for port in original.ports:
        actual = block.port_named(port.formal_name)
        assert (actual.role, actual.terminal_symbols) == (port.role, port.terminal_symbols)
    assert model.issues == ()


@pytest.mark.parametrize("count", [0, 3, 5])
def test_synthetic_unobserved_fb_port_counts_preserve_instance_and_all_ports(count):
    program = _program(72)
    ports = (program.nodes[0].ports + program.nodes[0].ports[:1])[:count]
    model = build_semantic_model(_replace_block(program, ports=ports))
    block, = model.function_blocks
    assert block.instance_name == "timer_a" and block.type_known
    assert len(block.ports) == count
    assert all(port.role == SemanticPortRole.UNKNOWN and port.formal_name is None for port in block.ports)
    assert [issue.code for issue in model.issues] == ["function_block_port_count"]


@pytest.mark.parametrize("change", ["function_input_code", "function_output_code", "duplicate_point", "wrong_row", "off_edge"])
def test_synthetic_unknown_fb_layout_is_retained_without_assigning_formal_names(change):
    program = _program(72)
    ports = list(program.nodes[0].ports)
    if change == "function_input_code":
        ports[0] = replace(ports[0], port_kind_code=3)
    elif change == "function_output_code":
        ports[2] = replace(ports[2], port_kind_code=2)
    elif change == "duplicate_point":
        ports[1] = ports[0]
    elif change == "wrong_row":
        ports[0] = replace(ports[0], local_y=1)
    else:
        ports[0] = replace(ports[0], local_x=1)
    model = build_semantic_model(_replace_block(program, ports=tuple(ports)))
    block, = model.function_blocks
    assert len(block.ports) == 4
    assert all(port.role == SemanticPortRole.UNKNOWN and port.formal_name is None for port in block.ports)
    assert [issue.code for issue in model.issues] == ["function_block_port_layout"]


def _replace_first_record(program, record):
    record = bytearray(record)
    struct.pack_into("<I", record, 0, len(record))
    body = bytes(record) + program.raw[95 + program.nodes[0].record_length:]
    header = bytearray(program.raw[:95])
    struct.pack_into("<I", header, 0x47, len(body))
    return bytes(header) + body


@pytest.mark.parametrize("damage", ["instance_length", "type_length", "type_utf16", "bbox", "port_count", "port_size", "truncated_port", "extra_bytes"])
def test_synthetic_malformed_fb_records_still_fail_closed(damage):
    program = _program(72)
    record = bytearray(program.nodes[0].raw)
    # The real TON record has instance text at 16, type count at 32, bbox at 44.
    if damage == "instance_length":
        struct.pack_into("<I", record, 12, 0xFFFFFFFF)
    elif damage == "type_length":
        struct.pack_into("<I", record, 32, 0)
    elif damage == "type_utf16":
        struct.pack_into("<H", record, 36, 0xD800)
    elif damage == "bbox":
        record = record[:56]
    elif damage == "port_count":
        struct.pack_into("<I", record, 60, 99)
    elif damage == "port_size":
        struct.pack_into("<I", record, 64, 12)
    elif damage == "truncated_port":
        record = record[:-1]
    else:
        record += b"\x00\x00"
    with pytest.raises(GXWFormatError):
        parse_structured_pou(_replace_first_record(program, record))
