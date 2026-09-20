"""Offline native token oracle, isolated from the application and real devices.

Uses the user's installed GX Works2 conversion DLL; does not redistribute it.
Native decoding is independent of our opcode/operand tables. Native encoding
and decoding share vendor code, so their agreement is not a compile/reopen
oracle. Keep GUI CSV checks and independent source expectations alongside it.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.lossless import inspect_project, sha256
from gxw.token_listing import decode_token_listing, TokenInstruction, TokenText, TokenLabel
from gxw.token_pou import parse_token_pou
from gxw.models import GXWFormatError

DLL_SHA256 = "a700da129ede89869a00a5614d08b43dc64e94c240494fc753fe09104c5b9399"
DEFAULT_DLL = Path("D:/GXWORKS2/Easysocket/CodeGenerator/ECCodeGeneratorFX2.dll")


def native_batch(requests, directory: Path, *, dll=DEFAULT_DLL, cpu_code=0x208, encode_option1=1):
    """Persist inputs before invoking native code; retain partial/crash results.

    Requests contain mode, input_base64 and optional provenance. Decode input
    is the source token body plus one zero sentinel. Encode input consists of
    zero-terminated instruction strings and an additional zero terminator.
    Experimental stored-steps returns the vendor's stored-field sum in
    native_value. recompute-width accepts one bounded binary instruction and
    returns its revised bytes using private 15.31 RVA 0x3E534; native_value is
    its signed length/error result. Neither operation is a project validator.
    machinecode accepts a bounded token body WITHOUT the decode sentinel,
    invokes ChangePToMcode with its six-field buffer ABI and native-initialized
    device-offset workspace, and retains output bytes/native word count.
    from-machinecode reverses the buffer roles for ChangeMToPcode and uses its
    returned byte count (the returned +8 pointer is a temporary native buffer).
    error_offset is the unmodified GetErrorOffset result on failure, otherwise
    null. Its units depend on the native operation; P -> M uses token bytes.
    The CPU code is a native adapter value, not inferred project CPU identity.
    """
    if os.name != "nt" or sha256(dll.read_bytes()) != DLL_SHA256:
        raise ValueError("requires Windows and the inspected ECCodeGeneratorFX2.dll 15.31 hash")
    directory.mkdir(parents=True, exist_ok=False)
    evidence = {"dll_sha256": DLL_SHA256, "cpu_code": cpu_code, "encode_option1": encode_option1, "requests": requests,
                "scope": "in-process offline native conversion; no native project compile/reopen"}
    (directory / "requests.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source = directory / "TokenOracle.cs"
    source.write_bytes((ROOT / "research/native/TokenOracle.cs").read_bytes())
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    executable = directory / "TokenOracle.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/out:" + str(executable), str(source)],
                           capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if build.returncode:
        (directory / "build-failure.bin").write_bytes(build.stdout + build.stderr)
        raise RuntimeError("native adapter build failed; output retained")
    protocol = "".join(f"{r['mode']}\t{i}\t{r['input_base64']}\n" for i, r in enumerate(requests))
    try:
        run = subprocess.run([str(executable.resolve()), str(dll.resolve()), str(cpu_code), str(encode_option1)], input=protocol.encode("ascii"),
                             capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, returncode = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, returncode = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "native-stdout.jsonl").write_bytes(stdout)
    (directory / "native-stderr.bin").write_bytes(stderr)
    rows = [json.loads(line) for line in stdout.splitlines()]
    status = {"returncode": returncode, "completed": len(rows), "requested": len(requests),
              "adapter_source_sha256": sha256(source.read_bytes()), "adapter_sha256": sha256(executable.read_bytes())}
    (directory / "process.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    if returncode or len(rows) != len(requests) or [r["id"] for r in rows] != list(range(len(requests))):
        raise RuntimeError("native process failed or produced incomplete results; immutable inputs/output retained")
    return rows


def native_il_records(raw, *, encoding="cp936"):
    """Read native IL result framing without using our source token decoder."""
    cursor, records, line_start = 0, [], True
    while cursor < len(raw):
        size = raw[cursor]
        if size == 0:  # native instruction delimiter
            cursor += 1
            line_start = True
            continue
        token = raw[cursor:cursor + size]
        if size < 3 or len(token) != size or token[-1] != 0:
            raise ValueError(f"unsupported native IL framing at {cursor}")
        kind, text = token[1], token[2:-1].decode(encoding)
        if kind >= 0x90:
            if line_start and kind in (0xD0, 0xD1):
                records.append({"kind": "label", "text": text})
            elif line_start or not records or records[-1]["kind"] != "instruction":
                raise ValueError("native IL operand has no instruction")
            else:
                records[-1]["args"].append(text)
        elif kind in (0x80, 0x82):
            records.append({"kind": "statement" if kind == 0x80 else "note", "text": text})
        elif kind < 0x80:
            records.append({"kind": "instruction", "op": text, "args": []})
        else:
            raise ValueError(f"unknown native IL class {kind}")
        line_start = False
        cursor += size
    return records


def canonical_record(record):
    # Native FX X/Y zero padding and H leading zero are lexical aliases only.
    if record["kind"] != "instruction":
        return record
    def operand(value):
        match = re.fullmatch(r"(K[1-8])?([XY])([0-7]+)([ZV][0-7])?", value)
        if match:
            return (match[1] or "") + match[2] + format(int(match[3], 8), "o") + (match[4] or "")
        if re.fullmatch(r"H[0-9A-F]+", value):
            return "H" + format(int(value[1:], 16), "X")
        return value
    return dict(record, args=[operand(a) for a in record["args"]])


def listing_records(listing):
    decoded = []
    for record in listing.records:
        if isinstance(record, TokenInstruction):
            decoded.append({"kind": "instruction", "op": record.mnemonic, "args": list(record.args)})
        elif isinstance(record, TokenText):
            decoded.append({"kind": record.role, "text": record.text})
        elif isinstance(record, TokenLabel):
            decoded.append({"kind": "label", "text": record.text})
        else:
            decoded.append({"kind": "gap", "raw": [t.raw.hex() for t in record.tokens]})
    return decoded


def cross_check(raw, native, *, encoding="cp936"):
    ours = decode_token_listing(raw, text_encoding=encoding)
    observed = native_il_records(native, encoding=encoding)
    decoded = listing_records(ours)
    a, b = [canonical_record(r) for r in decoded], [canonical_record(r) for r in observed]
    return {"status": "agrees" if a == b else "differs", "our_gaps": len(ours.gaps),
            "our_records": len(a), "native_records": len(b),
            "native_instructions": sum(r["kind"] == "instruction" for r in b),
            "differences": [{"index": i, "ours": a[i] if i < len(a) else None,
                             "native": b[i] if i < len(b) else None}
                            for i in range(max(len(a), len(b))) if i >= len(a) or i >= len(b) or a[i] != b[i]]}


def check_programs(programs, directory, *, dll=DEFAULT_DLL, cpu_code=0x208):
    requests = [{"mode": "decode", "input_base64": base64.b64encode(parse_token_pou(raw).body + b"\0").decode(),
                 "program_sha256": sha256(raw), "provenance": provenance} for raw, provenance in programs]
    rows = native_batch(requests, directory, dll=dll, cpu_code=cpu_code)
    checks = []
    for (raw, provenance), row in zip(programs, rows):
        case = {"program_sha256": sha256(raw), "provenance": provenance, "native_return_code": row["return_code"]}
        if row["return_code"] != "0x00000000":
            case.update(status="native-error", consumed_bytes=row["consumed_bytes"])
        else:
            case.update(cross_check(raw, base64.b64decode(row["output_base64"])))
            case["consumed_all_body"] = row["consumed_bytes"] == len(raw) - 103
            if not case["consumed_all_body"]:
                case["status"] = "incomplete-native-decode"
        checks.append(case)
    result = {"dll_sha256": DLL_SHA256, "cpu_code": cpu_code, "cases": checks,
              "summary": dict(Counter(c["status"] for c in checks)),
              "native_compile_reopen": "not_run; separate GUI evidence required"}
    (directory / "cross-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    from gxw_corpus import inputs
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    parser.add_argument("--cpu-code", type=lambda s: int(s, 0), default=0x208)
    parser.add_argument("-o", "--output", type=Path, required=True, help="new local evidence directory")
    args = parser.parse_args()
    programs, skipped = {}, []
    if args.fixture:
        for case in json.loads(args.fixture.read_text(encoding="utf-8"))["cases"]:
            raw = base64.b64decode(case["program_base64"])
            if sha256(raw) != case["program_sha256"]:
                raise ValueError("fixture source hash changed")
            programs.setdefault(sha256(raw), (raw, []))[1].append({"fixture": str(args.fixture), "case": case["source"]})
    for location, source in inputs(args.paths):
        image = inspect_project(source)
        if image.diagnostics:
            skipped.append({"source": location, "reason": "container inventory diagnostics", "diagnostics": list(image.diagnostics)})
        for stream in image.streams:
            if not (stream.logical_name or "").endswith(".Program.pou") or stream.raw is None:
                continue
            try:
                parse_token_pou(stream.raw)
            except GXWFormatError:
                skipped.append({"source": location, "program": stream.logical_name, "reason": "outside token envelope"})
                continue
            programs.setdefault(sha256(stream.raw), (stream.raw, []))[1].append({"source": location, "program": stream.logical_name})
    result = check_programs(list(programs.values()), args.output, dll=args.dll, cpu_code=args.cpu_code)
    (args.output / "skipped.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "skipped": len(skipped)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
