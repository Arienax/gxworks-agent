# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Read-only, byte-complete metadata comparison of three nested GXW _hdb CFBs.

Sector IDs are relative to the nested CFB; directory entries are paired by slot,
not by name. Full snapshots include unused slots and FAT/MiniFAT table tails.
No payload parsing, writer, or output-file operation is used here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import struct
import sys
import uuid

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.container import CompoundFile
from gxw.models import GXWFormatError


def _unpack(raw: bytes, offset: int, fmt: str = "I") -> int:
    return struct.unpack_from("<" + fmt, raw, offset)[0]


def _filetime(ticks: int) -> dict:
    # Keep all 100 ns ticks, including the digit datetime cannot represent.
    utc = None
    if ticks:
        try:
            value = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
                microseconds=ticks // 10
            )
            utc = value.strftime("%Y-%m-%dT%H:%M:%S") + (
                f".{ticks % 10_000_000:07d}Z"
            )
        except OverflowError:
            utc = "outside datetime range"
    return {"ticks": ticks, "utc": utc}


def parse_directory_entry(raw: bytes, index: int, major_version: int) -> dict:
    """Parse every field; retain the entire slot, including unused name bytes."""
    if len(raw) != 128:
        raise GXWFormatError("directory entry must contain exactly 128 bytes")
    name_length = _unpack(raw, 64, "H")
    valid_name = 2 <= name_length <= 64 and name_length % 2 == 0
    size = _unpack(raw, 120, "Q")
    return {
        "directory_index": index,
        "raw_hex": raw.hex(),
        "name_buffer_hex": raw[:64].hex(),
        "name_length": name_length,
        "name": raw[:name_length - 2].decode("utf-16le", errors="replace")
        if valid_name else "",
        "object_type": raw[66],
        "color": raw[67],
        "left": _unpack(raw, 68),
        "right": _unpack(raw, 72),
        "child": _unpack(raw, 76),
        "clsid": str(uuid.UUID(bytes_le=raw[80:96])),
        "state_bits": _unpack(raw, 96),
        "creation_time": _filetime(_unpack(raw, 100, "Q")),
        "modified_time": _filetime(_unpack(raw, 108, "Q")),
        "start_sector": _unpack(raw, 116),
        "stream_size_raw_u64": size,
        "stream_size": size & 0xFFFFFFFF if major_version == 3 else size,
    }


def snapshot(path: Path) -> dict:
    gxw = path.read_bytes()
    data = CompoundFile(gxw).read_stream("_hdb")
    cfb = CompoundFile(data)
    header = {
        "raw_hex": data[:512].hex(),
        "signature_hex": data[:8].hex(),
        "clsid": str(uuid.UUID(bytes_le=data[8:24])),
        "reserved_hex": data[34:40].hex(),
        "sector_padding_hex": data[512:cfb.sector_size].hex(),
    }
    for name, offset, fmt in (
        ("minor_version", 24, "H"), ("major_version", 26, "H"),
        ("byte_order", 28, "H"), ("sector_shift", 30, "H"),
        ("mini_sector_shift", 32, "H"), ("num_directory_sectors", 40, "I"),
        ("num_fat_sectors", 44, "I"), ("first_directory_sector", 48, "I"),
        ("transaction_signature", 52, "I"), ("mini_stream_cutoff", 56, "I"),
        ("first_minifat_sector", 60, "I"), ("num_minifat_sectors", 64, "I"),
        ("first_difat_sector", 68, "I"), ("num_difat_sectors", 72, "I"),
    ):
        header[name] = _unpack(data, offset, fmt)
    header["difat_entries"] = list(struct.unpack_from("<109I", data, 76))

    # Private read-only reader primitives expose physical table bytes that the
    # public directory dataclass deliberately does not retain.
    directory_chain = cfb._walk_chain(cfb.first_directory_sector, cfb._fat)
    directory = b"".join(cfb._sector(sid) for sid in directory_chain)
    entries = [parse_directory_entry(directory[i:i + 128], i // 128,
                                     cfb.major_version)
               for i in range(0, len(directory), 128)]
    difat_sectors = []
    sid = cfb.first_difat_sector
    for _ in range(cfb.num_difat_sectors):
        raw = cfb._sector(sid)
        values = list(struct.unpack("<" + "I" * (len(raw) // 4), raw))
        difat_sectors.append({"sector_id": sid, "entries": values[:-1],
                              "next_sector": values[-1]})
        sid = values[-1]

    root = entries[cfb.root_entry.index]
    root_chain = cfb._walk_chain(root["start_sector"], cfb._fat)
    streams = {}
    for entry in entries:
        if entry["object_type"] != 2:
            continue
        size = entry["stream_size"]
        mini = size < cfb.mini_stream_cutoff
        table = cfb._minifat if mini else cfb._fat
        chain = cfb._walk_chain(entry["start_sector"], table) if size else []
        streams[str(entry["directory_index"])] = {
            "directory_index": entry["directory_index"],
            "name": entry["name"],
            "highlight": "stream 16 (1.Program.pou)" if entry["name"] == "16" else "",
            "storage": "MiniStream" if mini else "FAT",
            "start_sector": entry["start_sector"],
            "stream_size": size,
            "chain": chain,
            "chain_terminator": table[chain[-1]] if chain else None,
            "allocation_capacity": len(chain) * (
                cfb.mini_sector_size if mini else cfb.sector_size
            ),
        }
    mini_streams = [s for s in streams.values() if s["storage"] == "MiniStream"
                    and s["stream_size"]]
    packed = [sid for s in mini_streams for sid in s["chain"]]
    minifat_chain = cfb._walk_chain(cfb.first_minifat_sector, cfb._fat)
    return {
        "source": {"path": str(path.resolve()), "gxw_bytes": len(gxw),
                   "gxw_sha256": hashlib.sha256(gxw).hexdigest(),
                   "hdb_sha256": hashlib.sha256(data).hexdigest()},
        "hdb_bytes": len(data),
        "header": header,
        "allocation": {
            "fat_sector_ids": cfb._fat_sector_ids,
            "difat_sectors": difat_sectors,
            "fat_entries": cfb._fat,
            "minifat_sector_chain": minifat_chain,
            "minifat_sector_ids_declared": minifat_chain[:cfb.num_minifat_sectors],
            "minifat_entries": cfb._minifat,
            "directory_sector_chain": directory_chain,
            "root_sector_chain": root_chain,
            "root_capacity": len(root_chain) * cfb.sector_size,
            "root_trailing_slack": len(root_chain) * cfb.sector_size - root["stream_size"],
            "mini_stream_count": len(mini_streams),
            "allocated_mini_sectors": len(packed),
            "dense_in_directory_order": packed == list(range(len(packed))),
        },
        "root_directory_index": root["directory_index"],
        "directory_entries": {str(e["directory_index"]): e for e in entries},
        "streams": streams,
    }


def differences(left, right, path: str = "") -> list[dict]:
    """Compare metadata by field/slot; hex differences retain exact byte offsets."""
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        changes = []
        for key in sorted(left.keys() | right.keys()):
            changes.extend(differences(left.get(key), right.get(key),
                                       f"{path}.{key}" if path else key))
        return changes
    if isinstance(left, list) and isinstance(right, list):
        changes = []
        for index in range(max(len(left), len(right))):
            changes.extend(differences(left[index] if index < len(left) else None,
                                       right[index] if index < len(right) else None,
                                       f"{path}[{index}]"))
        return changes
    change = {"field": path, "left": left, "right": right}
    if path.endswith("_hex") and isinstance(left, str) and isinstance(right, str):
        a, b = bytes.fromhex(left), bytes.fromhex(right)
        change["changed_byte_offsets"] = [
            i for i in range(max(len(a), len(b)))
            if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)
        ]
    return [change]


def compare(samples: dict) -> dict:
    return {
        f"{a} vs {b}": differences(
            {k: v for k, v in samples[a].items() if k != "source"},
            {k: v for k, v in samples[b].items() if k != "source"},
        ) for a, b in combinations(samples, 2)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native48", type=Path)
    parser.add_argument("native51", type=Path)
    parser.add_argument("dense_repack", type=Path)
    parser.add_argument("--json", action="store_true", help="emit full machine-readable report")
    args = parser.parse_args()
    samples = {label: snapshot(path) for label, path in (
        ("native48", args.native48), ("native51", args.native51),
        ("dense-repack", args.dense_repack),
    )}
    pairs = compare(samples)
    if args.json:
        print(json.dumps({"samples": samples, "comparisons": pairs}, indent=2))
        return
    print("Nested _hdb CFB metadata; read-only; all IDs are nested CFB IDs.")
    print("FAT/MiniFAT values: FREE=4294967295, END=4294967294, "
          "FAT=4294967293, DIFAT=4294967292; tree NO_STREAM=4294967295.")
    print("Directory matching is by index; names and raw slots remain visible.")
    for label, sample in samples.items():
        print(f"\n=== {label}: full metadata snapshot ===")
        for section, value in sample.items():
            if section in ("directory_entries", "streams"):
                for index, entry in value.items():
                    marker = " [ROOT]" if (section == "directory_entries" and
                             int(index) == sample["root_directory_index"]) else ""
                    if entry["name"] == "16":
                        marker += " [stream 16 (1.Program.pou)]"
                    print(f"{section}[{index}]{marker}: {json.dumps(entry)}")
            else:
                print(f"{section}: {json.dumps(value)}")
    for label, changes in pairs.items():
        print(f"\n=== {label} ===")
        print(f"{len(changes)} differing metadata values (derived/raw fields overlap)")
        for change in changes:
            print(json.dumps(change))


if __name__ == "__main__":
    main()
