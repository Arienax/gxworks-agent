"""Target runtime/special-device semantics for instructions.

Completion relays, pulse-output ownership and other target runtime facts live
here rather than in operand semantics or construction routing.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import json
from functools import lru_cache


@lru_cache(maxsize=1)
def _control_facts():
    from shared.paths import resource_path
    return json.loads(resource_path('instructions/control_runtime.json').read_text(encoding='utf-8'))


def control_runtime_facts(plc_model):
    """Only exact CPU entries; an unknown family never inherits another CPU."""
    return copy.deepcopy(_control_facts().get(str(plc_model).strip().upper(), {}))


def first_scan_fact(plc_model, execution_context=None):
    fact = control_runtime_facts(plc_model).get('first_scan')
    if not fact:
        return {'available': False, 'reason': 'missing_cpu_fact', 'target_model': plc_model}
    context = execution_context or {}
    missing = [key for key, value in fact['conditions'].items() if context.get(key) != value]
    return {**fact, 'target_model': plc_model, 'available': not missing,
            'reason': 'execution_condition_unverified' if missing else None,
            'unverified_conditions': missing}


def timer_runtime_fact(plc_model, address, opcode='OUT'):
    import re
    facts = control_runtime_facts(plc_model).get('timers', {})
    match = re.fullmatch(r'T(\d+)', str(address).upper())
    if match:
        for interval in facts.get(str(opcode).upper(), []):
            if interval['first'] <= int(match[1]) <= interval['last']:
                return {**interval, 'target_model': plc_model, 'form': str(opcode).upper(),
                        'device': str(address).upper(), 'source': copy.deepcopy(facts['source'])}
    return None


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


__all__ = ['resolve_instruction_runtime_semantics', 'control_runtime_facts', 'first_scan_fact', 'timer_runtime_fact']
