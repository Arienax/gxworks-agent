"""Non-executable v3 parameter domains, presentation hints and bounded evidence.

UI precision is not server precision. Observations never define allowed values.
This module performs no I/O and is safe to use when migrating old configurations.
"""
from __future__ import annotations
import copy
import math
import json
import re
from collections.abc import Mapping


def finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and abs(value) <= 9007199254740991
    except OverflowError:
        return False


def scalar(value):
    return isinstance(value, bool) or finite(value) or isinstance(value, str) and len(value) <= 256


SOURCES = {"metadata", "catalog", "manual", "generic", "probe", "observation", "legacy"}
DOMAIN_KEYS = {"values", "minimum", "maximum", "multiple_of", "step", "exclusive_minimum", "exclusive_maximum", "source", "enforcement"}


def evidence(value):
    if not isinstance(value, dict) or set(value) - {"accepted_values", "rejected_values", "observed_context", "scan", "observations"}:
        raise ValueError("Invalid parameter evidence")
    if len(json.dumps(value, allow_nan=False)) > 8192:
        raise ValueError("Parameter evidence is too large")
    for name in ("accepted_values", "rejected_values"):
        values = value.get(name, [])
        if not isinstance(values, list) or len(values) > 128 or any(
            not scalar(v) for v in values):
            raise ValueError("Invalid evidence samples")
    observations = value.get("observations", [])
    if not isinstance(observations, list) or len(observations) > 32:
        raise ValueError("Too many observations")
    for item in observations:
        if not isinstance(item, dict) or set(item) - {"outcome", "value", "context", "at", "source", "code"}:
            raise ValueError("Invalid observation")
        if item.get("outcome") not in {"accepted", "rejected", "observed"}:
            raise ValueError("Invalid observation outcome")
        if item.get("source") not in {"probe", "observation"}:
            raise ValueError("Invalid observation source")
        if not isinstance(item.get("context"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["context"]):
            raise ValueError("Observation needs an opaque context fingerprint")
        if not finite(item.get("at")):
            raise ValueError("Invalid observation timestamp")
        if item.get("code", "") not in {"", "unsupported_parameter", "invalid_parameter", "invalid_value", "unknown_parameter"}:
            raise ValueError("Unrecognized observation code")
        val = item.get("value")
        if val is not None and not scalar(val):
            raise ValueError("Observation must not contain messages or objects")
    # observed_context is legacy evidence only; never a runtime requirement.
    ctx = value.get("observed_context", {})
    if not isinstance(ctx, dict) or set(ctx) - {"requires", "conflicts_with"}:
        raise ValueError("Invalid observed context")
    if value.get("scan") not in {None, "partial", "complete"}:
        raise ValueError("Invalid evidence coverage")
    return copy.deepcopy(value)


def prepare(raw):
    """Translate a v3 descriptor or legacy flat descriptor to internal fields."""
    if not isinstance(raw, Mapping):
        raise ValueError("Expected a parameter descriptor object")
    raw = copy.deepcopy(dict(raw))
    observed = evidence(raw.pop("evidence", {}))
    hints = raw.pop("ui_hint", {})
    if not isinstance(hints, dict) or set(hints) - {"minimum", "maximum", "step", "suggestions", "advanced"}:
        raise ValueError("Invalid UI hint")
    domain = raw.pop("domain", None)
    if domain is not None:
        if not isinstance(domain, dict) or set(domain) - DOMAIN_KEYS:
            raise ValueError("Invalid parameter domain")
        if any(k in raw for k in ("values", "minimum", "maximum", "step", "exclusive_minimum", "exclusive_maximum")):
            raise ValueError("Do not mix v2 and v3 parameter domains")
        source = domain.get("source", raw.get("source", "generic"))
        enforcement = domain.get("enforcement", "hard")
        if source not in SOURCES or enforcement not in {"hard", "hint"}:
            raise ValueError("Invalid domain authority")
        if source in {"probe", "observation", "generic"} and enforcement == "hard":
            raise ValueError("Observations and generic templates cannot impose a hard domain")
        if enforcement == "hard":
            raw.update({k: v for k, v in domain.items() if k in {"values", "minimum", "maximum", "step", "exclusive_minimum", "exclusive_maximum"}})
            if "multiple_of" in domain:
                if "step" in domain:
                    raise ValueError("Choose multiple_of or legacy step")
                raw["step"] = domain["multiple_of"]
        else:
            hints = {**{k: v for k, v in domain.items() if k in {"minimum", "maximum", "step"}}, **hints}
            if "values" in domain:
                hints.setdefault("suggestions", domain["values"])
    else:
        source = raw.get("source", "legacy")
        enforcement = "hard" if source in {"metadata", "manual", "catalog", "legacy"} else "hint"
        if source in {"probe", "observation"}:
            # Old sample membership/fixed guesses must not constrain editing.
            observed.setdefault("accepted_values", raw.pop("values", []))
            ctx = {k: raw.pop(k) for k in ("requires", "conflicts_with") if k in raw}
            if ctx:
                observed["observed_context"] = ctx
            if "scan" in raw:
                observed["scan"] = raw.pop("scan")
            for k in ("minimum", "maximum", "step", "exclusive_minimum", "exclusive_maximum"):
                raw.pop(k, None)
            if raw.get("status") in {"fixed", "supported", "conditional", "unsupported"}:
                raw["status"] = "accepted" if observed.get("accepted_values") else "unknown"
        elif source == "generic":
            for k in ("minimum", "maximum", "step"):
                if k in raw:
                    hints.setdefault(k, raw.pop(k))
            if "values" in raw:
                hints.setdefault("suggestions", raw.pop("values"))
    for k in ("minimum", "maximum", "step"):
        v = hints.get(k)
        if k in hints and not finite(v):
            raise ValueError("Invalid numeric UI hint")
    if hints.get("step", 1) <= 0 or hints.get("maximum", math.inf) < hints.get("minimum", -math.inf):
        raise ValueError("Invalid UI range")
    if "advanced" in hints and not isinstance(hints["advanced"], bool):
        raise ValueError("Invalid advanced presentation flag")
    suggestions = hints.get("suggestions", [])
    evidence({"accepted_values": suggestions})
    # The actual domain may be continuous even when the slider moves by 0.01.
    return raw, evidence(observed), hints, source, enforcement, domain is not None and "multiple_of" in domain
