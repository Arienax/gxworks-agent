# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Encoded-operand view of a Q/L device and the rules that read it.

Device class, address, modifiers, module/network prefixes and named-call families are
described once here, in the encoded form, so the allocation classifier, the width
calculation and the writer cannot disagree about what one spelling addresses. The token
tables that decode encodings are shared; no second string classifier is maintained.

Deciding *about* storage never requires encoding *into* project bytes: the operand
encoder is supplied by the caller, so this module imports no project writer.
"""
from __future__ import annotations

import re

# Re-exported: the framing failure is one of the shared storage rule failures, so the
# project adapter's single conversion covers it.
from plc.storage.errors import StorageFramingError

__all__ = ["StorageFramingError", "ALLOCATION_DEVICE_CODES", "MODULE_PREFIX_CODES",
           "NAMED_CALL_FAMILIES", "NAMED_CALL", "PLACEHOLDER_FAMILIES",
           "allocation_dependent_operands", "encoded_tokens", "named_call_family"]




#: Encoded device codes whose stored width can follow the saved D/W allocation.
ALLOCATION_DEVICE_CODES = (0xa8, 0xb4)

#: Encoded prefix components that address another module or station rather than the
#: local device allocation: U..\, J..\, BL..\ and the extended ..\ prefix.
MODULE_PREFIX_CODES = (0xf8, 0xf9, 0xfc, 0xff)

#: Encoded family of a native named call, keyed by its letter prefix.
NAMED_CALL_FAMILIES = {"S": 0x70, "Z": 0x71, "G": 0x72, "J": 0x73, "D": 0x74,
                       "M": 0x78, "U": 0x79}
NAMED_CALL = re.compile(r"([SZGJDMU])(P?)\.([!-~]+)")

#: Named-call families the stored-width algorithm gives one placeholder word.
PLACEHOLDER_FAMILIES = (0x78, 0x79)


def encoded_tokens(raw):
    """Split one encoded operand into its framed components."""
    out = []
    at = 0
    while at < len(raw):
        n = raw[at]
        if n < 3 or at + n > len(raw) or raw[at + n - 1] != n:
            raise StorageFramingError("malformed token framing")
        out.append(raw[at:at + n])
        at += n
    return out


def named_call_family(name):
    """Encoded family number of one native named call, else None.

    Only the 0x78/0x79 pair (M./U.) is stored as a one-word placeholder whatever its
    unexpanded arguments name; S./Z./G./J./D. are ordinary applied instructions whose
    width follows the operands, the D/W allocation included. The stored-width algorithm
    and the allocation classifier ask this one function, so one mnemonic cannot be a
    placeholder in one place and an applied instruction in the other.
    """
    match = NAMED_CALL.fullmatch(str(name or "").strip().upper())
    return NAMED_CALL_FAMILIES[match[1]] if match is not None else None


def allocation_dependent_operands(operands, *, encode, op=None, profile="q03udv", cpu=None):
    r"""Operands whose stored width can follow the saved D/W device allocation.

    The decision is taken from the encoded operand rather than from its spelling, so
    every spelling of one address answers the same way: W is a hexadecimal device, so
    ``WA00`` and ``W0A00`` are the same local word, an ``@`` indirect D operand still
    reads the local allocation, an index or bit selector does not change what is
    addressed, and a module or network prefix (``U0\G0``, ``J1\W...``) addresses
    another unit and therefore never depends on the local mapping. Only the placeholder
    families (M./U., 0x78/0x79) store one word regardless of their arguments; the other
    named calls (S./Z./D. ...) keep the operand-dependent width their encoding has, so a
    D/W argument there does need the saved allocation.
    """
    name = str(op or "").strip().upper()
    if named_call_family(name) in PLACEHOLDER_FAMILIES:
        return ()
    dependent = []
    for index, value in enumerate(operands or ()):
        text = str(value).strip()
        if not text or text.startswith(('"', "'")):
            continue
        try:
            raw = encode(text, op=name, index=index, profile=profile, cpu=cpu)
        except ValueError:
            # An operand this encoder cannot parse yet is reported by the width path
            # itself; it is not evidence of an allocation dependency.
            continue
        try:
            tokens = encoded_tokens(raw)
        except StorageFramingError:
            continue
        if not tokens:
            continue
        if any(token[1] in MODULE_PREFIX_CODES for token in tokens[:-1]):
            continue
        if tokens[-1][1] in ALLOCATION_DEVICE_CODES:
            dependent.append(text)
    return tuple(dependent)
