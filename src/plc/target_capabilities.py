"""Target-specific instruction applicability and operand constraints.

This lane overlays a selected CPU/model on vendor-level operand semantics. It
does not own completion relays or other runtime/special-device behavior.
"""
from __future__ import annotations

from collections.abc import Mapping


def _opcode(target):
    if isinstance(target, Mapping):
        return str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
    return str(target or "").strip().upper()


def resolve_target_applicability(target, plc_model):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = _opcode(target)
    model = str(plc_model or "").strip().upper()
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=model)
    if form is None:
        return {
            "target_model": model,
            "opcode": opcode,
            "available": False,
            "support_status": "unknown_instruction",
            "native_operand_order": [],
            "operand_order_status": "unresolved",
            "operand_constraints": [],
        }

    spec = form.spec
    coverage = spec.contract_coverage()
    available = bool(spec.supports_cpu(model))
    if coverage.get("cpu_applicability") == "source_verified":
        support_status = "source_verified"
    elif spec.cpu_support:
        support_status = "declared_cpu_scope"
    else:
        support_status = "shared_catalog"

    constraints = []
    for index, operand in enumerate(spec.operands, start=1):
        if operand.device_prefixes:
            constraints.append({
                "position": index,
                "device_prefixes": list(operand.device_prefixes),
            })

    return {
        "target_model": model,
        "opcode": form.opcode,
        "base_mnemonic": form.base_mnemonic,
        "available": available,
        "support_status": support_status,
        "declared_cpu_support": sorted(spec.cpu_support),
        "replacement_opcode": spec.replacement_for_cpu(model),
        "min_operands": spec.min_operands,
        "max_operands": spec.max_operands,
        "native_operand_order": list(spec.native_operand_order),
        "operand_order_status": coverage.get("operand_order", "unresolved"),
        "operand_role_status": coverage.get("operand_roles", "unresolved"),
        "operand_type_status": coverage.get("operand_types", "unresolved"),
        "operand_constraints": constraints,
        "device_class_status": coverage.get("device_classes", "unresolved"),
        "execution_form": spec.execution_form,
        "instruction_width": spec.instruction_width,
        "numeric_operand_boundaries": [
            {
                "operand_index": item.operand_index,
                "minimum": item.minimum,
                "maximum": item.maximum,
                "absolute": item.absolute,
                "unit": item.unit,
                "label": item.label,
            }
            for item in spec.numeric_operand_boundaries
        ],
        "disjoint_bit_ranges": [
            {
                "source_operand_index": item.source_operand_index,
                "destination_operand_index": item.destination_operand_index,
                "destination_length_operand_index": item.destination_length_operand_index,
                "source_length_operand_index": item.source_length_operand_index,
                "same_device_prefix_only": item.same_device_prefix_only,
                "error_code": item.error_code,
            }
            for item in spec.disjoint_bit_ranges
        ],
        "boundary_status": coverage.get("numeric_and_memory_boundaries", "unresolved"),
    }


__all__ = ["resolve_target_applicability"]
