from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Tuple


class GXWFormatError(ValueError):
    """Raised when a GXW/CFB structure cannot be parsed safely."""


class NodeKind(str, Enum):
    FUNCTION = "function"
    FUNCTION_BLOCK = "function_block"
    CONTACT = "contact"
    CONTACT_NC = "contact_nc"
    COIL = "coil"
    INPUT = "input"
    OUTPUT = "output"
    UNKNOWN = "unknown"


def node_kind_from_code(kind_code: int) -> NodeKind:
    return {
        0x01: NodeKind.FUNCTION,
        0x02: NodeKind.FUNCTION_BLOCK,
        0x03: NodeKind.CONTACT,
        0x04: NodeKind.CONTACT_NC,
        0x05: NodeKind.COIL,
        0x0D: NodeKind.INPUT,
        0x0E: NodeKind.OUTPUT,
    }.get(kind_code, NodeKind.UNKNOWN)


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def __str__(self) -> str:
        return f"({self.left},{self.top})-({self.right},{self.bottom})"


@dataclass(frozen=True)
class PortDescriptor:
    size: int
    port_kind_code: int
    local_x: int
    local_y: int
    raw: bytes = field(repr=False)

    def absolute_point(self, bbox: Rect) -> Point:
        """Return the observed editor-grid point for this port."""
        return Point(bbox.left + self.local_x, bbox.top + self.local_y)

    # Compatibility aliases for early experimental callers. New code should use
    # port_kind_code/local_x/local_y so the verified geometry is explicit while
    # the still-unknown first semantic field remains neutrally named.
    @property
    def field_a(self) -> int:
        return self.port_kind_code

    @property
    def field_b(self) -> int:
        return self.local_x

    @property
    def field_c(self) -> int:
        return self.local_y


@dataclass(frozen=True)
class StructuredNode:
    offset: int
    record_length: int
    kind_code: int
    kind: NodeKind
    symbol: str
    bbox: Rect
    ports: Tuple[PortDescriptor, ...]
    object_flag: Optional[int]
    reserved: Optional[int]
    raw: bytes = field(repr=False)
    # Kind 0x02 has a second string for the FB type, and no object_flag/reserved.
    # Its first string (symbol) is the instance name, not the type name.
    type_name: Optional[str] = None

    @property
    def instance_name(self) -> Optional[str]:
        return self.symbol if self.kind == NodeKind.FUNCTION_BLOCK else None

    def port_point(self, port_index: int) -> Point:
        return self.ports[port_index].absolute_point(self.bbox)


@dataclass(frozen=True)
class StructuredWire:
    offset: int
    record_length: int
    start: Point
    end: Point
    prefix_fields: Tuple[int, int, int, int, int]
    suffix: int
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class UnknownRecord:
    offset: int
    record_length: int
    record_class: int
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class StructuredBlock:
    """Ordered record membership with block-local coordinates.

    Offsets identify records in the source (ordering keys during edits), not
    native block IDs. The 12 unparsed header bytes remain in ``raw_header``.
    """
    offset: int
    byte_length: int
    canvas_height: int
    record_offsets: Tuple[int, ...]
    raw_header: bytes = field(repr=False)

    @property
    def record_count(self) -> int:
        return len(self.record_offsets)


@dataclass(frozen=True)
class StructuredProgram:
    logical_name: str
    source_path: Optional[Path]
    record_count: int
    canvas_height: int
    body_size: int
    nodes: Tuple[StructuredNode, ...]
    wires: Tuple[StructuredWire, ...]
    unknown_records: Tuple[UnknownRecord, ...]
    trailer: bytes = field(repr=False)
    raw: bytes = field(repr=False)
    blocks: Tuple[StructuredBlock, ...] = ()

    def iter_records(self) -> Iterable[object]:
        records = [*self.nodes, *self.wires, *self.unknown_records]
        return iter(sorted(records, key=lambda item: item.offset))

    def block_records(self):
        """Yield (block, records), preserving the existing single-block edit API.

        Flat node/wire collections own record data. Multi-block membership must
        partition them exactly; missing, repeated or foreign keys fail closed.
        In a single block all records belong to it, including legacy insertions.
        Single-block canvas_height remains the compatible program-level field.
        """
        records = tuple(self.iter_records())
        by_offset = {r.offset: r for r in records}
        if len(by_offset) != len(records):
            raise GXWFormatError("duplicate structured record ordering key")
        if not self.blocks:
            raise GXWFormatError("StructuredProgram has no explicit block envelope")
        if len(self.blocks) == 1:
            block = replace(self.blocks[0], canvas_height=self.canvas_height,
                            record_offsets=tuple(r.offset for r in records))
            yield block, records
            return
        assigned = [offset for b in self.blocks for offset in b.record_offsets]
        if len(assigned) != len(set(assigned)) or set(assigned) != set(by_offset):
            raise GXWFormatError("block membership must cover every record exactly once")
        for block in self.blocks:
            yield block, tuple(by_offset[offset] for offset in block.record_offsets)

    def block_views(self):
        """Single-block views for algorithms that operate on a local canvas."""
        for block, records in self.block_records():
            yield replace(self, blocks=(block,), canvas_height=block.canvas_height,
                          record_count=len(records), body_size=block.byte_length,
                          nodes=tuple(r for r in records if isinstance(r, StructuredNode)),
                          wires=tuple(r for r in records if isinstance(r, StructuredWire)),
                          unknown_records=tuple(r for r in records if isinstance(r, UnknownRecord)))
