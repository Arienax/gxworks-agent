"""Minimize the public t2 compiler context and probe intermediate expressions.

Resource/POU records are copied from the observed native inputs. No GXW file
or device is written. Native output, missing output and rejection are distinct.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.compiler_tables import parse_compiler_tables
from gxw.token_pou import parse_token_fragment
from gxw.token_listing import decode_token_program
from replay_gxw_compile_trace import run_plan, b64
from native_gxw_tokens import native_batch, native_il_records, listing_records, canonical_record


def minimal_plan(observed):
    plan = copy.deepcopy(observed)
    original = next(u for u in plan["compile"] if u["event_id"] == 14)
    tables = parse_compiler_tables(base64.b64decode(original["symbols"]))
    pou = next(tables.pou_at(r.table_offset) for r in tables.tables[1].records
               if tables.pou_at(r.table_offset).name_bytes == b"1")
    record = bytearray(pou.record.raw)
    # The new root has no declared components. All other POU fields stay raw.
    struct.pack_into("<III", record, 4 + 1 + len(pou.name_bytes) + 4, 0, 0, 0)
    raw_tables = [b"\0" * 4 for _ in range(14)]
    raw_tables[1] = struct.pack("<I", len(record)) + record
    raw_tables[8] = tables.tables[8].raw
    symbols = b"".join(raw_tables)
    if len(symbols) != 150:
        raise ValueError("not the inspected t2 resource/POU context")
    plan["compile"] = [u for u in plan["compile"] if u["event_id"] in (12, 13, 14, 20)]
    for unit in plan["compile"]:
        unit["symbols"] = b64(symbols)
    return plan, tables


def set_source(plan, source):
    unit = next(u for u in plan["compile"] if u["event_id"] == 14)
    descriptors = unit["descriptors"]
    raw = bytearray(base64.b64decode(descriptors["raw"]))
    struct.pack_into("<i", raw, 0, len(source))
    descriptors["raw"] = b64(raw)
    next(r for r in descriptors["relocations"] if r["offset"] == 4)["data"] = b64(source)


def constant_record(value):
    # Native t2 CST offset 333: INT16_VAL has a four-byte signed payload,
    # despite the type's name. Probe its native behavior; no broad writer claim.
    text = str(value).encode("ascii")
    payload = b"\x03" + struct.pack("<H", len(text)) + text + struct.pack("<iI", value, 1)
    return struct.pack("<I", len(payload)) + payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observed_plan", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    original = args.observed_plan.read_bytes()
    plan, tables = minimal_plan(json.loads(original))
    (output / "minimal-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    cases = []
    for name, statement in [
        ("assignment", "%QX3 := %IX3;"),
        ("boolean", "%QX3 := (%IX3 AND NOT %IX4) OR %IX5;"),
        ("word-copy", "%MW0.0 := %MW0.1;"),
        ("internal-bit", "%MX0.3 := %IX3;"),
        ("not-bit", "%QX3 := NOT %IX3;"),
        ("xor-bit", "%QX3 := %IX3 XOR %IX4;"),
        ("multi-assignment", "%QX3 := %IX3;\r\n%QX4 := %IX4;"),
        ("invalid-literal", "%MW0.0 := 1;"),
        ("invalid-if", "IF %IX3 THEN %QX3 := %IX4; END_IF;"),
    ]:
        cases.append((name, "N0 LD_NET\r\n" + statement + "\r\n\r\n", None))
    for name, operation in [("add", "+"), ("subtract", "-"), ("multiply", "*"), ("divide", "/"),
                            ("modulus", "MOD"), ("and", "AND"), ("or", "OR"), ("xor", "XOR")]:
        cases.append(("word-" + name, f"N0 LD_NET\r\n%MW0.0 := %MW0.1 {operation} %MW0.2;\r\n\r\n", None))
    for name, operation in [("equal", "="), ("not-equal", "<>"), ("less", "<"), ("greater", ">"),
                            ("less-equal", "<="), ("greater-equal", ">=")]:
        cases.append(("compare-" + name, f"N0 LD_NET\r\n%QX3 := %MW0.1 {operation} %MW0.2;\r\n\r\n", None))
    for value in (-32768, -1, 0, 10, 32767):
        cases.append(("constant-" + str(value), "N0 LD_NET\r\n%MW0.0 := C0;\r\n\r\n", constant_record(value)))
    for name, offset in [("false", 244), ("true", 353)]:
        cases.append(("constant-" + name, "N0 LD_NET\r\n%QX3 := C0;\r\n\r\n", tables.tables[5].record_at(offset).raw))
    cases.extend([
        ("fbd-net", "N0 FBD_NET\r\n%QX3 := %IX3;\r\n\r\n", None),
        ("il-net", "N0 IL_NET\r\nLD %IX3 D0;\r\nST %QX3 D25;\r\n\r\n", None),
        ("il-no-debug", "N0 IL_NET\r\nLD %IX3;\r\nST %QX3;\r\n\r\n", None),
        ("invalid-net", "N0 UNKNOWN_NET\r\n%QX3 := %IX3;\r\n\r\n", None),
    ])
    rows, requests, listings = [], [], []
    for name, source, constant in cases:
        variant = copy.deepcopy(plan)
        if constant is not None:
            current = parse_compiler_tables(base64.b64decode(variant["compile"][0]["symbols"]))
            raw_tables = [t.raw for t in current.tables]
            raw_tables[5] = struct.pack("<I", len(constant)) + constant
            for unit in variant["compile"]:
                unit["symbols"] = b64(b"".join(raw_tables))
        set_source(variant, source.encode("ascii"))
        directory = output / name
        result = run_plan(variant, directory, provenance={"case": name,
            "observed_plan_sha256": hashlib.sha256(original).hexdigest(),
            "mutation_layer": "compiler intermediate input; not GXW serialization"})
        (directory / "source.txt").write_bytes(source.encode("ascii"))
        row = {"case": name, "returncode": result["returncode"]}
        log = directory / "native-events.jsonl"
        if log.exists():
            events = [json.loads(line) for line in log.read_text().splitlines()]
            row["failed_calls"] = [e for e in events if e["code"] != 0]
        program = directory / "pcode-0-1.bin"
        row["status"] = "native-rejected" if result["returncode"] else "no-program-output"
        if program.exists():
            raw = program.read_bytes()
            listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), text_encoding="cp936")
            row.update(status="emitted", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       records=listing_records(listing), gaps=len(listing.gaps), replay=listing.reconstruct() == raw)
            listings.append((row, listing))
            requests.append({"mode": "decode", "input_base64": b64(raw + b"\0"), "case": name})
        rows.append(row)
        (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps(row), flush=True)
    native_rows = native_batch(requests, output / "independent-token-decode")
    for (row, listing), native in zip(listings, native_rows):
        row["native_decode_code"] = native["return_code"]
        if native["return_code"] == "0x00000000":
            records = native_il_records(base64.b64decode(native["output_base64"]))
            row["independent_decode_agrees"] = ([canonical_record(r) for r in records]
                == [canonical_record(r) for r in listing_records(listing)])
        else:
            row["independent_decode_agrees"] = False
    (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps({"cases": len(rows), "emitted": len(listings),
                      "independent_decode_agrees": sum(r.get("independent_decode_agrees", False) for r in rows)}))


if __name__ == "__main__":
    main()
