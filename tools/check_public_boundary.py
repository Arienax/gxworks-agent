"""Check the exact public source manifest and reject accidental private data."""
from __future__ import annotations

import argparse
import io
import json
import lzma
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "publication/public-files.json"
REQUIRED = {
    "LICENSE", "LICENSE-SCOPE.md", "README.md", "README.zh-CN.md",
    "README.ja.md", "src/gxw/project_templates.py",
    "src/gxw/templates/blank-fbd-projects.zip.xz",
    "src/gxw/templates/fx3u.gxw", "src/gxw/templates/nodes.json",
    "src/gxw/templates/provenance.json",
    "src/gxw/templates/q02-token-grammar.json",
    "src/gxw/templates/q03udv-token-grammar.json",
    "resources/instructions/mitsubishi/common.json",
    "resources/instructions/mitsubishi/fx3u.json",
    "resources/instructions/mitsubishi/fx5u.json",
    "resources/instructions/mitsubishi/fx3u_step_widths.json",
    "resources/instructions/mitsubishi/fx3u_verified_opcodes.json",
    "resources/instructions/mitsubishi/modifier_rules.json",
    "resources/instructions/mitsubishi/instruction_capabilities.json",
    "checks/source_smoke.py",
}
BINARY = {
    "resources/app.ico", "src/gxw/templates/blank-fbd-projects.zip.xz",
    "src/gxw/templates/fx3u.gxw",
}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "API token": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    "local user path": re.compile(r"(?i)[a-z]:[\\/]+Users[\\/]+[^\s\"<>]+"),
    "private evidence path": re.compile(r"(?i)research[/\\]evidence[/\\]"),
}
MAX_SIZE = 2_000_000


def names_in_index() -> set[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                            stdout=subprocess.PIPE).stdout
    return {item.decode("utf-8") for item in result.split(b"\0") if item}


def names_on_disk() -> set[str]:
    ignored = {".git", ".venv", ".venv-mcp", "node_modules", "dist",
               "__pycache__", ".pytest_cache", "build"}
    return {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*")
            if path.is_file() and path.suffix not in {".log", ".tsbuildinfo", ".pyc"}
            and not any(part in ignored or part.startswith(".sfc-check-")
                        for part in path.relative_to(ROOT).parts)}


def check_archive(data: bytes) -> list[str]:
    problems = []
    try:
        with ZipFile(io.BytesIO(lzma.decompress(data))) as archive:
            names = archive.namelist()
            projects = [name for name in names if name.startswith("projects/") and name.endswith(".gxw")]
            if len(projects) != 57 or set(names) != set(projects) | {"manifest.json"}:
                problems.append("blank template archive must contain 57 projects and one manifest")
            if any(".." in Path(name).parts or name.startswith("/") for name in names):
                problems.append("template archive contains an unsafe path")
            manifest = json.loads(archive.read("manifest.json"))
            listed = {row["file"] for row in manifest["models"]}
            if listed != set(projects) or len(manifest["models"]) != 57:
                problems.append("template manifest does not match archived projects")
            for name in projects:
                raw = archive.read(name)
                if b"Arienax" in raw or b"research/evidence" in raw:
                    problems.append(f"{name}: local or research marker")
    except (OSError, ValueError, KeyError, lzma.LZMAError) as exc:
        problems.append(f"template archive cannot be read: {type(exc).__name__}")
    return problems


def check_file(name: str) -> list[str]:
    path = ROOT / name
    if not path.is_file() or path.is_symlink():
        return [f"{name}: missing, linked or not a file"]
    data = path.read_bytes()
    problems = []
    if len(data) > MAX_SIZE:
        problems.append(f"{name}: exceeds size limit")
    if name == "src/gxw/templates/blank-fbd-projects.zip.xz":
        problems.extend(check_archive(data))
    if name in BINARY or name.startswith("resources/assets/"):
        if b"Arienax" in data or b"research/evidence" in data:
            problems.append(f"{name}: local or research marker")
        return problems
    if b"\0" in data or data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        return problems + [f"{name}: unexpected binary or LFS pointer"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return problems + [f"{name}: not UTF-8"]
    for label, pattern in PATTERNS.items():
        matches = list(pattern.finditer(text))
        if label == "API token":
            matches = [match for match in matches
                       if match.group() != "sk-offline-package-self-test"]
        if matches:
            problems.append(f"{name}: possible {label} (value withheld)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk", action="store_true", help="inspect snapshot before Git staging")
    args = parser.parse_args()
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = data.get("files")
    if data.get("schema_version") != 2 or not isinstance(expected, list):
        raise SystemExit("invalid public manifest")
    expected_set = set(expected)
    if len(expected) != len(expected_set) or not REQUIRED <= expected_set:
        raise SystemExit("manifest is duplicated or missing required source/templates")
    actual = names_on_disk() if args.disk else names_in_index()
    problems = [f"unlisted: {name}" for name in sorted(actual - expected_set)]
    problems += [f"missing: {name}" for name in sorted(expected_set - actual)]
    for name in sorted(expected_set & actual):
        problems.extend(check_file(name))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"Public boundary OK: {len(expected)} files, including native templates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
