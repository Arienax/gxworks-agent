"""Path-addressed local repair for rejected ladder JSON.

The model never owns or returns a replacement rung.  The backend keeps the full
candidate, exposes only the failing rung(s) as read-only context, applies a small
set of JSON-pointer-like operations, and then revalidates the complete ladder.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from application.generation_repair import RepairAssemblyError, candidate_base
from contract_repair import patch_device_addresses
from model_provider import ModelRequest, collect_response
from workflow_response_contracts import FIELD_PATCH_RESPONSE

MODE = "local_patch"
SCHEMA_VERSION = 1
MAX_PATCHES = 8
_ALLOWED_ROOTS = frozenset({"header_element", "shared_inputs", "branches", "debug_note"})

SYSTEM_PROMPT = """# PLC ladder local JSON patch
Repair only the rejected locations in the supplied immutable ladder baseline.
The backend owns the complete program. You MUST NOT return a rung, branch list,
partial ladder, or full ladder program.

Return exactly one JSON object:
{"schema_version":1,"mode":"local_patch","base_sha256":"...","patches":[...]}

Each patch is one of:
{"rung_id":44,"op":"set","path":"/branches/0/inputs/0/branches","value":[...]}
{"rung_id":44,"op":"remove","path":"/shared_inputs/3"}

Rules:
- Copy base_sha256 exactly.
- Use only rung_id values listed in allowed_rung_ids.
- path is relative to that rung and must identify only the minimum local subtree.
- Never patch /rung_id and never replace the whole rung or the whole /branches array.
- Use `set` to replace an existing value or set an object field; use `remove` only
  for an existing field/list item. At most 8 operations.
- Preserve control logic, addresses, operands, parameters and contact polarity
  except where the reported structural error itself requires moving/re-encoding
  the same existing information.
- Do not invent device addresses. Do not output markdown or explanation.
"""


def base_sha256(value):
    saved = candidate_base(value)
    if saved is None:
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    raw = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pointer_segments(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    if len(pointer) > 240:
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    out = []
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not token or token in {".", ".."}:
            raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
        out.append(int(token) if token.isdigit() else token)
    if len(out) > 16 or out[0] not in _ALLOWED_ROOTS:
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    if out[0] == "branches" and len(out) == 1:
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    return out


def _lookup(root, segments):
    current = root
    for segment in segments:
        if isinstance(segment, int):
            if not isinstance(current, list) or not 0 <= segment < len(current):
                raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
            current = current[segment]
        else:
            if not isinstance(current, dict) or segment not in current:
                raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
            current = current[segment]
    return current


def _parent(root, segments, *, allow_new_object_key=False):
    if not segments:
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    parent_segments, leaf = segments[:-1], segments[-1]
    parent = _lookup(root, parent_segments) if parent_segments else root
    if isinstance(leaf, int):
        if not isinstance(parent, list) or not 0 <= leaf < len(parent):
            raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    elif not isinstance(parent, dict):
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    elif not allow_new_object_key and leaf not in parent:
        raise RepairAssemblyError("$.patches.path", "repair_scope_violation")
    return parent, leaf


def _target_rungs(base, allowed_rung_ids):
    ids = {
        int(item) for item in (allowed_rung_ids or ())
        if isinstance(item, int) and not isinstance(item, bool)
    }
    saved = candidate_base(base)
    if saved is None or not ids:
        raise RepairAssemblyError("$.allowed_rung_ids", "repair_base_invalid")
    selected = [
        copy.deepcopy(rung) for rung in saved.get("rungs", [])
        if isinstance(rung, dict) and rung.get("rung_id") in ids
    ]
    if {rung.get("rung_id") for rung in selected} != ids:
        raise RepairAssemblyError("$.allowed_rung_ids", "repair_scope_violation")
    return saved, ids, selected


def build_payload(base, violations, allowed_rung_ids, allowed_addresses, plc_model="FX3U"):
    saved, ids, selected = _target_rungs(base, allowed_rung_ids)
    safe_violations = []
    for row in violations or ():
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        safe_violations.append({
            "path": row["path"][:240],
            "reason": str(row.get("reason") or "invalid_ladder_structure")[:80],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": base_sha256(saved),
        "plc_model": str(plc_model or "FX3U").upper(),
        "allowed_rung_ids": sorted(ids),
        "allowed_addresses": sorted({
            str(item).strip().upper() for item in (allowed_addresses or ()) if str(item).strip()
        }),
        "violations": safe_violations[:16],
        "baseline_subset": {"rungs": selected},
    }


def request_patch(payload, provider, model_name, effort,
                  *, on_reasoning_chunk=None, on_content_chunk=None):
    if provider is None:
        raise ValueError("Local repair provider is unavailable")
    options = {"response_format": {"type": "json_object"}}
    if effort is not None:
        options["reasoning_effort"] = effort
    request = ModelRequest.from_messages(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ],
        model=model_name or None,
        options=options,
        stream=True,
        response_contract=FIELD_PATCH_RESPONSE,
    )
    response = collect_response(
        provider,
        request,
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    return response.message.reasoning, response.message.content


def apply(base, response, allowed_rung_ids, allowed_addresses=()):
    """Apply a bounded local patch while keeping rung identity/order immutable."""
    saved, allowed_ids, _selected = _target_rungs(base, allowed_rung_ids)
    original = copy.deepcopy(saved)
    expected_sha = base_sha256(saved)
    required = {"schema_version", "mode", "base_sha256", "patches"}
    if not isinstance(response, dict) or set(response) != required:
        raise RepairAssemblyError("$", "repair_shape_invalid")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("mode") != MODE:
        raise RepairAssemblyError("$.mode", "repair_shape_invalid")
    if response.get("base_sha256") != expected_sha:
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    patches = response.get("patches")
    if not isinstance(patches, list) or not 1 <= len(patches) <= MAX_PATCHES:
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")

    by_id = {
        rung.get("rung_id"): rung for rung in saved.get("rungs", [])
        if isinstance(rung, dict)
    }
    for index, patch in enumerate(patches):
        prefix = f"$.patches[{index}]"
        if not isinstance(patch, dict):
            raise RepairAssemblyError(prefix, "repair_shape_invalid")
        op = patch.get("op")
        expected_keys = {"rung_id", "op", "path", "value"} if op == "set" else {"rung_id", "op", "path"}
        if op not in {"set", "remove"} or set(patch) != expected_keys:
            raise RepairAssemblyError(prefix, "repair_shape_invalid")
        rung_id = patch.get("rung_id")
        if not isinstance(rung_id, int) or isinstance(rung_id, bool) or rung_id not in allowed_ids:
            raise RepairAssemblyError(prefix + ".rung_id", "repair_scope_violation")
        rung = by_id.get(rung_id)
        if not isinstance(rung, dict):
            raise RepairAssemblyError(prefix + ".rung_id", "repair_scope_violation")
        segments = _pointer_segments(patch.get("path"))
        if segments[0] == "rung_id":
            raise RepairAssemblyError(prefix + ".path", "repair_scope_violation")

        if op == "set":
            parent, leaf = _parent(rung, segments, allow_new_object_key=True)
            value = copy.deepcopy(patch.get("value"))
            if isinstance(leaf, int):
                parent[leaf] = value
            else:
                parent[leaf] = value
        else:
            parent, leaf = _parent(rung, segments)
            if isinstance(leaf, int):
                parent.pop(leaf)
            else:
                parent.pop(leaf)

    before_ids = [rung.get("rung_id") for rung in original.get("rungs", [])]
    after_ids = [rung.get("rung_id") for rung in saved.get("rungs", [])]
    if before_ids != after_ids:
        raise RepairAssemblyError("$.patches", "repair_scope_violation")
    if saved == original:
        raise RepairAssemblyError("$.patches", "repair_no_progress")

    allowed_devices = {
        str(item).strip().upper() for item in (allowed_addresses or ()) if str(item).strip()
    }
    if allowed_devices:
        changed = [by_id[rung_id] for rung_id in allowed_ids]
        introduced = patch_device_addresses({
            "mode": "partial", "device_comments": {}, "delete_rung_ids": [], "rungs": changed,
        }) - allowed_devices
        if introduced:
            raise RepairAssemblyError("$.patches", "repair_scope_violation")
    return saved
