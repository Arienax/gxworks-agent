# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Compiled base instruction catalogue. RAG definition owners are attached separately."""
from __future__ import annotations

from collections.abc import MutableMapping
from contextlib import closing, contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from plc.instruction_owner_store import _digest, _identity


CATALOG_FILENAME = "instruction_catalog.sqlite"
CATALOG_VERSION = 1
_COMPILER_FILES = ("instruction_catalog_store.py", "instructions.py", "instruction_definition.py",
                   "instruction_knowledge.py", "instruction_relations.py")


def _inputs(directory):
    from plc.instructions import CATALOG_REQUIRED_FILES, CATALOG_OPTIONAL_FILES, CATALOG_OVERLAY_FILES

    paths = {name: directory / name for name in CATALOG_REQUIRED_FILES}
    paths.update((name, directory / name) for name in CATALOG_OPTIONAL_FILES + CATALOG_OVERLAY_FILES
                 if (directory / name).is_file())
    if not getattr(sys, "frozen", False):
        paths.update(("compiler/" + name, Path(__file__).with_name(name)) for name in _COMPILER_FILES)
    return paths


def _stamp(path):
    state = path.stat()
    return state.st_size, state.st_mtime_ns


def _matches_inputs(paths, recorded):
    if getattr(sys, "frozen", False):
        recorded = {name: stamp for name, stamp in recorded.items() if not name.startswith("compiler/")}
    if paths.keys() != recorded.keys():
        return False
    for name, path in paths.items():
        size, mtime = _stamp(path)
        stored_size, stored_mtime, digest = recorded[name]
        # Staging and extraction can change timestamps without changing the input.
        if size != stored_size or mtime != stored_mtime and _digest(path) != digest:
            return False
    return True


@lru_cache(maxsize=1)
def _spec_adapter():
    from pydantic import TypeAdapter
    from plc.instructions import InstructionSpec

    return TypeAdapter(InstructionSpec)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _read_specs(connection, section, cpu=""):
    rows = connection.execute("SELECT entry_key, payload FROM catalog WHERE section=? AND cpu=? ORDER BY rowid",
                              (section, cpu))
    adapter = _spec_adapter()
    return {tuple(json.loads(key)): adapter.validate_json(payload, strict=True) for key, payload in rows}


class _CatalogStore:
    def __init__(self, path):
        self.path = path.resolve()
        self._identity = _identity(self.path)

    def current(self):
        try:
            return _identity(self.path) == self._identity
        except OSError:
            return False

    @contextmanager
    def connect(self):
        if not self.current():
            raise ValueError("Instruction catalogue changed; restart the checking context")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA trusted_schema=OFF")
            yield connection
        if not self.current():
            raise ValueError("Instruction catalogue changed while reading")

    def knowledge(self, section):
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM catalog WHERE section=? AND cpu='' ORDER BY rowid", (section,))
            return tuple(json.loads(row[0]) for row in rows)


class _CpuContracts(MutableMapping):
    """Materialize one CPU at a time and keep the existing writable owner overlay."""

    def __init__(self, store, keys):
        self._store = store
        self._keys = dict.fromkeys(keys)
        self._values = {}

    def __getitem__(self, key):
        if key not in self._keys:
            raise KeyError(key)
        if key not in self._values:
            with self._store.connect() as connection:
                specs = _read_specs(connection, "cpu", key[2])
            for compiled_key, spec in specs.items():
                if compiled_key in self._keys:
                    self._values.setdefault(compiled_key, spec)
        return self._values[key]

    def __setitem__(self, key, value):
        self._keys[key] = None
        self._values[key] = value

    def __delitem__(self, key):
        del self._keys[key]
        self._values.pop(key, None)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __contains__(self, key):
        return key in self._keys


def read_compiled_catalog(directory):
    """Use the compiled snapshot only while its inputs and format still match."""
    from plc.instructions import InstructionRegistry

    directory = Path(directory)
    path = directory / CATALOG_FILENAME
    if not path.is_file():
        return None
    try:
        store = _CatalogStore(path)
        with store.connect() as connection:
            if connection.execute("PRAGMA user_version").fetchone() != (CATALOG_VERSION,):
                return None
            row = connection.execute("SELECT value FROM meta WHERE key='inputs'").fetchone()
            if row is None or not _matches_inputs(_inputs(directory), json.loads(row[0])):
                return None
            registry = InstructionRegistry()
            registry._specs = _read_specs(connection, "base")
            registry._source_forms = _read_specs(connection, "source")
            keys = connection.execute("SELECT entry_key FROM catalog WHERE section='cpu' ORDER BY rowid")
            registry._cpu_contracts = _CpuContracts(store, (tuple(json.loads(row[0])) for row in keys))
        registry._compiled_catalog = store
        return registry
    except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _compiled_rows(registry):
    adapter = _spec_adapter()
    for section, specs in (("base", registry._specs), ("source", registry._source_forms), ("cpu", registry._cpu_contracts)):
        for key, spec in specs.items():
            payload = adapter.dump_json(spec, exclude_defaults=True, warnings="error")
            if adapter.validate_json(payload, strict=True) != spec:
                raise ValueError(f"Instruction catalogue would change a compiled spec: {key!r}")
            yield section, _json(key), key[2] if section == "cpu" else "", payload
    for section, rows in (("family", registry._family_knowledge), ("profile", registry._knowledge_profiles)):
        for index, row in enumerate(rows):
            yield section, str(index), "", _json(row)


def build_compiled_catalog(directory):
    from plc.instructions import load_catalog_sources

    directory = Path(directory).resolve()
    target = directory / CATALOG_FILENAME
    if read_compiled_catalog(directory) is not None:
        return target

    inputs = _inputs(directory)
    recorded = {name: (*_stamp(path), _digest(path)) for name, path in inputs.items()}
    registry = load_catalog_sources(directory)
    with TemporaryDirectory(prefix=".instruction-catalog-", dir=directory) as staging:
        database = Path(staging) / CATALOG_FILENAME
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript(f"""
                PRAGMA user_version={CATALOG_VERSION};
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE catalog (section TEXT NOT NULL, entry_key TEXT NOT NULL,
                    cpu TEXT NOT NULL, payload BLOB NOT NULL, UNIQUE(section, entry_key));
                CREATE INDEX catalog_cpu ON catalog(section, cpu);
            """)
            connection.execute("INSERT INTO meta VALUES ('inputs', ?)", (_json(recorded),))
            connection.executemany("INSERT INTO catalog VALUES (?, ?, ?, ?)", _compiled_rows(registry))
        if not _matches_inputs(_inputs(directory), recorded):
            raise ValueError("Instruction catalogue sources changed during compilation")
        os.replace(database, target)
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing the authored Mitsubishi instruction catalogue")
    print(build_compiled_catalog(parser.parse_args().directory))
