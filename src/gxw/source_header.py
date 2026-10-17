"""Bounded common source prefix; descriptive fields and flags remain raw.

Native project and library source objects start with two counted UTF-16 strings
and 42 further bytes. Nonempty descriptions move the language payload; locating
it by searching for a familiar byte sequence would lose that source boundary.
"""
from __future__ import annotations

import struct

from .models import GXWFormatError


def source_payload_offset(raw: bytes) -> int:
    offset = 0
    for _ in range(2):
        if offset + 4 > len(raw):
            raise GXWFormatError("truncated source header string count")
        count = struct.unpack_from("<I", raw, offset)[0]
        end = offset + 4 + 2 * count
        if count < 1 or end > len(raw) or raw[end - 2:end] != b"\0\0":
            raise GXWFormatError("unbounded source header string")
        offset = end
    if offset + 42 >= len(raw):
        raise GXWFormatError("truncated common source header")
    return offset + 42


def normalize_visible_save_header(before: bytes, after: bytes, *, cpu: str):
    """Bound the common save metadata observed in native FX3U task/FBD edits.

    The unchanged header passes through. Changed headers must have the exact
    observed 54-byte layout: a fixed 34-byte prefix, valid SYSTEMTIME fields and
    compile-pending 0/1 -> 1. No payload, description or other header bytes are
    normalized. The returned observation describes metadata, not compilation.
    """
    from datetime import datetime
    a, b = bytes(before), bytes(after)
    if a == b:
        return b, None
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    if (cpu != 'FX3U/FX3UC' or len(a) != 54 or len(b) != 54
            or a[:34] != prefix or b[:34] != prefix
            or int.from_bytes(a[50:54], 'little') not in (0, 1)
            or int.from_bytes(b[50:54], 'little') != 1):
        raise GXWFormatError('visible save changed an unverified native header')

    def stamp(raw):
        values = list(struct.unpack('<8H', raw[34:50]))
        year, month, weekday, day, hour, minute, second, millis = values
        try:
            value = datetime(year, month, day, hour, minute, second, millis * 1000)
        except ValueError as error:
            raise GXWFormatError('visible save has an invalid native save timestamp') from error
        if weekday != (value.weekday() + 1) % 7:
            raise GXWFormatError('visible save has an invalid native save weekday')
        return values

    observed = dict(status='native_header_metadata_inference', timestamp_before=stamp(a), timestamp_after=stamp(b),
                    compile_pending_before=int.from_bytes(a[50:54], 'little'), compile_pending_after=1)
    return a, observed
