"""Budgeted functional discovery shared by existing language/context adapters."""
from __future__ import annotations

import copy
import json
import re

from knowledge.evidence import KnowledgeContext, estimate_tokens
from plc.maintainability import SELECTION_POLICY, SELECTION_POLICY_VERSION


_BLOCK = re.compile(r"(?ms)(?:^Reference role:[^\n]*\n)?^\[KNOWLEDGE ([^\n]*)\]\n.*?^\[/KNOWLEDGE\]")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def public_discovery(value):
    """One compact projection for Web and MCP; full retrieval stays in records."""
    if not isinstance(value, dict):
        return None
    keys = ("version", "target_mode", "functions", "policy_version", "included_candidates",
            "omitted_candidates", "included_evidence", "omitted_evidence", "replaced_evidence",
            "gaps", "tokens", "model_calls", "deduplicated_units")
    result = {k: copy.deepcopy(value[k]) for k in keys if k in value}
    included = set(value.get("included_candidates", []))
    result["candidates"] = [{k: copy.deepcopy(c[k]) for k in
        ("id", "kind", "name", "purpose", "execution", "unknown", "functions", "sources") if k in c}
        for c in value.get("candidates", []) if c.get("id") in included]
    from application.generation_support import public_generation_value
    return public_generation_value(result)


def augment_generation_knowledge(requirement, knowledge="", *, plc_model="FX3U", target_mode="ladder",
                                 confirmed_spec=None, catalog=(), token_budget=12000):
    """Keep mandatory source blocks; omit whole discovery units with a receipt."""
    from knowledge.structured_facts import structured_fact_targets
    from knowledge.instruction_facts import retrieve_instruction_facts
    manifest = copy.deepcopy(getattr(knowledge, "manifest", {}))
    targets = (manifest.get("structured_facts") or {}).get("targets") or structured_fact_targets(
        requirement, confirmed_spec, plc_model=plc_model)
    explicit = targets.get("instructions") or []
    budget = max(0, int(token_budget))
    try:
        from knowledge.retriever import discover_capabilities
        discovery = discover_capabilities(requirement, plc_model=plc_model, target_mode=target_mode,
                                          explicit_targets=explicit, catalog=catalog)
    except Exception as error:
        # Discovery is advisory; existing exact facts and acceptance stay usable.
        discovery = {"version": "functional-discovery-v1", "functions": [], "candidates": [],
                     "queries": [], "gaps": [{"reason": "discovery_unavailable", "error_type": type(error).__name__}],
                     "model_calls": 0}
    discovery.update(policy_version=SELECTION_POLICY_VERSION, included_candidates=[], omitted_candidates=[],
                     included_evidence=[], omitted_evidence=[], replaced_evidence=[])
    base = str(knowledge or "")
    from application.context_compiler import deduplicate_evidence_text
    base, duplicate_units = deduplicate_evidence_text(base)
    discovery["deduplicated_units"] = duplicate_units
    source_tokens = estimate_tokens(base)
    blocks = list(_BLOCK.finditer(base))
    protected = {str(x) for x in (manifest.get("structured_facts") or {}).get("record_ids", [])}
    protected.update(str(x) for x in manifest.get("process_dependency_ids", []))
    wanted_policy = estimate_tokens(SELECTION_POLICY)
    # Remove only residual references to make room for selection guidance.
    for match in reversed(blocks):
        if estimate_tokens(base) + wanted_policy <= budget:
            break
        identity = str(json.loads(match.group(1)).get("id"))
        if identity not in protected:
            base = base.replace(match.group(0), "", 1)
            discovery["replaced_evidence"].append(identity)
    parts = [base] if base else []
    used = estimate_tokens(base)
    if used + wanted_policy <= budget:
        parts.append(SELECTION_POLICY)
        used = estimate_tokens("\n\n".join(parts))
    else:
        discovery["gaps"].append({"reason": "selection_policy_budget_omitted"})

    # Coverage-first ordering, then alternatives in the existing retrieval order.
    candidates = discovery["candidates"]
    ordered, seen = [], set()
    for function in ["explicit", *discovery["functions"], "project_catalog"]:
        candidate = next((c for c in candidates if function in c["functions"] and c["id"] not in seen), None)
        if candidate:
            ordered.append(candidate)
            seen.add(candidate["id"])
    ordered.extend(c for c in candidates if c["id"] not in seen)
    # Functional coverage may exceed this soft allocation; alternatives may
    # not consume all space needed by source facts. No count cap is introduced.
    brief_allowance = max(budget // 8, sum(estimate_tokens(_json(c)) for c in ordered[:len(seen)]))
    candidates_tokens = 0
    for candidate in ordered:
        # Source provenance stays detached; prompt citations do not repeat hashes.
        brief = {k: copy.deepcopy(candidate[k]) for k in
                 ("id", "kind", "name", "purpose", "interface", "execution", "unknown", "functions")}
        brief["sources"] = [{k: s[k] for k in ("id", "manual_id", "revision", "pdf_page", "pdf_pages", "identity") if k in s}
                            for s in candidate["sources"]]
        if target_mode == "fbd" and candidate["kind"] == "project_callable":
            # FBD's mandatory source catalog already carries the exact ports.
            brief["interface"] = {"catalog_reference": candidate["sources"][0]["identity"]}
        block = "[KNOWLEDGE " + _json({"id": "capability:" + candidate["id"]}) + "]\n" + _json(brief) + "\n[/KNOWLEDGE]"
        cost = estimate_tokens(block + "\n\n")
        if used + cost > budget or candidates_tokens + cost > brief_allowance:
            discovery["omitted_candidates"].append(candidate["id"])
            continue
        parts.append(block)
        used = estimate_tokens("\n\n".join(parts))
        candidates_tokens += cost
        discovery["included_candidates"].append(candidate["id"])
    included = set(discovery["included_candidates"])
    covered = {str(r.get("instruction_opcode") or "").upper() for r in manifest.get("records", [])}
    covered.update(str(t.get("opcode") or "").upper() for t in explicit)
    additional = []
    for function in discovery["functions"]:
        candidate = next((c for c in ordered if c["id"] in included and c["kind"] == "instruction"
                          and function in c["functions"]), None)
        if candidate and candidate["name"] not in covered:
            covered.add(candidate["name"])
            additional.append({"opcode": candidate["name"], "base_opcode": candidate["family"]})
    facts_tokens = 0
    for index, target in enumerate(additional):
        allowance = max(0, budget - used) // max(1, len(additional) - index)
        rows, report = retrieve_instruction_facts("", plc_model=plc_model, task_type="generate",
            targets=[target], char_budget=allowance * 8, token_budget=allowance, compact_sources=True)
        for view in report.get("definition_views", []):
            for gap in view.get("gaps", []):
                item = {"target": target["opcode"], **copy.deepcopy(gap)}
                if item not in discovery["gaps"]:
                    discovery["gaps"].append(item)
        if not rows:
            discovery["omitted_evidence"].append(target["opcode"])
            discovery["gaps"].append({"target": target["opcode"], "reason": "no_complete_fact_unit_within_budget"})
        for row in rows:
            from knowledge.retriever import format_knowledge_reference
            block = format_knowledge_reference(row)
            cost = estimate_tokens(block + "\n\n")
            if used + cost > budget:
                discovery["omitted_evidence"].append(row["id"])
                continue
            parts.append(block)
            used = estimate_tokens("\n\n".join(parts))
            facts_tokens += cost
            discovery["included_evidence"].append(row["id"])
            manifest.setdefault("records", []).append({k: copy.deepcopy(row[k]) for k in
                ("id", "source", "manual_id", "revision", "pdf_page", "instruction_opcode") if k in row})
    text = "\n\n".join(parts)
    manifest["records"] = [r for r in manifest.get("records", [])
                           if str(r.get("id")) not in discovery["replaced_evidence"]]
    discovery["tokens"] = {"basis": "deterministic_heuristic_estimate", "budget": budget,
                          "baseline_evidence": source_tokens, "final_evidence": estimate_tokens(text),
                          "net_delta": estimate_tokens(text) - source_tokens,
                          "candidate_briefs": candidates_tokens, "new_facts": facts_tokens,
                          "selection_policy": wanted_policy if SELECTION_POLICY in parts else 0}
    manifest["capability_discovery"] = discovery
    return KnowledgeContext(text, manifest)


def reconcile_discovery(discovery, evidence):
    """The final compiler determines what actually reached the model."""
    result = copy.deepcopy(discovery)
    for included_key, omitted_key, prefix in (("included_candidates", "omitted_candidates", "capability:"),
                                              ("included_evidence", "omitted_evidence", "")):
        retained = []
        for identity in result.get(included_key, []):
            if '"id":' + _json(prefix + identity) in evidence:
                retained.append(identity)
            else:
                result.setdefault(omitted_key, []).append(identity)
        result[included_key] = retained
    result.setdefault("tokens", {})["delivered_evidence"] = estimate_tokens(evidence)
    result["tokens"]["selection_policy_delivered"] = SELECTION_POLICY in evidence
    return result
