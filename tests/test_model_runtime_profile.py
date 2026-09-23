"""Runtime model-profile materialization tests; no network or provider calls."""
import copy

import pytest

from model_runtime.contract import (
    CapabilityContract,
    CapabilityDescriptor,
    ParameterDescriptor,
    UserModelSettings,
    contract_scope,
)
from model_runtime.runtime_profile import RuntimeModelProfile, materialize_runtime_profile
from model_runtime.request_policy import resolve_request
from test_model_capabilities import descriptor, profile


def current_profile(parameters=None, capabilities=None, **changes):
    p = profile(**changes)
    params = {
        name: ParameterDescriptor.from_dict(
            name,
            {"type": "number", "status": "supported", "source": "manual", **raw},
        )
        for name, raw in (parameters or {}).items()
    }
    caps = {
        name: CapabilityDescriptor.from_dict(
            {"status": "supported", "source": "manual", **raw}
        )
        for name, raw in (capabilities or {}).items()
    }
    contract = CapabilityContract(
        contract_scope(p, params, api_key="key"),
        caps,
        params,
    )
    p["capabilityContract"] = contract.to_dict()
    return p


def test_current_contract_materializes_without_reinterpreting_v3_inherit():
    p = current_profile(
        {"budget": {"type": "integer", "minimum": 0, "maximum": 4096}},
        generationDefaults={"budget": 1024, "extra_body": {"keep": True}},
    )
    p["userModelSettings"] = {
        "scope": p["capabilityContract"]["scope"],
        "parameters": {"budget": {"mode": "inherit"}},
    }
    before = copy.deepcopy(p)

    runtime = materialize_runtime_profile(p, api_key="key")

    assert isinstance(runtime, RuntimeModelProfile)
    assert runtime.model == "tenant-alias"
    assert runtime.contract.parameters["budget"].type == "integer"
    assert runtime.settings.parameters["budget"] == {"mode": "inherit"}
    assert runtime.defaults == {"budget": 1024, "extra_body": {"keep": True}}
    assert runtime.overrides == {}
    assert p == before


def test_legacy_parameter_support_and_capabilities_become_runtime_contract():
    p = profile(
        generationDefaults={
            "reasoning_effort": "high",
            "temperature": 0.5,
            "extra_body": {"keep": True},
        },
        capabilities={"multimodal": True, "tool_stream": True},
    )
    p["parameterSupport"] = descriptor(
        p,
        reasoning_effort={
            "status": "supported",
            "source": "probe",
            "values": ["low", "high"],
        },
        temperature={
            "status": "supported",
            "source": "probe",
            "values": [0, 0.5],
            "reasoning_effort": "high",
        },
    )
    before = copy.deepcopy(p)

    runtime = materialize_runtime_profile(p, api_key="key")

    assert runtime.contract.capabilities["vision"].status == "supported"
    assert runtime.contract.capabilities["vision"].source == "legacy"
    assert runtime.contract.capabilities["tool_stream"].status == "supported"
    assert runtime.settings.parameters["reasoning_effort"] == {
        "mode": "value",
        "value": "high",
    }
    assert runtime.settings.parameters["temperature"] == {
        "mode": "value",
        "value": 0.5,
    }
    assert runtime.contract.parameters["temperature"].evidence["observed_context"] == {
        "requires": {"reasoning_effort": ["high"]}
    }
    assert runtime.defaults == {"extra_body": {"keep": True}}
    assert runtime.overrides == {}
    assert p == before


def test_legacy_catalog_owned_values_are_promoted_and_removed_from_option_layers():
    p = {
        "id": "legacy-deepseek",
        "adapter": "openai_compatible",
        "baseUrl": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "capabilities": {
            "reasoning": True,
            "tools": True,
            "structured_output": True,
            "disable_tool_choice_with_thinking": True,
        },
        "generationDefaults": {
            "temperature": 1.0,
            "top_p": 0.95,
            "reasoning_effort": "max",
            "response_format": {"type": "json_object"},
        },
        "requestOverrides": {
            "extra_body": {
                "thinking": {
                    "type": "enabled",
                    "clear_thinking": False,
                }
            }
        },
    }
    before = copy.deepcopy(p)

    runtime = materialize_runtime_profile(p, api_key="key")

    assert runtime.settings.parameters["temperature"] == {
        "mode": "value",
        "value": 1.0,
    }
    assert runtime.settings.parameters["top_p"] == {
        "mode": "value",
        "value": 0.95,
    }
    assert runtime.settings.parameters["reasoning_effort"] == {
        "mode": "value",
        "value": "max",
    }
    assert runtime.settings.parameters["thinking_mode"] == {
        "mode": "value",
        "value": "enabled",
    }
    assert runtime.defaults == {"response_format": {"type": "json_object"}}
    assert runtime.overrides == {
        "extra_body": {"thinking": {"clear_thinking": False}}
    }
    assert runtime.contract.capabilities["tools"].source == "catalog"
    assert runtime.contract.capabilities["disable_tool_choice_with_thinking"].source == "legacy"
    assert p == before


def test_legacy_explicit_null_becomes_user_omit_not_a_missing_selection():
    p = profile(
        generationDefaults={"reasoning_effort": "high"},
        requestOverrides={"reasoning_effort": None},
    )

    runtime = materialize_runtime_profile(p, api_key="key")

    assert runtime.settings.parameters["reasoning_effort"] == {"mode": "omit"}
    assert "reasoning_effort" not in runtime.defaults
    assert "reasoning_effort" not in runtime.overrides


def test_stale_v3_selections_are_not_rebound_to_another_model():
    p = current_profile({"budget": {"type": "integer"}})
    p["userModelSettings"] = {
        "scope": p["capabilityContract"]["scope"],
        "parameters": {"budget": {"mode": "value", "value": 1024}},
    }
    p["model"] = "other-model"

    with pytest.raises(ValueError, match="Capability scope changed"):
        materialize_runtime_profile(p, api_key="key")


def test_runtime_settings_are_scoped_to_materialized_contract():
    p = profile(
        generationDefaults={"temperature": 0.7},
        capabilities={"multimodal": False},
    )

    runtime = materialize_runtime_profile(p, api_key="key")

    assert isinstance(runtime.settings, UserModelSettings)
    assert runtime.settings.scope == runtime.contract.scope
    assert runtime.contract.capabilities["vision"].status == "unsupported"


def test_materialized_runtime_is_a_direct_resolver_input_without_legacy_fields():
    p = profile(
        generationDefaults={"reasoning_effort": "high"},
        capabilities={"tools": True, "multimodal": True},
    )

    runtime = materialize_runtime_profile(p, api_key="key")
    resolved = resolve_request(
        runtime,
        {"reasoning_effort": "low"},
        protocol={"stream": False},
        model=runtime.model,
        api_key="key",
    )

    assert resolved.options["reasoning_effort"] == "high"
    assert runtime.contract.capabilities["tools"].status == "supported"
    assert runtime.contract.capabilities["vision"].status == "supported"
    assert not hasattr(runtime, "parameterSupport")
    assert not hasattr(runtime, "capabilities")
