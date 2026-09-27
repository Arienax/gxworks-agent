import base64
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.gxw import (
    CoilRole,
    ContactPolarity,
    LadderEdge,
    SemanticCoil,
    SemanticContact,
    SemanticLadderPort,
    SemanticPortRole,
    TerminalRole,
    build_connectivity_graph,
    build_semantic_model,
)
from src.gxw.models import NodeKind, Rect
from src.gxw.structured_pou import parse_structured_pou


_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_FIXTURE_FILES = (
    _FIXTURE_DIR / "gxw_structured_49_51.json",
    _FIXTURE_DIR / "gxw_structured_52.json",
    _FIXTURE_DIR / "gxw_structured_54_58.json",
    _FIXTURE_DIR / "gxw_structured_64.json",
    _FIXTURE_DIR / "gxw_structured_65_66.json",
    _FIXTURE_DIR / "gxw_structured_67_71.json",
)


def _fixture(sample: int):
    key = str(sample)
    for path in _FIXTURE_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if key in payload:
            return payload[key]
    raise KeyError(sample)


def _program(sample: int):
    fixture = _fixture(sample)
    data = base64.b64decode(fixture["program_pou_base64"])
    return parse_structured_pou(data, logical_name=fixture["logical_name"])


def _model(sample: int):
    return build_semantic_model(_program(sample))


def _function(model, symbol: str):
    matches = [function for function in model.functions if function.serialized_symbol == symbol]
    assert len(matches) == 1
    return matches[0]


def test_sample_64_preserves_unresolved_terminal_as_semantic_issue():
    model = _model(64)
    unresolved = [terminal for terminal in model.terminals if terminal.symbol == "?"]

    assert len(unresolved) == 1
    assert unresolved[0].role == TerminalRole.SOURCE
    assert unresolved[0].unresolved
    assert any(issue.code == "unresolved_terminal" for issue in model.issues)


def test_sample_65_function_to_function_enable_connection_uses_shared_net():
    model = _model(65)
    mov = _function(model, "MOV")
    add = _function(model, "ADD_E-2")

    assert mov.enable_out is not None
    assert add.enable_in is not None
    assert mov.enable_out.net_index == add.enable_in.net_index
    assert add.declared_arity == 2
    assert add.data_input_count == 2


def test_samples_66_68_resolve_extensible_function_family_and_arity():
    add = _function(_model(66), "ADD_E-3")
    and_fn = _function(_model(68), "AND_E-3")

    assert (add.base_name, add.extensible_inputs, add.declared_arity) == ("ADD_E", True, 3)
    assert (and_fn.base_name, and_fn.extensible_inputs, and_fn.declared_arity) == ("AND_E", True, 3)
    assert add.data_input_count == 3
    assert and_fn.data_input_count == 3
    assert [port.role for port in and_fn.ports] == [
        SemanticPortRole.ENABLE_IN,
        SemanticPortRole.DATA_IN,
        SemanticPortRole.DATA_IN,
        SemanticPortRole.DATA_IN,
        SemanticPortRole.ENABLE_OUT,
        SemanticPortRole.DATA_OUT,
    ]


def test_sample_69_fixed_div_e_is_not_treated_as_extensible_suffix_family():
    div = _function(_model(69), "DIV_E")

    assert div.base_name == "DIV_E"
    assert not div.extensible_inputs
    assert div.declared_arity is None
    assert div.data_input_count == 2
    assert div.has_enable_interface


def test_samples_70_71_plain_vs_enabled_abs_semantics_and_terminal_bindings():
    plain_model = _model(70)
    enabled_model = _model(71)
    plain = _function(plain_model, "ABS")
    enabled = _function(enabled_model, "ABS_E")

    assert [port.role for port in plain.ports] == [
        SemanticPortRole.DATA_IN,
        SemanticPortRole.DATA_OUT,
    ]
    assert not plain.has_enable_interface
    assert plain.data_inputs[0].terminal_symbols == ("D0",)
    assert plain.data_outputs[0].terminal_symbols == ("D2",)

    assert [port.role for port in enabled.ports] == [
        SemanticPortRole.ENABLE_IN,
        SemanticPortRole.DATA_IN,
        SemanticPortRole.ENABLE_OUT,
        SemanticPortRole.DATA_OUT,
    ]
    assert enabled.has_enable_interface
    assert enabled.enable_in.terminal_symbols == ("X1",)
    assert enabled.data_inputs[0].terminal_symbols == ("D0",)
    assert enabled.data_outputs[0].terminal_symbols == ("D2",)


def test_observed_samples_have_no_unexpected_semantic_warnings_except_64_placeholder():
    for sample in range(65, 72):
        assert _model(sample).issues == ()


def _replace_node(program, replacement):
    """Construct synthetic model inputs; these are not new GX Works2 samples."""
    return replace(
        program,
        nodes=tuple(
            replacement if node.offset == replacement.offset else node
            for node in program.nodes
        ),
    )


def test_sample_54_contact_function_coil_execution_connections():
    model = _model(54)
    contact, = model.contacts
    coil, = model.coils
    mov = _function(model, "MOV")

    assert isinstance(contact, SemanticContact)
    assert isinstance(coil, SemanticCoil)
    assert isinstance(contact.execution_in, SemanticLadderPort)
    assert (contact.symbol, contact.polarity) == ("X1", ContactPolarity.NORMALLY_OPEN)
    assert (coil.symbol, coil.role) == ("Y1", CoilRole.NORMAL)
    assert contact.execution_out.net_index == mov.enable_in.net_index
    assert mov.enable_out.net_index == coil.execution_in.net_index
    assert contact.execution_in.net_index != contact.execution_out.net_index
    assert model.unmodeled_nodes == ()
    assert {terminal.symbol for terminal in model.terminals} == {"10", "D1"}

    # Identical raw code 0 does not imply identical semantics on different kinds.
    assert mov.enable_out.role == SemanticPortRole.ENABLE_OUT
    assert coil.ports[1].port_kind_code == 0
    assert coil.ports[1].role == SemanticPortRole.UNKNOWN
    assert coil.ports[1].net_index != coil.execution_in.net_index
    assert [(issue.code, issue.node_offset, issue.port_index) for issue in model.issues] == [
        ("unmodeled_coil_output", coil.node_offset, 1),
    ]


def test_sample_52_parallel_contacts_share_each_side_without_bridging_through_contacts():
    model = _model(52)
    x1, x2 = model.contacts
    coil, = model.coils

    assert (x1.symbol, x2.symbol, coil.symbol) == ("X1", "X2", "Y1")
    assert x1.execution_in.net_index == x2.execution_in.net_index
    assert x1.execution_out.net_index == x2.execution_out.net_index
    assert x1.execution_out.net_index == coil.execution_in.net_index
    assert x1.execution_in.net_index != x1.execution_out.net_index
    assert x2.execution_in.net_index != x2.execution_out.net_index


def test_sample_56_contact_fans_out_to_two_coils_regardless_of_symbol_prefix():
    model = _model(56)
    contact, = model.contacts
    assert [coil.symbol for coil in model.coils] == ["X2", "Y1"]
    assert all(coil.role == CoilRole.NORMAL for coil in model.coils)
    assert all(
        coil.execution_in.net_index == contact.execution_out.net_index
        for coil in model.coils
    )
    assert len({coil.ports[1].net_index for coil in model.coils}) == 2


@pytest.mark.parametrize("sample", [49, 50, 51, 52, 54, 55, 56, 57, 58])
def test_real_ladder_samples_preserve_every_port_and_supplied_connectivity_net(sample):
    program = _program(sample)
    original_graph = build_connectivity_graph(program)
    # A caller-supplied graph must be used, including its own net numbering.
    graph = replace(
        original_graph,
        nets=tuple(replace(net, index=net.index + 100) for net in original_graph.nets),
    )
    model = build_semantic_model(program, connectivity=graph)
    nodes = {node.offset: node for node in program.nodes}
    for element in (*model.contacts, *model.coils):
        node = nodes[element.node_offset]
        assert len(element.ports) == len(node.ports)
        for index, port in enumerate(element.ports):
            assert (port.node_offset, port.port_index) == (node.offset, index)
            assert port.port_kind_code == node.ports[index].port_kind_code
            assert port.point == node.port_point(index)
            assert port.net_index == graph.net_for_port(node.offset, index).index
    assert model.unmodeled_nodes == ()
    assert [issue.code for issue in model.issues] == [
        "unmodeled_coil_output" for _ in model.coils
    ]
    assert build_connectivity_graph(program) == original_graph


def test_sample_50_nc_contact_has_code_11_input_and_code_2_output():
    model = _model(50)
    contact, = model.contacts
    coil, = model.coils
    assert contact.polarity == ContactPolarity.NORMALLY_CLOSED
    assert contact.symbol == "X1"
    assert contact.execution_in.port_kind_code == 11
    assert contact.execution_out.port_kind_code == 2
    assert contact.execution_out.net_index == coil.execution_in.net_index
    assert contact.execution_in.net_index != contact.execution_out.net_index
    assert [issue.code for issue in model.issues] == ["unmodeled_coil_output"]


def test_sample_49_label_symbols_are_preserved_on_contacts_and_coils():
    model = _model(49)
    assert model.contacts[0].symbol == "input_x1"
    assert model.coils[0].symbol == "output_y1"
    assert model.contacts[0].execution_out.net_index == model.coils[0].execution_in.net_index
    assert model.terminals == ()


def test_sample_51_series_contacts_keep_three_distinct_execution_nets():
    model = _model(51)
    first, second = model.contacts
    coil, = model.coils
    assert (first.symbol, second.symbol, coil.symbol) == ("X1", "M1", "Y1")
    assert first.execution_out.net_index == second.execution_in.net_index
    assert second.execution_out.net_index == coil.execution_in.net_index
    assert len({first.execution_in.net_index, first.execution_out.net_index, second.execution_out.net_index}) == 3


@pytest.mark.parametrize("sample,code,polarity", [
    (54, 11, ContactPolarity.NORMALLY_OPEN),
    (50, 3, ContactPolarity.NORMALLY_CLOSED),
])
def test_no_nc_input_aliases_keep_native_kind_polarity(sample, code, polarity):
    program = _program(sample)
    node = next(node for node in program.nodes if node.kind in {NodeKind.CONTACT, NodeKind.CONTACT_NC})
    ports = (replace(node.ports[0], port_kind_code=code), node.ports[1])
    model = build_semantic_model(_replace_node(program, replace(node, ports=ports)))
    contact, = model.contacts
    assert contact.execution_in.port_kind_code == code
    assert contact.polarity == polarity
    assert contact.edge == LadderEdge.NONE
    assert not any(issue.code == "unknown_ladder_port" for issue in model.issues)


def test_reordered_ladder_ports_preserve_connectivity_without_inferring_execution():
    program = _program(54)
    for node in program.nodes:
        if node.kind in {NodeKind.CONTACT, NodeKind.COIL}:
            program = _replace_node(program, replace(node, ports=tuple(reversed(node.ports))))
    model = build_semantic_model(program)
    contact, = model.contacts
    coil, = model.coils
    mov = _function(model, "MOV")

    assert contact.polarity == ContactPolarity.UNKNOWN
    assert coil.role == CoilRole.UNKNOWN
    assert contact.edge == coil.edge == LadderEdge.UNKNOWN
    assert contact.execution_in is contact.execution_out is coil.execution_in is None
    assert all(p.role == SemanticPortRole.UNKNOWN for p in (*contact.ports, *coil.ports))
    assert [p.port_index for p in contact.ports] == [0, 1]
    assert contact.ports[0].net_index == mov.enable_in.net_index
    assert coil.ports[1].net_index == mov.enable_out.net_index
    assert [issue.code for issue in model.issues] == ["ladder_port_order"] * 2


@pytest.mark.parametrize("kind", [NodeKind.CONTACT, NodeKind.COIL])
@pytest.mark.parametrize("port_count", [0, 1, 3])
def test_synthetic_unobserved_ladder_port_counts_preserve_node_and_all_ports(kind, port_count):
    program = _program(54)
    node = next(node for node in program.nodes if node.kind == kind)
    ports = (node.ports + node.ports[:1])[:port_count]
    model = build_semantic_model(_replace_node(program, replace(node, ports=ports)))
    element = next(item for item in (*model.contacts, *model.coils) if item.node_offset == node.offset)

    assert len(element.ports) == port_count
    assert element.execution_in is None
    assert all(port.role == SemanticPortRole.UNKNOWN for port in element.ports)
    assert any(
        issue.code == "ladder_port_count" and issue.node_offset == node.offset
        for issue in model.issues
    )


@pytest.mark.parametrize("layout", ["off_edge", "same_side", "misaligned", "outside_bbox"])
def test_synthetic_unsupported_ladder_layout_is_not_guessed(layout):
    program = _program(54)
    node = next(node for node in program.nodes if node.kind == NodeKind.CONTACT)
    left, right = node.ports
    bbox = node.bbox
    if layout == "off_edge":
        left = replace(left, local_x=1)
    elif layout == "same_side":
        right = replace(right, local_x=0)
    elif layout == "misaligned":
        bbox = replace(bbox, bottom=bbox.bottom + 2)
        right = replace(right, local_y=2)
    else:
        left = replace(left, local_y=0)
        right = replace(right, local_y=0)
    model = build_semantic_model(_replace_node(program, replace(node, bbox=bbox, ports=(left, right))))
    contact, = model.contacts

    assert contact.polarity == ContactPolarity.UNKNOWN
    assert contact.edge == LadderEdge.UNKNOWN
    assert contact.execution_in is None
    assert contact.execution_out is None
    assert [port.role for port in contact.ports] == [SemanticPortRole.UNKNOWN] * 2
    assert any(issue.code == "ladder_port_layout" for issue in model.issues)


@pytest.mark.parametrize("kind,index,code", [
    (NodeKind.CONTACT, 0, 2),
    (NodeKind.CONTACT, 1, 0),
    (NodeKind.COIL, 0, 9),
    (NodeKind.COIL, 1, 2),
])
def test_synthetic_unsupported_port_codes_do_not_borrow_function_or_contact_roles(kind, index, code):
    program = _program(54)
    node = next(node for node in program.nodes if node.kind == kind)
    ports = list(node.ports)
    ports[index] = replace(ports[index], port_kind_code=code)
    model = build_semantic_model(_replace_node(program, replace(node, ports=tuple(ports))))
    element = next(item for item in (*model.contacts, *model.coils) if item.node_offset == node.offset)

    assert element.ports[index].role == SemanticPortRole.UNKNOWN
    assert element.ports[index].port_kind_code == code
    assert any(
        (issue.code, issue.node_offset, issue.port_index) == ("unknown_ladder_port", node.offset, index)
        for issue in model.issues
    )


def test_synthetic_connected_coil_right_port_keeps_terminal_binding_and_unknown_role():
    program = _program(54)
    sink = next(node for node in program.nodes if node.kind == NodeKind.OUTPUT)
    # Move D1 to the coil's right port. A connection alone proves no execution role.
    program = _replace_node(program, replace(sink, bbox=Rect(31, 1, 33, 3)))
    model = build_semantic_model(program)
    coil, = model.coils
    terminal = next(terminal for terminal in model.terminals if terminal.symbol == "D1")

    assert coil.ports[1].role == SemanticPortRole.UNKNOWN
    assert coil.ports[1].net_index == terminal.net_index
    assert coil.ports[1].terminal_node_offsets == (sink.offset,)
    assert coil.ports[1].terminal_symbols == ("D1",)
    assert any(issue.code == "unmodeled_coil_output" for issue in model.issues)


@pytest.mark.parametrize("symbol", ["label_name", "T0", "C0"])
def test_synthetic_symbols_do_not_change_contact_or_coil_kind(symbol):
    program = _program(54)
    for node in program.nodes:
        if node.kind in {NodeKind.CONTACT, NodeKind.COIL}:
            program = _replace_node(program, replace(node, symbol=symbol))
    model = build_semantic_model(program)
    assert model.contacts[0].symbol == model.coils[0].symbol == symbol
    assert model.contacts[0].polarity == ContactPolarity.NORMALLY_OPEN
    assert model.coils[0].role == CoilRole.NORMAL
    assert {terminal.symbol for terminal in model.terminals} == {"10", "D1"}


@pytest.mark.parametrize("symbol", ["fb_instance", "TON", "CTU"])
def test_synthetic_unknown_kinds_remain_unmodeled_even_with_block_like_symbols(symbol):
    program = _program(54)
    node = next(node for node in program.nodes if node.kind == NodeKind.COIL)
    modified = replace(node, kind=NodeKind.UNKNOWN, kind_code=0xFE, symbol=symbol)
    model = build_semantic_model(_replace_node(program, modified))

    assert model.coils == ()
    assert len(model.unmodeled_nodes) == 1
    unmodeled, = model.unmodeled_nodes
    assert (unmodeled.node_offset, unmodeled.kind, unmodeled.symbol) == (node.offset, NodeKind.UNKNOWN, symbol)
    assert [function.serialized_symbol for function in model.functions] == ["MOV"]


_PRIMITIVE_FIXTURE = json.loads(
    (_FIXTURE_DIR / "gxw_ladder_primitives.json").read_text(encoding="utf-8")
)
_PRIMITIVE_CASES = _PRIMITIVE_FIXTURE["cases"]


@pytest.mark.parametrize("case", _PRIMITIVE_FIXTURE["port_order_failures"], ids=lambda row: row["case"])
def test_native_rejected_port_order_preserves_raw_ports_without_execution_claims(case):
    from hashlib import sha256

    raw = base64.b64decode(case["program_base64"])
    assert sha256(raw).hexdigest() == case["program_sha256"]
    assert not case["native_exported"]
    assert any(d["kind"] == 2 for d in case["native_diagnostics"])
    program = parse_structured_pou(raw, logical_name="1.Program.pou")
    model = build_semantic_model(program)
    element = model.contacts[0] if case["role"] == "contact" else model.coils[0]
    node = next(n for n in program.nodes if n.offset == element.node_offset)
    assert element.execution_in is None
    assert element.edge == LadderEdge.UNKNOWN
    assert all(p.role == SemanticPortRole.UNKNOWN for p in element.ports)
    assert [p.port_kind_code for p in element.ports] == [p.port_kind_code for p in node.ports]
    assert [p.point for p in element.ports] == [node.port_point(i) for i in range(2)]
    assert any(i.code == "ladder_port_order" and i.node_offset == node.offset for i in model.issues)


@pytest.mark.parametrize("case", _PRIMITIVE_CASES, ids=lambda row: row["case"])
def test_ladder_modifiers_agree_with_independent_native_instruction_listing(case):
    """Native listing, not the Core mapping table, supplies the expected effect."""
    from hashlib import sha256

    raw = base64.b64decode(case["program_base64"])
    assert sha256(raw).hexdigest() == case["program_sha256"]
    assert sha256(base64.b64decode(case["pcode_base64"])).hexdigest() == case["pcode_sha256"]
    program = parse_structured_pou(raw, logical_name="1.Program.pou")
    model = build_semantic_model(program)
    contact, = model.contacts
    coil, = model.coils
    element = contact if case["role"] == "contact" else coil
    instructions = [r for r in case["native_records"] if r["kind"] == "instruction"]
    load = instructions[0]
    assert load["args"] == ["X001"]
    expected_edge = {"LD": LadderEdge.NONE, "LDI": LadderEdge.NONE,
                     "LDP": LadderEdge.RISING, "LDF": LadderEdge.FALLING}[load["op"]]
    negated = load["op"] == "LDI" or instructions[1]["op"] == "INV"
    output = next(r for r in instructions if r["op"] in {"OUT", "SET", "RST"})
    assert output["args"] == ["Y001"]
    assert element.symbol == case["target_symbol"]
    assert element.execution_in.port_kind_code == case["port_code"]
    assert element.edge == expected_edge
    if case["role"] == "contact":
        assert element.polarity == (ContactPolarity.NORMALLY_CLOSED if negated
                                    else ContactPolarity.NORMALLY_OPEN)
        assert coil.role == CoilRole.NORMAL and coil.edge == LadderEdge.NONE
    else:
        expected_role = {"SET": CoilRole.SET, "RST": CoilRole.RESET}.get(
            output["op"], CoilRole.NEGATED if negated else CoilRole.NORMAL,
        )
        assert element.role == expected_role
        assert contact.polarity == ContactPolarity.NORMALLY_OPEN and contact.edge == LadderEdge.NONE
    assert contact.execution_out.net_index == coil.execution_in.net_index
    assert model.unmodeled_nodes == ()
    assert [issue.code for issue in model.issues] == ["unmodeled_coil_output"]


@pytest.mark.parametrize("kind,code", [
    (NodeKind.CONTACT, 51), (NodeKind.CONTACT_NC, 27),
    (NodeKind.CONTACT_RISING, 19), (NodeKind.CONTACT_FALLING, 35),
    (NodeKind.COIL, 259), (NodeKind.COIL_SET, 67), (NodeKind.COIL_RESET, 131),
])
def test_unobserved_modifier_combinations_remain_unknown(kind, code):
    from src.gxw.models import node_kind_from_code, CONTACT_NODE_KINDS

    case = next(row for row in _PRIMITIVE_CASES if node_kind_from_code(row["kind"]) == kind)
    program = parse_structured_pou(base64.b64decode(case["program_base64"]))
    node = next(n for n in program.nodes if n.symbol == case["target_symbol"])
    ports = (replace(node.ports[0], port_kind_code=code), node.ports[1])
    model = build_semantic_model(_replace_node(program, replace(node, ports=ports)))
    element = model.contacts[0] if kind in CONTACT_NODE_KINDS else model.coils[0]
    assert element.execution_in is None
    assert element.edge == LadderEdge.UNKNOWN
    assert (element.polarity if kind in CONTACT_NODE_KINDS else element.role).value == "unknown"
    assert element.ports[0].port_kind_code == code
    assert any(issue.code == "unknown_ladder_port" for issue in model.issues)
