"""Observe native compiler calls in an explicitly selected GX Works2 process.

The injected code only reads bounded arguments and attaches listeners. It does
not call the compiler, replace functions, alter arguments, or automate the UI.
Use an isolated public project; trace output may contain its source and paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/experiments/re-tools"))
import frida

DLL = Path("D:/GXWORKS2/Easysocket/Compiler/ECCompiler.dll")
DLL_HASH = "4f7b2398874c7a921f49f13f25a9a603562032b039de8a5bb74114df27fadf16"
IEC_DLL = DLL.with_name("ECCompiler_IEC.dll")
IEC_DLL_HASH = "f58e7c70b855526bf2f943399a14089f771a43109b13ee9af34c88fb065cf4d7"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--frontend", action="store_true", help="also observe the IEC source frontend")
    parser.add_argument("--workspace", action="store_true", help="also observe project-open COM calls")
    parser.add_argument("--sfc", action="store_true", help="also observe native SFC conversion and program checks")
    args = parser.parse_args()
    if hashlib.sha256(DLL.read_bytes()).hexdigest() != DLL_HASH:
        raise ValueError("native DLL differs from inspected version")
    if hashlib.sha256(IEC_DLL.read_bytes()).hexdigest() != IEC_DLL_HASH:
        raise ValueError("native IEC DLL differs from inspected version")
    device = frida.get_local_device()
    process = next((p for p in device.enumerate_processes() if p.pid == args.pid), None)
    if process is None or process.name.lower() != "gd2.exe":
        raise ValueError("explicit PID must identify GX Works2 GD2.exe")
    args.output.mkdir(parents=True, exist_ok=False)
    source = (ROOT / "research/native/CompilerTrace.js").read_bytes()
    frontend_hashes = {}
    if args.sfc:
        name = "DZDataABS_CompilerAdapter.dll"
        expected = "4a048e05c189d903d1f8384fd9730c3094d4ef98c795259ba07496fb381b3959"
        actual = hashlib.sha256((Path("D:/GXWORKS2/DNaviZero/DataAbsorber") / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("native SFC adapter differs from inspected version")
        frontend_hashes[name] = actual
        source = (ROOT / "research/native/SfcTrace.js").read_bytes() + b"\n" + source
    if args.frontend:
        for name, expected in {
            "DZDataABS_DataManager_IEC.dll": "4e6785f6797f4b621b0df3876172e45b43bb8f60a85abe715b1edbba05fc2b6d",
            "DZDataABS_SICConverter_IEC.dll": "670fd037852287e4c99de6aa58d19ea0ba9505f10bb46969d2e17d1e54b863f6",
        }.items():
            path = Path("D:/GXWORKS2/DNaviZero/DataAbsorber") / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"native frontend DLL differs: {name}")
            frontend_hashes[name] = actual
        source = (ROOT / "research/native/FrontendTrace.js").read_bytes() + b"\n" + source
    if args.workspace:
        for name, expected in {
            "DZDataABS_ProjectOperation.dll": "74e4cd0fd98215eb36a7c5df7103fdb3cc917d636e036f7f8b774a89b01dcfdb",
            "DZDataABS_Workspace.dll": "861c1e25aebb05f6f6ef972f721bcff047c01679b89fe9922abd4743e137676f",
            "DZDataABS_DataManager_IEC.dll": "4e6785f6797f4b621b0df3876172e45b43bb8f60a85abe715b1edbba05fc2b6d",
            "DZDataABS_CompilerAdapter.dll": "4a048e05c189d903d1f8384fd9730c3094d4ef98c795259ba07496fb381b3959",
        }.items():
            actual = hashlib.sha256((Path("D:/GXWORKS2/DNaviZero/DataAbsorber") / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("workspace DLL differs: " + name)
            frontend_hashes[name] = actual
        source = (ROOT / "research/native/WorkspaceTrace.js").read_bytes() + b"\n" + source
    (args.output / "CompilerTrace.js").write_bytes(source)
    request = dict(pid=args.pid, process=process.name, dll_sha256=DLL_HASH,
                   iec_dll_sha256=IEC_DLL_HASH,
                   frontend_dll_sha256=frontend_hashes,
                   script_sha256=hashlib.sha256(source).hexdigest(),
                   frida_version=frida.__version__, seconds=args.seconds,
                   scope="bounded read-only compiler argument observations")
    (args.output / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    lock = threading.Lock()
    with (args.output / "events.jsonl").open("w", encoding="utf-8") as output:
        def receive(message, data):
            with lock:
                output.write(json.dumps(dict(time_ns=time.time_ns(), message=message), ensure_ascii=True) + "\n")
                output.flush()
            if message.get("type") == "error":
                print(json.dumps(message), flush=True)
        session = device.attach(args.pid)
        script = session.create_script(source.decode("utf-8"))
        script.on("message", receive)
        try:
            script.load()
            print("Trace attached; create STOP in output directory to detach.", flush=True)
            deadline = time.monotonic() + min(args.seconds, 3600)
            while time.monotonic() < deadline and not (args.output / "STOP").exists():
                time.sleep(.25)
        finally:
            script.unload()
            session.detach()
    print("Trace detached.", flush=True)


if __name__ == "__main__":
    main()
