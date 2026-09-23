"""Pure request resolution: protocol > user selection > hint > default > omission.

This module neither discovers capabilities nor makes network requests. A scoped
observation is evidence, not permission to replace messages, tools or schemas.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from model_runtime.contract import (
    MISSING, CapabilityContract, ConstraintDescriptor, UserModelSettings,
    scoped_contract, legacy_contract, path_get,
)
from model_runtime.runtime_profile import RuntimeModelProfile


def merge(base, overlay):
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass(frozen=True)
class EffectiveRequest:
    options: Mapping[str, Any]
    sources: Mapping[str, str]
    values: Mapping[str, Any]


def parameter_value(options, name, descriptor):
    value = descriptor.read(options)
    if value is MISSING:
        value = path_get(options, ("extra_body", name), path_get(options, (name,)))
    return value



def parameter_override(layer, name, descriptor):
    """Read a layer, treating explicit parent deletion as a tombstone.

    A missing ancestor is inheritance. A null ancestor removes descendants,
    including descriptors mapped to extra_body nested paths.
    """
    value = parameter_value(layer, name, descriptor)
    if value is not MISSING:
        return value
    for path in (("extra_body",) + descriptor.wire_path, descriptor.wire_path,
                 ("extra_body", name), (name,)):
        for length in range(1, len(path)):
            if path_get(layer, path[:length]) is None:
                return None
    return MISSING

def without_workflow_effort(options, profile=None, *, model=None, api_key=None):
    """Remove workflow hints without touching the saved profile or other options.

    This is an application adapter, not part of authorized low-level probes.
    A descriptor identifies tuning aliases even on a framing wrapper which does
    not expose credentials. It is not evidence that a stale scope is applicable;
    ordinary provider resolution still validates scope before using selections.
    """
    result = copy.deepcopy(dict(options or {}))
    from model_runtime.contract import path_remove
    path_remove(result, ("reasoning_effort",))
    path_remove(result, ("extra_body", "reasoning_effort"))
    contract = scoped_contract(profile or {}, model, api_key)
    if contract is None and (profile or {}).get("capabilityContract"):
        try:
            contract = CapabilityContract.from_dict(profile["capabilityContract"])
        except (ValueError, TypeError, KeyError):
            pass  # Do not add a new capability-validation policy here.
    descriptor = contract.parameters.get("reasoning_effort") if contract else None
    if descriptor is not None:
        descriptor.remove(result, "reasoning_effort")
    if result.get("extra_body") == {}:
        result.pop("extra_body")
    return result


def condition_values(options, contract):
    values = {name: (desc.value if desc.value is not None else True) if desc.status == "supported"
        else False if desc.status == "unsupported" else None for name, desc in contract.capabilities.items()}
    for name, desc in contract.parameters.items():
        value = parameter_value(options, name, desc)
        values[name] = None if value is MISSING else value
    # Only actual request flags participate; advertising tools does not imply
    # that this particular request contains tools.
    values.update(tools=bool(options.get("tools")), stream=bool(options.get("stream")),
                  response_format=(options.get("response_format") or {}).get("type")
                  if isinstance(options.get("response_format"), Mapping) else None)
    return values


def constraints_for(contract, name, descriptor):
    return (descriptor.constraints, contract.constraints.get(name, ConstraintDescriptor()))


def resolve_request(profile, hints=None, *, protocol=None, model=None, api_key=None, transport_defaults=None):
    """Resolve JSON options without mutating profile, hints or cached evidence.

    RuntimeModelProfile is the production input: its capability contract and
    user selections are already materialized. Raw persisted profiles remain
    accepted here only for settings/migration callers until that compatibility
    surface is retired.
    """
    hints, protocol = hints or {}, protocol or {}
    if isinstance(profile, RuntimeModelProfile):
        if model not in (None, "", profile.model):
            raise ValueError("Runtime model profile does not match the requested model")
        defaults = merge(profile.defaults, transport_defaults or {})
        overrides = profile.overrides
        contract = profile.contract
        settings = profile.settings
    else:
        defaults = merge(profile.get("generationDefaults") or {}, transport_defaults or {})
        overrides = profile.get("requestOverrides") or {}
        contract = scoped_contract(profile, model, api_key)
        raw_settings = profile.get("userModelSettings") or {}
        if profile.get("capabilityContract") and contract is None:
            # Never forward stale selections after a programmatic model/endpoint or
            # context change. The UI/service clears them; direct callers must too.
            if raw_settings.get("parameters"):
                raise ValueError("Capability scope changed; reset selections or detect this model again")
            return EffectiveRequest(merge(merge(merge(defaults, hints), overrides), protocol), {}, {})
        if contract is None:
            # Raw legacy callers retain their old merge semantics outside the
            # provider. Production provider requests materialize first.
            return EffectiveRequest(merge(merge(merge(defaults, hints), overrides), protocol), {}, {})
        settings = UserModelSettings.from_dict(raw_settings, contract) if raw_settings else UserModelSettings(dict(contract.scope), {})
    layers = [("profile_default", defaults), ("workflow_hint", hints), ("user_advanced", overrides)]
    options = merge(merge(defaults, hints), overrides)
    values, sources = {}, {}
    for name, desc in contract.parameters.items():
        selected, source = MISSING, "server_default"
        for label, layer in layers:
            value = parameter_override(layer, name, desc)
            if value is not MISSING:
                selected, source = value, label
        choice = settings.parameters.get(name, {"mode": "inherit"})
        if choice["mode"] == "value":
            selected, source = choice["value"], "user_selection"
        elif choice["mode"] == "omit":
            selected, source = None, "user_omit"
        desc.remove(options, name)
        if desc.status in {"unsupported", "fixed"}:
            selected, source = None, "contract_omit"
        if selected is not MISSING and selected is not None:
            try:
                desc.validate(selected)
            except ValueError as error:
                raise ValueError(name + ": " + str(error)) from error
            desc.write(options, selected)
            values[name] = copy.deepcopy(selected)
        else:
            values[name] = None
        sources[name] = source
    # Canonical protocol fields are supplied by ModelProvider, not the user.
    options = merge(options, protocol)
    context = condition_values(options, contract)
    for name, desc in contract.parameters.items():
        if values.get(name) is not None and not all(c.satisfied(context) for c in constraints_for(contract, name, desc)):
            raise ValueError(name + ": requirements or conflicts are not satisfied; reset the parameter or re-detect")
    validate_capability_use(options, contract)
    return EffectiveRequest(options, sources, values)


def validate_capability_use(options, contract):
    """Reject known incompatibilities; never silently discard required tools/JSON."""
    requested = {"tools": bool(options.get("tools")), "streaming": bool(options.get("stream")),
        "parallel_tool_calls": options.get("parallel_tool_calls") is True,
        "structured_output": bool(options.get("response_format")) and (options.get("response_format") or {}).get("type") != "text"}
    context = condition_values(options, contract)
    for name, active in requested.items():
        desc = contract.capabilities.get(name)
        if not active or desc is None:
            continue
        if desc.status == "unsupported" or not all(c.satisfied(context) for c in constraints_for(contract, name, desc)):
            raise ValueError(name + ": requested operation conflicts with the capability contract")
        if name == "structured_output" and desc.status in {"supported", "conditional"} and desc.modes and desc.source in {"metadata", "catalog", "manual"}:
            if (options.get("response_format") or {}).get("type") not in desc.modes:
                raise ValueError("structured_output: this mode has not been declared or observed")


def contract_capability_available(contract, name, *, options=None):
    """Read one capability from a materialized contract; no legacy fallback."""
    desc = contract.capabilities.get(name)
    if desc is None:
        return False
    return desc.status in {"supported", "conditional"} and all(
        rule.satisfied(condition_values(options or {}, contract))
        for rule in constraints_for(contract, name, desc)
    )


def capability_available(profile, name, *, model=None, api_key=None, options=None, legacy_name=None):
    """Compatibility helper for non-provider callers using persisted profiles."""
    if isinstance(profile, RuntimeModelProfile):
        return contract_capability_available(profile.contract, name, options=options)
    contract = scoped_contract(profile, model, api_key)
    if contract is not None and name in contract.capabilities:
        return contract_capability_available(contract, name, options=options)
    return bool((profile.get("capabilities") or {}).get(legacy_name or name))


def public_contract_settings(profile, api_key=None):
    """Project persisted v2 or a read-only v1 migration for the settings API."""
    contract = scoped_contract(profile, api_key=api_key)
    if contract is not None:
        raw = profile.get("userModelSettings") or {"scope": dict(contract.scope), "parameters": {}}
        return contract.to_dict(), UserModelSettings.from_dict(raw, contract).to_dict()
    if profile.get("capabilityContract"):
        return {}, {}
    contract, settings = legacy_contract(profile, api_key)
    return (contract.to_dict(), settings) if contract else ({}, {})


def adopt_contract(profile, raw_contract, selections=None):
    """Explicit migration used on save, never during GET or ordinary requests."""
    contract = CapabilityContract.from_dict(raw_contract)
    if selections:
        settings = UserModelSettings.from_dict(selections, contract)
    else:
        settings = UserModelSettings(dict(contract.scope), {})
    result = copy.deepcopy(profile)
    result["capabilityContract"] = contract.to_dict()
    result["userModelSettings"] = settings.to_dict()
    result["parameterSupport"] = {}
    return result
