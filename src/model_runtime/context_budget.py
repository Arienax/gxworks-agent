"""Shared generation planning and final provider-wire size accounting."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from knowledge.evidence import estimate_tokens


def _contract_value(profile, name):
    profile = profile if isinstance(profile, Mapping) else {}
    direct = profile.get(name)
    if isinstance(direct, (int, float)) and direct > 0:
        return int(direct)
    contract = profile.get("capabilityContract")
    if isinstance(contract, Mapping):
        caps = contract.get("capabilities")
        raw = caps.get(name) if isinstance(caps, Mapping) else None
        if (
            isinstance(raw, Mapping)
            and isinstance(raw.get("value"), (int, float))
            and raw["value"] > 0
        ):
            return int(raw["value"])
    return None


def _positive_int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _output_limit(profile):
    """Resolve output reserve from canonical user settings, then contract bounds."""
    profile = profile if isinstance(profile, Mapping) else {}
    settings = profile.get("userModelSettings")
    selected = []
    if isinstance(settings, Mapping):
        parameters = settings.get("parameters")
        if isinstance(parameters, Mapping):
            for key in ("max_completion_tokens", "max_tokens"):
                choice = parameters.get(key)
                if isinstance(choice, Mapping) and choice.get("mode") == "value":
                    value = _positive_int(choice.get("value"))
                    if value is not None:
                        selected.append(value)
    if selected:
        return max(selected)

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
                value = _positive_int(
                    domain.get("maximum") if isinstance(domain, Mapping) else None
                )
                if value is not None:
                    maxima.append(value)
    return max(maxima) if maxima else None


def model_budget(profile):
    window = _contract_value(profile, "context_window")
    reserved_output = _output_limit(profile) or 8192
    protocol_overhead = 4096
    safety_margin = min(16384, max(2048, int(window * 0.02))) if window else 4096
    usable = max(0, window - reserved_output - protocol_overhead - safety_margin) if window else None
    # Model capacity is telemetry, not a RAG allocation rule.
    budget_state = "unknown" if window is None else "unusable" if usable <= 0 else "available"
    return {
        "context_window": window,
        "budget_confidence": "known" if window else "unknown",
        "budget_state": budget_state,
        "reserved_output_tokens": reserved_output,
        "protocol_overhead_tokens": protocol_overhead,
        "safety_margin_tokens": safety_margin,
        "usable_input_tokens": usable,
        "retrieval_query_token_budget": None,
        "rag_evidence_token_budget": None,
    }


def final_request_budget(profile, options):
    """Measure the complete resolved request, including tools and native schemas.

    Image payload bytes count toward transport size. Their token cost is an
    explicit estimate; base64 length is not mistaken for image input tokens.
    """
    counted = copy.deepcopy(options)
    images = 0
    for message in counted.get('messages', ()):
        parts = message.get('content') if isinstance(message, Mapping) else None
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get('type') == 'image_url':
                    images += 1
                    part['image_url'] = {'url': '[image]'}
    encoded = json.dumps(options, ensure_ascii=False, separators=(',', ':'))
    measured = estimate_tokens(json.dumps(counted, ensure_ascii=False, separators=(',', ':'))) + images * 2048
    report = model_budget(profile)
    # An explicitly resolved output cap is more precise than a profile maximum.
    sent_limits = [options.get(name) for name in ('max_tokens', 'max_completion_tokens')]
    output = max((_positive_int(value) or 0 for value in sent_limits), default=0)
    if output:
        report['reserved_output_tokens'] = output
        window = report['context_window']
        if window:
            report['usable_input_tokens'] = max(0, window - output - report['protocol_overhead_tokens'] - report['safety_margin_tokens'])
    usable = report['usable_input_tokens']
    return {**report, 'estimated_input_tokens': measured, 'utf8_bytes': len(encoded.encode('utf-8')),
            'image_count': images, 'image_token_cost_is_estimated': bool(images),
            'fits': None if usable is None else measured <= usable}
