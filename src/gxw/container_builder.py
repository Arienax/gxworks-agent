# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Compose a Compound File Binary container from named streams, without any source file.

Every other writer in this package starts from an existing project and replaces bytes
inside it. A project that must be created from a requirement has no such file to
replace, so this module writes the container itself: header, FAT, MiniFAT, directory
tree and payload sectors. Nothing here interprets GX Works2 content; the stream
payloads are supplied by the caller and only their names, lengths and nesting matter.

The writer is deliberately plain: 512-byte sectors, the observed 4096-byte MiniStream
cutoff, and a balanced directory tree with a red bottom level, satisfying the red-black
invariants the format prescribes. Reading the result back with
``gxw.container.CompoundFile`` and ``gxw.container_writer.validate_cfb_streams`` is the
structural acceptance for this layer; native acceptance of a generated project is a
separate, model-bound result.
"""
from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field

from .container import CFB_SIGNATURE, DIFSECT, ENDOFCHAIN, FATSECT, FREESECT
from .models import GXWFormatError

SECTOR_SIZE = 512
MINI_SECTOR_SIZE = 64
MINI_STREAM_CUTOFF = 4096
DIRECTORY_ENTRY_SIZE = 128
HEADER_SIZE = 512
OBJECT_TYPE_STREAM = 2
OBJECT_TYPE_ROOT = 5
COLOR_BLACK = 1
NO_ENTRY = 0xFFFFFFFF
DIFAT_HEADER_SLOTS = 109


@dataclass
class _Entry:
    name: str
    object_type: int
    start_sector: int = ENDOFCHAIN
    stream_size: int = 0
    left: int = NO_ENTRY
    right: int = NO_ENTRY
    child: int = NO_ENTRY
    color: int = COLOR_BLACK


@dataclass
class _Layout:
    sectors: list[bytes] = field(default_factory=list)
    entries: list[_Entry] = field(default_factory=list)
    directory_start: int = 0
    directory_sectors: int = 0
    mini_chains: dict[str, tuple[int, int]] = field(default_factory=dict)
    mini_fat_start: int = ENDOFCHAIN
    mini_fat_sectors: int = 0
    mini_sectors: int = 0
    payload_sectors: int = 0
    fat_sectors: int = 0
    difat_sectors: int = 0


def _entry_key(name: str):
    """Directory order prescribed by the compound-file format."""
    return (len(name), name.upper())


def _link_balanced(entries: list[tuple[int, _Entry]], table: dict[int, _Entry], *, depth=0, red_level=None) -> int:
    if not entries:
        return NO_ENTRY
    middle = len(entries) // 2
    index, entry = entries[middle]
    if red_level is None:
        red_level = (len(entries) + 1).bit_length() - 1
    entry.color = 0 if depth == red_level else COLOR_BLACK
    entry.left = _link_balanced(entries[:middle], table, depth=depth + 1, red_level=red_level)
    entry.right = _link_balanced(entries[middle + 1:], table, depth=depth + 1, red_level=red_level)
    table[index] = entry
    return index


def _append_sectors(layout: _Layout, payload: bytes) -> int:
    if not payload:
        return ENDOFCHAIN
    start = len(layout.sectors)
    for offset in range(0, len(payload), SECTOR_SIZE):
        layout.sectors.append(bytes(payload[offset:offset + SECTOR_SIZE]))
    return start


def _layout(streams: Mapping[str, bytes]) -> _Layout:
    layout = _Layout()
    names = sorted(streams, key=_entry_key)
    mini_payload = bytearray()
    mini_starts: dict[str, int] = {}
    regular: list[tuple[str, bytes]] = []
    for name in names:
        payload = bytes(streams[name])
        if not payload:
            continue
        if len(payload) < MINI_STREAM_CUTOFF:
            start = len(mini_payload) // MINI_SECTOR_SIZE
            mini_starts[name] = start
            layout.mini_chains[name] = (start, math.ceil(len(payload) / MINI_SECTOR_SIZE))
            mini_payload += payload
            mini_payload += b"\x00" * (-len(mini_payload) % MINI_SECTOR_SIZE)
        else:
            regular.append((name, payload))

    layout.mini_sectors = math.ceil(len(mini_payload) / MINI_SECTOR_SIZE)
    layout.mini_fat_sectors = (
        math.ceil(layout.mini_sectors * 4 / SECTOR_SIZE) if layout.mini_sectors else 0)

    root = _Entry("Root Entry", OBJECT_TYPE_ROOT, start_sector=_append_sectors(layout, bytes(mini_payload)),
                  stream_size=len(mini_payload))
    layout.entries.append(root)
    for name, payload in regular:
        layout.entries.append(_Entry(name, OBJECT_TYPE_STREAM, _append_sectors(layout, payload), len(payload)))
    for name in names:
        if name in mini_starts:
            layout.entries.append(_Entry(name, OBJECT_TYPE_STREAM, mini_starts[name], len(streams[name])))
        elif not streams[name]:
            layout.entries.append(_Entry(name, OBJECT_TYPE_STREAM, ENDOFCHAIN, 0))

    if layout.mini_fat_sectors:
        table = [FREESECT] * (layout.mini_fat_sectors * SECTOR_SIZE // 4)
        for name, (start, count) in layout.mini_chains.items():
            for offset in range(count):
                table[start + offset] = ENDOFCHAIN if offset + 1 == count else start + offset + 1
        payload = b"".join(struct.pack("<I", value) for value in table)
        layout.mini_fat_start = _append_sectors(layout, payload)

    layout.directory_sectors = math.ceil(len(layout.entries) * DIRECTORY_ENTRY_SIZE / SECTOR_SIZE)
    layout.directory_start = len(layout.sectors)
    layout.sectors.extend([b""] * layout.directory_sectors)
    layout.payload_sectors = len(layout.sectors)

    fat_sectors = difat_sectors = 0
    while True:
        total = layout.payload_sectors + fat_sectors + difat_sectors
        needed_fat = max(1, math.ceil(total / (SECTOR_SIZE // 4)))
        needed_difat = math.ceil(max(0, needed_fat - DIFAT_HEADER_SLOTS) / ((SECTOR_SIZE // 4) - 1))
        if needed_fat == fat_sectors and needed_difat == difat_sectors:
            break
        fat_sectors, difat_sectors = needed_fat, needed_difat
    layout.fat_sectors, layout.difat_sectors = fat_sectors, difat_sectors
    layout.sectors.extend([b""] * (fat_sectors + difat_sectors))
    return layout


def _sector_count(entry: _Entry, *, mini: bool) -> int:
    if not entry.stream_size:
        return 0
    unit = MINI_SECTOR_SIZE if mini else SECTOR_SIZE
    return math.ceil(entry.stream_size / unit)


def _fat_table(layout: _Layout) -> list[int]:
    total = len(layout.sectors)
    fat = [FREESECT] * total
    for entry in layout.entries:
        mini = entry is not layout.entries[0] and 0 < entry.stream_size < MINI_STREAM_CUTOFF
        if mini:
            # Small streams live in the root MiniStream and have no regular FAT chain.
            continue
        count = _sector_count(entry, mini=mini)
        if not count:
            continue
        for offset in range(count):
            sector = entry.start_sector + offset
            if sector >= layout.payload_sectors:
                raise GXWFormatError("stream chain runs into the allocation tables")
            fat[sector] = ENDOFCHAIN if offset + 1 == count else sector + 1
    for start, count in ((layout.mini_fat_start, layout.mini_fat_sectors),
                         (layout.directory_start, layout.directory_sectors)):
        for offset in range(count):
            sector = start + offset
            fat[sector] = ENDOFCHAIN if offset + 1 == count else sector + 1
    for sector in range(layout.payload_sectors, layout.payload_sectors + layout.fat_sectors):
        fat[sector] = FATSECT
    for sector in range(layout.payload_sectors + layout.fat_sectors, total):
        fat[sector] = DIFSECT
    return fat


def _directory_payload(layout: _Layout) -> bytes:
    table = {index: entry for index, entry in enumerate(layout.entries)}
    children = sorted(((index, entry) for index, entry in enumerate(layout.entries) if index),
                      key=lambda row: _entry_key(row[1].name))
    layout.entries[0].child = _link_balanced(children, table)
    table[0] = layout.entries[0]
    payload = bytearray()
    for index in range(len(layout.entries)):
        entry = table[index]
        name = "Root Entry" if entry is layout.entries[0] else entry.name
        encoded = (name + "\0").encode("utf-16le")
        if len(encoded) > 64:
            raise GXWFormatError("compound-file entry name exceeds 31 characters: " + name)
        record = bytearray(DIRECTORY_ENTRY_SIZE)
        record[0:len(encoded)] = encoded
        struct.pack_into("<H", record, 64, len(encoded))
        record[66] = entry.object_type
        record[67] = entry.color
        struct.pack_into("<III", record, 68, entry.left, entry.right, entry.child)
        struct.pack_into("<I", record, 116, entry.start_sector)
        struct.pack_into("<Q", record, 120, entry.stream_size)
        payload += record
    payload += b"\x00" * (-len(payload) % SECTOR_SIZE)
    return bytes(payload)


def build_compound_file(streams: Mapping[str, bytes]) -> bytes:
    """Write one container holding exactly ``streams`` at the root level."""
    if not isinstance(streams, Mapping) or not streams:
        raise GXWFormatError("a container needs at least one named stream")
    keys = set()
    for name, payload in streams.items():
        if not isinstance(name, str) or not name or not isinstance(payload, (bytes, bytearray)):
            raise GXWFormatError("container streams require a name and bytes")
        key = _entry_key(name)
        if key in keys:
            raise GXWFormatError('compound-file stream names must be unique ignoring case')
        keys.add(key)
    layout = _layout(streams)
    fat = _fat_table(layout)
    directory = _directory_payload(layout)
    for offset in range(layout.directory_sectors):
        start = offset * SECTOR_SIZE
        layout.sectors[layout.directory_start + offset] = directory[start:start + SECTOR_SIZE]
    fat_payload = bytearray()
    for value in fat:
        fat_payload += struct.pack("<I", value)
    fat_payload += struct.pack("<I", FREESECT) * (
        (layout.fat_sectors * SECTOR_SIZE - len(fat_payload)) // 4)
    for offset in range(layout.fat_sectors):
        layout.sectors[layout.payload_sectors + offset] = bytes(
            fat_payload[offset * SECTOR_SIZE:(offset + 1) * SECTOR_SIZE])
    for offset in range(layout.difat_sectors):
        record = bytearray(struct.pack("<I", FREESECT) * (SECTOR_SIZE // 4))
        first = DIFAT_HEADER_SLOTS + offset * ((SECTOR_SIZE // 4) - 1)
        for slot in range((SECTOR_SIZE // 4) - 1):
            value = first + slot
            struct.pack_into("<I", record, 4 * slot,
                             layout.payload_sectors + value if value < layout.fat_sectors else FREESECT)
        following = layout.payload_sectors + layout.fat_sectors + offset + 1
        struct.pack_into("<I", record, SECTOR_SIZE - 4,
                         following if offset + 1 < layout.difat_sectors else ENDOFCHAIN)
        layout.sectors[layout.payload_sectors + layout.fat_sectors + offset] = bytes(record)

    target = bytearray(HEADER_SIZE + SECTOR_SIZE * len(layout.sectors))
    target[0:8] = CFB_SIGNATURE
    struct.pack_into("<HH", target, 0x18, 0x003E, 3)
    struct.pack_into("<H", target, 0x1C, 0xFFFE)
    struct.pack_into("<HH", target, 0x1E, 9, 6)
    struct.pack_into("<I", target, 0x2C, layout.fat_sectors)
    struct.pack_into("<I", target, 0x30, layout.directory_start)
    struct.pack_into("<I", target, 0x38, MINI_STREAM_CUTOFF)
    struct.pack_into("<I", target, 0x3C, layout.mini_fat_start)
    struct.pack_into("<I", target, 0x40, layout.mini_fat_sectors)
    struct.pack_into("<I", target, 0x44, layout.payload_sectors + layout.fat_sectors
                     if layout.difat_sectors else ENDOFCHAIN)
    struct.pack_into("<I", target, 0x48, layout.difat_sectors)
    for index in range(DIFAT_HEADER_SLOTS):
        struct.pack_into("<I", target, 0x4C + 4 * index,
                         layout.payload_sectors + index if index < layout.fat_sectors else FREESECT)
    for index, sector in enumerate(layout.sectors):
        if len(sector) > SECTOR_SIZE:
            raise GXWFormatError("sector payload exceeds the sector size")
        start = HEADER_SIZE + SECTOR_SIZE * index
        target[start:start + SECTOR_SIZE] = bytes(sector).ljust(SECTOR_SIZE, b"\x00")
    return bytes(target)
