"""Display-only ladder rung numbering for legacy program JSON.

Legacy projects use ``rung_id`` as a stable merge and report anchor.  It is
therefore unsafe to renumber persisted JSON merely to make the UI continuous.
This module builds a version-local map between the operator-facing rung number
(``1..N``), the raw legacy id, and the canonical JSON path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


_RUNG_PATH_RE = re.compile(r"\$\.rungs\[(\d+)\]")


def build_rung_display_map(program: Any) -> Dict[str, Any]:
    """Return immutable-by-convention display mappings for one program version.

    Array position is the only source of the visible number.  Raw ``rung_id``
    values are retained verbatim and may be sparse.  Invalid/missing ids still
    receive a display number and JSON path, but are omitted from the raw-id
    lookup so callers never invent a persistence identity.
    """

    if isinstance(program, Mapping):
        rungs = program.get("rungs")
    elif isinstance(program, list):
        rungs = program
    else:
        rungs = None
    if not isinstance(rungs, list):
        rungs = []

    by_index = {}
    by_display = {}
    raw_to_display = {}
    display_to_raw = {}
    display_to_path = {}

    for index, rung in enumerate(rungs):
        display_number = index + 1
        raw_rung_id = rung.get("rung_id") if isinstance(rung, Mapping) else None
        json_path = "$.rungs[%d]" % index
        location = {
            "index": index,
            "display_number": display_number,
            "raw_rung_id": raw_rung_id,
            "json_path": json_path,
        }
        by_index[index] = location
        by_display[display_number] = location
        display_to_path[display_number] = json_path
        if raw_rung_id not in (None, ""):
            display_to_raw[display_number] = raw_rung_id
            # Duplicate ids are invalid, but keeping the first occurrence is
            # safer for read-only legacy data than silently pointing elsewhere.
            try:
                raw_to_display.setdefault(raw_rung_id, display_number)
            except TypeError:
                # Malformed legacy data remains viewable by array path, but an
                # unhashable value cannot safely act as a locate/repair key.
                pass

    return {
        "by_index": by_index,
        "by_display": by_display,
        "raw_to_display": raw_to_display,
        "display_to_raw": display_to_raw,
        "display_to_path": display_to_path,
    }


def rung_index_from_path(path: Any) -> Optional[int]:
    match = _RUNG_PATH_RE.search(str(path or ""))
    return int(match.group(1)) if match else None


def display_number_for_anchor(
    display_map: Any,
    raw_rung_id: Any = None,
    json_path: Any = "",
) -> Optional[int]:
    """Resolve a visible rung number, preferring the exact version path."""

    if not isinstance(display_map, Mapping):
        return None
    index = rung_index_from_path(json_path)
    by_index = display_map.get("by_index") or {}
    if index is not None and isinstance(by_index, Mapping):
        location = by_index.get(index)
        if location is None:
            location = by_index.get(str(index))
        if isinstance(location, Mapping):
            value = location.get("display_number")
            if isinstance(value, int):
                return value

    raw_to_display = display_map.get("raw_to_display") or {}
    if raw_rung_id not in (None, "") and isinstance(raw_to_display, Mapping):
        value = raw_to_display.get(raw_rung_id)
        if value is None:
            value = raw_to_display.get(str(raw_rung_id))
        if isinstance(value, int):
            return value
    return None


def location_for_display(display_map: Any, display_number: Any) -> Dict[str, Any]:
    """Resolve the raw id and path used when a visible rung is selected."""

    try:
        number = int(display_number)
    except (TypeError, ValueError):
        return {}
    if not isinstance(display_map, Mapping):
        return {}
    by_display = display_map.get("by_display") or {}
    location = by_display.get(number) if isinstance(by_display, Mapping) else None
    if location is None and isinstance(by_display, Mapping):
        location = by_display.get(str(number))
    return dict(location) if isinstance(location, Mapping) else {}


__all__ = [
    "build_rung_display_map",
    "display_number_for_anchor",
    "location_for_display",
    "rung_index_from_path",
]
