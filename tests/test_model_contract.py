"""Contract v2 regression tests: no provider keys, external calls or PLC access."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from model_runtime.contract import (
    CapabilityContract, CapabilityDescriptor, ConstraintDescriptor, ParameterDescriptor,
    UserModelSettings, contract_scope, legacy_contract, metadata_contract_parts, normalize_contract,
)
from model_runtime.request_policy import resolve_request, public_contract_settings
from model_runtime.provider import OpenAICompatibleProvider, ModelRequest, ModelProviderError, UserMessage
from application.model_detection import inspect_openai_compatible
from application.model_detection import list_metadata
from test_model_capabilities import Endpoint, profile, provider, descriptor
from test_application_settings import settings_env


def make_profile(parameters=None, capabilities=None, constraints=None, **changes):
    p = profile(**changes)
    params = {name: ParameterDescriptor.from_dict(name, {"type": "number", "status": "supported", "source": "manual", **raw})
              for name, raw in (parameters or {}).items()}
    caps = {name: CapabilityDescriptor.from_dict({"status": "supported", "source": "manual", **raw})
            for name, raw in (capabilities or {}).items()}
    rules = {name: ConstraintDescriptor.from_dict(raw) for name, raw in (constraints or {}).items()}
    contract = CapabilityContract(contract_scope(p, params, api_key="key"), caps, params, rules)
    p["capabilityContract"] = contract.to_dict()
    return p


def select(p, **values):
    p["userModelSettings"] = {"scope": p["capabilityContract"]["scope"], "parameters": {
        name: {"mode": "omit"} if value is None else {"mode": "value", "value": value}
        for name, value in values.items()}}
    return p


def test_typed_round_trip_accepts_unknown_names_not_a_hardcoded_parameter_list():
    p = make_profile({"never_seen_before": {"type": "integer", "minimum": 0, "maximum": 10000, "step": 128},
                      "dialect": {"type": "enum", "values": ["quiet", "verbose"]},
                      "feature_flag": {"type": "boolean"}})
    c = CapabilityContract.from_dict(p["capabilityContract"])
    assert isinstance(c.parameters["feature_flag"], ParameterDescriptor)
    assert c.to_dict() == p["capabilityContract"]
    assert normalize_contract(c.to_dict()) == c.to_dict()


@pytest.mark.parametrize("changes", [
    {"type": "nonsense"}, {"source": "guess"}, {"step": 0}, {"minimum": float("nan")},
    {"minimum": 2, "maximum": 0}, {"values": [1, 1]}, {"values": []},
    {"wire_location": "header"}, {"wire_path": ["messages"]}, {"wire_path": ["thinking", "api_key"]},
    {"wire_path": ["__proto__", "polluted"]}, {"wire_path": ["extra_headers", "Authorization"]},
    {"wire_path": ["extra_body"]}, {"status": "conditional"}, {"extra": True},
])
def test_untrusted_descriptors_cannot_widen_protocol_or_forge_valid_controls(changes):
    with pytest.raises(ValueError):
        ParameterDescriptor.from_dict("knob", {"type": "number", "status": "supported", "source": "metadata", **changes})


def test_boolean_numeric_type_and_grid_validation_are_strict():
    number = ParameterDescriptor.from_dict("number", {"type": "number", "status": "supported", "source": "manual", "minimum": 0, "maximum": 2, "step": .1})
    number.validate(.7)
    for bad in (True, .75, float("inf"), -1, "0.7"):
        with pytest.raises(ValueError): number.validate(bad)
    discrete = replace(number, values=(0, .5, 1.), step=None)
    with pytest.raises(ValueError): discrete.validate(.7)
    boolean = ParameterDescriptor.from_dict("toggle", {"type": "boolean", "status": "supported", "source": "manual"})
    boolean.validate(False)
    with pytest.raises(ValueError): boolean.validate(0)


@pytest.mark.parametrize("paths", [(["nested"], ["nested", "child"]), (["same"], ["same"])])
def test_overlapping_wire_mappings_are_rejected(paths):
    p = make_profile({"first": {"wire_location": "extra_body", "wire_path": paths[0]},
                      "second": {"wire_path": paths[1]}})
    with pytest.raises(ValueError): CapabilityContract.from_dict(p["capabilityContract"])


def test_user_omit_is_a_tombstone_and_inherit_is_not_omit():
    p = make_profile({"effort": {"type": "enum", "values": ["low", "max"]}}, generationDefaults={"effort": "low"})
    assert resolve_request(p, {"effort": "max"}, api_key="key").options["effort"] == "max"
    select(p, effort="low")
    assert resolve_request(p, {"effort": "max"}, api_key="key").options["effort"] == "low"
    select(p, effort=None)
    assert "effort" not in resolve_request(p, {"effort": "max"}, api_key="key").options
    p["userModelSettings"]["parameters"]["effort"] = {"mode": "inherit"}
    assert resolve_request(p, {"effort": "max"}, api_key="key").options["effort"] == "max"


def test_nested_budget_mapping_and_priority_preserve_unrelated_extensions():
    p = make_profile({"budget": {"type": "integer", "minimum": 0, "maximum": 32768, "step": 1024,
         "wire_location": "extra_body", "wire_path": ["thinking", "budget_tokens"]}},
         generationDefaults={"extra_body": {"thinking": {"budget_tokens": 1024, "type": "enabled"}}},
         requestOverrides={"budget": 4096, "extra_body": {"unrelated": True}})
    select(p, budget=8192)
    before = copy.deepcopy(p)
    result = resolve_request(p, {"budget": 2048}, api_key="key")
    assert result.options["extra_body"] == {"thinking": {"budget_tokens": 8192, "type": "enabled"}, "unrelated": True}
    assert "budget" not in result.options
    assert result.sources["budget"] == "user_selection"
    assert p == before
    select(p, budget=None)
    result = resolve_request(p, {"budget": 2048}, api_key="key")
    assert "budget_tokens" not in result.options["extra_body"]["thinking"]


def test_protocol_is_authoritative_and_contract_cannot_replace_required_tools():
    p = make_profile({"knob": {"type": "boolean"}}, capabilities={"tools": {"status": "unsupported"}})
    select(p, knob=True)
    model = OpenAICompatibleProvider(p, "key", client=Endpoint())
    with pytest.raises(ModelProviderError):
        model._request_params(ModelRequest((UserMessage("check"),), tools=({"type": "function"},), stream=False))
    p = make_profile({"knob": {"type": "boolean"}}, requestOverrides={"extra_body": {"messages": []}})
    with pytest.raises(ModelProviderError):
        OpenAICompatibleProvider(p, "key", client=Endpoint())._request_params(ModelRequest((UserMessage("check"),), stream=False))


def test_requires_and_conflicts_use_effective_values_not_observations():
    p = make_profile({"thinking": {"type": "boolean"},
         "budget": {"type": "integer", "status": "conditional", "requires": {"thinking": [True]}},
         "temp": {"conflicts_with": ["budget"]}})
    select(p, thinking=False, budget=1024)
    with pytest.raises(ValueError): resolve_request(p, api_key="key")
    select(p, thinking=True, budget=1024)
    assert resolve_request(p, api_key="key").options["budget"] == 1024
    select(p, thinking=True, budget=1024, temp=.5)
    with pytest.raises(ValueError): resolve_request(p, api_key="key")


def test_top_level_constraints_and_request_conditions_are_enforced():
    p = make_profile({"parallel_tool_calls": {"type": "boolean"}},
         constraints={"parallel_tool_calls": {"requires": {"stream": [True]}}})
    select(p, parallel_tool_calls=True)
    with pytest.raises(ValueError): resolve_request(p, protocol={"stream": False}, api_key="key")
    assert resolve_request(p, protocol={"stream": True}, api_key="key").options["parallel_tool_calls"]


@pytest.mark.parametrize("change", [{"model": "other"}, {"baseUrl": "https://else.invalid/v1"},
    {"requestOverrides": {"extra_body": {"non_parameter_context": True}}}])
def test_stale_contract_selections_cannot_leak_to_another_scope(change):
    p = select(make_profile({"budget": {"type": "integer"}}), budget=1024)
    p.update(change)
    with pytest.raises(ValueError): resolve_request(p, api_key="key")
    assert public_contract_settings(p, "key") == ({}, {})


def test_key_rotation_is_a_scope_change_but_a_tuning_value_is_not():
    p = select(make_profile({"budget": {"type": "integer"}}), budget=1024)
    with pytest.raises(ValueError): resolve_request(p, api_key="different-key")
    p["generationDefaults"]["budget"] = 2048
    assert resolve_request(p, api_key="key").options["budget"] == 1024


def test_metadata_supplies_arbitrary_controls_without_any_new_probe():
    endpoint = Endpoint(metadata=[{"id": "tenant-alias", "parameters": {
        "vendor_flag": {"type": "boolean"},
        "vendor_budget": {"type": "integer", "minimum": 0, "maximum": 32768, "multipleOf": 1024,
                          "wire_location": "extra_body", "wire_path": ["thinking", "budget_tokens"]},
        "custom_verbosity": {"enum": ["quiet", "normal", "verbose"]}},
        "capabilities": {"vision": True, "audio": False, "structured_output": {"status": "supported", "modes": ["json_schema"]}},
        "context_window": 262144}])
    model_provider = provider(endpoint)
    list_metadata(model_provider)
    result = inspect_openai_compatible(model_provider, "tenant-alias")
    assert "parameter_support" not in result and "probe_results" not in result
    contract = CapabilityContract.from_dict(result["contract"])
    assert contract.parameters["vendor_budget"].wire_path == ("thinking", "budget_tokens")
    assert contract.capabilities["context_window"].value == 262144
    assert contract.capabilities["audio"].status == "unsupported"
    assert contract.capabilities["structured_output"].modes == ("json_schema",)
    assert not any("vendor_budget" in call or "vendor_flag" in call for call in endpoint.calls)


def test_metadata_addition_requires_no_verifier_or_provider_branch():
    endpoint = Endpoint(metadata=[{"id":"tenant-alias","parameters":{"new_knob":{"type":"integer"}}}])
    list_metadata(provider(endpoint))
    result = inspect_openai_compatible(provider(endpoint), "tenant-alias")
    assert result["contract"]["parameters"]["new_knob"]["type"] == "integer"
    assert endpoint.calls == []


def test_capability_probe_failure_is_unknown_not_unsupported():
    class Broken:
        profile = profile()
        def list_models(self, **kwargs): return ["tenant-alias"]
        def stream(self, request): raise ModelProviderError("timeout", code="timeout")
    result = inspect_openai_compatible(Broken(), "tenant-alias")
    assert result["contract"]["capabilities"]["tools"]["status"] == "unknown"
    assert result["contract"]["capabilities"]["structured_output"]["status"] == "unknown"


def test_v1_migration_keeps_evidence_and_user_values_separate_without_mutation():
    p = profile(generationDefaults={"reasoning_effort": "high", "temperature": .5})
    p["parameterSupport"] = descriptor(p,
        reasoning_effort={"status": "supported", "source": "probe", "values": ["low", "high"]},
        temperature={"status": "supported", "source": "probe", "values": [0, .5], "reasoning_effort": "high"})
    before = copy.deepcopy(p)
    c, user = legacy_contract(p, "key")
    assert c.parameters["temperature"].constraints.requires == {}
    assert c.parameters["temperature"].evidence["observed_context"] == {"requires":{"reasoning_effort":["high"]}}
    c.parameters["temperature"].validate(.73)
    assert user["parameters"]["reasoning_effort"] == {"mode": "value", "value": "high"}
    assert "value" not in c.parameters["reasoning_effort"].to_dict()
    assert p == before


def test_settings_persist_contract_selections_and_invalidate_key_without_mutating_get(settings_env):
    env = settings_env
    p = make_profile({"new_budget": {"type": "integer", "minimum": 0, "maximum": 32768}})
    select(p, new_budget=8192)
    result = env.service.create_profile(id="v2", name="V2", model=p["model"], base_url=p["baseUrl"], api_key="key",
        contract=p["capabilityContract"], user_settings=p["userModelSettings"])
    row = next(item for item in result["profiles"] if item["id"] == "v2")
    assert row["user_settings"]["parameters"]["new_budget"]["value"] == 8192
    assert row["generation_defaults"] == {}
    before = env.path.read_bytes()
    assert env.service.public_settings() == result
    assert env.path.read_bytes() == before
    result = env.service.set_key("v2", "rotated-key")
    row = next(item for item in result["profiles"] if item["id"] == "v2")
    assert row["contract"] == {} and row["user_settings"] == {}


def test_contract_scope_and_user_values_are_validated_on_save(settings_env):
    env = settings_env
    p = make_profile({"temp": {"values": [0, .5, 1]}})
    select(p, temp=.7)
    with pytest.raises(ValueError):
        env.service.create_profile(id="v2", name="V2", model=p["model"], base_url=p["baseUrl"], api_key="key",
            contract=p["capabilityContract"], user_settings=p["userModelSettings"])
    assert not env.writes


def test_normal_requests_never_discover_again():
    p = select(make_profile({"new_knob": {"type": "boolean"}}), new_knob=False)
    endpoint = Endpoint()
    model = OpenAICompatibleProvider(p, "key", client=endpoint)
    request = ModelRequest((UserMessage("check"),), stream=False)
    for _ in range(3): assert model._request_params(request)["new_knob"] is False
    assert endpoint.calls == [] and endpoint.options == []


@pytest.mark.parametrize("overlay", [{"extra_body": None}, {"extra_body": {"thinking": None}},
    {"extra_body": {"thinking": {"budget_tokens": None}}}])
def test_parent_deletion_does_not_resurrect_a_mapped_default(overlay):
    p = make_profile({"budget": {"type": "integer", "wire_location": "extra_body",
        "wire_path": ["thinking", "budget_tokens"]}},
        generationDefaults={"extra_body": {"thinking": {"budget_tokens": 1024}}}, requestOverrides=overlay)
    result = resolve_request(p, api_key="key")
    assert "budget_tokens" not in repr(result.options)
    assert result.values["budget"] is None
    select(p, budget=2048)
    assert resolve_request(p, api_key="key").options["extra_body"]["thinking"]["budget_tokens"] == 2048


def test_json_schema_multiple_of_stays_anchored_at_zero():
    from model_runtime.contract import metadata_contract_parts
    parameters, _, _ = metadata_contract_parts({"parameters": {"budget": {
        "type": "integer", "minimum": 100, "maximum": 4096, "multipleOf": 1024}}})
    budget = parameters["budget"]
    assert budget.minimum == 1024
    budget.validate(2048)
    with pytest.raises(ValueError): budget.validate(1124)


def test_legacy_transport_defaults_coexist_with_nested_user_mapping():
    p = make_profile({"budget": {"type": "integer", "wire_location": "extra_body",
        "wire_path": ["thinking", "budget_tokens"]}}, capabilities={})
    p["capabilities"] = {"thinking_required": True}
    # Transport flags form part of the observation context.
    from model_runtime.contract import contract_scope, CapabilityContract
    c = CapabilityContract.from_dict(p["capabilityContract"])
    p["capabilityContract"]["scope"] = contract_scope(p, c.parameters, api_key="key")
    select(p, budget=2048)
    wire = OpenAICompatibleProvider(p, "key", client=Endpoint())._request_params(
        ModelRequest((UserMessage("check"),), stream=False))
    assert wire["extra_body"]["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_public_http_settings_schema_roundtrips_v2_without_exposing_key(settings_env):
    from integrations.web.responses import ModelSettings
    p = select(make_profile({"flag": {"type": "boolean"}}), flag=False)
    public = settings_env.service.create_profile(id="http-v2", name="HTTP", model=p["model"],
        base_url=p["baseUrl"], api_key="key", contract=p["capabilityContract"], user_settings=p["userModelSettings"])
    validated = ModelSettings.model_validate(public).model_dump(mode="json")
    row = next(item for item in validated["profiles"] if item["id"] == "http-v2")
    assert row["contract"]["scope"]["binding"] == p["capabilityContract"]["scope"]["binding"]
    assert row["user_settings"]["parameters"]["flag"] == {"mode": "value", "value": False}
    assert '"api_key"' not in json.dumps(validated)


def test_v2_selections_override_production_workflow_hints():
    import application.model_api as api
    import agent_runtime.agent as plc_agent
    from types import SimpleNamespace
    from test_model_provider import _Client, _chunk
    p = select(make_profile({"reasoning_effort": {"type": "enum", "values": ["low", "max"]},
         "temperature": {"minimum": 0, "maximum": 2, "step": .1}}), reasoning_effort=None, temperature=.7)
    client = _Client([{"choices": [{"message": {"content": "完成。"}}]}, iter([_chunk(content="完成。")])])
    provider = OpenAICompatibleProvider(p, "key", client=client)
    with api.provider_scope(provider):
        api._request_model([{"role": "user", "content": "检查"}], effort="high", stream=False,
                           options={"temperature": 1.5})
    plc_agent.run_tool_agent("检查", context=None, provider=provider, runtime=SimpleNamespace(list_tools=lambda context: []))
    for request in client.completions.calls:
        assert "reasoning_effort" not in request
        assert request["temperature"] == .7


def test_conditional_vision_uses_effective_user_value():
    from model_runtime.provider import ImageAttachment
    p = select(make_profile({"image_mode": {"type": "boolean"}}, capabilities={"vision": {
        "status": "conditional", "requires": {"image_mode": [True]}}}), image_mode=True)
    request = ModelRequest((UserMessage("check", (ImageAttachment("x.png", "image/png", b"synthetic"),)),), stream=False)
    provider = OpenAICompatibleProvider(p, "key", client=Endpoint())
    assert provider._request_params(request)["image_mode"] is True
    select(p, image_mode=False)
    with pytest.raises(ModelProviderError):
        OpenAICompatibleProvider(p, "key", client=Endpoint())._request_params(request)


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("selection", ["missing", "medium", "omit", "advanced", "inherit"])
@pytest.mark.parametrize("hint", ["low", "high", None])
def test_application_effort_is_profile_owned_on_the_actual_wire(legacy, selection, hint):
    from application import model_api as api
    from model_runtime.provider import TextDelta
    p = profile() if legacy else make_profile({"reasoning_effort": {"type": "enum", "values": ["low", "medium", "high"]}})
    if selection in {"medium", "inherit"}:
        if legacy or selection == "inherit":
            p["generationDefaults"] = {"reasoning_effort": "medium"}
        else:
            select(p, reasoning_effort="medium")
    elif selection == "omit":
        p["generationDefaults"] = {"reasoning_effort": "medium"}
        if legacy:
            p["requestOverrides"] = {"reasoning_effort": None}
        else:
            select(p, reasoning_effort=None)
    elif selection == "advanced":
        p["requestOverrides"] = {"reasoning_effort": "medium"}
    before = copy.deepcopy(p)
    class Capture(OpenAICompatibleProvider):
        def stream(self, request):
            self.request = request
            self.params = self._request_params(request)
            yield TextDelta("OK")
    provider = Capture(p, "key", client=object())
    options = {"reasoning_effort": hint, "extra_body": {"reasoning_effort": "high", "keep": True}}
    saved = copy.deepcopy(options)
    with api.provider_scope(provider):
        result = api.request_model([{"role": "user", "content": "fixture"}], effort=hint, options=options, max_retries=0)
    assert result.message.content == "OK"
    assert "reasoning_effort" not in provider.request.options
    assert "reasoning_effort" not in provider.request.options.get("extra_body", {})
    assert provider.params.get("reasoning_effort") == ("medium" if selection in {"medium", "advanced", "inherit"} else None)
    assert provider.params["extra_body"]["keep"] is True
    assert p == before and options == saved


@pytest.mark.parametrize("wrapped", [False, True])
def test_workflow_effort_cannot_escape_via_nested_wire_alias(wrapped):
    from model_runtime.request_policy import without_workflow_effort
    p = make_profile({"reasoning_effort": {"type": "enum", "values": ["quiet", "max"],
        "wire_location": "extra_body", "wire_path": ["reasoning", "effort"]}})
    select(p, reasoning_effort="quiet")
    hints = {"extra_body": {"reasoning": {"effort": "max", "keep": True}}}
    clean = without_workflow_effort(hints, p, api_key=None if wrapped else "key")
    assert clean == {"extra_body": {"reasoning": {"keep": True}}}
    effective = resolve_request(p, clean, api_key="key")
    assert effective.options["extra_body"]["reasoning"] == {"effort": "quiet", "keep": True}
    assert hints["extra_body"]["reasoning"]["effort"] == "max"


def test_stale_project_effort_is_inert_without_rewriting_history(tmp_path):
    from storage.session import SessionStore
    from application.generation import GenerationRequest
    from application.projects import ProjectService
    store = SessionStore(tmp_path)
    project = store.create_project(effort="high")
    assert project["effort"] is None
    old = {**project, "effort": "high"}
    store.save_project(old)
    before = store.project_path(old["id"]).read_bytes()
    assert store.get_project(old["id"])["effort"] == "high"
    assert ProjectService(tmp_path).project(old["id"])["effort"] is None
    assert store.project_path(old["id"]).read_bytes() == before
    assert GenerationRequest("fixture", effort="high").effort is None
    assert store.update_project_settings(old["id"], effort="low")["effort"] == "high"
