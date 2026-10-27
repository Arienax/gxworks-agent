# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Transactional project metadata and independently stored ordered records.

JSON manifests select this format explicitly. Legacy JSON remains readable until
an explicit write publishes a manifest; merely opening a project never migrates it.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

FORMAT = "project-records-v1"
COLLECTIONS = ("messages", "versions", "reports")
VERSION_COLLECTIONS = ("simulator_runs", "simulator_test_plans", "multi_agent_runs", "debug_attempts")


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _connect(path, *, writable=False):
    path = Path(path).resolve()
    if writable:
        return sqlite3.connect(str(path), timeout=10)
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)


@contextmanager
def _transaction(path, *, writable=False):
    try:
        with closing(_connect(path, writable=writable)) as connection, connection:
            connection.execute("PRAGMA synchronous=FULL" if writable else "PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
            yield connection
    except sqlite3.Error as error:
        raise OSError(f"Cannot access project records: {path}") from error


def _schema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS records (collection TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(collection, position))")


def _rows(connection, collection):
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT payload FROM records WHERE collection=? ORDER BY position", (collection,))]


def _metadata(connection):
    row = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchone()
    if row is None:
        raise ValueError("Project record metadata is missing")
    return json.loads(row[0])


def read(path):
    # A single snapshot includes both selection metadata and every record.
    with _transaction(path) as connection:
        project = _metadata(connection)
        for key in COLLECTIONS:
            project[key] = _rows(connection, key)
        for version in project["versions"]:
            for key in VERSION_COLLECTIONS:
                version[key] = _rows(connection, "versions/" + version["id"] + "/" + key)
        return project


def _replace_rows(connection, collection, values):
    previous = dict(connection.execute(
        "SELECT position, payload FROM records WHERE collection=?", (collection,)))
    for position, value in enumerate(values):
        encoded = _encode(value)
        if previous.get(position) != encoded:
            connection.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?)",
                               (collection, position, encoded))
    connection.execute("DELETE FROM records WHERE collection=? AND position>=?", (collection, len(values)))


def save(path, project):
    with _transaction(path, writable=True) as connection:
        _schema(connection)
        metadata = {key: value for key, value in project.items() if key not in COLLECTIONS}
        encoded = _encode(metadata)
        previous = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchone()
        if previous is None or previous[0] != encoded:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (1, ?)", (encoded,))
        collections = set(COLLECTIONS)
        for key in COLLECTIONS:
            values = project.get(key, [])
            if key == "versions":
                versions = []
                for version in values:
                    versions.append({k: v for k, v in version.items() if k not in VERSION_COLLECTIONS})
                    for nested in VERSION_COLLECTIONS:
                        name = "versions/" + version["id"] + "/" + nested
                        collections.add(name)
                        _replace_rows(connection, name, version.get(nested, []))
                values = versions
            _replace_rows(connection, key, values)
        for name, in connection.execute("SELECT DISTINCT collection FROM records").fetchall():
            if name not in collections:
                connection.execute("DELETE FROM records WHERE collection=?", (name,))


def append_message(path, message, *, updated_at):
    with _transaction(path, writable=True) as connection:
        metadata = _metadata(connection)
        position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM records WHERE collection='messages'").fetchone()[0]
        connection.execute("INSERT INTO records VALUES ('messages', ?, ?)", (position, _encode(message)))
        metadata["updated_at"] = updated_at
        connection.execute("UPDATE metadata SET payload=? WHERE id=1", (_encode(metadata),))
