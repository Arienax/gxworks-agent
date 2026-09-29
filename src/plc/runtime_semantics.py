"""Target runtime/special-device semantics for instructions.

Completion relays, pulse-output ownership and other target runtime facts live
here rather than in operand semantics or construction routing.
"""
from __future__ import annotations

from collections.abc import Mapping


def _opcode(target):
    if isinstance(target, Mapping):
        return str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
    return str(target or "").strip().upper()


def resolve_instruction_runtime_semantics(target, plc_model):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = _opcode(target)
    model = str(plc_model or "").strip().upper()
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=model)
    if form is None or not form.spec.supports_cpu(model):
        return {
            "target_model": model,
            "opcode": opcode,
            "available": False,
            "special_devices": [],
        }

    spec = form.spec
    coverage = spec.contract_coverage()
    completion = None
    special_devices = []
    if spec.completion is not None:
        completion = {
            "device": spec.completion.device,
            "placement": spec.completion.placement,
            "status": coverage.get("completion_ownership", "unresolved"),
        }
        special_devices.append(spec.completion.device)

    pulse_output = None
    if spec.pulse_output is not None:
        pulse_output = {
            "frequency_operand_indexes": list(spec.pulse_output.frequency_operand_indexes),
            "pulse_output_operand_index": spec.pulse_output.pulse_output_operand_index,
            "direction_output_operand_index": spec.pulse_output.direction_output_operand_index,
            "status": coverage.get("hardware_applicability", "unresolved"),
        }

    return {
        "target_model": model,
        "opcode": form.opcode,
        "available": True,
        "completion": completion,
        "pulse_output": pulse_output,
        "special_devices": special_devices,
    }


__all__ = ["resolve_instruction_runtime_semantics"]
