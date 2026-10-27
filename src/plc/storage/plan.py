# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""The Q/L storage plan: what form an instruction stores in, and its stored width.

One plan answers both questions a caller can have: Core asks for the expected width,
and the project writer encodes the operands and header from the same selection. The
plan states the target CPU and storage mode, the chosen family and variant, the operand
storage, the expected width, and the context or assumption it needed - or why it could
not decide.

Nothing here is CPU admission, native acceptance or execution evidence. ``context`` is
the saved project allocation; unknown stays unknown instead of becoming a default, and
``assume_default_allocation`` is the explicit opt-in that says so in the evidence.

Reading a stored width back out of an existing program is a different question and is
not answered here: a reader preserves the recorded width, including a positive width
that differs from this calculation.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from plc.storage.errors import StorageRuleError as GXWFormatError
from plc.storage.tables import storage_profile as source_token_profile
from plc.storage.q_encoder import (encode_q_operand, select_q_storage_form)


#: Failure texts that mean "known CPU, but this form has no covered rule".
_NO_PROFILE_MARKERS = (
    "has no proven storage descriptor",
    "needs an exact CPU rule",
    "needs the exact saved CPU",
    "no CPU storage mode",
    "exceeds its saved ordinary and extended capacity",
    "unsupported operand spelling",
    "outside the observed",
    "is unobserved",
    "not encodable",
)

_CONFLICT_MARKERS = ("ambiguous", "parameter roles")

_CONTEXT_MARKERS = (
    "needs project allocation context",
    "project allocation context is required",
    "storage context",
)

#: A string payload is stored in the project code page, so its width is unknown
#: until that is supplied: a context gap with a named requirement, not a gap in
#: the instruction coverage.
_ENCODING_MARKERS = ("code page", "text encoding", "string needs its")


def _normalised_operand(value: Any) -> str:
    """One operand with device/literal case folded, string payloads untouched."""
    text = str(value).strip()
    if text.startswith('"'):
        return text
    return text.upper()


def cpu_storage_mode(cpu: Any) -> str | None:
    """Observed CPU storage group, independent of the lexical profile."""
    from plc.storage.q_encoder import TABLES
    return TABLES["cpu_modes"].get(str(cpu or "").strip().upper())


def _status_for(error: GXWFormatError) -> tuple[str, tuple[str, ...]]:
    text = str(error)
    lowered = text.lower()
    if any(marker in lowered for marker in _ENCODING_MARKERS):
        return "requires_context", ("text_encoding",)
    if any(marker in lowered for marker in _CONTEXT_MARKERS):
        return "requires_context", ("storage_context",)
    if any(marker in lowered for marker in _CONFLICT_MARKERS):
        return "conflict", ()
    if any(marker in lowered for marker in _NO_PROFILE_MARKERS):
        return "no_profile", ()
    return "no_profile", ()


def _members(identity: str) -> set:
    """The menu option's own members (a single CPU is its own member)."""
    return {part.strip() for part in str(identity or "").split("/") if part.strip()}


def context_cpu_mismatch(context: Any, identity: str, *, target: str | None = None) -> str | None:
    """Reason when an allocation context was read for another CPU, else None.

    A merged menu option accepts any of its own members: one saved project cannot
    carry two allocations, and the context names the option it was read from.
    ``target`` is the whole menu option the member ``identity`` belongs to, so a
    context read for one member also covers a merged target and vice versa.
    """
    context_cpu = str(getattr(context, "cpu", "") or "").strip().upper()
    if not context_cpu:
        return None
    # One saved project cannot carry two allocations, so a merged option and a
    # context read from any of that option's members are the same allocation.
    accepted = {context_cpu} | _members(context_cpu)
    whole = str(target or identity or "").strip().upper()
    if identity in accepted or _members(whole) & accepted or whole in accepted:
        return None
    return (f"Q source storage context differs from the saved CPU: the allocation "
            f"was read for {context_cpu}, not {identity}")


def allocation_dependent_operands(operands: Sequence[Any], *, op: Any = None,
                                  profile: str = "q03udv",
                                  cpu: Any = None) -> tuple[str, ...]:
    """Operands whose stored width can follow the saved D/W allocation.

    The answer comes from the encoded operand, not from its spelling, so one
    address has one state however it is written (``WA00`` = ``W0A00``,
    ``@D12000``, ``D12000Z0``) and an operand that addresses another unit
    (``J1\\WA00``, ``U0\\G0``) or an unexpanded macro placeholder never asks for the
    local allocation.
    """
    from plc.storage.q_encoder import allocation_dependent_operands as classify
    return classify(operands, op=op, profile=profile, cpu=cpu)


def resolve_stored_steps(opcode: Any, operands: Sequence[Any] = (), *, cpu: Any,
                         context: Any = None, text_encoding: str | None = None,
                         assume_default_allocation: bool = False,
                         target_identity: str | None = None):
    """Return the expected stored width for one Q/L instruction instance.

    ``context`` is the saved project allocation. Three states are distinguished:
    unknown (``None``) answers ``requires_context`` for a D/W operand instead of
    assuming an unextended allocation, an empty allocation answers from the
    ordinary ranges, and a parsed allocation answers from its own ranges.
    ``assume_default_allocation`` is the explicit opt-in for callers that have no
    saved project and want the unextended result; the evidence states that
    premise.
    """
    from plc.step_width_types import (STATUS_KNOWN, STATUS_NO_PROFILE,
                                 STATUS_UNKNOWN_CPU, StepWidth,
                                 known_width as _known, unresolved_width as _unresolved)

    identity = str(cpu or "").strip().upper()
    if not identity:
        return _unresolved(STATUS_UNKNOWN_CPU,
                           "A Q/L stored width needs the saved CPU identity",
                           requirements=("plc_model",))
    try:
        profile = source_token_profile(identity)
    except GXWFormatError as error:
        if "parameter" in str(error).lower():
            # The CPU is known, but it carries parameter files instead of a
            # program: that is a coverage statement, not an unknown target.
            return _unresolved(STATUS_NO_PROFILE, str(error))
        return _unresolved(STATUS_UNKNOWN_CPU, str(error))
    name = str(opcode or "").strip().upper()
    if not name:
        return _unresolved("requires_operands", "Instruction opcode is missing",
                           requirements=("operands",))
    # A Q/L operand is a device reference or a literal; its case is spelling, not
    # storage. A quoted string keeps its case because it is payload.
    args = tuple(_normalised_operand(value) for value in operands or ())
    mode = cpu_storage_mode(identity)
    if context is not None:
        mismatch = context_cpu_mismatch(context, identity, target=target_identity)
        if mismatch is not None:
            return _unresolved("requires_context", mismatch,
                               requirements=("storage_context",))
    assumed_default = False
    dependent = allocation_dependent_operands(args, op=name, profile=profile, cpu=identity)
    if context is None:
        if dependent and not assume_default_allocation:
            return _unresolved(
                "requires_context",
                (f"{identity} needs the saved device allocation for "
                 f"{', '.join(dependent)}: a D/W range that is mapped onto the ZR "
                 "extension changes the stored width"),
                requirements=("storage_context",))
        assumed_default = bool(dependent)
    try:
        encoded = tuple(
            encode_q_operand(value, op=name, index=index, profile=profile,
                             text_encoding=text_encoding, cpu=identity)
            for index, value in enumerate(args))
        selection = select_q_storage_form(name, args, encoded, profile=profile, cpu=identity,
                                          storage_context=context,
                                          target_identity=target_identity)
    except GXWFormatError as error:
        status, requirements = _status_for(error)
        if not args and status == "no_profile":
            # No operand was supplied for a form that needs them: that is a
            # missing-operand question, not a coverage gap.
            return _unresolved("requires_operands", str(error), requirements=("operands",))
        if mode is None and status == "no_profile":
            return _unresolved(status, f"{identity} has no observed Q/L storage mode: {error}",
                               requirements=requirements)
        return _unresolved(status, str(error), requirements=requirements)
    # The width is the selection's own answer, not a byte read back out of a header.
    width = selection.steps
    evidence = (f"native Q/L stored header {selection.header.hex()} for {identity} "
                f"(family {selection.family:#04x}, variant {selection.variant:#04x}, "
                f"mode {selection.mode or 'common'}, profile {profile})",)
    if assumed_default:
        evidence = evidence + ("assumed the default (unextended) allocation for "
                               f"{', '.join(dependent)}; the saved project allocation was "
                               "not supplied",)
    return _known(int(width), "native_stored_width_algorithm", evidence=evidence)


__all__ = ["resolve_stored_steps", "cpu_storage_mode", "allocation_dependent_operands",
           "context_cpu_mismatch"]
