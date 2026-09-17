"""Explicit, budgeted discovery producing one scoped CapabilityContract.

The probe registry is a collection of strategies, not a capability allowlist.
All metadata-declared parameters pass through the same typed schema. Ordinary
requests only consume the saved contract and never call discovery.
"""
from __future__ import annotations

import copy
import time
from dataclasses import replace

from model_contract import (
    MISSING, CapabilityContract, CapabilityDescriptor, ConstraintDescriptor,
    contract_scope, metadata_contract_parts, identifier,
)
from model_request_policy import parameter_value, merge
from model_probes import PROBE_REGISTRY, ProbeContext
from model_provider import ModelProviderError


def _configured_values(profile, parameters):
    options = merge(profile.get("generationDefaults") or {}, profile.get("requestOverrides") or {})
    selections = (profile.get("userModelSettings") or {}).get("parameters", {})
    result = {}
    for name, descriptor in parameters.items():
        value = parameter_value(options, name, descriptor)
        selection = selections.get(name, {})
        if selection.get("mode") == "value":
            value = selection.get("value")
        elif selection.get("mode") == "omit":
            value = None
        if value is not MISSING and value is not None:
            try:
                descriptor.validate(value)
                result[name] = value
            except ValueError:
                pass
    return result


def inspect_openai_compatible(provider, model, configured_capabilities=None, *, probes=None):
    original = copy.deepcopy(getattr(provider, "profile", {}) or {})
    key = getattr(provider, "api_key", "")
    selected = str(model or "").strip()
    deadline = time.monotonic() + 90.0
    registry = PROBE_REGISTRY if probes is None else probes

    def remaining():
        return max(0.0, min(15.0, deadline - time.monotonic()))

    listing_available, metadata = True, []
    try:
        if callable(getattr(provider, "list_model_metadata", None)):
            metadata = list(provider.list_model_metadata(timeout=remaining()))
            models = sorted({str(item["id"]) for item in metadata if item.get("id")})
        else:
            models = sorted(set(provider.list_models(timeout=remaining())))
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        listing_available, models = False, []
    except Exception:
        listing_available, models = False, []
    probe_model = selected if selected and selected != "__discover__" else (models[0] if models else "")
    if not probe_model:
        raise ModelProviderError("模型列表不可用，请手动填写模型 ID。", code="invalid_request")
    entry = next((item for item in metadata if item.get("id") == probe_model), {})
    parameters, capabilities, constraints = metadata_contract_parts(entry)
    if callable(getattr(provider, "for_detection", None)):
        provider = provider.for_detection(parameters=parameters)
    if remaining() > 0 and callable(getattr(provider, "probe_parameter", None)):
        provider.probe_parameter(probe_model, timeout=remaining())
    # Probes cannot widen a provider's declared schema. No blind probing of
    # arbitrary new controls or guessed provider-specific nested field names.
    context = ProbeContext(provider, probe_model, remaining, parameters=parameters)
    for strategy in registry.values():
        target = parameters if strategy.kind == "parameter" else capabilities
        if strategy.name in target:
            continue
        context.configured_values = _configured_values(original, parameters)
        target[strategy.name] = strategy.run(context)
    # Preserve old manually configured flags as explicitly labelled legacy
    # evidence, not model-name facts. Unknown probe outcomes must not become
    # false merely because the network/budget failed.
    for name, value in (configured_capabilities or {}).items():
        try:
            identifier(name)
            if name not in capabilities and isinstance(value, bool):
                capabilities[name] = CapabilityDescriptor("supported" if value else "unsupported", "legacy")
        except ValueError:
            continue
    # Incomplete metadata dependencies are not usable controls. Retain their
    # existence as unknown evidence without activating an unsafe partial rule.
    refs = set(parameters) | set(capabilities) | {"stream", "tools", "response_format"}
    for group in (parameters, capabilities):
        for name, descriptor in list(group.items()):
            condition = descriptor.constraints
            if (set(condition.requires) | set(condition.conflicts_with)) - refs:
                group[name] = replace(descriptor, status="unknown", constraints=ConstraintDescriptor())
    constraints = {name: c for name, c in constraints.items()
        if not (set(c.requires) | set(c.conflicts_with)) - refs}
    contract = CapabilityContract(contract_scope(original, parameters, probe_model, key), capabilities, parameters, constraints)
    # Validate the assembled document too (e.g. cross-parameter alias collisions).
    contract = CapabilityContract.from_dict(contract.to_dict())
    return {
        "models": models,
        "recommended_model": probe_model,
        "selected_model_available": bool(selected and selected in models),
        "model_listing_available": listing_available,
        "contract": contract.to_dict(),
        "note": "能力与参数来自服务声明或有限测试；未知不等于不支持，接口接受不等于实际生效。未声明参数不会盲目探测。保存后正常调用仅使用缓存合同，不自动再次探测。",
    }
