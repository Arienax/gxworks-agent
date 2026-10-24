"""Lossless field sharing for owner storage, not semantic fact aggregation.

An integer identifies the exact pair (field name, JSON value). Owners, fact IDs,
status values, applicability, list order and dependency identities never change.
The storage layer owns catalog hashing and rejects missing/tampered pool rows.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping

FIELD_SERIALIZATION = "exact-fact-fields-v1"
POOLED_FIELDS = frozenset({
    "scope", "conditions", "depends_on", "source_refs", "value_ref",
    "dimension", "status", "verification",
})
SUPPORTED_POOLED_FIELDS = POOLED_FIELDS | {"id"}


class FactFieldCodecError(ValueError):
    """The exact field transport is malformed or ambiguous."""


def _json_tree(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise FactFieldCodecError("JSON object keys must be strings")
        for item in value.values():
            _json_tree(item)
    elif isinstance(value, list):
        for item in value:
            _json_tree(item)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise FactFieldCodecError("Field transport requires JSON values")
    elif type(value) is float and not math.isfinite(value):
        raise FactFieldCodecError("Field transport requires finite numbers")


def _reference(value):
    if type(value) is not int or value <= 0:
        raise FactFieldCodecError("Field references must be positive integers")
    return value


def pack_fact_fields(fact, intern, *, fields=POOLED_FIELDS):
    """Pack exact fields using intern(field, detached_value) -> positive int.

    The callback must key by both field name and exact typed JSON value, not by
    Python equality (which equates True and 1). No original object is exposed.
    Optional field selection may include an exact repeated id string; identity
    remains owner-qualified and is restored verbatim, never merged or rewritten.
    """
    if not isinstance(fact, Mapping) or "field_refs" in fact:
        raise FactFieldCodecError("Invalid or already packed fact")
    _json_tree(fact)
    try:
        fields = frozenset(fields)
    except TypeError as error:
        raise FactFieldCodecError("Invalid selected field set") from error
    if not fields <= SUPPORTED_POOLED_FIELDS:
        raise FactFieldCodecError("Unknown selected pooled field")
    result = {key: copy.deepcopy(value) for key, value in fact.items() if key not in fields}
    refs = []
    for field in sorted(fields & fact.keys()):
        identity = _reference(intern(field, copy.deepcopy(fact[field])))
        if identity in refs:
            raise FactFieldCodecError("Different fields share the same reference")
        refs.append(identity)
    result["field_refs"] = refs
    return result


def unpack_fact_fields(fact, resolve):
    """Restore a detached exact fact using resolve(int) -> {field, value}."""
    if not isinstance(fact, Mapping) or "field_refs" not in fact:
        raise FactFieldCodecError("Missing packed field references")
    _json_tree(fact)
    refs = fact["field_refs"]
    if not isinstance(refs, list):
        raise FactFieldCodecError("Field references must be an array")
    result = {key: copy.deepcopy(value) for key, value in fact.items() if key != "field_refs"}
    seen = set()
    for item in refs:
        identity = _reference(item)
        if identity in seen:
            raise FactFieldCodecError("Duplicate field reference")
        seen.add(identity)
        row = resolve(identity)
        if not isinstance(row, Mapping) or set(row) != {"field", "value"}:
            raise FactFieldCodecError("Invalid or missing field catalog row")
        field = row["field"]
        if not isinstance(field, str) or field not in SUPPORTED_POOLED_FIELDS:
            raise FactFieldCodecError("Unknown pooled field")
        if field in result:
            raise FactFieldCodecError("Duplicate or inline-conflicting pooled field")
        _json_tree(row["value"])
        result[field] = copy.deepcopy(row["value"])
    return result
