import json

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from model_provider import TextDelta


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
        self.profile = {
            "id": "offline-one-shot",
            "adapter": "openai_compatible",
            "model": "offline-one-shot",
            "capabilities": {"structured_output": True},
        }

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            raise AssertionError("confirmed generation must not request a second full ladder")
        yield TextDelta(json.dumps(_ladder(), ensure_ascii=False))


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
    assert provider.requests[0].response_contract.name == "ladder"
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation"]["status"] == "candidate_ready"


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
