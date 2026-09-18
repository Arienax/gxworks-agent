"""Versioned, model-neutral capability data. No SDK, network or PLC dependencies.

The schema is deliberately small: scalar controls, conjunctions of membership
conditions, conflicts and bounded JSON body paths. It is not an executable DSL.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlsplit
from model_runtime.domain import prepare as prepare_domain

PARAMETER_STATUSES = frozenset({"supported", "accepted", "unknown", "unsupported", "fixed", "conditional"})
CAPABILITY_STATUSES = frozenset({"supported", "unsupported", "unknown", "conditional"})
SOURCES = frozenset({"metadata", "probe", "manual", "legacy", "catalog", "generic", "observation"})
TYPES = frozenset({"enum", "number", "integer", "boolean", "string"})
# Protocol and credential ownership is NOT delegated to provider metadata.
RESERVED = frozenset({"model", "messages", "tools", "tool_choice", "functions", "function_call",
    "stream", "response_format", "api_key", "base_url", "api_base", "timeout", "max_retries",
    "headers", "extra_headers", "extra_query", "http_client", "client", "__proto__", "prototype", "constructor"})
MISSING = object()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("Invalid capability or parameter identifier")
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    if value in {"prototype", "constructor"} or compact in {"key", "token", "auth", "headers", "cookie", "cookies"} or any(
            part in compact for part in ("apikey", "credential", "authorization", "password", "secret", "accesstoken", "refreshtoken")):
        raise ValueError("Reserved identifier")
    return value


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def scalar(value):
    return value is None or isinstance(value, bool) or finite(value) or (isinstance(value, str) and len(value) <= 256)


def equal(left, right):
    # JSON booleans must not compare equal to numeric 0/1 in Python.
    return (type(left) is type(right) or (finite(left) and finite(right))) and left == right


def member(value, choices):
    return any(equal(value, choice) for choice in choices)


def checked_object(value, allowed, label):
    if not isinstance(value, Mapping) or set(value) - set(allowed):
        raise ValueError("Invalid " + label)
    return value


def bounded_map(value, limit=128):
    if not isinstance(value, Mapping) or len(value) > limit:
        raise ValueError("Expected a bounded descriptor map")
    return {identifier(k): v for k, v in value.items()}


def path_get(options, path, default=MISSING):
    for key in path:
        if not isinstance(options, Mapping) or key not in options:
            return default
        options = options[key]
    return options


def path_remove(options, path):
    if not path or not isinstance(options, dict):
        return
    if len(path) == 1:
        options.pop(path[0], None)
    elif isinstance(options.get(path[0]), dict):
        path_remove(options[path[0]], path[1:])
        if not options[path[0]]:
            options.pop(path[0], None)


def path_set(options, path, value):
    for key in path[:-1]:
        if key in options and not isinstance(options[key], dict):
            raise ValueError("Parameter wire path collides with a scalar option")
        options = options.setdefault(key, {})
    options[path[-1]] = copy.deepcopy(value)


@dataclass(frozen=True)
class ConstraintDescriptor:
    requires: Mapping[str, Tuple[Any, ...]] = field(default_factory=dict)
    conflicts_with: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value):
        value = checked_object(value or {}, {"requires", "conflicts_with"}, "constraint")
        requires = {}
        for name, choices in bounded_map(value.get("requires", {}), 32).items():
            if not isinstance(choices, (list, tuple)) or not 1 <= len(choices) <= 64 or not all(scalar(v) for v in choices):
                raise ValueError("A requirement needs a bounded list of scalar values")
            requires[name] = tuple(choices)
        conflicts = value.get("conflicts_with", [])
        if not isinstance(conflicts, (list, tuple)) or len(conflicts) > 32:
            raise ValueError("Invalid conflict list")
        return cls(requires, tuple(identifier(v) for v in conflicts))

    def to_dict(self):
        result = {}
        if self.requires:
            result["requires"] = {k: list(v) for k, v in self.requires.items()}
        if self.conflicts_with:
            result["conflicts_with"] = list(self.conflicts_with)
        return result

    def satisfied(self, values):
        return all(member(values.get(k), allowed) for k, allowed in self.requires.items()) and not any(
            values.get(k) is not None for k in self.conflicts_with)


@dataclass(frozen=True)
class ParameterDescriptor:
    type: str
    status: str
    source: str
    values: Optional[Tuple[Any, ...]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    wire_location: str = "body"
    wire_path: Tuple[str, ...] = ()
    label: str = ""
    default_mode: str = "omit"
    constraints: ConstraintDescriptor = field(default_factory=ConstraintDescriptor)
    # Coverage of the registered finite candidate set, NOT the whole value domain.
    scan: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    ui_hint: Mapping[str, Any] = field(default_factory=dict)
    domain_source: str = "metadata"
    enforcement: str = "hard"
    zero_anchored: bool = False
    exclusive_minimum: Optional[float] = None
    exclusive_maximum: Optional[float] = None

    @classmethod
    def from_dict(cls, name, value):
        identifier(name)
        value, observed, hints, domain_source, enforcement, zero_anchored = prepare_domain(value)
        value = checked_object(value, {"type", "status", "source", "values", "minimum", "maximum", "step",
            "wire_location", "wire_name", "wire_path", "label", "default_mode", "requires", "conflicts_with", "scan", "exclusive_minimum", "exclusive_maximum"}, "parameter descriptor")
        kind, status, source = value.get("type"), value.get("status"), value.get("source")
        if kind not in TYPES or status not in PARAMETER_STATUSES or source not in SOURCES:
            raise ValueError("Invalid parameter type or evidence")
        location = value.get("wire_location", "body")
        path = value.get("wire_path", [value.get("wire_name", name)])
        if location not in {"body", "extra_body"} or not isinstance(path, (list, tuple)) or not 1 <= len(path) <= 8:
            raise ValueError("Invalid parameter wire location")
        for key in path:
            identifier(key)
            compact = re.sub(r"[^a-z]", "", key.lower())
            if key in RESERVED or any(v in compact for v in ("credential", "authorization", "apikey", "password", "secret")):
                raise ValueError("Parameter cannot own a protocol or credential field")
        if len(path) > 1 and path[0] == name:
            raise ValueError("A scalar parameter cannot shadow its own object path")
        if name in RESERVED or (location == "body" and path[0] == "extra_body"):
            raise ValueError("Reserved parameter name or wire path")
        values = value.get("values")
        if values is not None:
            if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 128 or not all(scalar(v) and v is not None for v in values):
                raise ValueError("Invalid parameter values")
            if any(member(v, values[:i]) for i, v in enumerate(values)):
                raise ValueError("Duplicate parameter values")
        lower, upper, step = (value.get(k) for k in ("minimum", "maximum", "step"))
        if any(v is not None and (not finite(v) or abs(v) > 9007199254740991) for v in (lower, upper, step)):
            raise ValueError("Numeric bounds exceed the portable JSON control range")
        if step is not None and step < 1e-12:
            raise ValueError("Parameter step is too small")
        if any(v is not None for v in (lower, upper, step)):
            if kind not in {"number", "integer"} or any(v is not None and not finite(v) for v in (lower, upper, step)):
                raise ValueError("Numeric bounds must be finite")
            if lower is not None and upper is not None and lower > upper or step is not None and step <= 0:
                raise ValueError("Invalid numeric range")
            if kind == "integer" and any(v is not None and int(v) != v for v in (lower, upper, step)):
                raise ValueError("Integer bounds and steps must be integral")
        if kind == "enum" and status in {"supported", "fixed", "conditional"} and values is None:
            raise ValueError("Enum controls require declared values")
        if status == "fixed" and (values is None or len(values) != 1):
            raise ValueError("Fixed controls require one value")
        label = value.get("label", "")
        if not isinstance(label, str) or len(label) > 128 or value.get("default_mode", "omit") not in {"omit", "inherit"}:
            raise ValueError("Invalid parameter presentation")
        constraints = ConstraintDescriptor.from_dict({k: value[k] for k in ("requires", "conflicts_with") if k in value})
        if status == "conditional" and not constraints.to_dict():
            raise ValueError("Conditional parameters require a constraint")
        scan = value.get("scan")
        if scan is not None and (scan not in {"partial", "complete"} or source != "probe"):
            raise ValueError("Invalid probe scan coverage")
        result = cls(kind, status, source, tuple(values) if values is not None else None,
            lower, upper, step, location, tuple(path), label, value.get("default_mode", "omit"), constraints, scan, observed, hints, domain_source, enforcement, zero_anchored,
            value.get("exclusive_minimum"), value.get("exclusive_maximum"))
        for bound in (result.exclusive_minimum, result.exclusive_maximum):
            if bound is not None and (kind not in {"number", "integer"} or not finite(bound)):
                raise ValueError("Invalid exclusive numeric bound")
        for item in values or ():
            result.validate(item)
        return result

    def validate(self, value):
        if not scalar(value) or value is None:
            raise ValueError("Parameter requires a finite scalar value")
        if self.type in {"number", "integer"}:
            if not finite(value) or abs(value) > 9007199254740991 or (self.type == "integer" and int(value) != value):
                raise ValueError("Invalid numeric parameter type")
        elif self.type == "boolean" and not isinstance(value, bool):
            raise ValueError("Expected a boolean parameter")
        elif self.type in {"string", "enum"} and self.values is None and not isinstance(value, str):
            raise ValueError("Expected a string parameter")
        if self.values is not None and not member(value, self.values):
            raise ValueError("Parameter value is not in its declared or verified values")
        if self.minimum is not None and value < self.minimum or self.maximum is not None and value > self.maximum:
            raise ValueError("Parameter value is outside its declared bounds")
        if self.exclusive_minimum is not None and value <= self.exclusive_minimum or self.exclusive_maximum is not None and value >= self.exclusive_maximum:
            raise ValueError("Parameter value is outside exclusive bounds")
        if self.step is not None:
            # step is a grid anchored at minimum (or zero), not a guess of a
            # continuous range from a few successful probe values.
            units = (value - (0 if self.zero_anchored else self.minimum or 0)) / self.step
            if not math.isclose(units, round(units), rel_tol=0, abs_tol=1e-7):
                raise ValueError("Parameter value does not match its declared step")

    def to_dict(self):
        domain = {"source": self.domain_source, "enforcement": self.enforcement}
        if self.values is not None:
            domain["values"] = list(self.values)
        for key in ("minimum", "maximum", "step", "exclusive_minimum", "exclusive_maximum"):
            if getattr(self, key) is not None:
                domain["multiple_of" if key == "step" and self.zero_anchored else key] = getattr(self, key)
        result = {"type": self.type, "status": self.status, "source": self.source,
            "wire_location": self.wire_location, "wire_path": list(self.wire_path), "default_mode": self.default_mode,
            "domain": domain, "evidence": copy.deepcopy(dict(self.evidence)), "ui_hint": copy.deepcopy(dict(self.ui_hint))}
        if self.label:
            result["label"] = self.label
        return {**result, **self.constraints.to_dict()}

    def read(self, options):
        # extra_body wins exactly as it does in the OpenAI SDK.
        value = path_get(options, ("extra_body",) + self.wire_path)
        return path_get(options, self.wire_path) if value is MISSING else value

    def remove(self, options, name):
        for path in {self.wire_path, ("extra_body",) + self.wire_path, (name,), ("extra_body", name)}:
            path_remove(options, path)

    def write(self, options, value):
        path_set(options, (("extra_body",) if self.wire_location == "extra_body" else ()) + self.wire_path, value)


@dataclass(frozen=True)
class CapabilityDescriptor:
    status: str
    source: str
    modes: Tuple[str, ...] = ()
    value: Any = None
    constraints: ConstraintDescriptor = field(default_factory=ConstraintDescriptor)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value):
        checked_object(value, {"status", "source", "modes", "value", "requires", "conflicts_with", "evidence"}, "capability")
        if value.get("status") not in CAPABILITY_STATUSES or value.get("source") not in SOURCES:
            raise ValueError("Invalid capability evidence")
        modes = value.get("modes", [])
        if not isinstance(modes, (list, tuple)) or len(modes) > 32 or not scalar(value.get("value")):
            raise ValueError("Invalid capability details")
        constraints = ConstraintDescriptor.from_dict({k: value[k] for k in ("requires", "conflicts_with") if k in value})
        if value["status"] == "conditional" and not constraints.to_dict():
            raise ValueError("Conditional capabilities require a constraint")
        from model_runtime.domain import evidence
        return cls(value["status"], value["source"], tuple(identifier(v) for v in modes), value.get("value"), constraints, evidence(value.get("evidence", {})))

    def to_dict(self):
        result = {"status": self.status, "source": self.source, **self.constraints.to_dict()}
        if self.modes:
            result["modes"] = list(self.modes)
        if self.value is not None:
            result["value"] = self.value
        if self.evidence:
            result["evidence"] = copy.deepcopy(self.evidence)
        return result


@dataclass(frozen=True)
class CapabilityContract:
    scope: Mapping[str, str]
    capabilities: Mapping[str, CapabilityDescriptor] = field(default_factory=dict)
    parameters: Mapping[str, ParameterDescriptor] = field(default_factory=dict)
    constraints: Mapping[str, ConstraintDescriptor] = field(default_factory=dict)
    schema_version: int = 3

    @classmethod
    def from_dict(cls, value):
        checked_object(value, {"schema_version", "scope", "capabilities", "parameters", "constraints"}, "capability contract")
        if value.get("schema_version") not in {2, 3}:
            raise ValueError("Unsupported capability contract version")
        scope = value.get("scope")
        if not isinstance(scope, Mapping) or set(scope) != {"endpoint", "model", "context", "binding"}:
            raise ValueError("Contract requires endpoint/model/context/binding scope")
        if any(not isinstance(v, str) for v in scope.values()):
            raise ValueError("Invalid capability scope")
        parsed = urlsplit(scope["endpoint"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or any((parsed.username, parsed.password, parsed.query, parsed.fragment)):
            raise ValueError("Invalid contract endpoint")
        if not scope["model"] or len(scope["model"]) > 256 or len(scope["endpoint"]) > 2048:
            raise ValueError("Invalid contract model")
        if not re.fullmatch(r"[a-f0-9]{64}", scope["context"]) or (scope["binding"] and not re.fullmatch(r"[a-f0-9]{64}", scope["binding"])):
            raise ValueError("Invalid scope fingerprint")
        parameters = {k: ParameterDescriptor.from_dict(k, v) for k, v in bounded_map(value.get("parameters", {})).items()}
        validate_parameter_paths(parameters)
        capabilities = {k: CapabilityDescriptor.from_dict(v) for k, v in bounded_map(value.get("capabilities", {})).items()}
        constraints = {k: ConstraintDescriptor.from_dict(v) for k, v in bounded_map(value.get("constraints", {})).items()
            if not (value.get("schema_version") == 2 and value.get("parameters", {}).get(k, {}).get("source") == "probe")}
        if set(constraints) - set(parameters) - set(capabilities):
            raise ValueError("Constraint target is not declared")
        for name, desc in list(capabilities.items()) + list(parameters.items()):
            conditions = [desc.constraints, constraints.get(name, ConstraintDescriptor())]
            for condition in conditions:
                if name in condition.requires or name in condition.conflicts_with:
                    raise ValueError("Self-referential constraint")
                allowed_refs = set(parameters) | set(capabilities) | {"stream", "tools", "response_format"}
                if (set(condition.requires) | set(condition.conflicts_with)) - allowed_refs:
                    raise ValueError("Constraint references an undeclared field")
        result = cls(dict(scope), capabilities, parameters, constraints)
        if len(json.dumps(result.to_dict(), allow_nan=False)) > 64000:
            raise ValueError("Capability contract exceeds 64 KiB")
        return result

    def to_dict(self):
        return {"schema_version": 3, "scope": dict(self.scope),
            "capabilities": {k: v.to_dict() for k, v in self.capabilities.items()},
            "parameters": {k: v.to_dict() for k, v in self.parameters.items()},
            "constraints": {k: v.to_dict() for k, v in self.constraints.items()}}


def validate_parameter_paths(parameters):
    # Logical aliases and declared wire paths share one JSON namespace after
    # SDK extra_body merging. Reject parent/child ownership across controls.
    owned = []
    for name, desc in parameters.items():
        for path in {desc.wire_path, (name,)}:
            for owner, previous in owned:
                if owner != name and (path[:len(previous)] == previous or previous[:len(path)] == path):
                    raise ValueError("Overlapping parameter wire paths")
            owned.append((name, path))


def credential_fingerprint(key):
    return hashlib.sha256(("gxw-contract-v2\0" + str(key)).encode()).hexdigest() if key else ""


def contract_scope(profile, parameters, model=None, api_key=None):
    context = {}
    for group in ("generationDefaults", "requestOverrides"):
        options = copy.deepcopy(profile.get(group) or {})
        for name, desc in parameters.items():
            desc.remove(options, name)
        context[group] = options
    # These legacy switches affect the actual request shape, unlike detected
    # capability observations. They remain part of the context identity.
    if profile.get("capabilityOverrides"):
        context["manual_overrides"] = profile["capabilityOverrides"]
    context["transport_flags"] = {k: v for k, v in (profile.get("capabilities") or {}).items()
        if k in {"thinking_required", "tool_stream", "disable_tool_choice_with_thinking"}}
    return {"endpoint": str(profile.get("baseUrl") or "").strip().rstrip("/"),
        "model": str(model if model is not None else profile.get("model") or "").strip(),
        "context": hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest(),
        "binding": credential_fingerprint(api_key)}


def normalize_contract(value):
    if value is None or isinstance(value, dict) and not value:
        return {}
    result = CapabilityContract.from_dict(value).to_dict()
    # The TTL-bounded observation database owns runtime records. Do not freeze
    # transient observations in a saved profile and resurrect them after expiry.
    for group in ("parameters", "capabilities"):
        for descriptor in result.get(group, {}).values():
            evidence = descriptor.get("evidence", {})
            evidence.pop("observations", None)
    return CapabilityContract.from_dict(result).to_dict()


def scoped_contract(profile, model=None, api_key=None):
    raw = profile.get("capabilityContract")
    if not raw:
        return None
    contract = CapabilityContract.from_dict(raw)
    current = contract_scope(profile, contract.parameters, model, api_key)
    keys = ("endpoint", "model", "context") + (("binding",) if api_key is not None else ())
    return contract if all(current[k] == contract.scope[k] for k in keys) else None


@dataclass(frozen=True)
class UserModelSettings:
    scope: Mapping[str, str]
    parameters: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_dict(cls, value, contract):
        value = checked_object(value, {"scope", "parameters"}, "user model settings")
        if value.get("scope") != contract.scope:
            raise ValueError("User selections do not belong to the current contract")
        parameters = {}
        for name, selection in bounded_map(value.get("parameters", {})).items():
            if name not in contract.parameters:
                raise ValueError("User selected an undeclared parameter")
            checked_object(selection, {"mode", "value"}, "parameter selection")
            mode = selection.get("mode")
            if mode not in {"value", "omit", "inherit"} or (mode == "value") != ("value" in selection):
                raise ValueError("Invalid parameter selection mode")
            if mode == "value":
                desc = contract.parameters[name]
                if desc.status in {"fixed", "unsupported"}:
                    raise ValueError("Parameter is not adjustable")
                desc.validate(selection["value"])
            parameters[name] = copy.deepcopy(dict(selection))
        return cls(dict(contract.scope), parameters)

    def to_dict(self):
        return {"scope": dict(self.scope), "parameters": copy.deepcopy(dict(self.parameters))}


def legacy_contract(profile, api_key=None):
    """Read-only v1 migration. Never invent observations for an unscoped model."""
    from model_runtime.capabilities import scoped_parameters, effective_parameter
    legacy = scoped_parameters(profile)
    if not legacy:
        return None, {}
    parameters = {}
    for name, raw in legacy.items():
        item = {k: copy.deepcopy(v) for k, v in raw.items() if k != "reasoning_effort"}
        item["type"] = "number" if name == "temperature" else "enum"
        if "reasoning_effort" in raw:
            item["requires"] = {"reasoning_effort": [raw["reasoning_effort"]]}
        parameters[name] = ParameterDescriptor.from_dict(name, item)
    # A legacy temperature-only document can depend on a manually set effort.
    for desc in tuple(parameters.values()):
        for name in desc.constraints.requires:
            parameters.setdefault(name, ParameterDescriptor.from_dict(name, {"type": "enum", "status": "unknown", "source": "legacy"}))
    capabilities = {}
    for name, value in (profile.get("capabilities") or {}).items():
        try:
            identifier(name)
            if isinstance(value, bool):
                capabilities[name] = CapabilityDescriptor("supported" if value else "unsupported", "legacy")
        except ValueError:
            continue
    contract = CapabilityContract(contract_scope(profile, parameters, api_key=api_key), capabilities, parameters)
    selections = {}
    for name, desc in parameters.items():
        value = effective_parameter(profile, name)
        if desc.status in {"supported", "accepted", "unknown", "fixed", "unsupported"}:
            selections[name] = {"mode": "omit"} if value is None or desc.status in {"fixed", "unsupported"} else {"mode": "value", "value": value}
    return contract, UserModelSettings(dict(contract.scope), selections).to_dict()


def metadata_contract_parts(metadata):
    """Consume arbitrary *declared* scalar schemas; never a model-name registry.

    Unsupported JSON-Schema constructs are skipped, not misread as an
    unconstrained scalar. Bad metadata cannot break a usable endpoint.
    """
    parameters, capabilities, constraints = {}, {}, {}
    schema = metadata.get("parameters") or metadata.get("parameter_schema") or {}
    if isinstance(schema, Mapping):
        schema = schema.get("properties", schema)
    if isinstance(schema, Mapping):
        for name, raw in list(schema.items())[:128]:
            if not isinstance(raw, Mapping):
                continue
            try:
                if set(raw) & {"oneOf", "anyOf", "allOf", "$ref", "not", "pattern", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength"}:
                    continue
                kind = raw.get("type")
                if kind is None:
                    kind = "enum" if "enum" in raw or "values" in raw else "number" if "minimum" in raw or "maximum" in raw else None
                if "const" in raw and kind is None:
                    v = raw["const"]
                    kind = "boolean" if isinstance(v, bool) else "number" if finite(v) else "string"
                if "enum" in raw:
                    kind = "enum" if kind not in {"number", "integer", "boolean"} else kind
                item = {k: copy.deepcopy(raw[k]) for k in ("minimum", "maximum", "wire_location", "wire_path", "wire_name", "requires", "conflicts_with", "label", "default_mode") if k in raw}
                item.update(type=kind, status=raw.get("status", "supported"), source="metadata")
                if "enum" in raw or "values" in raw:
                    item["values"] = raw.get("enum", raw.get("values"))
                if "const" in raw:
                    item.update(status="fixed", values=[raw["const"]])
                if "multipleOf" in raw or "step" in raw:
                    item["step"] = raw.get("multipleOf", raw.get("step"))
                # JSON Schema multipleOf is anchored at zero; our small
                # control schema anchors step at minimum. Align the lower
                # bound instead of admitting values the provider forbids.
                if "multipleOf" in raw and finite(raw["multipleOf"]) and raw["multipleOf"] > 0 and finite(item.get("minimum")):
                    step = raw["multipleOf"]
                    item["minimum"] = round(math.ceil(item["minimum"] / step - 1e-9) * step, 12)
                desc = ParameterDescriptor.from_dict(name, item)
                validate_parameter_paths({**parameters, name: desc})
                parameters[name] = desc
            except (ValueError, TypeError, OverflowError):
                continue
    raw_caps = metadata.get("capabilities", {})
    if isinstance(raw_caps, Mapping):
        for name, raw in list(raw_caps.items())[:128]:
            try:
                identifier(name)
                item = {"status": "supported" if raw else "unsupported"} if isinstance(raw, bool) else dict(raw)
                item["source"] = "metadata"
                item.pop("evidence", None)  # Remote declarations cannot manufacture local observations.
                capabilities[name] = CapabilityDescriptor.from_dict(item)
            except (TypeError, ValueError):
                continue
    if finite(metadata.get("context_window")) and metadata["context_window"] > 0:
        capabilities["context_window"] = CapabilityDescriptor("supported", "metadata", value=metadata["context_window"])
    raw_constraints = metadata.get("constraints", {})
    if isinstance(raw_constraints, Mapping):
        for name, raw in list(raw_constraints.items())[:128]:
            try:
                if name in parameters or name in capabilities:
                    constraints[name] = ConstraintDescriptor.from_dict(raw)
            except (TypeError, ValueError):
                continue
    return parameters, capabilities, constraints
