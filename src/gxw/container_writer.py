from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Literal

from .container import CompoundFile, ENDOFCHAIN, FREESECT
from .models import GXWFormatError


@dataclass(frozen=True)
class CFBStreamAllocation:
    name: str
    storage: Literal["mini", "regular"]
    stream_size: int
    allocation_capacity: int
    chain_length: int


@dataclass(frozen=True)
class CFBRootMiniStreamAllocation:
    stream_size: int
    allocation_capacity: int
    regular_chain_length: int
    backed_mini_sectors: int
    max_backed_mini_sectors: int



def _regular_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._fat)



def _mini_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._minifat)



def inspect_stream_allocation(data: bytes, stream_name: str) -> CFBStreamAllocation:
    """Describe the allocation currently backing one unique CFB stream."""

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)

    if entry.stream_size < cfb.mini_stream_cutoff:
        chain = _mini_chain(cfb, entry.start_sector) if entry.stream_size else []
        return CFBStreamAllocation(
            name=stream_name,
            storage="mini",
            stream_size=entry.stream_size,
            allocation_capacity=len(chain) * cfb.mini_sector_size,
            chain_length=len(chain),
        )

    chain = _regular_chain(cfb, entry.start_sector)
    return CFBStreamAllocation(
        name=stream_name,
        storage="regular",
        stream_size=entry.stream_size,
        allocation_capacity=len(chain) * cfb.sector_size,
        chain_length=len(chain),
    )



def inspect_root_ministream_allocation(data: bytes) -> CFBRootMiniStreamAllocation:
    """Describe root MiniStream bytes already backed by its existing FAT chain."""

    cfb = CompoundFile(data)
    root = cfb.root_entry
    chain = _regular_chain(cfb, root.start_sector) if root.stream_size else []
    capacity = len(chain) * cfb.sector_size
    return CFBRootMiniStreamAllocation(
        stream_size=root.stream_size,
        allocation_capacity=capacity,
        regular_chain_length=len(chain),
        backed_mini_sectors=root.stream_size // cfb.mini_sector_size,
        max_backed_mini_sectors=min(
            capacity // cfb.mini_sector_size,
            len(cfb._minifat),
        ),
    )



def _write_regular_chain_range(
    target: bytearray,
    *,
    cfb: CompoundFile,
    chain: list[int],
    logical_offset: int,
    payload: bytes,
) -> None:
    if logical_offset < 0:
        raise GXWFormatError("negative CFB logical write offset")
    capacity = len(chain) * cfb.sector_size
    if logical_offset + len(payload) > capacity:
        raise GXWFormatError(
            "CFB write exceeds the existing regular-sector chain capacity"
        )

    cursor = 0
    offset = logical_offset
    while cursor < len(payload):
        chain_index = offset // cfb.sector_size
        within_sector = offset % cfb.sector_size
        sector_id = chain[chain_index]
        physical_offset = (sector_id + 1) * cfb.sector_size + within_sector
        chunk_size = min(
            cfb.sector_size - within_sector,
            len(payload) - cursor,
        )
        target[physical_offset : physical_offset + chunk_size] = payload[
            cursor : cursor + chunk_size
        ]
        cursor += chunk_size
        offset += chunk_size



def _write_directory_field(
    target: bytearray,
    *,
    cfb: CompoundFile,
    entry_index: int,
    relative_offset: int,
    payload: bytes,
) -> None:
    directory_chain = _regular_chain(cfb, cfb.first_directory_sector)
    logical_offset = entry_index * 128 + relative_offset
    _write_regular_chain_range(
        target,
        cfb=cfb,
        chain=directory_chain,
        logical_offset=logical_offset,
        payload=payload,
    )



def _write_directory_stream_size(
    target: bytearray,
    *,
    cfb: CompoundFile,
    entry_index: int,
    stream_size: int,
) -> None:
    if stream_size < 0:
        raise GXWFormatError("negative CFB stream size")
    if cfb.major_version == 3 and stream_size > 0xFFFFFFFF:
        raise GXWFormatError("CFB v3 stream size exceeds uint32 range")
    _write_directory_field(
        target,
        cfb=cfb,
        entry_index=entry_index,
        relative_offset=120,
        # The high DWORD is unused in v3. Preserve its source bytes.
        payload=struct.pack("<I" if cfb.major_version == 3 else "<Q", stream_size),
    )



def _write_minifat_entry(
    target: bytearray,
    *,
    cfb: CompoundFile,
    mini_sector: int,
    value: int,
) -> None:
    if mini_sector < 0 or mini_sector >= len(cfb._minifat):
        raise GXWFormatError(f"MiniFAT index {mini_sector} is outside the table")
    minifat_chain = _regular_chain(cfb, cfb.first_minifat_sector)
    if len(minifat_chain) < cfb.num_minifat_sectors:
        raise GXWFormatError("MiniFAT chain is shorter than the header declares")
    _write_regular_chain_range(
        target,
        cfb=cfb,
        chain=minifat_chain[: cfb.num_minifat_sectors],
        logical_offset=mini_sector * 4,
        payload=struct.pack("<I", value),
    )



def _write_mini_payload(
    target: bytearray,
    *,
    cfb: CompoundFile,
    chain: list[int],
    payload: bytes,
    root_stream_size_limit: int | None = None,
) -> None:
    root = cfb.root_entry
    limit = root.stream_size if root_stream_size_limit is None else root_stream_size_limit
    if limit == 0 and payload:
        raise GXWFormatError("CFB root MiniStream is empty")
    root_chain = _regular_chain(cfb, root.start_sector) if limit else []
    root_capacity = len(root_chain) * cfb.sector_size
    if limit > root_capacity:
        raise GXWFormatError(
            "requested root MiniStream size exceeds its existing regular-sector chain"
        )

    cursor = 0
    for mini_sector in chain:
        if cursor >= len(payload):
            break
        logical_offset = mini_sector * cfb.mini_sector_size
        chunk = payload[cursor : cursor + cfb.mini_sector_size]
        if logical_offset + len(chunk) > limit:
            raise GXWFormatError(
                f"mini-sector {mini_sector} is outside the permitted root MiniStream"
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
        raise GXWFormatError("mini-sector chain did not hold the requested payload")



def replace_stream_within_allocation(
    data: bytes,
    stream_name: str,
    new_data: bytes,
    *,
    allow_shrink: bool = False,
) -> bytes:
    """Replace a CFB stream without changing FAT/MiniFAT allocation."""

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if new_size < old_size and not allow_shrink:
        raise GXWFormatError(
            f"shrinking CFB stream {stream_name!r} is not enabled in this milestone"
        )

    old_is_mini = old_size < cfb.mini_stream_cutoff
    new_is_mini = new_size < cfb.mini_stream_cutoff
    if old_is_mini != new_is_mini:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} would cross the MiniStream cutoff "
            f"({old_size} -> {new_size}); allocation rebuild is required"
        )

    target = bytearray(data)

    if old_is_mini:
        if old_size == 0 and new_size:
            raise GXWFormatError(
                f"CFB stream {stream_name!r} has no existing mini-sector allocation"
            )
        if not cfb._minifat and new_size:
            raise GXWFormatError(
                f"MiniFAT unavailable for CFB stream {stream_name!r}"
            )

        chain = _mini_chain(cfb, entry.start_sector) if old_size else []
        capacity = len(chain) * cfb.mini_sector_size
        if new_size > capacity:
            raise GXWFormatError(
                f"CFB stream {stream_name!r} needs {new_size} bytes but its existing "
                f"mini-sector allocation holds only {capacity} bytes"
            )

        payload = new_data
        if new_size < old_size:
            payload += b"\x00" * (old_size - new_size)
        _write_mini_payload(target, cfb=cfb, chain=chain, payload=payload)
    else:
        chain = _regular_chain(cfb, entry.start_sector)
        capacity = len(chain) * cfb.sector_size
        if new_size > capacity:
            raise GXWFormatError(
                f"CFB stream {stream_name!r} needs {new_size} bytes but its existing "
                f"regular-sector allocation holds only {capacity} bytes"
            )

        payload = new_data
        if new_size < old_size:
            payload += b"\x00" * (old_size - new_size)
        _write_regular_chain_range(
            target,
            cfb=cfb,
            chain=chain,
            logical_offset=0,
            payload=payload,
        )

    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=entry.index,
        stream_size=new_size,
    )

    result = bytes(target)
    reparsed = CompoundFile(result)
    observed = reparsed.read_stream(stream_name)
    if observed != new_data:
        raise GXWFormatError(
            f"post-write verification failed for CFB stream {stream_name!r}"
        )
    if len(result) != len(data):
        raise GXWFormatError("within-allocation CFB replacement changed container length")

    return result


def validate_cfb_streams(data: bytes) -> dict[str, bytes]:
    """Writer preflight: reject truncated, aliased, or unterminated live chains.

    A native file may omit unused padding in its last physical sector. Check
    original payloads before any writer adds padding; never repair missing data.
    Unallocated bytes and unknown directory fields are not interpreted.
    """
    cfb = CompoundFile(data)
    if cfb.major_version not in (3, 4):
        raise GXWFormatError("unsupported CFB writer version")
    regular_owners, mini_owners = {}, {}

    def claim(chain, owners, label, table):
        if chain and table[chain[-1]] != ENDOFCHAIN:
            raise GXWFormatError(f"unterminated CFB chain: {label}")
        for sid in chain:
            if sid in owners:
                raise GXWFormatError(f"overlapping CFB allocations: {label} and {owners[sid]}")
            owners[sid] = label

    for sid in cfb._fat_sector_ids:
        if sid in regular_owners:
            raise GXWFormatError("duplicate CFB FAT sector")
        cfb._sector(sid)
        regular_owners[sid] = "FAT"
    sid = cfb.first_difat_sector
    for _ in range(cfb.num_difat_sectors):
        if sid in regular_owners:
            raise GXWFormatError("overlapping CFB DIFAT sector")
        sector = cfb._sector(sid)
        regular_owners[sid] = "DIFAT"
        sid = struct.unpack_from("<I", sector, len(sector) - 4)[0]
    for label, start in (("directory", cfb.first_directory_sector),
                         ("MiniFAT", cfb.first_minifat_sector),
                         ("root", cfb.root_entry.start_sector)):
        chain = _regular_chain(cfb, start)
        claim(chain, regular_owners, label, cfb._fat)
        if label == "root":
            cfb._read_regular_stream(start, cfb.root_entry.stream_size)
        else:
            for sid in chain:
                cfb._sector(sid)
        if label == "root" and cfb.root_entry.stream_size > len(chain) * cfb.sector_size:
            raise GXWFormatError("truncated root MiniStream")
        if label == "MiniFAT" and len(chain) != cfb.num_minifat_sectors:
            raise GXWFormatError("MiniFAT chain/header count mismatch")
    payloads = {}
    for entry in cfb.iter_streams():
        if entry.name in payloads:
            raise GXWFormatError(f"ambiguous CFB stream name: {entry.name}")
        mini = entry.stream_size < cfb.mini_stream_cutoff
        table = cfb._minifat if mini else cfb._fat
        chain = cfb._walk_chain(entry.start_sector, table)
        claim(chain, mini_owners if mini else regular_owners, entry.name, table)
        payload = cfb.read_entry(entry)
        if len(payload) != entry.stream_size:
            raise GXWFormatError(f"truncated CFB stream: {entry.name}")
        payloads[entry.name] = payload
    return payloads


def replace_project_stream(data: bytes, stream_name: str, new_data: bytes) -> tuple[bytes, str]:
    """Choose from existing allocation writers using actual capacities.

    Common edits retain the established allocation layout. Larger edits grow
    FAT/MiniFAT/DIFAT tables, including MiniStream cutoff transitions.
    No GX-specific rendering threshold or preferred physical layout is used.
    """
    from .container_growth_experimental import replace_regular_stream_with_appended_growth
    from .cfb_allocator import resize_cfb_stream
    allocation = inspect_stream_allocation(data, stream_name)
    cfb = CompoundFile(data)
    if len(data) % cfb.sector_size:
        expected = validate_cfb_streams(data)
        if expected[stream_name] == new_data:
            return data, "existing_allocation"
        # Allocation writers use full-sector EOF arithmetic. Padding is safe
        # only after every original live payload and metadata chain is checked.
        padded = data + bytes((-len(data)) % cfb.sector_size)
        result, mode = replace_project_stream(padded, stream_name, new_data)
        expected[stream_name] = new_data
        if validate_cfb_streams(result) != expected:
            raise GXWFormatError("padded CFB failed stream preservation checks")
        return result, "pad_final_sector+" + mode
    if bool(allocation.stream_size) != bool(new_data):
        return resize_cfb_stream(data, stream_name, new_data), "empty_stream_transition"
    if (allocation.stream_size < cfb.mini_stream_cutoff) != (len(new_data) < cfb.mini_stream_cutoff):
        return resize_cfb_stream(data, stream_name, new_data), "storage_transition"
    if len(new_data) <= allocation.allocation_capacity:
        return replace_stream_within_allocation(data, stream_name, new_data, allow_shrink=True), "existing_allocation"
    if allocation.storage == "regular":
        end = len(data) // cfb.sector_size - 1 + math.ceil(len(new_data) / cfb.sector_size) - allocation.chain_length
        if end <= len(cfb._fat):
            return replace_regular_stream_with_appended_growth(data, stream_name, new_data), "append_regular"
        return resize_cfb_stream(data, stream_name, new_data), "grow_allocation_tables"
    root = inspect_root_ministream_allocation(data)
    available = sum(value == FREESECT for value in cfb._minifat[:root.max_backed_mini_sectors])
    needed = (len(new_data) + cfb.mini_sector_size - 1) // cfb.mini_sector_size - allocation.chain_length
    if available >= needed:
        return replace_stream_with_ministream_growth(data, stream_name, new_data), "extend_mini_in_root"
    return resize_cfb_stream(data, stream_name, new_data), "grow_root_and_tables"



def replace_stream_with_free_mini_growth(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Grow a MiniStream-backed stream using only already-backed free mini-sectors."""

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if new_size <= old_size:
        return replace_stream_within_allocation(
            data,
            stream_name,
            new_data,
            allow_shrink=True,
        )
    if old_size == 0:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} has no existing mini-sector chain to extend"
        )
    if old_size >= cfb.mini_stream_cutoff or new_size >= cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} is not a supported MiniStream growth "
            f"({old_size} -> {new_size})"
        )
    if not cfb._minifat:
        raise GXWFormatError("MiniFAT unavailable for requested stream growth")

    chain = _mini_chain(cfb, entry.start_sector)
    required_chain_length = math.ceil(new_size / cfb.mini_sector_size)
    if required_chain_length <= len(chain):
        return replace_stream_within_allocation(data, stream_name, new_data)

    extra_needed = required_chain_length - len(chain)
    backed_mini_count = cfb.root_entry.stream_size // cfb.mini_sector_size
    backed_mini_count = min(backed_mini_count, len(cfb._minifat))
    free_backed = [
        index
        for index in range(backed_mini_count)
        if cfb._minifat[index] == FREESECT
    ]

    if len(free_backed) < extra_needed:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} needs {extra_needed} additional mini-sectors, "
            f"but only {len(free_backed)} free backed mini-sectors are available; "
            "root MiniStream growth is required"
        )

    allocated = free_backed[:extra_needed]
    extended_chain = [*chain, *allocated]
    target = bytearray(data)

    _write_minifat_entry(
        target,
        cfb=cfb,
        mini_sector=chain[-1],
        value=allocated[0],
    )
    for index, mini_sector in enumerate(allocated):
        next_value = allocated[index + 1] if index + 1 < len(allocated) else ENDOFCHAIN
        _write_minifat_entry(
            target,
            cfb=cfb,
            mini_sector=mini_sector,
            value=next_value,
        )

    _write_mini_payload(
        target,
        cfb=cfb,
        chain=extended_chain,
        payload=new_data,
    )
    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=entry.index,
        stream_size=new_size,
    )

    result = bytes(target)
    if len(result) != len(data):
        raise GXWFormatError("free-mini-sector growth unexpectedly changed CFB length")

    reparsed = CompoundFile(result)
    observed = reparsed.read_stream(stream_name)
    if observed != new_data:
        raise GXWFormatError(
            f"post-growth verification failed for CFB stream {stream_name!r}"
        )
    grown = inspect_stream_allocation(result, stream_name)
    if grown.chain_length != required_chain_length:
        raise GXWFormatError(
            "post-growth MiniFAT chain length does not match requested stream size"
        )

    return result



def replace_stream_with_ministream_growth(
    data: bytes,
    stream_name: str,
    new_data: bytes,
) -> bytes:
    """Grow a MiniStream stream into free entries and existing root-FAT slack.

    Unlike :func:`replace_stream_with_free_mini_growth`, this milestone may enlarge
    the root MiniStream *directory size* when the root's existing regular FAT chain
    already contains enough unused trailing bytes. It still never allocates a new
    regular FAT sector and never changes the CFB file length.

    This is specifically useful for GXW sample 48: the nested `_hdb` reports no
    free mini-sectors inside the current logical root MiniStream, but its final FAT
    sector may still contain enough unexposed bytes to back the two additional
    64-byte mini-sectors needed by the first structural insertion experiment.
    """

    cfb = CompoundFile(data)
    entry = cfb.get_stream_entry(stream_name)
    old_size = entry.stream_size
    new_size = len(new_data)

    if new_size <= old_size:
        return replace_stream_within_allocation(
            data,
            stream_name,
            new_data,
            allow_shrink=True,
        )
    if old_size == 0:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} has no existing mini-sector chain to extend"
        )
    if old_size >= cfb.mini_stream_cutoff or new_size >= cfb.mini_stream_cutoff:
        raise GXWFormatError(
            f"CFB stream {stream_name!r} is not a supported MiniStream growth "
            f"({old_size} -> {new_size})"
        )
    if not cfb._minifat:
        raise GXWFormatError("MiniFAT unavailable for requested stream growth")
    if cfb.root_entry.stream_size % cfb.mini_sector_size:
        raise GXWFormatError(
            "root MiniStream size is not aligned to the 64-byte mini-sector size"
        )

    chain = _mini_chain(cfb, entry.start_sector)
    required_chain_length = math.ceil(new_size / cfb.mini_sector_size)
    if required_chain_length <= len(chain):
        return replace_stream_within_allocation(data, stream_name, new_data)

    extra_needed = required_chain_length - len(chain)
    root = cfb.root_entry
    root_chain = _regular_chain(cfb, root.start_sector)
    root_capacity = len(root_chain) * cfb.sector_size
    current_backed = root.stream_size // cfb.mini_sector_size
    max_backed = min(
        root_capacity // cfb.mini_sector_size,
        len(cfb._minifat),
    )

    free_backed = [
        index
        for index in range(current_backed)
        if cfb._minifat[index] == FREESECT
    ]
    free_in_root_slack = [
        index
        for index in range(current_backed, max_backed)
        if cfb._minifat[index] == FREESECT
    ]
    candidates = [*free_backed, *free_in_root_slack]

    if len(candidates) < extra_needed:
        slack_bytes = max(0, root_capacity - root.stream_size)
        raise GXWFormatError(
            f"CFB stream {stream_name!r} needs {extra_needed} additional mini-sectors, "
            f"but only {len(candidates)} are available within the current root FAT "
            f"allocation ({slack_bytes} trailing bytes); root MiniStream FAT-chain "
            "growth is required"
        )

    allocated = candidates[:extra_needed]
    extended_chain = [*chain, *allocated]
    new_root_size = max(
        root.stream_size,
        (max(allocated) + 1) * cfb.mini_sector_size,
    )
    if new_root_size > root_capacity:
        raise GXWFormatError(
            "root MiniStream expansion would exceed its existing regular FAT allocation"
        )

    target = bytearray(data)

    # Newly exposed trailing bytes are zeroed before mini-sector payload is written.
    if new_root_size > root.stream_size:
        _write_regular_chain_range(
            target,
            cfb=cfb,
            chain=root_chain,
            logical_offset=root.stream_size,
            payload=b"\x00" * (new_root_size - root.stream_size),
        )

    _write_minifat_entry(
        target,
        cfb=cfb,
        mini_sector=chain[-1],
        value=allocated[0],
    )
    for index, mini_sector in enumerate(allocated):
        next_value = allocated[index + 1] if index + 1 < len(allocated) else ENDOFCHAIN
        _write_minifat_entry(
            target,
            cfb=cfb,
            mini_sector=mini_sector,
            value=next_value,
        )

    _write_mini_payload(
        target,
        cfb=cfb,
        chain=extended_chain,
        payload=new_data,
        root_stream_size_limit=new_root_size,
    )
    _write_directory_stream_size(
        target,
        cfb=cfb,
        entry_index=entry.index,
        stream_size=new_size,
    )
    if new_root_size != root.stream_size:
        _write_directory_stream_size(
            target,
            cfb=cfb,
            entry_index=root.index,
            stream_size=new_root_size,
        )

    result = bytes(target)
    if len(result) != len(data):
        raise GXWFormatError("MiniStream slack growth unexpectedly changed CFB length")

    reparsed = CompoundFile(result)
    if reparsed.read_stream(stream_name) != new_data:
        raise GXWFormatError(
            f"post-growth verification failed for CFB stream {stream_name!r}"
        )
    grown = inspect_stream_allocation(result, stream_name)
    if grown.chain_length != required_chain_length:
        raise GXWFormatError(
            "post-growth MiniFAT chain length does not match requested stream size"
        )
    if reparsed.root_entry.stream_size != new_root_size:
        raise GXWFormatError("post-growth root MiniStream size verification failed")

    return result
