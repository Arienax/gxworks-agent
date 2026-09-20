"""Append isolated CGTable array records and cross-check native descriptor reads.

These controls exercise the binary reader, not the source compiler or project
acceptance. Existing components and their table-relative references stay intact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from native_gxw_compiler import native_table_dump
from gxw.compiler_tables import parse_compiler_tables
from gxw.lossless import sha256


def controls():
    formats = {1: "b", 2: "B", 3: "i", 4: "H", 5: "i", 6: "I"}
    cases = []
    for kind, lower, extent in ((1, -7, 3), (2, 200, 200), (3, -70000, 100000),
                                (4, 40000, 60000), (5, -100000, 200000),
                                (6, 3000000000, 4000000000)):
        cases.append({"name": f"numeric-kind-{kind}", "element_type": 2,
                      "total": (kind, extent), "dimensions": [((kind, lower), (kind, extent))]})
    cases.extend([
        {"name": "mixed-two-dimensions", "element_type": 2, "total": (4, 12),
         "dimensions": [((1, -5), (2, 4)), ((3, 10), (4, 3))]},
        {"name": "inconsistent-total", "element_type": 2, "total": (1, 99),
         "dimensions": [((1, 0), (1, 4))]},
        {"name": "negative-extent", "element_type": 2, "total": (1, -4),
         "dimensions": [((1, 0), (1, -4))]},
        {"name": "no-dimensions", "element_type": 2, "total": (1, 1), "dimensions": []},
    ])
    for element in (0x0F, 0x34, 0x35):
        cases.append({"name": f"element-parameter-{element}", "element_type": element,
                      "parameter": 0x12345678, "total": (1, 4),
                      "dimensions": [((1, 0), (1, 4))]})
    for case in cases:
        payload = bytes([case["element_type"]]) + b"\xde\xad\xbe"
        if "parameter" in case:
            payload += struct.pack("<I", case["parameter"])
        kind, value = case["total"]
        payload += bytes([len(case["dimensions"]), kind]) + struct.pack("<" + formats[kind], value)
        for (lo_kind, lower), (count_kind, extent) in case["dimensions"]:
            payload += bytes([lo_kind | count_kind << 4])
            payload += struct.pack("<" + formats[lo_kind], lower)
            payload += struct.pack("<" + formats[count_kind], extent)
        payload += struct.pack("<I", 1)
        yield case, struct.pack("<I", len(payload)) + payload


def probe(seed, directory):
    raw = Path(seed).read_bytes()
    tables = parse_compiler_tables(raw)
    payload = tables.tables[13].raw[4:]
    expected = []
    for case, record in controls():
        expected.append(dict(case, offset=len(payload), record_hex=record.hex()))
        payload += record
    mutated = b"".join(t.raw for t in tables.tables[:13]) + struct.pack("<I", len(payload)) + payload
    result = native_table_dump(mutated, directory, read_arrays=True)
    (Path(directory) / "controls.json").write_text(json.dumps({
        "seed_sha256": sha256(raw), "seed_array_count": len(tables.tables[13].records),
        "scope": "synthetic appended records; native reader/Save only; no project compile claim",
        "controls": expected}, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.seed, args.output)))
