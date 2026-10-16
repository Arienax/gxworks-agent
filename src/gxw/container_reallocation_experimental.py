from __future__ import annotations

import math
import struct

from .container import CompoundFile, ENDOFCHAIN, FREESECT
from .container_growth_experimental import (
    _reserve_appended_regular_sectors,
    _write_fat_entry,
    _write_mini_payload_with_root_chain,
)
from .container_writer import (
    _mini_chain,
    _regular_chain,
    _write_directory_field,
    _write_directory_stream_size,
    _write_minifat_entry,
)
from .models import GXWFormatError


def replace_ministream_with_contiguous_appended_reallocation(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Relocate one MiniStream-backed stream to a fresh contiguous mini-sector run.

    This is a controlled GX Works2 compatibility experiment. Earlier growth paths
    extended the existing Program.pou MiniFAT chain with mini-sectors near the end
    of the root MiniStream, producing a fragmented chain such as
    ``27..33,176,177``. Our parser and generic CFB rules accept that layout, but GX
    Works2 renders the nodes while dropping the newly written wires.

    This routine deliberately avoids chain extension. It appends enough regular FAT
    sectors to the root MiniStream, allocates an entirely fresh *contiguous* run of
    mini-sectors beyond the old root FAT capacity, repoints the directory entry to
    that run, and frees the old mini-sector chain.

    Limitations are intentionally narrow:
    - existing non-empty MiniStream stream only;
    - old/new sizes remain below the MiniStream cutoff;
    - existing MiniFAT table must already contain enough entries;
    - existing FAT table must already cover the physically appended sectors;
    - no FAT/DIFAT or MiniFAT-sector growth.
    """

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if old_size == 0:
        raise GXWFormatError("contiguous MiniStream reallocation requires an existing chain")
    if old_size >= cfb.mini_stream_cutoff or new_size >= cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} is not a supported MiniStream reallocation "
            f"({old_size} -> {new_size})"
        )
    if not cfb._minifat:
        raise GXWFormatError("MiniFAT unavailable for contiguous reallocation")

    old_chain = _mini_chain(cfb, entry.start_sector)
    if not old_chain:
        raise GXWFormatError("source MiniStream chain is empty")

    required_mini = math.ceil(new_size / cfb.mini_sector_size)
    root = cfb.root_entry
    root_chain = _regular_chain(cfb, root.start_sector)
    if not root_chain:
        raise GXWFormatError("root MiniStream has no regular FAT chain")

    # Start the replacement at the first mini-sector beyond the current *physical*
    # root FAT capacity. This guarantees the new run is fresh and contiguous and
    # avoids consuming the old chain's trailing slack.
    first_new_mini = len(root_chain) * cfb.sector_size // cfb.mini_sector_size
    new_chain = [first_new_mini + index for index in range(required_mini)]
    if new_chain[-1] >= len(cfb._minifat):
        raise GXWFormatError("MiniFAT table growth is required for contiguous reallocation")
    for mini_sector in new_chain:
        if cfb._minifat[mini_sector] != FREESECT:
            raise GXWFormatError(
                f"MiniFAT entry {mini_sector} is not free for contiguous reallocation"
            )

    new_root_size = (new_chain[-1] + 1) * cfb.mini_sector_size
    required_root_chain_length = math.ceil(new_root_size / cfb.sector_size)
    extra_root_sectors = required_root_chain_length - len(root_chain)
    if extra_root_sectors < 1:
        raise GXWFormatError(
            "contiguous reallocation unexpectedly fit without physical root growth"
        )

    target, appended_root = _reserve_appended_regular_sectors(
        data,
        cfb=cfb,
        count=extra_root_sectors,
    )
    extended_root_chain = [*root_chain, *appended_root]

    _write_fat_entry(
        target,
        cfb=cfb,
        sector_id=root_chain[-1],
        value=appended_root[0],
    )
    for index, sector_id in enumerate(appended_root):
        next_value = (
            appended_root[index + 1]
            if index + 1 < len(appended_root)
            else ENDOFCHAIN
        )
        _write_fat_entry(target, cfb=cfb, sector_id=sector_id, value=next_value)

    # Release the old Program.pou mini chain instead of linking it to the new run.
    # The experiment is specifically testing whether GX Works2 requires the POU
    # payload to live in one contiguous MiniFAT run.
    for mini_sector in old_chain:
        _write_minifat_entry(
            target,
            cfb=cfb,
            mini_sector=mini_sector,
            value=FREESECT,
        )

    for index, mini_sector in enumerate(new_chain):
        next_value = new_chain[index + 1] if index + 1 < len(new_chain) else ENDOFCHAIN
        _write_minifat_entry(
            target,
            cfb=cfb,
            mini_sector=mini_sector,
            value=next_value,
        )

    _write_mini_payload_with_root_chain(
        target,
        cfb=cfb,
        root_chain=extended_root_chain,
        mini_chain=new_chain,
        payload=new_data,
        root_stream_size=new_root_size,
    )

    # Directory entry layout: start sector @ +116, stream size @ +120.
    _write_directory_field(
        target,
        cfb=cfb,
        entry_index=entry.index,
        relative_offset=116,
        payload=struct.pack("<I", new_chain[0]),
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
    observed_entry = reparsed.get_stream_entry(stream_name)
    observed_chain = _mini_chain(reparsed, observed_entry.start_sector)

    if reparsed.read_stream(stream_name) != new_data:
        raise GXWFormatError("post-reallocation MiniStream payload verification failed")
    if observed_entry.start_sector != new_chain[0]:
        raise GXWFormatError("post-reallocation start-sector verification failed")
    if observed_chain != new_chain:
        raise GXWFormatError("post-reallocation MiniFAT chain is not the expected contiguous run")
    if reparsed.root_entry.stream_size != new_root_size:
        raise GXWFormatError("post-reallocation root MiniStream size mismatch")

    return result
