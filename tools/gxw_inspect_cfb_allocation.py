# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.container import CompoundFile
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou


def _logical_stream_name(resolver: GXWProjectResolver, logical_name: str) -> str:
    for item in resolver.logical_files():
        if item.logical_name == logical_name:
            return item.stream_name
    raise KeyError(logical_name)


def _regular_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._fat)


def _mini_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._minifat)


def _fmt_chain(chain: list[int], limit: int = 24) -> str:
    if len(chain) <= limit:
        return "[" + ", ".join(str(value) for value in chain) + "]"
    head = ", ".join(str(value) for value in chain[: limit // 2])
    tail = ", ".join(str(value) for value in chain[-limit // 2 :])
    return f"[{head}, ..., {tail}]"


def inspect(path: Path) -> None:
    outer = CompoundFile.from_file(path)
    resolver = GXWProjectResolver(outer)
    program_name = resolver.choose_program_pou()
    program_stream = _logical_stream_name(resolver, program_name)

    outer_hdb_entry = outer.get_stream_entry("_hdb")
    outer_hdb_chain = _regular_chain(outer, outer_hdb_entry.start_sector)
    hdb_bytes = outer.read_stream("_hdb")
    hdb = CompoundFile(hdb_bytes)

    root = hdb.root_entry
    root_chain = _regular_chain(hdb, root.start_sector) if root.stream_size else []
    root_capacity = len(root_chain) * hdb.sector_size

    program_entry = hdb.get_stream_entry(program_stream)
    if program_entry.stream_size < hdb.mini_stream_cutoff:
        program_chain = _mini_chain(hdb, program_entry.start_sector)
        program_storage = "MiniStream"
        program_capacity = len(program_chain) * hdb.mini_sector_size
    else:
        program_chain = _regular_chain(hdb, program_entry.start_sector)
        program_storage = "regular FAT"
        program_capacity = len(program_chain) * hdb.sector_size

    pou = resolver.read_logical_file(program_name)
    program = parse_structured_pou(pou, logical_name=program_name, source_path=path)

    print(f"FILE: {path}")
    print(f"  GXW bytes: {path.stat().st_size}")
    print("  outer _hdb:")
    print(f"    stream_size: {outer_hdb_entry.stream_size}")
    print(f"    start_sector: {outer_hdb_entry.start_sector}")
    print(f"    FAT chain: {len(outer_hdb_chain)} sectors / {len(outer_hdb_chain) * outer.sector_size} bytes capacity")
    print(f"    FAT chain ids: {_fmt_chain(outer_hdb_chain)}")
    print("  nested _hdb CFB:")
    print(f"    bytes: {len(hdb_bytes)}")
    print(f"    sector_size: {hdb.sector_size}")
    print(f"    num_fat_sectors: {hdb.num_fat_sectors}")
    print(f"    num_minifat_sectors: {hdb.num_minifat_sectors}")
    print(f"    first_minifat_sector: {hdb.first_minifat_sector}")
    print("  root MiniStream:")
    print(f"    stream_size: {root.stream_size}")
    print(f"    start_sector: {root.start_sector}")
    print(f"    FAT chain: {len(root_chain)} sectors / {root_capacity} bytes capacity")
    print(f"    trailing FAT slack: {root_capacity - root.stream_size} bytes")
    print(f"    backed mini-sectors: {root.stream_size // hdb.mini_sector_size}")
    print(f"    physical mini capacity: {root_capacity // hdb.mini_sector_size}")
    print(f"    root FAT chain ids: {_fmt_chain(root_chain)}")
    print("  Program.pou:")
    print(f"    logical: {program_name}")
    print(f"    stream: {program_stream}")
    print(f"    storage: {program_storage}")
    print(f"    stream_size: {program_entry.stream_size}")
    print(f"    start_sector: {program_entry.start_sector}")
    print(f"    allocation: {len(program_chain)} sectors / {program_capacity} bytes capacity")
    print(f"    chain ids: {_fmt_chain(program_chain)}")
    print(f"    records/nodes/wires: {program.record_count}/{len(program.nodes)}/{len(program.wires)}")
    print(f"    node symbols: {[node.symbol for node in program.nodes]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the outer _hdb allocation, nested root MiniStream allocation, "
            "and Program.pou MiniFAT/FAT chain for one or more GXW files. This is "
            "intended to compare native GX Works2 growth against experimental writer growth."
        )
    )
    parser.add_argument("gxw", nargs="+", type=Path)
    args = parser.parse_args()

    for path in args.gxw:
        inspect(path)


if __name__ == "__main__":
    main()
