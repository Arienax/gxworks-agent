# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Task-relevant capability hints and explicit facts shared by context adapters."""
from __future__ import annotations

import copy
import json
import re

from knowledge.evidence import KnowledgeContext, estimate_tokens
from plc.maintainability import SELECTION_POLICY, SELECTION_POLICY_VERSION
from shared.model_context import technical_block, technical_value, technical_context_text


_BLOCK = re.compile(r"(?ms)(?:^Reference role:[^\n]*\n)?^\[KNOWLEDGE ([^\n]*)\]\n.*?^\[/KNOWLEDGE\]")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _candidate_brief(candidate, target_mode):
    brief = {k: copy.deepcopy(candidate[k]) for k in
             ("kind", "name", "purpose", "interface", "execution", "unknown", "functions")}
    if candidate.get("st_call"):
        brief["st_call"] = copy.deepcopy(candidate["st_call"])
    if candidate.get("target_call"):
        brief["target_call"] = copy.deepcopy(candidate["target_call"])
    if target_mode == "fbd" and candidate["kind"] == "project_callable":
        brief["interface"] = {"catalog_reference": candidate["sources"][0]["identity"]}
    return technical_value(brief)


def _call_contract_units(records, *, plc_model, explicit):
    """Prioritize complete declarations already relevant to this retrieval.

    The Core catalogue owns signatures; the application selects whole units.
    No reference program, device allocation or expected result is inserted.
    """
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    names = [str(t.get("opcode") or "") for t in explicit]
    # Only declared evidence targets qualify. Incidental mnemonics in examples,
    # navigation or comparisons must not expand the implementation catalogue.
    names += [str(row.get("instruction_opcode") or "") for row in records]
    forms = [DEFAULT_INSTRUCTION_REGISTRY.resolve_form(op, cpu=plc_model)
             for op in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()]
    st_names = {form.spec.st_call["name"]: form.opcode for form in forms
                if form and form.spec.supports_cpu(plc_model) and form.spec.st_call}
    units, seen = [], set()
    for name in names:
        opcode = st_names.get(name, name)
        if opcode in seen:
            continue
        seen.add(opcode)
        contract = DEFAULT_INSTRUCTION_REGISTRY.describe_call(opcode, cpu=plc_model)
        if contract:
            identity = "call-contract:" + plc_model + ":" + opcode
            units.append((identity, technical_block(contract)))
    return units


def public_discovery(value):
    """One compact projection for Web and MCP; full retrieval stays in records."""
    if not isinstance(value, dict):
        return None
    keys = ("version", "target_mode", "functions", "policy_version", "selection_scope", "repair_scope", "included_candidates",
            "omitted_candidates", "included_evidence", "omitted_evidence", "replaced_evidence",
            "gaps", "tokens", "model_calls", "deduplicated_units", "experiment_arm",
            "composition", "relations", "composition_units", "included_composition_units", "omitted_composition_units", "expansion",
            "relation_omissions", "included_call_contracts", "omitted_call_contracts")
    result = {k: copy.deepcopy(value[k]) for k in keys if k in value}
    included = set(value.get("included_candidates", []))
    result["candidates"] = [{k: copy.deepcopy(c[k]) for k in
        ("id", "kind", "name", "purpose", "execution", "unknown", "functions", "sources") if k in c}
        for c in value.get("candidates", []) if c.get("id") in included]
    delivered = set(value.get("included_composition_units", []))
    if "composition_units" in result:
        result["composition_units"] = [r for r in result["composition_units"] if r.get("id") in delivered]
    if "relations" in result:
        result["relations"] = [r for r in result["relations"] if "relation:" + r["id"] in delivered]
    from application.generation_support import public_generation_value
    return public_generation_value(result)


def augment_generation_knowledge(requirement, knowledge="", *, plc_model="FX3U", target_mode="ladder",
                                 confirmed_spec=None, catalog=(), token_budget=None, repair_scope=None):
    """Deliver relevant complete discovery units; legacy token_budget is ignored."""
    from knowledge.structured_facts import structured_fact_targets
    from knowledge.instruction_facts import retrieve_instruction_facts
    manifest = copy.deepcopy(getattr(knowledge, "manifest", {}))
    targets = (manifest.get("structured_facts") or {}).get("targets") or structured_fact_targets(
        requirement, confirmed_spec, plc_model=plc_model)
    explicit = targets.get("instructions") or []
    from application.composition_context import composition_experiment
    experiment = composition_experiment()
    selected = (confirmed_spec or {}).get("selected_approach") or {}
    selection_scope = "repair_baseline" if repair_scope else "selected_approach" if selected else "task_options"
    try:
        from knowledge.retriever import discover_capabilities
        options = ({"relation_limit": experiment.relation_limit,
                    "relation_records": manifest.get("records", [])} if experiment.arm in {"C", "D"} else {})
        discovery = discover_capabilities("" if selected or repair_scope else requirement, plc_model=plc_model, target_mode=target_mode,
                                          explicit_targets=explicit, catalog=catalog, **options)
    except Exception as error:
        # Discovery is advisory; existing exact facts and acceptance stay usable.
        discovery = {"version": "functional-discovery-v1", "functions": [], "candidates": [],
                     "queries": [], "gaps": [{"reason": "discovery_unavailable", "error_type": type(error).__name__}],
                     "model_calls": 0}
    discovery.update(policy_version=SELECTION_POLICY_VERSION, selection_scope=selection_scope,
                     included_candidates=[], omitted_candidates=[],
                     included_evidence=[], omitted_evidence=[], replaced_evidence=[])
    if repair_scope:
        discovery["repair_scope"] = copy.deepcopy(repair_scope)
    base = technical_context_text(knowledge)
    from application.context_compiler import deduplicate_evidence_text
    base, duplicate_units = deduplicate_evidence_text(base)
    discovery["deduplicated_units"] = duplicate_units
    source_tokens = estimate_tokens(base)
    call_units = _call_contract_units(manifest.get("records", []), plc_model=plc_model, explicit=explicit)
    discovery.update(included_call_contracts=[], omitted_call_contracts=[])
    wanted_policy = estimate_tokens(SELECTION_POLICY)
    parts, model_units = [], {}
    for identity, block in call_units:
        parts.append(block)
        model_units[identity] = block
        discovery["included_call_contracts"].append(identity)
    if base:
        parts.append(base)
    parts.append(SELECTION_POLICY)

    # Coverage-first ordering, then alternatives in the existing retrieval order.
    candidates = discovery["candidates"]
    # A complete project catalogue is a lookup table, not task evidence. Select
    # before rendering; explicit calls are unlimited, advisory alternatives use
    # the same cap as the FBD catalogue selector.
    from application.fbd_context import DISCOVERY_CALLABLE_LIMIT
    relevant = [c for c in candidates if set(c['functions']) - {'project_catalog'}]
    mandatory = [c for c in relevant if 'explicit' in c['functions']]
    advisory = [c for c in relevant if 'explicit' not in c['functions']][:DISCOVERY_CALLABLE_LIMIT]
    retained_ids = {c['id'] for c in [*mandatory, *advisory]}
    discovery['omitted_candidates'] = [c['id'] for c in candidates if c['id'] not in retained_ids]
    candidates = [c for c in candidates if c['id'] in retained_ids]
    ordered, seen = [], set()
    for function in ["explicit", *discovery["functions"], "project_catalog"]:
        candidate = next((c for c in candidates if function in c["functions"] and c["id"] not in seen), None)
        if candidate:
            ordered.append(candidate)
            seen.add(candidate["id"])
    ordered.extend(c for c in candidates if c["id"] not in seen)
    candidates_tokens = 0
    for candidate in ordered:
        # Audit identities remain in the manifest, outside model text.
        brief = _candidate_brief(candidate, target_mode)
        call_identity = "call-contract:" + plc_model + ":" + candidate["name"]
        if call_identity in discovery["included_call_contracts"]:
            brief.pop("target_call", None)
        block = technical_block(brief)
        model_units['capability:' + candidate['id']] = block
        cost = estimate_tokens(block + "\n\n")
        parts.append(block)
        candidates_tokens += cost
        discovery["included_candidates"].append(candidate["id"])
    included = set(discovery["included_candidates"])
    # The exact-fact path normally supplies explicit/selected calls. Fill only
    # missing explicit targets here, never expand a merely suggested option.
    covered = {str(r.get("fact_target") or r.get("instruction_opcode") or "").upper()
               for r in manifest.get("records", [])}
    additional = [t for t in explicit if str(t.get("opcode") or "").upper() not in covered]
    facts_tokens = 0
    for target in additional:
        rows, report = retrieve_instruction_facts("", plc_model=plc_model, task_type="generate",
            targets=[target], compact_sources=True)
        for view in report.get("definition_views", []):
            for gap in view.get("gaps", []):
                item = {"target": target["opcode"], **copy.deepcopy(gap)}
                if item not in discovery["gaps"]:
                    discovery["gaps"].append(item)
        if not rows:
            discovery["omitted_evidence"].append(target["opcode"])
            discovery["gaps"].append({"target": target["opcode"], "reason": "no_complete_fact_unit"})
        for row in rows:
            from knowledge.retriever import format_knowledge_reference
            block = format_knowledge_reference(row)
            model_units[str(row['id'])] = block
            cost = estimate_tokens(block + "\n\n")
            parts.append(block)
            facts_tokens += cost
            discovery["included_evidence"].append(row["id"])
            manifest.setdefault("records", []).append({k: copy.deepcopy(row[k]) for k in
                ("id", "source", "manual_id", "revision", "pdf_page", "instruction_opcode") if k in row})
            manifest['records'][-1]['model_text'] = block
    # Suggestions retain their status and provenance after exact source facts.
    if experiment.arm != "A":
        from plc.composition import derive_composition
        view = derive_composition({**(confirmed_spec or {}), "plc_model": plc_model}, input_version=dict(experiment.input_version),
                                  relations=discovery.get("relations", []))
        discovery.update(experiment_arm=experiment.arm, included_composition_units=[], omitted_composition_units=[], composition_units=[])
        units = [(g["id"], g) for g in view["equipment_groups"]]
        if experiment.arm in {"C", "D"}:
            for relation in discovery.get("relations", []):
                identity = "relation:" + relation["id"]
                if relation["from"] not in included or relation["to"] not in included:
                    discovery["omitted_composition_units"].append({"id": identity, "reason": "endpoint_not_delivered"})
                else:
                    # The unit retains endpoint meanings even if a downstream
                    # context omits the separate option briefs.
                    units.append((identity, {**relation, "endpoint_briefs": [_candidate_brief(
                        next(c for c in candidates if c["id"] == endpoint), target_mode)
                        for endpoint in (relation["from"], relation["to"])]}))
        if experiment.arm == "D":
            # Atomic plan references already confirmed constraints; the view does
            # not duplicate their expressions or allocate additional state cells.
            plan = {k: v for k, v in view.items() if k != "equipment_groups"}
            plan["equipment_refs"] = [{k: copy.deepcopy(group[k]) for k in
                ("id", "binding_ids", "confirmation", "source_status", "binding_status", "state_ownership", "gaps")}
                for group in view["equipment_groups"]]
            units.append(("composition:plan", plan))
        for identity, unit in units:
            block = technical_block(unit)
            model_units['composition-unit:' + identity] = block
            parts.append(block)
            discovery["included_composition_units"].append(identity)
            discovery["composition_units"].append({"id": identity, "value": copy.deepcopy(unit)})
        discovery["composition"] = {"version": view["version"], "input_version": view["input_version"],
                                     "counts": view["counts"], "gaps": view["gaps"], "authority": view["authority"]}
    text = "\n\n".join(parts)
    manifest["records"] = [r for r in manifest.get("records", [])
                           if str(r.get("id")) not in discovery["replaced_evidence"]]
    discovery["tokens"] = {"basis": "deterministic_heuristic_estimate",
                          "baseline_evidence": source_tokens, "final_evidence": estimate_tokens(text),
                          "net_delta": estimate_tokens(text) - source_tokens,
                          "candidate_briefs": candidates_tokens, "new_facts": facts_tokens,
                          "selection_policy": wanted_policy if SELECTION_POLICY in parts else 0}
    discovery['model_units'] = model_units
    manifest["capability_discovery"] = discovery
    return KnowledgeContext(text, manifest)


def reconcile_discovery(discovery, evidence):
    """The final compiler determines what actually reached the model."""
    result = copy.deepcopy(discovery)
    model_units = result.get('model_units', {})
    for included_key, omitted_key, prefix in (("included_call_contracts", "omitted_call_contracts", ""),
                                              ("included_candidates", "omitted_candidates", "capability:"),
                                              ("included_evidence", "omitted_evidence", "")):
        retained = []
        for identity in result.get(included_key, []):
            block = model_units.get(prefix + str(identity))
            if (block and block in evidence) or '"id":' + _json(prefix + str(identity)) in evidence:
                retained.append(identity)
            else:
                result.setdefault(omitted_key, []).append(identity)
        result[included_key] = retained
    result.setdefault("tokens", {})["delivered_evidence"] = estimate_tokens(evidence)
    retained = []
    complete_units = {}
    for match in _BLOCK.finditer(evidence):
        try:
            identity = json.loads(match.group(1)).get("id")
            if not isinstance(identity, str) or not identity.startswith("composition-unit:"):
                continue
            block = match.group(0)
            header_end = block.index("\n", block.index("[KNOWLEDGE ")) + 1
            complete_units[identity] = json.loads(block[header_end:block.rfind("[/KNOWLEDGE]")].strip())
        except (ValueError, TypeError, AttributeError):
            continue
    expected_units = {r["id"]: json.loads(_json(r["value"])) for r in result.get("composition_units", [])}
    for identity in result.get("included_composition_units", []):
        marker = "composition-unit:" + identity
        block = model_units.get(marker)
        if (block and block in evidence) or (marker in complete_units and complete_units[marker] == expected_units.get(identity)):
            retained.append(identity)
        else:
            result.setdefault("omitted_composition_units", []).append({"id": identity, "reason": "final_context_omission"})
    if "included_composition_units" in result:
        result["included_composition_units"] = retained
    result["tokens"]["selection_policy_delivered"] = SELECTION_POLICY in evidence
    return result
