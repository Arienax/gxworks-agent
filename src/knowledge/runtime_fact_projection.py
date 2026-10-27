# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Conservative semantic publication, separate from complete private evidence.

Only facts belonging to the same literal owner and having the same technical
payload, status, verification, applicability and dependency semantics merge.
The input is never edited. The returned private sidecar records lineage and
removed audit coordinates; the immutable input remains the complete evidence
archive, not a runtime payload or something to reconstruct from a receipt.

Source and value pooling are optional, lossless transport steps. Consumers resolve one
owner at a time; it must not grant applicability or verification inheritance.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from types import MappingProxyType

METHOD = "runtime-semantic-projection-v1"
SOURCE_SERIALIZATION = "fact-source-pool-v1"
VALUE_SERIALIZATION = "fact-value-pool-v1"
# Deliberately ROOT-only. A field with the same name inside a rule/condition is
# not known to be an audit coordinate and must remain in the technical value.
AUDIT_VALUE_KEYS = frozenset({
    "source_page", "source_line", "clause_index",
    "complete_clause_review", "full_clause_review",
})
_SCOPE_SETS = frozenset({"models", "forms", "languages", "versions"})
# Everything else on a source (including unknown fields) participates in the
# semantic grouping key. In particular conditional provenance cannot silently
# turn into an unconditional union of supporting references.
_SOURCE_LOCATORS = frozenset({
    "id", "source_id", "source_ref", "manual_id", "manual_number", "revision",
    "pdf_page", "pdf_pages", "official_url", "source_kind", "pdf_sha256",
    "page_text_sha256",
})


class RuntimeProjectionError(ValueError):
    """Publication is unsafe or its source/dependency transport is malformed."""


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise RuntimeProjectionError("Projection requires finite JSON values") from error


def _json_tree(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RuntimeProjectionError("Projection requires string object keys")
        for item in value.values():
            _json_tree(item)
    elif isinstance(value, list):
        for item in value:
            _json_tree(item)
    elif value is not None and type(value) not in {str, bool, int, float}:
        raise RuntimeProjectionError("Projection requires JSON values")
    _json(value) if not isinstance(value, (Mapping, list)) else None


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _freeze_json(value):
    # All containers become tagged immutable tuples. Leaves retain exact JSON
    # types; booleans, integers and floats are not canonicalized together.
    if isinstance(value, dict):
        return ("object", tuple((key, _freeze_json(item)) for key, item in value.items()))
    if isinstance(value, list):
        return ("array", tuple(_freeze_json(item) for item in value))
    return value


def _thaw_json(value):
    if not isinstance(value, tuple):
        return value
    kind, contents = value
    if kind == "object":
        return {key: _thaw_json(item) for key, item in contents}
    if kind == "array":
        return [_thaw_json(item) for item in contents]
    return contents


class VerifiedCatalog(Mapping):
    """Per-load validated, owned content snapshots; never a trust flag/cache.

    Each lookup thaws fresh containers while sharing immutable keys/leaves.
    Caller mutation cannot alter the validated, privately owned frozen tree.
    """
    __slots__ = ("__snapshots",)

    def __init__(self, catalog):
        if not isinstance(catalog, Mapping):
            raise RuntimeProjectionError("Invalid content catalog")
        snapshots = {}
        for identity, value in catalog.items():
            if not isinstance(identity, str) or not isinstance(value, Mapping):
                raise RuntimeProjectionError("Invalid catalog entry")
            _json_tree(value)
            encoded = _json(value)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != identity:
                raise RuntimeProjectionError("Altered content catalog: " + identity)
            # Freeze the exact bytes we validated, not caller-owned containers.
            # Retain immutable strings across hydrations instead of JSON-decoding
            # every occurrence (which would allocate repeated keys and tokens).
            snapshots[identity] = _freeze_json(json.loads(encoded))
        object.__setattr__(self, "_VerifiedCatalog__snapshots", MappingProxyType(snapshots))

    def __setattr__(self, name, value):
        raise AttributeError("VerifiedCatalog is immutable")

    def __getitem__(self, identity):
        return _thaw_json(self.__snapshots[identity])

    def __iter__(self):
        return iter(self.__snapshots)

    def __len__(self):
        return len(self.__snapshots)


def _owner(entry):
    owner = (entry.get("vendor", "mitsubishi"), entry.get("target_model"), entry.get("opcode"))
    if any(not isinstance(item, str) or not item for item in owner):
        raise RuntimeProjectionError("Literal vendor/model/form owner required")
    return owner


def _sources(fact):
    sources = fact.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(source, Mapping) for source in sources):
        raise RuntimeProjectionError("Fact sources must be a list of objects")
    for source in sources:
        _json_tree(source)
    return sources


def _body(fact):
    result = {key: copy.deepcopy(value) for key, value in fact.items()
              if key not in {"id", "sources", "depends_on"}}
    value = result.get("value", {})
    if not isinstance(value, Mapping):
        raise RuntimeProjectionError("Fact technical value must be an object")
    removed = {key: value[key] for key in AUDIT_VALUE_KEYS if key in value}
    result["value"] = {key: item for key, item in value.items() if key not in AUDIT_VALUE_KEYS}
    scope = result.get("scope")
    if isinstance(scope, Mapping):
        for key in _SCOPE_SETS & scope.keys():
            values = scope[key]
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise RuntimeProjectionError("Scoped applicability must contain string lists")
            scope[key] = sorted(set(values))
    decision = result.get("authority_decision")
    if isinstance(decision, Mapping) and decision.get("fact_id"):
        if decision["fact_id"] != fact["id"]:
            raise RuntimeProjectionError("Authority decision belongs to another fact")
        decision["fact_id"] = {"self_fact_identity": True}
    return result, removed


def _source_qualifiers(fact):
    # Empty source locators do not constrain the technical assertion. Source
    # conditions, applicability, owner selectors and unknown metadata do.
    return sorted({_json({key: value for key, value in source.items()
                          if key not in _SOURCE_LOCATORS})
                   for source in _sources(fact)} - {"{}"})


def _project_owner(entry, external_id_references=()):
    owner = _owner(entry)
    facts = entry.get("facts", [])
    if not isinstance(facts, list):
        raise RuntimeProjectionError("Owner facts must be a list")
    by_id, bodies, removed, dependencies, reverse = {}, {}, {}, {}, defaultdict(list)
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise RuntimeProjectionError("Fact must be an object")
        _json_tree(fact)
        identity = fact.get("id")
        if not isinstance(identity, str) or not identity or identity in by_id:
            raise RuntimeProjectionError("Duplicate or missing owner-qualified fact ID")
        if "source_refs" in fact or "value_ref" in fact:
            raise RuntimeProjectionError("Resolve pooled sources and values before semantic projection")
        if not isinstance(fact.get("dimension"), str) or not fact["dimension"]:
            raise RuntimeProjectionError("Fact dimension required")
        deps = fact.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) or not dep for dep in deps):
            raise RuntimeProjectionError("Dependency IDs must be a list of strings")
        if len(set(deps)) != len(deps):
            raise RuntimeProjectionError("Duplicate dependency IDs")
        by_id[identity] = fact
        dependencies[identity] = deps
        bodies[identity], removed[identity] = _body(fact)
    for identity, deps in dependencies.items():
        if set(deps) - by_id.keys():
            raise RuntimeProjectionError("Dangling owner-local fact dependency: " + identity)
        for dep in deps:
            reverse[dep].append(identity)
    # Unknown embedded references cannot be safely rewritten as generic JSON.
    # Preserve their target IDs, and the known runtime-significant PWM prefix.
    protected = {identity for identity in by_id if identity.startswith("pwm-formal:")}
    protected.update(set(external_id_references) & by_id.keys())
    def protect_embedded(value):
        if isinstance(value, str) and value in by_id:
            protected.add(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                protect_embedded(key)
                protect_embedded(item)
        elif isinstance(value, list):
            for item in value:
                protect_embedded(item)
    for identity, body in bodies.items():
        protect_embedded(body)
        protect_embedded(_sources(by_id[identity]))
    protect_embedded({key: value for key, value in entry.items() if key != "facts"})
    # Bottom-up dependency equivalence avoids recursive depth limits and the
    # unsafe collapse of a cycle into a self-dependency. Cycles fail explicitly.
    pending = {identity: len(deps) for identity, deps in dependencies.items()}
    ready = [identity for identity, count in pending.items() if not count]
    heapq.heapify(ready)
    classes, class_for, dependency_classes = {}, {}, {}
    while ready:
        identity = heapq.heappop(ready)
        dep_classes = tuple(sorted({class_for[dep] for dep in dependencies[identity]}))
        signature = _json({"body": bodies[identity], "dependencies": dep_classes,
                           "source_qualifiers": _source_qualifiers(by_id[identity]),
                           "protected_identity": identity if identity in protected else None})
        # Hash only controls key length. Keep the signature to reject a collision.
        key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        group = classes.setdefault(key, {"signature": signature, "ids": []})
        if group["signature"] != signature:
            raise RuntimeProjectionError("Semantic identity hash collision")
        group["ids"].append(identity)
        class_for[identity] = key
        dependency_classes[key] = dep_classes
        for dependant in reverse[identity]:
            pending[dependant] -= 1
            if pending[dependant] == 0:
                heapq.heappush(ready, dependant)
    if len(class_for) != len(facts):
        raise RuntimeProjectionError("Cyclic fact dependencies require explicit review")
    representatives = {key: min(group["ids"]) for key, group in classes.items()}
    output, lineage = [], []
    for key, group in sorted(classes.items(), key=lambda item: representatives[item[0]]):
        identity = representatives[key]
        fact = copy.deepcopy(bodies[identity])
        fact["id"] = identity
        decision = fact.get("authority_decision")
        if isinstance(decision, Mapping) and decision.get("fact_id"):
            decision["fact_id"] = identity
        deps = sorted(representatives[dep] for dep in dependency_classes[key])
        if deps or "depends_on" in by_id[identity]:
            fact["depends_on"] = deps
        source_union = {}
        for old_id in sorted(group["ids"]):
            original = by_id[old_id]
            for source in _sources(original):
                source_union[_json(source)] = source
            lineage.append({"input_id": old_id, "output_id": identity,
                            "input_sha256": _hash(original),
                            "removed_value_fields": copy.deepcopy(removed[old_id]),
                            "sources": copy.deepcopy(_sources(original))})
        # A singleton retains exact source ordering for existing reviewed
        # identity gates; only an actual merge canonicalizes its source union.
        fact["sources"] = (copy.deepcopy(_sources(by_id[identity])) if len(group["ids"]) == 1 else
                           [copy.deepcopy(source_union[token]) for token in sorted(source_union)])
        output.append(fact)
    result = {key: copy.deepcopy(value) for key, value in entry.items() if key != "facts"}
    result["facts"] = output
    return result, {"vendor": owner[0], "target_model": owner[1], "opcode": owner[2],
                    "facts": sorted(lineage, key=lambda item: item["input_id"])}, {
        "input_facts": len(facts), "output_facts": len(output),
        "merged_facts": len(facts) - len(output),
        "audit_fields_moved": sum(len(row) for row in removed.values()),
        "input_dependencies": sum(len(deps) for deps in dependencies.values()),
        "output_dependencies": sum(len(fact.get("depends_on", [])) for fact in output),
    }


def project_runtime_definitions(payload):
    """Return (runtime, private_lineage, receipt), without editing evidence.

    The sidecar is PRIVATE: it contains original source locations and removed
    audit data. Keep it beside the original unmodified evidence document.
    No status ranking, model inheritance or strongest-per-dimension selection
    is performed. Unknown top-level/value fields remain part of equivalence.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("entries"), list):
        raise RuntimeProjectionError("Expanded definition entries are required")
    if any(key in payload for key in ("source_catalog", "source_serialization", "value_catalog", "value_serialization")):
        raise RuntimeProjectionError("Resolve pooled sources and values before semantic projection")
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("facts", []), list):
            raise RuntimeProjectionError("Each definition owner must contain a fact list")
    entries, private_entries, counts, owners = [], [], Counter(), set()
    external_id_references = set()
    known_ids = {fact.get("id") for entry in payload["entries"] if isinstance(entry, Mapping)
                 for fact in entry.get("facts", []) if isinstance(fact, Mapping)
                 and isinstance(fact.get("id"), str)}
    def collect_external_strings(value):
        if isinstance(value, str) and value in known_ids:
            external_id_references.add(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                collect_external_strings(key)
                collect_external_strings(item)
        elif isinstance(value, list):
            for item in value:
                collect_external_strings(item)
    collect_external_strings({key: value for key, value in payload.items() if key != "entries"})
    # Unknown JSON references may point across owners. Pin their exact targets
    # globally rather than guessing their scope or rewriting arbitrary strings.
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping):
            continue
        collect_external_strings({key: value for key, value in entry.items() if key != "facts"})
        for fact in entry.get("facts", []):
            if not isinstance(fact, Mapping):
                continue
            for key, value in fact.items():
                if key in {"id", "depends_on"}:
                    continue
                if key == "authority_decision" and isinstance(value, Mapping):
                    value = {name: item for name, item in value.items()
                             if name != "fact_id" or item != fact.get("id")}
                collect_external_strings(key)
                collect_external_strings(value)
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping):
            raise RuntimeProjectionError("Definition entry must be an object")
        owner = _owner(entry)
        if owner in owners:
            raise RuntimeProjectionError("Duplicate definition owner")
        owners.add(owner)
        projected, private, count = _project_owner(entry, external_id_references)
        entries.append(projected)
        private_entries.append(private)
        counts.update(count)
    entries.sort(key=_owner)
    private_entries.sort(key=_owner)
    runtime = {key: copy.deepcopy(value) for key, value in payload.items() if key != "entries"}
    runtime["entries"] = entries
    private = {"method": METHOD, "evidence_retention": "original_input_preserved_separately",
               "audit_value_keys": sorted(AUDIT_VALUE_KEYS), "entries": private_entries}
    receipt = {"method": METHOD, "owners": len(owners), **dict(counts),
               "status_promotion": False, "scope_widening": False,
               "dependency_policy": "owner_local_equivalent_DAG_dependencies",
               "source_policy": "exact_union_with_equal_source_qualifiers"}
    return runtime, private, receipt


def intern_runtime_sources(payload, *, intern_values=False):
    """Pool exact sources and optionally values, without semantic regrouping."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("entries"), list):
        raise RuntimeProjectionError("Expanded definition entries are required")
    if any(key in payload for key in ("source_catalog", "source_serialization", "value_catalog", "value_serialization")):
        raise RuntimeProjectionError("Sources or values are already pooled or ambiguous")
    catalog, value_catalog, entries = {}, {}, []
    for entry in payload["entries"]:
        result = {key: copy.deepcopy(value) for key, value in entry.items() if key != "facts"}
        result["facts"] = []
        for fact in entry.get("facts", []):
            if "source_refs" in fact or "value_ref" in fact:
                raise RuntimeProjectionError("Ambiguous source/value references")
            excluded = {"sources", "value"} if intern_values and "value" in fact else {"sources"}
            projected = {key: copy.deepcopy(value) for key, value in fact.items() if key not in excluded}
            if intern_values and "value" in fact:
                value = fact["value"]
                if not isinstance(value, Mapping):
                    raise RuntimeProjectionError("Fact technical value must be an object")
                _json_tree(value)
                value_id = _hash(value)
                if value_id in value_catalog and _json(value_catalog[value_id]) != _json(value):
                    raise RuntimeProjectionError("Value identity hash collision")
                if value_id not in value_catalog:
                    value_catalog[value_id] = copy.deepcopy(value)
                projected["value_ref"] = value_id
            refs = []
            for source in _sources(fact):
                identity = _hash(source)
                if identity in catalog and _json(catalog[identity]) != _json(source):
                    raise RuntimeProjectionError("Source identity hash collision")
                if identity not in catalog:
                    catalog[identity] = copy.deepcopy(source)
                refs.append(identity)
            projected["source_refs"] = refs
            result["facts"].append(projected)
        entries.append(result)
    result = {key: copy.deepcopy(value) for key, value in payload.items() if key != "entries"}
    result.update(entries=entries, source_serialization=SOURCE_SERIALIZATION,
                  source_catalog={key: catalog[key] for key in sorted(catalog)})
    if intern_values:
        result.update(value_serialization=VALUE_SERIALIZATION,
                      value_catalog={key: value_catalog[key] for key in sorted(value_catalog)})
    return result


def resolve_fact_sources(fact, catalog, value_catalog=None):
    """Hydrate one detached fact, checking exact content-addressed references."""
    if not isinstance(fact, Mapping) or not isinstance(catalog, Mapping):
        raise RuntimeProjectionError("Invalid source reference transport")
    if "source_refs" not in fact or "sources" in fact:
        raise RuntimeProjectionError("Missing or ambiguous pooled source references")
    refs = fact["source_refs"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise RuntimeProjectionError("Invalid source references")
    sources = []
    for ref in refs:
        source = catalog.get(ref)
        if not isinstance(source, Mapping):
            raise RuntimeProjectionError("Missing or altered pooled source: " + ref)
        if type(catalog) is not VerifiedCatalog:
            _json_tree(source)
            if _hash(source) != ref:
                raise RuntimeProjectionError("Missing or altered pooled source: " + ref)
            source = copy.deepcopy(source)
        sources.append(source)
    result = {key: copy.deepcopy(value) for key, value in fact.items()
              if key not in {"source_refs", "value_ref"}}
    result["sources"] = sources
    if "value_ref" in fact:
        ref = fact["value_ref"]
        if "value" in fact or not isinstance(ref, str) or not isinstance(value_catalog, Mapping):
            raise RuntimeProjectionError("Missing or ambiguous pooled value")
        value = value_catalog.get(ref)
        if not isinstance(value, Mapping):
            raise RuntimeProjectionError("Missing pooled value: " + ref)
        if type(value_catalog) is not VerifiedCatalog:
            _json_tree(value)
            if _hash(value) != ref:
                raise RuntimeProjectionError("Altered pooled value: " + ref)
            value = copy.deepcopy(value)
        result["value"] = value
    return result


def expand_runtime_sources(payload):
    """Compatibility/verification helper; production readers can hydrate owners."""
    if (not isinstance(payload, Mapping) or payload.get("source_serialization") != SOURCE_SERIALIZATION
            or not isinstance(payload.get("source_catalog"), Mapping)
            or not isinstance(payload.get("entries"), list)):
        raise RuntimeProjectionError("Invalid pooled definition document")
    catalog = payload["source_catalog"]
    value_catalog = payload.get("value_catalog")
    if ((value_catalog is not None or "value_serialization" in payload)
            and (payload.get("value_serialization") != VALUE_SERIALIZATION
                 or not isinstance(value_catalog, Mapping))):
        raise RuntimeProjectionError("Invalid pooled value document")
    result = {key: copy.deepcopy(value) for key, value in payload.items()
              if key not in {"source_catalog", "source_serialization", "value_catalog", "value_serialization", "entries"}}
    result["entries"] = []
    for entry in payload["entries"]:
        out = {key: copy.deepcopy(value) for key, value in entry.items() if key != "facts"}
        out["facts"] = [resolve_fact_sources(fact, catalog, value_catalog) for fact in entry.get("facts", [])]
        result["entries"].append(out)
    return result
