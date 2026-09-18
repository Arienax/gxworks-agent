"""Small template-backed lowering using the existing ladder/PLC IR contract."""
from __future__ import annotations

from dataclasses import replace
import re

from .connectivity import build_connectivity_graph
from .models import GXWFormatError, NodeKind, Point, StructuredProgram
from .structured_pou import parse_structured_pou
from .structured_pou_writer import insert_series_contact_after, serialize_structured_pou


def normalize(program: StructuredProgram) -> StructuredProgram:
    return parse_structured_pou(serialize_structured_pou(program), logical_name=program.logical_name,
                                source_path=program.source_path)


def simple_rung_template(program: StructuredProgram):
    """Require the demonstrated NO contact -> coil + rail topology."""
    contacts = [n for n in program.nodes if n.kind == NodeKind.CONTACT]
    coils = [n for n in program.nodes if n.kind == NodeKind.COIL]
    if len(program.nodes) != 2 or len(contacts) != 1 or len(coils) != 1 or program.unknown_records:
        raise GXWFormatError("expected a simple one-contact/one-coil baseline")
    contact, coil = contacts[0], coils[0]
    if [p.port_kind_code for p in contact.ports] != [3, 2] or [p.port_kind_code for p in coil.ports] != [3, 0]:
        raise GXWFormatError("unsupported contact/coil template ABI")
    rails = [w for w in program.wires if w.start.x == w.end.x and w.start.y == 0
             and w.end.y == program.canvas_height and w.start.x < contact.bbox.left]
    graph = build_connectivity_graph(program)
    if len(rails) != 1 or len(program.wires) != 3:
        raise GXWFormatError("expected one rail and two feed wires")
    if not graph.ports_connected(contact.offset, 1, coil.offset, 0):
        raise GXWFormatError("template contact is not connected to its coil")
    if graph.net_for_wire(rails[0].offset).index != graph.net_for_port(contact.offset, 0).index:
        raise GXWFormatError("template input is not connected to its rail")
    if len(graph.nets) != 3:
        raise GXWFormatError("template has unexpected conductive nets")
    return contact, coil, rails[0]


def build_series_program(template: StructuredProgram, contacts: list[str], coil: str) -> StructuredProgram:
    """Generate NO series logic while retaining template header/record ABI."""
    source, output, _ = simple_rung_template(template)
    if not contacts or any(not isinstance(s, str) or not s or "\x00" in s for s in [*contacts, coil]):
        raise GXWFormatError("nonempty contact and coil symbols are required")
    program = replace(template, nodes=tuple(
        replace(n, symbol=contacts[0] if n.offset == source.offset else coil) for n in template.nodes))
    program = normalize(program)
    current_offset = next(n.offset for n in program.nodes if n.kind == NodeKind.CONTACT)
    for symbol in contacts[1:]:
        previous = next(n for n in program.nodes if n.offset == current_offset)
        modified = insert_series_contact_after(program, previous.symbol, symbol, node_offset=current_offset)
        # Find the new node by its ordering key before normalization (symbols may repeat).
        clone = next(n for n in modified.nodes if n.offset == current_offset + 1)
        program = normalize(modified)
        current_offset = next(n.offset for n in program.nodes if n.bbox == clone.bbox and n.kind == NodeKind.CONTACT)
    # raw remains bound to the original template for project-writer stale checks.
    return replace(program, raw=template.raw)


def structured_from_ladder(template: StructuredProgram, model: dict) -> StructuredProgram:
    """Lower the supported single series rung from existing ladder JSON or PLC IR.

    Unsupported branches, operators and annotations fail explicitly instead of
    silently producing a smaller/different program. This is not a new PLC IR.
    """
    from plc.ir import ir_to_ladder, is_plc_ir, validate_plc_ir, lower_rung_instructions
    from plc.validation import validate_ladder_full
    if is_plc_ir(model):
        validate_plc_ir(model)
        if model["plc"]["cpu"].upper() != "FX3U":
            raise GXWFormatError("current template lowering is validated only for FX3U")
    ladder = ir_to_ladder(model)
    validate_ladder_full(ladder, plc_model="FX3U")
    rungs = ladder.get("rungs", [])
    if len(rungs) != 1 or len(rungs[0].get("branches", [])) != 1:
        raise GXWFormatError("current semantic lowering supports one series rung with one branch")
    branch = rungs[0]["branches"][0]
    if rungs[0].get("header_element"):
        raise GXWFormatError("rung header instructions are not supported by series lowering")
    if ladder.get("device_comments") or rungs[0].get("debug_note"):
        raise GXWFormatError("comment/annotation lowering is not implemented")
    inputs = [*rungs[0].get("shared_inputs", []), *branch.get("inputs", [])]
    outputs = branch.get("outputs", [])
    if not inputs or len(outputs) != 1 or any(i.get("type") != "NO" for i in inputs):
        raise GXWFormatError("expected NO contacts and one ordinary OUT coil")
    if outputs[0].get("type") != "COIL":
        raise GXWFormatError("only ordinary COIL outputs are supported by this lowering")
    if any(element.get("label") for element in [*inputs, *outputs]):
        raise GXWFormatError("element label annotations are not implemented")
    addresses = [i.get("address", "") for i in inputs] + [outputs[0].get("address", "")]
    if any(not re.fullmatch(r"(?:[XY][0-7]+|M[0-9]+)", a) for a in addresses):
        raise GXWFormatError("current direct-device lowering supports octal X/Y and decimal M")
    if addresses[-1].startswith("X"):
        raise GXWFormatError("input X device cannot be an output coil")
    actual = [(i["op"], i["args"]) for i in lower_rung_instructions(rungs[0])]
    expected = [("LD" if i == 0 else "AND", [a]) for i, a in enumerate(addresses[:-1])]
    expected.append(("OUT", [addresses[-1]]))
    if actual != expected:
        raise GXWFormatError("source instruction semantics exceed supported series lowering")
    return build_series_program(template, addresses[:-1], addresses[-1])


def add_detached_node(program: StructuredProgram, kind: NodeKind, symbol: str) -> StructuredProgram:
    """Diagnostic object-count experiment; deliberately disconnected, not compile-ready."""
    matches = [n for n in program.nodes if n.kind == kind]
    if len(matches) != 1 or program.unknown_records:
        raise GXWFormatError("detached-node experiment needs one unambiguous template")
    source = matches[0]
    top = max([program.canvas_height] + [n.bbox.bottom for n in program.nodes]) + 2
    bbox = replace(source.bbox, top=top, bottom=top + source.bbox.bottom - source.bbox.top)
    key = max(r.offset for r in program.iter_records()) + 1
    return replace(program, nodes=(*program.nodes, replace(source, offset=key, symbol=symbol, bbox=bbox)),
                   canvas_height=max(program.canvas_height, bbox.bottom + 1))


def insert_parallel_contact(program: StructuredProgram, symbol: str) -> StructuredProgram:
    """Add a parallel contact on a clear new row, using sample-52 geometry."""
    source, coil, rail = simple_rung_template(program)
    dy = source.bbox.bottom - source.bbox.top + 1
    clone = replace(source, offset=max(n.offset for n in program.nodes) + 1, symbol=symbol,
                    bbox=replace(source.bbox, top=source.bbox.top + dy, bottom=source.bbox.bottom + dy))
    wire_template = next(w for w in program.wires if w is not rail)
    key = max(r.offset for r in program.iter_records()) + 1
    new_wires = tuple(replace(wire_template, offset=key + p, start=source.port_point(p), end=clone.port_point(p))
                      for p in range(2))
    height = max(program.canvas_height, clone.bbox.bottom)
    wires = tuple(replace(w, end=Point(w.end.x, height)) if w is rail else w for w in program.wires)
    return replace(program, nodes=(*program.nodes, clone), wires=(*wires, *new_wires), canvas_height=height)
