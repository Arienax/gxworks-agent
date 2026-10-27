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
from gxw.container_growth_experimental import replace_regular_stream_with_appended_growth
from gxw.container_reallocation_experimental import (
    replace_ministream_with_contiguous_appended_reallocation,
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


def _mini_chain(cfb: CompoundFile, start_sector: int) -> list[int]:
    return CompoundFile._walk_chain(start_sector, cfb._minifat)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled GX Works2 experiment: generate X1->X2->Y1 from sample 48, "
            "then relocate the entire Program.pou payload to one fresh contiguous "
            "MiniFAT run instead of extending its old chain."
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
    before_entry = nested_before.get_stream_entry(program_stream)
    old_chain = _mini_chain(nested_before, before_entry.start_sector)
    root_before = inspect_root_ministream_allocation(hdb_before)

    hdb_after = replace_ministream_with_contiguous_appended_reallocation(
        hdb_before,
        program_stream,
        patched_pou,
    )
    nested_after = CompoundFile(hdb_after)
    after_entry = nested_after.get_stream_entry(program_stream)
    new_chain = _mini_chain(nested_after, after_entry.start_sector)
    root_after = inspect_root_ministream_allocation(hdb_after)

    if any(right != left + 1 for left, right in zip(new_chain, new_chain[1:])):
        raise GXWFormatError("generated Program.pou MiniFAT chain is not contiguous")

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
        raise GXWFormatError("end-to-end contiguous-reallocation verification failed")
    check_program = parse_structured_pou(check_pou, logical_name=check_name)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(outer_after)

    print("Contiguous Program.pou reallocation test created:")
    print(f"  source:             {args.gxw}")
    print(f"  output:             {args.output}")
    print(f"  edit:               insert {args.new_symbol} after {args.after_symbol}")
    print(f"  Program.pou:        {len(original_pou)} -> {len(patched_pou)} bytes")
    print(f"  old mini-chain:     {old_chain}")
    print(f"  new mini-chain:     {new_chain}")
    print(f"  new chain contiguous: YES")
    print(
        f"  root MiniStream:    {root_before.stream_size} -> {root_after.stream_size} bytes"
    )
    print(
        f"  root FAT chain:     {root_before.regular_chain_length} -> "
        f"{root_after.regular_chain_length} sectors"
    )
    print(f"  nested _hdb bytes:  {len(hdb_before)} -> {len(hdb_after)}")
    print(
        f"  outer _hdb chain:   {outer_hdb_before.chain_length} -> "
        f"{outer_hdb_after.chain_length} sectors"
    )
    print(f"  GXW bytes:          {len(outer_bytes)} -> {len(outer_after)}")
    print(f"  nodes:              {[node.symbol for node in check_program.nodes]}")
    print(
        "  wires:              "
        + str(
            [
                (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
                for wire in check_program.wires
            ]
        )
    )
    print("  parser:             OK")
    print()
    print("GX Works2 interpretation:")
    print("  PASS = all horizontal wires render.")
    print("         => the prior failure was caused by fragmenting Program.pou's MiniFAT chain.")
    print("  FAIL = nodes render but wires do not.")
    print("         => next test must repack the whole root MiniStream, not just Program.pou.")
    print("  OPEN ERROR = report separately.")


if __name__ == "__main__":
    main()
