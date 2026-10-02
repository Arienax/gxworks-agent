import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from application import construction_examples as examples
from application.compact_protocol import expand_compact_ladder, validate_compact_structure
from plc.ir import IR_SCHEMA_VERSION, ir_to_ladder, validate_plc_ir

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from model_runtime.provider import TextDelta
from model_profile_fixtures import offline_runtime_profile


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


@pytest.mark.parametrize(("required_opcode", "expected_status"), [
    ("OUT", "verified"), ("MOV", "violated"),
])
def test_fresh_semantic_receipt_survives_one_call_delivery(tmp_path, monkeypatch, required_opcode, expected_status):
    import application.generation_agent as agent_b

    # Keep the legacy snapshot above as a separate compatibility case. Fresh
    # semantics must not pass only because an old contract was deferred.
    specification = _spec()
    selected = specification["selected_approach"]
    selected.pop("generation_contract")
    selected["implementation_semantics"] = []
    selected["explicit_user_constraints"] = {"required_opcodes": [required_opcode]}
    before = copy.deepcopy(specification)
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")
    provider = OneShotProvider()
    metadata = GenerationWorkflow(
        GenerationRequest(user_input="Generate the confirmed program", confirmed_context=specification,
                          plc_model="FX3U", model_name=provider.profile["model"]),
        tmp_path,
        dependencies=GenerationDependencies(provider=provider),
    ).run()

    assert specification == before
    assert len(provider.requests) == 1
    assert provider.requests[0].max_retries == 0
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation"]["status"] == "candidate_ready"
    assert metadata["candidate_origin"] == "compact_agent"
    receipt = metadata["semantic_validation"]
    assert receipt["legacy_compatibility"] is False
    assert receipt["status"] == expected_status
    assert any(row.get("kind") == "opcode" and row.get("expected") == required_opcode
               and row["status"] == expected_status for row in receipt["checks"])
    assert bool(receipt["violations"]) == (expected_status == "violated")
    if expected_status == "violated":
        assert any(row.get("expected") == required_opcode for row in receipt["violations"])
    ladder = json.loads((tmp_path / metadata["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert ladder["rungs"]
    assert (tmp_path / metadata["artifacts"]["ir"]).is_file()


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


@pytest.fixture(autouse=True)
def isolated_flag(monkeypatch):
    monkeypatch.delenv(examples.ENV_NAME, raising=False)


def test_default_is_off_and_does_not_build_examples(monkeypatch):
    def forbidden():
        raise AssertionError("disabled generation must not build example IR")

    monkeypatch.setattr(examples, "_ir_examples", forbidden)
    block = examples.prepare_construction_examples("FX3U")
    assert block.text == ""
    assert block.manifest()["requested"] is False
    assert block.manifest()["enabled"] is False
    assert block.manifest()["example_ids"] == []


@pytest.mark.parametrize("value", ["1", "true", "ON", " yes "])
def test_environment_opt_in_and_explicit_false_override(monkeypatch, value):
    monkeypatch.setenv(examples.ENV_NAME, value)
    assert examples.resolve_construction_examples() is True
    assert examples.resolve_construction_examples(False) is False


@pytest.mark.parametrize("value", ["", "0", "false", "OFF", " no "])
def test_environment_off_and_explicit_true_override(monkeypatch, value):
    monkeypatch.setenv(examples.ENV_NAME, value)
    assert examples.resolve_construction_examples() is False
    assert examples.resolve_construction_examples(True) is True


@pytest.mark.parametrize("value", ["false", "true", 0, 1, {}, []])
def test_explicit_option_does_not_coerce_strings_or_integers(value):
    with pytest.raises(TypeError):
        examples.resolve_construction_examples(value)


def test_invalid_environment_is_reported_not_silently_enabled(monkeypatch):
    monkeypatch.setenv(examples.ENV_NAME, "maybe")
    with pytest.raises(ValueError, match=examples.ENV_NAME):
        examples.resolve_construction_examples()
    assert examples.resolve_construction_examples(False) is False


def test_other_model_is_not_given_fx3u_examples():
    block = examples.prepare_construction_examples("FX5U", True)
    assert block.text == ""
    assert block.manifest()["requested"] is True
    assert block.manifest()["enabled"] is False
    assert block.manifest()["reason"] == "unsupported_model"


def test_all_examples_are_current_valid_ir_and_round_trip_exactly():
    items = examples.construction_examples_ir()
    assert len(items) == 6
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        program = item["program_ir"]
        assert program["kind"] == "plc_program_ir"
        assert program["schema_version"] == IR_SCHEMA_VERSION
        assert program["plc"]["cpu"] == "FX3U"
        validate_plc_ir(program)
        ladder = ir_to_ladder(program)
        compact = examples._compact_from_ir(program)
        validate_compact_structure(compact)
        projected = {"io_table": [
            {"address": address, "label": label}
            for address, label in ladder["device_comments"].items()
        ]}
        assert expand_compact_ladder(compact, projected) == ladder
        assert not any(address.startswith(("SM", "SD", "M8", "D8")) for address in program["devices"])


def test_ir_exports_and_manifests_cannot_mutate_cached_examples():
    routed_spec = {
        "selected_approach": {
            "generation_contract": {"required_structures": ["self_hold"]}
        }
    }
    before = examples.prepare_construction_examples("FX3U", True, routed_spec)
    exported = examples.construction_examples_ir()
    exported[0]["program_ir"]["networks"].clear()
    assert examples.construction_examples_ir()[0]["program_ir"]["networks"]
    manifest = before.manifest()
    manifest["example_ids"].clear()
    assert before.manifest()["example_ids"] == ["reset_dominant_hold"]
    assert before == examples.prepare_construction_examples("fx3u", True, routed_spec)
    assert before.manifest()["sha256"] == hashlib.sha256(before.text.encode()).hexdigest()


def _construction_spec():
    return {
        "summary": "X5 controls Y5",
        "io_table": [{"address": "X5", "kind": "X", "label": "input"},
                     {"address": "Y5", "kind": "Y", "label": "output"}],
        "parameters": [],
        "selected_approach": {
            "approach_id": "direct", "name": "direct",
            "generation_contract": {
                "required_structures": ["self_hold"], "forbidden_structures": [],
                "required_opcodes": ["OUT"], "forbidden_opcodes": [],
                "required_devices": ["X5", "Y5"], "forbidden_devices": [],
            },
        },
    }


def test_wire_renderer_changes_only_the_example_block_and_freezes_flag(monkeypatch):
    from application.generation_agent import _compact_wire_renderer, _COMPACT_PROTOCOL
    from application.compact_protocol import compact_capability_prompt
    from application.confirmed_generation_context import generation_execution_prompt
    from application.generation_wire import render_context_checkpoint, render_wire_messages
    from plc.specification.provenance import SOURCE_PRECEDENCE

    spec, evidence, request = _construction_spec(), "\nEVIDENCE_CONTROL_SENTINEL\n", "Generate confirmed program"
    args = (spec, evidence, request, None, "", [])
    off_block = examples.prepare_construction_examples("FX3U", False, spec)
    on_block = examples.prepare_construction_examples("FX3U", True, spec)
    off = _compact_wire_renderer("FX3U", example_block=off_block)
    expected = (_COMPACT_PROTOCOL + SOURCE_PRECEDENCE + "\n# Selected PLC\nFX3U\n"
                + "\n# Confirmed project specification\n"
                + json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
                + compact_capability_prompt("FX3U", spec) + render_context_checkpoint("")
                + evidence + generation_execution_prompt(spec, evidence_text=evidence, task_type="generate"))
    assert off(*args) == {"messages": render_wire_messages(expected, [{"role": "user", "content": request}])}
    on = _compact_wire_renderer("FX3U", example_block=on_block)
    off_packet, on_packet = off(*args), on(*args)
    block = on_block
    assert block.example_ids == ("reset_dominant_hold",)
    assert on_packet["messages"][0]["content"].count(block.text) == 1
    on_packet["messages"][0]["content"] = on_packet["messages"][0]["content"].replace(block.text, "", 1)
    assert on_packet == off_packet


def test_actual_agent_request_budget_handoff_and_retrieval_are_isolated(monkeypatch):
    from application import generation_agent as agent
    from application import model_api as api
    from application.generation_wire import wire_sha256, wire_token_estimate
    from model_profile_fixtures import offline_runtime_profile

    specification = _construction_spec()
    before = copy.deepcopy(specification)
    calls, lookups, callbacks, results = [], [], [], []
    provider = SimpleNamespace(profile=offline_runtime_profile("offline-examples"))

    def knowledge(query, **kwargs):
        lookups.append((str(query), copy.deepcopy(kwargs.get("confirmed_context"))))
        return "\nEVIDENCE_CONTROL_SENTINEL\n"

    def request_model(messages, **kwargs):
        calls.append((copy.deepcopy(messages), kwargs.copy()))
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({"r": [
            {"h": None, "s": [], "b": [{"i": ["NO X5"], "o": ["COIL Y5"]}]}
        ]})))

    monkeypatch.setattr(agent, "_build_knowledge_context", knowledge)
    monkeypatch.setattr(api, "request_model", request_model)
    for enabled in (False, True):
        with api.provider_scope(provider, model_name="offline-examples"):
            result = agent.generate_confirmed_ladder(
                specification, "FX3U", model_name="offline-examples",
                construction_examples=enabled, on_context=callbacks.append,
            )
        results.append(result)
        packet = {"messages": calls[-1][0]}
        handoff = result["generation_handoff"]
        assert handoff["wire_sha256"] == wire_sha256(packet)
        assert handoff["budget_report"]["compiled_budget_payload_tokens"] == wire_token_estimate(packet)
        assert handoff["construction_examples"]["enabled"] is enabled
        assert callbacks[-1] == handoff
        assert result["model_calls"] == 1
        assert calls[-1][1]["effort"] is None
        assert calls[-1][1]["max_retries"] == 0
        assert "Optional construction examples" not in json.dumps(handoff["construction_examples"])
    assert len(calls) == 2
    assert specification == before
    assert results[0]["ladder"] == results[1]["ladder"]
    assert lookups[0] == lookups[1]
    assert len(lookups) == 2
    assert "C0" not in lookups[0][0] and "compound_condition" not in lookups[0][0]
    off, on = copy.deepcopy(calls[0][0]), copy.deepcopy(calls[1][0])
    block = examples.prepare_construction_examples("FX3U", True, specification)
    assert block.example_ids == ("reset_dominant_hold",)
    on[0]["content"] = on[0]["content"].replace(block.text, "", 1)
    assert on == off
    assert calls[0][1] == calls[1][1]
    assert results[1]["generation_handoff"]["budget_report"]["compiled_budget_payload_tokens"] > results[0]["generation_handoff"]["budget_report"]["compiled_budget_payload_tokens"]



@pytest.mark.parametrize("enabled", [False, True])
def test_generation_workflow_forwards_explicit_construction_example_arm(monkeypatch, tmp_path, enabled):
    import application.generation_agent as agent_b
    import application.model_api as api

    captured = []

    def fake_generate(spec, plc_model="FX3U", **kwargs):
        captured.append(kwargs.get("construction_examples"))
        return {
            "ladder": _ladder(),
            "model_calls": 1,
            "generation_handoff": {
                "construction_examples": {"enabled": bool(kwargs.get("construction_examples"))}
            },
        }

    monkeypatch.setattr(agent_b, "generate_confirmed_ladder", fake_generate)
    provider = OneShotProvider()
    with api.provider_scope(provider, model_name="offline-one-shot"):
        metadata = GenerationWorkflow(
            GenerationRequest(
                "Generate confirmed program",
                confirmed_context=_spec(),
                plc_model="FX3U",
                model_name="offline-one-shot",
                construction_examples=enabled,
            ),
            tmp_path,
            dependencies=GenerationDependencies(provider=provider),
        ).run()

    assert captured == [enabled]
    assert metadata["generation_handoff"]["construction_examples"]["enabled"] is enabled


@pytest.mark.parametrize("value", ["1", "false", 0, 1, {}, []])
def test_generation_request_rejects_non_boolean_construction_example_arm(value):
    with pytest.raises(TypeError, match="construction_examples"):
        GenerationRequest("Generate", construction_examples=value)



@pytest.mark.parametrize("value", [None, "1", "false", 0, 1, {}, []])
def test_generation_request_rejects_non_boolean_fresh_confirmed_mode(value):
    with pytest.raises(TypeError, match="fresh_confirmed_generation"):
        GenerationRequest("Generate", fresh_confirmed_generation=value)
