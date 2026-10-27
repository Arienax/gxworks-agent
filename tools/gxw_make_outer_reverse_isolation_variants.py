# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import argparse
import hashlib
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


PROJECT_OUTER_STREAMS = (
    "projectdatalist.xml",
    "projectlist.xml",
    "history.xml",
)
USER_OUTER_STREAMS = (
    "user.xml",
    "dataprotection.xml",
)
ALL_OUTER_STREAMS = PROJECT_OUTER_STREAMS + USER_OUTER_STREAMS


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _replace_outer_streams(
    *,
    host_bytes: bytes,
    source_outer: CompoundFile,
    streams: tuple[str, ...],
) -> bytes:
    result = host_bytes
    for name in streams:
        current = CompoundFile(result)
        host_payload = current.read_stream(name)
        source_payload = source_outer.read_stream(name)
        if len(source_payload) > len(host_payload):
            raise GXWFormatError(
                f"reverse isolation expected source {name!r} to fit the native-host "
                f"allocation, but size is {len(host_payload)} -> {len(source_payload)}"
            )
        result = replace_stream_within_allocation(
            result,
            name,
            source_payload,
            allow_shrink=True,
        )
    return result


def _verify_variant(
    data: bytes,
    *,
    native_hdb: bytes,
    source_outer: CompoundFile,
    replaced: tuple[str, ...],
) -> tuple[list[str], list[tuple[int, int, int, int]]]:
    outer = CompoundFile(data)
    if outer.read_stream("_hdb") != native_hdb:
        raise GXWFormatError("variant unexpectedly changed native host _hdb")

    for name in replaced:
        if outer.read_stream(name) != source_outer.read_stream(name):
            raise GXWFormatError(f"variant outer stream {name!r} verification failed")

    resolver = GXWProjectResolver(outer)
    logical_name = resolver.choose_program_pou()
    program = parse_structured_pou(
        resolver.read_logical_file(logical_name),
        logical_name=logical_name,
    )
    nodes = [node.symbol for node in program.nodes]
    wires = [
        (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
        for wire in program.wires
    ]
    return nodes, wires


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reverse-isolate outer GXW metadata. Start from the native donor/host "
            "project whose Structured Ladder wires render, keep its nested _hdb "
            "byte-for-byte unchanged, and replace selected outer streams with the "
            "controlled base project's payloads. This tests whether outer XML/user "
            "stream contents control wire rendering."
        )
    )
    parser.add_argument(
        "base_payload_gxw",
        type=Path,
        help="Controlled base project supplying outer-stream payloads (sample 48).",
    )
    parser.add_argument(
        "native_host_gxw",
        type=Path,
        help="Native project whose wires render (sample 51).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory; defaults to the native host directory.",
    )
    args = parser.parse_args()

    base_outer = CompoundFile.from_file(args.base_payload_gxw)
    host_bytes = args.native_host_gxw.read_bytes()
    host_outer = CompoundFile(host_bytes, source=args.native_host_gxw)
    native_hdb = host_outer.read_stream("_hdb")

    variants = (
        ("O1_PROJECT_OUTER", PROJECT_OUTER_STREAMS),
        ("O2_USER_OUTER", USER_OUTER_STREAMS),
        ("O3_ALL_OUTER", ALL_OUTER_STREAMS),
    )

    out_dir = args.output_dir or args.native_host_gxw.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    host_stem = args.native_host_gxw.stem

    print("Outer-stream reverse isolation")
    print(f"  base payload source: {args.base_payload_gxw}")
    print(f"  native host:         {args.native_host_gxw}")
    print(f"  native _hdb sha:     {_sha(native_hdb)}")
    print()

    for label, streams in variants:
        variant = _replace_outer_streams(
            host_bytes=host_bytes,
            source_outer=base_outer,
            streams=streams,
        )
        nodes, wires = _verify_variant(
            variant,
            native_hdb=native_hdb,
            source_outer=base_outer,
            replaced=streams,
        )
        output = out_dir / f"{host_stem}_{label}.gxw"
        output.write_bytes(variant)
        print(f"{label}:")
        print(f"  output:      {output}")
        print(f"  replaced:    {', '.join(streams)}")
        print(f"  _hdb:        UNCHANGED sha {_sha(CompoundFile(variant).read_stream('_hdb'))}")
        print(f"  GXW bytes:   {len(host_bytes)} -> {len(variant)}")
        print(f"  nodes:       {nodes}")
        print(f"  wires:       {wires}")
        print()

    print("GX Works2 interpretation:")
    print("  PASS = all native horizontal wires still render.")
    print("  FAIL = nodes render but one or more native wires disappear.")
    print("  O1 FAIL -> split projectdatalist/projectlist/history next.")
    print("  O2 FAIL -> split user/dataprotection next.")
    print("  O1/O2/O3 PASS -> outer stream payloads are not the cause; focus on")
    print("                    nested _hdb CFB directory/MiniFAT/layout metadata.")


if __name__ == "__main__":
    main()
