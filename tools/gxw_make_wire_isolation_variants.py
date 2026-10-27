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


EDITOR_METADATA_STREAMS = ("1", "10", "15", "23")
DERIVED_STATE_STREAMS = ("31", "32", "33", "37")


def _logical_stream_name(resolver: GXWProjectResolver, logical_name: str) -> str:
    for item in resolver.logical_files():
        if item.logical_name == logical_name:
            return item.stream_name
    raise KeyError(logical_name)


def _replace_exact_size_nested_stream(hdb: bytes, donor_hdb: CompoundFile, stream: str) -> bytes:
    try:
        donor_payload = donor_hdb.read_stream(stream)
    except KeyError as exc:
        raise GXWFormatError(f"donor _hdb stream {stream!r} is missing") from exc

    base_cfb = CompoundFile(hdb)
    try:
        base_payload = base_cfb.read_stream(stream)
    except KeyError as exc:
        raise GXWFormatError(f"base _hdb stream {stream!r} is missing") from exc

    if len(base_payload) != len(donor_payload):
        raise GXWFormatError(
            f"diagnostic stream {stream!r} is not exact-size compatible: "
            f"{len(base_payload)} -> {len(donor_payload)}"
        )

    return replace_stream_within_allocation(
        hdb,
        stream,
        donor_payload,
        allow_shrink=True,
    )


def _build_variant(
    *,
    base_bytes: bytes,
    donor_outer: CompoundFile,
    donor_hdb: CompoundFile,
    donor_pou: bytes,
    base_program_stream: str,
    extra_streams: tuple[str, ...],
) -> bytes:
    base_outer = CompoundFile(base_bytes)
    hdb = base_outer.read_stream("_hdb")

    before = inspect_stream_allocation(hdb, base_program_stream)
    if len(donor_pou) <= before.allocation_capacity:
        hdb = replace_stream_within_allocation(
            hdb,
            base_program_stream,
            donor_pou,
        )
    else:
        hdb = replace_stream_with_ministream_growth(
            hdb,
            base_program_stream,
            donor_pou,
        )

    for stream in extra_streams:
        hdb = _replace_exact_size_nested_stream(hdb, donor_hdb, stream)

    if len(hdb) != len(base_outer.read_stream("_hdb")):
        raise GXWFormatError("diagnostic variant unexpectedly changed nested _hdb byte length")

    result = replace_stream_within_allocation(
        base_bytes,
        "_hdb",
        hdb,
        allow_shrink=True,
    )

    # End-to-end sanity check that the donor Program.pou survived the additional
    # stream replacements.
    check = GXWProjectResolver(CompoundFile(result))
    check_name = check.choose_program_pou()
    check_pou = check.read_logical_file(check_name)
    if check_pou != donor_pou:
        raise GXWFormatError("variant verification failed: Program.pou changed unexpectedly")
    parse_structured_pou(check_pou, logical_name=check_name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build two controlled GXW variants for isolating the external state that "
            "controls Structured Ladder wire rendering. Both variants transplant the "
            "donor Program.pou; variant A additionally transplants likely editor metadata "
            "streams, while variant B transplants same-size compiler/analysis state."
        )
    )
    parser.add_argument("base_gxw", type=Path)
    parser.add_argument("donor_gxw", type=Path)
    parser.add_argument("--rename-old", default="M1")
    parser.add_argument("--rename-new", default="X2")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated variants; defaults to the base GXW directory.",
    )
    args = parser.parse_args()

    base_bytes = args.base_gxw.read_bytes()
    donor_bytes = args.donor_gxw.read_bytes()
    base_outer = CompoundFile(base_bytes, source=args.base_gxw)
    donor_outer = CompoundFile(donor_bytes, source=args.donor_gxw)
    base_resolver = GXWProjectResolver(base_outer)
    donor_resolver = GXWProjectResolver(donor_outer)

    base_program_name = base_resolver.choose_program_pou()
    donor_program_name = donor_resolver.choose_program_pou()
    base_program_stream = _logical_stream_name(base_resolver, base_program_name)

    donor_pou = donor_resolver.read_logical_file(donor_program_name)
    donor_program = parse_structured_pou(
        donor_pou,
        logical_name=donor_program_name,
        source_path=args.donor_gxw,
    )
    donor_program = replace_node_symbol(
        donor_program,
        args.rename_old,
        args.rename_new,
    )
    donor_pou = serialize_structured_pou(donor_program)

    donor_hdb = CompoundFile(donor_outer.read_stream("_hdb"))
    out_dir = args.output_dir or args.base_gxw.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    variant_a = _build_variant(
        base_bytes=base_bytes,
        donor_outer=donor_outer,
        donor_hdb=donor_hdb,
        donor_pou=donor_pou,
        base_program_stream=base_program_stream,
        extra_streams=EDITOR_METADATA_STREAMS,
    )
    variant_b = _build_variant(
        base_bytes=base_bytes,
        donor_outer=donor_outer,
        donor_hdb=donor_hdb,
        donor_pou=donor_pou,
        base_program_stream=base_program_stream,
        extra_streams=DERIVED_STATE_STREAMS,
    )

    stem = args.base_gxw.stem
    out_a = out_dir / f"{stem}_WIRE_AB_A_EDITOR_METADATA.gxw"
    out_b = out_dir / f"{stem}_WIRE_AB_B_DERIVED_STATE.gxw"
    out_a.write_bytes(variant_a)
    out_b.write_bytes(variant_b)

    print("Wire-state isolation variants created:")
    print(f"  A: {out_a}")
    print(f"     donor Program.pou + streams {', '.join(EDITOR_METADATA_STREAMS)}")
    print(f"  B: {out_b}")
    print(f"     donor Program.pou + streams {', '.join(DERIVED_STATE_STREAMS)}")
    print()
    print("Open both in GX Works2 and report only whether the horizontal wires render:")
    print("  A PASS / FAIL")
    print("  B PASS / FAIL")
    print()
    print("Interpretation:")
    print("  A PASS, B FAIL -> split streams 1/10/15/23 next.")
    print("  A FAIL, B PASS -> split streams 31/32/33/37 next.")
    print("  A FAIL, B FAIL -> next suspects require size-changing or outer-stream tests,")
    print("                    especially Gppw2.gpj and user.xml.")
    print("  A PASS, B PASS -> test overlap/redundancy with individual-stream variants.")


if __name__ == "__main__":
    main()
