"""Independent offline PCode -> ladder conversion, with raw native buffers.

The FX3G project's real compiler calls ChangePToLDcode with adapter CPU 521,
mode 0, count 1 and packed descriptor fields 23, height, 8. Replaying all 454
calls, including six failures, matches the public call's result, consumed byte
count, returned count, descriptor fields and error offset. Evidence is under
experiments/sfc-graph-20260926/public-corpus-discovery/fx3g-ladder-call-corpus.

This interface converts at most one native circuit per call. A success can
consume only a prefix. It does not validate a whole project or execute code.
The two native output buffers remain opaque here; failure output is partial.
GetErrorOffset is retained verbatim, not interpreted as a source line/step.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess

from native_gxw_tokens import DEFAULT_DLL, DLL_SHA256

ROOT = Path(__file__).resolve().parents[1]


def native_ladder_batch(requests, directory: Path, *, dll: Path = DEFAULT_DLL):
    """Retain requests, helper, process results and guarded raw output buffers.

    Each request has input_base64 (2..32768 bytes ending in one zero sentinel)
    and optionally initial_height (0..24, default 1). Other request fields are
    provenance. The descriptor's remaining fields and CPU are fixed to the
    observed native configuration. A new code-generator object isolates each
    request from preceding calls; 454 real calls verify this replay boundary.

    Returned consumed_bytes includes the sentinel only when native consumes
    it. Callers must check consumption before claiming full-input coverage.
    Even complete, successful conversion is not ProgramCheck or reopen proof.
    """
    from hashlib import sha256

    dll = Path(dll).resolve()
    if os.name != "nt" or sha256(dll.read_bytes()).hexdigest() != DLL_SHA256:
        raise ValueError("requires the inspected Windows FX2 15.31 converter")
    normalized = []
    for request in requests:
        raw = base64.b64decode(request["input_base64"], validate=True)
        if not 2 <= len(raw) <= 32768 or raw[-1:] != b"\0":
            raise ValueError("PCode input requires the observed bound and zero sentinel")
        height = request.get("initial_height", 1)
        if type(height) is not int or not 0 <= height <= 24:
            raise ValueError("descriptor height outside native observations")
        if request.get("cpu", 521) != 521:
            raise ValueError("only the independently observed FX3G configuration is admitted")
        normalized.append(dict(request, cpu=521, initial_height=height))
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "LadderByteOracle.cs"
    source.write_bytes((ROOT / "research/native/LadderByteOracle.cs").read_bytes())
    evidence = dict(dll_path=str(dll), dll_sha256=DLL_SHA256, cpu_code=521,
                    requests=normalized, scope="offline conversion, no project or device APIs",
                    adapter_source_sha256=sha256(source.read_bytes()).hexdigest())
    (directory / "requests.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    executable = directory / "LadderByteOracle.exe"
    build = subprocess.run([str(compiler), "/nologo", "/platform:x86", "/r:System.Web.Extensions.dll",
                            "/out:" + str(executable), str(source)], capture_output=True,
                           timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "build.stdout.bin").write_bytes(build.stdout)
    (directory / "build.stderr.bin").write_bytes(build.stderr)
    if build.returncode:
        raise RuntimeError("native ladder helper build failed; output retained")
    protocol = "".join(json.dumps(dict(r, id=i)) + "\n" for i, r in enumerate(normalized))
    try:
        process = subprocess.run([str(executable), str(dll), str(directory)],
                                 input=protocol.encode("ascii"), capture_output=True,
                                 timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, status = process.stdout, process.stderr, process.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, status = exc.stdout or b"", exc.stderr or b"", "timeout"
    (directory / "native-stdout.jsonl").write_bytes(stdout)
    (directory / "native-stderr.bin").write_bytes(stderr)
    outcome = dict(returncode=status, requested=len(normalized), completed=None,
                   adapter_sha256=sha256(executable.read_bytes()).hexdigest())
    outcome_path = directory / "process.json"
    outcome_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    rows = [json.loads(line) for line in stdout.splitlines()]
    outcome["completed"] = len(rows)
    outcome_path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    if (status or len(rows) != len(normalized) or
            [row["id"] for row in rows] != list(range(len(normalized))) or
            not all(row["guards_intact"] and row["pointers_unchanged"] for row in rows)):
        raise RuntimeError("native ladder replay failed or incomplete; evidence retained")
    return rows
