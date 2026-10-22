"""GX Works2 Q/L project-token adapter.

The storage rules, operand encodings, header construction and stored widths live in
:mod:`plc.storage.q_encoder`. This module keeps what belongs to a GX Works2 project:
reading the bounded ``Param.wpa`` allocation record, and reporting the shared
computation error as the project format error existing callers expect.
"""
from __future__ import annotations

from functools import lru_cache
import json
import re
import struct
import sys

from plc.storage import q_encoder as _shared
from plc.storage.context import QStorageContext
from plc.storage.errors import StorageRuleError
from plc.storage.q_encoder import TABLES, _context_operand as _shared_context_operand

from .models import GXWFormatError

TABLES = _shared.TABLES

__all__ = ["QStorageContext", "TABLES", "encode_q_header", "encode_q_operand",
           "q_header_compatible", "q_project_storage_context", "q_storage_context"]


def encode_q_operand(*args, **kwargs):
    """Encode one operand, reporting a shared rule failure as a format error."""
    try:
        return _shared.encode_q_operand(*args, **kwargs)
    except StorageRuleError as error:
        raise GXWFormatError(str(error)) from None


def encode_q_header(*args, **kwargs):
    """Build one stored header, reporting a shared rule failure as a format error."""
    try:
        return _shared.encode_q_header(*args, **kwargs)
    except StorageRuleError as error:
        raise GXWFormatError(str(error)) from None


def q_header_compatible(*args, **kwargs):
    """Answer a compatibility question about one header."""
    try:
        return _shared.q_header_compatible(*args, **kwargs)
    except StorageRuleError as error:
        raise GXWFormatError(str(error)) from None


def _context_operand(context, device, value):
    """One context operand, reported as a project format error."""
    try:
        return _shared_context_operand(context, device, value)
    except StorageRuleError as error:
        raise GXWFormatError(str(error)) from None


def __getattr__(name):
    """Every other shared name stays reachable under its historical spelling."""
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_shared, name)


def q_storage_context(parameter_record: bytes, *, cpu: str) -> QStorageContext:
    """Read the bounded 2000 record used by the Q/L source encoder.

    The shared initializer has precisely two extension slots, D then W.
    Banks 0/1 give the ordinary capacity, 2 gives additional words and 3
    gives additional 1024-word blocks. ZR's bank 3 supplies their base.
    Other parameter rows are retained and cannot create extension slots.
    """
    identity = str(cpu).strip().upper()
    if identity not in TABLES['cpu_modes']:
        raise GXWFormatError('Q storage context needs the exact supported saved CPU')
    raw = bytes(parameter_record)
    if (not 6 <= len(raw) <= 4096 or struct.unpack_from('<HH', raw) != (len(raw), 0x2000)
            or len(raw) != 6 + 4 * struct.unpack_from('<H', raw, 4)[0]):
        raise GXWFormatError('unsupported Q device-allocation parameter record')
    ordinary = {0xa8: 0, 0xb4: 0}
    extended = {0xa8: 0, 0xb4: 0}
    seen = set()
    cursor = 0
    for offset in range(6, len(raw), 4):
        kind, value = struct.unpack_from('<HH', raw, offset)
        device, bank = kind & 255, kind >> 8
        if kind == 0x3b0:
            key = ('ZR',)
            cursor = value << 10
        elif device in ordinary:
            if bank > 3:
                # The native 2D375 initializer explicitly skips other banks.
                # Keep their bytes in parameter_record without making them a
                # new ordinary/extended range. Independent controls retain
                # bank 4 and 255 rows and produce identical known widths.
                continue
            key = (device, 'ordinary' if bank < 2 else 'extended')
            (ordinary if bank < 2 else extended)[device] = value << (10 if bank == 3 else 0)
        else:
            continue
        if key in seen:
            raise GXWFormatError('ambiguous duplicate Q allocation parameter row')
        seen.add(key)
    ranges = []
    for device in (0xa8, 0xb4):
        count = extended[device]
        if count:
            ranges.append((device, ordinary[device], count, cursor))
        cursor += count
    limits = tuple((device, ordinary[device] + extended[device]) for device in (0xa8, 0xb4))
    return QStorageContext(identity, raw, tuple(ranges), device_limits=limits)


def q_project_storage_context(parameter_stream: bytes | None, *, cpu: str) -> QStorageContext:
    """Read one complete Param.wpa chain; never search beyond malformed data.

    Missing/unimplemented input returns an unavailable context so the writer
    can still edit instructions independent of D/W extension allocation.
    """
    identity = str(cpu).strip().upper()
    if parameter_stream is None:
        return QStorageContext(identity, None, None, 'project Param.wpa is missing or ambiguous')
    raw, offset, records = bytes(parameter_stream), 0, []
    try:
        while offset + 2 <= len(raw):
            length, = struct.unpack_from('<H', raw, offset)
            if length == 0xffff:
                if offset + 2 != len(raw):
                    raise GXWFormatError('bytes after terminal parameter marker')
                if len(records) != 1:
                    raise GXWFormatError('expected one Q device-allocation parameter record')
                return q_storage_context(records[0], cpu=identity)
            if length < 4 or offset + length > len(raw):
                raise GXWFormatError('invalid project parameter record framing')
            if struct.unpack_from('<H', raw, offset + 2)[0] == 0x2000:
                records.append(raw[offset:offset + length])
            offset += length
        raise GXWFormatError('missing terminal project parameter marker')
    except GXWFormatError as error:
        return QStorageContext(identity, records[0] if len(records) == 1 else None, None, str(error))
