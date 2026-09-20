"""Rebase observed IEC binary source objects and replay the offline frontend.

No GXW writer or source grammar assumptions: records and payloads stay opaque.
Captured pointers are removed; unsupported nested POU records fail closed.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = Path("D:/GXWORKS2/DNaviZero/DataAbsorber")
HASHES = {
    "DZDataABS_DataManager_IEC.dll": "4e6785f6797f4b621b0df3876172e45b43bb8f60a85abe715b1edbba05fc2b6d",
    "DZDataABS_SICConverter_IEC.dll": "670fd037852287e4c99de6aa58d19ea0ba9505f10bb46969d2e17d1e54b863f6",
}


def blob(raw=b"", relocations=()):
    return {"raw": base64.b64encode(raw).decode(), "relocations": list(relocations)}


def relocate(raw, offset, data, relocations):
    struct.pack_into("<I", raw, offset, 0)
    relocations.append({"offset": offset, "data": data})


def text_blob(text):
    if text is None:
        raise ValueError("missing bounded native name")
    return blob(text.encode("ascii") + b"\0")


def array_blob(array):
    if array.get("error") or array["count"] != len(array["records"]):
        raise ValueError("incomplete native array capture")
    label, stride = array["label"], array["stride"]
    raw, relocs = bytearray(), []
    for record in array["records"]:
        start = len(raw)
        chunk = bytes.fromhex(record["raw"])
        if len(chunk) != stride:
            raise ValueError("native record truncated")
        raw.extend(chunk)
        if label == "pou" and any(chunk[32:]):
            raise ValueError("nested action/zoom/transition descriptors not yet captured")
        if label == "pou" and any(chunk[4:8]):
            raise ValueError("POU completion pointer has not been captured for rebasing")
        relocate(raw, start + (8 if label == "pou" else 0), text_blob(record["name"]), relocs)
        if label == "task":
            relocate(raw, start + 12, text_blob(record["resource_name"]), relocs)
        for payload in record["payloads"]:
            data = bytes.fromhex(payload["bytes"] or "")
            if len(data) != payload["size"]:
                raise ValueError("native source payload truncated")
            relocate(raw, start + payload["pointer_offset"], blob(data), relocs)
    return blob(raw, relocs)


def plan_from_trace(path: Path):
    rows = [json.loads(line)["message"].get("payload", {}) for line in path.read_text().splitlines()]
    front = next(r for r in rows if r.get("event") == "frontend_enter" and r.get("name") == "Memory.ParseData")
    build = front["build"]
    raw = bytearray.fromhex(build["header"])
    if len(raw) != 52 or front.get("error"):
        raise ValueError("incomplete source frontend observation")
    relocs = []
    relocate(raw, 0, text_blob(build["name"]), relocs)
    libraries, lib_relocs = bytearray(), []
    for lib in build["libraries"]:
        start = len(libraries)
        libraries.extend(bytes.fromhex(lib["raw"]))
        if len(libraries) != start + 32:
            raise ValueError("library descriptor truncated")
        relocate(libraries, start, text_blob(lib["name"]), lib_relocs)
        for a in lib["arrays"]:
            offset = {"global": 8, "structure": 16, "pou": 24}[a["label"]]
            relocate(libraries, start + offset, array_blob(a), lib_relocs)
    if len(libraries) != build["library_count"] * 32:
        raise ValueError("library count mismatch")
    relocate(raw, 8, blob(libraries, lib_relocs), relocs)
    for a in build["arrays"]:
        offset = {"resource": 12, "global": 20, "structure": 28, "task": 36, "pou": 44}[a["label"]]
        relocate(raw, offset, array_blob(a), relocs)
    parameter = bytearray.fromhex(front["parameter"])
    address, size = struct.unpack_from("<II", parameter)
    block = bytes.fromhex(front.get("parameter_block") or "")
    if len(block) != size:
        # Earlier observation mislabelled the pointer/length pair. Recover only
        # from the exact same address and length observed downstream, never guess.
        candidates = {bytes.fromhex(b["bytes"]) for r in rows for b in r.get("buffers", [])
                      if b.get("label") == "parameter_block" and int(b["address"], 16) == address
                      and b["requested_size"] == size}
        if len(candidates) != 1:
            raise ValueError("parameter block was not captured consistently")
        block = candidates.pop()
    param_relocs = []
    relocate(parameter, 0, blob(block), param_relocs)
    build_call = next(r for r in rows if r.get("event") == "frontend_enter" and r.get("name") == "Process.Build")
    return dict(cpu=front["cpu"], build_flags=int(build_call["args"][0]["value"], 16),
                memory_allocation_size=4096, process_allocation_size=256,
                build=blob(raw, relocs), parameter=blob(parameter, param_relocs),
                options=blob(struct.pack("<" + "I" * len(front["options"]), *front["options"])))


def run_plan(plan, output: Path, provenance=None):
    if os.name != "nt":
        raise ValueError("requires the inspected Windows native frontend")
    for name, expected in HASHES.items():
        if hashlib.sha256((LIBRARY / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frontend DLL differs: " + name)
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    work = output / "work"
    work.mkdir()
    # Never inherit a captured/saved working path: vendor cleanup removes it.
    plan = dict(plan, library_directory=str(LIBRARY), working_directory=str(work),
                memory_dll=str(LIBRARY / "DZDataABS_DataManager_IEC.dll"),
                process_dll=str(LIBRARY / "DZDataABS_SICConverter_IEC.dll"))
    plan_path = output / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    source = output / "FrontendReplayOracle.cs"
    source.write_bytes((ROOT / "research/native/FrontendReplayOracle.cs").read_bytes())
    request = dict(provenance=provenance or {}, dll_sha256=HASHES,
                   source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                   plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest())
    (output / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    exe = output / "FrontendReplayOracle.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/r:System.Web.Extensions.dll",
                            "/r:System.Windows.Forms.dll", "/out:" + str(exe), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (output / "build.txt").write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError("adapter build failed; evidence retained")
    try:
        run = subprocess.run([str(exe), str(plan_path)], cwd=output, capture_output=True,
                             timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        code, stdout, stderr = run.returncode, run.stdout, run.stderr
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = "timeout", exc.stdout or b"", exc.stderr or b""
    (output / "stdout.txt").write_bytes(stdout)
    (output / "stderr.txt").write_bytes(stderr)
    result = dict(request, returncode=code, adapter_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    (output / "process.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args()
    plan = json.loads(args.input.read_text()) if args.plan else plan_from_trace(args.input)
    result = run_plan(plan, args.output, {"input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest()})
    print(json.dumps({"returncode": result["returncode"], "output": str(args.output.resolve())}))
    events = args.output / "native-events.jsonl"
    if events.exists():
        print(events.read_text())


if __name__ == "__main__":
    main()
