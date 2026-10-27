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
from gxw.container_repack_experimental import repack_ministream_dense_with_replacement
from gxw.container_writer import (
    inspect_root_ministream_allocation,
    inspect_stream_allocation,
    replace_stream_within_allocation,
)
from gxw.models import GXWFormatError
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import (
    insert_series_contact_after,
    serialize_structured_pou,
)


def _logical_stream_name(resolver: GXWProjectResolver, logical_name: str) -> str:
    for item in resolver.logical_files():
        if item.logical_name == logical_name:
            return item.stream_name
    raise KeyError(logical_name)


def _mini_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._minifat)


def _mini_layout(cfb: CompoundFile) -> list[tuple[str, int, int, list[int]]]:
    rows = []
    for entry in sorted(cfb.directory_entries, key=lambda item: item.index):
        if (
            entry.is_stream
            and entry.name
            and entry.stream_size > 0
            and entry.stream_size < cfb.mini_stream_cutoff
        ):
            rows.append(
                (
                    entry.name,
                    entry.stream_size,
                    entry.start_sector,
                    _mini_chain(cfb, entry.start_sector),
                )
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled GX Works2 experiment: generate one series contact, then repack "
            "every nested _hdb MiniStream stream densely in directory-entry order. "
            "For sample 48 this uses the existing 22-sector root FAT allocation and "
            "therefore performs no nested or outer CFB growth."
        )
    )
    parser.add_argument("gxw", type=Path)
    parser.add_argument("after_symbol")
    parser.add_argument("new_symbol")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--program", default=None)
    parser.add_argument("--gap", type=int, default=3)
    args = parser.parse_args()

    source = args.gxw.resolve()
    output = args.output.resolve()
    if source == output:
        raise SystemExit("refusing to overwrite the source GXW")

    outer_bytes = args.gxw.read_bytes()
    outer = CompoundFile(outer_bytes, source=args.gxw)
    resolver = GXWProjectResolver(outer)
    logical_name = resolver.choose_program_pou(args.program)
    program_stream = _logical_stream_name(resolver, logical_name)

    original_pou = resolver.read_logical_file(logical_name)
    program = parse_structured_pou(
        original_pou,
        logical_name=logical_name,
        source_path=args.gxw,
    )
    modified = insert_series_contact_after(
        program,
        args.after_symbol,
        args.new_symbol,
        horizontal_gap=args.gap,
    )
    patched_pou = serialize_structured_pou(modified)

    hdb_before = outer.read_stream("_hdb")
    nested_before = CompoundFile(hdb_before)
    root_before = inspect_root_ministream_allocation(hdb_before)
    before_layout = _mini_layout(nested_before)
    before_entry = nested_before.get_stream_entry(program_stream)
    old_program_chain = _mini_chain(nested_before, before_entry.start_sector)

    hdb_after = repack_ministream_dense_with_replacement(
        hdb_before,
        program_stream,
        patched_pou,
    )
    if len(hdb_after) != len(hdb_before):
        raise GXWFormatError("dense repack should not change nested _hdb length")

    nested_after = CompoundFile(hdb_after)
    root_after = inspect_root_ministream_allocation(hdb_after)
    after_layout = _mini_layout(nested_after)
    after_entry = nested_after.get_stream_entry(program_stream)
    new_program_chain = _mini_chain(nested_after, after_entry.start_sector)

    # For this controlled sample-48 experiment the root regular FAT chain must stay
    # unchanged.  The whole point is to isolate dense MiniStream repacking without
    # any physical CFB growth.
    if root_after.regular_chain_length != root_before.regular_chain_length:
        raise GXWFormatError("dense repack unexpectedly changed root FAT chain length")

    outer_hdb_before = inspect_stream_allocation(outer_bytes, "_hdb")
    if len(hdb_after) > outer_hdb_before.allocation_capacity:
        raise GXWFormatError("dense nested repack unexpectedly requires outer growth")
    outer_after = replace_stream_within_allocation(
        outer_bytes,
        "_hdb",
        hdb_after,
    )
    if len(outer_after) != len(outer_bytes):
        raise GXWFormatError("dense repack unexpectedly changed GXW byte length")

    check_resolver = GXWProjectResolver(CompoundFile(outer_after, source=args.output))
    check_name = check_resolver.choose_program_pou(logical_name)
    check_pou = check_resolver.read_logical_file(check_name)
    if check_pou != patched_pou:
        raise GXWFormatError("end-to-end dense-repack Program.pou verification failed")
    check_program = parse_structured_pou(check_pou, logical_name=check_name)

    # Dense invariant: every MiniStream stream begins immediately after the previous
    # stream's last mini-sector, starting at zero.
    expected = 0
    for name, size, start, chain in after_layout:
        if not chain or chain[0] != expected:
            raise GXWFormatError(
                f"post-repack MiniStream is not dense before stream {name!r}"
            )
        if any(right != left + 1 for left, right in zip(chain, chain[1:])):
            raise GXWFormatError(
                f"post-repack stream {name!r} is not internally contiguous"
            )
        expected = chain[-1] + 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(outer_after)

    print("Dense whole-MiniStream repack test created:")
    print(f"  source:               {args.gxw}")
    print(f"  output:               {args.output}")
    print(f"  edit:                 insert {args.new_symbol} after {args.after_symbol}")
    print(f"  Program.pou:          {len(original_pou)} -> {len(patched_pou)} bytes")
    print(f"  old Program chain:    {old_program_chain}")
    print(f"  new Program chain:    {new_program_chain}")
    print(f"  MiniStream streams:   {len(after_layout)}")
    print(f"  packed mini-sectors:  {expected}")
    print(
        f"  root MiniStream:      {root_before.stream_size} -> "
        f"{root_after.stream_size} bytes"
    )
    print(
        f"  root FAT chain:       {root_before.regular_chain_length} -> "
        f"{root_after.regular_chain_length} sectors"
    )
    print(f"  nested _hdb bytes:    {len(hdb_before)} -> {len(hdb_after)}")
    print(
        f"  outer _hdb chain:     {outer_hdb_before.chain_length} -> "
        f"{inspect_stream_allocation(outer_after, '_hdb').chain_length} sectors"
    )
    print(f"  GXW bytes:            {len(outer_bytes)} -> {len(outer_after)}")
    print(f"  dense allocation:     YES")
    print(f"  nodes:                {[node.symbol for node in check_program.nodes]}")
    print(
        "  wires:                "
        + str(
            [
                (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
                for wire in check_program.wires
            ]
        )
    )
    print("  parser:               OK")
    print()
    print("Key expected sample-48 behavior:")
    print("  - no Program.pou tail jump to mini-sector 174/176;")
    print("  - Program.pou should become one contiguous run beginning at mini-sector 27;")
    print("  - every later small stream is shifted forward by two mini-sectors;")
    print("  - total allocation should be mini-sectors 0..175 with no holes;")
    print("  - nested _hdb and outer GXW file lengths should remain unchanged.")
    print()
    print("GX Works2 interpretation:")
    print("  PASS = all horizontal wires render.")
    print("         => GX Works2 requires/canonicalizes dense whole-root MiniStream packing")
    print("            for this Structured Ladder edit path.")
    print("  FAIL = nodes render but wires do not.")
    print("         => allocation topology alone is still insufficient; next compare native")
    print("            sample-48->51 directory/MiniFAT metadata and CFB header-level state.")
    print("  OPEN ERROR = report separately.")


if __name__ == "__main__":
    main()
