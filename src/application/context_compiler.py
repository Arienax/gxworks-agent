"""Deterministic model-aware context selection for generation and retrieval.

This module owns no PLC semantics. It only selects, deduplicates, budgets and
accounts for already-confirmed engineering state before a model call.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from collections.abc import Mapping

from knowledge.evidence import estimate_tokens
from model_runtime.capabilities import effective_parameter


def _contract_value(profile, name):
    profile = profile if isinstance(profile, Mapping) else {}
    direct = profile.get(name)
    if isinstance(direct, (int, float)) and direct > 0:
        return int(direct)
    capabilities = profile.get("capabilities")
    if isinstance(capabilities, Mapping):
        raw = capabilities.get(name)
        if isinstance(raw, (int, float)) and raw > 0:
            return int(raw)
        if isinstance(raw, Mapping) and isinstance(raw.get("value"), (int, float)) and raw["value"] > 0:
            return int(raw["value"])
    contract = profile.get("capabilityContract")
    if isinstance(contract, Mapping):
        caps = contract.get("capabilities")
        raw = caps.get(name) if isinstance(caps, Mapping) else None
        if isinstance(raw, Mapping) and isinstance(raw.get("value"), (int, float)) and raw["value"] > 0:
            return int(raw["value"])
    return None


def _positive_int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _output_limit(profile):
    """Resolve the effective requested output reserve from the model runtime contract."""
    profile = profile if isinstance(profile, Mapping) else {}
    # Reuse the provider's precedence: requestOverrides > generationDefaults,
    # including extra_body. If both aliases are present, reserve the larger
    # value rather than claiming input space that either wire field may consume.
    requested = [
        _positive_int(effective_parameter(profile, name))
        for name in ("max_completion_tokens", "max_tokens")
    ]
    requested = [value for value in requested if value is not None]
    if requested:
        return max(requested)

    contract = profile.get("capabilityContract")
    maxima = []
    if isinstance(contract, Mapping):
        params = contract.get("parameters")
        if isinstance(params, Mapping):
            for key in ("max_completion_tokens", "max_tokens"):
                raw = params.get(key)
                if not isinstance(raw, Mapping):
                    continue
                domain = raw.get("domain") if isinstance(raw.get("domain"), Mapping) else raw
                value = _positive_int(domain.get("maximum") if isinstance(domain, Mapping) else None)
                if value is not None:
                    maxima.append(value)
    return max(maxima) if maxima else None


def model_budget(profile):
    window = _contract_value(profile, "context_window")
    reserved_output = _output_limit(profile) or 8192
    protocol_overhead = 4096
    safety_margin = min(16384, max(2048, int(window * 0.02))) if window else 4096
    usable = max(0, window - reserved_output - protocol_overhead - safety_margin) if window else None
    # Unknown capacity and known exhaustion are different states. An unknown
    # model keeps the conservative fallback budgets; a known zero-input budget
    # must not schedule retrieval that cannot fit in the model context.
    if window is None:
        budget_state = "unknown"
        retrieval = 16000
        rag_evidence = 12000
    elif usable <= 0:
        budget_state = "unusable"
        retrieval = 0
        rag_evidence = 0
    else:
        budget_state = "available"
        retrieval = min(24000, max(6000, usable // 8))
        rag_evidence = min(32000, max(4000, usable // 8))
    return {
        "context_window": window,
        "budget_confidence": "known" if window else "unknown",
        "budget_state": budget_state,
        "reserved_output_tokens": reserved_output,
        "protocol_overhead_tokens": protocol_overhead,
        "safety_margin_tokens": safety_margin,
        "usable_input_tokens": usable,
        "retrieval_query_token_budget": retrieval,
        "rag_evidence_token_budget": rag_evidence,
    }



def _estimate(value):
    if isinstance(value, str):
        return estimate_tokens(value)
    return estimate_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


_KNOWLEDGE_BLOCK_RE = re.compile(
    r"(?ms)(?:^Reference role:[^\n]*\n)?^\[KNOWLEDGE [^\n]*\]\n.*?^\[/KNOWLEDGE\]"
)


def deduplicate_evidence_text(text):
    """Deduplicate only complete source blocks; never split manual paragraphs."""
    raw = str(text or "")
    matches = list(_KNOWLEDGE_BLOCK_RE.finditer(raw))
    if not matches:
        return raw, 0
    seen, parts, dropped = set(), [], 0
    cursor = 0
    for match in matches:
        parts.append(raw[cursor:match.start()])
        block = match.group(0)
        marker = hashlib.sha256(block.encode("utf-8")).hexdigest()
        if marker in seen:
            dropped += 1
        else:
            seen.add(marker)
            parts.append(block)
        cursor = match.end()
    parts.append(raw[cursor:])
    return "".join(parts), dropped

def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _clean_request_rows(context):
    context = copy.deepcopy(context) if isinstance(context, Mapping) else {}
    if not context:
        return {}, 0, 0
    rows = context.get("requests") if isinstance(context.get("requests"), list) else []
    seen_ids, seen_text, result, duplicate = set(), {}, [], 0
    superseded = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(raw)
        request_id = str(row.get("id") or "")
        if request_id and request_id in seen_ids:
            duplicate += 1
            continue
        if request_id:
            seen_ids.add(request_id)
        absorbed_fields = row.get("absorbed_by_confirmed_fields")
        absorbed = bool(
            row.get("superseded_by")
            or absorbed_fields is True
            or (isinstance(absorbed_fields, (list, tuple)) and absorbed_fields)
        )
        text = str(row.get("text") or "")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        if absorbed and text:
            row.pop("text", None)
            row["text_sha256"] = text_hash
            row["runtime_text_status"] = "superseded_structured"
            superseded += 1
        elif text and text_hash in seen_text:
            row.pop("text", None)
            row["text_sha256"] = text_hash
            row["runtime_text_status"] = "duplicate_exact"
            row["duplicate_of"] = seen_text[text_hash]
            duplicate += 1
        elif text:
            seen_text[text_hash] = request_id or text_hash
        result.append(row)
    context["requests"] = result
    return context, duplicate, superseded


def _positive_selected(selected):
    selected = selected if isinstance(selected, Mapping) else {}
    contract = selected.get("generation_contract") if isinstance(selected.get("generation_contract"), Mapping) else {}
    prefs = selected.get("implementation_preferences") if isinstance(selected.get("implementation_preferences"), Mapping) else {}
    positive = ("required_opcodes", "required_devices", "required_structures",
                "any_of_opcode_groups", "any_of_structure_groups", "instruction_instances")
    return (
        {k: copy.deepcopy(selected[k]) for k in ("name", "description", "generation_guide") if k in selected},
        {k: copy.deepcopy(prefs[k]) for k in positive if k in prefs},
        {k: copy.deepcopy(contract[k]) for k in positive if k in contract},
    )


_QUERY_METADATA = frozenset({"id", "source", "schema_version", "min_reader_version",
                             "binding_id", "row_binding_id", "source_parameter_id"})


def _flatten(value, prefix=""):
    """Index engineering values, not JSON paths or provenance identifiers."""
    rows = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key not in _QUERY_METADATA:
                rows.extend(_flatten(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            rows.extend(_flatten(nested))
    elif value is not None and not isinstance(value, bool):
        text = " ".join(str(value).strip().split())
        if text:
            rows.append(text)
    return rows


def _retrieval_facts(facts):
    """Do not search manuals for form choices or repeat resolved wire bindings.

    The complete bindings/answers remain in the generation packet. Hardware
    parameters and unbound engineering rows still contribute retrieval values.
    Explicit device/instruction references in the user's request or plan are
    kept, including operands; this is not a request-complexity classifier.
    """
    result = copy.deepcopy(facts)
    bindings = result.pop("io_bindings", [])
    # Generic/older bindings can omit role. Their resolved address and stable
    # parameter link still identify a settled wiring answer, not a manual query.
    bindings = [row for row in bindings if isinstance(row, Mapping)
                and row.get("kind") in {"X", "Y"} and row.get("address")]
    bound_ids = {row.get("source_parameter_id") for row in bindings
                 if isinstance(row, Mapping) and row.get("source_parameter_id")}
    bound_addresses = {row["address"] for row in bindings}
    if isinstance(result.get("parameters"), list):
        result["parameters"] = [
            {key: row[key] for key in ("name", "value") if key in row}
            for row in result["parameters"] if isinstance(row, Mapping)
            and str(row.get("value") if row.get("value") is not None else "").strip()
            and row.get("id") not in bound_ids
        ]
    if isinstance(result.get("io_table"), list):
        result["io_table"] = [row for row in result["io_table"] if isinstance(row, Mapping)
                              and row.get("address") not in bound_addresses]
    return result


def _trim_to_tokens(text, limit):
    """Return a head/tail fragment whose estimated size never exceeds limit."""
    if estimate_tokens(text) <= limit:
        return text, False
    if limit <= 0:
        return "", True
    marker = " … "
    if estimate_tokens(marker) > limit:
        return "", True
    low, high, best = 0, len(text), marker
    while low <= high:
        keep = (low + high) // 2
        head = (keep + 1) // 2
        tail = keep // 2
        candidate = text[:head] + marker + (text[-tail:] if tail else "")
        if estimate_tokens(candidate) <= limit:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best, True


def _pack_sections(sections, token_budget):
    weights = {"confirmed_facts": .45, "selected_method": .20, "latest_amendment": .10,
               "implementation_preferences": .08, "positive_contract": .10, "older_requests": .07}
    if token_budget <= 0:
        return "", {
            name: {"budget_tokens": 0, "included_tokens": 0, "omitted_fragments": len(fragments)}
            for name, fragments in sections
        }, 0
    packed, report = [], {}
    used_total = 0
    leftovers = []
    seen_fragments = set()
    for name, fragments in sections:
        unique = []
        for fragment in fragments:
            if fragment not in seen_fragments:
                seen_fragments.add(fragment)
                unique.append(fragment)
        fragments = unique
        quota = max(64, int(token_budget * weights.get(name, 0)))
        used = 0
        included = []
        omitted = 0
        for fragment in fragments:
            cost = estimate_tokens(fragment)
            if used + cost <= quota:
                included.append(fragment); used += cost
            else:
                remaining = quota - used
                trimmed, cut = _trim_to_tokens(fragment, remaining)
                if trimmed:
                    included.append(trimmed); used += estimate_tokens(trimmed)
                omitted += 1 if cut else 0
                leftovers.extend(fragments[len(included):])
                break
        packed.extend(included)
        used_total += used
        report[name] = {"budget_tokens": quota, "included_tokens": used, "omitted_fragments": omitted}
    # Spare capacity goes back to earlier-priority leftovers without section labels in the query text.
    spare = max(0, token_budget - used_total)
    for fragment in leftovers:
        if spare <= 0:
            break
        text, cut = _trim_to_tokens(fragment, spare)
        if text:
            packed.append(text)
            cost = estimate_tokens(text)
            used_total += cost; spare -= cost
        if cut:
            break
    query = "\n".join(packed)
    if estimate_tokens(query) > token_budget:
        query, _ = _trim_to_tokens(query, token_budget)
    return query, report, estimate_tokens(query)


def _generation_packet(runtime, value, evidence_text):
    return {
        "confirmed_spec": runtime,
        "generation_request": value.generation_request,
        "current_program": copy.deepcopy(value.current_program),
        "evidence": str(evidence_text or ""),
    }


def _wire_packet(renderer, runtime, value, evidence_text):
    if renderer is None:
        return {}
    from application.generation_wire import normalize_wire_packet
    rendered = renderer(
        copy.deepcopy(runtime),
        str(evidence_text or ""),
        str(value.generation_request or ""),
        copy.deepcopy(value.current_program),
    )
    return normalize_wire_packet(rendered)


def _pressure(utilization):
    if utilization is None:
        return "unknown"
    if utilization < .35:
        return "low"
    if utilization < .65:
        return "moderate"
    if utilization < .80:
        return "high"
    return "critical"


@dataclass(frozen=True)
class ContextCompilerInput:
    confirmed_spec: dict
    intent_context: dict | None = None
    selected_approach: dict = field(default_factory=dict)
    evidence: object = None
    plc_model: str = "FX3U"
    model_profile: dict = field(default_factory=dict)
    task_type: str = "generate"
    generation_request: str = ""
    current_program: object = None
    wire_renderer: object = None


@dataclass(frozen=True)
class CompiledContext:
    generation_packet: dict
    retrieval_packet: dict
    provenance_receipt: dict
    budget_report: dict
    wire_packet: dict = field(default_factory=dict)

    def to_dict(self):
        return {item.name: copy.deepcopy(getattr(self, item.name)) for item in fields(self)}


class ContextCompiler:
    """Compile runtime views without mutating persistent engineering state."""

    def compile(self, value: ContextCompilerInput, *, evidence_text="") -> CompiledContext:
        from plc.specification.provenance import confirmed_spec_fields, intent_context
        persistent = confirmed_spec_fields(value.confirmed_spec or {})
        runtime = copy.deepcopy(persistent)
        context = (intent_context({"intent_context": value.intent_context})
                   if value.intent_context is not None else intent_context(runtime))
        original_evidence_text = str(evidence_text or "")
        runtime_context, duplicate_requests, superseded = _clean_request_rows(context)
        if runtime_context or "intent_context" in runtime or value.intent_context is not None:
            runtime["intent_context"] = runtime_context

        selected = value.selected_approach or runtime.get("selected_approach") or {}
        method, prefs, contract = _positive_selected(selected)
        requests = [row for row in runtime_context.get("requests", []) if isinstance(row, Mapping)]
        latest = [requests[-1].get("text", "")] if requests and requests[-1].get("text") else []
        older = [row.get("text", "") for row in requests[:-1] if row.get("text")]
        if value.task_type == "edit" and value.generation_request:
            latest = [value.generation_request, *latest]
        facts = {key: copy.deepcopy(runtime[key]) for key in (
            "parameters", "io_table", "io_bindings", "execution_semantics",
            "hardware_profile", "hardware_context", "user_notes",
        ) if key in runtime}
        sections = [
            ("confirmed_facts", _flatten(_retrieval_facts(facts))),
            ("selected_method", _flatten(method)),
            ("latest_amendment", _flatten(latest)),
            ("implementation_preferences", _flatten(prefs)),
            ("positive_contract", _flatten(contract)),
            ("older_requests", _flatten(older)),
        ]
        budget = model_budget(value.model_profile)
        retrieval_query, section_report, retrieval_tokens = _pack_sections(
            sections, budget["retrieval_query_token_budget"])

        evidence_text, duplicate_evidence = deduplicate_evidence_text(evidence_text)
        source_tokens = {
            "confirmed_facts": _estimate(facts),
            "selected_method": _estimate(method),
            "active_requests": _estimate(latest),
            "historical_requests": _estimate(older),
            "generation_contract": _estimate(selected.get("generation_contract", {})),
            "rag_evidence": estimate_tokens(evidence_text),
            "current_program": _estimate(value.current_program) if value.current_program is not None else 0,
        }
        usable = budget["usable_input_tokens"]
        original_packet = _generation_packet(persistent, value, original_evidence_text)
        original_payload_tokens = _estimate(original_packet)
        original_wire = _wire_packet(
            value.wire_renderer, persistent, value, original_evidence_text,
        )
        if original_wire:
            from application.generation_wire import wire_token_estimate
            original_budget_payload_tokens = wire_token_estimate(original_wire)
            budget_basis = "wire_messages"
        else:
            original_budget_payload_tokens = original_payload_tokens
            budget_basis = "logical_generation_packet"
        original_estimated = original_budget_payload_tokens + budget["protocol_overhead_tokens"]
        if usable is None:
            original_utilization = None
            pressure = "unknown"
        elif usable <= 0:
            original_utilization = None
            pressure = "critical"
        else:
            original_utilization = original_estimated / usable
            pressure = _pressure(original_utilization)

        compacted_runtime = runtime
        provenance_saved = max(0, _estimate(context) - _estimate(runtime_context))
        generation_packet = _generation_packet(compacted_runtime, value, evidence_text)
        compiled_payload_tokens = _estimate(generation_packet)
        wire_packet = _wire_packet(
            value.wire_renderer, compacted_runtime, value, evidence_text,
        )
        if wire_packet:
            from application.generation_wire import wire_token_estimate
            compiled_budget_payload_tokens = wire_token_estimate(wire_packet)
        else:
            compiled_budget_payload_tokens = compiled_payload_tokens
        compiled_estimated = compiled_budget_payload_tokens + budget["protocol_overhead_tokens"]
        utilization = (compiled_estimated / usable) if usable is not None and usable > 0 else None
        # Pressure is telemetry, not permission to trim unknown engineering
        # intent. Report actual work rather than an "aggressive" no-op label.
        mode = "priority_projection" if superseded else "dedupe_only"
        report = {
            "model_context_window": budget["context_window"],
            "budget_confidence": budget["budget_confidence"],
            "budget_state": budget["budget_state"],
            "reserved_output_tokens": budget["reserved_output_tokens"],
            "protocol_overhead_tokens": budget["protocol_overhead_tokens"],
            "safety_margin_tokens": budget["safety_margin_tokens"],
            "usable_input_tokens": usable,
            "pre_compaction_estimated_input_tokens": original_estimated,
            "estimated_input_tokens": compiled_estimated,
            "original_generation_payload_tokens": original_payload_tokens,
            "compiled_generation_payload_tokens": compiled_payload_tokens,
            "budget_basis": budget_basis,
            "original_budget_payload_tokens": original_budget_payload_tokens,
            "compiled_budget_payload_tokens": compiled_budget_payload_tokens,
            "compaction_saved_tokens": max(
                0, original_budget_payload_tokens - compiled_budget_payload_tokens
            ),
            "context_pressure": pressure,
            "pre_compaction_context_utilization": (
                round(original_utilization, 6) if original_utilization is not None else None
            ),
            "context_utilization": round(utilization, 6) if utilization is not None else None,
            "budget_exceeded_after_compaction": bool(usable is not None and compiled_estimated > usable),
            "retrieval_query_token_budget": budget["retrieval_query_token_budget"],
            "rag_evidence_token_budget": budget["rag_evidence_token_budget"],
            "retrieval_query_estimated_tokens": retrieval_tokens,
            "source_tokens": source_tokens,
            "dropped": {"duplicate_requests": duplicate_requests,
                        "superseded_structured": superseded,
                        "duplicate_evidence_blocks": duplicate_evidence,
                        "provenance_tokens": provenance_saved},
            "compression_mode": mode,
            "semantic_curator_eligible": pressure == "critical",
            "semantic_curator_invoked": False,
            "retrieval_sections": section_report,
            "token_estimate": "deterministic_heuristic_estimate",
        }
        retrieval_packet = {
            "query": retrieval_query,
            "estimated_tokens": retrieval_tokens,
            "sections": section_report,
        }
        wire_hash = None
        if wire_packet:
            from application.generation_wire import wire_sha256
            wire_hash = wire_sha256(wire_packet)
        plan = {
            "task_type": value.task_type,
            "plc_model": value.plc_model,
            "persistent_spec_sha256": _fingerprint(persistent),
            "runtime_spec_sha256": _fingerprint(compacted_runtime),
            "retrieval_query_sha256": hashlib.sha256(retrieval_query.encode("utf-8")).hexdigest(),
            "wire_sha256": wire_hash,
            "budget_report": report,
        }
        receipt = {
            "context_plan_sha256": _fingerprint(plan),
            "persistent_spec_sha256": plan["persistent_spec_sha256"],
            "runtime_spec_sha256": plan["runtime_spec_sha256"],
            "retrieval_query_sha256": plan["retrieval_query_sha256"],
            "wire_sha256": plan["wire_sha256"],
            "context_pressure": pressure,
            "compression_mode": mode,
            "audit_policy": "decision_receipt_reference_only",
        }
        return CompiledContext(
            generation_packet, retrieval_packet, receipt, report, wire_packet,
        )
