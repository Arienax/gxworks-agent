"""CFB stream resizing with FAT, MiniFAT and DIFAT growth.

Existing directory identities and unrelated stream bytes stay in place. New
regular sectors are appended; table sizes follow MS-CFB allocation equations,
including the sectors occupied by the new tables themselves.
"""
from __future__ import annotations

import struct

from .container import CompoundFile, DIFSECT, ENDOFCHAIN, FATSECT, FREESECT
from .models import GXWFormatError


def _ceil(value, unit):
    return (value + unit - 1) // unit


class _Allocator:
    def __init__(self, raw):
        self.cfb = cfb = CompoundFile(raw)
        if (cfb.major_version, cfb.sector_size) not in ((3, 512), (4, 4096)):
            raise GXWFormatError("CFB version/sector-size mismatch")
        if cfb.mini_stream_cutoff != 4096 or cfb.root_entry.stream_size % 64:
            raise GXWFormatError("unsupported CFB MiniStream geometry")
        self.data = bytearray(raw)
        self.size = cfb.sector_size
        self.per_table = self.size // 4
        self.fat = list(cfb._fat)
        self.minifat = list(cfb._minifat)
        self.fat_ids = list(cfb._fat_sector_ids)
        self.difat_ids = []
        sid = cfb.first_difat_sector
        for _ in range(cfb.num_difat_sectors):
            self.difat_ids.append(sid)
            sid = struct.unpack_from("<I", cfb._sector(sid), self.size - 4)[0]
        if self.difat_ids and sid != ENDOFCHAIN:
            raise GXWFormatError("unterminated DIFAT chain")
        self.directory = cfb._walk_chain(cfb.first_directory_sector, self.fat)
        self.root = cfb._walk_chain(cfb.root_entry.start_sector, self.fat)
        self.root_size = cfb.root_entry.stream_size
        self.mini_tables = cfb._walk_chain(cfb.first_minifat_sector, self.fat)

    def append(self, count):
        first = len(self.data) // self.size - 1
        end = first + count
        if end >= 0xFFFFFFFA:
            raise GXWFormatError("CFB sector address space exhausted")
        if any(v != FREESECT for v in self.fat[first:end]):
            raise GXWFormatError("allocated FAT entry points beyond physical EOF")
        self.fat.extend([FREESECT] * max(0, end - len(self.fat)))
        self.data.extend(b"\0" * (count * self.size))
        return list(range(first, end))

    @staticmethod
    def link(chain, table):
        for index, sid in enumerate(chain):
            table[sid] = chain[index + 1] if index + 1 < len(chain) else ENDOFCHAIN

    def regular_size(self, chain, count):
        result = chain[:count] + self.append(max(0, count - len(chain)))
        for sid in chain[count:]:
            self.fat[sid] = FREESECT
        self.link(result, self.fat)
        return result

    def write(self, chain, payload, offset=0):
        if offset < 0 or offset + len(payload) > len(chain) * self.size:
            raise GXWFormatError("CFB write outside allocation")
        cursor = 0
        while cursor < len(payload):
            index, within = divmod(offset + cursor, self.size)
            start = (chain[index] + 1) * self.size + within
            count = min(self.size - within, len(payload) - cursor)
            self.data[start:start + count] = payload[cursor:cursor + count]
            cursor += count

    def directory_stream(self, entry, chain, size):
        start = chain[0] if chain else ENDOFCHAIN
        self.write(self.directory, struct.pack("<I", start), entry.index * 128 + 116)
        if self.cfb.major_version == 3 and size > 0xFFFFFFFF:
            raise GXWFormatError("CFB v3 stream exceeds uint32 size")
        self.write(self.directory, struct.pack("<I" if self.cfb.major_version == 3 else "<Q", size),
                   entry.index * 128 + 120)

    def mini_size(self, chain, count):
        result = chain[:count]
        for sid in chain[count:]:
            self.minifat[sid] = FREESECT
        needed = count - len(result)
        if needed:
            available = [sid for sid, value in enumerate(self.minifat) if value == FREESECT][:needed]
            shortfall = needed - len(available)
            if shortfall:
                start = len(self.minifat)
                self.minifat.extend([FREESECT] * (_ceil(shortfall, self.per_table) * self.per_table))
                available.extend(range(start, start + shortfall))
            result.extend(available)
        self.link(result, self.minifat)
        if result:
            self.root_size = max(self.root_size, (max(result) + 1) * 64)
            self.root = self.regular_size(self.root, _ceil(self.root_size, self.size))
        return result

    def replace(self, name, payload):
        entry = self.cfb.get_stream_entry(name)
        old_mini, new_mini = entry.stream_size < 4096, len(payload) < 4096
        old_table = self.minifat if old_mini else self.fat
        chain = self.cfb._walk_chain(entry.start_sector, old_table)
        if old_mini != new_mini:
            for sid in chain:
                old_table[sid] = FREESECT
            chain = []
        if new_mini:
            chain = self.mini_size(chain, _ceil(len(payload), 64))
            for index, sid in enumerate(chain):
                self.write(self.root, payload[index * 64:(index + 1) * 64], sid * 64)
        else:
            chain = self.regular_size(chain, _ceil(len(payload), self.size))
            self.write(chain, payload)
        self.directory_stream(entry, chain, len(payload))

    def finish(self):
        self.directory_stream(self.cfb.root_entry, self.root, self.root_size)
        self.mini_tables = self.regular_size(self.mini_tables, _ceil(len(self.minifat), self.per_table))
        if self.minifat:
            self.write(self.mini_tables, struct.pack(f"<{len(self.minifat)}I", *self.minifat))
        struct.pack_into("<II", self.data, 0x3C,
                         self.mini_tables[0] if self.mini_tables else ENDOFCHAIN, len(self.mini_tables))

        # Least fixed point: adding FAT/DIFAT sectors can itself require more FAT.
        physical = len(self.data) // self.size - 1
        nf, nd = len(self.fat_ids), len(self.difat_ids)
        while True:
            total = physical + nf - len(self.fat_ids) + nd - len(self.difat_ids)
            next_f = max(nf, _ceil(total, self.per_table))
            next_d = max(nd, _ceil(max(0, next_f - 109), self.per_table - 1))
            if (nf, nd) == (next_f, next_d):
                break
            nf, nd = next_f, next_d
        self.fat_ids.extend(self.append(nf - len(self.fat_ids)))
        self.difat_ids.extend(self.append(nd - len(self.difat_ids)))
        self.fat.extend([FREESECT] * (nf * self.per_table - len(self.fat)))
        for sid in self.fat_ids:
            self.fat[sid] = FATSECT
        for sid in self.difat_ids:
            self.fat[sid] = DIFSECT
        for index, sid in enumerate(self.fat_ids):
            values = self.fat[index * self.per_table:(index + 1) * self.per_table]
            self.write([sid], struct.pack(f"<{self.per_table}I", *values))
        header_ids = self.fat_ids[:109]
        struct.pack_into("<109I", self.data, 0x4C, *(header_ids + [FREESECT] * (109 - len(header_ids))))
        for index, sid in enumerate(self.difat_ids):
            first = 109 + index * (self.per_table - 1)
            ids = self.fat_ids[first:first + self.per_table - 1]
            values = ids + [FREESECT] * (self.per_table - 1 - len(ids))
            values.append(self.difat_ids[index + 1] if index + 1 < nd else ENDOFCHAIN)
            self.write([sid], struct.pack(f"<{self.per_table}I", *values))
        struct.pack_into("<I", self.data, 0x2C, nf)
        struct.pack_into("<II", self.data, 0x44, self.difat_ids[0] if nd else ENDOFCHAIN, nd)
        return bytes(self.data)


def resize_cfb_stream(data: bytes, name: str, payload: bytes) -> bytes:
    """Resize one existing stream, including zero/cutoff/table transitions."""
    from .container_writer import validate_cfb_streams
    expected = validate_cfb_streams(data)
    if name not in expected:
        raise KeyError(name)
    if expected[name] == payload:
        return data
    # Preflight above reads the original bytes first, including any partially
    # present final sector. Complete only its unused tail before EOF allocation.
    sector_size = CompoundFile(data).sector_size
    data = data + bytes((-len(data)) % sector_size)
    allocator = _Allocator(data)
    allocator.replace(name, payload)
    result = allocator.finish()
    expected[name] = payload
    if validate_cfb_streams(result) != expected:
        raise GXWFormatError("resized CFB failed stream preservation checks")
    return result
