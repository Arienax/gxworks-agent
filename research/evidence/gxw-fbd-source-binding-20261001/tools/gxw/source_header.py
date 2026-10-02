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
