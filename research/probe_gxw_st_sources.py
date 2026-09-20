"""Probe raw-preserved ST source replacement against the offline native compiler.

The framing is a hypothesis from the native FX3U ST seed, not a general ST
reader. Every variant retains its input, exact mutation, native snapshot and
compiler diagnostics, including failures. No device session is opened.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container_writer import replace_project_stream, validate_cfb_streams
from gxw.lossless import inspect_project
from gxw.project_metadata import logical_mapping, synchronize_history
from replay_gxw_workspace import run


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_observed_st(raw, source):
    if len(raw) < 99 or raw[54] != 0xC1:
        raise ValueError("not the observed uncompressed ST seed envelope")
    count = struct.unpack_from("<I", raw, 67)[0]
    if (struct.unpack_from("<II", raw, 55) != (len(raw) - 83,) * 2
            or struct.unpack_from("<I", raw, 63)[0] != 1
            or count < 1 or 71 + count * 2 + 28 != len(raw)
            or raw[71 + count * 2 - 2:71 + count * 2] != b"\0\0"
            or raw[-28:] != b"\0" * 28):
        raise ValueError("ST source framing differs from the native seed")
    old = raw[71:71 + count * 2 - 2].decode("utf-16le")
    if "\0" in old or "\0" in source:
        raise ValueError("embedded NUL is outside this source experiment")
    text = source.encode("utf-16le") + b"\0\0"
    updated = bytearray(raw[:71] + text + raw[-28:])
    struct.pack_into("<I", updated, 67, len(text) // 2)
    struct.pack_into("<II", updated, 55, len(updated) - 83, len(updated) - 83)
    return bytes(updated), old


def replace_source(project, logical_name, source):
    outer = validate_cfb_streams(project)
    mapping = logical_mapping(outer["projectdatalist.xml"])
    stream = mapping[logical_name]
    nested = validate_cfb_streams(outer["_hdb"])
    old = nested[stream]
    new, old_source = replace_observed_st(old, source)
    history, changes, preserved = synchronize_history(outer["history.xml"],
        {logical_name: (stream, old, new)})
    hdb, inner_mode = replace_project_stream(outer["_hdb"], stream, new)
    updated, outer_mode = replace_project_stream(project, "_hdb", hdb)
    updated, history_mode = replace_project_stream(updated, "history.xml", history)
    if validate_cfb_streams(hdb) != dict(nested, **{stream: new}):
        raise ValueError("unrelated nested payload changed")
    if validate_cfb_streams(updated) != dict(outer, _hdb=hdb, **{"history.xml": history}):
        raise ValueError("unrelated outer payload changed")
    return updated, dict(input_sha256=digest(project), output_sha256=digest(updated),
        logical_name=logical_name, old_stream_bytes=len(old), new_stream_bytes=len(new),
        old_source=old_source, source=source, metadata_changes=changes,
        preserved_metadata=preserved, other_payloads="byte-identical",
        allocations=dict(program=inner_mode, outer=outer_mode, history=history_mode))


def child(blob, offset):
    return next(r["data"] for r in blob["relocations"] if r["offset"] == offset)


def snapshot_body(snapshot, name):
    pool = child(snapshot["build"], 44)
    raw = base64.b64decode(pool["raw"])
    for start in range(0, len(raw), 56):
        if base64.b64decode(child(pool, start + 8)["raw"]) == name.encode("ascii") + b"\0":
            return base64.b64decode(child(pool, start + 24)["raw"])
    raise ValueError("POU absent from native frontend snapshot")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    project = args.seed.read_bytes()
    image = inspect_project(project)
    candidates = [s for s in image.streams if s.logical_name and s.logical_name.endswith(".Program.pou")]
    if len(candidates) != 1:
        raise ValueError("experiment requires a single known ST POU")
    logical = candidates[0].logical_name
    _, original = replace_observed_st(candidates[0].raw, "")
    cases = {
        "equal-device": original.replace("X0", "X3"),
        "grow-control": "(* 原生 ST 对照：分支、循环及索引 *)\r\nY0 := X3;\r\nIF X1 THEN\r\n    D0 := D1 + 300;\r\nELSE\r\n    D0 := 70;\r\nEND_IF;\r\nFOR D10 := 0 TO 3 BY 1 DO\r\n    D20 := D20 + D10;\r\nEND_FOR;\r\n",
        "empty": "",
        "short": "Y0:=X0;",
        "chinese-comment": "(* 中文注释保留 *)\r\nY0:=X0;\r\n",
        "regular-storage": "(* " + "native ST source boundary; " * 200 + "*)\r\nY0:=X0;\r\n",
        "case-control": "CASE D0 OF\r\n0: Y0 := TRUE;\r\n1: Y0 := FALSE;\r\nELSE Y0 := X0;\r\nEND_CASE;\r\n",
        "function-call": "D0 := ABS(D1);\r\n",
        "invalid-syntax": "IF X0 THEN Y0 := TRUE;\r\n",
        "invalid-device": "Y0 := X8;\r\n",
    }
    with (args.output / "cases.jsonl").open("w", encoding="utf-8") as log:
        for name, source in cases.items():
            if args.case and name not in args.case:
                continue
            directory = args.output / name
            directory.mkdir()
            patched, mutation = replace_source(project, logical, source)
            (directory / "source.txt").write_bytes(source.encode("utf-8"))
            (directory / "patched.gxw").write_bytes(patched)
            (directory / "mutation.json").write_text(json.dumps(mutation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            outcome = run(directory / "patched.gxw", directory / "native", compile=True, snapshot_frontend=True)
            snapshot = directory / "native/frontend-snapshot.json"
            row = dict(case=name, source_bytes_utf16=len(source.encode("utf-16le")), mutation=mutation, native=outcome)
            if snapshot.exists():
                body = snapshot_body(json.loads(snapshot.read_text(encoding="utf-8")), logical.removesuffix(".Program.pou"))
                (directory / "frontend-body.bin").write_bytes(body)
                encoded = source.encode("cp936") + b"\0"
                row["native_source_bytes_match"] = body[76:76 + len(encoded)] == encoded
                row["native_body_bytes"] = len(body)
                row["native_text_length_field"] = struct.unpack_from("<I", body, 72)[0]
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps({k: v for k, v in row.items() if k not in ("mutation", "native")}, ensure_ascii=True),
                outcome["returncode"], "rejected", outcome["compiler_rejected"], flush=True)


if __name__ == "__main__":
    main()
