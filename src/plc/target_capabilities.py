# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Target-specific instruction applicability and operand constraints.

This lane overlays a selected CPU/model on vendor-level operand semantics. It
does not own completion relays or other runtime/special-device behavior.
"""
from __future__ import annotations

from collections.abc import Mapping


def _operand_usage_facts(spec, coverage, *, model, opcode):
    """Project existing target rules, keeping their own evidence and indexes."""
    from plc.instructions import OperandUsageFact

    facts = []

    def add(index, facet, value, dimension):
        sources = tuple(
            {**source, "target_model": model, "opcode": opcode} for source in spec.contract_sources
            if dimension in (source.get("verified_fields") or ())
        )
        status = coverage.get(dimension, "unresolved")
        if status != "source_verified" or not sources:
            status = "declared_unverified"
        fact = OperandUsageFact(facet, value, status, sources)
        facts.append({"position": index + 1, "fact": fact.as_mapping()})

    boundary_dimension = "numeric_and_memory_boundaries"
    for item in spec.numeric_operand_boundaries:
        if item.label and item.label != "operand":
            add(item.operand_index, "purpose", item.label, boundary_dimension)
        if item.unit:
            add(item.operand_index, "unit", item.unit, boundary_dimension)
        add(item.operand_index, "range", {
            "minimum": item.minimum, "maximum": item.maximum, "absolute": item.absolute,
        }, boundary_dimension)
    for item in spec.disjoint_bit_ranges:
        for index, length, other in (
            (item.source_operand_index, item.source_length_operand_index, item.destination_operand_index),
            (item.destination_operand_index, item.destination_length_operand_index, item.source_operand_index),
        ):
            add(index, "range", {
                "length_position": length + 1, "unit": "bit",
                "disjoint_with_position": other + 1,
                "same_device_prefix_only": item.same_device_prefix_only,
                "error_code": item.error_code,
            }, boundary_dimension)
        for index, base in (
            (item.destination_length_operand_index, item.destination_operand_index),
            (item.source_length_operand_index, item.source_operand_index),
        ):
            # Knowing a range's length dependency does not prove the complete
            # instruction-specific purpose of that length/count operand.
            add(index, "range", {"length_of_position": base + 1, "unit": "bit"}, boundary_dimension)
            add(index, "unit", "bit", boundary_dimension)
    if spec.pulse_output:
        pulse = spec.pulse_output
        for index in pulse.frequency_operand_indexes:
            add(index, "purpose", "Pulse-output frequency", "hardware_applicability")
        add(pulse.pulse_output_operand_index, "purpose", "Pulse output device", "hardware_applicability")
        if pulse.direction_output_operand_index is not None:
            add(pulse.direction_output_operand_index, "purpose", "Direction output device", "hardware_applicability")
    return facts


def _opcode(target):
    if isinstance(target, Mapping):
        return str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
    return str(target or "").strip().upper()


def _generic_target_applicability(target, plc_model):
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
        "operand_definitions": [{"name": o.name, "role": o.role.value, "data_type": o.data_type,
                                 "usage_facts": [f.as_mapping() for f in o.usage_facts]}
                                for o in spec.operands]
                               if "operand_roles" in spec.verified_fields else [],
        "operand_constraints": constraints,
        "device_class_status": coverage.get("device_classes", "unresolved"),
        "execution_form": spec.execution_form,
        "execution_form_status": coverage.get("execution_form", "unresolved"),
        "operand_usage_facts": _operand_usage_facts(spec, coverage, model=model, opcode=form.opcode),
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
        **({"st_call": dict(spec.st_call), "call_contract": DEFAULT_INSTRUCTION_REGISTRY.describe_call(form.opcode, cpu=model), "source_notes": spec.notes,
            "contract_sources": [dict(s) for s in spec.contract_sources]} if spec.st_call else {}),
    }


def owner_admission(opcode, model, *, registry=None):
    """The exact compiled owner's admission record for one literal form and CPU.

    The lookup is vendor, literal form and exact CPU only: a record for another
    model or another form says nothing about this question.
    """
    if registry is None:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        registry = DEFAULT_INSTRUCTION_REGISTRY
    entry = registry.definition_entry(
        str(opcode or "").strip().upper(),
        cpu=str(model or "").strip().upper(),
        vendor="mitsubishi",
    )
    if not isinstance(entry, Mapping) or "call_available" not in entry:
        return None
    return entry


def apply_owner_admission(applicability, opcode, model, *, registry=None):
    """Reconcile one availability answer with the exact owner's admission record.

    ``call_available`` says whether the compiled build admitted this literal form on
    this CPU as an instruction a program may call. It is a software admission state,
    not a statement that the controller cannot execute the code. The shared spec
    answers from its CPU scope, where an empty scope means "not narrowed" rather
    than "admitted", so a non-callable owner would otherwise be reported available.

    The direct applicability query, the top-level lane and the nested definition all
    read this one projection. Missing records keep the previous answer: absence of an
    admission record is not an explicit refusal.
    """
    entry = owner_admission(opcode, model, registry=registry)
    if entry is None:
        return applicability
    admitted = bool(entry.get("call_available"))
    available = bool(applicability.get("available"))
    reasons = [str(gap) for gap in entry.get("gaps") or ()]
    projected = dict(applicability)
    record = {
        "available": admitted,
        "support_status": "definition_only" if not admitted else "callable",
        "reasons": reasons,
        "basis": {
            "vendor": str(entry.get("vendor") or "mitsubishi"),
            "opcode": entry.get("opcode"),
            "target_model": entry.get("target_model"),
        },
    }
    if not admitted:
        if available:
            # The generic definition could not express this admission state on its
            # own; keep the admission conclusion and say where it came from.
            record["generic_definition_available"] = True
        projected["available"] = False
        projected["support_status"] = record["support_status"]
    elif not available:
        # An admitted call the shared definition cannot describe is a disagreement
        # to report, not a reason to restore availability from the owner alone.
        record["inconsistent"] = True
        record["reasons"] = reasons + ["owner_admits_call_without_runtime_form"]
    projected["owner_admission"] = record
    return projected


def resolve_target_applicability(target, plc_model):
    """Public applicability for one target, reconciled with its exact owner."""
    applicability = _generic_target_applicability(target, plc_model)
    return apply_owner_admission(
        applicability, _opcode(target), str(plc_model or "").strip().upper())


__all__ = ["resolve_target_applicability"]
