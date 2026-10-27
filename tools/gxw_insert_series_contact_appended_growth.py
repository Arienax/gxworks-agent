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
from gxw.container_growth_experimental import (
    replace_ministream_with_appended_root_growth,
    replace_regular_stream_with_appended_growth,
)
from gxw.container_writer import (
    inspect_root_ministream_allocation,
    inspect_stream_allocation,
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled GXW structure-write experiment using physical append-only CFB "
            "growth. Unlike gxw_insert_series_contact.py, this path deliberately does "
            "not consume trailing root-MiniStream FAT slack."
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
    pou_alloc_before = inspect_stream_allocation(hdb_before, program_stream)
    root_before = inspect_root_ministream_allocation(hdb_before)

    if len(patched_pou) <= pou_alloc_before.allocation_capacity:
        raise GXWFormatError(
            "this experiment is only meaningful when Program.pou requires allocation growth"
        )

    hdb_after = replace_ministream_with_appended_root_growth(
        hdb_before,
        program_stream,
        patched_pou,
    )
    pou_alloc_after = inspect_stream_allocation(hdb_after, program_stream)
    root_after = inspect_root_ministream_allocation(hdb_after)

    outer_hdb_before = inspect_stream_allocation(outer_bytes, "_hdb")
    outer_after = replace_regular_stream_with_appended_growth(
        outer_bytes,
        "_hdb",
        hdb_after,
    )
    outer_hdb_after = inspect_stream_allocation(outer_after, "_hdb")

    check_resolver = GXWProjectResolver(CompoundFile(outer_after, source=args.output))
    check_name = check_resolver.choose_program_pou(logical_name)
    check_pou = check_resolver.read_logical_file(check_name)
    if check_pou != patched_pou:
        raise GXWFormatError("end-to-end appended-growth Program.pou verification failed")
    check_program = parse_structured_pou(check_pou, logical_name=check_name)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(outer_after)

    print("Append-only CFB growth structure test created:")
    print(f"  source:            {args.gxw}")
    print(f"  output:            {args.output}")
    print(f"  edit:              insert {args.new_symbol} after {args.after_symbol}")
    print(f"  Program.pou:       {len(original_pou)} -> {len(patched_pou)} bytes")
    print(
        f"  Program mini-chain:{pou_alloc_before.chain_length} -> "
        f"{pou_alloc_after.chain_length} sectors"
    )
    print(
        f"  root MiniStream:   {root_before.stream_size} -> {root_after.stream_size} bytes"
    )
    print(
        f"  root FAT chain:    {root_before.regular_chain_length} -> "
        f"{root_after.regular_chain_length} sectors"
    )
    print(f"  nested _hdb bytes: {len(hdb_before)} -> {len(hdb_after)}")
    print(
        f"  outer _hdb chain:  {outer_hdb_before.chain_length} -> "
        f"{outer_hdb_after.chain_length} sectors"
    )
    print(f"  GXW bytes:         {len(outer_bytes)} -> {len(outer_after)}")
    print(f"  nodes:             {[node.symbol for node in check_program.nodes]}")
    print(
        "  wires:             "
        + str(
            [
                (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
                for wire in check_program.wires
            ]
        )
    )
    print("  parser:            OK")
    print()
    print("GX Works2 interpretation:")
    print("  PASS = all expected wires render. This confirms physical root-FAT/outer-FAT")
    print("         growth fixes the failure seen with slack-only MiniStream growth.")
    print("  FAIL = nodes render but wires do not. Then allocation remains the culprit,")
    print("         but GX Works2 requires an additional native CFB allocation invariant.")
    print("  OPEN ERROR = report separately.")


if __name__ == "__main__":
    main()
