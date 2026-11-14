# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Map resolved application options to the Responses API request body."""
import copy

from model_runtime.contract import MISSING, path_get
from model_runtime.request_policy import parameter_value


OUTPUT_LIMITS = ("max_output_tokens", "max_completion_tokens", "max_tokens")


def _thinking_modes(body, reasoning):
    modes = [body.pop("enable_thinking", None), reasoning.pop("enabled", None)]
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        modes.append(thinking.pop("type", None))
        thinking.pop("clear_thinking", None)
        if not thinking:
            body.pop("thinking")
    for key in ("chat_template_args", "chat_template_kwargs"):
        template = body.get(key)
        if isinstance(template, dict) and "enable_thinking" in template:
            modes.append(template.pop("enable_thinking"))
            if not template:
                body.pop(key)
    return modes


def encode_options(options, *, reasoning_levels=None):
    """Keep logical profile names readable while sending native Responses fields."""
    body = copy.deepcopy(dict(options))
    body.update(body.pop("extra_body", {}) or {})
    for key in ("messages", "functions", "function_call", "stream_options", "tool_stream", "n"):
        body.pop(key, None)
    limits = [body.pop(name, None) for name in OUTPUT_LIMITS]
    limit = next((value for value in limits if value is not None), None)
    if limit is not None:
        body["max_output_tokens"] = max(16, limit) if type(limit) is int else limit

    fmt = body.pop("response_format", None)
    if body.get("text") is not None and not isinstance(body["text"], dict):
        raise ValueError("Responses text options must be a JSON object")
    if isinstance(fmt, dict):
        schema = fmt.pop("json_schema", None)
        if isinstance(schema, dict):
            fmt = {**schema, **fmt}
        body["text"] = {**(body.get("text") or {}), "format": fmt}

    tools = body.get("tools")
    if tools:
        body["tools"] = [
            {"type": "function", "strict": False, **tool["function"]}
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict) else tool
            for tool in tools
        ]
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function" and isinstance(choice.get("function"), dict):
        body["tool_choice"] = {"type": "function", "name": choice["function"]["name"]}

    reasoning = body.pop("reasoning", None)
    if reasoning is None:
        reasoning = {}
    elif not isinstance(reasoning, dict):
        raise ValueError("Responses reasoning options must be a JSON object")
    effort = reasoning.get("effort", body.pop("reasoning_effort", None))
    modes = _thinking_modes(body, reasoning)
    if any(mode is False or mode == "disabled" for mode in modes):
        effort = "none"
    elif effort is None and any(mode is True or mode in ("enabled", "adaptive") for mode in modes):
        effort = "medium"
    if effort == "default" and (reasoning_levels or {}).get("high") == effort:
        effort = "high"
    if effort is not None:
        reasoning["effort"] = effort
    if reasoning:
        body["reasoning"] = reasoning
    body.setdefault("store", False)
    return body


def wire_response_format(body):
    fmt = (body.get("text") or {}).get("format") if "input" in body else body.get("response_format")
    return (fmt or {}).get("type")


def wire_parameter(body, name, descriptor):
    if "input" not in body:
        value = parameter_value(body, name, descriptor)
        if value is MISSING and name in OUTPUT_LIMITS:
            return next((body[key] for key in OUTPUT_LIMITS if key in body), MISSING)
        return value
    if name == "reasoning_effort":
        items = body.get("input") or ()
        if items and items[-1].get("type") == "configuration_update":
            return path_get(items[-1], ("reasoning", "effort"))
        return path_get(body, ("reasoning", "effort"))
    if name in OUTPUT_LIMITS:
        return body.get("max_output_tokens", MISSING)
    return parameter_value(body, name, descriptor)
