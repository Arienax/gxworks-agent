import math
import struct

import pytest

from src.gxw.container import (
    CFB_SIGNATURE,
    ENDOFCHAIN,
    FATSECT,
    FREESECT,
    NO_STREAM,
    CompoundFile,
)
from src.gxw.container_writer import (
    inspect_root_ministream_allocation,
    inspect_stream_allocation,
    replace_stream_with_free_mini_growth,
    replace_stream_with_ministream_growth,
    replace_stream_within_allocation,
)
from src.gxw.models import GXWFormatError


SECTOR_SIZE = 512


def _directory_entry(
    name: str,
    object_type: int,
    *,
    child: int = NO_STREAM,
    left: int = NO_STREAM,
    right: int = NO_STREAM,
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
    struct.pack_into("<III", raw, 68, left, right, child)
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
    struct.pack_into("<I", raw, 0x28, 0)
    struct.pack_into("<I", raw, 0x2C, 1)
    struct.pack_into("<I", raw, 0x30, first_directory_sector)
    struct.pack_into("<I", raw, 0x34, 0)
    struct.pack_into("<I", raw, 0x38, 4096)
    struct.pack_into("<I", raw, 0x3C, first_minifat_sector)
    struct.pack_into("<I", raw, 0x40, num_minifat_sectors)
    struct.pack_into("<I", raw, 0x44, ENDOFCHAIN)
    struct.pack_into("<I", raw, 0x48, 0)
    for index in range(109):
        struct.pack_into("<I", raw, 0x4C + 4 * index, FREESECT)
    struct.pack_into("<I", raw, 0x4C, fat_sector)
    return bytes(raw)


def _build_mini_stream_cfb(
    payload: bytes,
    *,
    root_stream_size: int = SECTOR_SIZE,
) -> bytes:
    # Sector 0: directory; 1: root MiniStream; 2: MiniFAT; 3: FAT.
    if not payload or len(payload) >= 4096:
        raise ValueError("mini fixture payload must be 1..4095 bytes")
    if root_stream_size <= 0 or root_stream_size > SECTOR_SIZE:
        raise ValueError("synthetic root MiniStream must fit its one FAT sector")
    if root_stream_size % 64:
        raise ValueError("synthetic root MiniStream size must be mini-sector aligned")
    if len(payload) > root_stream_size:
        raise ValueError("payload exceeds the logical root MiniStream size")

    needed_mini_sectors = math.ceil(len(payload) / 64)
    if needed_mini_sectors > 8:
        raise ValueError("synthetic root MiniStream contains only eight mini-sectors")

    directory = bytearray(SECTOR_SIZE)
    directory[0:128] = _directory_entry(
        "Root Entry",
        5,
        child=1,
        start_sector=1,
        stream_size=root_stream_size,
    )
    directory[128:256] = _directory_entry(
        "16",
        2,
        start_sector=0,
        stream_size=len(payload),
    )

    mini_stream = bytearray(SECTOR_SIZE)
    mini_stream[: len(payload)] = payload

    minifat = [FREESECT] * (SECTOR_SIZE // 4)
    for index in range(needed_mini_sectors):
        minifat[index] = (
            index + 1 if index + 1 < needed_mini_sectors else ENDOFCHAIN
        )

    fat = [FREESECT] * (SECTOR_SIZE // 4)
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
            bytes(mini_stream),
            struct.pack("<128I", *minifat),
            struct.pack("<128I", *fat),
        ]
    )


def _build_regular_stream_cfb(payload: bytes) -> bytes:
    if len(payload) < 4096:
        raise ValueError("regular fixture payload must be at least 4096 bytes")

    data_sector_count = math.ceil(len(payload) / SECTOR_SIZE)
    fat_sector = data_sector_count + 1
    if fat_sector >= 128:
        raise ValueError("synthetic fixture supports one FAT sector only")

    directory = bytearray(SECTOR_SIZE)
    directory[0:128] = _directory_entry("Root Entry", 5, child=1)
    directory[128:256] = _directory_entry(
        "_hdb",
        2,
        start_sector=1,
        stream_size=len(payload),
    )

    data_sectors = []
    for index in range(data_sector_count):
        chunk = payload[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE]
        data_sectors.append(chunk + b"\x00" * (SECTOR_SIZE - len(chunk)))

    fat = [FREESECT] * (SECTOR_SIZE // 4)
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


def test_grow_ministream_without_reallocating_minifat_chain():
    original_payload = bytes(index % 251 for index in range(411))
    raw = _build_mini_stream_cfb(original_payload)

    allocation = inspect_stream_allocation(raw, "16")
    assert allocation.storage == "mini"
    assert allocation.stream_size == 411
    assert allocation.chain_length == 7
    assert allocation.allocation_capacity == 448

    new_payload = original_payload + b"ABCD"
    patched = replace_stream_within_allocation(raw, "16", new_payload)

    assert len(patched) == len(raw)
    reparsed = CompoundFile(patched)
    assert reparsed.read_stream("16") == new_payload
    assert reparsed.get_stream_entry("16").stream_size == 415


def test_ministream_growth_fails_when_existing_chain_is_too_small():
    original_payload = bytes(index % 251 for index in range(411))
    raw = _build_mini_stream_cfb(original_payload)

    with pytest.raises(GXWFormatError, match="holds only 448 bytes"):
        replace_stream_within_allocation(raw, "16", original_payload + b"X" * 38)


def test_grow_ministream_by_linking_free_backed_mini_sectors():
    original_payload = bytes(index % 251 for index in range(300))
    raw = _build_mini_stream_cfb(original_payload)

    before = inspect_stream_allocation(raw, "16")
    assert before.chain_length == 5
    assert before.allocation_capacity == 320

    new_payload = original_payload + b"G" * 130
    patched = replace_stream_with_free_mini_growth(raw, "16", new_payload)

    assert len(patched) == len(raw)
    reparsed = CompoundFile(patched)
    assert reparsed.read_stream("16") == new_payload
    after = inspect_stream_allocation(patched, "16")
    assert after.stream_size == 430
    assert after.chain_length == 7
    assert after.allocation_capacity == 448


def test_free_mini_growth_fails_when_root_ministream_has_no_backed_capacity():
    original_payload = bytes(index % 251 for index in range(300))
    raw = _build_mini_stream_cfb(original_payload)

    with pytest.raises(GXWFormatError, match="root MiniStream growth is required"):
        replace_stream_with_free_mini_growth(raw, "16", original_payload + b"Z" * 300)


def test_grow_root_ministream_into_existing_fat_slack():
    original_payload = bytes(index % 251 for index in range(411))
    raw = _build_mini_stream_cfb(original_payload, root_stream_size=448)

    root_before = inspect_root_ministream_allocation(raw)
    assert root_before.stream_size == 448
    assert root_before.allocation_capacity == 512
    assert root_before.backed_mini_sectors == 7
    assert root_before.max_backed_mini_sectors == 8

    # 479 bytes require eight mini-sectors. The eighth MiniFAT entry is free but
    # initially outside the logical root MiniStream; the same one-sector FAT chain
    # already contains the needed final 64 bytes.
    new_payload = original_payload + b"R" * 68
    patched = replace_stream_with_ministream_growth(raw, "16", new_payload)

    assert len(patched) == len(raw)
    reparsed = CompoundFile(patched)
    assert reparsed.read_stream("16") == new_payload
    assert reparsed.root_entry.stream_size == 512
    after = inspect_stream_allocation(patched, "16")
    assert after.stream_size == 479
    assert after.chain_length == 8
    assert after.allocation_capacity == 512


def test_root_ministream_growth_still_fails_when_fat_chain_has_no_slack():
    original_payload = bytes(index % 251 for index in range(411))
    raw = _build_mini_stream_cfb(original_payload, root_stream_size=448)

    # 535 bytes would require nine mini-sectors, but this synthetic root FAT chain
    # can expose only eight. A real FAT-chain extension would be required.
    with pytest.raises(GXWFormatError, match="FAT-chain growth is required"):
        replace_stream_with_ministream_growth(raw, "16", original_payload + b"Q" * 124)


def test_grow_regular_stream_within_existing_fat_chain():
    original_payload = bytes(index % 251 for index in range(5000))
    raw = _build_regular_stream_cfb(original_payload)

    allocation = inspect_stream_allocation(raw, "_hdb")
    assert allocation.storage == "regular"
    assert allocation.stream_size == 5000
    assert allocation.chain_length == 10
    assert allocation.allocation_capacity == 5120

    new_payload = original_payload + b"Z" * 100
    patched = replace_stream_within_allocation(raw, "_hdb", new_payload)

    assert len(patched) == len(raw)
    reparsed = CompoundFile(patched)
    assert reparsed.read_stream("_hdb") == new_payload
    assert reparsed.get_stream_entry("_hdb").stream_size == 5100
