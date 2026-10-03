"""Vendor-level instruction operand semantics.

This lane answers one question only: what does each operand position mean?
It deliberately excludes CPU applicability, special completion devices, hardware
resources and construction examples.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence


def _opcode(target):
    if isinstance(target, Mapping):
        return str(target.get("opcode") or target.get("base_opcode") or "").strip().upper()
    return str(target or "").strip().upper()


def resolve_common_operand_semantics(target):
    """Return the catalogue-owned operand meaning independent of target overlays."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = _opcode(target)
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
    if form is None:
        return {
            "vendor": "mitsubishi",
            "opcode": opcode,
            "semantic_scope": "unknown",
            "operands": [],
            "operand_role_status": "unresolved",
        }

    # base_spec is the pre-CPU-overlay definition. For generated D/P forms it
    # also points back to the semantic owner instead of copying one contract per
    # literal form.
    spec = form.base_spec
    coverage = spec.contract_coverage()
    cpu_scope = sorted(spec.cpu_support)
    return {
        "vendor": spec.vendor,
        "opcode": form.opcode,
        "base_mnemonic": form.base_mnemonic,
        "canonical_op": spec.canonical_op or form.base_mnemonic,
        "category": spec.category.value,
        "semantic_kind": spec.semantic_kind.value,
        "semantic_scope": "mitsubishi_common" if not cpu_scope else "catalog_scoped",
        "declared_cpu_support": cpu_scope,
        "operand_role_status": coverage.get("operand_roles", "unresolved"),
        "operand_type_status": coverage.get("operand_types", "unresolved"),
        "operands": [
            {
                "position": index,
                "name": operand.name,
                "role": operand.role.value,
                "data_type": operand.data_type,
                "usage_facts": [fact.as_mapping() for fact in operand.usage_facts],
            }
            for index, operand in enumerate(spec.operands, start=1)
        ],
    }


def bind_operand_slots(operand_semantics, target_applicability, operands=None):
    """Zip semantic positions, target-native symbols and concrete operands."""
    semantics = operand_semantics if isinstance(operand_semantics, Mapping) else {}
    applicability = target_applicability if isinstance(target_applicability, Mapping) else {}
    definitions = list(semantics.get("operands") or [])
    symbols = list(applicability.get("native_operand_order") or [])
    values = (
        [str(value) for value in operands]
        if isinstance(operands, Sequence) and not isinstance(operands, (str, bytes))
        else []
    )
    constraints = {
        int(item["position"]): list(item.get("device_prefixes") or ())
        for item in applicability.get("operand_constraints") or ()
        if isinstance(item, Mapping)
        and isinstance(item.get("position"), int)
        and item["position"] > 0
    }

    def merged_status(*values):
        normalized = [str(value or "unresolved") for value in values]
        if "source_verified" in normalized:
            return "source_verified"
        for value in normalized:
            if value not in {"", "unresolved"}:
                return value
        return "unresolved"

    role_status = merged_status(
        semantics.get("operand_role_status"),
        applicability.get("operand_role_status"),
    )
    type_status = merged_status(
        semantics.get("operand_type_status"),
        applicability.get("operand_type_status"),
    )
    order_status = str(applicability.get("operand_order_status") or "unresolved")
    device_class_status = str(
        applicability.get("device_class_status") or "unresolved"
    )
    target_usage = applicability.get("operand_usage_facts") or []

    count = max(len(definitions), len(symbols), len(values))
    slots = []
    for offset in range(count):
        definition = definitions[offset] if offset < len(definitions) else {}
        position = offset + 1
        has_definition = offset < len(definitions)
        slot = {
            "position": position,
            "name": str(definition.get("name") or f"operand_{position}"),
            "role": str(definition.get("role") or "unknown"),
            "role_status": role_status if has_definition else "unresolved",
            "data_type": str(definition.get("data_type") or "any"),
            "data_type_status": type_status if has_definition else "unresolved",
            "device_class_status": (
                device_class_status if has_definition else "unresolved"
            ),
        }
        usage = list(definition.get("usage_facts") or ())
        usage.extend(
            item["fact"] for item in target_usage
            if isinstance(item, Mapping) and item.get("position") == position
            and isinstance(item.get("fact"), Mapping)
        )
        slot["usage_facts"] = copy.deepcopy(usage)
        slot["purpose_status"] = operand_usage_status(usage, "purpose")
        if position in constraints:
            slot["device_prefixes"] = constraints[position]
        if offset < len(symbols):
            slot["symbol"] = str(symbols[offset])
            slot["symbol_status"] = order_status
        else:
            slot["symbol_status"] = "unresolved"
        if offset < len(values):
            slot["value"] = values[offset]
        slots.append(slot)
    return slots


def operand_usage_status(facts, facet):
    """No instruction-wide verification can certify an operand usage fact."""
    matching = [item for item in facts if isinstance(item, Mapping) and item.get("facet") == facet]
    if not matching:
        return "unresolved"
    statuses = {str(item.get("status") or "declared_unverified") for item in matching}
    if statuses == {"source_verified"} and all(item.get("sources") for item in matching):
        return "source_verified"
    if "declared_unverified" in statuses:
        return "declared_unverified"
    return "candidate_evidence"


def attach_operand_usage(slots, bindings):
    """Attach evidence to its position without promoting it or changing order."""
    result = copy.deepcopy(slots)
    for slot in result:
        usage = slot.setdefault("usage_facts", [])
        for binding in bindings:
            if binding.get("position") != slot.get("position"):
                continue
            fact = binding.get("fact")
            if not isinstance(fact, Mapping):
                continue
            if operand_usage_status(usage, fact.get("facet")) == "source_verified":
                continue
            if fact not in usage:
                usage.append(copy.deepcopy(fact))
        slot["purpose_status"] = operand_usage_status(usage, "purpose")
    return result


__all__ = ["attach_operand_usage", "bind_operand_slots", "operand_usage_status", "resolve_common_operand_semantics"]
