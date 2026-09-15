"""Deterministic recovery for compact Agent-B data emitted with ladder_v1 key names.

Some json_object providers keep the compact token values but expand the transport
field names (``r`` -> ``rungs``, ``b`` -> ``branches``, ``i`` -> ``inputs``,
``o`` -> ``outputs``).  That response is neither valid ladder_v1 nor the exact
compact protocol, but the mapping back to compact form is purely structural.
This module performs only that key/container normalization; PLC tokens are copied
verbatim and the existing compact decoder remains authoritative for semantics.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _compact_or(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"or"}:
        return False
    branches = value.get("or")
    return (
        isinstance(branches, list)
        and bool(branches)
        and all(
            isinstance(branch, list)
            and bool(branch)
            and all(_nonempty_string(item) for item in branch)
            for branch in branches
        )
    )


def _compact_input(value: Any) -> bool:
    return _nonempty_string(value) or _compact_or(value)


def _hybrid_to_compact(value: Any) -> dict[str, Any] | None:
    """Map only an unambiguous long-key compact alias back to ``{"r": ...}``."""
    if not isinstance(value, Mapping):
        return None
    if not set(value).issubset({"device_comments", "rungs"}):
        return None
    raw_rungs = value.get("rungs")
    if not isinstance(raw_rungs, list) or not raw_rungs:
        return None

    comments = value.get("device_comments", {})
    if not isinstance(comments, Mapping) or any(
        not isinstance(key, str) or not isinstance(comment, str)
        for key, comment in comments.items()
    ):
        return None

    rows: list[dict[str, Any]] = []
    for rung_index, rung in enumerate(raw_rungs, start=1):
        if not isinstance(rung, Mapping) or not set(rung).issubset(
            {"rung_id", "header_element", "shared_inputs", "branches"}
        ):
            return None
        rung_id = rung.get("rung_id")
        if rung_id is not None and (
            isinstance(rung_id, bool) or not isinstance(rung_id, int) or rung_id != rung_index
        ):
            return None

        header = rung.get("header_element")
        if header is not None and not _nonempty_string(header):
            return None
        shared = rung.get("shared_inputs", [])
        if not isinstance(shared, list) or any(not _nonempty_string(item) for item in shared):
            return None

        raw_branches = rung.get("branches")
        if not isinstance(raw_branches, list) or not raw_branches:
            return None
        compact_branches: list[dict[str, Any]] = []
        for branch_index, branch in enumerate(raw_branches, start=1):
            if not isinstance(branch, Mapping) or not set(branch).issubset(
                {"branch_id", "y_offset_level", "inputs", "outputs"}
            ):
                return None
            branch_id = branch.get("branch_id")
            if branch_id is not None and (
                isinstance(branch_id, bool)
                or not isinstance(branch_id, int)
                or branch_id != branch_index
            ):
                return None
            y_offset = branch.get("y_offset_level")
            if y_offset is not None and (
                isinstance(y_offset, bool)
                or not isinstance(y_offset, int)
                or y_offset != branch_index - 1
            ):
                return None

            inputs = branch.get("inputs", [])
            outputs = branch.get("outputs")
            if not isinstance(inputs, list) or any(not _compact_input(item) for item in inputs):
                return None
            if not isinstance(outputs, list) or not outputs or any(
                not _nonempty_string(item) for item in outputs
            ):
                return None

            compact_branch = {"o": copy.deepcopy(outputs)}
            if inputs:
                compact_branch["i"] = copy.deepcopy(inputs)
            compact_branches.append(compact_branch)

        row: dict[str, Any] = {"b": compact_branches}
        if header is not None:
            row["h"] = header
        if shared:
            row["s"] = copy.deepcopy(shared)
        rows.append(row)

    return {"r": rows}


def expand_hybrid_compact_ladder(value: Any, confirmed_spec=None) -> dict[str, Any] | None:
    """Expand a long-key compact alias to canonical ladder_v1, or return ``None``.

    Recognition is deliberately strict.  No PLC token is edited here; the
    existing Agent-B compact decoder parses every input/output token and rejects
    unsupported semantics in the same way as a normal compact response.
    """
    compact = _hybrid_to_compact(value)
    if compact is None:
        return None

    from application.generation_agent import _expand_compact_ladder, _strict_generation_projection

    projected = (
        _strict_generation_projection(confirmed_spec)
        if isinstance(confirmed_spec, Mapping)
        else {}
    )
    ladder = _expand_compact_ladder(compact, projected)
    comments = value.get("device_comments") if isinstance(value, Mapping) else None
    if isinstance(comments, Mapping) and comments:
        ladder["device_comments"].update(copy.deepcopy(dict(comments)))
    return ladder
