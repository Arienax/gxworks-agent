# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""One-hop source annotations over candidates from the existing Retriever.

These are advisory composition evidence, not confirmed engineering requirements.
The caller supplies retrieved/current-source annotations, never model verdicts.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from functools import lru_cache
import json

from knowledge.evidence import evidence_record
from plc.composition import RELATION_KINDS


@lru_cache(maxsize=1)
def _annotations():
    from shared.paths import resource_path
    path = resource_path("knowledge/function_relations.json")
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["annotations"]


def annotate_source_records(records):
    """Attach only annotations whose manual/revision/page/opcode actually match.

    No synthetic evidence is inserted when the local source is unavailable.
    The annotations remain suggestions; native or process behavior is unknown.
    """
    result = deepcopy(records)
    for record in result:
        for annotation in _annotations():
            if all(record.get(key) == value for key, value in annotation["source_locator"].items()):
                record.setdefault("function_relations", []).extend(deepcopy(annotation["relations"]))
    return result


def expand_relations(candidates, records, *, plc_model, target_mode, retrieve, instruction_card, limit=12):
    if type(limit) is not int or not 0 <= limit <= 64:
        raise ValueError("relation expansion limit must be between 0 and 64")
    seeds = {c["id"] for c in candidates}
    known = {c["id"]: c for c in candidates}
    relations, omissions, additions, seen = [], [], [], {}
    lookups = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for raw in record.get("function_relations", []):
            if not isinstance(raw, Mapping):
                omissions.append({"reason": "invalid_relation_annotation"})
                continue
            identity = raw.get("id")
            if not isinstance(identity, str) or not identity:
                continue
            signature = {k: deepcopy(raw.get(k)) for k in ("kind", "from", "to", "plc_models", "target_modes", "preconditions")}
            if identity in seen:
                if seen[identity] != signature:
                    relations[:] = [r for r in relations if r["id"] != identity]
                    if not any(o.get("id") == identity and o["reason"] == "relation_identity_conflict" for o in omissions):
                        omissions.append({"id": identity, "reason": "relation_identity_conflict"})
                continue
            seen[identity] = signature
            reason = None
            if raw.get("kind") not in RELATION_KINDS or not all(isinstance(raw.get(k), str) and raw[k] for k in ("from", "to")):
                reason = "invalid_relation_endpoints"
            elif not record.get("id") or not isinstance(raw.get("plc_models"), list) or plc_model not in raw["plc_models"]:
                reason = "exact_cpu_source_missing"
            elif not isinstance(raw.get("target_modes"), list) or target_mode not in raw["target_modes"]:
                reason = "language_scope_mismatch"
            elif not isinstance(raw.get("preconditions"), list) or not raw["preconditions"] or any(not isinstance(p, str) or not p for p in raw["preconditions"]):
                reason = "relation_preconditions_missing"
            elif not ({raw["from"], raw["to"]} & seeds):
                reason = "outside_one_hop_scope"
            elif len(relations) >= limit:
                reason = "relation_expansion_limit"
            if reason:
                omissions.append({"id": identity, "reason": reason})
                continue
            pending = []
            for endpoint in (raw["from"], raw["to"]):
                if endpoint in known:
                    continue
                if not endpoint.startswith("instruction:") or target_mode == "fbd":
                    reason = "endpoint_not_in_current_catalog"
                    break
                opcode = endpoint.split(":", 1)[1]
                if opcode not in lookups:
                    # Bound related lookups too; metadata cannot cause arbitrary
                    # recursive retrieval or hundreds of requests.
                    if len(lookups) >= limit:
                        reason = "relation_lookup_limit"
                        break
                    hits = retrieve("PLC " + opcode, plc_model=plc_model, task_type="generate",
                                    top_k=8, char_budget=12000, source_lanes=("fact",))
                    source = next((r for r in hits if str(r.get("instruction_opcode") or "").upper() == opcode), None)
                    lookups[opcode] = instruction_card(opcode, plc_model, source) if source else None
                card = lookups[opcode]
                if card is None:
                    reason = "related_endpoint_source_unavailable"
                    break
                pending.append(card)
            if reason:
                omissions.append({"id": identity, "reason": reason})
                continue
            for card in pending:
                if card["id"] not in known:
                    card = deepcopy(card)
                    card["functions"] = ["relation_neighbor"]
                    known[card["id"]] = card
                    additions.append(card)
            relations.append({k: deepcopy(raw[k]) for k in ("id", "kind", "from", "to", "preconditions", "plc_models", "target_modes")})
            relations[-1].update(confirmation="candidate", source_status="source_annotation",
                                 binding_status="unverified", sources=[evidence_record(record)])
    return {"relations": relations, "relation_omissions": omissions, "additions": additions,
            "expansion": {"depth": 1, "limit": limit, "lookups": len(lookups), "seed_ids": sorted(seeds)}}
