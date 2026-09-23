import copy
import json
from types import SimpleNamespace

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from model_runtime.provider import TextDelta
from test_web_api import offline_runtime_profile


def _spec():
    return {
        "summary": "X0 controls Y0",
        "io_table": [
            {"address": "X0", "kind": "X", "label": "start"},
            {"address": "Y0", "kind": "Y", "label": "motor"},
        ],
        "parameters": [],
        "selected_approach": {
            "approach_id": "direct",
            "name": "direct",
            "generation_contract": {
                "required_structures": ["direct_logic"],
                "forbidden_structures": [],
                "required_opcodes": ["OUT"],
                "forbidden_opcodes": [],
                "required_devices": ["X0", "Y0"],
                "forbidden_devices": [],
            },
        },
        "analysis_internal": "MUST_NOT_REACH_GENERATOR",
    }


def _ladder():
    return {
        "device_comments": {"X0": "start", "Y0": "motor"},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0", "label": None}],
                "outputs": [{"type": "COIL", "address": "Y0", "label": None}],
            }],
        }],
    }


class OneShotProvider:
    def __init__(self):
        self.requests = []
        # Canonical v3 runtime profile: the model path materializes provider.profile.
        self.profile = offline_runtime_profile("offline-one-shot")

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            raise AssertionError("confirmed generation must not request a second full ladder")
        yield TextDelta(json.dumps({"r": [{"b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]}, ensure_ascii=False))


def test_confirmed_generation_uses_one_isolated_agent_call(tmp_path):
    provider = OneShotProvider()
    request = GenerationRequest(
        user_input="RAW USER REQUIREMENT MUST NOT REACH GENERATOR",
        target_mode="ladder",
        conversation_history=[
            {"role": "user", "content": "OLD ANALYSIS HISTORY MUST NOT REACH GENERATOR"},
            {"role": "assistant", "content": "old analysis answer"},
        ],
        confirmed_context=_spec(),
        plc_model="FX3U",
        model_name="offline-one-shot",
    )
    metadata = GenerationWorkflow(
        request,
        tmp_path,
        dependencies=GenerationDependencies(provider=provider),
    ).run()

    assert len(provider.requests) == 1
    sent = "\n".join(str(getattr(message, "content", "")) for message in provider.requests[0].messages)
    assert "RAW USER REQUIREMENT MUST NOT REACH GENERATOR" not in sent
    assert "OLD ANALYSIS HISTORY MUST NOT REACH GENERATOR" not in sent
    assert "MUST_NOT_REACH_GENERATOR" not in sent
    assert "X0" in sent and "Y0" in sent
    assert provider.requests[0].stream is True
    assert provider.requests[0].max_retries == 0
    assert provider.requests[0].response_contract.name == "compact_ladder"
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation"]["status"] == "candidate_ready"


def test_compact_agent_sends_the_compiler_budgeted_application_wire(monkeypatch):
    import application.generation_agent as agent_b
    import application.model_api as api
    from application.generation_wire import wire_sha256, wire_token_estimate

    provider = OneShotProvider()
    captured = {}
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")

    def request_model(messages, **kwargs):
        captured["messages"] = messages
        return SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(
                {"r": [{"h": None, "s": [], "b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]},
                ensure_ascii=False,
            )
        ))

    monkeypatch.setattr(api, "request_model", request_model)
    with api.provider_scope(provider, model_name="offline-one-shot"):
        result = agent_b.generate_confirmed_ladder(
            _spec(), "FX3U", model_name="offline-one-shot"
        )

    actual = {"messages": captured["messages"]}
    handoff = result["generation_handoff"]
    assert handoff["wire_sha256"] == wire_sha256(actual)
    assert handoff["budget_report"]["budget_basis"] == "application_wire_messages"
    assert handoff["budget_report"]["compiled_budget_payload_tokens"] == wire_token_estimate(actual)


def test_over_budget_confirmed_generation_compacts_before_agent_b(monkeypatch):
    import application.generation_agent as agent_b
    import application.model_api as api

    provider = OneShotProvider()
    provider.profile["context_window"] = 24_000
    provider.profile["generationDefaults"] = {"max_completion_tokens": 2_048}
    specification = _spec()
    specification["intent_context"] = {
        "schema_version": 1,
        "requests": [
            {
                "id": f"r{i}",
                "source": "user_request",
                "text": f"OLD_CONTEXT_{i} X{i} K{i} " + ("历史上下文" * 350),
            }
            for i in range(10)
        ],
    }
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")
    calls = []

    def request_model(messages, **kwargs):
        name = kwargs["response_contract"].name
        calls.append((name, copy.deepcopy(messages)))
        if name == "context_checkpoint":
            return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "checkpoint": "Older intent checkpoint; preserve prior X/K references only for continuity."
            })))
        assert name == "compact_ladder"
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "r": [{"h": None, "s": [], "b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]
        })))

    monkeypatch.setattr(api, "request_model", request_model)
    with api.provider_scope(provider, model_name="offline-one-shot"):
        result = agent_b.generate_confirmed_ladder(
            specification, "FX3U", model_name="offline-one-shot"
        )

    assert [name for name, _ in calls] == ["context_checkpoint", "compact_ladder"]
    sent = json.dumps(calls[-1][1], ensure_ascii=False)
    assert "# Compacted historical context" in sent
    assert "Older intent checkpoint" in sent
    assert "OLD_CONTEXT_0" not in sent
    assert "OLD_CONTEXT_9" in sent
    assert result["model_calls"] == 2
    compaction = result["generation_handoff"]["budget_report"]["context_compaction"]
    assert compaction["status"] == "installed"
    assert compaction["model_calls"] == 1


def test_injected_direct_generator_remains_one_call(tmp_path):
    calls = []

    def direct(*args, **kwargs):
        calls.append((args, kwargs))
        return "", json.dumps(_ladder(), ensure_ascii=False)

    request = GenerationRequest(
        user_input="X0 controls Y0",
        target_mode="ladder",
        confirmed_context=_spec(),
        plc_model="FX3U",
        model_name="offline-direct",
    )
    metadata = GenerationWorkflow(
        request,
        tmp_path,
        dependencies=GenerationDependencies(stream_response=direct),
    ).run()
    assert len(calls) == 1
    assert metadata["first_pass_pipeline"] == {"mode": "direct"}
    assert metadata["validation"]["status"] == "candidate_ready"


def test_builtin_deepseek_chat_profile_uses_json_object_transport():
    from types import SimpleNamespace
    from application.generation_agent import _response_options
    from storage.config import DEFAULT_MODEL_PROFILES

    profile = next(item for item in DEFAULT_MODEL_PROFILES if item["id"] == "deepseek-default")
    assert _response_options(SimpleNamespace(profile=profile)) == {
        "response_format": {"type": "json_object"}
    }


def _profile_declaring_structured_output(modes):
    """Canonical v3 profile whose contract offers exactly these structured-output modes.

    The scope is computed with the same helper a saved profile uses, because a
    contract whose scope does not match its profile is ignored in favour of the
    generic template.
    """
    from model_runtime.contract import contract_scope

    profile = offline_runtime_profile()
    capabilities = {
        "structured_output": {
            "status": "supported", "source": "manual", "modes": list(modes),
        },
    }
    return {
        **profile,
        "capabilityContract": {
            "schema_version": 3,
            "scope": contract_scope(profile, {}, model=profile["model"], api_key=None),
            "capabilities": capabilities,
            "parameters": {},
        },
    }


def test_json_schema_transport_requires_explicit_profile_capability():
    from types import SimpleNamespace
    from application.generation_agent import _response_options

    # json_schema is offered only when the v3 contract declares it as a mode.
    provider = SimpleNamespace(profile=_profile_declaring_structured_output(["json_schema"]))
    response_format = _response_options(provider)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_json_schema_is_not_offered_without_a_declared_mode():
    from types import SimpleNamespace
    from application.generation_agent import _response_options

    provider = SimpleNamespace(profile=_profile_declaring_structured_output(["json_object"]))
    assert _response_options(provider)["response_format"] == {"type": "json_object"}
