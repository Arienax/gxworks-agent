# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Optional

from .models import (
    GXWFormatError,
    node_kind_from_code,
    Point,
    PortDescriptor,
    Rect,
    StructuredBlock,
    StructuredNode,
    StructuredProgram,
    StructuredWire,
    UnknownRecord,
)


PROGRAM_PREFIX_SIZE = 71
BLOCK_HEADER_SIZE = 24
BLOCK_COUNT_OFFSET = 0x43
# Compatibility offsets for the first block.
STRUCTURED_RECORDS_OFFSET = 0x5F
BODY_SIZE_OFFSET = 0x47
CANVAS_HEIGHT_OFFSET = 0x57
RECORD_COUNT_OFFSET = 0x5B
OBSERVED_TRAILER_SIZE = 24
# Original native captures include zero and this opaque retained value after
# editing the left rail in GX Works2 1.635.0.1. Preserve it without assigning
# PLC semantics or accepting arbitrary trailer words.
OBSERVED_STRUCTURED_TRAILERS = frozenset((bytes(24), struct.pack('<I', 2) + bytes(20)))


def structured_block_text(header: bytes) -> tuple[str, str]:
    """Read the two native counted UTF-16 network strings."""
    if structured_block_header_end(header, 0, len(header)) != len(header):
        raise GXWFormatError('incomplete structured block header')
    cursor, values = 4, []
    for _ in range(2):
        count = _u32(header, cursor)
        stop = cursor + 4 + count * 2
        try:
            value = header[cursor+4:stop-2].decode('utf-16le')
        except UnicodeError as error:
            raise GXWFormatError('invalid structured block UTF-16 text') from error
        if '\0' in value:
            raise GXWFormatError('embedded NUL in structured block text')
        values.append(value)
        cursor = stop
    return tuple(values)


def edit_structured_block_text(header: bytes, *, title=None, comment=None) -> bytes:
    old = structured_block_text(header)
    fields = []
    cursor = 4
    for index, value in enumerate((title, comment)):
        count = _u32(header, cursor)
        stop = cursor + 4 + count * 2
        if value is None or value == old[index]:
            fields.append(header[cursor:stop])
        else:
            if not isinstance(value, str) or '\0' in value:
                raise GXWFormatError('structured block text must be a string without NUL')
            try:
                encoded = (value+'\0').encode('utf-16le')
            except UnicodeError as error:
                raise GXWFormatError('invalid structured block UTF-16 text') from error
            fields.append(struct.pack('<I',len(encoded)//2)+encoded)
        cursor = stop
    return header[:4] + b''.join(fields) + header[-8:]


def structured_block_header_end(data: bytes, start: int, end: int) -> int:
    """Bound the two saved network strings before its height/record count.

    Network labels and comments are counted UTF-16 strings, just as an empty
    network has two one-character NUL strings. Their bytes are retained in the
    block header; they are not records or a fixed-size 24-byte prefix.
    """
    cursor = start + 4
    for _ in range(2):
        if cursor + 4 > end:
            raise GXWFormatError('structured block string count is truncated')
        count = _u32(data, cursor)
        stop = cursor + 4 + count * 2
        if count < 1 or stop > end - 8 or data[stop - 2:stop] != b'\0\0':
            raise GXWFormatError('unbounded structured block header string')
        cursor = stop
    if cursor + 8 > end:
        raise GXWFormatError('structured block height/count is truncated')
    return cursor + 8


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise GXWFormatError(f"truncated uint16 at 0x{offset:X}")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise GXWFormatError(f"truncated uint32 at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def _parse_node_string(
    record: bytes, cursor: int, absolute_offset: int, field_name: str
) -> tuple[str, int]:
    char_count = _u32(record, cursor)
    string_start = cursor + 4
    string_end = string_start + char_count * 2
    if char_count == 0 or string_end > len(record):
        raise GXWFormatError(
            f"invalid structured-node {field_name} length at 0x{absolute_offset:X}: {char_count}"
        )
    raw_symbol = record[string_start:string_end]
    try:
        symbol_with_nul = raw_symbol.decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise GXWFormatError(
            f"invalid UTF-16LE node {field_name} at 0x{absolute_offset:X}"
        ) from exc
    symbol = (
        symbol_with_nul[:-1]
        if symbol_with_nul.endswith("\x00")
        else symbol_with_nul
    )

    return symbol, string_end


def _parse_node(record: bytes, absolute_offset: int) -> StructuredNode:
    if len(record) < 42:
        raise GXWFormatError(f"structured node at 0x{absolute_offset:X} is too short")

    kind_code = _u32(record, 8)
    symbol, cursor = _parse_node_string(record, 12, absolute_offset, "symbol")
    if '\0' in symbol:
        raise GXWFormatError(f'embedded NUL in structured node symbol at 0x{absolute_offset:X}')
    type_name = None
    object_flag = None
    reserved = None
    if kind_code == 0x02:
        # Samples 72-75: instance string, type string, bbox, port count/ports.
        # There are no ordinary-node flag/reserved fields in this record form.
        type_name, cursor = _parse_node_string(record, cursor, absolute_offset, "FB type")
        if '\0' in type_name:
            raise GXWFormatError(f'embedded NUL in structured FB type at 0x{absolute_offset:X}')
    else:
        object_flag = _u32(record, cursor)
        cursor += 4
        reserved = _u16(record, cursor)
        cursor += 2
    if cursor + 16 > len(record):
        raise GXWFormatError(
            f"structured node bbox is truncated at 0x{absolute_offset:X}"
        )
    left, top, right, bottom = struct.unpack_from("<IIII", record, cursor)
    cursor += 16

    port_count = _u32(record, cursor)
    cursor += 4
    ports: List[PortDescriptor] = []
    for port_index in range(port_count):
        if cursor + 16 > len(record):
            raise GXWFormatError(
                f"structured node port {port_index} is truncated at 0x{absolute_offset:X}"
            )
        size, port_kind_code, local_x, local_y = struct.unpack_from(
            "<IIII", record, cursor
        )
        if size != 16:
            raise GXWFormatError(
                f"unexpected structured port size {size} at 0x{absolute_offset + cursor:X}"
            )
        raw_port = record[cursor : cursor + 16]
        ports.append(
            PortDescriptor(
                size=size,
                port_kind_code=port_kind_code,
                local_x=local_x,
                local_y=local_y,
                raw=raw_port,
            )
        )
        cursor += 16

    if cursor != len(record):
        raise GXWFormatError(
            f"unparsed bytes remain in structured node at 0x{absolute_offset:X}: "
            f"{len(record) - cursor}"
        )

    return StructuredNode(
        offset=absolute_offset,
        record_length=len(record),
        kind_code=kind_code,
        kind=node_kind_from_code(kind_code),
        symbol=symbol,
        bbox=Rect(left=left, top=top, right=right, bottom=bottom),
        ports=tuple(ports),
        object_flag=object_flag,
        reserved=reserved,
        raw=record,
        type_name=type_name,
    )


def _parse_wire(record: bytes, absolute_offset: int) -> StructuredWire:
    if len(record) != 44:
        raise GXWFormatError(
            f"unexpected structured wire length {len(record)} at 0x{absolute_offset:X}"
        )
    unknown0 = _u32(record, 8)
    unknown1 = _u32(record, 12)
    unknown2 = _u16(record, 16)
    unknown3 = _u16(record, 18)
    unknown4 = _u32(record, 20)
    start_x, start_y, end_x, end_y = struct.unpack_from("<IIII", record, 24)
    suffix = _u32(record, 40)
    record_class=_u32(record,4)
    if record_class not in (2,3):
        raise GXWFormatError('unsupported structured line record class')
    if record_class==3 and ((start_x,start_y)!=(end_x,end_y)
            or (unknown0,unknown1,unknown2,unknown3,unknown4,suffix)!=(0,1,0,1,0,0)):
        raise GXWFormatError('uncovered FIXPOINT layout')
    return StructuredWire(
        offset=absolute_offset,
        record_length=len(record),
        start=Point(start_x, start_y),
        end=Point(end_x, end_y),
        prefix_fields=(unknown0, unknown1, unknown2, unknown3, unknown4),
        suffix=suffix,
        raw=record,
        record_class=record_class,
    )


def parse_structured_pou(
    data: bytes,
    *,
    logical_name: str = "<Program.pou>",
    source_path: Optional[Path] = None,
    preserve_unsupported_records: bool = False,
) -> StructuredProgram:
    """Parse the observed GX Works2 Structured Ladder/FBD Program.pou format.

    Block and record boundaries are always checked against their saved sizes.
    The explicit inspection option preserves an undecodable *bounded record* as
    opaque. It also retains an unrecognized fixed 24-byte suffix without
    interpreting its options. Neither mode relaxes block/record boundaries;
    preservation is not permission to regenerate an opaque record or suffix.
    """

    if len(data) < STRUCTURED_RECORDS_OFFSET + OBSERVED_TRAILER_SIZE:
        raise GXWFormatError(
            "Program.pou is too short for the observed structured layout"
        )

    from .source_header import source_payload_offset
    payload = source_payload_offset(data)
    if data[payload] != 208:
        raise GXWFormatError('outside observed structured source language')
    prefix_size = payload + 17
    if len(data) < prefix_size + BLOCK_HEADER_SIZE + OBSERVED_TRAILER_SIZE:
        raise GXWFormatError('structured source body is truncated')
    block_count = _u32(data, payload + 13)
    if not 1 <= block_count <= (len(data) - prefix_size - OBSERVED_TRAILER_SIZE) // BLOCK_HEADER_SIZE:
        raise GXWFormatError("invalid or unsupported structured block count")
    cursor = prefix_size
    nodes: List[StructuredNode] = []
    wires: List[StructuredWire] = []
    unknown: List[UnknownRecord] = []
    blocks = []
    for block_index in range(block_count):
        start = cursor
        if start + BLOCK_HEADER_SIZE > len(data) - OBSERVED_TRAILER_SIZE:
            raise GXWFormatError("structured block header is truncated")
        size = _u32(data, start)
        end = start + size
        if size < BLOCK_HEADER_SIZE or end > len(data) - OBSERVED_TRAILER_SIZE:
            raise GXWFormatError("invalid structured block byte length")
        header_end = structured_block_header_end(data, start, end)
        count = _u32(data, header_end - 4)
        if count > (end - header_end) // 8:
            raise GXWFormatError("structured block record count exceeds capacity")
        cursor = header_end
        offsets = []
        for record_index in range(count):
            if cursor + 8 > end:
                raise GXWFormatError(f"block {block_index} record header is truncated")
            record_length = _u32(data, cursor)
            if record_length < 8 or cursor + record_length > end:
                raise GXWFormatError(f"record crosses block boundary at 0x{cursor:X}")
            record = data[cursor:cursor + record_length]
            record_class = _u32(record, 4)
            try:
                if record_class == 1:
                    nodes.append(_parse_node(record, cursor))
                elif record_class in (2,3):
                    wires.append(_parse_wire(record, cursor))
                else:
                    unknown.append(UnknownRecord(cursor, record_length, record_class, record))
            except GXWFormatError:
                if not preserve_unsupported_records:
                    raise
                unknown.append(UnknownRecord(cursor, record_length, record_class, record))
            offsets.append(cursor)
            cursor += record_length
        if cursor != end:
            raise GXWFormatError(f"unconsumed bytes inside block {block_index}")
        blocks.append(StructuredBlock(start, size, _u32(data, header_end - 8), tuple(offsets),
                                      data[start:header_end]))

    trailer = data[cursor:]
    if len(trailer) != OBSERVED_TRAILER_SIZE or (
            not preserve_unsupported_records and trailer not in OBSERVED_STRUCTURED_TRAILERS):
        raise GXWFormatError(
            "unsupported Program.pou trailer; expected an observed 24-byte trailer after records"
        )

    return StructuredProgram(
        logical_name=logical_name,
        source_path=source_path,
        record_count=sum(b.record_count for b in blocks),
        canvas_height=sum(b.canvas_height for b in blocks),
        body_size=sum(b.byte_length for b in blocks),
        nodes=tuple(nodes),
        wires=tuple(wires),
        unknown_records=tuple(unknown),
        trailer=trailer,
        raw=data,
        blocks=tuple(blocks),
    )
