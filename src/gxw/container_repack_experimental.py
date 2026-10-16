from __future__ import annotations

import math
import struct

from .container import CompoundFile, ENDOFCHAIN, FREESECT
from .container_writer import (
    _regular_chain,
    _write_directory_field,
    _write_directory_stream_size,
    _write_regular_chain_range,
)
from .models import GXWFormatError


def repack_ministream_dense_with_replacement(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Repack every non-empty MiniStream stream densely in directory-entry order.

    This is a controlled GX Works2 compatibility experiment.  Native GXW samples
    observed so far keep their small nested ``_hdb`` streams in contiguous,
    back-to-back MiniFAT runs.  Earlier experiments only grew or relocated
    ``Program.pou`` and therefore left holes or moved that stream away from its
    neighbors.  GX Works2 could parse the nodes but did not render the wires.

    This routine rebuilds the *entire* root MiniStream allocation while leaving
    regular streams and the root FAT chain itself untouched:

    - read every existing non-empty stream below the 4096-byte cutoff;
    - replace ``stream_name`` with ``new_data``;
    - assign each small stream one contiguous run, packed from mini-sector 0 in
      directory-entry order;
    - rebuild the MiniFAT entries for all backed mini-sectors;
    - rewrite every affected directory start-sector field;
    - rewrite the root MiniStream bytes and logical size.

    The current experimental implementation deliberately refuses to grow the root
    regular FAT chain.  For controlled sample 48, growing ``1.Program.pou`` from
    411 to 535 bytes raises the total packed MiniStream footprint from 174 to 176
    mini-sectors, which exactly fills the already allocated 22 x 512-byte root
    FAT chain.  This makes the experiment allocation-neutral at both nested and
    outer CFB levels and isolates dense repacking itself.
    """

    cfb = CompoundFile(data)
    target_entry = cfb.get_stream_entry(stream_name)
    if target_entry.stream_size == 0:
        raise GXWFormatError("dense MiniStream repack requires an existing target stream")
    if target_entry.stream_size >= cfb.mini_stream_cutoff:
        raise GXWFormatError(f"target stream {stream_name!r} is not MiniStream-backed")
    if len(new_data) >= cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"replacement for {stream_name!r} crosses the MiniStream cutoff"
        )
    if not cfb._minifat:
        raise GXWFormatError("MiniFAT unavailable for dense MiniStream repack")

    root = cfb.root_entry
    root_chain = _regular_chain(cfb, root.start_sector) if root.stream_size else []
    if not root_chain:
        raise GXWFormatError("root MiniStream has no regular FAT chain")
    root_capacity = len(root_chain) * cfb.sector_size

    mini_entries = sorted(
        (
            entry
            for entry in cfb.directory_entries
            if entry.is_stream
            and entry.name
            and entry.stream_size > 0
            and entry.stream_size < cfb.mini_stream_cutoff
        ),
        key=lambda entry: entry.index,
    )
    if not any(entry.index == target_entry.index for entry in mini_entries):
        raise GXWFormatError("target MiniStream directory entry was not selected for repack")

    payloads: dict[int, bytes] = {}
    sizes: dict[int, int] = {}
    for entry in mini_entries:
        payload = new_data if entry.index == target_entry.index else cfb.read_entry(entry)
        payloads[entry.index] = payload
        sizes[entry.index] = len(payload)

    assignments: dict[int, list[int]] = {}
    cursor = 0
    for entry in mini_entries:
        count = math.ceil(sizes[entry.index] / cfb.mini_sector_size)
        chain = list(range(cursor, cursor + count))
        assignments[entry.index] = chain
        cursor += count

    required_mini_sectors = cursor
    new_root_size = required_mini_sectors * cfb.mini_sector_size
    if new_root_size > root_capacity:
        raise GXWFormatError(
            "dense MiniStream repack exceeds the existing root FAT allocation: "
            f"needs {new_root_size} bytes, has {root_capacity}; physical root growth "
            "is intentionally disabled in this experiment"
        )
    if required_mini_sectors > len(cfb._minifat):
        raise GXWFormatError(
            "dense MiniStream repack exceeds the existing MiniFAT table capacity"
        )

    target = bytearray(data)

    # Canonicalize the complete physical root allocation before writing packed
    # payloads.  This removes all stale bytes from holes and previous stream tails.
    _write_regular_chain_range(
        target,
        cfb=cfb,
        chain=root_chain,
        logical_offset=0,
        payload=b"\x00" * root_capacity,
    )

    # Rebuild the entire declared MiniFAT table.  Every unassigned entry is free;
    # assigned runs are contiguous and terminate with ENDOFCHAIN.
    rebuilt_minifat = [FREESECT] * len(cfb._minifat)
    for entry in mini_entries:
        chain = assignments[entry.index]
        for index, mini_sector in enumerate(chain):
            rebuilt_minifat[mini_sector] = (
                chain[index + 1] if index + 1 < len(chain) else ENDOFCHAIN
            )

    minifat_chain = _regular_chain(cfb, cfb.first_minifat_sector)
    if len(minifat_chain) < cfb.num_minifat_sectors:
        raise GXWFormatError("MiniFAT regular chain is shorter than declared")
    minifat_bytes = struct.pack(f"<{len(rebuilt_minifat)}I", *rebuilt_minifat)
    _write_regular_chain_range(
        target,
        cfb=cfb,
        chain=minifat_chain[: cfb.num_minifat_sectors],
        logical_offset=0,
        payload=minifat_bytes,
    )

    # Write every small stream into its new packed location and repoint its
    # directory entry.  The payload is padded to whole mini-sectors so there are
    # no residual bytes inside an assigned run.
    for entry in mini_entries:
        chain = assignments[entry.index]
        payload = payloads[entry.index]
        padded = payload + b"\x00" * (
            len(chain) * cfb.mini_sector_size - len(payload)
        )
        logical_offset = chain[0] * cfb.mini_sector_size
        _write_regular_chain_range(
            target,
            cfb=cfb,
            chain=root_chain,
            logical_offset=logical_offset,
            payload=padded,
        )
        _write_directory_field(
            target,
            cfb=cfb,
            entry_index=entry.index,
            relative_offset=116,
            payload=struct.pack("<I", chain[0]),
        )
        if entry.index == target_entry.index:
            _write_directory_stream_size(
                target,
                cfb=cfb,
                entry_index=entry.index,
                stream_size=len(new_data),
            )

    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=root.index,
        stream_size=new_root_size,
    )

    result = bytes(target)
    if len(result) != len(data):
        raise GXWFormatError("dense MiniStream repack unexpectedly changed CFB length")

    reparsed = CompoundFile(result)
    if reparsed.root_entry.stream_size != new_root_size:
        raise GXWFormatError("dense repack root MiniStream size verification failed")

    # Verify payload preservation/replacement and canonical packed chains.
    expected_cursor = 0
    for entry in mini_entries:
        observed = reparsed.get_stream_entry(entry.name)
        observed_payload = reparsed.read_entry(observed)
        expected_payload = payloads[entry.index]
        if observed_payload != expected_payload:
            raise GXWFormatError(
                f"dense repack payload verification failed for stream {entry.name!r}"
            )
        observed_chain = CompoundFile._walk_chain(observed.start_sector, reparsed._minifat)
        expected_chain = assignments[entry.index]
        if observed_chain != expected_chain:
            raise GXWFormatError(
                f"dense repack chain verification failed for stream {entry.name!r}: "
                f"{observed_chain} != {expected_chain}"
            )
        if observed_chain[0] != expected_cursor:
            raise GXWFormatError("dense repack left a gap between MiniStream allocations")
        expected_cursor = observed_chain[-1] + 1

    return result
