import json

import pytest

from application.generation import (
    GenerationDependencies,
    GenerationRequest,
    GenerationValidationError,
    GenerationWorkflow,
)
from model_provider import (
    AssistantMessage,
    ModelRequest,
    RawModelResponse,
    ResponseRejectedError,
    UserMessage,
)
from response_language import LanguageViolation
from workflow_response_contracts import LADDER_RESPONSE


def _ladder():
    return {
        "device_comments": {"X0": "Input", "Y0": "Output"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [
                            {"type": "NO", "address": "X0", "label": "Input"}
                        ],
                        "outputs": [
                            {"type": "COIL", "address": "Y0", "label": "Output"}
                        ],
                    }
                ],
            }
        ],
    }


def _json_rejection(content):
    request = ModelRequest.from_messages(
        [UserMessage("generate ladder")],
        response_contract=LADDER_RESPONSE,
    )
    raw = RawModelResponse(AssistantMessage(content=content))
    return ResponseRejectedError(
        request,
        (raw,),
        (LanguageViolation("content", "invalid_json_object"),),
    )


def test_generation_recovers_rejected_redundant_json_tail_without_model_retry(tmp_path):
    raw = json.dumps(_ladder(), ensure_ascii=False) + "}"

    def stream(*_args, **_kwargs):
        raise _json_rejection(raw)

    result = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *_a, **_k: pytest.fail(
                "redundant closing delimiter must not trigger another model call"
            ),
            preserve_rejected_candidate=True,
        ),
    ).run()

    assert result["validation"]["status"] == "candidate_ready"
    assert any("多余" in message for message in result["validation"]["messages"])
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == _ladder()
    assert not (tmp_path / "repair_candidate.json").exists()


def test_generation_preserves_unrepairable_rejected_json_for_explicit_repair(tmp_path):
    raw = '{"device_comments":{},"rungs":['

    def stream(*_args, **_kwargs):
        raise _json_rejection(raw)

    workflow = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *_a, **_k: pytest.fail(
                "invalid completed response must not silently regenerate"
            ),
            preserve_rejected_candidate=True,
        ),
    )

    with pytest.raises(GenerationValidationError) as captured:
        workflow.run()

    assert captured.value.diagnostics["violations"][0]["reason"] == "invalid_json_object"
    assert (tmp_path / "repair_candidate.json").read_text(encoding="utf-8") == raw
