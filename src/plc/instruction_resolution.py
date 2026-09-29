"""Materialize the three non-construction instruction lanes for one target."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from plc.instruction_semantics import bind_operand_slots, resolve_common_operand_semantics
from plc.runtime_semantics import resolve_instruction_runtime_semantics
from plc.target_capabilities import resolve_target_applicability


def resolve_instruction_lanes(target, *, plc_model):
    operand_semantics = resolve_common_operand_semantics(target)
    target_applicability = resolve_target_applicability(target, plc_model)
    runtime_semantics = resolve_instruction_runtime_semantics(target, plc_model)
    operands = target.get("operands") if isinstance(target, Mapping) else None
    if not isinstance(operands, Sequence) or isinstance(operands, (str, bytes)):
        operands = None
    operand_slots = bind_operand_slots(
        operand_semantics,
        target_applicability,
        operands,
    )
    return {
        "operand_semantics": operand_semantics,
        "target_applicability": target_applicability,
        "runtime_semantics": runtime_semantics,
        "operand_slots": operand_slots,
    }


__all__ = ["resolve_instruction_lanes"]
