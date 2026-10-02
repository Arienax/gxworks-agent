"""Freeze existing GXW observations before further reverse engineering.

No native process is started. Never overwrite a checkpoint. Project copies and
derived third-party evidence stay in the ignored local archive, as in the prior
source/SFC checkpoint. The existing archive verifier can check this manifest.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "research/experiments/sfc-graph-20260926/public-corpus-discovery"
OUT = ROOT / "research/results/gxw-check-allocation-20260930"
PREVIOUS = ROOT / "research/results/gxw-source-sfc-20260927/manifest.json"
PUBLIC_ARCHIVE = ROOT / "research/evidence/gxw-check-allocation-20260930.zip"
LOCAL_ARCHIVE = P / "gxw-check-allocation-local-20260930.zip"
EXCLUDED = {".exe", ".dll", ".pdb", ".pyc", ".zip"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def pool_observations():
    """Summarize saved native events without treating rejected source as valid."""
    cases = []
    for group in ("extended-allocation-pools-20260930", "retain-pool-selection-20260930"):
        comparison = P / group / "comparison.json"
        for row in read(comparison):
            native = Path(row["native_directory"])
            before = read(native.parent / "prediction-before-native.json")
            cases.append(dict(
                case=group + "/" + row["case"],
                evidence=dict(path=relative(comparison), sha256=digest(comparison.read_bytes())),
                native_directory=relative(native), input_sha256=digest((native / "input.gxw").read_bytes()),
                source_class=before.get("source_class"), changed_pools=before.get("changed_pools", before.get("changes")),
                trace=row["trace"], trace_errors=row["trace_errors"], check=row["check"],
                diagnostics=row["diagnostics"], generated=row["generated"],
                initial_word_pools=[{k: d[k] for k in ("index", "code", "end", "limit")}
                    for d in row["pools"][0]["descriptors"] if 24 <= d["index"] <= 30],
                allocations=[{k: a.get(k) for k in ("compile_id", "allocation_id", "type", "category", "result", "after")}
                    for a in row["typed"]]))
    return dict(
        scope="Saved Q03UDV native pool observations in one real project; no new execution during freezing.",
        evidence_level="observed", handling="partially-decoded; source-model support remains unsupported",
        findings=[
            "Ordinary word selector 4 searches descriptors 24..27; isolated R and ZR pools allocate descending addresses.",
            "With R and ZR configured together, the measured constructor leaves descriptor 27 empty; R suppresses ZR in this control.",
            "Without R, D then W then ZR are used. Configuring all four banks is not a four-bank fallback in this project.",
            "Latch D1 or D2 initializes descriptor 28; when both exist, D1 is selected. W1/W2 independently initialize descriptor 29.",
            "Source class 6 survives the frontend snapshot but SICConverter rejects it with 0x500c5046. No retain variable allocation was established.",
            "Allocator categories 6/13 select retain pools in static code; their source representation is still unresolved."],
        cases=cases,
        limits=[
            "R-only, ZR-only and D/W/ZR compile but fresh checks retain the baseline duplicate-Y21 errors; these are not accepted complete projects.",
            "Source class, compiler category and raw record fields are separate namespaces.",
            "Other PLC families, retain variables, pool-constructor cause, general temporaries and arbitrary FB locals remain unverified."])


def main():
    for destination in (OUT, PUBLIC_ARCHIVE, LOCAL_ARCHIVE):
        if destination.exists():
            raise FileExistsError(destination)
    OUT.mkdir()
    public, local = {}, {}

    def add(target, path):
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() in EXCLUDED or "__pycache__" in path.parts:
            return
        target[relative(path)] = path

    def tree(target, directory):
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        for path in directory.rglob("*"):
            if path.is_file():
                add(target, path)

    # Include complete changed experiment directories, including failed attempts.
    # Freeze the exact selection; mtime is only a discovery aid, not a claim.
    prior = read(PREVIOUS)
    cutoff = datetime.fromisoformat(prior["created_utc"]).timestamp()
    selected = set()
    for directory in P.iterdir():
        if directory.is_dir() and directory.name != "__pycache__" and any(
            f.is_file() and f.suffix.lower() not in EXCLUDED and "__pycache__" not in f.parts
            and f.stat().st_mtime >= cutoff for f in directory.rglob("*")
        ):
            selected.add(directory)
    # Older dependencies of the fresh-check selection and current allocator trace.
    selected.update(P / name for name in (
        "native-allocation-bitmap-v2", "sfc-source-targeted-mutations",
        "q-repeated-materialization-lifetimes", "noeul-native-batch/case-08"))
    for directory in sorted(selected):
        tree(local, directory)
    add(local, P / "samples/project2.gxw")
    for file in P.iterdir():
        if file.is_file():
            add(public if file.suffix in {".py", ".js", ".cs"} else local, file)

    # Preserve current experimental dependencies alongside Core and native helpers.
    for directory, patterns in (
        (ROOT / "src/gxw", ("**/*.py", "**/*.json")),
        (ROOT / "research", ("*.py", "native/*.cs", "native/*.js")),
        (ROOT / "tests", ("test_gxw*.py", "fixtures/gxw*.json")),
    ):
        for pattern in patterns:
            for file in directory.glob(pattern):
                add(public, file)
    for file in (ROOT / "research/results").rglob("*"):
        if file.is_file() and OUT not in file.parents and file.stat().st_mtime >= cutoff:
            add(public, file)
    for name in ("AGENTS.md", "research/README.md", "docs/reports/README.md",
                 "docs/reports/gxw/2026-09-30-check-allocation-checkpoint.md"):
        add(public, ROOT / name)

    comparisons = []

    def pair(case, candidate, native, source, **extra):
        a, b = candidate.read_bytes(), native.read_bytes()
        if a != b:
            raise ValueError(f"Saved byte comparison differs: {case}")
        for path in (candidate, native, source):
            add(local, path)
        comparisons.append(dict(case=case, candidate=relative(candidate), native=relative(native),
            source=relative(source), source_sha256=digest(source.read_bytes()),
            predicted_sha256=digest(a), native_sha256=digest(b), bytes=len(a), **extra))

    matrix = read(P / "fresh-check-matrix-20260930/matrix-v2.json")["cases"]
    for case in matrix:
        previous = None
        for phase in case["phases"]:
            directory = Path(phase["native_directory"])
            source = directory / "input.gxw"
            correspondence = read(directory / "resource-correspondence.json")
            generated = {g["name"]: g for g in correspondence["generated"]}
            if "legacy" not in case["case"]:
                starts = {s["check_index"]: s["target"] for s in correspondence["check_starts"]}
                for row in correspondence["resource_reads"]:
                    if row["phase"] != "check" or starts.get(row["check_index"]) != row["target"]:
                        continue
                    for channel, buffer in enumerate(row["buffers"]):
                        if not buffer["sha256"] or not buffer["size"]:
                            continue
                        index = generated[row["resource_name"]]["index"]
                        pair(f'{case["case"]}/{phase["phase"]}/read-{row["serial"]}-{channel}',
                            directory / f'checked-read-{row["serial"]}-{channel}.bin',
                            directory / f"pcode-{index}-{channel}.bin", source,
                            candidate_kind="actual-checker-read", full_check_accepted=not phase["check"]["rejected"])
            prediction = phase.get("source_prediction")
            if prediction:
                pair(f'{case["case"]}/{phase["phase"]}/prediction', Path(prediction["path"]),
                    directory / "pcode-0-0.bin", source, candidate_kind="source-model-prediction",
                    full_check_accepted=not phase["check"]["rejected"])
            if previous is not None:
                for row in phase["current_code"]:
                    for channel, buffer in enumerate(row["channels"]):
                        if buffer["size"]:
                            filename = f'pcode-{row["index"]}-{channel}.bin'
                            pair(f'{case["case"]}/reopen/{filename}', previous / filename,
                                directory / filename, source, candidate_kind="pre-reopen-code",
                                full_check_accepted=not phase["check"]["rejected"])
            previous = directory

    pools = pool_observations()
    save(OUT / "pool-observations.json", pools)
    add(public, OUT / "pool-observations.json")
    claims = dict(fresh_check=read(ROOT / "research/results/program-check-matrix-20260930.json")["summary"],
        source_allocation=read(ROOT / "research/results/source-allocation-boundaries-20260930.json")["validation"],
        additional_pool_cases=len(pools["cases"]), saved_byte_pairs=len(comparisons))
    save(OUT / "claims.json", claims)
    add(public, OUT / "claims.json")
    (OUT / "worktree-status.txt").write_bytes(subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT))
    add(public, OUT / "worktree-status.txt")
    (OUT / "research-working-diff.patch").write_bytes(subprocess.check_output(
        ["git", "diff", "--", "research", "docs/reports"], cwd=ROOT))
    add(public, OUT / "research-working-diff.patch")
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
        return dict(path=relative(path), bytes=path.stat().st_size, sha256=digest(path.read_bytes()),
            visibility=visibility, files=rows)

    archives = [archive(public, PUBLIC_ARCHIVE, "repository-source-snapshot"),
        archive(local, LOCAL_ARCHIVE, "local-only; third-party redistribution rights not established")]
    environment = [dict(path=str(path), bytes=path.stat().st_size, sha256=digest(path.read_bytes())) for path in (
        Path("D:/GXWORKS2/Easysocket/Compiler/ECCompiler_IEC.dll"),
        Path("D:/GXWORKS2/Easysocket/CodeGenerator/ECCodeGenerator2.dll"),
        Path("D:/GXWORKS2/Easysocket/CodeGenerator/ECCodeGeneratorFX2.dll"),
        Path("D:/GXWORKS2/DNaviZero/DataAbsorber/DZDataABS_SICConverter_IEC.dll"))]
    manifest = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        python=platform.python_version(), platform=platform.platform(), environment=environment,
        prior_checkpoint=dict(path=relative(PREVIOUS), sha256=digest(PREVIOUS.read_bytes())),
        selection=dict(cutoff_utc=prior["created_utc"], whole_experiment_directories=[relative(d) for d in sorted(selected)],
            exclusions=sorted(EXCLUDED), policy="Whole selected directories, including rejection, crash and startup failure evidence."),
        archives=archives, comparisons=comparisons, claims=claims,
        negative_evidence=[
            "Legacy ProgramCheck falsely accepts stale code and exports different unchecked bytes.",
            "Repeated-call source predictions match current PCode, but fresh ProgramCheck rejects both selected cases.",
            "Global DINT starting below the automatic range still overlaps and is rejected; local address fields are ignored by the frontend.",
            "Configuring all D/W/R/ZR pools exhausts D/W/R because the observed ZR descriptor remains empty.",
            "Source class 6 is rejected before retain allocation; retain metadata observations do not establish a retain compiler model.",
            "Both ladder-to-PCode prototype attempts crash with AccessViolation; conversion direction remains unresolved.",
            "Initial restricted fresh-check attempt exited before native temporary-directory creation, with no compiler result."],
        limits=[
            "Archive integrity and saved byte equality are not new native execution or semantic verification.",
            "No PLC/simulator access, GUI reopen, commit, push or release is performed by this checkpoint.",
            "Ordinary R/ZR, retain-source representation, temporary lifetime and arbitrary FB local addresses are not promoted to Core.",
            "The nine pool cases share one project and are not independent real-project coverage.",
            "Current REAL display handling does not establish binary re-encoding equivalence.",
            "Missing local archive makes its saved-byte comparisons not-checkable; preserve it for complete recovery."])
    save(OUT / "manifest.json", manifest)
    print(json.dumps(dict(archives=[{k: v for k, v in a.items() if k != "files"} | dict(files=len(a["files"]))
        for a in archives], claims=claims)), flush=True)


if __name__ == "__main__":
    main()
