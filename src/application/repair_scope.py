"""Derive explicit-repair scope from the latest failed full candidate."""
from __future__ import annotations

import copy
import json
import re

from application.generation_repair import candidate_base, materialize_partial, RepairAssemblyError
from contract_repair import patch_device_addresses


class RepairScopeError(ValueError):
    pass


def _latest_full_candidate(candidate_text, snapshot):
    try:
        parsed = json.loads(candidate_text)
    except (TypeError, ValueError):
        parsed = None

    full = candidate_base(parsed)
    if full is not None:
        return full

    # Compatibility for a failed job created before path-patch repair: an old
    # repair job may have staged only its partial rung envelope. Reconstruct the
    # exact full candidate once from that job's immutable baseline, then forget
    # the old scope and derive a new one from the current validation failure.
    baseline = candidate_base(snapshot.get("repair_baseline")) if snapshot.get("repair_mode") else None
    if baseline is not None and isinstance(parsed, dict) and parsed.get("mode") == "partial":
        try:
            return materialize_partial(baseline, parsed, replacement_only=True)
        except RepairAssemblyError:
            pass
    return None


def derive(candidate_text, snapshot, violations):
    """Return (full_candidate, rung_ids, addresses) for this failure only.

    A prior repair scope is never inherited after a complete candidate exists.
    If the current model patch itself failed before materialization, the previous
    immutable baseline/scope may be reused because there is no newer ladder state.
    """
    full = _latest_full_candidate(candidate_text, snapshot)
    if full is None:
        baseline = candidate_base(snapshot.get("repair_baseline")) if snapshot.get("repair_mode") else None
        if baseline is None:
            return None, set(), set()
        # Patch-protocol failure before a full candidate could be materialized.
        ids = {
            int(item) for item in (snapshot.get("allowed_rung_ids") or ())
            if isinstance(item, int) and not isinstance(item, bool)
        }
        if not ids:
            raise RepairScopeError("Failed repair has no recoverable rung scope")
        selected = [rung for rung in baseline["rungs"] if rung.get("rung_id") in ids]
        addresses = patch_device_addresses({
            "mode": "partial", "device_comments": {}, "delete_rung_ids": [],
            "rungs": selected,
        })
        return baseline, ids, addresses

    rungs = full.get("rungs", [])
    by_index = {index: rung for index, rung in enumerate(rungs)}
    ids = set()
    saw_rung_path = False
    for item in violations or ():
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or "rungs" not in path:
            continue
        saw_rung_path = True
        match = re.search(r"rungs(?:\.|\[)(\d+)", path)
        if match is None:
            raise RepairScopeError("Validation rung path cannot be resolved")
        rung = by_index.get(int(match.group(1)))
        rung_id = rung.get("rung_id") if isinstance(rung, dict) else None
        if not isinstance(rung_id, int) or isinstance(rung_id, bool):
            raise RepairScopeError("Validation rung identity cannot be resolved")
        ids.add(rung_id)

    if not saw_rung_path or not ids:
        raise RepairScopeError("Local repair requires an exact rung validation path")

    selected = [copy.deepcopy(rung) for rung in rungs if rung.get("rung_id") in ids]
    addresses = patch_device_addresses({
        "mode": "partial", "device_comments": {}, "delete_rung_ids": [],
        "rungs": selected,
    })
    return full, ids, addresses
