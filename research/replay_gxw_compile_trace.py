"""Rebase a complete observed compiler input trace into an isolated process."""
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
DLL = Path("D:/GXWORKS2/Easysocket/Compiler/ECCompiler_IEC.dll")
DLL_HASH = "f58e7c70b855526bf2f943399a14089f771a43109b13ee9af34c88fb065cf4d7"


def b64(b):
    return base64.b64encode(b).decode("ascii")


def plan_from_trace(trace: Path):
    rows = [json.loads(l)["message"].get("payload", {}) for l in trace.read_text().splitlines()]
    enters = [r for r in rows if r.get("event") == "enter"]
    def event(name):
        return next(r for r in enters if r["name"] == name)
    def buffers(row):
        result = {}
        for b in row.get("buffers", []):
            raw = bytes.fromhex(b["bytes"] or "")
            if "requested_size" in b and len(raw) != b["requested_size"]:
                raise ValueError("trace buffer truncated")
            result[b["label"]] = raw
        return result
    init, start = event("ChangeInit"), event("CompileStart")
    words = [int(a["value"], 16) for a in start["args"][2:26]]
    # Rebase only the parameter-block pointer; all other observed words remain.
    parameter_block = buffers(start)["parameter_block"]
    words[1] = 0
    parameters = {"raw": b64(struct.pack("<24I", *words)), "relocations": [{"offset": 4, "data": b64(parameter_block)}]}
    options_raw = bytes.fromhex(start["args"][26]["bytes"])
    end = next(i for i in range(0, len(options_raw), 4) if options_raw[i:i+4] == b"\xff"*4) + 4
    compile_rows = []
    for row in enters:
        if row["name"] != "Compile":
            continue
        b = buffers(row)
        descriptors = bytearray(b["compile_descriptors"])
        count = int(row["args"][1]["value"], 16)
        if len(descriptors) != count*28:
            raise ValueError("descriptor count differs")
        relocations = []
        for i in range(count):
            name = b[f"name_{i}"]
            name = name[:name.index(b"\0")+1]
            source = b[f"source_{i}"]
            if len(source) != struct.unpack_from("<i", descriptors, i*28)[0]:
                raise ValueError("source length differs")
            struct.pack_into("<II", descriptors, i*28+4, 0, 0)
            relocations.extend([{"offset": i*28+4, "data": b64(source)}, {"offset": i*28+8, "data": b64(name)}])
        compile_rows.append({"event_id": row["id"], "count": count, "symbols": b64(b["symbolic_data"]),
                             "descriptors": {"raw": b64(descriptors), "relocations": relocations}})
    return dict(init_a=int(init["args"][2]["value"], 16), init_b=int(init["args"][3]["value"], 16),
                system_variables=b64(buffers(event("SetSystemVariables"))["system_variables"]),
                parameters=parameters, options=b64(options_raw[:end]),
                start_mode=int(start["args"][1]["value"], 16), start_flags=int(start["args"][27]["value"], 16),
                link_flags=int(event("Link")["args"][3]["value"], 16), compile=compile_rows)


def run_plan(plan, output: Path, *, provenance=None):
    """Execute pointer-free inputs in a newly created, isolated work directory.

    Paths from a saved plan are never reused: ObjectDelete can remove the
    compiler's work directory. The adapter and native DLL are pinned anew.
    A zero return code is not proof that requested program units were emitted.
    """
    if os.name != "nt" or hashlib.sha256(DLL.read_bytes()).hexdigest() != DLL_HASH:
        raise ValueError("requires the inspected Windows IEC compiler")
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    work = output / "work"
    work.mkdir()
    plan = dict(plan, dll=str(DLL), library_directory=str(DLL.parent) + "\\", working_directory=str(work))
    plan_path = output / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    source = output / "CompilerReplayOracle.cs"
    source.write_bytes((ROOT / "research/native/CompilerReplayOracle.cs").read_bytes())
    request = dict(provenance=provenance or {}, dll_sha256=DLL_HASH,
                   plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                   source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), compile_calls=len(plan["compile"]),
                   differences_from_gui=["empty isolated working directory", "Link count=0 selects all native resources", "no SetSymbolicData restoration"])
    (output / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    exe = output / "CompilerReplayOracle.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/r:System.Web.Extensions.dll", "/out:"+str(exe), str(source)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (output / "build.txt").write_bytes(build.stdout+build.stderr)
    if build.returncode:
        raise RuntimeError("build failed; evidence retained")
    try:
        run = subprocess.run([str(exe), str(plan_path)], cwd=output, capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        code, stdout, stderr = run.returncode, run.stdout, run.stderr
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = "timeout", exc.stdout or b"", exc.stderr or b""
    (output / "stdout.txt").write_bytes(stdout)
    (output / "stderr.txt").write_bytes(stderr)
    result = dict(request, returncode=code, adapter_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    (output / "process.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--plan", action="store_true", help="input is a saved pointer-free plan, not a raw trace")
    args = parser.parse_args()
    plan = json.loads(args.input.read_text()) if args.plan else plan_from_trace(args.input)
    result = run_plan(plan, args.output, provenance={
        "input_kind": "pointer-free-plan" if args.plan else "read-only-gui-trace",
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest()})
    print(json.dumps({"returncode": result["returncode"], "output": str(args.output.resolve())}))
    log = args.output / "native-events.jsonl"
    print(log.read_text() if log.exists() else "no native events")


if __name__ == "__main__":
    main()
