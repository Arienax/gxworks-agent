import math
import struct

from src.gxw.container import (
    CFB_SIGNATURE,
    ENDOFCHAIN,
    FATSECT,
    FREESECT,
    NO_STREAM,
    CompoundFile,
)
from src.gxw.container_growth_experimental import (
    replace_ministream_with_appended_root_growth,
    replace_regular_stream_with_appended_growth,
)
from src.gxw.container_writer import inspect_stream_allocation


SECTOR_SIZE = 512


def _directory_entry(
    name: str,
    object_type: int,
    *,
    child: int = NO_STREAM,
    start_sector: int = ENDOFCHAIN,
    stream_size: int = 0,
) -> bytes:
    raw = bytearray(128)
    if name:
        encoded = (name + "\x00").encode("utf-16le")
        raw[: len(encoded)] = encoded
        struct.pack_into("<H", raw, 64, len(encoded))
    raw[66] = object_type
    raw[67] = 1
    struct.pack_into("<III", raw, 68, NO_STREAM, NO_STREAM, child)
    struct.pack_into("<I", raw, 116, start_sector)
    struct.pack_into("<Q", raw, 120, stream_size)
    return bytes(raw)


def _header(
    *,
    first_directory_sector: int,
    fat_sector: int,
    first_minifat_sector: int = ENDOFCHAIN,
    num_minifat_sectors: int = 0,
) -> bytes:
    raw = bytearray(SECTOR_SIZE)
    raw[:8] = CFB_SIGNATURE
    struct.pack_into("<H", raw, 0x18, 0x003E)
    struct.pack_into("<H", raw, 0x1A, 3)
    struct.pack_into("<H", raw, 0x1C, 0xFFFE)
    struct.pack_into("<H", raw, 0x1E, 9)
    struct.pack_into("<H", raw, 0x20, 6)
    struct.pack_into("<I", raw, 0x2C, 1)
    struct.pack_into("<I", raw, 0x30, first_directory_sector)
    struct.pack_into("<I", raw, 0x38, 4096)
    struct.pack_into("<I", raw, 0x3C, first_minifat_sector)
    struct.pack_into("<I", raw, 0x40, num_minifat_sectors)
    struct.pack_into("<I", raw, 0x44, ENDOFCHAIN)
    for index in range(109):
        struct.pack_into("<I", raw, 0x4C + 4 * index, FREESECT)
    struct.pack_into("<I", raw, 0x4C, fat_sector)
    return bytes(raw)


def _build_mini_fixture(payload: bytes) -> bytes:
    # 0 directory, 1 root MiniStream, 2 MiniFAT, 3 FAT.
    needed = math.ceil(len(payload) / 64)
    directory = bytearray(SECTOR_SIZE)
    directory[0:128] = _directory_entry(
        "Root Entry", 5, child=1, start_sector=1, stream_size=448
    )
    directory[128:256] = _directory_entry(
        "16", 2, start_sector=0, stream_size=len(payload)
    )

    root = bytearray(SECTOR_SIZE)
    root[: len(payload)] = payload

    minifat = [FREESECT] * 128
    for index in range(needed):
        minifat[index] = index + 1 if index + 1 < needed else ENDOFCHAIN

    fat = [FREESECT] * 128
    fat[0] = ENDOFCHAIN
    fat[1] = ENDOFCHAIN
    fat[2] = ENDOFCHAIN
    fat[3] = FATSECT

    return b"".join(
        [
            _header(
                first_directory_sector=0,
                fat_sector=3,
                first_minifat_sector=2,
                num_minifat_sectors=1,
            ),
            bytes(directory),
            bytes(root),
            struct.pack("<128I", *minifat),
            struct.pack("<128I", *fat),
        ]
    )


def _build_regular_fixture(payload: bytes) -> bytes:
    data_sector_count = math.ceil(len(payload) / SECTOR_SIZE)
    fat_sector = data_sector_count + 1

    directory = bytearray(SECTOR_SIZE)
    directory[0:128] = _directory_entry("Root Entry", 5, child=1)
    directory[128:256] = _directory_entry(
        "_hdb", 2, start_sector=1, stream_size=len(payload)
    )

    data_sectors = []
    for index in range(data_sector_count):
        chunk = payload[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE]
        data_sectors.append(chunk + b"\x00" * (SECTOR_SIZE - len(chunk)))

    fat = [FREESECT] * 128
    fat[0] = ENDOFCHAIN
    for sector_id in range(1, data_sector_count + 1):
        fat[sector_id] = (
            sector_id + 1 if sector_id < data_sector_count else ENDOFCHAIN
        )
    fat[fat_sector] = FATSECT

    return b"".join(
        [
            _header(first_directory_sector=0, fat_sector=fat_sector),
            bytes(directory),
            *data_sectors,
            struct.pack("<128I", *fat),
        ]
    )


def test_append_root_sector_for_ministream_growth():
    original = bytes(index % 251 for index in range(411))
    raw = _build_mini_fixture(original)
    grown_payload = original + b"N" * 124  # 535 bytes => 9 mini-sectors

    patched = replace_ministream_with_appended_root_growth(raw, "16", grown_payload)

    assert len(patched) == len(raw) + SECTOR_SIZE
    cfb = CompoundFile(patched)
    assert cfb.read_stream("16") == grown_payload
    assert cfb.root_entry.stream_size == 640
    assert CompoundFile._walk_chain(cfb.root_entry.start_sector, cfb._fat) == [1, 4]
    allocation = inspect_stream_allocation(patched, "16")
    assert allocation.chain_length == 9
    # The eighth mini-sector is deliberately skipped because it lives in old FAT slack.
    assert CompoundFile._walk_chain(
        cfb.get_stream_entry("16").start_sector, cfb._minifat
    ) == [0, 1, 2, 3, 4, 5, 6, 8, 9]


def test_append_sector_for_regular_stream_growth():
    original = bytes(index % 251 for index in range(5000))
    raw = _build_regular_fixture(original)
    grown_payload = original + b"R" * 300

    patched = replace_regular_stream_with_appended_growth(raw, "_hdb", grown_payload)

    assert len(patched) == len(raw) + SECTOR_SIZE
    cfb = CompoundFile(patched)
    assert cfb.read_stream("_hdb") == grown_payload
    allocation = inspect_stream_allocation(patched, "_hdb")
    assert allocation.chain_length == 11
    assert allocation.allocation_capacity == 11 * SECTOR_SIZE
