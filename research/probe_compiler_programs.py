"""Probe native resource ordering, omitted members and compiler state chaining.

Requires the retained single-local-BOOL plan from the named-variable experiment.
Each variant starts from an empty directory; all input/output remains available.
"""
from __future__ import annotations
import argparse
import base64
import copy
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.compiler_tables import parse_compiler_tables
from gxw.compiler_assignment import parse_compiler_assignment
from gxw.compiler_storage import parse_compiler_index, parse_compiler_link_map
from gxw.token_pou import parse_token_fragment
from gxw.token_listing import decode_token_program
from gxw.container import CompoundFile
from replay_gxw_compile_trace import run_plan, b64


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    tables = parse_compiler_tables(base64.b64decode(plan["compile"][0]["symbols"]))
    record = bytearray(tables.tables[1].records[0].raw)
    component = tables.tables[2].records[0].raw
    if len(tables.tables[1].records) != 1 or len(tables.tables[2].records) != 1 or record[4:6] != b"\x011":
        raise ValueError("requires the minimized single-POU, single-variable seed")
    second = bytearray(record)
    second[5] = ord("2")
    struct.pack_into("<III", second, 10, len(component), len(component) * 2, 1)

    def unit(name, event, source=None):
        result = copy.deepcopy(next(u for u in plan["compile"] if u["event_id"] == (14 if source is not None else 13)))
        result["event_id"] = event
        for relocation in result["descriptors"]["relocations"]:
            if relocation["offset"] == 8:
                relocation["data"] = b64(name.encode() + b"\0")
            if relocation["offset"] == 4 and source is not None:
                relocation["data"] = b64(source)
        if source is not None:
            raw = bytearray(base64.b64decode(result["descriptors"]["raw"]))
            struct.pack_into("<I", raw, 0, len(source))
            result["descriptors"]["raw"] = b64(raw)
        return result

    source1 = b"N0 LD_NET\r\nK0 := %IX3;\r\n%QX3 := K0;\r\n\r\n"
    source2 = f"N1 LD_NET\r\nK{len(component)} := %IX4;\r\n%QX4 := K{len(component)};\r\n\r\n".encode()
    rows = []
    for names in (("1", "2"), ("2", "1"), ("1",), ("2",)):
        for chain in (True, False):
            name = "-".join(names) + ("-chained" if chain else "-reset")
            variant = copy.deepcopy(plan)
            raw_tables = [t.raw for t in tables.tables]
            raw_tables[1] = struct.pack("<I", len(record) + len(second)) + record + second
            raw_tables[2] = struct.pack("<I", len(component) * 2) + component * 2
            # Native resource row: preserve the observed prefix; vary only its
            # counted list of NUL-terminated task names. No task layout claim.
            resource = bytearray(tables.tables[8].records[0].payload[:18])
            resource += struct.pack("<I", len(names))
            for member in names:
                raw_name = member.encode() + b"\0"
                resource += struct.pack("<I", len(raw_name)) + raw_name
            resource = struct.pack("<I", len(resource)) + resource
            raw_tables[8] = struct.pack("<I", len(resource)) + resource
            symbols = b"".join(raw_tables)
            variant["compile"] = [variant["compile"][0], unit("1", 13), unit("1", 14, source1),
                                  unit("2", 15), unit("2", 16, source2), variant["compile"][-1]]
            for i, item in enumerate(variant["compile"]):
                item["symbols"] = b64(symbols)
                item["use_previous_symbols"] = chain and i > 0
            directory = args.output / name
            result = run_plan(variant, directory, provenance={"case": name, "resource_order": names,
                "input_source": "captured POU/component/resource templates with bounded changes"})
            row = {"case": name, "returncode": result["returncode"]}
            code = directory / "pcode-0-1.bin"
            if code.exists():
                raw = code.read_bytes()
                listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), text_encoding="cp936")
                row.update(instructions=[(r.mnemonic, r.args) for r in listing.instructions],
                           gaps=[r.reason for r in listing.gaps])
            last = directory / "symbols-after-20.bin"
            if last.exists():
                current = parse_compiler_tables(last.read_bytes())
                row["assignments"] = [(r.table_offset, parse_compiler_assignment(current.component_at(r.table_offset).user_info).fx_operand)
                                      for r in current.tables[2].records]
            snapshot = directory / "compiler-snapshot.stg"
            if snapshot.exists():
                cfb = CompoundFile(snapshot.read_bytes())
                index = parse_compiler_index(cfb.read_stream("Index"))
                entry = next((e for e in index.entries if e.name_bytes == b"MAIN*MAIN.map"), None)
                if entry:
                    mapping = cfb.read_stream(entry.stream_name)
                    (directory / "MAIN.map").write_bytes(mapping)
                    try:
                        row["map"] = [(r.name_bytes.decode(), r.fields) for r in parse_compiler_link_map(mapping).entries]
                    except ValueError as exc:
                        row["map_parse_gap"] = str(exc)
            rows.append(row)
            (args.output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
