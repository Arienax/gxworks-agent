"""Raw-preserved index and primary link map from observed ESCompiler storage.

These are stored compiler artifacts, not authoritative current source. Index
entries resolve numbered streams; link ranges can be checked against their
actual token bytes. Unresolved fields and secondary map sections stay raw.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError


@dataclass(frozen=True)
class CompilerIndexEntry:
    offset: int
    stream_id: int
    kind_code: int
    name_bytes: bytes
    raw: bytes

    @property
    def stream_name(self) -> str:
        return f"{self.stream_id:08X}"

    def display_name(self, encoding: str) -> str:
        return self.name_bytes.decode(encoding)


@dataclass(frozen=True)
class CompilerIndex:
    raw: bytes
    entries: tuple[CompilerIndexEntry, ...]

    def reconstruct(self) -> bytes:
        return self.raw[:4] + b"".join(e.raw for e in self.entries)


@dataclass(frozen=True)
class CompilerLinkEntry:
    offset: int
    fields: tuple[int, ...]
    name_bytes: bytes
    raw: bytes

    @property
    def step_offset(self) -> int:
        return self.fields[4]

    @property
    def step_count(self) -> int:
        return self.fields[5]

    @property
    def token_offset(self) -> int:
        return self.fields[6]

    @property
    def token_length(self) -> int:
        return self.fields[7]

    @property
    def unlinked(self) -> bool:
        # ECCompiler.dll 15.22: GetPCodeInfo/GetUnLinkedPCodeInfo pass 0/1
        # through RVA 0x2ED50 to 0x2D750; RVA 0x2D7EF tests field +0x24 bit 15.
        # Retain all other flags; this does not prove source/cache freshness.
        return bool(self.fields[9] & 0x8000)


@dataclass(frozen=True)
class CompilerLinkMap:
    raw: bytes
    entries: tuple[CompilerLinkEntry, ...]
    tail_offset: int

    @property
    def opaque_tail(self) -> bytes:
        return self.raw[self.tail_offset:]

    def reconstruct(self) -> bytes:
        return self.raw[:4] + b"".join(e.raw for e in self.entries) + self.opaque_tail


def _count(raw: bytes, minimum_record: int) -> int:
    if len(raw) < 4:
        raise GXWFormatError("truncated compiler table count")
    count = struct.unpack_from("<I", raw)[0]
    if count > (len(raw) - 4) // minimum_record:
        raise GXWFormatError("compiler table count exceeds its bytes")
    return count


def _name(raw: bytes, cursor: int, *, allow_empty: bool = False) -> tuple[bytes, int]:
    if cursor + 4 > len(raw):
        raise GXWFormatError("truncated compiler name length")
    length = struct.unpack_from("<I", raw, cursor)[0]
    if length == 0 and allow_empty:
        return b"", cursor + 4
    end = cursor + 4 + length
    if length < 1 or end > len(raw):
        raise GXWFormatError("compiler name exceeds its bytes")
    value = raw[cursor + 4:end]
    if value[-1] != 0 or b"\0" in value[:-1]:
        raise GXWFormatError("unsupported compiler name framing")
    return value[:-1], end


def parse_compiler_index(raw: bytes) -> CompilerIndex:
    raw = bytes(raw)
    count, cursor, entries = _count(raw, 13), 4, []
    for _ in range(count):
        start = cursor
        if cursor + 12 > len(raw):
            raise GXWFormatError("truncated compiler index entry")
        stream_id, kind = struct.unpack_from("<II", raw, cursor)
        name, cursor = _name(raw, cursor + 8)
        entries.append(CompilerIndexEntry(start, stream_id, kind, name, raw[start:cursor]))
    if cursor != len(raw):
        raise GXWFormatError("unsupported compiler index suffix")
    # Repeated IDs/names remain explicit entries; callers must resolve ambiguity.
    return CompilerIndex(raw, tuple(entries))


def parse_compiler_link_map(raw: bytes) -> CompilerLinkMap:
    """Read the primary counted entries; keep all following sections opaque.

    Stored ranges may describe excluded/cached fragments as well as linked
    code. The numeric fields alone do not establish membership or freshness.
    """
    raw = bytes(raw)
    # IEC compiler Link can emit a nameless 44-byte FEND placeholder when no
    # FEND fragment was compiled. Its zero name length has no NUL byte.
    count, cursor, entries = _count(raw, 44), 4, []
    for _ in range(count):
        start = cursor
        if cursor + 44 > len(raw):
            raise GXWFormatError("truncated compiler link entry")
        fields = struct.unpack_from("<10I", raw, cursor)
        name, cursor = _name(raw, cursor + 40, allow_empty=True)
        entries.append(CompilerLinkEntry(start, fields, name, raw[start:cursor]))
    return CompilerLinkMap(raw, tuple(entries), cursor)
