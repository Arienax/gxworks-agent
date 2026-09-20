"""Controlled native compiler-source mutations of the captured public t2 case.

This changes the compiler's intermediate text, not GXW source serialization.
Every run starts in a new empty directory and keeps failures and native outputs.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import os
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.token_pou import parse_token_fragment
from gxw.token_listing import decode_token_program


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    plan = json.loads((baseline / "plan.json").read_text())
    exe = baseline / "CompilerReplayOracle.exe"
    expected = (baseline / "request.json").read_text()
    if os.name != "nt" or sha(Path(plan["dll"]).read_bytes()) != json.loads(expected)["dll_sha256"]:
        raise ValueError("requires the captured native compiler build")
    units = [u for u in plan["compile"] if u["event_id"] == 14]
    if len(units) != 1 or units[0]["count"] != 1:
        raise ValueError("not the inspected t2 trace")
    source = next(r for r in units[0]["descriptors"]["relocations"] if r["offset"] == 4)
    original = base64.b64decode(source["data"])
    if original != (b"N0 LD_NET\r\nK2005(%IX1, C372) := C353;\r\n%QX1 := K2005.K602;\r\n"
                    b"K2284 := K2005.K662;\r\nK2144(%IX2, C399) := C353;\r\n"
                    b"%QX2 := K2144.K1307;\r\nK2352 := K2144.K1367;\r\n\r\n"):
        raise ValueError("source differs from the inspected public seed")
    cases = {
        "identity": original,
        "input-x3": original.replace(b"%IX1", b"%IX3"),
        "input-x10": original.replace(b"%IX1", b"%IX10"),
        "output-y3": original.replace(b"%QX1", b"%QX3"),
        "duration-second": original.replace(b"C372", b"C399"),
        "output-from-second-instance": original.replace(b"%QX1 := K2005.K602", b"%QX1 := K2144.K1307"),
        "remove-second-call": original[:original.index(b"K2144(")] + b"\r\n",
        "append-direct-assignment": original.rstrip(b"\r\n") + b"\r\n%QX3 := %IX3;\r\n\r\n",
        "invalid-reference": original.replace(b"K2005(%IX1", b"K999999(%IX1"),
        "invalid-grammar": original.replace(b"N0 LD_NET", b"N0 UNKNOWN_NET"),
    }
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    provenance = {"baseline_plan_sha256": sha((baseline / "plan.json").read_bytes()),
                  "oracle_sha256": sha(exe.read_bytes()), "source_sha256": sha(original),
                  "mutation_layer": "native compiler intermediate text; GXW source unchanged"}
    (output / "request.json").write_text(json.dumps(provenance, indent=2) + "\n")
    rows = []
    baseline_code = (baseline / "pcode-0-1.bin").read_bytes()
    for name, modified in cases.items():
        directory = output / name
        directory.mkdir()
        work = directory / "work"
        work.mkdir()
        variant = copy.deepcopy(plan)
        variant["working_directory"] = str(work)
        unit = next(u for u in variant["compile"] if u["event_id"] == 14)
        descriptors = unit["descriptors"]
        raw = bytearray(base64.b64decode(descriptors["raw"]))
        struct.pack_into("<i", raw, 0, len(modified))
        descriptors["raw"] = base64.b64encode(raw).decode()
        next(r for r in descriptors["relocations"] if r["offset"] == 4)["data"] = base64.b64encode(modified).decode()
        plan_path = directory / "plan.json"
        plan_path.write_text(json.dumps(variant, indent=2) + "\n")
        (directory / "source.txt").write_bytes(modified)
        try:
            process = subprocess.run([str(exe), str(plan_path)], cwd=directory, capture_output=True,
                                     timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            code, stdout, stderr = process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired as exc:
            code, stdout, stderr = "timeout", exc.stdout or b"", exc.stderr or b""
        (directory / "stdout.txt").write_bytes(stdout)
        (directory / "stderr.txt").write_bytes(stderr)
        row = {"case": name, "returncode": code, "source_sha256": sha(modified)}
        native_log = directory / "native-events.jsonl"
        if native_log.exists():
            events = [json.loads(line) for line in native_log.read_text().splitlines()]
            row["failed_calls"] = [e for e in events if e["code"] != 0]
        generated = directory / "pcode-0-1.bin"
        if generated.exists():
            raw = generated.read_bytes()
            row.update(pcode_bytes=len(raw), pcode_sha256=sha(raw), identical=raw == baseline_code,
                       changed_offsets=[i for i in range(min(len(raw), len(baseline_code))) if raw[i] != baseline_code[i]])
            try:
                listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), text_encoding="cp936")
                row["instructions"] = [{"step": r.step, "op": r.mnemonic, "args": r.args} for r in listing.instructions]
                row["gaps"] = [r.reason for r in listing.gaps]
                row["token_replay"] = listing.reconstruct() == raw
            except ValueError as exc:
                row["decode_error"] = str(exc)
        rows.append(row)
        (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k not in ("instructions", "changed_offsets")}), flush=True)


if __name__ == "__main__":
    main()
