"""Bounded discovery based on endpoint metadata and isolated validation probes.

No provider/model-name table. Unknown, ignored and unsupported are deliberately
not conflated. Probes use synthetic prompts only; never PLC data or user history.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Mapping

from model_capabilities import (
    EFFORT_CANDIDATES, capability_scope, effective_parameter, metadata_parameters,
)
from model_provider import (
    ModelProviderError, ModelRequest, SystemMessage, TextDelta, ToolCallEnd, UserMessage,
)


def _consume(provider, request):
    return list(provider.stream(request))


def _tool_probe(provider, model: str, timeout=15.0) -> bool:
    tool = {"type": "function", "function": {
        "name": "capability_probe", "description": "Return the fixed probe value.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["ok"]}},
                       "required": ["value"], "additionalProperties": False}}}
    request = ModelRequest(
        messages=(SystemMessage("This is a capability probe. Follow the user instruction exactly."),
                  UserMessage("Call capability_probe exactly once with value='ok'. Do not answer with prose.")),
        model=model, tools=(tool,), stream=False, timeout=timeout, max_retries=0,
        options={"response_format": None, getattr(provider, "_probe_token_key", "max_completion_tokens"): 256},
    )
    try:
        events = _consume(provider, request)
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        return False
    except Exception:
        return False
    return any(isinstance(event, ToolCallEnd) and event.tool_call.name == "capability_probe" for event in events)


def _structured_output_probe(provider, model: str, timeout=15.0) -> bool:
    request = ModelRequest(
        messages=(SystemMessage("Return only valid JSON."),
                  UserMessage('Return exactly one JSON object with key "probe" and value true.')),
        model=model, stream=False, timeout=timeout, max_retries=0,
        options={"response_format": {"type": "json_object"},
                 getattr(provider, "_probe_token_key", "max_completion_tokens"): 256},
    )
    try:
        text = "".join(event.text for event in _consume(provider, request) if isinstance(event, TextDelta)).strip()
        payload = json.loads(text)
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        return False
    except Exception:
        return False
    return isinstance(payload, Mapping) and payload.get("probe") is True


def _parameter_probe(provider, model, name, candidates, invalid, remaining, context=None):
    result = {"status": "unknown", "source": "probe"}
    if not callable(getattr(provider, "probe_parameter", None)):
        return result

    def attempt(value):
        timeout = remaining()
        if timeout <= 0:
            return "unknown"
        try:
            return provider.probe_parameter(model, name, value, timeout=timeout, context=context)
        except ModelProviderError as error:
            if error.code in {"authentication", "rate_limit"}:
                raise
            return "unknown"

    # Negative control: permissive gateways can silently ignore parameters.
    negative = attempt(invalid)
    if negative == "unsupported":
        return {**result, "status": "unsupported"}
    if negative == "accepted":
        return {**result, "status": "accepted"}
    if negative != "rejected":
        return result
    accepted = []
    rejected = 0
    for value in candidates:
        outcome = attempt(value)
        if outcome == "accepted":
            accepted.append(value)
        elif outcome in {"rejected", "unsupported"}:
            rejected += 1
        else:
            # Stop on network/time/budget failure; retain validated values, but
            # do not claim that missing values or an entire parameter are absent.
            break
    if accepted:
        result.update(status="supported", values=accepted)
        if name == "temperature" and len(accepted) == 1 and rejected == len(candidates) - 1:
            result["status"] = "fixed"
    return result


def inspect_openai_compatible(provider, model: str, configured_capabilities=None) -> Dict[str, Any]:
    original = getattr(provider, "profile", {}) or {}
    selected = str(model or "").strip()
    deadline = time.monotonic() + 90.0

    def remaining():
        return max(0.0, min(15.0, deadline - time.monotonic()))

    listing_available = True
    metadata = []
    try:
        if callable(getattr(provider, "list_model_metadata", None)):
            metadata = list(provider.list_model_metadata(timeout=remaining()))
            models = sorted({str(item["id"]) for item in metadata if item.get("id")})
        else:
            models = sorted(set(provider.list_models(timeout=15.0)))
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        listing_available, models = False, []
    except Exception:
        listing_available, models = False, []
    # A gateway alias may not occur in /models. Never replace an explicit model
    # with the first advertised entry and accidentally attach its capabilities.
    probe_model = selected if selected and selected != "__discover__" else (models[0] if models else "")
    if not probe_model:
        raise ModelProviderError("模型列表不可用，请手动填写模型 ID。", code="invalid_request")
    if callable(getattr(provider, "for_detection", None)):
        provider = provider.for_detection()
    if callable(getattr(provider, "probe_parameter", None)):
        # GET /models alone is not evidence of a usable Chat Completions API.
        provider.probe_parameter(probe_model, timeout=remaining())
    entry = next((item for item in metadata if item.get("id") == probe_model), {})
    parameters = metadata_parameters(entry)
    if "reasoning_effort" not in parameters:
        parameters["reasoning_effort"] = _parameter_probe(
            provider, probe_model, "reasoning_effort", EFFORT_CANDIDATES,
            "__gxw_invalid_effort__", remaining,
        )
    configured_effort = effective_parameter(original, "reasoning_effort")
    effort_values = parameters["reasoning_effort"].get("values", [])
    effort = configured_effort if configured_effort in effort_values else None
    if "temperature" not in parameters:
        parameters["temperature"] = _parameter_probe(
            provider, probe_model, "temperature", (0.0, 0.5, 1.0, 1.5, 2.0), -1.0,
            remaining, {"reasoning_effort": effort} if effort is not None else {},
        )
    # Temperature support can change when reasoning is enabled or disabled.
    parameters["temperature"]["reasoning_effort"] = effort
    capabilities = dict(configured_capabilities or {})
    detected_values = {
        "tools": _tool_probe(provider, probe_model, remaining()) if remaining() > 0 else False,
        "structured_output": _structured_output_probe(provider, probe_model, remaining()) if remaining() > 0 else False,
    }
    for name, supported in detected_values.items():
        if supported or name not in capabilities:
            capabilities[name] = supported
    return {
        "models": models,
        "recommended_model": probe_model,
        "selected_model_available": bool(selected and selected in models),
        "model_listing_available": listing_available,
        "capabilities": capabilities,
        "detected": sorted(detected_values),
        "probe_results": detected_values,
        "parameter_support": {"scope": capability_scope(original, probe_model), "parameters": parameters},
        "note": "参数滑条只显示已声明或已通过有效值与无效值对照检测的档位；接口接受不等于实际生效。工具和 JSON 为本次观察结果，失败可能是超时或输出预算不足；视觉能力需手动确认。检测结果保存后生效。",
    }
