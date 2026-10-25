"""Fact-only runtime storage; original evidence is an offline build input.

This module projects existing Core definitions. It does not infer PLC behavior
from prose. Unknown conditions withhold the entire affected fact, including its
dependants; presentation text can be discarded without changing typed values.
"""
from __future__ import annotations

from knowledge import index_access

from knowledge.record_projection import (
    chunk_result,
    select_ranked,
)

import copy
import json
import math
from pathlib import Path
import re
import sqlite3

CORPUS_KIND = "authored_plc_facts_v1"
PUBLICATION_STATUS = "fact_representation_reviewed"
# This is a representation review, never a claim of a vendor copyright licence.
SOURCE_KEYS = frozenset({"id", "manual_id", "manual_number", "revision", "pdf_page",
                         "pdf_pages", "opcode", "target_model", "official_url",
                         "source_kind", "pdf_sha256", "page_text_sha256", "coverage"})
PRESENTATION_KEYS = frozenset({"text", "manual_text", "layout_text", "raw_text", "bbox",
    "source_span", "source_spans", "value_spans", "context_span", "row_span", "geometry_reference"})
_TOKEN = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]*\Z")
_OPERATOR_FORM = re.compile(r'(?:\*\*|[+\-*/&]|<>|<=|>=|=|<|>)(?:P)?(?:_U|_E)?\Z')
_TYPE_LABEL = re.compile(r"(?:signed|unsigned|binary|bit|word|float|double|16|32|64)(?:[- ](?:bit|word|integer|binary|float|16|32|64)){0,4}\Z", re.I)
_MEASUREMENT = re.compile(r"[+-]?\d+(?:\.\d+)?\s*(?:V|A|Hz|kHz|MHz|ms|us|s|mm|rpm)(?:\s+(?:AC|DC))?\Z", re.I)

# Formal literal spellings, not a PLC compiler/admission rule. Keep # confined
# to recognized bases/units instead of widening the generic technical token.
_BASED_LITERAL = re.compile(r'(?:2#[01]+(?:_[01]+)*|8#[0-7]+(?:_[0-7]+)*|16#[0-9A-Fa-f]+(?:_[0-9A-Fa-f]+)*)\Z')
_TIME_LITERAL = re.compile(r'T#[+-]?[0-9]+(?:\.[0-9]+)?(?:ms|s|m|h|d)\Z', re.I)


def _schema_technical_string(value, path):
    # Human-readable spellings are admitted only at their explicit technical
    # schema slots. Free prose elsewhere must still withhold the whole fact.
    if path == ('target_product',):
        return value == 'GX Works2'
    if path == ('constant_input_formal_classes',):
        return value == 'VAR_INPUT CONSTANT'
    if path == ('block_open',):
        return value == '(*'
    if path == ('block_close',):
        return value == '*)'
    if path == ('examples', 'string'):
        # Literal examples only: matched quotes, no control characters, physical
        # newlines or path/escape syntax. More dialect escapes need own review.
        if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
            return False
        quote, body = value[0], value[1:-1]
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 or char in {chr(92), '/', '\u2028', '\u2029'} for char in body):
            return False
        return quote not in body.replace(quote + quote, '')
    return False


class UnresolvedExpression(ValueError):
    pass


def typed_value(value, *, _path=()):
    """Keep formal values exactly; reject unclassified linguistic conditions.

    A rejected child rejects the complete value. Never partially strip a
    semantic condition and leave the remaining claim looking unconditional.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise UnresolvedExpression("nonfinite_numeric_value")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if (not value or _TOKEN.fullmatch(value) or _OPERATOR_FORM.fullmatch(value)
                or _TYPE_LABEL.fullmatch(value) or _MEASUREMENT.fullmatch(value)
                or _BASED_LITERAL.fullmatch(value) or _TIME_LITERAL.fullmatch(value)
                or _schema_technical_string(value, _path)
                or re.fullmatch(r'-[A-Za-z0-9][A-Za-z0-9_-]*', value)):
            return value
        raise UnresolvedExpression("unclassified_expression")
    if isinstance(value, list):
        return [typed_value(item, _path=_path) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]+', key)
               for key in value if key not in PRESENTATION_KEYS):
            raise UnresolvedExpression('unclassified_field_name')
        return {key: typed_value(item, _path=(*_path, key)) for key, item in value.items() if key not in PRESENTATION_KEYS}
    raise UnresolvedExpression("unsupported_value")


BUILD_LOCAL_ID = re.compile(r"\d+\Z")


def public_source_ref(source):
    """Build a public reference identity that carries no build-index row number."""
    manual_id = str(source.get("manual_id") or "")
    revision = str(source.get("revision") or "")
    pages = source.get("pdf_pages") or ([source["pdf_page"]] if source.get("pdf_page") is not None else [])
    parts = [part for part in (manual_id, revision) if part]
    if pages:
        parts.append("pages=" + ",".join(str(page) for page in pages))
    return ":".join(parts)


def source_reference(source):
    result = {key: copy.deepcopy(value) for key, value in source.items() if key in SOURCE_KEYS}
    if "source_ref" in source:
        reference = source["source_ref"]
        if (not isinstance(reference, str) or not _TOKEN.fullmatch(reference)
                or reference != public_source_ref(source)):
            raise UnresolvedExpression("invalid_public_source_reference")
        result["source_ref"] = reference
    identity = result.get("id")
    if identity is not None and BUILD_LOCAL_ID.fullmatch(str(identity).strip()):
        # A bare number is a row id of some development index; publish an identity
        # derived from the material instead of a number that means something else
        # in every other database.
        result.pop("id", None)
        reference = public_source_ref(source)
        if reference:
            result["source_ref"] = reference
    for key in ('conditions', 'applicability'):
        if source.get(key):
            result[key] = typed_value(source[key])
    return result


DECISION_KEYS = ("fact_id", "dimension", "opcode", "plc_model", "form", "status",
                 "selected_manual_id", "selected_revision", "reason", "policy",
                 "schema_version")
RULE_KEYS = ("rule_id", "manual_id", "revision", "authority", "fact_types", "policy")
CANDIDATE_KEYS = ("manual_id", "revision", "selected", "equivalent", "excluded_because")


def public_authority_decision(decision):
    """Publish the ruling, never the working material it was reached with.

    Raw text, geometry, private paths and build-local ids stay in the development
    data; what ships is the fact identity, the compared candidates, the selection and
    the rule that produced it.
    """
    if not isinstance(decision, dict):
        return None
    result = {key: copy.deepcopy(decision[key]) for key in DECISION_KEYS if key in decision}
    rule = decision.get("rule")
    if isinstance(rule, dict):
        result["rule"] = {key: copy.deepcopy(rule[key]) for key in RULE_KEYS if key in rule}
    candidates = []
    for item in decision.get("candidates") or ():
        if isinstance(item, dict):
            candidates.append({key: copy.deepcopy(item[key]) for key in CANDIDATE_KEYS
                               if key in item})
    if candidates:
        result["candidates"] = candidates
    return result


def project_definition(entry, *, value_projection=None):
    """Project one literal model/form owner, preserving status and dependencies."""
    result = {key: copy.deepcopy(entry[key]) for key in (
        "vendor", "opcode", "target_model", "registry_member", "call_available", "support_status",
        "semantic_completeness", "gaps", "call_admission_reason", "call_admission_details") if key in entry}
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
        ruling = public_authority_decision(fact.get("authority_decision"))
        if ruling:
            item["authority_decision"] = ruling
        note = fact.get("note")
        if isinstance(note, str) and note.strip():
            # Explanatory text: readable, explicitly not a machine behaviour rule.
            item["note"] = note.strip()
            item["note_kind"] = "explanation"
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
        # This is a page locator, not the original chapter identity: two chapters that
        # share a page range would collide, so the field says what it is.
        material["document_id"] = ":".join([str(material.get("manual_id", "")),
            str(material.get("revision", "")), "pages=" + ",".join(map(str, material.get("pdf_pages", [])))])
        material["location_kind"] = "page_locator"
        material["explicit_reference_ids"] = []
        result["source_materials"].append(material)
    result["uninterpreted_content"] = [{"reason": "private_source_not_distributed",
        "unit_count": sum(int(s.get("unit_count", 0)) for s in entry.get("uninterpreted_content", []))}]
    result["conflicts"] = [{"reason": "source_conflict_not_resolved"}] if entry.get("conflicts") else []
    result.setdefault("gaps", [])
    if withheld:
        result["gaps"] = list(dict.fromkeys([*result["gaps"], "linguistic_conditions_require_review"]))
    return result, withheld


def runtime_index_path(kind=None):
    """Keep owner knowledge on the Registry pair and other domains installed."""
    if kind in {'instruction', 'language'}:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        path = DEFAULT_INSTRUCTION_REGISTRY.selected_owner_database_path()
        if path is not None:
            return path
    return index_access.index_path()


def runtime_connection(kind=None):
    path = runtime_index_path(kind)
    if path is None:
        return None
    connection = index_access.connection(path, index_access.index_identity(path))
    meta = index_access.load_meta(connection, index_access.schema(connection))
    if kind in {'instruction', 'language'} and meta.get('corpus_kind') == CORPUS_KIND:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        version = meta.get('fact_bundle_version')
        expected = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None)
        if version and expected and (version != expected or not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current()):
            raise ValueError('Runtime knowledge and Core fact bundles differ; reload the checking context')
    return connection if meta.get("corpus_kind") == CORPUS_KIND else None


_DEFAULT_CONNECTION = object()


def model_corpus_status(plc_model):
    """Report missing model facts separately from an inaccessible fact index."""
    from plc.target_identity import target_members
    from knowledge.scope import scope_matches
    members = target_members(plc_model)
    result = {"plc_model": plc_model, "members": list(members)}
    try:
        path = runtime_index_path('instruction')
        if path is None:
            return {**result, "status": "index_unavailable"}
        connection = index_access.connection(path, index_access.index_identity(path))
        schema = index_access.schema(connection)
        if "facts" in schema:
            present = {row[0] for row in connection.execute(
                "SELECT DISTINCT plc_model FROM facts WHERE plc_model IN ("
                + ",".join("?" for _ in members) + ")", members)}
        elif "chunks" in schema and "plc_models" in schema["chunks"]["columns"]:
            # Explicit legacy evidence fixtures keep their own source scope.
            scopes = [row[0] for row in connection.execute("SELECT DISTINCT plc_models FROM chunks")]
            present = {member for member in members if any(scope_matches(scope, member) for scope in scopes)}
        else:
            return {**result, "status": "index_unavailable"}
    except (OSError, sqlite3.Error):
        index_access.close_thread_connection()
        return {**result, "status": "index_unavailable"}
    missing = [member for member in members if member not in present]
    return {**result, "status": "no_facts_for_model" if missing else "facts_available",
            "missing_members": missing}


def resolve(kind, targets, *, plc_model, task_type="analysis", connection=_DEFAULT_CONNECTION, domain_results=None):
    """Exact projection over the same database and scope used by broad RRF."""
    if domain_results is None:
        domain_results = connection is _DEFAULT_CONNECTION
    if connection is _DEFAULT_CONNECTION:
        connection = runtime_connection(kind)
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
    if marker and kind in {'instruction', 'language'}:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        expected = getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None)
        if expected and (marker[0] != expected or not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current()):
            raise ValueError('Runtime knowledge and Core fact bundles differ; reload the checking context')
    databases = connection.execute('PRAGMA database_list').fetchall()
    source_path = next((Path(row[2]) for row in databases if row[1] == 'main' and row[2]), None)
    result = []
    from plc.target_identity import reference_target_identity, target_members
    model = reference_target_identity(plc_model)
    if model is None:
        return []
    plc_model = model
    members = target_members(model)
    for target in targets or ():
        name = str(target.get("opcode") if isinstance(target, dict) else target).upper()
        if kind == "device":
            from plc.device_identity import canonical_reference_device
            name = canonical_reference_device(name)
        if kind == "error":
            from knowledge.text_matching import error_code_aliases
            names = error_code_aliases(name)
        else:
            names = (name,)
        rows = connection.execute(
            "SELECT c.rowid AS _chunk_rowid,c.*,f.payload_json FROM facts f JOIN chunks c ON c.id=f.chunk_id "
            "WHERE f.kind=? AND f.target IN (" + ",".join("?" for _ in names) + ") "
            "AND f.plc_model IN (" + ",".join("?" for _ in members) + ") "
            "ORDER BY CASE WHEN f.id LIKE 'definition:%' OR f.id LIKE 'function:definition:%' THEN 0 ELSE 1 END,f.id",
            (kind, *names, *members)).fetchall()
        from knowledge.exact_search_storage import validate_search_rows
        validate_search_rows(connection, rows)
        for row in rows:
            projected = chunk_result(row, {"corpus_kind": CORPUS_KIND}, source_path, plc_model, task_type, connection=connection, qualify_result=domain_results)
            if projected is not None:
                projected.update(json.loads(row["payload_json"]).get("metadata", {}))
                result.append(projected)
    # These rows are explicit identities and their applicable source variants.
    # Related-result limits belong to the supplemental retrieval lane; they
    # must never erase a later requested target or a CPU/source condition here.
    return select_ranked(result, len(result))


def is_authored_record(row):
    return row.get("content_origin") in {"authored_fact", "original_case"}
