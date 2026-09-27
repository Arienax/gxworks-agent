"""Verify frozen evidence hashes and compare saved candidate/native PCode bytes.

This checks archival integrity and recorded byte equality, not compiler semantics.
It does not import experiment code or load vendor DLLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def verify(manifest_path: Path, root: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    needed = {row[key] for row in manifest["comparisons"] for key in ("candidate", "native", "source")}
    content: dict[str, bytes] = {}
    checked = 0
    missing = []
    for item in manifest["archives"]:
        path = root / item["path"]
        if not path.exists():
            missing.append(item["path"])
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Archive digest mismatch: {path}")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = [row["path"] for row in item["files"]] + ["checkpoint-files.json"]
            if sorted(names) != sorted(expected) or len(names) != len(set(names)):
                raise ValueError(f"Archive inventory mismatch: {path}")
            internal = json.loads(archive.read("checkpoint-files.json"))
            if internal["files"] != item["files"]:
                raise ValueError(f"Embedded inventory mismatch: {path}")
            for row in item["files"]:
                raw = archive.read(row["path"])
                if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                    raise ValueError(f"Member digest mismatch: {row['path']}")
                checked += 1
                if row["path"] in needed:
                    previous = content.setdefault(row["path"], raw)
                    if previous != raw:
                        raise ValueError(f"Conflicting archive member: {row['path']}")
    comparisons = []
    for row in manifest["comparisons"]:
        if any(row[key] not in content for key in ("candidate", "native", "source")):
            comparisons.append({"case": row["case"], "status": "not-checkable", "reason": "archive unavailable"})
            continue
        candidate, native, source = (content[row[key]] for key in ("candidate", "native", "source"))
        if hashlib.sha256(source).hexdigest() != row["source_sha256"]:
            raise ValueError(f"Source digest mismatch: {row['case']}")
        if candidate != native or hashlib.sha256(candidate).hexdigest() != row["predicted_sha256"] or row["predicted_sha256"] != row["native_sha256"]:
            raise ValueError(f"PCode comparison mismatch: {row['case']}")
        comparisons.append({"case": row["case"], "status": "byte-identical", "bytes": len(candidate)})
    return {"files_verified": checked, "missing_archives": missing, "comparisons": comparisons,
            "scope": "Archive integrity and saved byte equality only; native compiler was not rerun."}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "research/results/gxw-source-allocation-20260927/manifest.json")
    args = parser.parse_args()
    result = verify(args.manifest, root)
    print(json.dumps(result, indent=2))
    if any(row["status"] != "byte-identical" for row in result["comparisons"]):
        raise SystemExit(2)
