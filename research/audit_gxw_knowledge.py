"""Inventory existing GXW knowledge without changing evidence or vendor projects."""
from __future__ import annotations

import ast
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gxw.container import CompoundFile
from gxw.project_metadata import logical_mapping
from gxw.structured_pou import parse_structured_pou


def digest(raw):
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify_evidence_manifests():
    """Verify all nested legacy/current manifests, including separate screenshots."""
    reports = []
    for path in sorted((ROOT / "research/results").rglob("*manifest.json")):
        raw = path.read_bytes()
        manifest = json.loads(raw)
        archive_ref = manifest.get("archive")
        if not archive_ref or "files" not in manifest:
            continue
        archive_name = archive_ref["path"] if isinstance(archive_ref, dict) else archive_ref
        archive_path = ROOT / archive_name
        if not archive_path.is_file():
            archive_path = path.parent / archive_name
        archive_hash = digest(archive_path.read_bytes())["sha256"]
        expected_hash = archive_ref.get("sha256") if isinstance(archive_ref, dict) else manifest.get("archive_sha256")
        if expected_hash and archive_hash != expected_hash:
            raise ValueError(f"archive hash mismatch: {path}")
        files = manifest["files"]
        members = files.items() if isinstance(files, dict) else ((f["member"], f) for f in files)
        count = screenshots = 0
        with zipfile.ZipFile(archive_path) as archive:
            for member, expected in members:
                content = archive.read(member)
                if digest(content)["sha256"] != expected["sha256"]:
                    raise ValueError(f"evidence member hash mismatch: {member}")
                expected_size = expected.get("size", expected.get("length"))
                if expected_size is not None and len(content) != expected_size:
                    raise ValueError(f"evidence member length mismatch: {member}")
                count += 1
        for name, expected in manifest.get("screenshots", {}).items():
            screenshot = ROOT / "research/evidence" / Path(name).name
            if digest(screenshot.read_bytes())["sha256"] != expected["sha256"]:
                raise ValueError(f"screenshot hash mismatch: {name}")
            screenshots += 1
        reports.append({"manifest": path.relative_to(ROOT).as_posix(), "manifest_sha256": digest(raw)["sha256"],
                        "archive": archive_path.resolve().relative_to(ROOT).as_posix(), "archive_sha256": archive_hash,
                        "verified_members": count, "verified_external_screenshots": screenshots,
                        "artifact_errata": manifest.get("artifact_errata")})
    return reports


def audit():
    paths = sorted(set([*ROOT.glob("docs/research/gxw_*.md"), ROOT / "research/README.md",
                        *ROOT.glob("research/evidence/*"), *ROOT.glob("research/results/**/*.json"),
                        *ROOT.glob("research/*.py"), *ROOT.glob("research/catalogs/*"),
                        *ROOT.glob("tools/gxw_*.py"), *ROOT.glob("tests/fixtures/gxw_*.json"),
                        *ROOT.glob("research/models/*"), *ROOT.glob("src/gxw/**/*.py"),
                        *ROOT.glob("src/gxw/templates/*"), *ROOT.glob("tests/test_gxw_*.py")]))
    entries, archives = {}, {}
    for path in paths:
        if not path.is_file():
            continue
        raw, name = path.read_bytes(), path.relative_to(ROOT).as_posix()
        entry = digest(raw)
        if path.suffix == ".py":
            tree = ast.parse(raw.decode("utf-8-sig"))
            entry["definitions"] = [{"name": n.name, "line": n.lineno, "end_line": n.end_lineno}
                                    for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        if path.suffix == ".json":
            value = json.loads(raw)
            entry["keys"] = list(value) if isinstance(value, dict) else None
            observations = []
            def walk(obj, location=""):
                if isinstance(obj, dict):
                    for key, child in obj.items():
                        pos = location + "/" + key
                        if key in {"native_compile", "gxworks_validation", "validation", "checks",
                                   "gxworks_compile", "gxworks_save_reload", "warning_codes"}:
                            observations.append({"path": pos, "value": child})
                        walk(child, pos)
                elif isinstance(obj, list):
                    for i, child in enumerate(obj):
                        walk(child, location + "/" + str(i))
            walk(value)
            entry["observations"] = observations
        if path.suffix == ".zip":
            members = {}
            with zipfile.ZipFile(path) as z:
                assert z.testzip() is None
                for member in z.namelist():
                    if member.endswith("/"):
                        continue
                    content = z.read(member)
                    item = digest(content)
                    if member.endswith(".gxw"):
                        outer = CompoundFile(content)
                        nested = CompoundFile(outer.read_stream("_hdb"))
                        mapping = logical_mapping(outer.read_stream("projectdatalist.xml"))
                        item["outer_streams"] = {e.name: digest(outer.read_entry(e)) for e in outer.iter_streams()}
                        names = {sid: logical for logical, sid in mapping.items()}
                        item["nested_streams"] = {e.name: {"logical_name": names.get(e.name), **digest(nested.read_entry(e))}
                                                   for e in nested.iter_streams()}
                        item["programs"] = {}
                        for logical, sid in mapping.items():
                            if logical.endswith(".Program.pou"):
                                try:
                                    p = parse_structured_pou(nested.read_stream(sid), logical_name=logical)
                                    item["programs"][logical] = {"nodes": len(p.nodes), "wires": len(p.wires),
                                        "record_count": p.record_count, "canvas_height": p.canvas_height,
                                        "unknown_records": len(p.unknown_records), "parser": "accepted"}
                                except ValueError as exc:
                                    item["programs"][logical] = {"parser": "rejected", "reason": str(exc)}
                    members[member] = item
            archives[name] = members
        entries[name] = entry
    # Existing manifests are the immutable reference, not freshly generated claims.
    evidence = verify_evidence_manifests()
    return {"schema_version": 1, "scope": "existing knowledge inventory; parser checks are not native validation",
            "files": entries, "archives": archives, "evidence_verification": evidence}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence-only", action="store_true")
    args = parser.parse_args()
    result = {"evidence_verification": verify_evidence_manifests()} if args.evidence_only else audit()
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({"files": len(result.get("files", {})), "verified_manifests": len(result["evidence_verification"]),
                      "manifest_hashes": "passed"}, ensure_ascii=False))
