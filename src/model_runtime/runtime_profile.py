"""Materialize persisted model profiles into one runtime capability snapshot.

This boundary performs no model calls and never interprets retired profile
fields directly. Legacy schema translation is isolated in legacy_migration.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from model_runtime.catalog import ModelCatalog, resolve_capabilities
from model_runtime.contract import (
    CapabilityContract,
    UserModelSettings,
    scoped_contract,
)
from model_runtime.legacy_migration import (
    merge_legacy_contract,
    runtime_option_layers,
)


@dataclass(frozen=True)
class RuntimeModelProfile:
    """Detached, typed model configuration consumed by request resolution."""

    adapter: str
    endpoint: str
    model: str
    contract: CapabilityContract
    settings: UserModelSettings
    defaults: Mapping[str, Any]
    overrides: Mapping[str, Any]
    profile_id: str = ""


def _rescope_settings(raw_settings, source_contract, target_contract):
    if not raw_settings:
        return UserModelSettings(dict(target_contract.scope), {})
    current = UserModelSettings.from_dict(raw_settings, source_contract)
    return UserModelSettings.from_dict(
        {
            "scope": dict(target_contract.scope),
            "parameters": copy.deepcopy(dict(current.parameters)),
        },
        target_contract,
    )


def materialize_runtime_profile(
    profile: Mapping[str, Any],
    *,
    api_key: Optional[str],
    model: Optional[str] = None,
    catalog: Optional[ModelCatalog] = None,
) -> RuntimeModelProfile:
    """Return one v3 runtime snapshot from canonical or legacy persisted input."""

    source = copy.deepcopy(dict(profile))
    adapter = str(source.get("adapter") or "").strip()
    if adapter != "openai_compatible":
        raise ValueError(
            "RuntimeModelProfile currently supports openai_compatible only"
        )

    endpoint = str(source.get("baseUrl") or "").strip().rstrip("/")
    selected_model = str(
        model if model is not None else source.get("model") or ""
    ).strip()
    if not endpoint or not selected_model or selected_model == "__discover__":
        raise ValueError("Runtime model profile requires endpoint and model")

    raw_contract = source.get("capabilityContract") or {}
    raw_settings = source.get("userModelSettings") or {}
    current = scoped_contract(source, selected_model, api_key)
    if (
        raw_contract
        and current is None
        and (raw_settings.get("parameters") or {})
    ):
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

    contract = merge_legacy_contract(
        source,
        base_contract,
        api_key=api_key,
        model=selected_model,
    )

    migrated_settings, defaults, overrides = runtime_option_layers(
        source,
        contract,
        promote=current is None,
    )
    settings = (
        _rescope_settings(raw_settings, current, contract)
        if current is not None
        else migrated_settings
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
