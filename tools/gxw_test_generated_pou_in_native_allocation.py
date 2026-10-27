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
from gxw.container_writer import inspect_stream_allocation, replace_stream_within_allocation
from gxw.models import GXWFormatError
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import insert_series_contact_after, serialize_structured_pou


def _logical_stream_name(resolver: GXWProjectResolver, logical_name: str) -> str:
    for item in resolver.logical_files():
        if item.logical_name == logical_name:
            return item.stream_name
    raise KeyError(logical_name)


def _wire_tuple(wire) -> tuple[int, int, int, int]:
    return (wire.start.x, wire.start.y, wire.end.x, wire.end.y)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a structure-edited Program.pou from a small source project, then "
            "place that exact Program.pou into a native host project whose Program.pou "
            "already has enough MiniStream allocation. This isolates Program.pou/layout "
            "correctness from MiniFAT/root-MiniStream growth behavior."
        )
    )
    parser.add_argument("source_gxw", type=Path, help="Small source project, e.g. sample 48")
    parser.add_argument("host_gxw", type=Path, help="Native larger-allocation host, e.g. sample 51")
    parser.add_argument("after_symbol", nargs="?", default="X1")
    parser.add_argument("new_symbol", nargs="?", default="X2")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source_gxw.resolve()
    host_path = args.host_gxw.resolve()
    output_path = args.output.resolve()
    if output_path in (source_path, host_path):
        raise SystemExit("refusing to overwrite source or host GXW")

    source_outer = CompoundFile.from_file(args.source_gxw)
    source_resolver = GXWProjectResolver(source_outer)
    source_name = source_resolver.choose_program_pou()
    source_raw = source_resolver.read_logical_file(source_name)
    source_program = parse_structured_pou(
        source_raw,
        logical_name=source_name,
        source_path=args.source_gxw,
    )

    generated = insert_series_contact_after(
        source_program,
        args.after_symbol,
        args.new_symbol,
    )
    generated_pou = serialize_structured_pou(generated)
    generated_check = parse_structured_pou(generated_pou, logical_name=source_name)

    host_bytes = args.host_gxw.read_bytes()
    host_outer = CompoundFile(host_bytes, source=args.host_gxw)
    host_resolver = GXWProjectResolver(host_outer)
    host_name = host_resolver.choose_program_pou()
    host_stream = _logical_stream_name(host_resolver, host_name)
    host_hdb = host_outer.read_stream("_hdb")
    allocation = inspect_stream_allocation(host_hdb, host_stream)

    if allocation.storage != "mini":
        raise GXWFormatError(
            f"host Program.pou is not MiniStream-backed: {allocation.storage}"
        )
    if len(generated_pou) > allocation.allocation_capacity:
        raise GXWFormatError(
            "host does not have enough existing Program.pou allocation for this test: "
            f"generated {len(generated_pou)} bytes > capacity {allocation.allocation_capacity}"
        )

    # Critical diagnostic property: no MiniFAT/root-MiniStream/FAT growth occurs here.
    new_hdb = replace_stream_within_allocation(
        host_hdb,
        host_stream,
        generated_pou,
        allow_shrink=True,
    )
    if len(new_hdb) != len(host_hdb):
        raise GXWFormatError("diagnostic unexpectedly changed nested _hdb file length")

    new_outer = replace_stream_within_allocation(
        host_bytes,
        "_hdb",
        new_hdb,
        allow_shrink=True,
    )
    if len(new_outer) != len(host_bytes):
        raise GXWFormatError("diagnostic unexpectedly changed outer GXW file length")

    verify_resolver = GXWProjectResolver(CompoundFile(new_outer, source=args.output))
    verify_raw = verify_resolver.read_logical_file(verify_resolver.choose_program_pou())
    if verify_raw != generated_pou:
        raise GXWFormatError("end-to-end verification failed: host Program.pou differs")
    verify_program = parse_structured_pou(verify_raw, logical_name=host_name)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(new_outer)

    print("Generated-POU / native-allocation isolation file created:")
    print(f"  source structure: {args.source_gxw}")
    print(f"  native host:      {args.host_gxw}")
    print(f"  output:           {args.output}")
    print(f"  edit:             insert {args.new_symbol} after {args.after_symbol}")
    print(f"  generated size:   {len(generated_pou)} bytes")
    print(
        f"  host allocation:  {allocation.chain_length} mini-sectors / "
        f"{allocation.allocation_capacity} bytes"
    )
    print("  allocation growth: NONE")
    print(f"  records:          {verify_program.record_count}")
    print(f"  nodes:            {[node.symbol for node in verify_program.nodes]}")
    print(f"  wires:            {[_wire_tuple(wire) for wire in verify_program.wires]}")
    print("  parser:           OK")
    print()
    print("GX Works2 interpretation:")
    print("  PASS = all expected horizontal wires render.")
    print("         => generated Program.pou/layout is accepted; our MiniStream growth path")
    print("            is the remaining primary suspect.")
    print("  FAIL = nodes render but one or more expected wires do not.")
    print("         => do NOT blame allocation yet; the generated Program.pou/layout still")
    print("            differs in a GX Works2-significant way from the native sample 51 POU.")
    print("  OPEN ERROR = report separately; this test performs no allocation growth.")


if __name__ == "__main__":
    main()
