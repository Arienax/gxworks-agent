"""Check Git objects, not smudged working files, for accidental bulk storage.

Only newly introduced blobs are size-limited in a commit range. Every commit
is examined so an add-then-delete does not hide a large historical object.
Evidence archives and bulk raw results are local only, even as LFS pointers.
The pre-push mode checks every outgoing ref before invoking the LFS uploader.
Checks do not rewrite, stage, or delete anything.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

MAX_BLOB_BYTES = 5 * 1024 * 1024
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst",
                    ".tgz", ".tbz", ".tbz2", ".txz")


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def commit_id(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def is_local_only_evidence(path: str) -> bool:
    path = path.casefold()
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


def index_entries(repo: Path) -> set[tuple[str, str]]:
    entries = set()
    for item in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if stage != b"0":
            raise ValueError("Resolve index conflicts before checking repository storage")
        if mode != b"160000":
            entries.add((oid.decode("ascii"), os.fsdecode(name)))
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
    repo: Path, *, base: str | tuple[str, ...] | None = None, head: str = "HEAD",
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
        entries.update((oid, path) for oid, path in index_entries(repo)
                       if is_local_only_evidence(path))
    else:
        head = commit_id(repo, head)
        if all_files:
            entries = tree_entries(repo, head)
        else:
            bases = (base,) if isinstance(base, str) else (base or ())
            excluded = ["^" + commit_id(repo, ref) for ref in bases]
            introduced = set(git(
                repo, "rev-list", "--objects", "--no-object-names", head, *excluded,
            ).decode().splitlines())
            entries = set()
            for commit in git(repo, "rev-list", head, *excluded).decode().splitlines():
                entries.update(
                    (oid, path) for oid, path in diff_entries(git(
                        repo, "diff-tree", "--root", "-r", "-m", "--no-commit-id",
                        "--raw", "-z", "--no-abbrev", "--no-renames",
                        "--diff-filter=AMT", commit,
                    )) if oid in introduced or is_local_only_evidence(path)
                )
            # Already published history is excluded; the outgoing tree must
            # still remove every archive, including unchanged LFS pointers.
            entries.update(
                (oid, path) for oid, path in tree_entries(
                    repo, head,
                ) if is_local_only_evidence(path)
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
    for oid, path in sorted(entries, key=lambda item: (item[1], item[0])):
        size = sizes[oid]
        if is_local_only_evidence(path):
            issues.append(f"{path!r}: evidence packages/raw results are local only; ordinary Git and Git LFS uploads are prohibited.")
        elif size > max_bytes:
            issues.append(f"{path!r}: ordinary Git blob is {size} bytes; limit is {max_bytes}. Keep a summary/small witness and keep research bulk data local.")
    return issues


def check_push(repo: Path, remote: str, data: bytes) -> list[str]:
    issues = []
    for line in data.decode("utf-8").splitlines():
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if not local_sha.strip("0"):  # Deleting a ref does not upload objects.
            continue
        if remote_sha.strip("0"):
            base: str | tuple[str, ...] = remote_sha
        else:
            # New refs can share already published history. Exclude the
            # cached remote refs, but examine every newly introduced commit.
            base = tuple(git(repo, "for-each-ref", "--format=%(objectname)",
                             "refs/remotes/" + remote + "/").decode().splitlines())
        issues.extend(f"{local_ref} -> {remote_ref}: {issue}"
                      for issue in check(repo, base=base, head=local_sha))
    return issues


def report_issues(issues: list[str]) -> None:
    print("Repository storage policy failed:", file=sys.stderr)
    for issue in issues:
        print("  " + issue, file=sys.stderr)
    print("See docs/guides/evidence-storage.md. Nothing was uploaded or changed.", file=sys.stderr)


def pre_push(repo: Path, remote: str, url: str, data: bytes) -> int:
    issues = check_push(repo, remote, data)
    if issues:
        report_issues(issues)
        return 1
    # Preserve LFS uploads for the knowledge database. LFS must receive the
    # original ref input, and must never run until ALL refs pass the policy.
    return subprocess.run(["git", "lfs", "pre-push", remote, url],
                          cwd=repo, input=data, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", help="Exclude objects already reachable from this commit")
    mode.add_argument("--staged", action="store_true", help="Check index additions/modifications")
    mode.add_argument("--all", dest="all_files", action="store_true", help="Check all files at --head")
    mode.add_argument("--pre-push", nargs=2, metavar=("REMOTE", "URL"), help="Check outgoing refs before LFS upload")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(git(Path.cwd(), "rev-parse", "--show-toplevel")).strip())
        if args.pre_push:
            return pre_push(root, *args.pre_push, sys.stdin.buffer.read())
        issues = check(root, base=args.base, head=args.head, staged=args.staged, all_files=args.all_files)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Storage check could not complete: {error}", file=sys.stderr)
        return 2
    if issues:
        report_issues(issues)
        return 1
    print("Repository storage policy passed (Git objects only; remote LFS upload is separate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
