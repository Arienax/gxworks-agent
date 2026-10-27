# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from __future__ import annotations

import argparse
from pathlib import Path

import olefile


def dump_ole(path: Path, *, title: str) -> None:
    print(f"\n== {title}: {path} ==")
    if not olefile.isOleFile(str(path)):
        raise SystemExit(f"{path} is not a valid OLE/CFB file")

    with olefile.OleFileIO(str(path)) as ole:
        for item in ole.listdir(streams=True, storages=True):
            kind = "STREAM" if ole.exists(item) and ole.get_type(item) == olefile.STGTY_STREAM else "STORAGE"
            try:
                size = ole.get_size(item) if kind == "STREAM" else 0
            except Exception:
                size = 0
            print(f"{kind:7} {size:8}  {'/'.join(item)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List the outer GXW CFB tree and the nested _hdb CFB tree."
    )
    parser.add_argument("gxw", type=Path)
    parser.add_argument("--extract-hdb", type=Path, default=None)
    args = parser.parse_args()

    dump_ole(args.gxw, title="outer GXW")

    with olefile.OleFileIO(str(args.gxw)) as outer:
        if not outer.exists("_hdb"):
            raise SystemExit("outer GXW has no _hdb stream")
        hdb = outer.openstream("_hdb").read()

    hdb_path = args.extract_hdb or args.gxw.with_suffix(".hdb.bin")
    hdb_path.write_bytes(hdb)
    print(f"\nExtracted _hdb -> {hdb_path}")
    dump_ole(hdb_path, title="nested _hdb")


if __name__ == "__main__":
    main()
