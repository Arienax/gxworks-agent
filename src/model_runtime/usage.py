# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public model usage receipts; never retain prompts, tools, keys or wire bodies."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math
import re

from model_runtime.contract import MISSING, member
from model_runtime.request_policy import parameter_value


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
    result["effect_confirmed"] = False
    return result


def request_summary(runtime, effective, *, profile_name="", api_key="", options=None):
    """Read resolved values and request flags, never advertised capabilities."""
    options = effective.options if options is None else options
    rows = []
    for name, desc in runtime.contract.parameters.items():
        value = parameter_value(options, name, desc)
        sent = value is not MISSING and value is not None
        # Arbitrary text parameters may contain private prose or credentials.
        redacted = sent and isinstance(value, str) and (
            value == api_key or not member(value, desc.values or desc.ui_hint.get("suggestions", ())))
        rows.append({"name": name, "value": value if sent and not redacted else None,
                     "source": effective.sources.get(name, "server_default"), "sent": sent, "redacted": redacted})
    return public_usage({"profile_id": runtime.profile_id, "profile_name": profile_name, "model": runtime.model,
        "parameters": rows, "features": {
            "tools": bool(options.get("tools")), "streaming": bool(options.get("stream")),
            "structured_output": (options.get("response_format") or {}).get("type") in {"json_object", "json_schema"},
            "images": any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))
                          for m in options.get("messages", []) if isinstance(m, dict)),
        }})


def saved_preferences(contract, settings):
    parameters = settings.get("parameters") or {}
    thinking = parameters.get("reasoning_effort") or {"mode": contract.get("parameters", {}).get("reasoning_effort", {}).get("default_mode", "inherit")}
    return {"thinking": str(thinking["value"]) if thinking.get("mode") == "value" else
            "跟随应用设置" if thinking.get("mode") == "inherit" else "服务默认",
            "custom_count": sum(s.get("mode") == "value" or s.get("mode") !=
                                (contract.get("parameters", {}).get(n, {}).get("default_mode") or "inherit")
                                for n, s in parameters.items())}
