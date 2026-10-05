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
import struct
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
Q_DLL = DEFAULT_DLL.with_name("ECCodeGenerator2.dll")
Q_DLL_SHA256 = "2e5c47b0a0d0b59eb26dbab149862a05983a1fa9519a75e446bc680e7cc16f73"


def native_batch(requests, directory: Path, *, dll=DEFAULT_DLL, cpu_code=0x208, encode_option1=1,
                 native_versions=None, device_context=None):
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
    native_versions explicitly replays the inspected Q SetVersion(count,
    uint32*) initialization. Values ()/(1,)/(25,) were observed in an actual
    Q03UDV build; omission retains the unconfigured historical oracle behavior.
    This setting is not inferred from successful decoding or a CPU name.
    device_context is an explicit raw Q device-allocation table captured from
    the project compiler: a 6-byte header plus count * 4 bytes. It affects
    operand-range checks; do not invent or broaden it to make an input pass.
    """
    # Q03UDV's native offline build opens ECCodeGenerator2 with adapter CPU
    # 209, not its Navigator CPU identifier 192. Public text conversion and
    # GetStepSize (Q 0x61ab -> 0x17e9) have inspected ABIs; FX private routines
    # and machine buffers are deliberately excluded.
    dll = Path(dll)
    dll_hash = sha256(dll.read_bytes())
    profile = {DLL_SHA256: "fx", Q_DLL_SHA256: "q"}.get(dll_hash)
    if os.name != "nt" or profile is None:
        raise ValueError("requires Windows and an inspected native token converter hash")
    if profile == "q" and (cpu_code not in (209, 34) or any(
            r["mode"] not in ("decode", "encode", "stored-steps") for r in requests)):
        raise ValueError("Q oracle requires an observed CPU and public conversion operation")
    # Q02/Q02H Open(cpu=34, mode=0) is observed in the independently compiled
    # new-source control; traced/untraced PCode agrees. Extend decoding only.
    if profile == "q" and cpu_code == 34 and any(r["mode"] != "decode" for r in requests):
        raise ValueError("Q02/Q02H oracle is limited to independently observed text decoding")
    if native_versions is not None:
        native_versions = tuple(native_versions)
        if profile != "q" or native_versions not in ((), (1,), (25,)):
            raise ValueError("SetVersion is limited to independently observed Q configuration values")
    if device_context is not None:
        if (profile != "q" or native_versions is None or
                any(r["mode"] != "encode" for r in requests)):
            raise ValueError("device context requires explicitly configured Q encoding")
        if (not isinstance(device_context, bytes) or not 6 <= len(device_context) <= 4096 or
                struct.unpack_from('<HH', device_context) != (len(device_context), 0x2000) or
                len(device_context) != 6 + 4 * struct.unpack_from('<H', device_context, 4)[0]):
            raise ValueError("unrecognized bounded native device-allocation table")
    directory.mkdir(parents=True, exist_ok=False)
    evidence = {"dll_sha256": dll_hash, "profile": profile, "cpu_code": cpu_code, "encode_option1": encode_option1,
                "native_versions": native_versions, "requests": requests,
                "scope": "in-process offline native conversion; no native project compile/reopen"}
    if device_context is not None:
        (directory / "device-context.bin").write_bytes(device_context)
        evidence["device_context"] = {"sha256": sha256(device_context), "bytes": len(device_context),
                                      "handling": "raw-preserved; supplied explicitly"}
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
    command = [str(executable.resolve()), str(dll.resolve()), str(cpu_code), str(encode_option1), profile]
    if native_versions is not None:
        command.append(",".join(str(value) for value in native_versions))
    if device_context is not None:
        command.append(str((directory / "device-context.bin").resolve()))
    try:
        run = subprocess.run(command, input=protocol.encode("ascii"),
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
    return _read_native_il(raw, encoding=encoding, preserve_opaque_text=False)["records"]


def native_il_projection(raw, *, encoding):
    """Keep undecodable text local to its native-output token.

    Cross-language native paste can retain source-language comments. A project
    code page therefore is not proof that every text token uses that encoding.
    Do not guess another encoding or replace characters. Missing text is None;
    each gap retains the exact token and its offset in the native IL output,
    not an invented offset in the original POU. Grammar errors still reject.
    """
    return _read_native_il(raw, encoding=encoding, preserve_opaque_text=True)


def _read_native_il(raw, *, encoding, preserve_opaque_text):
    from gxw.token_listing import native_il_projection as project_native_il
    result = project_native_il(raw, encoding=encoding, preserve_opaque_text=preserve_opaque_text)
    # Preserve the existing research output field; Core compares actual bytes.
    return {**result, "native_output_sha256": sha256(raw)}


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
    if sha256(Path(dll).read_bytes()) != DLL_SHA256:
        raise ValueError("this Python lexical cross-check is FX-specific; use native_batch for Q observations")
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
