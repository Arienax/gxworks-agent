"""Fact-only runtime storage; original evidence is an offline build input.

This module projects existing Core definitions. It does not infer PLC behavior
from prose. Unknown conditions withhold the entire affected fact, including its
dependants; presentation text can be discarded without changing typed values.
"""
from __future__ import annotations

from knowledge import index_access

from knowledge.record_projection import (
    MAX_TOP_K,
    chunk_result,
    select_ranked,
)

import copy
import json
import math
import re
import sqlite3

CORPUS_KIND = "authored_plc_facts_v1"
PUBLICATION_STATUS = "fact_representation_reviewed"
# This is a representation review, never a claim of a vendor copyright licence.
SOURCE_KEYS = frozenset({"id", "manual_id", "manual_number", "revision", "pdf_page",
                         "pdf_pages", "opcode", "target_model", "official_url"})
PRESENTATION_KEYS = frozenset({"text", "manual_text", "layout_text", "raw_text", "bbox",
    "source_span", "source_spans", "value_spans", "context_span", "row_span", "geometry_reference"})
_TOKEN = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]*\Z")
_TYPE_LABEL = re.compile(r"(?:signed|unsigned|binary|bit|word|float|double|16|32|64)(?:[- ](?:bit|word|integer|binary|float|16|32|64)){0,4}\Z", re.I)
_MEASUREMENT = re.compile(r"[+-]?\d+(?:\.\d+)?\s*(?:V|A|Hz|kHz|MHz|ms|us|s|mm|rpm)(?:\s+(?:AC|DC))?\Z", re.I)


class UnresolvedExpression(ValueError):
    pass


def typed_value(value):
    """Keep formal values exactly; reject unclassified linguistic conditions.

    A rejected child rejects the complete value. Never partially strip a
    semantic condition and leave the remaining claim looking unconditional.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise UnresolvedExpression("nonfinite_numeric_value")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if (not value or _TOKEN.fullmatch(value) or _TYPE_LABEL.fullmatch(value) or _MEASUREMENT.fullmatch(value)
                or re.fullmatch(r'-[A-Za-z0-9][A-Za-z0-9_-]*', value)):
            return value
        raise UnresolvedExpression("unclassified_expression")
    if isinstance(value, list):
        return [typed_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]+', key)
               for key in value if key not in PRESENTATION_KEYS):
            raise UnresolvedExpression('unclassified_field_name')
        return {key: typed_value(item) for key, item in value.items() if key not in PRESENTATION_KEYS}
    raise UnresolvedExpression("unsupported_value")


def source_reference(source):
    result = {key: copy.deepcopy(value) for key, value in source.items() if key in SOURCE_KEYS}
    for key in ('conditions', 'applicability'):
        if source.get(key):
            result[key] = typed_value(source[key])
    return result


def project_definition(entry, *, value_projection=None):
    """Project one literal model/form owner, preserving status and dependencies."""
    result = {key: copy.deepcopy(entry[key]) for key in (
        "vendor", "opcode", "target_model", "registry_member", "call_available", "support_status",
        "semantic_completeness", "gaps") if key in entry}
    accepted, withheld = [], []
    for fact in entry.get("facts", []):
        try:
            raw_value = (value_projection(entry, fact) if value_projection else fact.get('value', {}))
            value = ({'evidence_marker': 'model_badge_observed'} if fact.get('id') == 'native:model_mark'
                     and isinstance(raw_value, dict) and set(raw_value) == {'bbox'} else typed_value(raw_value))
            if fact.get("value") and not value:
                raise UnresolvedExpression("no_formal_value")
            # Scope/conditions are semantic, not presentation metadata.
            scope = typed_value(fact.get("scope", {}))
            conditions = typed_value(fact.get("conditions", []))
            sources = [source_reference(s) for s in fact.get('sources', [])]
        except UnresolvedExpression as error:
            withheld.append({"id": fact["id"], "reason": str(error), "status": fact.get("status", "unknown")})
            continue
        item = {key: copy.deepcopy(fact[key]) for key in (
            "id", "dimension", "status", "members", "depends_on", "verification") if key in fact}
        item.update(value=value, scope=scope, sources=sources)
        if conditions:
            item["conditions"] = conditions
        # Source precedence is semantic: a fact keeps the material that supports it,
        # with its own basis, instead of inheriting the instruction's preferred manual.
        if isinstance(fact.get("authority_decision"), dict):
            item["authority_decision"] = copy.deepcopy(fact["authority_decision"])
        accepted.append(item)
    # Removing a dependency cannot certify an orphaned result/effect.
    while True:
        ids = {f["id"] for f in accepted}
        orphaned = [f for f in accepted if set(f.get("depends_on", [])) - ids]
        if not orphaned:
            break
        for fact in orphaned:
            accepted.remove(fact)
            withheld.append({"id": fact["id"], "reason": "withheld_dependency", "status": fact.get("status", "unknown")})
    result["facts"] = accepted
    result["source_materials"] = []
    for source in entry.get("source_materials", []):
        material = {key: copy.deepcopy(source[key]) for key in (
            "manual_id", "revision", "pdf_pages", "primary_semantic_source") if key in source}
        material["document_id"] = ":".join([str(material.get("manual_id", "")),
            str(material.get("revision", "")), "pages=" + ",".join(map(str, material.get("pdf_pages", [])))])
        material["explicit_reference_ids"] = []
        result["source_materials"].append(material)
    result["uninterpreted_content"] = [{"reason": "private_source_not_distributed",
        "unit_count": sum(int(s.get("unit_count", 0)) for s in entry.get("uninterpreted_content", []))}]
    result["conflicts"] = [{"reason": "source_conflict_not_resolved"}] if entry.get("conflicts") else []
    result.setdefault("gaps", [])
    if withheld:
        result["gaps"] = list(dict.fromkeys([*result["gaps"], "linguistic_conditions_require_review"]))
    return result, withheld


def runtime_connection():
    path = index_access.index_path()
    if path is None:
        return None
    connection = index_access.connection(path, index_access.index_identity(path))
    meta = index_access.load_meta(connection, index_access.schema(connection))
    if meta.get('corpus_kind') == CORPUS_KIND:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        version = meta.get('fact_bundle_version')
        expected = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None)
        if version and expected and (version != expected or not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current()):
            raise ValueError('Runtime knowledge and Core fact bundles differ; reload the checking context')
    return connection if meta.get("corpus_kind") == CORPUS_KIND else None


_DEFAULT_CONNECTION = object()


def resolve(kind, targets, *, plc_model, task_type="analysis", connection=_DEFAULT_CONNECTION):
    """Exact projection over the same database and scope used by broad RRF."""
    if connection is _DEFAULT_CONNECTION:
        connection = runtime_connection()
    elif connection is not None:
        try:
            marker = connection.execute("SELECT value FROM meta WHERE key='corpus_kind'").fetchone()
        except sqlite3.Error:
            marker = None
        if not marker or marker[0] != CORPUS_KIND:
            return None
    if connection is None:
        return None  # Legacy research fixtures are separate from the runtime corpus.
    marker = connection.execute("SELECT value FROM meta WHERE key='fact_bundle_version'").fetchone()
    if marker:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        expected = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None)
        if expected and (marker[0] != expected or not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current()):
            raise ValueError('Runtime knowledge and Core fact bundles differ; reload the checking context')
    result = []
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(plc_model)
    members = profile.cpu.removesuffix("(RemoteI/O)").split("/") if profile else [plc_model]
    for target in targets or ():
        name = str(target.get("opcode") if isinstance(target, dict) else target).upper()
        if kind == "device":
            from plc.device_identity import canonical_reference_device
            name = canonical_reference_device(name)
        if kind == "error":
            name = name.removeprefix("0X").removesuffix("H")
        rows = connection.execute(
            "SELECT c.rowid AS _chunk_rowid,c.*,f.payload_json FROM facts f JOIN chunks c ON c.id=f.chunk_id "
            "WHERE f.kind=? AND f.target=? AND f.plc_model IN (" + ",".join("?" for _ in members) + ") "
            "ORDER BY CASE WHEN f.id LIKE 'definition:%' OR f.id LIKE 'function:definition:%' THEN 0 ELSE 1 END,f.id",
            (kind, name, *members)).fetchall()
        for row in rows:
            projected = chunk_result(row, {"corpus_kind": CORPUS_KIND}, index_access.index_path(), plc_model, task_type)
            if projected is not None:
                projected.update(json.loads(row["payload_json"]).get("metadata", {}))
                result.append(projected)
    return select_ranked(result, MAX_TOP_K)


def is_authored_record(row):
    return row.get("content_origin") in {"authored_fact", "original_case"}
