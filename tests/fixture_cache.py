"""Process-local caches for immutable test inputs, never tested results."""
from __future__ import annotations

import ast
from pathlib import Path
from zipfile import ZipFile


def _identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class SourceCache:
    """Parse each source content once; changing bytes invalidates the entry."""

    def __init__(self):
        self._entries = {}

    def tree(self, path):
        path = Path(path).resolve()
        identity = _identity(path)
        previous = self._entries.get(path)
        if previous is None or previous[0] != identity:
            content = path.read_bytes()
            previous = (identity, ast.parse(content.decode('utf-8-sig'), filename=str(path)))
            self._entries[path] = previous
        return previous[1]


class ArchiveCache:
    """Read only requested members and return a fresh dictionary of bytes.

    Archives are frozen inputs for a test process. File identity/metadata changes
    invalidate their entries, including replacement at the same path. No mutable
    parsed JSON, GXW context or verifier result is shared between cases.
    """

    def __init__(self):
        self._entries = {}

    def members(self, path, prefix=''):
        path = Path(path).resolve()
        identity = _identity(path)
        previous = self._entries.get(path)
        if previous is None or previous[0] != identity:
            with ZipFile(path) as archive:
                names = tuple(archive.namelist())
            previous = (identity, names, {})
            self._entries[path] = previous
        _, names, values = previous
        selected = list(dict.fromkeys(name for name in names if name.startswith(prefix)))
        missing = [name for name in selected if name not in values]
        if missing:
            with ZipFile(path) as archive:
                for name in missing:
                    values[name] = archive.read(name)
        return {name.removeprefix(prefix): values[name] for name in selected}


SOURCE_CACHE = SourceCache()
ARCHIVE_CACHE = ArchiveCache()
