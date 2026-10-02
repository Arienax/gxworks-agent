"""Freeze post-allocation GXW evidence, including failed native experiments.

Read-only evidence collection; no vendor code is executed. Existing checkpoints
are never overwritten. Use verify_gxw_source_allocation_checkpoint.py to verify.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "research/experiments/sfc-graph-20260926/public-corpus-discovery"
PREVIOUS = ROOT / "research/results/gxw-check-allocation-20260930/manifest.json"
OUT = ROOT / "research/results/gxw-st-transfers-20260930"
PUBLIC = ROOT / "research/evidence/gxw-st-transfers-20260930.zip"
LOCAL = P / "gxw-st-transfers-local-20260930.zip"
EXCLUDED = {".exe", ".dll", ".pdb", ".pyc", ".zip"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rel(path):
    return path.resolve().relative_to(ROOT).as_posix()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main():
    for path in (OUT, PUBLIC, LOCAL):
        if path.exists():
            raise FileExistsError(path)
    prior = read(PREVIOUS)
    cutoff = datetime.fromisoformat(prior["created_utc"]).timestamp()
    selected, public, local, comparisons = [], {}, {}, []

    def add(target, path):
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in EXCLUDED and "__pycache__" not in path.parts:
            target[rel(path)] = path

    for directory in sorted(P.iterdir()):
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        files = [f for f in directory.rglob("*") if f.is_file()
                 and f.suffix.lower() not in EXCLUDED and "__pycache__" not in f.parts]
        if any(f.stat().st_mtime >= cutoff for f in files):
            selected.append(rel(directory))
            for file in files:
                add(local, file)
    for file in P.iterdir():
        if file.is_file():
            if file.suffix in {".py", ".js", ".cs"}:
                add(public, file)
            elif file.stat().st_mtime >= cutoff:
                add(local, file)
    add(local, P / "ST-nested-FB-controls/single/native/native-saved.gxw")
    for directory, patterns in (
        (ROOT / "src/gxw", ("**/*.py", "**/*.json")),
        (ROOT / "research", ("*.py", "native/*.cs", "native/*.js")),
        (ROOT / "tests", ("test_gxw*.py", "fixtures/gxw*.json")),
    ):
        for pattern in patterns:
            for file in directory.glob(pattern):
                add(public, file)
    for name in ("AGENTS.md", "research/README.md", "docs/reports/README.md",
                 "docs/reports/gxw/2026-09-30-st-transfer-checkpoint.md"):
        add(public, ROOT / name)

    def pair(case, candidate, native, source, kind):
        a, b = candidate.read_bytes(), native.read_bytes()
        if a != b:
            raise ValueError(f"Saved bytes differ: {case}")
        for path in (candidate, native, source):
            add(local, path)
        comparisons.append(dict(case=case, candidate=rel(candidate), native=rel(native),
            source=rel(source), source_sha256=digest(source.read_bytes()),
            predicted_sha256=digest(a), native_sha256=digest(b), bytes=len(a), candidate_kind=kind))

    fb_groups = ["st-fb-index-capture-20260930", "st-fb-index-liveness-20260930",
                 "st-fb-index-predictions-20260930"]
    for group in fb_groups:
        for case in read(P / group / "comparison.json"):
            n = Path(case["native_directory"])
            if case.get("prediction_identical") is True:
                pair(f'{group}/{case["case"]}/prediction', n.parent / "predicted-0.bin",
                     n / "pcode-0-0.bin", n / "input.gxw", "prediction-saved-before-native")
            phases = [n] + ([Path(case["reopen"]["native_directory"])] if "reopen" in case else [])
            for phase in phases:
                result = read(phase / "resource-correspondence.json")
                generated = {g["name"]: g["index"] for g in result["generated"]}
                for row in result["resource_reads"]:
                    if row["phase"] != "check" or row["caller"]["rva"] != "0x82a34" or row["hresult"] < 0 or row["code"] != 0:
                        continue
                    for channel in range(3):
                        pair(f'{group}/{case["case"]}/{rel(phase).split(group + "/", 1)[1]}/read-{row["serial"]}-{channel}',
                            phase / f'checked-read-{row["serial"]}-{channel}.bin',
                            phase / f'pcode-{generated[row["resource_name"]]}-{channel}.bin',
                            phase / "input.gxw", "actual-checker-read")

    OUT.mkdir()
    evidence = [
        "port-check-correspondence-20260930.json", "temporary-check-correspondence-20260930.json",
        "st-nested-index-counterexamples-20260930/offline-witnesses.json",
        "st-fb-index-predictions-20260930/offline-transfer-witnesses.json",
        "st-fb-index-predictions-20260930/current-check-export-correspondence.json",
        "st-index-register-lifetime-failure-notes-20260930.json",
    ]
    claims = dict(evidence=[dict(path=rel(P / n), sha256=digest((P / n).read_bytes())) for n in evidence],
        fb_index_cases=17, fb_index_accepted_compile_and_reopen=15, fb_index_precheck_rejections=2,
        complete_pcode_predictions_before_native=4, post_observation_source_fits=11,
        evidence_level="native observations and bounded verified byte reproduction; no production-safe promotion",
        handling="partially-decoded; bounded research source models; opaque unrelated logical payloads preserved",
        findings=[
            "Retain source class 10 reaches native category 6; rejected source class 6 is a different namespace.",
            "General expression slots retain physical reservations while released compatible slots can be reused; INT and DINT/REAL use distinct slot types.",
            "Temporary word bank priority ZR > R > W > D differs from ordinary variable pools, with no exhaustion fallback in measured controls.",
            "Nested dynamic indices can reuse Z16 before the earlier operand is consumed; initialized finite witnesses distinguish source arithmetic from emitted instructions.",
            "Dynamic ST IN_OUT copies in then recomputes the actual index for copy-back; direct scalar IN_OUT aliases the actual.",
            "Dynamic ST OUTPUT aliases an index register set before the body; another dynamic access in the body can redirect its target.",
            "Four complete FB PCode outputs were saved before native execution and matched; eleven earlier fits are post-observation.",
            "Both tested ST input writes are rejected before checking; this does not generalize Simple/FBD input behavior."],
        limits=[
            "Native compile/check/export/reopen is not physical PLC execution or proof of intended source semantics.",
            "Most new controls share one Q03UDV source lineage; sample variants are not independent real-project coverage.",
            "IF/STRING ST calls, nested FB calls, general arrays and arbitrary generation remain outside the bounded call predictor.",
            "Earlier original failures remain intact; the v2 index observer terminated prematurely without a captured exception, not a proven AccessViolation.",
            "REAL text display is not binary roundtrip equivalence; the four rounding counterexamples remain."])
    save(OUT / "claims.json", claims)
    add(public, OUT / "claims.json")
    (OUT / "worktree-status.txt").write_bytes(subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT))
    (OUT / "research-working-diff.patch").write_bytes(subprocess.check_output(["git", "diff", "--", "research", "docs/reports"], cwd=ROOT))
    for name in ("worktree-status.txt", "research-working-diff.patch"):
        add(public, OUT / name)
    for name in public:
        local.pop(name, None)

    def archive(files, path, visibility):
        rows = []
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name, file in sorted(files.items()):
                raw = file.read_bytes()
                rows.append(dict(path=name, bytes=len(raw), sha256=digest(raw)))
                z.writestr(name, raw)
            z.writestr("checkpoint-files.json", json.dumps(dict(visibility=visibility, files=rows), indent=2))
        return dict(path=rel(path), bytes=path.stat().st_size, sha256=digest(path.read_bytes()), visibility=visibility, files=rows)

    archives = [archive(public, PUBLIC, "repository-source-snapshot"),
                archive(local, LOCAL, "local-only; third-party redistribution rights not established")]
    environment = []
    for item in prior["environment"]:
        path = Path(item["path"])
        environment.append(dict(path=str(path), bytes=path.stat().st_size, sha256=digest(path.read_bytes())))
    manifest = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        python=platform.python_version(), platform=platform.platform(), environment=environment,
        prior_checkpoint=dict(path=rel(PREVIOUS), sha256=digest(PREVIOUS.read_bytes())),
        selection=dict(cutoff_utc=prior["created_utc"], whole_experiment_directories=selected,
            exclusions=sorted(EXCLUDED), policy="Whole changed directories including startup, prediction, instrumentation and compiler failures; unchanged dependencies may require prior checkpoint."),
        archives=archives, comparisons=comparisons, claims=claims,
        limits=["Archival verification does not rerun native code or establish semantic equivalence.",
                "Local archive and prior checkpoint are required for complete recovery. No commit, push or release."])
    save(OUT / "manifest.json", manifest)
    print(json.dumps(dict(archives=[{k:v for k,v in a.items() if k != "files"} | dict(files=len(a["files"])) for a in archives],
                         byte_pairs=len(comparisons), experiment_directories=len(selected))), flush=True)


if __name__ == "__main__":
    main()
