# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Source identity for compiled instruction definitions.

A compiled definition records two different things and they must not be confused:

* ``source_materials`` - the official material, revision and pages a fact came from.
  This identity stays true whatever database produced it.
* ``build_source_ids`` - local row ids of the build index the artifact was compiled
  from. They mean nothing outside that index: the same number identifies a different
  material in another database, so they are never looked up in the runtime index.

``source_reference_status`` records the evidence quality of those references. It is a
marker, not an admission requirement: a definition whose original chapter cannot be
replayed at runtime is still a usable definition.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

LEGACY_UNVERIFIED = "legacy_unverified"
VERIFIED_AT_BUILD = "verified_at_build"
NO_BUILD_REFERENCE = "no_build_reference"

REPLAY_AVAILABLE = "available"
REPLAY_UNAVAILABLE = "unavailable"
REPLAY_NOT_APPLICABLE = "not_applicable"


@lru_cache(maxsize=8)
def _digest(path_text):
    digest = hashlib.sha256()
    with open(path_text, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_index_identity(index_path):
    """Return the audited identity of a build index, or ``None`` when unavailable.

    The digest is optional audit information. It is written when the build index is
    present and never used to reject an existing artifact.
    """
    if index_path is None:
        return None
    path = Path(index_path)
    if not path.is_file():
        return None
    digest = _digest(str(path.resolve()))
    return {
        "build_index_id": "sha256:" + digest,
        "digest": digest,
        "file_name": path.name,
    }


def _rows_by_id(db, ids):
    ids = [str(value) for value in ids if str(value)]
    if db is None or not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        "SELECT id, manual_id, revision FROM chunks WHERE CAST(id AS TEXT) IN ("
        + placeholders + ")",
        tuple(ids),
    ).fetchall()
    return {str(row[0]): (str(row[1] or ""), str(row[2] or "")) for row in rows}


def source_reference_status(ids, *, db=None, identity=None):
    """Say how far the build references have been checked, using real evidence."""
    ids = [str(value) for value in ids if str(value)]
    if not ids:
        return NO_BUILD_REFERENCE
    if db is None or identity is None:
        return LEGACY_UNVERIFIED
    found = _rows_by_id(db, ids)
    if set(found) != set(ids):
        return LEGACY_UNVERIFIED
    return VERIFIED_AT_BUILD


def runtime_replay_status(entry, *, db=None):
    """Decide at read time whether this runtime can replay the build references.

    Computed on every read instead of stored: the same artifact becomes replayable as
    soon as the matching build index is available, and stops being replayable when the
    runtime index changes.
    """
    if not isinstance(entry, dict):
        return REPLAY_NOT_APPLICABLE
    ids = [str(value) for value in entry.get("build_source_ids") or () if str(value)]
    if not ids:
        return REPLAY_NOT_APPLICABLE
    if db is None:
        return REPLAY_UNAVAILABLE
    if entry.get("source_reference_status") != VERIFIED_AT_BUILD:
        # Never claim a replay the build itself did not verify.
        return REPLAY_UNAVAILABLE
    found = _rows_by_id(db, ids)
    if set(found) != set(ids):
        return REPLAY_UNAVAILABLE
    claimed = {(str(material.get("manual_id") or ""), str(material.get("revision") or ""))
               for material in entry.get("source_materials") or () if isinstance(material, dict)}
    if claimed and not set(found.values()) <= claimed:
        # The numbers exist here but belong to another material.
        return REPLAY_UNAVAILABLE
    return REPLAY_AVAILABLE
