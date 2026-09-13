"""Inventory existing GXW knowledge without changing evidence or vendor projects."""
from __future__ import annotations

import ast
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


def audit():
    paths = sorted(set([*ROOT.glob("docs/research/gxw_*.md"), ROOT / "research/README.md",
                        *ROOT.glob("research/evidence/*"), *ROOT.glob("research/results/*.json"),
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
    # Existing manifests are the immutable reference, not a freshly generated hash claim.
    for manifest_name in ("evidence_manifest.json", "generation-evidence-manifest.json"):
        manifest = json.loads((ROOT / "research/results" / manifest_name).read_bytes())
        archive_path = ROOT / manifest["archive"]
        if not archive_path.is_file():
            archive_path = ROOT / "research/results" / manifest["archive"]
        archive_name = archive_path.resolve().relative_to(ROOT).as_posix()
        if "archive_sha256" in manifest:
            assert digest(archive_path.read_bytes())["sha256"] == manifest["archive_sha256"]
        for member, expected in manifest["files"].items():
            assert archives[archive_name][member]["sha256"] == expected["sha256"], member
        for screenshot, expected in manifest.get("screenshots", {}).items():
            actual = ROOT / "research/evidence" / Path(screenshot).name
            assert digest(actual.read_bytes())["sha256"] == expected["sha256"]
    return {"schema_version": 1, "scope": "existing knowledge inventory; parser checks are not native validation",
            "files": entries, "archives": archives}


if __name__ == "__main__":
    result = audit()
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({"files": len(result["files"]), "archives": {k: len(v) for k, v in result["archives"].items()},
                      "manifest_hashes": "passed"}, ensure_ascii=False))
