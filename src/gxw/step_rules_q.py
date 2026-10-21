"""Q/L stored program widths for the shared step-width service.

The arithmetic lives here because the native encodings live here: a Q/L width is
a property of the stored header, which is only known once the operands are
encoded (device class, modifiers, D/W extension mapping, string payload length).
:mod:`plc.instruction_steps` delegates to this module for Q/L targets, so Core
queries, the GXW writer and the CSV writer all read one implementation.

Nothing here is CPU admission, native acceptance or execution evidence. The
``cpu`` argument is the saved CPU identity; ``context`` is the project allocation
:class:`gxw.token_q_encoder.QStorageContext` when it exists.

Macro placeholders (families ``0x78``/``0x79``) keep their stored width of 1.
That number is the placeholder's stored width, not the expanded macro body's
program capacity; this module never turns one into the other.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from .models import GXWFormatError
from .token_profiles import source_token_profile
from .token_q_encoder import encode_q_header, encode_q_operand


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


def cpu_storage_mode(cpu: Any) -> str | None:
    """Observed CPU storage group, independent of the lexical profile."""
    from .token_q_encoder import TABLES
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


def context_cpu_mismatch(context: Any, identity: str) -> str | None:
    """Reason when an allocation context was read for another CPU, else None.

    A merged menu option accepts any of its own members: one saved project cannot
    carry two allocations, and the context names the option it was read from.
    """
    context_cpu = str(getattr(context, "cpu", "") or "").strip().upper()
    if not context_cpu:
        return None
    accepted = {context_cpu}
    accepted.update(part.strip() for part in context_cpu.split("/") if part.strip())
    if identity in accepted:
        return None
    return (f"Q source storage context differs from the saved CPU: the allocation "
            f"was read for {context_cpu}, not {identity}")


#: Devices whose stored width depends on the saved allocation: D and W can be
#: mapped onto the ZR extension, which changes the encoded operand width.
_ALLOCATION_DEVICES = re.compile(r"^[DW]\d", re.IGNORECASE)


def allocation_dependent_operands(operands: Sequence[Any]) -> tuple[str, ...]:
    """Operands whose stored width depends on the saved D/W allocation."""
    dependent = []
    for value in operands or ():
        text = str(value).strip()
        if text.startswith('"'):
            # A string literal is payload, not a device reference.
            continue
        if _ALLOCATION_DEVICES.match(text):
            dependent.append(text)
    return tuple(dependent)


def resolve_stored_steps(opcode: Any, operands: Sequence[Any] = (), *, cpu: Any,
                         context: Any = None, text_encoding: str | None = None,
                         assume_default_allocation: bool = False):
    """Return the expected stored width for one Q/L instruction instance.

    ``context`` is the saved project allocation. Three states are distinguished:
    unknown (``None``) answers ``requires_context`` for a D/W operand instead of
    assuming an unextended allocation, an empty allocation answers from the
    ordinary ranges, and a parsed allocation answers from its own ranges.
    ``assume_default_allocation`` is the explicit opt-in for callers that have no
    saved project and want the unextended result; the evidence states that
    premise.
    """
    from plc.instruction_steps import (STATUS_KNOWN, STATUS_UNKNOWN_CPU, StepWidth,
                                      _known, _unresolved)

    identity = str(cpu or "").strip().upper()
    if not identity:
        return _unresolved(STATUS_UNKNOWN_CPU,
                           "A Q/L stored width needs the saved CPU identity",
                           requirements=("plc_model",))
    try:
        profile = source_token_profile(identity)
    except GXWFormatError as error:
        return _unresolved(STATUS_UNKNOWN_CPU, str(error))
    name = str(opcode or "").strip()
    if not name:
        return _unresolved("requires_operands", "Instruction opcode is missing",
                           requirements=("operands",))
    args = tuple(str(value).strip() for value in operands or ())
    mode = cpu_storage_mode(identity)
    if context is not None:
        mismatch = context_cpu_mismatch(context, identity)
        if mismatch is not None:
            return _unresolved("requires_context", mismatch,
                               requirements=("storage_context",))
    assumed_default = False
    if context is None:
        dependent = allocation_dependent_operands(args)
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
        header = encode_q_header(name, args, encoded, profile=profile, cpu=identity,
                                 storage_context=context)
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
    width = 1 if len(header) <= 3 else header[2]
    # An implicit one-word header carries no width byte; say which form answered.
    evidence = (f"native Q/L stored header {header.hex()} for {identity} "
                f"(mode {mode or 'common'}, profile {profile})",)
    if assumed_default:
        evidence = evidence + (f"assumed the default (unextended) allocation for "
                               f"{', '.join(allocation_dependent_operands(args))}; the saved "
                               "project allocation was not supplied",)
    return _known(int(width), "native_stored_width_algorithm", evidence=evidence)


__all__ = ["resolve_stored_steps", "cpu_storage_mode", "allocation_dependent_operands",
           "context_cpu_mismatch"]
