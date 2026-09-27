"""Freeze the 2026-09-27 source-only and SFC research checkpoint once.

Archive existing observations; do not execute native tools or change inputs.
Third-party projects and derived evidence remain in a local-only archive.
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
B = ROOT / "research/experiments/sfc-graph-20260926"
P = B / "public-corpus-discovery"
OUT = ROOT / "research/results/gxw-source-sfc-20260927"
PREVIOUS = ROOT / "research/results/gxw-source-allocation-20260927/manifest.json"
EXCLUDED = {".exe", ".dll", ".pdb", ".pyc", ".zip"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    OUT.mkdir(exist_ok=False)
    public, local = {}, {}

    def add(target, path):
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() in EXCLUDED or "__pycache__" in path.parts:
            return
        target[path.relative_to(ROOT).as_posix()] = path

    def tree(target, path):
        if not path.is_dir():
            raise FileNotFoundError(path)
        for file in path.rglob("*"):
            if file.is_file():
                add(target, file)

    # Date selects whole experiment directories, not individual successful files.
    # The exact resulting selection is frozen below and in every member inventory.
    cutoff = datetime.fromisoformat(read(PREVIOUS)["created_utc"]).timestamp()
    selected = []
    for directory in sorted(P.iterdir()):
        if directory.is_dir() and directory.name != "__pycache__" and any(
            f.is_file() and f.suffix.lower() not in EXCLUDED and f.stat().st_mtime >= cutoff
            for f in directory.rglob("*")
        ):
            selected.append(directory)
    for name in ["noeul-native-batch", "samples", "q-product-public-corpus-scan"]:
        selected.append(P / name)
    selected += [d for d in P.glob("q-task-*") if d.is_dir()]
    for directory in sorted(set(selected)):
        tree(local, directory)
    for file in P.iterdir():
        if file.is_file():
            add(public if file.suffix in {".py", ".js", ".cs"} else local, file)

    # Freeze source and relevant tests before further reverse engineering edits.
    for directory, patterns in [
        (ROOT / "src/gxw", ["**/*.py", "**/*.json"]),
        (ROOT / "research", ["*.py", "native/*.cs", "native/*.js"]),
        (ROOT / "tests", ["test_gxw*.py", "fixtures/gxw*.json"]),
    ]:
        for pattern in patterns:
            for file in directory.glob(pattern):
                add(public, file)
    for name in ["AGENTS.md", "docs/reports/gxw/2026-09-27-source-sfc-checkpoint.md"]:
        add(public, ROOT / name)

    # Include original buffers, events and input files used by the SFC comparisons.
    referenced = set()
    for row in read(P / "sfc-grid-cell-crosscheck/comparison.json"):
        referenced.add(Path(row["path"]).parent)
    for row in read(P / "sfc-grid-path-crosscheck/comparison.json"):
        referenced.add(Path(row["native_directory"]))
    for row in read(P / "existing-sfc-projections/candidates-v3/comparison.json"):
        for path in row["paths"]:
            add(local, Path(path))
        for native in row.get("native", []):
            referenced.add(Path(native["path"]).parent)
    for directory in referenced:
        if not directory.resolve().is_relative_to(B):
            raise ValueError(f"Unexpected native dependency: {directory}")
        tree(local, directory)

    comparisons = []

    def pair(case, candidate, native, source, **extra):
        a, b = candidate.read_bytes(), native.read_bytes()
        if a != b:
            raise ValueError(f"Saved byte comparison differs: {case}")
        for path in [candidate, native, source]:
            add(local, path)
        comparisons.append(dict(case=case, candidate=candidate.relative_to(ROOT).as_posix(),
            native=native.relative_to(ROOT).as_posix(), source=source.relative_to(ROOT).as_posix(),
            source_sha256=digest(source.read_bytes()), predicted_sha256=digest(a),
            native_sha256=digest(b), bytes=len(a), **extra))

    qrows = read(P / "q-real-corpus-portable-v4/comparison.json")
    for row in qrows:
        if row.get("byte_identical"):
            source = Path(row["source"])
            index = int(row["case"].rsplit("-", 1)[1])
            pair("q-real/" + row["case"], P / "q-real-corpus-portable-v4" / row["case"] / "predicted-pcode.bin",
                source.parent / f"pcode-{index}-0.bin", source, program=row["root_name"])
    for row in read(P / "existing-sfc-projections/candidates-v3/comparison.json"):
        for native in row.get("native", []):
            path = Path(native["path"])
            pair("sfc-existing/" + path.parent.relative_to(B).as_posix(),
                P / "existing-sfc-projections/candidates-v3" / row["candidate"], path,
                path.parent / "native-saved.gxw", full_program_check_claimed=False)
    for group in ["sfc-source-targeted-mutations", "pending-q-end-kind", "pending-sfc-reload"]:
        for row in read(P / group / "comparison.json"):
            if not row["full_pcode_identical"]:
                continue
            directory = P / group / row["case"] if "case" in row else P / group
            native = Path(row["native_directory"])
            outcome = row["outcome"]
            if not outcome["program_check_completed"] or outcome["compiler_rejected"]:
                raise ValueError(f"Unexpected full program check result: {native}")
            pair(group + "/" + row.get("case", row.get("phase")), directory / "candidate.bin",
                native / "pcode-0-0.bin", native / "input.gxw", full_program_check_claimed=True)

    cells = read(P / "sfc-grid-cell-crosscheck/comparison.json")
    paths = read(P / "sfc-grid-path-crosscheck/comparison.json")
    if not all(r["symbols_agree"] and r["dimensions_agree"] for r in cells):
        raise ValueError("Saved cell comparison has differences")
    if not all(r["edges_agree"] and r["gaps_agree"] for r in paths):
        raise ValueError("Saved path comparison has differences")
    claims = dict(q_real_programs_exact=sum(bool(r.get("byte_identical")) for r in qrows),
        q_real_programs_recorded=len(qrows), q_native_byte_pairs=sum(r["case"].startswith("q-real/") for r in comparisons),
        sfc_grid_snapshots=len(cells), sfc_effective_symbols=sum(r["native_symbols"] for r in cells),
        sfc_path_controls=len(paths), sfc_path_edges=sum(len(r["graph"]["edges"]) for r in paths),
        saved_byte_comparisons=len(comparisons),
        product_q_corpus=read(P / "q-product-public-corpus-scan/scan.json")["summary"],
        q_width_application=read(P / "q-width-all-application-holdouts/comparison.json")["counts"],
        q_width_boundaries=read(P / "q-width-numeric-boundaries/comparison.json")["counts"])
    (OUT / "claims.json").write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
    add(public, OUT / "claims.json")
    status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT)
    (OUT / "worktree-status.txt").write_bytes(status)
    add(public, OUT / "worktree-status.txt")
    for path in public:
        local.pop(path, None)

    def archive(files, path, visibility):
        rows = []
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name, file in sorted(files.items()):
                raw = file.read_bytes()
                rows.append(dict(path=name, bytes=len(raw), sha256=digest(raw)))
                z.writestr(name, raw)
            z.writestr("checkpoint-files.json", json.dumps(dict(visibility=visibility, files=rows), indent=2))
        return dict(path=path.relative_to(ROOT).as_posix(), bytes=path.stat().st_size,
            sha256=digest(path.read_bytes()), visibility=visibility, files=rows)

    archives = [archive(public, ROOT / "research/evidence/gxw-source-sfc-20260927.zip", "repository-source-snapshot"),
        archive(local, P / "gxw-source-sfc-local-20260927.zip", "local-only; third-party redistribution rights not established")]
    environment = [dict(path=str(path), bytes=path.stat().st_size, sha256=digest(path.read_bytes())) for path in [
        Path("D:/GXWORKS2/Easysocket/Compiler/ECCompiler_IEC.dll"),
        Path("D:/GXWORKS2/Easysocket/CodeGenerator/ECCodeGenerator2.dll"),
        Path("D:/GXWORKS2/Easysocket/CodeGenerator/ECCodeGeneratorFX2.dll")]]
    manifest = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        python=platform.python_version(), platform=platform.platform(), environment=environment,
        prior_checkpoint=dict(path=PREVIOUS.relative_to(ROOT).as_posix(), sha256=digest(PREVIOUS.read_bytes())),
        selection=dict(cutoff_utc=read(PREVIOUS)["created_utc"], whole_experiment_directories=[d.relative_to(ROOT).as_posix() for d in sorted(set(selected))]),
        archives=archives, comparisons=comparisons, claims=claims,
        negative_evidence=["Two original Q programs with unresolved declarations remain failures in portable-v4.",
            "Eight special Q application dispatcher cases remain unsupported in the frozen width comparison.",
            "Real FX SFC input and variants reject full ProgramCheck despite exact converted PCode.",
            "Empty SFC task can return stale or deliberately poisoned resource PCode; not proof of recompilation.",
            "Initial source-grid extent assumptions and inactive native symbol field comparisons failed; originals preserved.",
            "Q source cell kind 15 with target 999 rejects; kind 14 is the validated end control."],
        limits=["Archive verification checks preserved bytes, not fresh native execution.",
            "Experimental source models are not a general compiler or production-safe writer.",
            "Overlapping cases and mutations are not independent real projects.",
            "Opaque bytes and rejected inputs are retained; missing local archive is not-checkable.",
            "No PLC/simulator execution, GUI reopen, Git commit, push or release performed for this checkpoint."])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(archives=[{k: v for k, v in a.items() if k != "files"} | dict(files=len(a["files"])) for a in archives], claims=claims)), flush=True)


if __name__ == "__main__":
    main()
