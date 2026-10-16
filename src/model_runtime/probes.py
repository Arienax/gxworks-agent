"""Safe synthetic fixtures for explicit, single-request verification.

This is a capability verifier registry, never a list of allowed model controls.
There is deliberately no numeric sampling, negative control or auto discovery.
"""
from dataclasses import dataclass
from typing import Mapping, Any


@dataclass(frozen=True)
class VerificationSpec:
    prompt: str
    tools: tuple[Mapping[str, Any], ...] = ()
    response_format: Mapping[str, Any] | None = None


VERIFIERS = {
    "tools": VerificationSpec(
        "Call capability_probe exactly once with value='ok'.",
        ({"type": "function", "function": {
            "name": "capability_probe", "description": "Return the fixed probe value.",
            "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["ok"]}},
                           "required": ["value"], "additionalProperties": False}}},)),
    "structured_output": VerificationSpec(
        'Return only the JSON object {"probe":true}.', response_format={"type": "json_object"}),
}
