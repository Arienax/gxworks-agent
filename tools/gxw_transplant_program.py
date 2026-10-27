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
from gxw.container_writer import (
    inspect_stream_allocation,
    replace_stream_with_ministream_growth,
    replace_stream_within_allocation,
)
from gxw.models import GXWFormatError
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import replace_node_symbol, serialize_structured_pou


def _logical_stream_name(resolver: GXWProjectResolver, logical_name: str) -> str:
    for item in resolver.logical_files():
        if item.logical_name == logical_name:
            return item.stream_name
    raise KeyError(logical_name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic: transplant a donor Structured Ladder/FBD Program.pou into "
            "a base GXW while leaving the base project's other logical streams intact. "
            "This isolates whether GX Works2 wire rendering depends on state outside "
            "Program.pou."
        )
    )
    parser.add_argument("base_gxw", type=Path)
    parser.add_argument("donor_gxw", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--base-program", default=None)
    parser.add_argument("--donor-program", default=None)
    parser.add_argument(
        "--rename-old",
        default=None,
        help="Optional donor node symbol to rename before transplant, e.g. M1.",
    )
    parser.add_argument(
        "--rename-new",
        default=None,
        help="Replacement donor node symbol, e.g. X2. Must be used with --rename-old.",
    )
    args = parser.parse_args()

    if (args.rename_old is None) != (args.rename_new is None):
        raise SystemExit("--rename-old and --rename-new must be supplied together")

    base_path = args.base_gxw.resolve()
    donor_path = args.donor_gxw.resolve()
    output = args.output.resolve()
    if output in (base_path, donor_path):
        raise SystemExit("refusing to overwrite the base or donor GXW")

    base_bytes = args.base_gxw.read_bytes()
    donor_bytes = args.donor_gxw.read_bytes()
    base_outer = CompoundFile(base_bytes, source=args.base_gxw)
    donor_outer = CompoundFile(donor_bytes, source=args.donor_gxw)
    base_resolver = GXWProjectResolver(base_outer)
    donor_resolver = GXWProjectResolver(donor_outer)

    base_name = base_resolver.choose_program_pou(args.base_program)
    donor_name = donor_resolver.choose_program_pou(args.donor_program)
    base_stream = _logical_stream_name(base_resolver, base_name)

    donor_pou = donor_resolver.read_logical_file(donor_name)
    donor_program = parse_structured_pou(
        donor_pou,
        logical_name=donor_name,
        source_path=args.donor_gxw,
    )

    if args.rename_old is not None:
        donor_program = replace_node_symbol(
            donor_program,
            args.rename_old,
            args.rename_new,
        )
        donor_pou = serialize_structured_pou(donor_program)

    base_hdb = base_outer.read_stream("_hdb")
    before = inspect_stream_allocation(base_hdb, base_stream)

    if len(donor_pou) <= before.allocation_capacity:
        new_hdb = replace_stream_within_allocation(
            base_hdb,
            base_stream,
            donor_pou,
        )
        allocation_mode = "existing chain"
    else:
        new_hdb = replace_stream_with_ministream_growth(
            base_hdb,
            base_stream,
            donor_pou,
        )
        allocation_mode = "MiniStream growth"

    if len(new_hdb) != len(base_hdb):
        raise GXWFormatError(
            "diagnostic expected nested _hdb byte length to remain unchanged"
        )

    new_outer = replace_stream_within_allocation(
        base_bytes,
        "_hdb",
        new_hdb,
    )

    check = GXWProjectResolver(CompoundFile(new_outer, source=args.output))
    check_name = check.choose_program_pou(base_name)
    check_pou = check.read_logical_file(check_name)
    if check_pou != donor_pou:
        raise GXWFormatError("end-to-end transplant verification failed")
    check_program = parse_structured_pou(check_pou, logical_name=check_name)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(new_outer)

    print("Program.pou transplant completed:")
    print(f"  base:       {args.base_gxw}")
    print(f"  donor:      {args.donor_gxw}")
    print(f"  output:     {args.output}")
    print(f"  logical:    {base_name} <- {donor_name}")
    if args.rename_old is not None:
        print(f"  donor edit: {args.rename_old} -> {args.rename_new}")
    print(f"  size:       {len(donor_pou)} bytes")
    print(f"  records:    {check_program.record_count}")
    print(f"  nodes:      {[node.symbol for node in check_program.nodes]}")
    print(f"  wires:      {len(check_program.wires)}")
    print(f"  allocation: {allocation_mode}")
    print("  parser:     OK")
    print()
    print("Interpretation:")
    print("  - If donor wires render in the base project, the missing-wire bug is inside")
    print("    the generated Program.pou/layout, not an external project dependency.")
    print("  - If donor nodes render but donor wires still do not, compare other logical")
    print("    streams between base and donor; wire rendering depends on state outside")
    print("    Program.pou or on a cross-stream consistency field.")


if __name__ == "__main__":
    main()
