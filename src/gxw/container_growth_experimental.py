from __future__ import annotations

import math
import struct

from .container import CompoundFile, ENDOFCHAIN, FREESECT
from .container_writer import (
    _mini_chain,
    _regular_chain,
    _write_directory_stream_size,
    _write_minifat_entry,
    _write_regular_chain_range,
    replace_stream_within_allocation,
)
from .models import GXWFormatError


def _next_appended_sector_id(data: bytes, cfb: CompoundFile) -> int:
    if len(data) % cfb.sector_size:
        raise GXWFormatError("CFB file length is not sector aligned")
    # One sector-sized header precedes sector id 0.
    return len(data) // cfb.sector_size - 1


def _write_fat_entry(
    target: bytearray,
    *,
    cfb: CompoundFile,
    sector_id: int,
    value: int,
) -> None:
    if sector_id < 0 or sector_id >= len(cfb._fat):
        raise GXWFormatError(
            f"FAT entry {sector_id} is outside the currently allocated FAT table"
        )
    entries_per_fat_sector = cfb.sector_size // 4
    fat_table_index = sector_id // entries_per_fat_sector
    if fat_table_index >= len(cfb._fat_sector_ids):
        raise GXWFormatError("appended sector would require a new FAT sector")
    fat_sector_id = cfb._fat_sector_ids[fat_table_index]
    within_fat = (sector_id % entries_per_fat_sector) * 4
    physical_offset = (fat_sector_id + 1) * cfb.sector_size + within_fat
    target[physical_offset : physical_offset + 4] = struct.pack("<I", value)


def _reserve_appended_regular_sectors(
    data: bytes,
    *,
    cfb: CompoundFile,
    count: int,
) -> tuple[bytearray, list[int]]:
    if count < 1:
        raise GXWFormatError("appended regular-sector count must be positive")
    first = _next_appended_sector_id(data, cfb)
    sector_ids = [first + index for index in range(count)]
    for sector_id in sector_ids:
        if sector_id >= len(cfb._fat):
            raise GXWFormatError(
                "appended sector would exceed the current FAT table; FAT growth is required"
            )
        if cfb._fat[sector_id] != FREESECT:
            raise GXWFormatError(
                f"FAT entry {sector_id} is not free for append-only allocation"
            )
    target = bytearray(data)
    target.extend(b"\x00" * (count * cfb.sector_size))
    return target, sector_ids


def replace_regular_stream_with_appended_growth(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Grow one regular CFB stream by appending new physical sectors.

    This deliberately narrow writer never reuses an existing FREESECT hole and never
    allocates a new FAT/DIFAT sector. New data sectors are appended to the physical
    end of the CFB file and linked into the stream's existing FAT chain.
    """

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if old_size < cfb.mini_stream_cutoff or new_size < cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} is not a supported regular-stream growth "
            f"({old_size} -> {new_size})"
        )
    if new_size <= old_size:
        return replace_stream_within_allocation(
            data, stream_name, new_data, allow_shrink=True
        )

    chain = _regular_chain(cfb, entry.start_sector)
    required_chain_length = math.ceil(new_size / cfb.sector_size)
    if required_chain_length <= len(chain):
        return replace_stream_within_allocation(data, stream_name, new_data)

    extra_needed = required_chain_length - len(chain)
    target, appended = _reserve_appended_regular_sectors(
        data, cfb=cfb, count=extra_needed
    )
    extended_chain = [*chain, *appended]

    _write_fat_entry(target, cfb=cfb, sector_id=chain[-1], value=appended[0])
    for index, sector_id in enumerate(appended):
        next_value = appended[index + 1] if index + 1 < len(appended) else ENDOFCHAIN
        _write_fat_entry(target, cfb=cfb, sector_id=sector_id, value=next_value)

    _write_regular_chain_range(
        target,
        cfb=cfb,
        chain=extended_chain,
        logical_offset=0,
        payload=new_data,
    )
    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=entry.index,
        stream_size=new_size,
    )

    result = bytes(target)
    reparsed = CompoundFile(result)
    if reparsed.read_stream(stream_name) != new_data:
        raise GXWFormatError(
            f"post-append verification failed for regular stream {stream_name!r}"
        )
    observed_chain = _regular_chain(
        reparsed, reparsed.get_stream_entry(stream_name).start_sector
    )
    if len(observed_chain) != required_chain_length:
        raise GXWFormatError("post-append regular FAT chain length mismatch")
    return result


def _write_mini_payload_with_root_chain(
    target: bytearray,
    *,
    cfb: CompoundFile,
    root_chain: list[int],
    mini_chain: list[int],
    payload: bytes,
    root_stream_size: int,
) -> None:
    if root_stream_size > len(root_chain) * cfb.sector_size:
        raise GXWFormatError("root MiniStream size exceeds the supplied FAT chain")
    cursor = 0
    for mini_sector in mini_chain:
        if cursor >= len(payload):
            break
        logical_offset = mini_sector * cfb.mini_sector_size
        chunk = payload[cursor : cursor + cfb.mini_sector_size]
        if logical_offset + len(chunk) > root_stream_size:
            raise GXWFormatError(
                f"mini-sector {mini_sector} lies outside the grown root MiniStream"
            )
        _write_regular_chain_range(
            target,
            cfb=cfb,
            chain=root_chain,
            logical_offset=logical_offset,
            payload=chunk,
        )
        cursor += len(chunk)
    if cursor != len(payload):
        raise GXWFormatError("grown MiniFAT chain did not hold the requested payload")


def replace_ministream_with_appended_root_growth(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Grow a MiniStream stream by appending a new regular sector to the root stream.

    This experiment intentionally does *not* consume trailing bytes in the current
    root MiniStream FAT allocation. New mini-sectors begin at the next regular-sector
    boundary and the root MiniStream FAT chain is physically extended at EOF. This
    isolates the GX Works2 behavior observed when slack-only growth parses correctly
    but does not render newly written wires.

    Limitations: existing non-empty MiniStream stream only; stays below the 4096-byte
    cutoff; existing MiniFAT has enough free entries; existing FAT table has enough
    unused entries; no FAT/DIFAT or MiniFAT-sector growth.
    """

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if old_size == 0:
        raise GXWFormatError("append-root MiniStream growth requires an existing chain")
    if old_size >= cfb.mini_stream_cutoff or new_size >= cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} is not a supported MiniStream growth "
            f"({old_size} -> {new_size})"
        )
    if new_size <= old_size:
        return replace_stream_within_allocation(
            data, stream_name, new_data, allow_shrink=True
        )
    if not cfb._minifat:
        raise GXWFormatError("MiniFAT unavailable for append-root growth")

    stream_chain = _mini_chain(cfb, entry.start_sector)
    required_stream_chain_length = math.ceil(new_size / cfb.mini_sector_size)
    if required_stream_chain_length <= len(stream_chain):
        return replace_stream_within_allocation(data, stream_name, new_data)

    extra_mini = required_stream_chain_length - len(stream_chain)
    root = cfb.root_entry
    root_chain = _regular_chain(cfb, root.start_sector)
    if not root_chain:
        raise GXWFormatError("root MiniStream has no regular FAT chain")

    # Skip the current root-chain slack on purpose. The first new mini-sector starts
    # at the next 512-byte root-stream boundary so its bytes live in an appended FAT
    # sector rather than in already allocated trailing slack.
    first_new_mini = len(root_chain) * cfb.sector_size // cfb.mini_sector_size
    allocated_mini = [first_new_mini + index for index in range(extra_mini)]
    if allocated_mini[-1] >= len(cfb._minifat):
        raise GXWFormatError("MiniFAT table growth is required for appended root growth")
    for mini_sector in allocated_mini:
        if cfb._minifat[mini_sector] != FREESECT:
            raise GXWFormatError(
                f"MiniFAT entry {mini_sector} is not free for append-root allocation"
            )

    new_root_size = (allocated_mini[-1] + 1) * cfb.mini_sector_size
    required_root_chain_length = math.ceil(new_root_size / cfb.sector_size)
    extra_root_sectors = required_root_chain_length - len(root_chain)
    if extra_root_sectors < 1:
        raise GXWFormatError(
            "append-root experiment unexpectedly fit without physical FAT growth"
        )

    target, appended_root = _reserve_appended_regular_sectors(
        data, cfb=cfb, count=extra_root_sectors
    )
    extended_root_chain = [*root_chain, *appended_root]

    _write_fat_entry(
        target, cfb=cfb, sector_id=root_chain[-1], value=appended_root[0]
    )
    for index, sector_id in enumerate(appended_root):
        next_value = (
            appended_root[index + 1]
            if index + 1 < len(appended_root)
            else ENDOFCHAIN
        )
        _write_fat_entry(target, cfb=cfb, sector_id=sector_id, value=next_value)

    _write_minifat_entry(
        target,
        cfb=cfb,
        mini_sector=stream_chain[-1],
        value=allocated_mini[0],
    )
    for index, mini_sector in enumerate(allocated_mini):
        next_value = (
            allocated_mini[index + 1]
            if index + 1 < len(allocated_mini)
            else ENDOFCHAIN
        )
        _write_minifat_entry(
            target,
            cfb=cfb,
            mini_sector=mini_sector,
            value=next_value,
        )

    extended_stream_chain = [*stream_chain, *allocated_mini]
    _write_mini_payload_with_root_chain(
        target,
        cfb=cfb,
        root_chain=extended_root_chain,
        mini_chain=extended_stream_chain,
        payload=new_data,
        root_stream_size=new_root_size,
    )
    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=entry.index,
        stream_size=new_size,
    )
    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=root.index,
        stream_size=new_root_size,
    )

    result = bytes(target)
    reparsed = CompoundFile(result)
    if reparsed.read_stream(stream_name) != new_data:
        raise GXWFormatError(
            f"post-append verification failed for MiniStream {stream_name!r}"
        )
    observed_stream_chain = _mini_chain(
        reparsed, reparsed.get_stream_entry(stream_name).start_sector
    )
    if len(observed_stream_chain) != required_stream_chain_length:
        raise GXWFormatError("post-append MiniFAT chain length mismatch")
    observed_root_chain = _regular_chain(reparsed, reparsed.root_entry.start_sector)
    if len(observed_root_chain) != required_root_chain_length:
        raise GXWFormatError("post-append root FAT chain length mismatch")
    if reparsed.root_entry.stream_size != new_root_size:
        raise GXWFormatError("post-append root MiniStream size mismatch")
    return result
