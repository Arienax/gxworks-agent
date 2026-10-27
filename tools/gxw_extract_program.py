# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Run from repository root so `src` is importable without installation.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.project_resolver import GXWProjectResolver


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve and extract a logical *.Program.pou from a GXW file."
    )
    parser.add_argument("gxw", type=Path)
    parser.add_argument(
        "--program",
        default=None,
        help="Logical name, e.g. 1.Program.pou. Required only if multiple Program.pou objects exist.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .pou path"
    )
    args = parser.parse_args()

    resolver = GXWProjectResolver.from_file(args.gxw)
    logical_name = resolver.choose_program_pou(args.program)
    data = resolver.read_logical_file(logical_name)

    out = args.output or Path(logical_name.replace("/", "_"))
    out.write_bytes(data)

    print(f"GXW:      {args.gxw}")
    print(f"Logical:  {logical_name}")
    print(f"Size:     {len(data)} bytes")
    print(f"Output:   {out}")


if __name__ == "__main__":
    main()
