"""Data-backed authority for choosing the primary official instruction source."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from collections.abc import Mapping

from shared.paths import resource_path

_AUTHORITY_RESOURCE = "resources/instructions/mitsubishi/instruction_source_authority.json"
_SOURCE_MANIFEST = "knowledge/sources.json"


def _manual_catalog():
    payload = json.loads(resource_path(_SOURCE_MANIFEST).read_text(encoding="utf-8"))
    manuals = payload.get("manuals") if isinstance(payload, Mapping) else None
    if not isinstance(manuals, list):
        raise ValueError("knowledge source manifest has no manuals list")
    return {
        str(row.get("id") or "").strip(): row
        for row in manuals
        if isinstance(row, Mapping) and str(row.get("id") or "").strip()
    }


@lru_cache(maxsize=1)
def instruction_source_authorities():
    """Return validated immutable-by-convention instruction source rules."""
    payload = json.loads(resource_path(_AUTHORITY_RESOURCE).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("instruction source authority schema_version must be 1")
    rows = payload.get("instruction_sources")
    if not isinstance(rows, list):
        raise ValueError("instruction source authority must contain instruction_sources")

    manuals = _manual_catalog()
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("instruction source authority entries must be objects")
        manual_id = str(row.get("manual_id") or "").strip()
        opcodes = tuple(
            str(value).strip().upper()
            for value in row.get("opcodes") or ()
            if str(value).strip()
        )
        models = tuple(
            str(value).strip().upper()
            for value in row.get("plc_models") or ()
            if str(value).strip()
        )
        if not manual_id or not opcodes or not models:
            raise ValueError("instruction source authority entry is incomplete")
        manual = manuals.get(manual_id)
        if manual is None:
            raise ValueError("unknown authoritative manual: " + manual_id)
        supported = {str(value).upper() for value in manual.get("plc_models") or ()}
        if not set(models) <= supported:
            raise ValueError(
                "authority model scope exceeds manual applicability: " + manual_id
            )
        normalized = {
            "manual_id": manual_id,
            "authority": str(
                row.get("authority") or "primary_instruction_semantics"
            ),
            "reason": str(row.get("reason") or ""),
            "plc_models": list(models),
            "opcodes": list(opcodes),
        }
        for model in models:
            for opcode in opcodes:
                key = (model, opcode)
                existing = result.get(key)
                if existing is not None and existing["manual_id"] != manual_id:
                    raise ValueError(
                        f"conflicting instruction source authority for {model} {opcode}"
                    )
                result[key] = normalized
    return result


def instruction_source_authority(opcode, plc_model="FX3U"):
    """Return the data-backed authority rule for one CPU/opcode, if declared."""
    key = (
        str(plc_model or "").strip().upper(),
        str(opcode or "").strip().upper(),
    )
    rule = instruction_source_authorities().get(key)
    return copy.deepcopy(rule) if rule is not None else None


def authoritative_instruction_manual(opcode, plc_model="FX3U"):
    """Return only the preferred manual ID for ranking/filtering consumers."""
    rule = instruction_source_authority(opcode, plc_model)
    return str(rule["manual_id"]) if rule else None
