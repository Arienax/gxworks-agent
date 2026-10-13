"""Audit the complete Git index or every locally reachable commit before upload.

An explicit allowlist and text checks prevent common accidental disclosures.
They do not replace review of changes inside an already allowed text file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

POLICY = "publication/public-files.json"
MAX_BYTES = 2 * 1024 * 1024
README_ASSETS = {"resources/assets/demo.gif"}
ROOT_FILES = {
    "package.json", "package-lock.json", "tools/web-runtime.mjs", "checks/web-runtime.test.mjs",
    "README.md", "README.zh-CN.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md", "SECURITY.md", ".gitignore", ".gitattributes",
    ".githooks/pre-commit", ".githooks/pre-push",
    ".github/workflows/public-boundary.yml", "docs/private-component.md",
    "docs/publishing.md", POLICY, "tools/check_public_boundary.py",
    "checks/test_public_boundary.py", "web/index.html", "web/package.json",
    "web/package-lock.json", "web/openapi.json", "web/tsconfig.json", "web/vite.config.ts",
}
PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("API token", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("local user path", re.compile(r"(?i)[a-z]:[\\/]+Users[\\/]+(?!Public(?:[\\/]|$))[^\s\"<>]+")),
    ("native interface implementation", re.compile(r"Marshal\.(?:ReadIntPtr|GetDelegateForFunctionPointer)|\bSlot<[^>]+>\s*\(")),
)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if "\\" in name or ":" in name or path.is_absolute() or any(part in {"..", ".git", "node_modules", "dist"} for part in path.parts):
        return False
    if path.as_posix() != name:
        return False
    if name in ROOT_FILES or name in README_ASSETS:
        return True
    return ((name.startswith("web/src/") and path.suffix in {".ts", ".tsx", ".css"})
            or (name.startswith("web/tests/") and name.endswith(".test.mjs")))


def load_allowlist(data: bytes) -> set[str]:
    names = json.loads(data)["files"]
    if not isinstance(names, list) or any(not isinstance(name, str) or not allowed_path(name) for name in names):
        raise ValueError("Public allowlist contains an unsupported path")
    if len(names) != len(set(names)) or not ROOT_FILES.issubset(names):
        raise ValueError("Public allowlist is duplicated or missing required publication controls")
    return set(names)


def inspect_file(name: str, mode: str, data: bytes, allowed: set[str]) -> list[str]:
    problems = []
    if name not in allowed or not allowed_path(name):
        problems.append(f"{name}: path is not approved for public distribution")
    if mode not in {"100644", "100755"}:
        problems.append(f"{name}: symbolic links and submodules are forbidden")
    if len(data) > MAX_BYTES:
        return problems + [f"{name}: exceeds the public text-file size limit"]
    if name in README_ASSETS:
        if not valid_demo_gif(data):
            problems.append(f"{name}: invalid GIF or unsupported metadata")
        return problems
    if data.startswith((b"PK\x03\x04", b"MZ", b"\x7fELF", b"\x1f\x8b", b"version https://git-lfs.github.com/spec/v1")) or b"\x00" in data:
        return problems + [f"{name}: binary, archive or LFS payload is forbidden"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return problems + [f"{name}: only UTF-8 text may be published"]
    for label, pattern in PATTERNS:
        if pattern.search(text):
            problems.append(f"{name}: possible {label}; inspect locally (matched value withheld)")
    return problems


def valid_demo_gif(data: bytes) -> bool:
    """Accept a complete GIF stream with frame controls and optional looping only."""
    if len(data) < 14 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        return False
    if not int.from_bytes(data[6:8], "little") or not int.from_bytes(data[8:10], "little"):
        return False
    position, frames = 13, 0
    if data[10] & 0x80:
        position += 3 * (2 ** ((data[10] & 7) + 1))
    def blocks(start):
        while start < len(data):
            size = data[start]
            start += 1
            if size == 0:
                return start
            start += size
        return len(data) + 1
    while position < len(data):
        marker = data[position]
        position += 1
        if marker == 0x3B:
            return frames > 0 and position == len(data)
        if marker == 0x2C:
            if position + 9 >= len(data):
                return False
            flags = data[position + 8]
            position += 9
            if flags & 0x80:
                position += 3 * (2 ** ((flags & 7) + 1))
            if position >= len(data) or not 2 <= data[position] <= 8:
                return False
            position = blocks(position + 1)
            frames += 1
        elif marker == 0x21:
            if data[position:position + 2] == b"\xF9\x04":
                if data[position + 6:position + 7] != b"\x00":
                    return False
                position += 7
            elif data[position:position + 13] == b"\xFF\x0BNETSCAPE2.0":
                if data[position + 13:position + 15] != b"\x03\x01" or data[position + 17:position + 18] != b"\x00":
                    return False
                position += 18
            else:
                return False
        else:
            return False
    return False


def entries(repo: Path, revision: str | None):
    if revision is None:
        raw = git(repo, "ls-files", "--stage", "-z")
    else:
        raw = git(repo, "ls-tree", "-r", "-z", revision)
    for item in raw.split(b"\0"):
        if not item:
            continue
        meta, name = item.split(b"\t", 1)
        mode, second, third = meta.decode().split()
        if revision is None and third != "0":
            raise ValueError("Resolve index conflicts before checking publication")
        yield name.decode("utf-8"), mode, second if revision is None else third


def check(repo: Path, *, history: bool = False) -> list[str]:
    # The proposed policy comes from the index for pre-commit, HEAD for history.
    policy_ref = "HEAD:" + POLICY if history else ":" + POLICY
    allowed = load_allowlist(git(repo, "show", policy_ref))
    revisions = git(repo, "rev-list", "--all").decode().splitlines() if history else [None]
    if history and not revisions:
        raise ValueError("No committed history to inspect")
    seen, problems = set(), []
    for revision in revisions:
        for name, mode, oid in entries(repo, revision):
            identity = (name, mode, oid)
            if identity in seen:
                continue
            seen.add(identity)
            if mode not in {"100644", "100755"}:
                problems.append(f"{name}: non-regular Git entry is forbidden")
                continue
            size = int(git(repo, "cat-file", "-s", oid))
            if size > MAX_BYTES:
                problems.append(f"{name}: oversized Git object is forbidden")
                continue
            problems.extend(inspect_file(name, mode, git(repo, "cat-file", "blob", oid), allowed))
    return sorted(set(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--index", action="store_true")
    mode.add_argument("--history", action="store_true")
    args = parser.parse_args()
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    try:
        problems = check(repo, history=args.history)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Public boundary check could not complete: {type(error).__name__}", file=sys.stderr)
        return 2
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("Public boundary passed: " + ("all reachable commit trees" if args.history else "complete staged tree"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
