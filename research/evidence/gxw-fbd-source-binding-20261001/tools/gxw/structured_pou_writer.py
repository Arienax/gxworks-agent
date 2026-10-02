from __future__ import annotations

from dataclasses import replace
import struct
from typing import Optional

from .models import (
    GXWFormatError,
    NodeKind,
    Point,
    Rect,
    StructuredNode,
    StructuredProgram,
    StructuredWire,
    UnknownRecord,
)
from .structured_pou import (
    BLOCK_COUNT_OFFSET,
    PROGRAM_PREFIX_SIZE,
    BODY_SIZE_OFFSET,
    CANVAS_HEIGHT_OFFSET,
    RECORD_COUNT_OFFSET,
    STRUCTURED_RECORDS_OFFSET,
    parse_structured_pou,
)


SIZE_LIKE_A_OFFSET = 0x37
SIZE_LIKE_B_OFFSET = 0x3B


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise GXWFormatError(f"truncated uint32 at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def _serialize_node_string(value: str, *, field_name: str) -> bytes:
    if "\x00" in value:
        raise GXWFormatError(f"embedded NUL is unsupported in structured-node {field_name}")
    encoded = (value + "\x00").encode("utf-16le")
    char_count = len(encoded) // 2
    return struct.pack("<I", char_count) + encoded


def serialize_structured_node(node: StructuredNode) -> bytes:
    """Serialize one verified Structured Ladder/FBD node record.

    The implementation intentionally covers only layouts already accepted by the
    strict parser: ordinary nodes and the observed kind-0x02 function-block ABI.
    Record length is recomputed from the serialized fields rather than copied from
    ``node.record_length`` so variable-length symbol edits are supported.
    """

    body = bytearray()
    body.extend(struct.pack("<II", 1, node.kind_code))
    body.extend(_serialize_node_string(node.symbol, field_name="symbol"))

    if node.kind_code == 0x02:
        if node.type_name is None:
            raise GXWFormatError("function-block node is missing its serialized type name")
        if node.object_flag is not None or node.reserved is not None:
            raise GXWFormatError(
                "function-block node unexpectedly contains ordinary-node flag fields"
            )
        body.extend(_serialize_node_string(node.type_name, field_name="FB type"))
    else:
        if node.object_flag is None or node.reserved is None:
            raise GXWFormatError(
                f"ordinary node kind 0x{node.kind_code:02X} is missing flag/reserved fields"
            )
        body.extend(struct.pack("<IH", node.object_flag, node.reserved))

    body.extend(
        struct.pack(
            "<IIII",
            node.bbox.left,
            node.bbox.top,
            node.bbox.right,
            node.bbox.bottom,
        )
    )
    body.extend(struct.pack("<I", len(node.ports)))

    for port in node.ports:
        if port.size != 16:
            raise GXWFormatError(
                f"cannot serialize unsupported structured port size {port.size}"
            )
        body.extend(
            struct.pack(
                "<IIII",
                port.size,
                port.port_kind_code,
                port.local_x,
                port.local_y,
            )
        )

    record_length = 4 + len(body)
    return struct.pack("<I", record_length) + bytes(body)


def serialize_structured_wire(wire: StructuredWire) -> bytes:
    """Serialize one observed 44-byte wire record."""

    if wire.record_length != 44:
        raise GXWFormatError(
            f"cannot serialize unsupported structured wire length {wire.record_length}"
        )

    unknown0, unknown1, unknown2, unknown3, unknown4 = wire.prefix_fields
    return struct.pack(
        "<IIIIHHIIIIII",
        44,
        2,
        unknown0,
        unknown1,
        unknown2,
        unknown3,
        unknown4,
        wire.start.x,
        wire.start.y,
        wire.end.x,
        wire.end.y,
        wire.suffix,
    )


def _validate_source_header(program: StructuredProgram) -> None:
    """Validate the immutable raw source behind a possibly mutated model.

    Structural mutations intentionally change ``record_count`` and ``body_size`` on
    the dataclass before serialization. Therefore this guard validates the original
    ``program.raw`` header against itself rather than requiring the mutated semantic
    fields to still equal the source values.
    """

    raw = program.raw
    if len(raw) < STRUCTURED_RECORDS_OFFSET:
        raise GXWFormatError("StructuredProgram raw source is shorter than its header")

    parse_structured_pou(raw, logical_name=program.logical_name, source_path=program.source_path)

    # This relation is strongly repeated across the controlled Structured
    # Ladder/FBD corpus. The writer intentionally fails closed rather than
    # inventing values for a project variant where the invariant does not hold.
    expected_size_like = len(raw) - 83
    for offset in (SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET):
        actual = _u32(raw, offset)
        if actual != expected_size_like:
            raise GXWFormatError(
                "unsupported Structured Program header invariant: "
                f"0x{offset:X}=0x{actual:X}, expected len-83 "
                f"(0x{expected_size_like:X})"
            )


def serialize_structured_pou(
    program: StructuredProgram,
    *,
    verify: bool = True,
) -> bytes:
    """Serialize the currently verified Structured Ladder/FBD Program.pou model.

    Unknown record classes are preserved byte-for-byte. Known node and wire records
    are rebuilt from parsed fields. The original header is retained except for the
    verified size/count/height fields that must follow the rebuilt body.

    With an unmodified parsed program this function is expected to produce a
    byte-perfect round trip for the controlled corpus.
    """

    _validate_source_header(program)

    containers = []
    total_records = 0
    for block, records in program.block_records():
        serialized_records = []
        for record in records:
            if isinstance(record, StructuredNode):
                serialized_records.append(serialize_structured_node(record))
            elif isinstance(record, StructuredWire):
                serialized_records.append(serialize_structured_wire(record))
            elif isinstance(record, UnknownRecord):
                if len(record.raw) != record.record_length or len(record.raw) < 8 or (
                    _u32(record.raw, 0), _u32(record.raw, 4)
                ) != (record.record_length, record.record_class):
                    raise GXWFormatError("opaque record framing disagrees with raw source")
                serialized_records.append(record.raw)
            else:
                raise GXWFormatError(f"unsupported structured record: {type(record).__name__}")
        if len(block.raw_header) != 24:
            raise GXWFormatError("block requires a preserved 24-byte native header")
        body = b"".join(serialized_records)
        header = bytearray(block.raw_header)
        struct.pack_into("<I", header, 0, 24 + len(body))
        struct.pack_into("<I", header, 16, block.canvas_height)
        struct.pack_into("<I", header, 20, len(serialized_records))
        containers.append(bytes(header) + body)
        total_records += len(serialized_records)
    if len(program.trailer) != 24 or any(program.trailer):
        raise GXWFormatError("writer only supports the observed 24-zero-byte trailer")
    header = bytearray(program.raw[:PROGRAM_PREFIX_SIZE])
    body = b"".join(containers) + program.trailer
    size_like = len(header) + len(body) - 83
    struct.pack_into("<I", header, SIZE_LIKE_A_OFFSET, size_like)
    struct.pack_into("<I", header, SIZE_LIKE_B_OFFSET, size_like)
    struct.pack_into("<I", header, BLOCK_COUNT_OFFSET, len(containers))
    result = bytes(header) + body
    if verify:
        reparsed = parse_structured_pou(result, logical_name=program.logical_name, source_path=program.source_path)
        if reparsed.record_count != total_records or len(reparsed.blocks) != len(containers):
            raise GXWFormatError("serialized Program.pou failed block/record-count verification")

    return result


def replace_node_symbol(
    program: StructuredProgram,
    old_symbol: str,
    new_symbol: str,
    *,
    node_offset: Optional[int] = None,
) -> StructuredProgram:
    """Return a copy with exactly one parsed node symbol replaced.

    ``node_offset`` refers to the node offset in the source Program.pou and is only
    needed when the same symbol occurs in multiple nodes.
    """

    matches = [
        (index, node)
        for index, node in enumerate(program.nodes)
        if node.symbol == old_symbol
        and (node_offset is None or node.offset == node_offset)
    ]

    if not matches:
        suffix = f" at offset 0x{node_offset:X}" if node_offset is not None else ""
        raise GXWFormatError(f"no structured node with symbol {old_symbol!r}{suffix}")

    if len(matches) > 1:
        offsets = ", ".join(f"0x{node.offset:X}" for _, node in matches)
        raise GXWFormatError(
            f"symbol {old_symbol!r} occurs in multiple nodes ({offsets}); "
            "specify node_offset"
        )

    index, node = matches[0]
    nodes = list(program.nodes)
    nodes[index] = replace(node, symbol=new_symbol)
    return replace(program, nodes=tuple(nodes))


def insert_series_contact_after(
    program: StructuredProgram,
    after_symbol: str,
    new_symbol: str,
    *,
    node_offset: Optional[int] = None,
    horizontal_gap: int = 3,
) -> StructuredProgram:
    """Insert one normally-open contact into a verified simple horizontal series wire.

    This is intentionally the first narrow structure-edit primitive. It clones the
    ABI of an existing normally-open contact, places the new contact to its right,
    splits the single outgoing horizontal wire into two conductors, and preserves
    all still-unknown fields from the source contact/wire records.

    If the existing downstream coil is too close to fit the cloned contact, the
    controlled simple-rung case shifts that one coil to the right by exactly the
    inserted contact's horizontal footprint. This preserves the original gap between
    the source contact and the downstream coil instead of guessing a new layout.

    The controlled target is sample 48 ``X1 -> Y1`` becoming
    ``X1 -> X2 -> Y1``. More general autorouting is deliberately out of scope.
    """

    if len(program.blocks) != 1:
        raise GXWFormatError("series insertion requires one selected block")

    matches = [
        node
        for node in program.nodes
        if node.symbol == after_symbol
        and (node_offset is None or node.offset == node_offset)
    ]
    if not matches:
        suffix = f" at offset 0x{node_offset:X}" if node_offset is not None else ""
        raise GXWFormatError(f"no structured node with symbol {after_symbol!r}{suffix}")
    if len(matches) > 1:
        offsets = ", ".join(f"0x{node.offset:X}" for node in matches)
        raise GXWFormatError(
            f"symbol {after_symbol!r} occurs in multiple nodes ({offsets}); "
            "specify node_offset"
        )

    source = matches[0]
    if source.kind != NodeKind.CONTACT or source.kind_code != 0x03:
        raise GXWFormatError(
            "first structure-insertion milestone only clones a normally-open contact"
        )
    if len(source.ports) != 2:
        raise GXWFormatError("source contact does not have the verified two-port ABI")
    if program.unknown_records or [p.port_kind_code for p in source.ports] != [3, 2]:
        raise GXWFormatError("series insertion requires a known NO contact ABI and modeled records")
    if horizontal_gap < 1:
        raise GXWFormatError("horizontal_gap must be at least one grid unit")

    left_port = source.port_point(0)
    right_port = source.port_point(1)
    if left_port.y != right_port.y or left_port.x >= right_port.x:
        raise GXWFormatError("source contact ports are not the verified left-to-right geometry")

    outgoing: list[tuple[StructuredWire, Point]] = []
    for wire in program.wires:
        if wire.start == right_port:
            outgoing.append((wire, wire.end))
        elif wire.end == right_port:
            outgoing.append((wire, wire.start))

    if len(outgoing) != 1:
        raise GXWFormatError(
            "series insertion requires exactly one explicit wire connected to the "
            f"source contact right port; found {len(outgoing)}"
        )

    target_wire, far_end = outgoing[0]
    from .connectivity import build_connectivity_graph
    net = build_connectivity_graph(program).net_for_port(source.offset, 1)
    if len(net.ports) != 2 or set(net.wire_offsets) != {target_wire.offset}:
        raise GXWFormatError("series insertion requires an untapped two-port outgoing net")
    if far_end.y != right_port.y or far_end.x <= right_port.x:
        raise GXWFormatError(
            "series insertion currently requires a horizontal outgoing wire to the right"
        )

    width = source.bbox.right - source.bbox.left
    height = source.bbox.bottom - source.bbox.top
    new_left_x = source.bbox.right + horizontal_gap
    new_bbox = Rect(
        new_left_x,
        source.bbox.top,
        new_left_x + width,
        source.bbox.top + height,
    )

    new_left_port = source.ports[0].absolute_point(new_bbox)
    new_right_port = source.ports[1].absolute_point(new_bbox)
    if not (
        right_port.x < new_left_port.x < new_right_port.x
        and new_left_port.y == right_port.y
        and new_right_port.y == right_port.y
    ):
        raise GXWFormatError("cloned contact geometry is not a verified horizontal placement")

    nodes = [node for node in program.nodes]

    # Real sample 48 keeps Y1 closer to X1 than the sample-51-derived synthetic
    # baseline used in the first unit test. If the inserted contact would collide
    # with that downstream endpoint, conservatively support only the simple case:
    # exactly one downstream coil port at the far endpoint and no other explicit
    # wire attached to that coil. Shift the coil by the inserted contact footprint,
    # which preserves the original wire gap after the new contact.
    if new_right_port.x >= far_end.x:
        downstream_matches: list[tuple[int, StructuredNode, int]] = []
        for index, node in enumerate(nodes):
            if node is source:
                continue
            for port_index in range(len(node.ports)):
                if node.port_point(port_index) == far_end:
                    downstream_matches.append((index, node, port_index))

        if len(downstream_matches) != 1:
            raise GXWFormatError(
                "not enough horizontal room and the downstream endpoint does not map "
                "to exactly one movable node port"
            )

        downstream_index, downstream, downstream_port_index = downstream_matches[0]
        if downstream.kind != NodeKind.COIL or downstream.kind_code != 0x05:
            raise GXWFormatError(
                "not enough horizontal room; automatic downstream shifting is "
                "currently limited to one coil"
            )
        if downstream_port_index != 0:
            raise GXWFormatError(
                "controlled series insertion expects the outgoing wire to reach the "
                "downstream coil's left port"
            )

        downstream_points = {
            downstream.port_point(port_index)
            for port_index in range(len(downstream.ports))
        }
        other_incident = [
            wire
            for wire in program.wires
            if wire is not target_wire
            and (wire.start in downstream_points or wire.end in downstream_points)
        ]
        if other_incident:
            raise GXWFormatError(
                "downstream coil has additional explicit wiring; automatic shifting "
                "would require general rerouting"
            )

        shift_x = new_right_port.x - right_port.x
        shifted_bbox = Rect(
            downstream.bbox.left + shift_x,
            downstream.bbox.top,
            downstream.bbox.right + shift_x,
            downstream.bbox.bottom,
        )
        shifted_downstream = replace(downstream, bbox=shifted_bbox)
        nodes[downstream_index] = shifted_downstream
        far_end = shifted_downstream.port_point(downstream_port_index)

    if not (new_right_port.x < far_end.x and far_end.y == new_right_port.y):
        raise GXWFormatError(
            "not enough horizontal room on the existing wire for the cloned contact"
        )

    # Offsets on mutated objects are ordering keys until the serializer rebuilds
    # real byte offsets. Existing GX Works2 samples place node records before wires.
    clone = replace(
        source,
        offset=source.offset + 1,
        symbol=new_symbol,
        bbox=new_bbox,
    )

    first_segment = replace(
        target_wire,
        start=right_port,
        end=new_left_port,
    )
    second_segment = replace(
        target_wire,
        offset=target_wire.offset + 1,
        start=new_right_port,
        end=far_end,
    )

    nodes.append(clone)
    wires = [
        first_segment if wire is target_wire else wire
        for wire in program.wires
    ]
    wires.append(second_segment)

    added_body_bytes = len(serialize_structured_node(clone)) + len(
        serialize_structured_wire(second_segment)
    )
    return replace(
        program,
        nodes=tuple(nodes),
        wires=tuple(wires),
        record_count=program.record_count + 2,
        body_size=program.body_size + added_body_bytes,
    )
