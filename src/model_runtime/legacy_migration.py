"""Legacy model-profile migration into the v3 capability runtime.

Only this module should interpret the retired persisted fields:
capabilities, parameterSupport, generationDefaults and requestOverrides.
They remain readable for backward compatibility, but are not runtime contracts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import replace

from model_runtime.contract import (
    MISSING,
    CapabilityContract,
    CapabilityDescriptor,
    ParameterDescriptor,
    UserModelSettings,
    identifier,
    path_get,
)


LEGACY_PARAMETER_NAMES = ("reasoning_effort", "temperature")
EFFORT_CANDIDATES = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
STATUSES = {"supported", "accepted", "unknown", "unsupported", "fixed"}
SOURCES = {"probe", "metadata", "manual"}
LEGACY_PROFILE_FIELDS = frozenset({
    "capabilities", "parameterSupport", "generationDefaults", "requestOverrides",
})
_LEGACY_CAPABILITY_ALIASES = {"multimodal": "vision"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def merge_options(base, overlay):
    result = copy.deepcopy(dict(base or {}))
    for key, value in (overlay or {}).items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merge_options(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def legacy_option_layers(profile):
    """Return detached legacy option layers. No caller should read them directly."""
    return (
        copy.deepcopy(dict(profile.get("generationDefaults") or {})),
        copy.deepcopy(dict(profile.get("requestOverrides") or {})),
    )


def migrate_request_template(profile, template):
    """Translate the retired flat request_template into legacy profile input.

    This is persistence migration only. The resulting retired fields are later
    materialized into the v3 runtime contract/settings and are never public API.
    """
    result = copy.deepcopy(dict(profile))
    if not isinstance(template, Mapping):
        return result

    portable = {
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "response_format",
        "seed",
        "frequency_penalty",
        "presence_penalty",
    }
    defaults = {}
    overrides = {}
    for key, value in template.items():
        if key in {"model", "messages", "stream", "tools", "tool_choice"}:
            continue
        if value == "{effort}":
            continue
        if key == "extra_body" and isinstance(value, Mapping):
            value = copy.deepcopy(dict(value))
            if value.get("reasoning_effort") == "{effort}":
                value.pop("reasoning_effort")
            if not value:
                continue
        target = defaults if key in portable else overrides
        target[key] = copy.deepcopy(value)
    if defaults:
        result["generationDefaults"] = defaults
    if overrides:
        result["requestOverrides"] = overrides
    return result


def legacy_boolean_capabilities(profile):
    raw = profile.get("capabilities") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("Legacy capabilities must be an object")
    return {
        str(name): value
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, bool)
    }


def legacy_scope_context(profile, parameters):
    """Historical request-shape fingerprint used by persisted v3 contracts."""
    context = {}
    defaults, overrides = legacy_option_layers(profile)
    for label, options in (("generationDefaults", defaults), ("requestOverrides", overrides)):
        for name, descriptor in parameters.items():
            descriptor.remove(options, name)
        context[label] = options
    if profile.get("capabilityOverrides"):
        context["manual_overrides"] = copy.deepcopy(profile["capabilityOverrides"])
    flags = legacy_boolean_capabilities(profile)
    context["transport_flags"] = {
        key: value
        for key, value in flags.items()
        if key in {"thinking_required", "tool_stream", "disable_tool_choice_with_thinking"}
    }
    return context


def legacy_capability_scope(profile, model=None):
    """v1 parameterSupport scope. Kept only to read old saved documents."""
    context = {}
    defaults, overrides = legacy_option_layers(profile)
    for label, options in (("generationDefaults", defaults), ("requestOverrides", overrides)):
        context[label] = {
            key: value
            for key, value in options.items()
            if key not in (
                *LEGACY_PARAMETER_NAMES,
                "top_p",
                "max_tokens",
                "max_completion_tokens",
                "response_format",
            )
        }
        if isinstance(context[label].get("extra_body"), dict):
            context[label]["extra_body"] = {
                key: value
                for key, value in context[label]["extra_body"].items()
                if key not in LEGACY_PARAMETER_NAMES
            }
    return {
        "base_url": str(profile.get("baseUrl") or "").strip().rstrip("/"),
        "model": str(model if model is not None else profile.get("model") or "").strip(),
        "context": hashlib.sha256(
            json.dumps(context, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest(),
    }


def normalize_parameter_support(value):
    """Validate the retired v1 parameterSupport document."""
    if value is None or (isinstance(value, dict) and not value):
        return {}
    if not isinstance(value, dict) or set(value) - {"scope", "parameters"}:
        raise ValueError("Invalid parameter support document")
    scope = value.get("scope")
    parameters = value.get("parameters")
    if not isinstance(scope, dict) or set(scope) != {"base_url", "model", "context"}:
        raise ValueError("Parameter support requires an endpoint/model/context scope")
    if any(not isinstance(v, str) for v in scope.values()):
        raise ValueError("Invalid parameter support scope")
    if not re.fullmatch(r"[a-f0-9]{64}", scope["context"]):
        raise ValueError("Invalid parameter context fingerprint")
    if not isinstance(parameters, dict) or set(parameters) - set(LEGACY_PARAMETER_NAMES):
        raise ValueError("Unsupported parameter contract")
    result = {"scope": dict(scope), "parameters": {}}
    for name, item in parameters.items():
        if not isinstance(item, dict) or set(item) - {
            "status", "source", "values", "minimum", "maximum", "step", "reasoning_effort"
        }:
            raise ValueError("Invalid parameter descriptor")
        if item.get("status") not in STATUSES or item.get("source") not in SOURCES:
            raise ValueError("Invalid parameter evidence")
        descriptor = copy.deepcopy(item)
        values = item.get("values")
        if values is not None:
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError("Parameter values must be a nonempty bounded list")
            if name == "reasoning_effort":
                if any(
                    not isinstance(v, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v)
                    for v in values
                ):
                    raise ValueError("Invalid reasoning effort value")
            elif any(not _number(v) or not 0 <= v <= 2 for v in values):
                raise ValueError("Invalid temperature value")
            if len(values) != len(set(values)):
                raise ValueError("Duplicate parameter values")
        if any(k in item for k in ("minimum", "maximum", "step")):
            if name != "temperature" or not all(
                _number(item.get(k)) for k in ("minimum", "maximum", "step")
            ):
                raise ValueError("A temperature range requires finite minimum, maximum and step")
            if (
                not 0 <= item["minimum"] < item["maximum"] <= 2
                or not 0 < item["step"] <= 2
            ):
                raise ValueError("Invalid temperature range")
        if item["status"] in {"supported", "fixed"} and values is None and "minimum" not in item:
            raise ValueError("Adjustable parameters require declared values or a range")
        if item["status"] == "fixed" and (values is None or len(values) != 1):
            raise ValueError("Fixed parameters require exactly one value")
        if "reasoning_effort" in item:
            effort = item["reasoning_effort"]
            if name != "temperature" or (
                effort is not None
                and (
                    not isinstance(effort, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", effort)
                )
            ):
                raise ValueError("Invalid temperature mode condition")
        result["parameters"][name] = descriptor
    return result


capability_scope = legacy_capability_scope


def scoped_parameters(profile, model=None):
    support = normalize_parameter_support(profile.get("parameterSupport") or {})
    return (
        support.get("parameters", {})
        if support.get("scope") == legacy_capability_scope(profile, model)
        else {}
    )


def effective_parameter(profile, name):
    """Read the old defaults/overrides precedence for migration only."""
    value, extra = None, {}
    for options in legacy_option_layers(profile):
        if name in options:
            value = options[name]
        if "extra_body" in options:
            body = options["extra_body"]
            if body is None:
                extra = {}
            elif isinstance(body, Mapping) and name in body:
                if body[name] is None:
                    extra.pop(name, None)
                else:
                    extra[name] = body[name]
    return extra.get(name, value)


def apply_parameter_contract(params, profile, model=None):
    """Legacy v1 parameter application retained only for migration regression tests."""
    params = copy.deepcopy(params)
    descriptors = scoped_parameters(profile, model)
    for name, descriptor in descriptors.items():
        if descriptor["status"] in {"supported", "fixed", "unsupported"}:
            params.pop(name, None)
            (params.get("extra_body") or {}).pop(name, None)
            selected = effective_parameter(profile, name)
            if descriptor["status"] == "supported" and selected is not None:
                params[name] = copy.deepcopy(selected)
    for name, descriptor in descriptors.items():
        extra = params.get("extra_body") or {}
        value = extra.get(name, params.get(name))
        if descriptor["status"] in {"unsupported", "fixed"}:
            params.pop(name, None)
            extra.pop(name, None)
            continue
        if value is None or descriptor["status"] != "supported":
            continue
        values = descriptor.get("values")
        if (values is not None and value not in values) or (
            name == "temperature" and (not _number(value) or not 0 <= value <= 2)
        ):
            raise ValueError(f"{name} is outside its detected parameter contract")
        if "minimum" in descriptor and not descriptor["minimum"] <= value <= descriptor["maximum"]:
            raise ValueError("temperature is outside its detected range")
        if name == "temperature" and "reasoning_effort" in descriptor:
            effort = extra.get("reasoning_effort", params.get("reasoning_effort"))
            if effort != descriptor["reasoning_effort"]:
                raise ValueError(
                    "Reasoning mode changed; reset temperature or detect capabilities again"
                )
    return params


def metadata_parameters(metadata):
    """Read the retired two-parameter metadata projection."""
    result = {}
    schema = metadata.get("parameters") or metadata.get("parameter_schema") or {}
    if not isinstance(schema, Mapping):
        return result
    schema = schema.get("properties", schema)
    if not isinstance(schema, Mapping):
        return result
    for name in LEGACY_PARAMETER_NAMES:
        raw = schema.get(name)
        if not isinstance(raw, Mapping):
            continue
        item = {"status": "supported", "source": "metadata"}
        if "enum" in raw:
            item["values"] = raw["enum"]
        elif "const" in raw:
            item.update(status="fixed", values=[raw["const"]])
        elif name == "temperature" and "minimum" in raw and "maximum" in raw:
            item.update(
                minimum=raw["minimum"],
                maximum=raw["maximum"],
                step=raw.get("multipleOf", 0.05),
            )
        else:
            continue
        try:
            checked = normalize_parameter_support(
                {
                    "scope": legacy_capability_scope({}),
                    "parameters": {name: item},
                }
            )
            result[name] = checked["parameters"][name]
        except (TypeError, ValueError):
            continue
    return result


def legacy_capability_descriptors(profile):
    raw = legacy_boolean_capabilities(profile)
    result = {}
    for name, value in raw.items():
        if name in _LEGACY_CAPABILITY_ALIASES:
            continue
        try:
            identifier(name)
            result[name] = CapabilityDescriptor.from_dict(
                {
                    "status": "supported" if value else "unsupported",
                    "source": "legacy",
                }
            )
        except ValueError:
            continue
    for old_name, canonical in _LEGACY_CAPABILITY_ALIASES.items():
        value = raw.get(old_name, MISSING)
        if canonical in result or value is MISSING:
            continue
        result[canonical] = CapabilityDescriptor.from_dict(
            {
                "status": "supported" if value else "unsupported",
                "source": "legacy",
            }
        )
    return result


def legacy_contract(profile, api_key=None):
    """Project a v1 profile into a v3 contract/settings pair without mutation."""
    legacy = scoped_parameters(profile)
    if not legacy and not legacy_boolean_capabilities(profile):
        return None, {}
    parameters = {}
    for name, raw in legacy.items():
        item = {
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key != "reasoning_effort"
        }
        item["type"] = "number" if name == "temperature" else "enum"
        if "reasoning_effort" in raw:
            item["requires"] = {"reasoning_effort": [raw["reasoning_effort"]]}
        parameters[name] = ParameterDescriptor.from_dict(name, item)
    for descriptor in tuple(parameters.values()):
        for name in descriptor.constraints.requires:
            parameters.setdefault(
                name,
                ParameterDescriptor.from_dict(
                    name,
                    {"type": "enum", "status": "unknown", "source": "legacy"},
                ),
            )
    capabilities = legacy_capability_descriptors(profile)
    from model_runtime.contract import contract_scope
    contract = CapabilityContract(
        contract_scope(profile, parameters, api_key=api_key),
        capabilities,
        parameters,
    )
    selections = {}
    for name, descriptor in parameters.items():
        value = effective_parameter(profile, name)
        if descriptor.status in {"supported", "accepted", "unknown", "fixed", "unsupported"}:
            selections[name] = (
                {"mode": "omit"}
                if value is None or descriptor.status in {"fixed", "unsupported"}
                else {"mode": "value", "value": value}
            )
    return contract, UserModelSettings(
        dict(contract.scope), selections
    ).to_dict()


def _parameter_layer_value(layer, name, descriptor):
    value = descriptor.read(layer)
    if value is MISSING:
        value = path_get(layer, ("extra_body", name), path_get(layer, (name,)))
    if value is not MISSING:
        return value
    for path in (
        ("extra_body",) + descriptor.wire_path,
        descriptor.wire_path,
        ("extra_body", name),
        (name,),
    ):
        for length in range(1, len(path)):
            if path_get(layer, path[:length]) is None:
                return None
    return MISSING


def _merge_evidence(primary, secondary):
    result = copy.deepcopy(dict(primary or {}))
    for key, value in (secondary or {}).items():
        result.setdefault(key, copy.deepcopy(value))
    return result


def _legacy_wire_locations(profile, parameters):
    result = dict(parameters)
    defaults, overrides = legacy_option_layers(profile)
    for name, descriptor in parameters.items():
        if (
            descriptor.source not in {"generic", "probe", "legacy"}
            or descriptor.wire_path != (name,)
        ):
            continue
        if any(
            path_get(layer, ("extra_body", name)) is not MISSING
            for layer in (defaults, overrides)
        ):
            result[name] = replace(
                descriptor,
                wire_location="extra_body",
                wire_path=(name,),
            )
    return result


def merge_legacy_contract(profile, base, *, api_key, model):
    """Merge legacy evidence into a canonical local/catalog contract."""
    parameters = dict(base.parameters)
    capabilities = dict(base.capabilities)
    constraints = dict(base.constraints)

    old_contract, _ = legacy_contract(profile, api_key)
    if old_contract is not None:
        for name, old in old_contract.parameters.items():
            current = parameters.get(name)
            if current is None or current.source == "generic":
                parameters[name] = replace(
                    old,
                    ui_hint=(
                        current.ui_hint
                        if current is not None and current.ui_hint
                        else old.ui_hint
                    ),
                )
            elif (
                old.evidence
                and current.type == old.type
                and current.wire_path == old.wire_path
            ):
                parameters[name] = replace(
                    current,
                    evidence=_merge_evidence(current.evidence, old.evidence),
                )

    for name, old in legacy_capability_descriptors(profile).items():
        current = capabilities.get(name)
        if current is None or current.source == "generic":
            capabilities[name] = old

    parameters = _legacy_wire_locations(profile, parameters)
    from model_runtime.contract import contract_scope
    scope = contract_scope(profile, parameters, model=model, api_key=api_key)
    return CapabilityContract.from_dict(
        CapabilityContract(scope, capabilities, parameters, constraints).to_dict()
    )


def runtime_option_layers(profile, contract, *, promote):
    """Return migrated v3 settings plus detached residual option layers."""
    defaults, overrides = legacy_option_layers(profile)
    selections = {}
    if promote:
        for name, descriptor in contract.parameters.items():
            selected = MISSING
            for layer in (defaults, overrides):
                value = _parameter_layer_value(layer, name, descriptor)
                if value is not MISSING:
                    selected = value
            if selected is not MISSING:
                if selected is None or descriptor.status in {"fixed", "unsupported"}:
                    selections[name] = {"mode": "omit"}
                else:
                    descriptor.validate(selected)
                    selections[name] = {
                        "mode": "value",
                        "value": copy.deepcopy(selected),
                    }
    for name, descriptor in contract.parameters.items():
        descriptor.remove(defaults, name)
        descriptor.remove(overrides, name)
    settings = UserModelSettings.from_dict(
        {"scope": dict(contract.scope), "parameters": selections},
        contract,
    )
    return settings, defaults, overrides


def detection_profile(profile, parameters=()):
    """Build a legacy-compatible probe profile without optional tuning controls."""
    result = copy.deepcopy(dict(profile))
    declared = dict(parameters or {})
    result.pop("parameterSupport", None)
    result.pop("capabilityContract", None)
    result.pop("userModelSettings", None)
    optional = {
        "temperature", "reasoning_effort", "top_p", "response_format",
        "max_tokens", "max_completion_tokens", "stream_options",
    }
    for group in ("generationDefaults", "requestOverrides"):
        options = result.setdefault(group, {})
        for name, descriptor in declared.items():
            descriptor.remove(options, name)
        for key in optional:
            options.pop(key, None)
            if isinstance(options.get("extra_body"), dict):
                options["extra_body"].pop(key, None)
    return result


def clear_legacy_detection(profile):
    """Clear retired detection evidence after identity/credential changes."""
    profile.pop("parameterSupport", None)


def strip_legacy_runtime_fields(profile):
    """Remove retired runtime inputs when explicitly persisting a canonical profile."""
    for name in LEGACY_PROFILE_FIELDS:
        profile.pop(name, None)
    return profile
