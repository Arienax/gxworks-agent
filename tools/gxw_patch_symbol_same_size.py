# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.models import GXWFormatError
from gxw.structured_pou import parse_structured_pou


def _encode_symbol(symbol: str) -> bytes:
    # Program.pou stores UTF-16LE text followed by a terminating NUL code unit.
    return symbol.encode("utf-16le") + b"\x00\x00"


def _find_target_node(raw_pou: bytes, logical_name: str, old_symbol: str, node_index: int | None):
    program = parse_structured_pou(raw_pou, logical_name=logical_name)

    matches = [node for node in program.nodes if node.symbol == old_symbol]
    if node_index is not None:
        matches = [node for node in matches if node.offset == node_index]

    if not matches:
        raise GXWFormatError(
            f"no structured node with symbol {old_symbol!r}"
            + (f" at offset 0x{node_index:X}" if node_index is not None else "")
        )
    if len(matches) > 1:
        offsets = ", ".join(f"0x{node.offset:X}" for node in matches)
        raise GXWFormatError(
            f"symbol {old_symbol!r} occurs in multiple nodes ({offsets}); "
            "rerun with --node-offset 0x..."
        )
    return matches[0]


def _patch_node_symbol_same_size(
    raw_pou: bytes,
    *,
    logical_name: str,
    old_symbol: str,
    new_symbol: str,
    node_offset: int | None,
) -> bytes:
    old_bytes = _encode_symbol(old_symbol)
    new_bytes = _encode_symbol(new_symbol)

    if len(old_bytes) != len(new_bytes):
        raise GXWFormatError(
            "this tool only supports equal-length symbol replacement; "
            f"{old_symbol!r} encodes to {len(old_bytes)} bytes, "
            f"{new_symbol!r} encodes to {len(new_bytes)} bytes"
        )

    node = _find_target_node(raw_pou, logical_name, old_symbol, node_offset)

    # Ordinary and FB node records both store the first string at record offset 16:
    #   u32 record_length
    #   u32 record_class
    #   u32 node_kind
    #   u32 char_count
    #   UTF-16LE string...
    symbol_start = node.offset + 16
    symbol_end = symbol_start + len(old_bytes)

    actual = raw_pou[symbol_start:symbol_end]
    if actual != old_bytes:
        raise GXWFormatError(
            f"parser/raw mismatch at node 0x{node.offset:X}: "
            f"expected {old_bytes.hex(' ')}, got {actual.hex(' ')}"
        )

    patched = bytearray(raw_pou)
    patched[symbol_start:symbol_end] = new_bytes

    # Re-parse to ensure all existing structural invariants remain valid.
    reparsed = parse_structured_pou(bytes(patched), logical_name=logical_name)
    changed = [n for n in reparsed.nodes if n.offset == node.offset]
    if len(changed) != 1 or changed[0].symbol != new_symbol:
        raise GXWFormatError("post-patch parser verification failed")

    return bytes(patched)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Patch one Structured Ladder/FBD node symbol in a GXW file, "
            "restricted to equal-length replacements such as X1 -> X2."
        )
    )
    parser.add_argument("gxw", type=Path)
    parser.add_argument("old_symbol")
    parser.add_argument("new_symbol")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument(
        "--program",
        default=None,
        help="Logical *.Program.pou name; required only when the project has multiple ones.",
    )
    parser.add_argument(
        "--node-offset",
        type=lambda s: int(s, 0),
        default=None,
        help="Exact Program.pou node offset, e.g. 0x103, if the symbol appears more than once.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if len(_encode_symbol(args.old_symbol)) != len(_encode_symbol(args.new_symbol)):
        parser.error("this compatibility command requires equal UTF-16 symbol lengths")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gxw_project import main as project_main
    report = args.report or args.output.with_suffix(".write.json")
    forwarded = ["symbol", str(args.gxw), args.old_symbol, args.new_symbol,
                 "-o", str(args.output), "--report", str(report)]
    if args.program:
        forwarded += ["--program", args.program]
    if args.node_offset is not None:
        forwarded += ["--node-offset", str(args.node_offset)]
    project_main(forwarded)


if __name__ == "__main__":
    main()
