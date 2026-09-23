"""Materialize persisted model profiles into one runtime capability snapshot.

This boundary is deliberately local: it performs no model calls, does not mutate
persisted configuration, and removes legacy capability/parameter schemas before
the request runtime sees the profile.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from model_runtime.catalog import ModelCatalog, resolve_capabilities
from model_runtime.contract import (
    MISSING,
    CapabilityContract,
    CapabilityDescriptor,
    UserModelSettings,
    contract_scope,
    legacy_contract,
    path_get,
    scoped_contract,
)


_LEGACY_CAPABILITY_ALIASES = {
    "multimodal": "vision",
}


@dataclass(frozen=True)
class RuntimeModelProfile:
    """Detached, typed model configuration consumed by the request runtime.

    Legacy persisted field names intentionally do not appear here. The defaults
    and overrides members are request-option layers, not capability evidence.
    Capability semantics live only in contract and explicit selections live
    only in settings.
    """

    adapter: str
    endpoint: str
    model: str
    contract: CapabilityContract
    settings: UserModelSettings
    defaults: Mapping[str, Any]
    overrides: Mapping[str, Any]
    profile_id: str = ""


def _parameter_layer_value(layer, name, descriptor):
    value = descriptor.read(layer)
    if value is MISSING:
        value = path_get(layer, ("extra_body", name), path_get(layer, (name,)))
    if value is not MISSING:
        return value
    # A null parent is an explicit deletion of every mapped descendant.
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
    """Preserve explicit legacy wire placement when no authoritative mapping exists.

    Old request templates could place an otherwise generic scalar directly under
    extra_body. That placement is configuration evidence, not a provider/model
    special case. Catalog/metadata/manual wire mappings remain authoritative.
    """
    result = dict(parameters)
    for name, descriptor in parameters.items():
        if (
            descriptor.source not in {"generic", "probe", "legacy"}
            or descriptor.wire_path != (name,)
        ):
            continue
        nested = False
        for group in ("generationDefaults", "requestOverrides"):
            layer = profile.get(group) or {}
            if path_get(layer, ("extra_body", name)) is not MISSING:
                nested = True
        if nested:
            result[name] = replace(
                descriptor,
                wire_location="extra_body",
                wire_path=(name,),
            )
    return result


def _legacy_capabilities(profile):
    raw = profile.get("capabilities") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("Legacy capabilities must be an object")

    result = {}
    # Canonical legacy names win over aliases if both are present.
    for name, value in raw.items():
        if name in _LEGACY_CAPABILITY_ALIASES or not isinstance(value, bool):
            continue
        result[name] = CapabilityDescriptor.from_dict({
            "status": "supported" if value else "unsupported",
            "source": "legacy",
        })
    for old_name, canonical in _LEGACY_CAPABILITY_ALIASES.items():
        value = raw.get(old_name, MISSING)
        if canonical in result or value is MISSING or not isinstance(value, bool):
            continue
        result[canonical] = CapabilityDescriptor.from_dict({
            "status": "supported" if value else "unsupported",
            "source": "legacy",
        })
    return result


def _merge_legacy_contract(profile, base, *, api_key, model):
    parameters = dict(base.parameters)
    capabilities = dict(base.capabilities)
    constraints = dict(base.constraints)

    legacy, _ = legacy_contract(profile, api_key)
    if legacy is not None:
        for name, old in legacy.parameters.items():
            current = parameters.get(name)
            if current is None or current.source == "generic":
                # Keep generic presentation hints while replacing its guessed
                # domain/status with the scoped legacy evidence.
                parameters[name] = replace(
                    old,
                    ui_hint=current.ui_hint if current is not None and current.ui_hint else old.ui_hint,
                )
                continue
            if (
                old.evidence
                and current.type == old.type
                and current.wire_path == old.wire_path
            ):
                parameters[name] = replace(
                    current,
                    evidence=_merge_evidence(current.evidence, old.evidence),
                )

    for name, old in _legacy_capabilities(profile).items():
        current = capabilities.get(name)
        if current is None or current.source == "generic":
            capabilities[name] = old

    parameters = _legacy_wire_locations(profile, parameters)
    scope = contract_scope(profile, parameters, model=model, api_key=api_key)
    return CapabilityContract.from_dict(
        CapabilityContract(scope, capabilities, parameters, constraints).to_dict()
    )


def _rescope_settings(raw_settings, source_contract, target_contract):
    if not raw_settings:
        return UserModelSettings(dict(target_contract.scope), {})
    current = UserModelSettings.from_dict(raw_settings, source_contract)
    selected = copy.deepcopy(dict(current.parameters))
    return UserModelSettings.from_dict(
        {"scope": dict(target_contract.scope), "parameters": selected},
        target_contract,
    )


def _promote_legacy_options(contract, defaults, overrides):
    """Move contract-owned legacy option values into UserModelSettings.

    This is used only when there is no valid current contract. It turns old
    generationDefaults/requestOverrides tuning into explicit selections while
    retaining unrelated provider extensions in the canonical option layers.
    """

    defaults = copy.deepcopy(dict(defaults or {}))
    overrides = copy.deepcopy(dict(overrides or {}))
    choices = {}

    for name, descriptor in contract.parameters.items():
        selected = MISSING
        for layer in (defaults, overrides):
            value = _parameter_layer_value(layer, name, descriptor)
            if value is not MISSING:
                selected = value

        if selected is not MISSING:
            if selected is None or descriptor.status in {"fixed", "unsupported"}:
                choices[name] = {"mode": "omit"}
            else:
                descriptor.validate(selected)
                choices[name] = {"mode": "value", "value": copy.deepcopy(selected)}

        # Contract-owned controls must not survive as a second runtime source.
        descriptor.remove(defaults, name)
        descriptor.remove(overrides, name)

    settings = UserModelSettings.from_dict(
        {"scope": dict(contract.scope), "parameters": choices},
        contract,
    )
    return settings, defaults, overrides


def materialize_runtime_profile(
    profile: Mapping[str, Any],
    *,
    api_key: Optional[str],
    model: Optional[str] = None,
    catalog: Optional[ModelCatalog] = None,
) -> RuntimeModelProfile:
    """Return a detached runtime profile with one capability representation.

    A valid scoped v3 contract is preserved. Otherwise capability information is
    resolved locally from the catalog/manual overrides and enriched with scoped
    v1 parameter evidence plus legacy boolean capabilities. No generation,
    metadata fetch, persistence, or profile mutation occurs here.

    Stale v3 selections are never carried to a different endpoint/model/key.
    """

    source = copy.deepcopy(dict(profile))
    adapter = str(source.get("adapter") or "").strip()
    if adapter != "openai_compatible":
        raise ValueError("RuntimeModelProfile currently supports openai_compatible only")

    endpoint = str(source.get("baseUrl") or "").strip().rstrip("/")
    selected_model = str(model if model is not None else source.get("model") or "").strip()
    if not endpoint or not selected_model or selected_model == "__discover__":
        raise ValueError("Runtime model profile requires endpoint and model")

    raw_contract = source.get("capabilityContract") or {}
    raw_settings = source.get("userModelSettings") or {}
    current = scoped_contract(source, selected_model, api_key)
    if raw_contract and current is None and (raw_settings.get("parameters") or {}):
        raise ValueError(
            "Capability scope changed; reset selections or resolve this model again"
        )

    if current is None:
        resolved = resolve_capabilities(
            source,
            api_key=api_key,
            catalog=catalog,
        )
        base_contract = CapabilityContract.from_dict(resolved["contract"])
    else:
        base_contract = current

    contract = _merge_legacy_contract(
        source,
        base_contract,
        api_key=api_key,
        model=selected_model,
    )

    defaults = copy.deepcopy(source.get("generationDefaults") or {})
    overrides = copy.deepcopy(source.get("requestOverrides") or {})

    if current is not None:
        settings = _rescope_settings(raw_settings, current, contract)
    else:
        settings, defaults, overrides = _promote_legacy_options(
            contract,
            defaults,
            overrides,
        )

    return RuntimeModelProfile(
        adapter=adapter,
        endpoint=endpoint,
        model=selected_model,
        contract=contract,
        settings=settings,
        defaults=defaults,
        overrides=overrides,
        profile_id=str(source.get("id") or ""),
    )
