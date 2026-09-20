"""Lossless framing of the stored GX Works2 CallTree.dat maps.

DZDataABS_DataManager 1.635 Save/Load write three maps. A node contains
two integers, four CArchive strings and a list of serialized selectors.
These are cached references, not a claim about linked or executable code.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import GXWFormatError


@dataclass(frozen=True)
class CallTreeString:
    offset: int
    raw: bytes
    value_bytes: bytes
    character_width: int

    def text(self, encoding: str = "cp936") -> str:
        return self.value_bytes.decode("utf-16le" if self.character_width == 2 else encoding)

    @property
    def ascii_key(self) -> str | None:
        try:
            value = self.text("ascii")
        except UnicodeError:
            return None
        # Native keys use _mbsupr (with an alternate GB18030 path). Restrict
        # portable normalization to ASCII instead of guessing locale rules.
        return value.upper() if value.isascii() and "\0" not in value else None


@dataclass(frozen=True)
class CallTreeNode:
    offset: int
    raw: bytes
    flags: int
    kind_code: int
    names: tuple[CallTreeString, ...]
    references: tuple[CallTreeString, ...]

    @property
    def ascii_key(self) -> str | None:
        # Native selector formatter 0x30668/0x53B63 uses kind and the first
        # THREE names. The fourth stored string stays independent/raw.
        names = [name.ascii_key for name in self.names[:3]]
        if any(name is None for name in names):
            return None
        kind = self.kind_code if self.kind_code < 0x80000000 else self.kind_code - 0x100000000
        return f"{kind}, {names[0]}, {names[1]}, {names[2]}"


@dataclass(frozen=True)
class CallTreeReference:
    map_index: int
    node_offset: int
    reference_offset: int
    source_key: str | None
    target_key: str | None


@dataclass(frozen=True)
class CompilerCallTree:
    raw: bytes
    maps: tuple[tuple[CallTreeNode, ...], ...]
    resource_offset: int
    resource_tree: bytes | None
    opaque_tail: bytes

    def reconstruct(self) -> bytes:
        return self.raw

    def references(self, map_index: int) -> tuple[CallTreeReference, ...]:
        """Orient reference-map and used-by-map edges in the same direction.

        LocalGetCallTreeData selects map 1 for kind >= 21; LocalGetUsedByData
        selects map 2 for these POUs/instances. It uses map 0 for pool nodes.
        The two indexes can disagree; this method never merges or repairs them.
        """
        if map_index not in (0, 1, 2):
            raise GXWFormatError("compiler call-tree map index must be 0, 1 or 2")
        result = []
        for node in self.maps[map_index]:
            for ref in node.references:
                source, target = node.ascii_key, ref.ascii_key
                if map_index == 2:
                    source, target = target, source
                result.append(CallTreeReference(map_index, node.offset, ref.offset, source, target))
        return tuple(result)


class _Reader:
    def __init__(self, raw):
        self.raw, self.offset = raw, 0

    def take(self, length):
        if length < 0 or length > len(self.raw) - self.offset:
            raise GXWFormatError("truncated compiler call tree")
        value = self.raw[self.offset:self.offset + length]
        self.offset += length
        return value

    def integer(self, width=4):
        return int.from_bytes(self.take(width), "little")

    def string(self):
        # MFC71 ordinal 1159 (AfxReadStringLength), as used by native Load.
        # Keep the entire prefix, including non-minimal width choices.
        start, width = self.offset, 1
        count = self.integer(1)
        if count == 255:
            count = self.integer(2)
            if count == 65534:
                width = 2
                count = self.integer(1)
                if count == 255:
                    count = self.integer(2)
            if count == 65535:
                count = self.integer()
                if count == 0xFFFFFFFF:
                    count = self.integer(8)
                    if count > 0x7FFFFFFF:
                        raise GXWFormatError("compiler call-tree string length exceeds native limit")
        value = self.take(count * width)
        return CallTreeString(start, self.raw[start:self.offset], value, width)


def parse_compiler_call_tree(raw: bytes) -> CompilerCallTree:
    raw = bytes(raw)
    reader, maps = _Reader(raw), []
    for _ in range(3):
        count = reader.integer()
        # Every node needs two u32, four empty strings and a u32 count.
        if count > (len(raw) - reader.offset) // 16:
            raise GXWFormatError("compiler call-tree node count exceeds input")
        nodes = []
        for _ in range(count):
            start = reader.offset
            flags, kind = reader.integer(), reader.integer()
            names = tuple(reader.string() for _ in range(4))
            ref_count = reader.integer()
            if ref_count > len(raw) - reader.offset:
                raise GXWFormatError("compiler call-tree reference count exceeds input")
            refs = tuple(reader.string() for _ in range(ref_count))
            nodes.append(CallTreeNode(start, raw[start:reader.offset], flags, kind, names, refs))
        maps.append(tuple(nodes))
    offset, resource = reader.offset, None
    # Native Save appends ResourceCallTree.dat only when that file opens.
    # Native projects with exactly the three maps are retained as such.
    if reader.offset < len(raw):
        resource = reader.take(reader.integer())
    return CompilerCallTree(raw, tuple(maps), offset, resource, raw[reader.offset:])
