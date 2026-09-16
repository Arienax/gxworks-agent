"""Data-driven parameter contracts for any OpenAI-compatible endpoint.

No model-name/provider-name dispatch. Discovery is evidence, not a model registry.
The scope includes the endpoint, model and non-sampling request extensions, so a
saved result is not silently reused after changing a gateway or thinking mode.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping

PARAMETERS = ("reasoning_effort", "temperature")
EFFORT_CANDIDATES = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
STATUSES = {"supported", "accepted", "unknown", "unsupported", "fixed"}
SOURCES = {"probe", "metadata", "manual"}


def capability_scope(profile, model=None):
    context = {}
    for group in ("generationDefaults", "requestOverrides"):
        context[group] = {k: v for k, v in (profile.get(group) or {}).items()
                          if k not in (*PARAMETERS, "top_p", "max_tokens", "max_completion_tokens",
                                       "response_format")}
        if isinstance(context[group].get("extra_body"), dict):
            context[group]["extra_body"] = {k: v for k, v in context[group]["extra_body"].items()
                                            if k not in PARAMETERS}
    return {"base_url": str(profile.get("baseUrl") or "").strip().rstrip("/"),
            "model": str(model if model is not None else profile.get("model") or "").strip(),
            "context": hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=True).encode()).hexdigest()}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_parameter_support(value):
    """Validate persisted/UI contracts; never preserve arbitrary error bodies."""
    if value is None or (isinstance(value, dict) and not value):
        return {}
    if not isinstance(value, dict) or set(value) - {"scope", "parameters"}:
        raise ValueError("Invalid parameter support document")
    scope = value.get("scope")
    parameters = value.get("parameters")
    if not isinstance(scope, dict) or set(scope) != {"base_url", "model", "context"}:
        raise ValueError("Parameter support requires an endpoint/model/context scope")
    if any(not isinstance(v, str) for v in scope.values()):
        raise ValueError("Invalid parameter support scope")
    if not re.fullmatch(r"[a-f0-9]{64}", scope["context"]):
        raise ValueError("Invalid parameter context fingerprint")
    if not isinstance(parameters, dict) or set(parameters) - set(PARAMETERS):
        raise ValueError("Unsupported parameter contract")
    result = {"scope": dict(scope), "parameters": {}}
    for name, item in parameters.items():
        if not isinstance(item, dict) or set(item) - {
            "status", "source", "values", "minimum", "maximum", "step", "reasoning_effort"
        }:
            raise ValueError("Invalid parameter descriptor")
        if item.get("status") not in STATUSES or item.get("source") not in SOURCES:
            raise ValueError("Invalid parameter evidence")
        descriptor = copy.deepcopy(item)
        values = item.get("values")
        if values is not None:
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError("Parameter values must be a nonempty bounded list")
            if name == "reasoning_effort":
                if any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v)
                       for v in values):
                    raise ValueError("Invalid reasoning effort value")
            elif any(not _number(v) or not 0 <= v <= 2 for v in values):
                raise ValueError("Invalid temperature value")
            if len(values) != len(set(values)):
                raise ValueError("Duplicate parameter values")
        if any(k in item for k in ("minimum", "maximum", "step")):
            if name != "temperature" or not all(_number(item.get(k)) for k in ("minimum", "maximum", "step")):
                raise ValueError("A temperature range requires finite minimum, maximum and step")
            if not 0 <= item["minimum"] < item["maximum"] <= 2 or not 0 < item["step"] <= 2:
                raise ValueError("Invalid temperature range")
        if item["status"] in {"supported", "fixed"} and values is None and "minimum" not in item:
            raise ValueError("Adjustable parameters require declared values or a range")
        if item["status"] == "fixed" and (values is None or len(values) != 1):
            raise ValueError("Fixed parameters require exactly one value")
        if "reasoning_effort" in item:
            effort = item["reasoning_effort"]
            if name != "temperature" or (effort is not None and (
                    not isinstance(effort, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", effort))):
                raise ValueError("Invalid temperature mode condition")
        result["parameters"][name] = descriptor
    return result


def scoped_parameters(profile, model=None):
    support = normalize_parameter_support(profile.get("parameterSupport") or {})
    return support.get("parameters", {}) if support.get("scope") == capability_scope(profile, model) else {}


def effective_parameter(profile, name):
    # Profile/request deep merges happen before the SDK overlays extra_body.
    value, extra = None, {}
    for group in ("generationDefaults", "requestOverrides"):
        options = profile.get(group) or {}
        if name in options:
            value = options[name]
        if "extra_body" in options:
            body = options["extra_body"]
            if body is None:
                extra = {}
            elif isinstance(body, Mapping) and name in body:
                if body[name] is None:
                    extra.pop(name, None)
                else:
                    extra[name] = body[name]
    return extra.get(name, value)


def apply_parameter_contract(params, profile, model=None):
    """Validate after overrides, including extra_body, without dropping tools/schema.

    Unknown support does not ban explicit advanced options. Unsupported/fixed
    controls omit the parameter and let the server choose its own default. A
    validated slider is authoritative over task hints and extra_body duplicates.
    """
    params = copy.deepcopy(params)
    descriptors = scoped_parameters(profile, model)
    # A detected control is a user-level setting, not a task default. Workflow
    # stages still suggest low/high in ModelRequest.options; those suggestions
    # must not override a saved slider, or resurrect a "server default" field.
    # Profiles without a scoped control retain the legacy merge semantics.
    for name, descriptor in descriptors.items():
        if descriptor["status"] in {"supported", "fixed", "unsupported"}:
            params.pop(name, None)
            (params.get("extra_body") or {}).pop(name, None)
            selected = effective_parameter(profile, name)
            if descriptor["status"] == "supported" and selected is not None:
                params[name] = copy.deepcopy(selected)
    for name, descriptor in descriptors.items():
        extra = params.get("extra_body") or {}
        value = extra.get(name, params.get(name))
        if descriptor["status"] in {"unsupported", "fixed"}:
            params.pop(name, None)
            extra.pop(name, None)
            continue
        if value is None:
            continue
        if descriptor["status"] != "supported":
            continue
        values = descriptor.get("values")
        if (values is not None and value not in values) or (
            name == "temperature" and (not _number(value) or not 0 <= value <= 2)
        ):
            raise ValueError(f"{name} is outside its detected parameter contract")
        if "minimum" in descriptor and not descriptor["minimum"] <= value <= descriptor["maximum"]:
            raise ValueError("temperature is outside its detected range")
        if name == "temperature" and "reasoning_effort" in descriptor:
            effort = extra.get("reasoning_effort", params.get("reasoning_effort"))
            if effort != descriptor["reasoning_effort"]:
                raise ValueError("Reasoning mode changed; reset temperature or detect capabilities again")
    return params


def parameter_error(error, name):
    """Return only a classification, never provider text (which may echo secrets)."""
    status = getattr(error, "status_code", None)
    if status not in (400, 422):
        return "unknown"
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return "unknown"
    body = body.get("error", body)
    if not isinstance(body, Mapping):
        return "unknown"
    parameter = str(body.get("param") or body.get("parameter") or "")
    message = str(body.get("message") or "").lower()
    # Schema errors often put the parameter in a nested location list.
    mentions_parameter = parameter.split(".")[-1] == name or bool(
        re.search(r"(?<![a-z_])" + re.escape(name) + r"(?![a-z_])", message))
    if not mentions_parameter:
        return "unknown"
    code = str(body.get("code") or body.get("type") or "").lower()
    if code in {"unsupported_parameter", "unknown_parameter", "unrecognized_parameter"}:
        return "unsupported"
    return "rejected"


def metadata_parameters(metadata):
    """Consume explicit parameter schemas, never infer from a model ID."""
    result = {}
    schema = metadata.get("parameters") or metadata.get("parameter_schema") or {}
    if not isinstance(schema, Mapping):
        return result
    schema = schema.get("properties", schema)
    if not isinstance(schema, Mapping):
        return result
    for name in PARAMETERS:
        raw = schema.get(name)
        if not isinstance(raw, Mapping):
            continue
        item = {"status": "supported", "source": "metadata"}
        if "enum" in raw:
            item["values"] = raw["enum"]
        elif "const" in raw:
            item.update(status="fixed", values=[raw["const"]])
        elif name == "temperature" and "minimum" in raw and "maximum" in raw:
            item.update(minimum=raw["minimum"], maximum=raw["maximum"], step=raw.get("multipleOf", 0.05))
        else:
            continue
        try:
            checked = normalize_parameter_support({"scope": capability_scope({}), "parameters": {name: item}})
            result[name] = checked["parameters"][name]
        except (TypeError, ValueError):
            # Untrusted/partial metadata must not break a working endpoint.
            continue
    return result
