# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Conservative semantic publication, separate from complete private evidence.

Only facts belonging to the same literal owner and having the same technical
payload, status, verification, applicability and dependency semantics merge.
The input is never edited. The private sidecar records removed research data,
withheld facts and the inverse changes needed to restore each complete owner.

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
STRICT_OWNER_POLICY = "strict_verified_facts_only-v1"
COMPATIBILITY_POLICY = "compatibility_semantic_deduplication-v1"
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
_PRIVATE_OWNER_FIELDS = frozenset({
    "superseded_facts", "source_materials", "uninterpreted_content",
    "semantic_completeness", "inventory_gaps", "gaps", "conflicts",
    "core_unresolved_aspects",
})
_PRIVATE_VALUE_FIELDS = frozenset({
    "superseded_facts", "source_materials", "uninterpreted_content",
    "semantic_completeness", "inventory_gaps", "gaps", "conflicts",
    "core_unresolved_aspects", "unresolved_items", "full_source_relation_complete",
    "technical_relation_resolved",
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


def _research_marker(key):
    """Recognize field names that report research state, not PLC behavior."""
    if not isinstance(key, str):
        return False
    if key in {"source_verified", "source_checked"}:
        return False
    lowered = key.lower()
    return (key in _PRIVATE_VALUE_FIELDS or lowered.endswith("_established")
            or lowered.endswith("_verified"))


def _private_owner_field(key):
    return key in _PRIVATE_OWNER_FIELDS or _research_marker(key)


def _marker_blocks_fact(key, value):
    """Return a withholding reason only for explicit unresolved evidence."""
    if key == "unresolved_items" and value:
        return "unresolved_items"
    if key == "full_source_relation_complete" and value is not True:
        return "full_source_relation_incomplete"
    if key in {"gaps", "conflicts", "core_unresolved_aspects"} and value:
        return "research_gap_or_conflict"
    if key == "technical_relation_resolved" and value is False:
        return "technical_relation_unresolved"
    return None


def _strict_fact_blockers(fact):
    """Return strict publication gates without interpreting PLC semantics.

    Only explicit source-review states and dedicated unresolved capability
    declarations gate a fact. In particular, ``*_established=False`` and
    ``*_verified=False`` are retained as private research annotations and do
    not negate an independently source-verified rule.
    """
    blockers = []
    status = fact.get("status")
    verification = fact.get("verification")
    if status == "candidate_evidence":
        blockers.append("candidate_evidence")
    elif status != "source_verified":
        blockers.append("status_not_source_verified")
    if verification == "not_performed":
        blockers.append("verification_not_performed")
    elif verification != "source_checked":
        blockers.append("verification_not_source_checked")
    sources = _sources(fact)
    if not sources or any(not source for source in sources):
        blockers.append("source_missing")

    value = fact.get("value")
    if isinstance(value, Mapping):
        if (value.get("capability") == "not_established"
                or value.get("core_execution_capability") == "not_established"
                or (fact.get("dimension") == "execution.core_execution_capability"
                    and value.get("executor_capability") == "not_established")):
            blockers.append("capability_not_established")
    return blockers


def _is_contextual_documented_requirement(fact):
    """Recognize an explicit runtime rule whose unresolved context is a limitation.

    These markers describe what a caller still has to establish (for example,
    whether a selected module and address range are valid). Keeping them with
    the source-verified predicate preserves the actual constraint and prevents
    the projection from making it look fully resolved. This narrow exception
    does not cover incomplete source relations or general research gaps.
    """
    value = fact.get("value")
    if (fact.get("dimension") != "constraints.documented_requirements"
            or not isinstance(value, Mapping)
            or not isinstance(value.get("predicate"), Mapping)):
        return False
    required = value.get("required_context")
    unresolved = value.get("unresolved_items")
    if (not isinstance(required, list) or not required
            or any(not isinstance(item, Mapping)
                   or not isinstance(item.get("field"), str) or not item["field"]
                   for item in required)
            or not isinstance(unresolved, list) or not unresolved
            or any(not isinstance(item, str) or not item for item in unresolved)):
        return False
    return (value.get("selection_domain_not_module_existence_proof") is True
            or value.get("branch_selection_is_not_hardware_validation") is True)


def _is_partial_documented_hardware_path(fact):
    """Keep explicit, source-checked hardware paths usable without certifying them.

    Some authored requirements contain concrete path limits but also state that
    the complete source relation or the selected hardware has not been
    validated. The limits remain useful rejection constraints; the incomplete
    relation marker and field-verification notes stay private. This deliberately
    recognizes only the structured positioning path shape, not arbitrary
    incomplete documented requirements.
    """
    value = fact.get("value")
    if (fact.get("dimension") != "constraints.documented_requirements"
            or fact.get("status") != "source_verified"
            or fact.get("verification") != "source_checked"
            or not _sources(fact)
            or not isinstance(value, Mapping)
            or value.get("kind") != "structured_positioning_output_path"
            or value.get("full_source_relation_complete") is not False
            or value.get("hardware_validation_complete") is not False
            or value.get("executor_capability") != "not_established"):
        return False
    scope = fact.get("scope")
    dependencies = fact.get("depends_on")
    if (not isinstance(scope, Mapping)
            or any(not isinstance(scope.get(axis), list) or len(scope[axis]) != 1
                   for axis in ("models", "forms", "languages"))
            or scope["languages"][0] not in {"st", "fbd", "ladder"}
            or not isinstance(dependencies, list) or not dependencies):
        return False
    if any(value.get(key) for key in
           ("unresolved_items", "gaps", "conflicts", "core_unresolved_aspects")):
        return False
    if value.get("technical_relation_resolved") is False:
        return False
    bindings = value.get("bindings")
    if not isinstance(bindings, Mapping):
        return False
    for name in ("pulse_output", "direction_signal", "frequency"):
        binding = bindings.get(name)
        if not isinstance(binding, Mapping) or not isinstance(binding.get("name"), str) or not binding["name"]:
            return False
    paths = value.get("paths")
    if (not isinstance(paths, list) or len(paths) < 2
            or len({path.get("path") for path in paths if isinstance(path, Mapping)}) != len(paths)):
        return False
    for path in paths:
        if (not isinstance(path, Mapping) or not isinstance(path.get("path"), str)
                or not isinstance(path.get("output_type"), str)
                or not isinstance(path.get("pulse_output_devices"), list)
                or not path["pulse_output_devices"]
                or any(not isinstance(device, str) or not device
                       for device in path["pulse_output_devices"])):
            return False
        frequency = path.get("frequency_Hz")
        if (not isinstance(frequency, Mapping)
                or type(frequency.get("minimum")) not in {int, float}
                or type(frequency.get("maximum")) not in {int, float}
                or frequency["minimum"] > frequency["maximum"]):
            return False
    return True


def _move_research_fields(value, *, preserve_unresolved_items=False,
                          preserve_partial_hardware_path=False):
    """Remove only named research fields; preserve conditions and scope trees."""
    removed, blockers = [], []

    def visit(node, path):
        if isinstance(node, dict):
            for key in list(node):
                if key in {"conditions", "scope"}:
                    continue
                item = node[key]
                if key == "unresolved_items" and preserve_unresolved_items and not path:
                    # This is an explicit runtime-context limitation attached
                    # to a documented predicate, not a research checklist.
                    continue
                if _research_marker(key):
                    removed.append({"path": [*path, key], "value": copy.deepcopy(item)})
                    reason = _marker_blocks_fact(key, item)
                    if (key == "full_source_relation_complete" and item is False
                            and not path and preserve_partial_hardware_path):
                        reason = None
                    if reason:
                        blockers.append(reason)
                    del node[key]
                else:
                    visit(item, [*path, key])
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, [*path, index])

    visit(value, [])
    return removed, sorted(set(blockers))


def _body(fact):
    result = {key: copy.deepcopy(value) for key, value in fact.items()
              if key not in {"id", "sources", "depends_on"}}
    value_present = "value" in result
    value = result.get("value", {})
    if not isinstance(value, Mapping):
        raise RuntimeProjectionError("Fact technical value must be an object")
    removed = {key: copy.deepcopy(value[key]) for key in AUDIT_VALUE_KEYS if key in value}
    technical_value = {key: copy.deepcopy(item) for key, item in value.items()
                       if key not in AUDIT_VALUE_KEYS}
    moved_paths, blockers = _move_research_fields(
        technical_value,
        preserve_unresolved_items=_is_contextual_documented_requirement(fact),
        preserve_partial_hardware_path=_is_partial_documented_hardware_path(fact))
    if value_present:
        result["value"] = technical_value
    removed_fact_fields = {}
    for key in list(result):
        if key in {"value", "scope", "conditions", "status", "verification"}:
            continue
        if _research_marker(key):
            removed_fact_fields[key] = copy.deepcopy(result.pop(key))
            reason = _marker_blocks_fact(key, removed_fact_fields[key])
            if reason:
                blockers.append(reason)
    scope = result.get("scope")
    if isinstance(scope, Mapping):
        for key in _SCOPE_SETS & scope.keys():
            values = scope[key]
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise RuntimeProjectionError("Scoped applicability must contain string lists")
    decision = result.get("authority_decision")
    if isinstance(decision, Mapping) and decision.get("fact_id"):
        if decision["fact_id"] != fact["id"]:
            raise RuntimeProjectionError("Authority decision belongs to another fact")
        decision["fact_id"] = {"self_fact_identity": True}
    return result, removed, moved_paths, removed_fact_fields, sorted(set(blockers))


def _source_qualifiers(fact):
    # Empty source locators do not constrain the technical assertion. Source
    # conditions, applicability, owner selectors and unknown metadata do.
    return sorted({_json({key: value for key, value in source.items()
                          if key not in _SOURCE_LOCATORS})
                   for source in _sources(fact)} - {"{}"})


def _project_owner(entry, external_id_references=(), *, strict=False):
    owner = _owner(entry)
    facts = entry.get("facts", [])
    if not isinstance(facts, list):
        raise RuntimeProjectionError("Owner facts must be a list")
    by_id, bodies, removed, removed_paths, removed_fact_fields, blockers = {}, {}, {}, {}, {}, {}
    dependencies, reverse, input_indexes = {}, defaultdict(list), {}
    for index, fact in enumerate(facts):
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
        input_indexes[identity] = index
        dependencies[identity] = deps
        (bodies[identity], removed[identity], removed_paths[identity],
         removed_fact_fields[identity], blockers[identity]) = _body(fact)
    for identity, deps in dependencies.items():
        if set(deps) - by_id.keys():
            raise RuntimeProjectionError("Dangling owner-local fact dependency: " + identity)
        for dep in deps:
            reverse[dep].append(identity)

    # A fact whose own relation is unresolved, plus every fact depending on it,
    # stays private. Other independent facts from this owner remain publishable.
    private_reasons = {identity: list(reasons) for identity, reasons in blockers.items() if reasons}
    if strict:
        for identity, fact in by_id.items():
            private_reasons.setdefault(identity, []).extend(_strict_fact_blockers(fact))
        private_reasons = {identity: sorted(set(reasons)) for identity, reasons in private_reasons.items()
                           if reasons}
    pending_private = list(private_reasons)
    while pending_private:
        unresolved = pending_private.pop()
        for dependant in reverse[unresolved]:
            if dependant not in private_reasons:
                private_reasons[dependant] = ["depends_on_private_fact"]
                pending_private.append(dependant)
            elif "depends_on_private_fact" not in private_reasons[dependant]:
                private_reasons[dependant].append("depends_on_private_fact")
    active_ids = set(by_id) - private_reasons.keys()
    private_facts = [{"input_index": input_indexes[identity], "reasons": sorted(private_reasons[identity]),
                      "fact": copy.deepcopy(by_id[identity])}
                     for identity in sorted(private_reasons, key=input_indexes.__getitem__)]

    # Unknown embedded references cannot be safely rewritten as generic JSON.
    # Preserve their target IDs, and the known runtime-significant PWM prefix.
    protected = {identity for identity in active_ids if identity.startswith("pwm-formal:")}
    protected.update(set(external_id_references) & active_ids)
    def protect_embedded(value):
        if isinstance(value, str) and value in active_ids:
            protected.add(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                protect_embedded(key)
                protect_embedded(item)
        elif isinstance(value, list):
            for item in value:
                protect_embedded(item)
    for identity in active_ids:
        body = bodies[identity]
        protect_embedded(body)
        protect_embedded(_sources(by_id[identity]))
    protect_embedded({key: value for key, value in entry.items()
                      if key != "facts" and not _private_owner_field(key)})
    # Bottom-up dependency equivalence avoids recursive depth limits and the
    # unsafe collapse of a cycle into a self-dependency. Cycles fail explicitly.
    pending = {identity: len([dep for dep in deps if dep in active_ids])
               for identity, deps in dependencies.items() if identity in active_ids}
    ready = [identity for identity, count in pending.items() if not count]
    heapq.heapify(ready)
    classes, class_for, dependency_classes = {}, {}, {}
    while ready:
        identity = heapq.heappop(ready)
        dep_classes = tuple(sorted({class_for[dep] for dep in dependencies[identity] if dep in active_ids}))
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
            if dependant not in pending:
                continue
            pending[dependant] -= 1
            if pending[dependant] == 0:
                heapq.heappush(ready, dependant)
    if len(class_for) != len(active_ids):
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
            row = {"input_id": old_id, "output_id": identity,
                            "input_index": input_indexes[old_id],
                            "input_sha256": _hash(original),
                            "removed_value_fields": copy.deepcopy(removed[old_id]),
                            "removed_value_paths": copy.deepcopy(removed_paths[old_id]),
                            "removed_fact_fields": copy.deepcopy(removed_fact_fields[old_id]),
                            "sources_present": "sources" in original,
                            "sources": copy.deepcopy(_sources(original)),
                            "depends_on_present": "depends_on" in original,
                            "depends_on": copy.deepcopy(original.get("depends_on", [])),
                            "value_present": "value" in original}
            lineage.append(row)
        # A singleton retains exact source ordering for existing reviewed
        # identity gates; only an actual merge canonicalizes its source union.
        fact["sources"] = (copy.deepcopy(_sources(by_id[identity])) if len(group["ids"]) == 1 else
                           [copy.deepcopy(source_union[token]) for token in sorted(source_union)])
        output.append(fact)
    private_metadata = {key: copy.deepcopy(entry[key]) for key in entry if _private_owner_field(key)}
    result = {key: copy.deepcopy(value) for key, value in entry.items()
              if key != "facts" and not _private_owner_field(key)}
    result["facts"] = output
    policy = STRICT_OWNER_POLICY if strict else COMPATIBILITY_POLICY
    return result, {"method": METHOD, "publication_policy": policy,
                    "vendor": owner[0], "target_model": owner[1], "opcode": owner[2],
                    "facts": sorted(lineage, key=lambda item: item["input_id"]),
                    "private_facts": private_facts, "private_metadata": private_metadata,
                    "admission_blockers": [
                        {"fact_id": item["fact"]["id"],
                         "dimension": item["fact"].get("dimension"),
                         "reasons": copy.deepcopy(item["reasons"])}
                        for item in private_facts]}, {
        "input_facts": len(facts), "output_facts": len(output),
        "merged_facts": len(active_ids) - len(output), "withheld_facts": len(private_facts),
        "audit_fields_moved": sum(len(row) for row in removed.values()),
        "research_fields_moved": sum(len(row) for row in removed_paths.values())
            + sum(len(row) for row in removed.values())
            + sum(len(row) for row in removed_fact_fields.values()) + len(private_metadata),
        "input_dependencies": sum(len(deps) for deps in dependencies.values()),
        "output_dependencies": sum(len(fact.get("depends_on", [])) for fact in output),
    }


def project_runtime_owner(entry, *, external_id_references=()):
    """Strictly publish verified facts for one owner with a private inverse sidecar.

    Facts without a source-verified/source-checked state, source references, or
    a resolved dedicated capability stay private together with their dependants.
    The returned admission blockers identify each withheld fact and reason so
    the compiler can refresh call admission for the affected owner.
    """
    if not isinstance(entry, Mapping) or not isinstance(entry.get("facts", []), list):
        raise RuntimeProjectionError("Definition owner must contain a fact list")
    runtime, sidecar, counts = _project_owner(entry, external_id_references, strict=True)
    return runtime, sidecar, {"method": METHOD, "publication_policy": STRICT_OWNER_POLICY,
                              "owner": list(_owner(entry)), **counts,
                              "admission_blockers": copy.deepcopy(sidecar["admission_blockers"])}


def _restore_path(root, path, value):
    if not isinstance(path, list) or not path:
        raise RuntimeProjectionError("Invalid private value path")
    node = root
    for part in path[:-1]:
        if isinstance(part, int) and not isinstance(part, bool) and isinstance(node, list):
            if part < 0 or part >= len(node):
                raise RuntimeProjectionError("Private value path is out of range")
            node = node[part]
        elif isinstance(part, str) and isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            raise RuntimeProjectionError("Private value path no longer matches runtime content")
    leaf = path[-1]
    if isinstance(leaf, str) and isinstance(node, dict):
        if leaf in node:
            raise RuntimeProjectionError("Private value field collides with runtime content")
        node[leaf] = copy.deepcopy(value)
    elif (isinstance(leaf, int) and not isinstance(leaf, bool) and isinstance(node, list)
          and 0 <= leaf < len(node)):
        raise RuntimeProjectionError("Private value path no longer matches runtime content")
    else:
        raise RuntimeProjectionError("Invalid private value path")


def restore_runtime_research(runtime_entry, sidecar):
    """Restore the exact owner input from its runtime projection and private sidecar.

    This restores only values removed or normalized by this projection. It
    validates literal owner identity, projected fact bodies, source unions and
    dependency remapping; it does not use a stored source hash as a trust gate.
    """
    if not isinstance(runtime_entry, Mapping) or not isinstance(sidecar, Mapping):
        raise RuntimeProjectionError("Runtime owner and private sidecar are required")
    if (sidecar.get("method") != METHOD
            or sidecar.get("publication_policy") not in {STRICT_OWNER_POLICY, COMPATIBILITY_POLICY}
            or _owner(runtime_entry) != _owner(sidecar)):
        raise RuntimeProjectionError("Private sidecar owner or method does not match runtime owner")
    _json_tree(runtime_entry)
    _json_tree(sidecar)
    runtime_facts = runtime_entry.get("facts", [])
    rows = sidecar.get("facts")
    private_facts = sidecar.get("private_facts")
    private_metadata = sidecar.get("private_metadata")
    if (not isinstance(runtime_facts, list) or not isinstance(rows, list)
            or not isinstance(private_facts, list) or not isinstance(private_metadata, Mapping)):
        raise RuntimeProjectionError("Private sidecar is missing reversible owner content")
    output_by_id = {}
    for fact in runtime_facts:
        if not isinstance(fact, Mapping) or not isinstance(fact.get("id"), str) or not fact["id"]:
            raise RuntimeProjectionError("Runtime fact identity is invalid")
        if fact["id"] in output_by_id:
            raise RuntimeProjectionError("Duplicate projected fact identity")
        output_by_id[fact["id"]] = fact
    rows_by_output, input_to_output, input_facts, input_indexes = defaultdict(list), {}, set(), set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RuntimeProjectionError("Private fact lineage is invalid")
        input_id, output_id = row.get("input_id"), row.get("output_id")
        index = row.get("input_index")
        if (not isinstance(input_id, str) or not input_id or output_id not in output_by_id
                or not isinstance(index, int) or isinstance(index, bool) or index < 0
                or input_id in input_facts or index in input_indexes):
            raise RuntimeProjectionError("Private fact lineage identity does not match runtime content")
        if (not isinstance(row.get("sources_present"), bool)
                or not isinstance(row.get("sources"), list)
                or not isinstance(row.get("depends_on_present"), bool)
                or not isinstance(row.get("depends_on"), list)
                or not isinstance(row.get("value_present"), bool)):
            raise RuntimeProjectionError("Private fact inverse fields are incomplete")
        input_facts.add(input_id)
        input_indexes.add(index)
        input_to_output[input_id] = output_id
        rows_by_output[output_id].append(row)
    private_by_id = {}
    for item in private_facts:
        if not isinstance(item, Mapping) or not isinstance(item.get("fact"), Mapping):
            raise RuntimeProjectionError("Withheld private fact is invalid")
        fact = item["fact"]
        identity, index = fact.get("id"), item.get("input_index")
        if (not isinstance(identity, str) or not identity or identity in input_facts
                or identity in private_by_id or not isinstance(index, int)
                or isinstance(index, bool) or index < 0 or index in input_indexes):
            raise RuntimeProjectionError("Withheld private fact identity is invalid")
        if not isinstance(item.get("reasons"), list) or any(not isinstance(x, str) for x in item["reasons"]):
            raise RuntimeProjectionError("Withheld private fact reason is invalid")
        private_by_id[identity] = item
        input_facts.add(identity)
        input_indexes.add(index)
    if input_indexes != set(range(len(input_indexes))):
        raise RuntimeProjectionError("Private sidecar does not cover the complete input fact order")
    if set(rows_by_output) != set(output_by_id):
        raise RuntimeProjectionError("Private sidecar does not cover projected runtime facts")

    restored_by_index = {}
    for output_id, group_rows in rows_by_output.items():
        projected = output_by_id[output_id]
        sources_by_token = {}
        for row in group_rows:
            for source in row["sources"]:
                sources_by_token[_json(source)] = source
        expected_sources = (copy.deepcopy(group_rows[0]["sources"]) if len(group_rows) == 1 else
                            [copy.deepcopy(sources_by_token[token]) for token in sorted(sources_by_token)])
        if projected.get("sources", []) != expected_sources:
            raise RuntimeProjectionError("Projected sources no longer match private lineage")
        representative = next((row for row in group_rows if row["input_id"] == output_id), None)
        if representative is None:
            raise RuntimeProjectionError("Private lineage lacks the projected representative")
        expected_dependencies = sorted({input_to_output[dependency]
                                        for row in group_rows
                                        for dependency in row["depends_on"]
                                        if dependency in input_to_output})
        for row in group_rows:
            if any(dependency not in input_to_output for dependency in row["depends_on"]):
                raise RuntimeProjectionError("Retained fact depends on withheld or missing private content")
        if projected.get("depends_on", []) != expected_dependencies:
            raise RuntimeProjectionError("Projected dependencies no longer match private lineage")
        expected_depends_present = bool(expected_dependencies) or representative["depends_on_present"]
        if ("depends_on" in projected) != expected_depends_present:
            raise RuntimeProjectionError("Projected dependency field presence no longer matches lineage")

        projected_body_source = copy.deepcopy(dict(projected))
        projected_body_source.setdefault("id", output_id)
        normalized_projected, projected_audit, projected_paths, projected_fact_fields, projected_blockers = _body(
            projected_body_source)
        if projected_audit or projected_paths or projected_fact_fields or projected_blockers:
            raise RuntimeProjectionError("Runtime entry still contains private research fields")
        normalized_projected.pop("id", None)
        normalized_projected.pop("sources", None)
        normalized_projected.pop("depends_on", None)

        for row in group_rows:
            original = copy.deepcopy(dict(projected))
            original.pop("sources", None)
            original.pop("depends_on", None)
            original["id"] = row["input_id"]
            decision = original.get("authority_decision")
            if isinstance(decision, dict) and decision.get("fact_id") == output_id:
                decision["fact_id"] = row["input_id"]
            if row["value_present"]:
                value = original.get("value")
                if not isinstance(value, dict):
                    raise RuntimeProjectionError("Projected value no longer matches private lineage")
                for removed in row.get("removed_value_paths", []):
                    if not isinstance(removed, Mapping) or "value" not in removed:
                        raise RuntimeProjectionError("Private value inverse path is invalid")
                    _restore_path(value, removed.get("path"), removed["value"])
                for key, value_item in row.get("removed_value_fields", {}).items():
                    if key in value:
                        raise RuntimeProjectionError("Private audit field collides with runtime content")
                    value[key] = copy.deepcopy(value_item)
            elif "value" in original:
                raise RuntimeProjectionError("Projected value presence no longer matches private lineage")
            for key, value_item in row.get("removed_fact_fields", {}).items():
                if key in original:
                    raise RuntimeProjectionError("Private fact field collides with runtime content")
                original[key] = copy.deepcopy(value_item)
            if row["sources_present"]:
                original["sources"] = copy.deepcopy(row["sources"])
            if row["depends_on_present"]:
                original["depends_on"] = copy.deepcopy(row["depends_on"])
            original_body, _, _, _, _ = _body(original)
            original_body.pop("id", None)
            if _json(original_body) != _json(normalized_projected):
                raise RuntimeProjectionError("Private fact changes do not reconstruct runtime body")
            restored_by_index[row["input_index"]] = original
    for identity, item in private_by_id.items():
        restored_by_index[item["input_index"]] = copy.deepcopy(dict(item["fact"]))

    result = {key: copy.deepcopy(value) for key, value in runtime_entry.items()}
    for key in list(result):
        if _private_owner_field(key):
            raise RuntimeProjectionError("Runtime owner still contains private research metadata")
    for key, value in private_metadata.items():
        if not _private_owner_field(key):
            raise RuntimeProjectionError("Private sidecar contains an unrecognized owner field")
        result[key] = copy.deepcopy(value)
    result["facts"] = [restored_by_index[index] for index in range(len(restored_by_index))]
    return result


def project_runtime_definitions(payload):
    """Compatibility semantic deduplication for already reviewed definitions.

    The sidecar is PRIVATE: it contains original source locations, removed
    research fields and withheld facts. Use restore_runtime_research for an
    exact per-owner reconstruction before the next incremental compilation.
    This legacy whole-payload entry point preserves candidate and unverified
    facts for existing deduplication callers. Strict publication must use
    :func:`project_runtime_owner` per owner. Unknown top-level/value fields
    remain part of equivalence.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("entries"), list):
        raise RuntimeProjectionError("Expanded definition entries are required")
    if any(key in payload for key in ("source_catalog", "source_serialization", "value_catalog", "value_serialization")):
        raise RuntimeProjectionError("Resolve pooled sources and values before semantic projection")
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("facts", []), list):
            raise RuntimeProjectionError("Each definition owner must contain a fact list")
    entries, private_entries, counts, owners, admission_blockers = [], [], Counter(), set(), []
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
        collect_external_strings({key: value for key, value in entry.items()
                                  if key != "facts" and not _private_owner_field(key)})
        for fact in entry.get("facts", []):
            if (not isinstance(fact, Mapping) or not isinstance(fact.get("id"), str)
                    or not fact.get("id")):
                continue
            body, _, _, _, _ = _body(fact)
            for key, value in body.items():
                collect_external_strings(key)
                collect_external_strings(value)
    for entry in payload["entries"]:
        if not isinstance(entry, Mapping):
            raise RuntimeProjectionError("Definition entry must be an object")
        owner = _owner(entry)
        if owner in owners:
            raise RuntimeProjectionError("Duplicate definition owner")
        owners.add(owner)
        projected, private, owner_counts = _project_owner(
            entry, external_id_references=external_id_references, strict=False)
        owner_receipt = {"method": METHOD, "publication_policy": COMPATIBILITY_POLICY,
                         "owner": list(owner), **owner_counts,
                         "admission_blockers": copy.deepcopy(private["admission_blockers"])}
        entries.append(projected)
        private_entries.append(private)
        counts.update({key: value for key, value in owner_receipt.items()
                       if key not in {"method", "publication_policy", "owner", "admission_blockers"}})
        admission_blockers.extend(copy.deepcopy(owner_receipt["admission_blockers"]))
    entries.sort(key=_owner)
    private_entries.sort(key=_owner)
    runtime = {key: copy.deepcopy(value) for key, value in payload.items() if key != "entries"}
    runtime["entries"] = entries
    private = {"method": METHOD, "publication_policy": COMPATIBILITY_POLICY,
               "evidence_retention": "owner_input_restorable_from_sidecar",
               "audit_value_keys": sorted(AUDIT_VALUE_KEYS), "entries": private_entries}
    receipt = {"method": METHOD, "publication_policy": COMPATIBILITY_POLICY,
               "owners": len(owners), **dict(counts),
               "admission_blockers": admission_blockers,
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
