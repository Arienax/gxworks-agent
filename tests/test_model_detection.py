from application.model_detection import inspect_openai_compatible
from model_provider import TextDelta, ToolCallEnd
from tool_messages import ToolCall


class _Provider:
    profile = {"baseUrl": "https://test.invalid/v1"}

    def __init__(self):
        self.stream_calls = 0

    def list_models(self, *, timeout=None):
        assert timeout == 15.0
        return ("model-b", "model-a", "model-b")

    def stream(self, request):
        self.stream_calls += 1
        assert request.timeout == 15.0
        assert request.max_retries == 0
        if request.tools:
            yield ToolCallEnd(ToolCall("probe-1", "capability_probe", '{"value":"ok"}'))
        else:
            yield TextDelta('{"probe":true}')


def test_discovery_lists_models_without_guessing_or_probing_first_result():
    provider = _Provider()
    result = inspect_openai_compatible(
        provider,
        "__discover__",
        {"reasoning": True, "tools": False, "structured_output": False},
    )
    assert result["models"] == ["model-a", "model-b"]
    assert result["recommended_model"] is None
    assert result["selected_model_available"] is False
    assert "contract" not in result
    assert provider.stream_calls == 0


def test_explicit_selected_model_is_probed_and_receives_contract():
    provider = _Provider()
    result = inspect_openai_compatible(
        provider,
        "model-b",
        {"reasoning": True, "tools": False, "structured_output": False},
    )
    assert result["models"] == ["model-a", "model-b"]
    assert result["recommended_model"] == "model-b"
    assert result["selected_model_available"] is True
    assert result["contract"]["scope"]["model"] == "model-b"
    assert result["contract"]["capabilities"]["reasoning"]["status"] == "supported"
    assert result["contract"]["capabilities"]["tools"]["status"] == "supported"
    assert result["contract"]["capabilities"]["structured_output"]["status"] == "supported"
    assert "probe_results" not in result and "parameter_support" not in result
    assert provider.stream_calls > 0
