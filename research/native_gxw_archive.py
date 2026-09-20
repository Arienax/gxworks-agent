"""Read-only native CArchive cross-check of a Python-supplied read plan."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.compiler_call_tree import parse_compiler_call_tree
from gxw.lossless import sha256

DLL = Path("D:/GXWORKS2/DNaviZero/DataAbsorber/DZDataABS_DataManager.dll")
MFC = Path("C:/Windows/SysWOW64/mfc71.dll")


def compare_call_tree_replay(before, after):
    """Ignore only CMap iteration order, preserving raw string encodings.

    Nodes and their reference lists are serialized by native CMap iteration.
    All other fields, multiplicities, embedded bytes and opaque tail must agree.
    """
    a, b = parse_compiler_call_tree(before), parse_compiler_call_tree(after)
    def node_key(node):
        return (node.flags, node.kind_code, tuple(s.raw for s in node.names),
                tuple(sorted(s.raw for s in node.references)))
    equal = [Counter(map(node_key, x)) == Counter(map(node_key, y)) for x, y in zip(a.maps, b.maps)]
    resource_equal, opaque_equal = a.resource_tree == b.resource_tree, a.opaque_tail == b.opaque_tail
    return {"byte_identical": before == after, "map_content_equal_ignoring_iteration_order": equal,
            "resource_tree_equal": resource_equal, "opaque_tail_preserved": opaque_equal,
            "structurally_identical": all(equal) and resource_equal and opaque_equal}


def native_call_tree_replay(raw, directory):
    parse_compiler_call_tree(raw)
    if os.name != "nt" or not 0 < len(raw) <= 65536:
        raise ValueError("requires Windows and an inspected bounded call tree")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "CallTreeReplayOracle.cs"
    source.write_bytes((ROOT / "research/native/CallTreeReplayOracle.cs").read_bytes())
    (directory / "input.bin").write_bytes(raw)
    (directory / "CallTree.dat").write_bytes(raw)
    request = {"input_sha256": sha256(raw), "adapter_source_sha256": sha256(source.read_bytes()),
               "dll_sha256": sha256(DLL.read_bytes()), "mfc_sha256": sha256(MFC.read_bytes()),
               "scope": "native CallTree component Load/Save with inspected file-path getters; isolated copies; not whole-project acceptance"}
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    exe = directory / "CallTreeReplayOracle.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/out:" + str(exe), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "build-output.bin").write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError("native call-tree replay adapter build failed")
    try:
        run = subprocess.run([str(exe.resolve()), str(DLL), str(MFC), str(directory.resolve())],
                             capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, code = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "stdout.txt").write_bytes(stdout); (directory / "stderr.txt").write_bytes(stderr)
    after_load, after_save = directory / "after-load.dat", directory / "native-save.dat"
    result = dict(request, returncode=code, adapter_sha256=sha256(exe.read_bytes()),
                  load_input_unchanged=after_load.exists() and after_load.read_bytes() == raw)
    (directory / "process.json").write_text(json.dumps(result, indent=2) + "\n")
    if code:
        raise RuntimeError("native call-tree replay failed; input and partial output retained")
    result.update(compare_call_tree_replay(raw, after_save.read_bytes()))
    (directory / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def call_tree_plan(raw):
    tree = parse_compiler_call_tree(raw)
    operations, offset = [], 0
    def add(kind, start, end, value):
        operations.append({"operation": kind, "offset": start, "end_offset": end,
                           "value_base64": base64.b64encode(value).decode()})
    def string_value(string):
        if string.character_width == 1:
            return string.value_bytes
        text = string.text()
        if not text.isascii():
            raise ValueError("native ANSI comparison needs an explicit non-ASCII conversion policy")
        return text.encode("ascii")
    for nodes in tree.maps:
        add("u32", offset, offset + 4, raw[offset:offset + 4]); offset += 4
        for node in nodes:
            for _ in range(2):
                add("u32", offset, offset + 4, raw[offset:offset + 4]); offset += 4
            for string in node.names:
                add("string", offset, offset + len(string.raw), string_value(string)); offset += len(string.raw)
            add("u32", offset, offset + 4, raw[offset:offset + 4]); offset += 4
            for string in node.references:
                add("string", offset, offset + len(string.raw), string_value(string)); offset += len(string.raw)
    if tree.resource_tree is not None:
        add("u32", offset, offset + 4, raw[offset:offset + 4]); offset += 4
        n = len(tree.resource_tree)
        add(f"bytes:{n}", offset, offset + n, tree.resource_tree); offset += n
    if tree.opaque_tail:
        add(f"bytes:{len(tree.opaque_tail)}", offset, len(raw), tree.opaque_tail)
    return operations


def native_archive_read(raw, plan, directory):
    if os.name != "nt" or not 0 < len(raw) < 4096:
        raise ValueError("requires Windows and a single-buffer input below 4096 bytes")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "ArchiveReadOracle.cs"
    source.write_bytes((ROOT / "research/native/ArchiveReadOracle.cs").read_bytes())
    input_path, plan_path, output_path = directory / "input.bin", directory / "operations.txt", directory / "native-reads.jsonl"
    input_path.write_bytes(raw)
    plan_path.write_text("".join(row["operation"] + "\n" for row in plan), encoding="ascii")
    (directory / "expected-reads.json").write_text(json.dumps(plan, indent=2) + "\n")
    request = {"input_sha256": sha256(raw), "adapter_source_sha256": sha256(source.read_bytes()),
               "dll_sha256": sha256(DLL.read_bytes()), "mfc_sha256": sha256(MFC.read_bytes()),
               "operations_sha256": sha256(plan_path.read_bytes()),
               "scope": "read-only CArchive primitive reads; Python selects framing operations; no native project load"}
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    exe = directory / "ArchiveReadOracle.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/out:" + str(exe), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "build-output.bin").write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError("native archive adapter build failed")
    try:
        run = subprocess.run([str(exe.resolve()), str(DLL), str(MFC), str(input_path.resolve()),
                              str(plan_path.resolve()), str(output_path.resolve())],
                             capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, code = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "stdout.txt").write_bytes(stdout); (directory / "stderr.txt").write_bytes(stderr)
    result = dict(request, returncode=code, input_unchanged=input_path.read_bytes() == raw,
                  adapter_sha256=sha256(exe.read_bytes()))
    (directory / "process.json").write_text(json.dumps(result, indent=2) + "\n")
    if code:
        raise RuntimeError("native archive read failed; input and partial output retained")
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    result["reads_complete"] = len(rows) == len(plan) and [r["index"] for r in rows] == list(range(len(plan)))
    result["mismatches"] = [{"index": i, "field": key} for i, (expected, actual) in enumerate(zip(plan, rows))
                            for key in ("operation", "end_offset", "value_base64") if expected[key] != actual[key]]
    result["read_count"] = len(rows)
    (directory / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="extracted CallTree.dat")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("primitives", "replay"), default="replay")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = (native_call_tree_replay(raw, args.output) if args.mode == "replay" else
              native_archive_read(raw, call_tree_plan(raw), args.output))
    print(json.dumps(result))
