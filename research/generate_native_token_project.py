"""Build an offline experimental ordinary Ladder GXW from an empty native seed.

The vendor converter supplies instruction bytes; Python preserves the seed's
other streams and updates only source lengths/history. The old .res is retained
until actual GX Works2 conversion. Never connects to an editor or a device.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import struct

from native_gxw_tokens import native_batch, DEFAULT_DLL
from native_gxw_roundtrip import require_machinecode_roundtrip
from gxw.container import CompoundFile
from gxw.lossless import sha256, _replace_token_program
from gxw.project_metadata import logical_mapping
from gxw.project_writer import write_new_file
from gxw.token_listing import decode_token_listing


def generate(seed, expected_sha256, text, directory, *, program="MAIN.Program.pou", dll=DEFAULT_DLL):
    if sha256(seed) != expected_sha256:
        raise ValueError("seed identity changed")
    outer = CompoundFile(seed)
    stream = logical_mapping(outer.read_stream("projectdatalist.xml"))[program]
    old = CompoundFile(outer.read_stream("_hdb")).read_stream(stream)
    listing = decode_token_listing(old)
    if len(listing.records) != 1 or listing.instruction_ir() != [{"op": "END", "args": []}]:
        raise ValueError("this generator requires an empty native token program seed")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[-1] != "END" or "END" in lines[:-1] or any("\0" in line for line in lines):
        raise ValueError("provide instructions with exactly one final END")
    request = {"mode": "encode", "input_base64": base64.b64encode(("\0".join(lines) + "\0\0").encode("cp936")).decode(),
               "instructions": lines, "seed_sha256": expected_sha256, "program": program}
    reply = native_batch([request], directory, dll=dll)[0]
    if reply["return_code"] != "0x00000000":
        raise ValueError("native encoder rejected the input: " + reply["return_code"])
    body = base64.b64decode(reply["output_base64"])
    raw = bytearray(old[:79] + body + old[-24:])
    struct.pack_into("<II", raw, 55, len(raw) - 83, len(raw) - 83)
    updated = bytes(raw)
    # A native conversion success is still not a source grammar/compile oracle.
    decoded = decode_token_listing(updated, text_encoding="cp936")
    if decoded.gaps or decoded.reconstruct() != updated:
        raise ValueError("generated token source is outside the understood grammar; evidence retained")
    require_machinecode_roundtrip([body], directory / "machinecode-roundtrip", dll=dll)
    result = _replace_token_program(seed, program, old, updated)
    (directory / "patch.json").write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--instructions", type=Path, required=True)
    parser.add_argument("--program", default="MAIN.Program.pou")
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    result = generate(args.seed.read_bytes(), args.sha256, args.instructions.read_text(encoding="utf-8"),
                      args.evidence_dir, program=args.program, dll=args.dll)
    write_new_file(args.output, result.data)
    print(json.dumps({"sha256": sha256(result.data), "allocations": result.report["allocations"], "native_project_conversion": "not_run"}))
