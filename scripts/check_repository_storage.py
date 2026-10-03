"""Check Git objects, not smudged working files, for accidental bulk storage.

Only newly introduced blobs are size-limited in a commit range. Every commit
is examined so an add-then-delete does not hide a large historical object.
Evidence archives at the final revision must also be LFS pointers. This tool
does not check remote LFS availability or rewrite, stage, or delete anything.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

MAX_BLOB_BYTES = 5 * 1024 * 1024
ARCHIVE_SUFFIXES = (".zip", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst")
LFS_POINTER = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\n"
    rb"oid sha256:[0-9a-f]{64}\nsize [0-9]+\n"
)


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def commit_id(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def requires_lfs(path: str) -> bool:
    return path.startswith("research/results/raw/") or (
        path.startswith("research/evidence/")
        and path.lower().endswith(ARCHIVE_SUFFIXES)
    )


def diff_entries(raw: bytes) -> set[tuple[str, str]]:
    fields = raw.split(b"\0")
    entries = set()
    for index in range(0, len(fields) - 1, 2):
        metadata, name = fields[index:index + 2]
        if not metadata:
            continue
        parts = metadata.split()
        if parts[1] == b"160000":  # Gitlinks are commits, not file blobs.
            continue
        oid = parts[3].decode("ascii")
        if oid.strip("0"):
            entries.add((oid, os.fsdecode(name)))
    return entries


def tree_entries(repo: Path, head: str, *paths: str) -> set[tuple[str, str]]:
    raw = git(repo, "ls-tree", "-r", "-z", "--full-tree", head, "--", *paths)
    entries = set()
    for item in raw.split(b"\0"):
        if item:
            metadata, name = item.split(b"\t", 1)
            _, kind, oid = metadata.split()
            if kind == b"blob":
                entries.add((oid.decode("ascii"), os.fsdecode(name)))
    return entries


def check(
    repo: Path, *, base: str | None = None, head: str = "HEAD",
    staged: bool = False, all_files: bool = False,
    max_bytes: int = MAX_BLOB_BYTES,
) -> list[str]:
    if sum((base is not None, staged, all_files)) != 1:
        raise ValueError("Choose exactly one of base, staged, or all_files")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if staged:
        entries = diff_entries(git(
            repo, "diff", "--cached", "--raw", "-z", "--no-abbrev",
            "--no-renames", "--diff-filter=AMT",
        ))
    else:
        head = commit_id(repo, head)
        if all_files:
            entries = tree_entries(repo, head)
        else:
            base = commit_id(repo, base or "HEAD")
            introduced = set(git(
                repo, "rev-list", "--objects", "--no-object-names", head, "^" + base,
            ).decode().splitlines())
            entries = set()
            for commit in git(repo, "rev-list", head, "^" + base).decode().splitlines():
                entries.update(
                    (oid, path) for oid, path in diff_entries(git(
                        repo, "diff-tree", "--root", "-r", "-m", "--no-commit-id",
                        "--raw", "-z", "--no-abbrev", "--no-renames",
                        "--diff-filter=AMT", commit,
                    )) if oid in introduced
                )
            # Legacy ordinary blobs may remain in history, but not as the
            # current representation of frozen archives after migration.
            entries.update(
                (oid, path) for oid, path in tree_entries(
                    repo, head, "research/evidence", "research/results/raw",
                ) if requires_lfs(path)
            )
    if not entries:
        return []
    oids = sorted({oid for oid, _ in entries})
    metadata = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                   data=("\n".join(oids) + "\n").encode("ascii"))
    sizes = {}
    for line in metadata.decode("ascii").splitlines():
        oid, kind, size = line.split()
        if kind != "blob":
            raise ValueError(f"Expected a Git blob, got {kind}: {oid}")
        sizes[oid] = int(size)
    issues = []
    pointer_cache = {}
    for oid, path in sorted(entries, key=lambda item: (item[1], item[0])):
        size = sizes[oid]
        if requires_lfs(path):
            if oid not in pointer_cache:
                pointer_cache[oid] = size <= 1024 and LFS_POINTER.fullmatch(git(repo, "cat-file", "blob", oid)) is not None
            if not pointer_cache[oid]:
                issues.append(f"{path!r}: frozen archive/raw result must be a Git LFS pointer (Git blob: {size} bytes).")
        elif size > max_bytes:
            issues.append(f"{path!r}: ordinary Git blob is {size} bytes; limit is {max_bytes}. Keep a summary/small witness and move bulk data to LFS or release storage.")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", help="Exclude objects already reachable from this commit")
    mode.add_argument("--staged", action="store_true", help="Check index additions/modifications")
    mode.add_argument("--all", dest="all_files", action="store_true", help="Check all files at --head")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(git(Path.cwd(), "rev-parse", "--show-toplevel")).strip())
        issues = check(root, base=args.base, head=args.head, staged=args.staged, all_files=args.all_files)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Storage check could not complete: {error}", file=sys.stderr)
        return 2
    if issues:
        print("Repository storage policy failed:", file=sys.stderr)
        for issue in issues:
            print("  " + issue, file=sys.stderr)
        print("See docs/guides/evidence-storage.md. Nothing was changed.", file=sys.stderr)
        return 1
    print("Repository storage policy passed (Git objects only; remote LFS upload is separate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
