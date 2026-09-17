"""Opt-in probe strategies, not an exhaustive list of model capabilities.

Unknown metadata controls need no registry entry. Only registered, bounded,
synthetic probes may actively spend tokens; registration never authorizes tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Tuple

from model_capabilities import EFFORT_CANDIDATES
from model_contract import CapabilityDescriptor, ParameterDescriptor, member
from model_provider import ModelProviderError, ModelRequest, SystemMessage, TextDelta, ToolCallEnd, UserMessage

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
    calls = [event.tool_call for event in events if isinstance(event, ToolCallEnd)]
    if len(calls) != 1 or calls[0].name != "capability_probe":
        return False
    try:
        arguments = calls[0].arguments
        return (json.loads(arguments) if isinstance(arguments, str) else arguments) == {"value": "ok"}
    except (TypeError, ValueError):
        return False


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


def _parameter_probe(provider, model, name, candidates, invalid, remaining, context=None,
                     fixed_single=False, *, known_values=(), exhaustive=True):
    # A short scan never turns one accepted sample into a fixed or continuous
    # domain. Deep scans reuse positive evidence but still run a negative control.
    accepted = list(known_values)
    result = {"status": "supported" if accepted else "unknown", "source": "probe", "scan": "partial"}
    if accepted:
        result["values"] = accepted
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

    negative = attempt(invalid)
    if negative in {"unsupported", "accepted"}:
        # A newly permissive gateway invalidates earlier validation evidence.
        return {"status": negative, "source": "probe", "scan": "complete"}
    if negative != "rejected":
        return result
    rejected = 0
    complete = exhaustive
    for value in candidates:
        if member(value, accepted):
            continue
        outcome = attempt(value)
        if outcome == "accepted":
            accepted.append(value)
        elif outcome in {"rejected", "unsupported"}:
            rejected += 1
        else:
            complete = False
            break
    if accepted:
        # Stable registry order makes deep-scan output independent of which
        # positive sample the quick scan tried first.
        values = [v for v in candidates if member(v, accepted)]
        values.extend(v for v in accepted if not member(v, values))
        result.update(status="supported", values=values)
        if exhaustive and complete and fixed_single and len(values) == 1 and rejected == len(candidates) - 1:
            result["status"] = "fixed"
    result["scan"] = "complete" if complete else "partial"
    return result


@dataclass
class ProbeContext:
    provider: Any
    model: str
    remaining: Callable[[], float]
    configured_values: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, ParameterDescriptor] = field(default_factory=dict)
    mode: str = "deep"
    previous: Mapping[str, ParameterDescriptor] = field(default_factory=dict)


@dataclass(frozen=True)
class ParameterProbe:
    name: str
    type: str
    candidates: Tuple[Any, ...]
    invalid: Any
    context_dependencies: Tuple[str, ...] = ()
    fixed_single: bool = False
    kind: str = "parameter"
    quick_value: Any = None

    def requirements(self, context):
        return {name: [context.configured_values.get(name)] for name in self.context_dependencies}

    def run(self, context):
        options, requires = {}, {}
        for name in self.context_dependencies:
            value = context.configured_values.get(name)
            descriptor = context.parameters.get(name)
            requires[name] = [value]
            if descriptor is not None and value is not None:
                descriptor.write(options, value)
        candidates = self.candidates
        if context.mode == "quick":
            candidate = context.configured_values.get(self.name,
                self.quick_value if self.quick_value is not None else self.candidates[0])
            # Only the registered scalar domain is tested; raw advanced JSON
            # cannot inject an object or a guessed nested provider field.
            template = ParameterDescriptor.from_dict(self.name,
                {"type": self.type, "status": "unknown", "source": "probe"})
            try:
                template.validate(candidate)
            except ValueError:
                candidate = self.quick_value if self.quick_value is not None else self.candidates[0]
            candidates = (candidate,)
        previous = context.previous.get(self.name)
        known = previous.values or () if previous and previous.status == "supported" else ()
        raw = _parameter_probe(context.provider, context.model, self.name, candidates,
            self.invalid, context.remaining, options, self.fixed_single,
            known_values=known, exhaustive=context.mode == "deep")
        if raw.get("values"):
            # Extending a quick result must not reorder an effort slider around
            # the newest sample. Only order observed values, never add untested
            # candidates from the registry to the reported domain.
            values = [v for v in self.candidates if member(v, raw["values"])]
            values.extend(v for v in raw["values"] if not member(v, values))
            raw["values"] = values
        if requires:
            raw["requires"] = requires
        raw["type"] = self.type
        return ParameterDescriptor.from_dict(self.name, raw)


@dataclass(frozen=True)
class CapabilityProbe:
    name: str
    check: Callable
    modes: Tuple[str, ...] = ()
    kind: str = "capability"

    def run(self, context):
        timeout = context.remaining()
        ok = self.check(context.provider, context.model, timeout) if timeout > 0 else False
        # A timeout, unavailable model or malformed output is not evidence of
        # lack of capability. Only explicit metadata/rejection can say that.
        return CapabilityDescriptor("supported" if ok else "unknown", "probe", self.modes if ok else ())


PROBE_REGISTRY = {
    "reasoning_effort": ParameterProbe("reasoning_effort", "enum", EFFORT_CANDIDATES, "__gxw_invalid_effort__", quick_value="low"),
    "temperature": ParameterProbe("temperature", "number", (0., .5, 1., 1.5, 2.), -1., ("reasoning_effort",), True, quick_value=1.0),
    "tools": CapabilityProbe("tools", _tool_probe),
    "structured_output": CapabilityProbe("structured_output", _structured_output_probe, ("json_object",)),
}
