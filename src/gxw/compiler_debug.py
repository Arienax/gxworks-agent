"""Observed version-3 DebugInformation2 sections, retaining the search-map tail.

Field order follows DZDataABS_DataManager's LocalLoadOffsetTable and
LocalLoadPOUElementLocationInfo. Offset table rows are sentinel-terminated;
the stored allocation size is not a safe framing count. No source freshness
or source-language coordinate meaning is inferred from these stored records.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError


@dataclass(frozen=True)
class CompilerDebugOffsets:
    offset: int
    kind_code: int
    declared_size: int
    rows: tuple[tuple[int, ...], ...]
    sentinel: tuple[int, ...]
    raw: bytes


@dataclass(frozen=True)
class CompilerDebugElement:
    offset: int
    resource_bytes: bytes
    fields: tuple[int, ...]
    names: tuple[bytes, ...]
    kind_code: int
    extra_name: bytes
    offset_table_index: int
    raw: bytes

    @property
    def linked_step_start(self) -> int:
        return self.fields[2]

    @property
    def linked_step_end(self) -> int:
        # Stored endpoint, not a universal half-open/inclusive source span.
        # Two observed editor snapshots extend to the following FEND position.
        return self.fields[3]


@dataclass(frozen=True)
class CompilerDebug:
    raw: bytes
    offset_tables: tuple[CompilerDebugOffsets, ...]
    elements: tuple[CompilerDebugElement, ...]
    element_count_offset: int
    tail_offset: int

    @property
    def opaque_tail(self) -> bytes:
        return self.raw[self.tail_offset:]

    def reconstruct(self) -> bytes:
        return (self.raw[:8] + b"".join(t.raw for t in self.offset_tables)
                + self.raw[self.element_count_offset:self.element_count_offset + 4]
                + b"".join(e.raw for e in self.elements) + self.opaque_tail)


class _Reader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.cursor = 0

    def integers(self, count=1) -> tuple[int, ...]:
        end = self.cursor + 4 * count
        if end > len(self.raw):
            raise GXWFormatError("truncated compiler debug fields")
        result = struct.unpack_from("<" + "i" * count, self.raw, self.cursor)
        self.cursor = end
        return result

    def integer(self) -> int:
        return self.integers()[0]

    def count(self, minimum: int) -> int:
        value = self.integer()
        if value < 0 or value > (len(self.raw) - self.cursor) // minimum:
            raise GXWFormatError("compiler debug count exceeds input")
        return value

    def name(self) -> bytes:
        size = self.integer()
        end = self.cursor + size
        if size < 1 or end > len(self.raw):
            raise GXWFormatError("compiler debug name exceeds input")
        value = self.raw[self.cursor:end]
        self.cursor = end
        if value[-1] != 0 or b"\0" in value[:-1]:
            raise GXWFormatError("unsupported compiler debug name framing")
        return value[:-1]


def parse_compiler_debug(raw: bytes) -> CompilerDebug:
    raw = bytes(raw)
    reader, offsets, elements = _Reader(raw), [], []
    if reader.integer() != 3:
        raise GXWFormatError("unsupported compiler debug version")
    for _ in range(reader.count(32)):
        start = reader.cursor
        kind, declared_size = reader.integers(2)
        rows = []
        while True:
            row = reader.integers(6)
            if row[0] == -1:
                break
            rows.append(row)
        offsets.append(CompilerDebugOffsets(start, kind, declared_size, tuple(rows), row, raw[start:reader.cursor]))
    element_count_offset = reader.cursor
    for _ in range(reader.count(67)):
        start = reader.cursor
        resource = reader.name()
        fields = reader.integers(6)
        names = tuple(reader.name() for _ in range(5))
        kind, extra, index = reader.integer(), reader.name(), reader.integer()
        if not 0 <= index < len(offsets):
            raise GXWFormatError("compiler debug references missing offset table")
        elements.append(CompilerDebugElement(start, resource, fields, names, kind, extra, index, raw[start:reader.cursor]))
    return CompilerDebug(raw, tuple(offsets), tuple(elements), element_count_offset, reader.cursor)
