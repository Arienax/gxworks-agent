"""Offline native CGTable restore/dump/replay; no project or device APIs."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.lossless import sha256
from gxw.compiler_tables import parse_compiler_tables

DLL = Path("D:/GXWORKS2/Easysocket/Compiler/ECCompiler.dll")
DLL_SHA256 = "4f7b2398874c7a921f49f13f25a9a603562032b039de8a5bb74114df27fadf16"


def native_assignment_format(records, directory, *, dll=DLL):
    """Exercise only inspected CPU-independent branches of the native formatter.

    Zero UserInfo is intentionally included: native `%IX0` is a fallback, not
    evidence of an X0 allocation. No synthesized CPU descriptor is supplied.
    """
    records = [bytes(r) for r in records]
    for raw in records:
        if len(raw) != 26 or (any(raw) and (raw[0], raw[1]) not in
                             ((0x21, 0), (0x24, 5), (5, 11), (5, 12), (5, 13))):
            raise ValueError("assignment variant outside inspected native experiment")
    if os.name != "nt" or sha256(dll.read_bytes()) != DLL_SHA256:
        raise ValueError("requires Windows and the inspected ECCompiler.dll 15.22 hash")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "CompilerAssignmentOracle.cs"
    source.write_bytes((ROOT / "research/native/CompilerAssignmentOracle.cs").read_bytes())
    input_path, output_path = directory / "inputs.txt", directory / "outputs.jsonl"
    input_path.write_text("".join(base64.b64encode(r).decode() + "\n" for r in records), encoding="ascii")
    request = {"dll_sha256": DLL_SHA256, "source_sha256": sha256(source.read_bytes()),
               "input_sha256": sha256(input_path.read_bytes()), "record_count": len(records),
               "scope": "offline native UserInfo formatting; no project or device API"}
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    executable = directory / "CompilerAssignmentOracle.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/out:" + str(executable), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "build-output.bin").write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError("native assignment adapter build failed; evidence retained")
    try:
        run = subprocess.run([str(executable.resolve()), str(dll.resolve()), str(input_path.resolve()), str(output_path.resolve())],
                             capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, code = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "stdout.txt").write_bytes(stdout)
    (directory / "stderr.txt").write_bytes(stderr)
    (directory / "process.json").write_text(json.dumps(dict(request, returncode=code,
        adapter_sha256=sha256(executable.read_bytes())), indent=2) + "\n")
    if code:
        raise RuntimeError("native assignment process failed; partial output retained")
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    if [base64.b64decode(r["input_base64"], validate=True) for r in rows] != records:
        raise RuntimeError("native assignment output is incomplete or out of order")
    return rows


def compare_component_reads(raw, rows):
    """Compare initialized scalar fields and the separate raw UserInfo block.

    The native union includes pointers and unused stack bytes for some types.
    Those bytes remain recorded but must not become a portable equality claim.
    """
    tables = parse_compiler_tables(raw)
    offsets = [r.table_offset for r in tables.tables[2].records]
    complete = ([r["requested_offset"] for r in rows] == offsets
                and all(r["return_code"] == 1 for r in rows))
    mismatches = []
    for row in rows:
        component = tables.component_at(row["requested_offset"])
        record = base64.b64decode(row["record_base64"], validate=True)
        user = base64.b64decode(row["user_info_base64"], validate=True)
        if len(record) != 0x129:
            mismatches.append({"offset": row["requested_offset"], "field": "native struct size"})
            continue
        checks = {"name": record[:261].split(b"\0", 1)[0] == component.name_bytes,
                  "scope": struct.unpack_from("<I", record, 0x105)[0] == component.scope_code,
                  "type": struct.unpack_from("<I", record, 0x10D)[0] == tables.component_type(component),
                  "address_reference": struct.unpack_from("<i", record, 0x109)[0] == tables.component_address_offset(component),
                  "user_info": user == component.user_info,
                  "next_offset": row["next_offset"] == component.record.table_offset + len(component.record.raw)}
        mismatches.extend({"offset": row["requested_offset"], "field": key} for key, equal in checks.items() if not equal)
    return {"component_reads_complete": complete, "component_count": len(rows),
            "component_field_mismatches": mismatches}


def compare_table_dump(raw, dump):
    """Compare ordered native POU/component names, byte indexes and FB targets.

    Ignore the native textual rendering of binary UserInfo; retain its original
    bytes in the input and replay. This comparison does not cover every field.
    """
    tables = parse_compiler_tables(raw)
    pous = [tables.pou_at(r.table_offset) for r in tables.tables[1].records]
    components = [c for p in pous for c in tables.components(p)]
    native_pous = [(int(m[1]), m[2]) for m in re.finditer(rb"^Index\s+(\d+): ([^\r\n]+) \(POU_[^)]+\)", dump, re.M)]
    native_components = [(int(m[1]), m[2]) for m in re.finditer(rb"^ +Index\s+(\d+): ([^\r\n]+) \(TYPE_[^)]+\)", dump, re.M)]
    native_references = [int(m[1]) for m in re.finditer(rb"^ +PouIdx = (-?\d+)", dump, re.M)]
    references = [c.instance_pou_offset for c in components if c.instance_pou_offset is not None]
    for reference in references:
        tables.pou_at(reference)
    return {"pous_equal": native_pous == [(p.record.table_offset, p.name_bytes) for p in pous],
            "components_equal": native_components == [(c.record.table_offset, c.name_bytes) for c in components],
            "instance_refs_equal": native_references == references,
            "pous": len(pous), "components": len(components), "instances": len(references)}


def compare_address_reads(raw, rows):
    tables = parse_compiler_tables(raw)
    expected = [r.table_offset for r in tables.tables[4].records]
    mismatches = []
    for row in rows:
        address = tables.address_at(row["requested_offset"])
        checks = {"location": address.location_code == row["location_code"],
                  "size": address.size_code == row["size_code"],
                  "name": address.name_bytes == base64.b64decode(row["name_base64"], validate=True),
                  "reference_count": address.reference_count == row["reference_count"],
                  "next_offset": address.record.table_offset + len(address.record.raw) == row["next_offset"]}
        mismatches.extend({"offset": row["requested_offset"], "field": key} for key, equal in checks.items() if not equal)
    return {"address_reads_complete": expected == [r["requested_offset"] for r in rows]
            and all(r["return_code"] == 1 for r in rows),
            "address_count": len(rows), "address_field_mismatches": mismatches}


def compare_array_reads(raw, rows):
    """Compare values copied from native-owned objects, excluding pointers."""
    tables = parse_compiler_tables(raw)
    expected = [r.table_offset for r in tables.tables[13].records]
    mismatches = []
    def signed_pair(data, offset):
        sign, magnitude = struct.unpack_from("<II", data, offset)
        if sign not in (0, 1):
            raise ValueError("native array number has unsupported sign")
        return -magnitude if sign else magnitude
    for row in rows:
        array = tables.array_at(row["requested_offset"])
        header = base64.b64decode(row["header_base64"], validate=True)
        union = base64.b64decode(row["union_base64"], validate=True)
        nodes = [base64.b64decode(n, validate=True) for n in row["nodes_base64"]]
        if len(header) != 25 or len(union) != 8 or any(len(n) != 20 for n in nodes):
            raise ValueError("native array ABI snapshot has unexpected size")
        checks = {"element_type": struct.unpack_from("<I", header)[0] == array.element_type,
                  "element_parameter": array.element_parameter is None or
                  struct.unpack_from("<I", union, 4)[0] == array.element_parameter,
                  "total_count": signed_pair(header, 4) == array.total_count,
                  "dimension_count": header[24] == len(array.dimensions) == len(nodes),
                  "dimensions": [(signed_pair(n, 0), signed_pair(n, 8)) for n in nodes] ==
                  [(d.lower, d.extent) for d in array.dimensions],
                  "next_offset": row["next_offset"] == array.record.table_offset + len(array.record.raw)}
        mismatches.extend({"offset": row["requested_offset"], "field": key} for key, equal in checks.items() if not equal)
    return {"array_reads_complete": expected == [r["requested_offset"] for r in rows]
            and all(r["return_code"] == 1 for r in rows),
            "array_count": len(rows), "array_field_mismatches": mismatches}


def native_table_dump(raw, directory, *, dll=DLL, read_components=False, read_addresses=False, read_arrays=False):
    if os.name != "nt" or sha256(dll.read_bytes()) != DLL_SHA256:
        raise ValueError("requires Windows and the inspected ECCompiler.dll 15.22 hash")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "CompilerTableOracle.cs"
    source.write_bytes((ROOT / "research/native/CompilerTableOracle.cs").read_bytes())
    input_path = directory / "CGTable.dat"
    input_path.write_bytes(raw)
    request = {"input_sha256": sha256(raw), "dll_sha256": DLL_SHA256,
               "adapter_source_sha256": sha256(source.read_bytes()),
               "scope": "offline table restore/dump/replay; no source freshness, execution or project acceptance claim"}
    read_addresses = read_addresses or read_arrays
    read_components = read_components or read_addresses
    if read_components:
        request["component_offsets"] = [r.table_offset for r in parse_compiler_tables(raw).tables[2].records]
        (directory / "component-offsets.txt").write_text("".join(str(p) + "\n" for p in request["component_offsets"]), encoding="ascii")
    if read_addresses:
        request["address_offsets"] = [r.table_offset for r in parse_compiler_tables(raw).tables[4].records]
        (directory / "address-offsets.txt").write_text("".join(str(p) + "\n" for p in request["address_offsets"]), encoding="ascii")
    if read_arrays:
        request["array_offsets"] = [r.table_offset for r in parse_compiler_tables(raw).tables[13].records]
        (directory / "array-offsets.txt").write_text("".join(str(p) + "\n" for p in request["array_offsets"]), encoding="ascii")
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    executable = directory / "CompilerTableOracle.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/out:" + str(executable), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "build-output.bin").write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError("native compiler-table adapter build failed; evidence retained")
    try:
        command = [str(executable.resolve()), str(dll.resolve()), str(input_path.resolve()), str(directory.resolve())]
        if read_components:
            command.append(str((directory / "component-offsets.txt").resolve()))
        if read_addresses:
            command.append(str((directory / "address-offsets.txt").resolve()))
        if read_arrays:
            command.append(str((directory / "array-offsets.txt").resolve()))
        run = subprocess.run(command,
                             capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, code = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "stdout.txt").write_bytes(stdout)
    (directory / "stderr.txt").write_bytes(stderr)
    result = dict(request, returncode=code, adapter_sha256=sha256(executable.read_bytes()))
    replay = directory / "native-replay.bin"
    result["replay_equal"] = replay.exists() and replay.read_bytes() == raw
    (directory / "process.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if code:
        raise RuntimeError("native compiler-table process failed; inputs and partial output retained")
    result.update(compare_table_dump(raw, (directory / "native-table-dump.txt").read_bytes()))
    if read_components:
        rows = [json.loads(line) for line in (directory / "native-components.jsonl").read_text().splitlines()]
        result.update(compare_component_reads(raw, rows))
    if read_addresses:
        rows = [json.loads(line) for line in (directory / "native-addresses.jsonl").read_text().splitlines()]
        result.update(compare_address_reads(raw, rows))
    if read_arrays:
        rows = [json.loads(line) for line in (directory / "native-arrays.jsonl").read_text().splitlines()]
        result.update(compare_array_reads(raw, rows))
    (directory / "comparison.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="extracted CGTable.dat")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--dll", type=Path, default=DLL)
    parser.add_argument("--components", action="store_true", help="also read native CMP_LINE and UserInfo for each component")
    parser.add_argument("--addresses", action="store_true", help="also read native declaration addresses and component references")
    parser.add_argument("--arrays", action="store_true", help="also snapshot native array descriptor objects and dimension nodes")
    args = parser.parse_args()
    print(json.dumps(native_table_dump(args.input.read_bytes(), args.output, dll=args.dll,
                                     read_components=args.components, read_addresses=args.addresses,
                                     read_arrays=args.arrays)))
