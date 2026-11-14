# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Public model usage receipts; never retain prompts, tools, keys or wire bodies."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import math
import re

from model_runtime.contract import MISSING, member
from model_runtime.responses_wire import wire_parameter, wire_response_format


_sink = ContextVar("model_usage_sink", default=None)
_SOURCES = {"profile_default", "workflow_hint", "user_advanced", "user_selection",
            "user_omit", "contract_omit", "server_default"}


@contextmanager
def usage_scope(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit_usage(summary):
    sink = _sink.get()
    if sink is not None:
        sink("model_request", summary)


def emit_token_usage(usage):
    sink = _sink.get()
    if sink is None or usage is None:
        return
    raw = usage.raw_usage
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
        if isinstance(details, Mapping):
            cached = details.get("cached_tokens")
    sink("model_token_usage", {"input_tokens": usage.input_tokens,
                              "output_tokens": usage.output_tokens,
                              "cached_input_tokens": cached})


def public_token_counts(value):
    """Only publish metered counts. An absent cache count stays unknown."""
    if not isinstance(value, dict):
        return {}
    counts = {key: value.get(key) for key in ("input_tokens", "output_tokens")}
    if any(type(count) is not int or not 0 <= count <= 2**53 - 1 for count in counts.values()):
        return {}
    cached = value.get("cached_input_tokens")
    if type(cached) is int and 0 <= cached <= counts["input_tokens"]:
        counts["cached_input_tokens"] = cached
    return counts


def add_token_counts(total, counts):
    if not total:
        return counts
    result = {key: total[key] + counts[key] for key in ("input_tokens", "output_tokens")}
    if "cached_input_tokens" in total and "cached_input_tokens" in counts:
        result["cached_input_tokens"] = total["cached_input_tokens"] + counts["cached_input_tokens"]
    return result


def tool_executed(name, success):
    sink = _sink.get()
    if sink is not None:
        sink("tool_execution", {"name": name, "success": bool(success)})


def public_usage(value):
    """Narrow projection used again at durable/public job boundaries."""
    if not isinstance(value, dict):
        return {}
    result = {key: value[key][:256] for key in ("profile_id", "profile_name", "model")
              if isinstance(value.get(key), str)}
    result["parameters"] = []
    rows = value.get("parameters")
    for row in (rows if isinstance(rows, list) else [])[:128]:
        if not isinstance(row, dict) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(row.get("name", ""))):
            continue
        scalar = row.get("value")
        if not (scalar is None or type(scalar) is bool or type(scalar) in (int, float) and math.isfinite(scalar)
                or isinstance(scalar, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", scalar)):
            scalar = None
        result["parameters"].append({"name": row["name"], "value": scalar,
            "source": row.get("source") if row.get("source") in _SOURCES else "server_default",
            "sent": row.get("sent") is True, "redacted": row.get("redacted") is True})
    features = value.get("features") if isinstance(value.get("features"), dict) else {}
    result["features"] = {name: features.get(name) is True
                          for name in ("tools", "streaming", "structured_output", "images")}
    if tokens := public_token_counts(value.get("tokens")):
        result["tokens"] = tokens
    result["effect_confirmed"] = False
    return result


def request_summary(runtime, effective, *, profile_name="", api_key="", options=None):
    """Read resolved values and request flags, never advertised capabilities."""
    options = effective.options if options is None else options
    rows = []
    for name, desc in runtime.contract.parameters.items():
        value = wire_parameter(options, name, desc)
        sent = value is not MISSING and value is not None
        # Arbitrary text parameters may contain private prose or credentials.
        redacted = sent and isinstance(value, str) and (
            value == api_key or not member(value, desc.values or desc.ui_hint.get("suggestions", ())))
        rows.append({"name": name, "value": value if sent and not redacted else None,
                     "source": effective.sources.get(name, "server_default"), "sent": sent, "redacted": redacted})
    for name in runtime.settings.request_parameters:
        if name in runtime.contract.parameters or name not in options:
            continue
        value = options[name]
        redacted = isinstance(value, (str, list, dict))
        rows.append({"name": name, "value": None if redacted else value, "source": "user_advanced",
                     "sent": True, "redacted": redacted})
    return public_usage({"profile_id": runtime.profile_id, "profile_name": profile_name, "model": runtime.model,
        "parameters": rows, "features": {
            "tools": bool(options.get("tools")), "streaming": bool(options.get("stream")),
            "structured_output": wire_response_format(options) in {"json_object", "json_schema"},
            "images": any(isinstance(m.get("content"), list)
                          and any(p.get("type") in {"image_url", "input_image"} for p in m["content"] if isinstance(p, dict))
                          for m in options.get("input", options.get("messages", ())) if isinstance(m, dict)),
        }})


def saved_preferences(contract, settings):
    parameters = settings.get("parameters") or {}
    thinking = parameters.get("reasoning_effort") or {"mode": contract.get("parameters", {}).get("reasoning_effort", {}).get("default_mode", "inherit")}
    return {"thinking": str(thinking["value"]) if thinking.get("mode") == "value" else
            "跟随应用设置" if thinking.get("mode") == "inherit" else "服务默认",
            "custom_count": sum(s.get("mode") == "value" or s.get("mode") !=
                                (contract.get("parameters", {}).get(n, {}).get("default_mode") or "inherit")
                                for n, s in parameters.items())}
