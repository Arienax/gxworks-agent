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
from gxw.container_writer import replace_stream_within_allocation
from gxw.models import GXWFormatError
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou


# Reverse isolation starts from the native donor project, whose wires are known to
# render, and replaces non-Program nested streams with their sample-48 versions.
# Every observed base payload is <= the corresponding donor payload, so these
# variants exercise only conservative same-allocation/shrink writes.
EDITOR_AND_PROJECT_STATE = ("1", "10", "14", "15", "23", "39")
DERIVED_AND_COMPILER_STATE = ("31", "32", "33", "34", "35", "37")
ALL_NONPROGRAM_CHANGED = EDITOR_AND_PROJECT_STATE + DERIVED_AND_COMPILER_STATE


def _replace_nested_from_base(
    donor_hdb_bytes: bytes,
    base_hdb: CompoundFile,
    stream_names: tuple[str, ...],
) -> bytes:
    current = donor_hdb_bytes
    for stream in stream_names:
        donor_cfb = CompoundFile(current)
        try:
            donor_payload = donor_cfb.read_stream(stream)
        except KeyError as exc:
            raise GXWFormatError(f"donor _hdb stream {stream!r} is missing") from exc
        try:
            base_payload = base_hdb.read_stream(stream)
        except KeyError as exc:
            raise GXWFormatError(f"base _hdb stream {stream!r} is missing") from exc

        if len(base_payload) > len(donor_payload):
            raise GXWFormatError(
                f"reverse-isolation stream {stream!r} would grow "
                f"{len(donor_payload)} -> {len(base_payload)} bytes; "
                "this diagnostic intentionally supports only same-size/shrink writes"
            )

        current = replace_stream_within_allocation(
            current,
            stream,
            base_payload,
            allow_shrink=True,
        )
    return current


def _build_variant(
    *,
    donor_bytes: bytes,
    base_hdb: CompoundFile,
    donor_program_name: str,
    donor_program_bytes: bytes,
    stream_names: tuple[str, ...],
) -> bytes:
    donor_outer = CompoundFile(donor_bytes)
    donor_hdb_bytes = donor_outer.read_stream("_hdb")
    patched_hdb = _replace_nested_from_base(
        donor_hdb_bytes,
        base_hdb,
        stream_names,
    )

    if len(patched_hdb) != len(donor_hdb_bytes):
        raise GXWFormatError(
            "reverse-isolation nested _hdb unexpectedly changed container byte length"
        )

    result = replace_stream_within_allocation(
        donor_bytes,
        "_hdb",
        patched_hdb,
        allow_shrink=True,
    )

    # The native donor Program.pou is the fixed control variable. If it changes,
    # this experiment is invalid.
    check = GXWProjectResolver(CompoundFile(result))
    check_name = check.choose_program_pou(donor_program_name)
    check_program_bytes = check.read_logical_file(check_name)
    if check_program_bytes != donor_program_bytes:
        raise GXWFormatError(
            "reverse-isolation verification failed: donor Program.pou changed"
        )
    parse_structured_pou(check_program_bytes, logical_name=check_name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build reverse-isolation GXW variants. Unlike the earlier forward test, "
            "these start from the native donor project whose wires already render, "
            "keep donor Program.pou byte-identical, and replace groups of other nested "
            "_hdb streams with their base-project versions. This covers size-changing "
            "candidates without requiring any CFB growth."
        )
    )
    parser.add_argument("base_gxw", type=Path, help="Native simple project, e.g. sample 48")
    parser.add_argument("donor_gxw", type=Path, help="Native wired series project, e.g. sample 51")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for variants; defaults to donor GXW directory.",
    )
    args = parser.parse_args()

    base_bytes = args.base_gxw.read_bytes()
    donor_bytes = args.donor_gxw.read_bytes()
    base_outer = CompoundFile(base_bytes, source=args.base_gxw)
    donor_outer = CompoundFile(donor_bytes, source=args.donor_gxw)
    base_hdb = CompoundFile(base_outer.read_stream("_hdb"))

    donor_resolver = GXWProjectResolver(donor_outer)
    donor_program_name = donor_resolver.choose_program_pou()
    donor_program_bytes = donor_resolver.read_logical_file(donor_program_name)
    donor_program = parse_structured_pou(
        donor_program_bytes,
        logical_name=donor_program_name,
        source_path=args.donor_gxw,
    )

    out_dir = args.output_dir or args.donor_gxw.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.donor_gxw.stem

    variants = [
        (
            "REV_C_EDITOR_PROJECT",
            EDITOR_AND_PROJECT_STATE,
            "editor/project + labels + Gppw2.gpj",
        ),
        (
            "REV_D_DERIVED_COMPILER",
            DERIVED_AND_COMPILER_STATE,
            "derived/compiler/analysis state",
        ),
        (
            "REV_E_ALL_NONPROGRAM",
            ALL_NONPROGRAM_CHANGED,
            "all changed nested _hdb streams except Program.pou",
        ),
    ]

    print("Reverse wire-state isolation")
    print(f"  fixed donor Program.pou: {donor_program_name}")
    print(f"  donor nodes: {[node.symbol for node in donor_program.nodes]}")
    print(f"  donor wires: {len(donor_program.wires)}")
    print("  NOTE: all variants intentionally keep these nodes/Program.pou identical.")
    print("        The only experimental variable is the non-Program stream group.\n")

    for suffix, streams, description in variants:
        result = _build_variant(
            donor_bytes=donor_bytes,
            base_hdb=base_hdb,
            donor_program_name=donor_program_name,
            donor_program_bytes=donor_program_bytes,
            stream_names=streams,
        )
        output = out_dir / f"{stem}_{suffix}.gxw"
        output.write_bytes(result)
        print(f"  {suffix}: {output}")
        print(f"    replace from base: {', '.join(streams)}")
        print(f"    group: {description}")

    print()
    print("Open the three generated files in GX Works2 and report only wire rendering:")
    print("  C PASS / FAIL")
    print("  D PASS / FAIL")
    print("  E PASS / FAIL")
    print()
    print("Interpretation:")
    print("  C FAIL -> split streams 1/10/14/15/23/39.")
    print("  D FAIL -> split streams 31/32/33/34/35/37.")
    print("  C PASS + D PASS + E FAIL -> cross-group interaction; bisect combinations.")
    print("  C PASS + D PASS + E PASS -> non-Program nested _hdb state is ruled out;")
    print("                                move to outer streams such as user.xml/project metadata.")


if __name__ == "__main__":
    main()
