"""Observed FX operand storage shared by validation and token writing.

Stored literal values and reference bounds do not prove project acceptance,
module presence, configured allocation or effective indexed addresses.
"""
from __future__ import annotations

from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
import math
import re
import struct

from plc.storage.errors import StorageRuleError


def real_literal_operand(text, *, token_code=None):
    """Use the existing FX native input rounding for one binary32 constant.

    This formats a value; the caller still owns parameter-specific admission.
    FX2 15.31 RVAs 0x2b3d0/0x2b9f8 convert strtod -> seven-significant-digit
    text -> binary32; this is the former token-writer formatter unchanged.
    """
    from plc.ordered_source import native_real_literal
    match = native_real_literal(text)
    if match is None:
        raise StorageRuleError('unsupported floating-point literal spelling')
    code = token_code if token_code in (0xec, 0xed) else 0xed if match[2] else 0xec
    try:
        value = float(match[1] + ('e' + match[2] if match[2] else ''))
        if not math.isfinite(value):
            raise StorageRuleError('non-finite floating-point operand')
        exact_input = Decimal(match[1]).scaleb(int(match[2] or '0'))
        if value == 0 and exact_input:
            raise StorageRuleError('floating-point operand underflows binary32')
        minimum, maximum = ((1e-10, 999999900000.0) if code == 0xec else
                            (1.175495e-38, 3.402823e38))
        if value and not minimum <= abs(value) <= maximum:
            raise StorageRuleError('float is outside the stored notation range')
        with localcontext() as context:
            context.prec = 100
            exact = Decimal.from_float(value)
            rounded = exact.quantize(Decimal(1).scaleb(exact.adjusted() - 6),
                                     rounding=ROUND_HALF_UP) if value else Decimal(0)
        payload = struct.pack('<f', float(rounded))
    except (OverflowError, ValueError, DecimalException) as exc:
        raise StorageRuleError('float is outside the observed binary32 range') from exc
    stored = struct.unpack('<f', payload)[0]
    if not math.isfinite(stored):
        raise StorageRuleError('non-finite floating-point operand')
    if stored == 0 and Decimal(match[1]):
        raise StorageRuleError('floating-point operand underflows binary32')
    return bytes((7, code)) + payload + bytes((7,))


def real_literal_value(text):
    """Read the same stored FX value used by the token writer."""
    return struct.unpack('<f', real_literal_operand(text)[2:-1])[0]


def call_indexed_pointer_operand(value):
    """Parse the FX CALL target identity, never a data-memory operand.

    CPU520 converter witnesses cover P0Z0/P64V7 for CALL and CALLP.
    Effective indexed targets and callee binding remain caller obligations.
    """
    match = re.fullmatch(r'P([0-9]+)([ZV])([0-7])', value)
    if match is None:
        return None
    number, index, index_number = match.groups()
    number = int(number)
    if number == 63 or number > 4095:
        raise StorageRuleError('CALL pointer base is outside the documented FX3U pointer intervals')
    return {'pointer_number': number, 'index': index, 'index_number': int(index_number)}


def buffer_memory_operand(value, *, point_count=1):
    """Read one FX U/G word reference without granting instruction admission."""
    if not isinstance(value, str) or '\\' not in value:
        return None
    module = re.fullmatch(r'U([0-7])\\G([0-9]+)(?:([ZV])([0-7]))?', value)
    if module is None:
        raise StorageRuleError('FX buffer memory requires U0..U7\\Gnumber with an optional buffer V/Z index')
    unit, number, index, index_number = module.groups()
    address = int(number)
    if address > 32767:
        raise StorageRuleError('FX buffer-memory source address exceeds its observed 15-bit range')
    if type(point_count) is not int or point_count < 1:
        raise StorageRuleError('FX buffer-memory operand needs a positive word extent')
    if address + point_count - 1 > 32767:
        raise StorageRuleError('FX buffer-memory operand extent exceeds its observed 15-bit range')
    return {'unit': int(unit), 'address': address, 'index': index,
            'index_number': int(index_number) if index else None}
