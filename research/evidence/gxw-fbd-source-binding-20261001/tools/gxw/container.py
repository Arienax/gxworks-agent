from __future__ import annotations

from dataclasses import dataclass
import struct
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from .models import GXWFormatError


CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NO_STREAM = 0xFFFFFFFF


@dataclass(frozen=True)
class CFBDirectoryEntry:
    index: int
    name: str
    object_type: int
    left_sibling: int
    right_sibling: int
    child: int
    start_sector: int
    stream_size: int

    @property
    def is_stream(self) -> bool:
        return self.object_type == 2

    @property
    def is_root(self) -> bool:
        return self.object_type == 5


class CompoundFile:
    """Small read-only Compound File Binary reader used for GXW projects.

    It intentionally implements only safe read operations. Both normal FAT streams
    and MiniFAT streams are supported, which is required because GXW metadata XML
    files may be small while the nested ``_hdb`` container is large.
    """

    def __init__(self, data: bytes, *, source: Optional[Path] = None) -> None:
        self._data = bytes(data)
        self.source = source
        if len(self._data) < 512 or self._data[:8] != CFB_SIGNATURE:
            raise GXWFormatError("not a Microsoft Compound File Binary container")

        self.major_version = self._u16(0x1A)
        byte_order = self._u16(0x1C)
        if byte_order != 0xFFFE:
            raise GXWFormatError(f"unsupported CFB byte order: 0x{byte_order:04X}")

        self.sector_size = 1 << self._u16(0x1E)
        self.mini_sector_size = 1 << self._u16(0x20)
        if self.sector_size not in (512, 4096):
            raise GXWFormatError(f"unsupported CFB sector size: {self.sector_size}")
        if self.mini_sector_size != 64:
            raise GXWFormatError(
                f"unsupported CFB mini-sector size: {self.mini_sector_size}"
            )

        self.num_fat_sectors = self._u32(0x2C)
        self.first_directory_sector = self._u32(0x30)
        self.mini_stream_cutoff = self._u32(0x38)
        self.first_minifat_sector = self._u32(0x3C)
        self.num_minifat_sectors = self._u32(0x40)
        self.first_difat_sector = self._u32(0x44)
        self.num_difat_sectors = self._u32(0x48)

        self._fat_sector_ids = self._read_difat()
        self._fat = self._read_fat()
        self.directory_entries = self._read_directory_entries()
        self.root_entry = next(
            (entry for entry in self.directory_entries if entry.is_root), None
        )
        if self.root_entry is None:
            raise GXWFormatError("CFB root directory entry is missing")

        self._minifat = self._read_minifat()
        self._mini_stream = self._read_regular_stream(
            self.root_entry.start_sector, self.root_entry.stream_size
        ) if self.root_entry.stream_size else b""

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "CompoundFile":
        source = Path(path)
        return cls(source.read_bytes(), source=source)

    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self._data, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self._data, offset)[0]

    def _sector(self, sector_id: int, *, byte_count: Optional[int] = None) -> bytes:
        if sector_id in (FREESECT, ENDOFCHAIN, FATSECT, DIFSECT):
            raise GXWFormatError(f"invalid data sector id: 0x{sector_id:08X}")
        offset = (sector_id + 1) * self.sector_size
        count = self.sector_size if byte_count is None else byte_count
        if not 0 < count <= self.sector_size:
            raise GXWFormatError("invalid CFB sector read length")
        end = offset + count
        if offset < self.sector_size or end > len(self._data):
            raise GXWFormatError(f"CFB sector {sector_id} is outside the file")
        return self._data[offset:end]

    def _read_difat(self) -> List[int]:
        difat = list(struct.unpack_from("<109I", self._data, 0x4C))
        fat_sector_ids = [
            value for value in difat if value not in (FREESECT, ENDOFCHAIN)
        ]

        sector_id = self.first_difat_sector
        per_sector = self.sector_size // 4
        for _ in range(self.num_difat_sectors):
            sector = self._sector(sector_id)
            values = struct.unpack_from(f"<{per_sector}I", sector, 0)
            fat_sector_ids.extend(
                value for value in values[:-1] if value not in (FREESECT, ENDOFCHAIN)
            )
            sector_id = values[-1]

        if len(fat_sector_ids) < self.num_fat_sectors:
            raise GXWFormatError("CFB DIFAT contains fewer FAT sectors than declared")
        return fat_sector_ids[: self.num_fat_sectors]

    def _read_fat(self) -> List[int]:
        fat: List[int] = []
        per_sector = self.sector_size // 4
        for sector_id in self._fat_sector_ids:
            fat.extend(struct.unpack_from(f"<{per_sector}I", self._sector(sector_id), 0))
        return fat

    @staticmethod
    def _walk_chain(start_sector: int, fat: Sequence[int], *, limit: Optional[int] = None) -> List[int]:
        if start_sector in (FREESECT, ENDOFCHAIN):
            return []
        result: List[int] = []
        seen = set()
        sector_id = start_sector
        while sector_id not in (FREESECT, ENDOFCHAIN):
            if sector_id >= len(fat):
                raise GXWFormatError(f"sector chain references out-of-range sector {sector_id}")
            if sector_id in seen:
                raise GXWFormatError(f"cycle detected in CFB sector chain at {sector_id}")
            seen.add(sector_id)
            result.append(sector_id)
            if limit is not None and len(result) >= limit:
                break
            sector_id = fat[sector_id]
        return result

    def _read_regular_stream(self, start_sector: int, size: Optional[int] = None, *, sector_limit: Optional[int] = None) -> bytes:
        chunks = []
        for index, sector_id in enumerate(self._walk_chain(start_sector, self._fat, limit=sector_limit)):
            count = self.sector_size
            if size is not None:
                remaining = size - index * self.sector_size
                if 0 < remaining < count:
                    # Native GX Works2 can omit the last sector's unused tail
                    # in _hdb. Require all declared payload bytes, not padding.
                    # Metadata and extra allocated sectors still require a
                    # complete sector; chain validation is unchanged.
                    count = remaining
            chunks.append(self._sector(sector_id, byte_count=count))
        data = b"".join(chunks)
        return data if size is None else data[:size]

    def _read_directory_entries(self) -> List[CFBDirectoryEntry]:
        directory = self._read_regular_stream(self.first_directory_sector)
        entries: List[CFBDirectoryEntry] = []
        for offset in range(0, len(directory), 128):
            raw = directory[offset : offset + 128]
            if len(raw) < 128:
                break
            name_length = struct.unpack_from("<H", raw, 64)[0]
            name = ""
            if 2 <= name_length <= 64:
                name = raw[: name_length - 2].decode("utf-16le", errors="replace")
            object_type = raw[66]
            left_sibling, right_sibling, child = struct.unpack_from("<III", raw, 68)
            start_sector = struct.unpack_from("<I", raw, 116)[0]
            stream_size = struct.unpack_from("<Q", raw, 120)[0]
            if self.major_version == 3:
                stream_size &= 0xFFFFFFFF
            entries.append(
                CFBDirectoryEntry(
                    index=offset // 128,
                    name=name,
                    object_type=object_type,
                    left_sibling=left_sibling,
                    right_sibling=right_sibling,
                    child=child,
                    start_sector=start_sector,
                    stream_size=stream_size,
                )
            )
        return entries

    def _read_minifat(self) -> List[int]:
        if self.num_minifat_sectors == 0 or self.first_minifat_sector in (FREESECT, ENDOFCHAIN):
            return []
        raw = self._read_regular_stream(
            self.first_minifat_sector, sector_limit=self.num_minifat_sectors
        )
        if len(raw) % 4:
            raise GXWFormatError("MiniFAT byte length is not divisible by four")
        return list(struct.unpack_from(f"<{len(raw) // 4}I", raw, 0))

    def iter_streams(self) -> Iterable[CFBDirectoryEntry]:
        return (entry for entry in self.directory_entries if entry.is_stream and entry.name)

    def find_streams(self, name: str) -> List[CFBDirectoryEntry]:
        return [entry for entry in self.iter_streams() if entry.name == name]

    def get_stream_entry(self, name: str) -> CFBDirectoryEntry:
        matches = self.find_streams(name)
        if not matches:
            raise KeyError(f"CFB stream not found: {name}")
        if len(matches) != 1:
            raise GXWFormatError(f"CFB stream name is ambiguous: {name}")
        return matches[0]

    def read_entry(self, entry: CFBDirectoryEntry) -> bytes:
        if not entry.is_stream:
            raise GXWFormatError(f"directory entry is not a stream: {entry.name}")
        if entry.stream_size == 0:
            return b""
        if entry.stream_size < self.mini_stream_cutoff:
            if not self._minifat:
                raise GXWFormatError(f"MiniFAT unavailable for small stream {entry.name}")
            chunks = []
            for mini_sector in self._walk_chain(entry.start_sector, self._minifat):
                offset = mini_sector * self.mini_sector_size
                end = offset + self.mini_sector_size
                if end > len(self._mini_stream):
                    raise GXWFormatError(
                        f"mini-sector {mini_sector} for {entry.name} is outside root mini stream"
                    )
                chunks.append(self._mini_stream[offset:end])
            return b"".join(chunks)[: entry.stream_size]
        return self._read_regular_stream(entry.start_sector, entry.stream_size)

    def read_stream(self, name: str) -> bytes:
        return self.read_entry(self.get_stream_entry(name))
