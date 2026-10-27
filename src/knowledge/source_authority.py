# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Data-backed authority for choosing the primary official instruction source."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from collections.abc import Mapping

from shared.paths import resource_path

_AUTHORITY_RESOURCE = "resources/instructions/mitsubishi/instruction_source_authority.json"
_SOURCE_MANIFEST = "knowledge/runtime/manifest.json"


def _manual_catalog():
    payload = json.loads(resource_path(_SOURCE_MANIFEST).read_text(encoding="utf-8"))
    manuals = payload.get("manuals") if isinstance(payload, Mapping) else None
    if not isinstance(manuals, list):
        raise ValueError("knowledge source manifest has no manuals list")
    result = {}
    for row in manuals:
        if not isinstance(row, Mapping):
            continue
        identity = str(row.get("manual_id") or row.get("id") or "").strip()
        if not identity:
            continue
        models = row.get("plc_models") or ()
        if isinstance(models, str):
            models = [value.strip() for value in models.split(",") if value.strip()]
        result[identity] = {**row, "plc_models": models}
    return result


@lru_cache(maxsize=1)
def instruction_source_authorities():
    """Return validated immutable-by-convention instruction source rules."""
    path = resource_path(_AUTHORITY_RESOURCE)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
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
            "fact_types": list(_normalized_fact_types(row)),
            "revision": str(row.get("revision") or "").strip() or None,
            "authority": str(
                row.get("authority") or "primary_instruction_semantics"
            ),
            "reason": str(row.get("reason") or ""),
            "plc_models": list(models),
            "opcodes": list(opcodes),
        }
        for model in models:
            for opcode in opcodes:
                for fact_type in normalized["fact_types"]:
                    key = (model, opcode, fact_type)
                    existing = result.get(key)
                    if existing is not None and existing["manual_id"] != manual_id:
                        raise ValueError(
                            "conflicting instruction source authority for "
                            f"{model} {opcode} {fact_type}"
                        )
                    result[key] = normalized
    return result


def instruction_source_authority(opcode, plc_model="FX3U", fact_type=None):
    """Return the data-backed authority rule for one CPU/opcode, if declared.

    Without a fact type the rule that speaks for every fact type of that form is
    returned, which keeps the historical whole-instruction answer available while
    fact-type rules stay separate.
    """
    model = str(plc_model or "").strip().upper()
    form = str(opcode or "").strip().upper()
    declared = instruction_source_authorities()
    if fact_type is None:
        rule = declared.get((model, form, ALL_FACT_TYPES))
        if rule is None:
            specific = [value for (cpu, opcode, _kind), value in declared.items()
                        if cpu == model and opcode == form]
            rule = specific[0] if len({item["manual_id"] for item in specific}) == 1 else None
        return copy.deepcopy(rule) if rule is not None else None
    rule = declared.get((model, form, str(fact_type).strip()))
    if rule is None:
        rule = declared.get((model, form, ALL_FACT_TYPES))
    return copy.deepcopy(rule) if rule is not None else None


ADJUDICATION_SCHEMA_VERSION = 1
ADJUDICATION_POLICY = "fact-source-adjudication-v1"

#: How one fact type's supporting material was decided. ``selected`` and
#: ``equivalent`` carry an answer; the other three say why there is none.
ADJUDICATION_STATUSES = (
    "selected",
    "equivalent",
    "unadjudicated",
    "conflict",
    "source_missing",
)

ALL_FACT_TYPES = "*"


def _normalized_fact_types(row):
    declared = row.get("fact_types") or row.get("fact_type") or ()
    if isinstance(declared, str):
        declared = [declared]
    values = tuple(str(value).strip() for value in declared if str(value).strip())
    return values or (ALL_FACT_TYPES,)


def instruction_source_rules(opcode, plc_model, fact_type=None):
    """Return the adjudication rules that apply to one form, CPU and fact type.

    A rule may name the fact types it decides; a rule without ``fact_types`` speaks
    for every fact type of that form, which is how the existing per-instruction
    rules keep working. A rule may also pin the material revision it was audited
    against, so a second revision of the same manual is not silently equivalent.
    """
    model = str(plc_model or "").strip().upper()
    form = str(opcode or "").strip().upper()
    wanted = str(fact_type).strip() if fact_type else None
    rules = []
    seen = set()
    for rule in instruction_source_authorities().values():
        if id(rule) in seen:
            continue
        seen.add(id(rule))
        if model not in rule["plc_models"] or form not in rule["opcodes"]:
            continue
        declared = tuple(rule.get("fact_types") or (ALL_FACT_TYPES,))
        if wanted is not None and ALL_FACT_TYPES not in declared and wanted not in declared:
            continue
        rules.append(rule)
    specific = [rule for rule in rules
                if ALL_FACT_TYPES not in tuple(rule.get("fact_types") or (ALL_FACT_TYPES,))]
    return copy.deepcopy(specific if specific and wanted else rules)


def _candidate_identity(candidate):
    return (
        str(candidate.get("manual_id") or "").strip(),
        str(candidate.get("revision") or "").strip().upper(),
    )


def _candidate_value(candidate):
    if "value" in candidate:
        return json.dumps(candidate.get("value"), sort_keys=True, ensure_ascii=True, default=str)
    return str(candidate.get("fact_id") or candidate.get("source_id") or "")


def adjudicate_fact_sources(*, opcode, plc_model, fact_type, candidates, rules=None):
    """Decide which candidate material supports one fact type of one form.

    ``candidates`` are the materials that actually carry the fact; ``rules`` are the
    audited precedence rules (defaults to the ones declared for this form and CPU).
    The answer names the selected source, keeps every candidate with the reason it
    was not selected, and reports the rule and policy version that produced it.

    Selecting a primary source here says nothing about whether the fact itself was
    verified (``source_verified`` stays its own status), and an unresolved source
    question never turns the instruction into a non-callable one.
    """
    model = str(plc_model or "").strip().upper()
    form = str(opcode or "").strip().upper()
    declared = [dict(item) for item in (candidates or ()) if isinstance(item, Mapping)]
    applicable = (instruction_source_rules(form, model, fact_type)
                  if rules is None else [dict(rule) for rule in rules])
    decision = {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "policy": ADJUDICATION_POLICY,
        "opcode": form,
        "plc_model": model,
        "fact_type": str(fact_type),
        "status": "unadjudicated",
        "selected": None,
        "rule": None,
        "candidates": [],
    }

    def record(status, *, selected=None, rule=None, reason=""):
        decision["status"] = status
        decision["selected"] = selected
        decision["rule"] = rule
        decision["reason"] = reason
        return decision

    if not declared:
        return record("source_missing", reason="no_candidate_material")

    for rule in applicable:
        manual_id = str(rule.get("manual_id") or "").strip()
        pinned = str(rule.get("revision") or "").strip().upper()
        matching = [item for item in declared if _candidate_identity(item)[0] == manual_id]
        for item in declared:
            identity_manual, identity_revision = _candidate_identity(item)
            if identity_manual != manual_id:
                item["excluded_because"] = "rule_names_other_manual"
            elif pinned and identity_revision != pinned:
                item["excluded_because"] = "rule_names_other_revision"
            else:
                item.pop("excluded_because", None)
        usable = [item for item in matching
                  if not pinned or _candidate_identity(item)[1] == pinned]
        decision["candidates"] = declared
        rule_record = {
            "rule_id": str(rule.get("rule_id") or f"{form}:{manual_id}"),
            "manual_id": manual_id,
            "revision": rule.get("revision"),
            "authority": rule.get("authority"),
            "fact_types": list(rule.get("fact_types") or (ALL_FACT_TYPES,)),
            "policy": ADJUDICATION_POLICY,
        }
        if usable:
            usable[0]["selected"] = True
            return record("selected", selected=dict(usable[0]), rule=rule_record)
        # The rule points at a material this fact's candidates do not contain: that is
        # a gap in the materials, not permission to promote another manual.
        if pinned and matching:
            return record("source_missing", rule=rule_record,
                          reason="rule_revision_not_available")
        return record("source_missing", rule=rule_record,
                      reason="rule_manual_not_available")

    values = {_candidate_value(item) for item in declared}
    identities = {_candidate_identity(item) for item in declared}
    decision["candidates"] = declared
    if len(declared) == 1:
        declared[0]["selected"] = True
        return record("selected", selected=dict(declared[0]),
                      reason="only_candidate_material")
    if len(values) == 1 and len(identities) > 1:
        for item in declared:
            item["equivalent"] = True
        return record("equivalent", reason="candidates_agree_on_the_value")
    if len(values) > 1:
        return record("conflict", reason="candidates_disagree_and_no_rule_decides")
    return record("unadjudicated", reason="no_rule_for_this_fact_type")


def authoritative_instruction_manual(opcode, plc_model="FX3U"):
    """Return only the preferred manual ID for ranking/filtering consumers."""
    rule = instruction_source_authority(opcode, plc_model)
    return str(rule["manual_id"]) if rule else None
