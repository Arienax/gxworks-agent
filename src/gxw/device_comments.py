# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Lossless editing of the counted UTF-16 GX Works2 device-comment table.

Native project/CSV and typed comment-API controls establish stored device
identities, the range directory and the separate word-bit comment segment.
Other device codes and per-row metadata remain opaque. This
module handles an existing logical ``.qcd`` object, not its project container,
compile status or native acceptance. QCD strings are UTF-16LE independently of
the project's ANSI code page; generated CSV comment limits do not apply here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import re
import struct

from gxworks2.project_profiles import native_project_profile
from plc.target_support import normalize_target_cpu
from plc.validation import (device_address_radix, parse_device_address,
                            ValidationProfileUnavailableError)

from .models import GXWFormatError


_SIGNATURE = bytes.fromhex("1a0008010100000000000602000000000a0403000100000000001200")
_DEVICE_TYPES = {0x90: "M", 0x91: "SM", 0x92: "L", 0x93: "F", 0x94: "V",
                 0x98: "S", 0x99: "TR", 0x9C: "X", 0x9D: "Y", 0xA0: "B", 0xA1: "SB",
                 0xA8: "D", 0xA9: "SD", 0xAF: "R", 0xB0: "ZR", 0xB4: "W", 0xB5: "SW",
                 0xC2: "T", 0xC5: "C", 0xC8: "ST", 0xCC: "Z", 0xD0: "P", 0xD1: "I",
                 0xD2: "N", 0xD8: "U", 0xDC: "BL"}
_TYPE_CODES = {value: key for key, value in _DEVICE_TYPES.items()}
_SIMPLE_ADDRESS_RE = re.compile("(" + "|".join(sorted(_TYPE_CODES, key=lambda key: (-len(key), key))) + r")([0-9A-F]+)")
_MODULE_ADDRESS_RE = re.compile(r"U([0-9A-F]+)\\G([0-9]+)")
_NETWORK_ADDRESS_RE = re.compile(r"J([0-9]+)\\(B|W)([0-9A-F]+)")
_BLOCK_ADDRESS_RE = re.compile(r"BL([0-9]+)\\TR([0-9]+)")
_WORD_BIT_TYPES = frozenset({0xA8, 0xA9, 0xAF, 0xB0, 0xB4, 0xB5, 0xF8AB, 0xF9B4})


def _device_prefix(code: int, plc_model: str) -> str | None:
    profile = native_project_profile(plc_model)
    fx = profile is not None and profile.series[0] == "FXCPU"
    if code in (0x94, 0xCD):
        return "V" if code == (0xCD if fx else 0x94) else None
    return _DEVICE_TYPES.get(code)


def _device_code(prefix: str, plc_model: str) -> int:
    if prefix == "V":
        profile = native_project_profile(plc_model)
        if profile is not None and profile.series[0] == "FXCPU":
            return 0xCD
    return _TYPE_CODES[prefix]


def _radix(prefix: str, plc_model: str) -> int | None:
    # These are lexical spellings, independently of Core's often smaller
    # instruction/device validation subset. The native menu retains its exact
    # identity: it is never replaced by another CPU to obtain more coverage.
    # They match token_listing.fx_operand_text and token_q.q_operand_text.
    if prefix in {"B", "W", "SB", "SW", "U"}:
        return 16
    if prefix not in {"X", "Y"}:
        return 10
    profile = native_project_profile(plc_model)
    if profile is not None:
        if profile.series[0] == "FXCPU":
            return 8
        if profile.series[0].startswith("QCPU") or profile.series[0] == "LCPU":
            return 16
    try:
        return device_address_radix(prefix, plc_model)
    except ValueError:
        return None


def _address(device_code: int, index: int, plc_model: str, bit_index: int | None = None) -> str | None:
    if bit_index is not None and device_code not in _WORD_BIT_TYPES:
        return None
    if device_code == 0xF8AB:
        result = f"U{index >> 16:X}\\G{index & 0xFFFF}"
    elif device_code in (0xF9A0, 0xF9B4):
        result = f"J{index >> 16}\\{_DEVICE_TYPES[device_code & 0xFF]}{index & 0xFFFF:X}"
    elif device_code == 0xFC99:
        result = f"BL{index >> 16}\\TR{index & 0xFFFF}"
    else:
        prefix = _device_prefix(device_code, plc_model)
        if prefix is None:
            return None
        radix = _radix(prefix, plc_model)
        result = prefix + format(index, {8: "o", 10: "d", 16: "X"}[radix]) if radix in (8, 10, 16) else None
    return result + f".{bit_index:X}" if result is not None and bit_index is not None else result


def _identity(value: str, plc_model: str) -> tuple[int, int, int | None]:
    token = value.strip().upper() if isinstance(value, str) else ""
    bit_index = None
    if "." in token:
        token, bit = token.rsplit(".", 1)
        if not re.fullmatch(r"[0-9A-F]+", bit) or int(bit, 16) > 15:
            raise GXWFormatError(f"invalid QCD word-bit comment address: {value!r}")
        bit_index = int(bit, 16)
    module = _MODULE_ADDRESS_RE.fullmatch(token)
    network = _NETWORK_ADDRESS_RE.fullmatch(token)
    block = _BLOCK_ADDRESS_RE.fullmatch(token)
    if module is not None or network is not None or block is not None:
        group = module or network or block
        head = int(group[1], 16 if module is not None else 10)
        index = int(group[2], 10) if module is not None or block is not None else int(group[3], 16)
        if head > 0xFFFF or index > 0xFFFF:
            raise GXWFormatError(f"QCD composite address exceeds its stored integer: {value!r}")
        code = 0xF8AB if module is not None else (0xFC99 if block is not None else 0xF900 | _TYPE_CODES[group[2]])
        if bit_index is not None and code not in _WORD_BIT_TYPES:
            raise GXWFormatError(f"unverified QCD word-bit device type: {value!r}")
        return code, (head << 16) | index, bit_index
    match = _SIMPLE_ADDRESS_RE.fullmatch(token)
    if match is None:
        raise GXWFormatError(f"unverified QCD comment address: {value!r}")
    prefix, digits = match[1], match[2]
    radix = _radix(prefix, plc_model)
    if radix not in (8, 10, 16) or any(char not in "0123456789ABCDEF"[:radix] for char in digits):
        raise GXWFormatError(f"unverified QCD comment address: {value!r}")
    index = int(digits, radix)
    if index > 0xFFFFFFFF:
        raise GXWFormatError(f"QCD comment address exceeds its stored integer: {value!r}")
    code = _device_code(prefix, plc_model)
    if bit_index is not None and code not in _WORD_BIT_TYPES:
        raise GXWFormatError(f"unverified QCD word-bit device type: {value!r}")
    return code, index, bit_index


def _text_bytes(value: str) -> bytes:
    if not isinstance(value, str) or "\0" in value:
        raise GXWFormatError("QCD comment text must be a string without NUL")
    try:
        result = (value + "\0").encode("utf-16le")
    except UnicodeError as exc:
        raise GXWFormatError("invalid QCD comment UTF-16") from exc
    if len(result) // 2 > 0xFFFFFFFF:
        raise GXWFormatError("QCD comment string is too large")
    return result


def _validate_new_address(address: str, identity: tuple[int, int, int | None], plc_model: str) -> None:
    """Validate annotation storage without inventing hardware address coverage."""
    prefix = _device_prefix(identity[0], plc_model)
    if prefix is None:
        profile = native_project_profile(plc_model)
        if profile is None or not profile.program_supported:
            raise GXWFormatError("new QCD comment requires a known programmable CPU")
        return
    try:
        validated = parse_device_address(_address(identity[0], identity[1], plc_model), plc_model)
    except ValidationProfileUnavailableError:
        profile = native_project_profile(plc_model)
        if profile is None or not profile.program_supported:
            raise GXWFormatError("new QCD comment requires a known programmable CPU")
        # The format identity remains meaningful when Core has no device
        # profile. This does not extend PLC address limits or device access.
        return
    except ValueError:
        validated = None
    if validated is None:
        from plc.device_profiles import DEVICE_LIMITS
        if prefix not in DEVICE_LIMITS.get(normalize_target_cpu(plc_model), {}):
            profile = native_project_profile(plc_model)
            if profile is None or not profile.program_supported:
                raise GXWFormatError("new QCD comment requires a known programmable CPU")
            return
    if validated != (prefix, identity[1]):
        raise GXWFormatError(f"new QCD comment address has no current Core validation coverage: {address}")


@dataclass(frozen=True)
class DeviceCommentRow:
    device_code: int
    index: int
    address: str | None
    text: str
    metadata: bytes = field(repr=False)
    raw: bytes = field(repr=False)
    offset: int = 0
    bit_index: int | None = None

    @property
    def identity(self) -> tuple[int, int, int | None]:
        return self.device_code, self.index, self.bit_index

    @property
    def editable(self) -> bool:
        return self.address is not None and self.metadata == (b"" if self.bit_index is not None else b"\0" * 4)


@dataclass(frozen=True)
class DeviceCommentRange:
    device_code: int
    first_index: int
    rows: tuple[DeviceCommentRow, ...]


@dataclass(frozen=True)
class DeviceCommentDocument:
    logical_name: str
    plc_model: str
    ranges: tuple[DeviceCommentRange, ...]
    header: bytes = field(repr=False)
    trailer: bytes = field(repr=False)
    raw: bytes = field(repr=False)
    extensions: bytes = field(default=b"", repr=False)
    bit_rows: tuple[DeviceCommentRow, ...] = ()
    bit_extension_supported: bool = False

    @property
    def rows(self) -> tuple[DeviceCommentRow, ...]:
        return tuple(row for group in self.ranges for row in group.rows) + self.bit_rows

    @property
    def comments(self) -> dict[str, str]:
        """Known address spellings; unknown device rows stay in ``rows``."""
        return {row.address: row.text for row in self.rows if row.address is not None}

    @property
    def address_validation(self) -> str:
        """Whether Core currently covers every exposed annotation address."""
        from plc.validation import normalize_plc_model
        try:
            normalize_plc_model(self.plc_model)
            for row in self.rows:
                prefix = _device_prefix(row.device_code, self.plc_model)
                if prefix is None or row.address is None:
                    return "unverified"
                if parse_device_address(row.address, self.plc_model) != (prefix, row.index):
                    return "unverified"
        except ValueError:
            return "unverified"
        return "available"

    @property
    def write_blockers(self) -> tuple[str, ...]:
        blockers = []
        if self.trailer:
            blockers.append("unverified_qcd_trailer")
        if self.extensions and not self.bit_extension_supported:
            blockers.append("unverified_qcd_extension")
        if self.header[44:50] != bytes.fromhex("010000000000") or self.header[54:56] not in (b"\0\0", b"\0\xff"):
            blockers.append("unverified_qcd_header_options")
        return tuple(blockers)


def _parse_bit_rows(raw: bytes, start: int, plc_model: str) -> tuple[DeviceCommentRow, ...]:
    """Read the independently counted native word-bit comment segment."""
    if len(raw) - start < 12:
        raise GXWFormatError("truncated QCD word-bit comment segment")
    size, flags, version, groups = struct.unpack_from("<IIHH", raw, start)
    if size != len(raw) - start or flags or version != 1:
        raise GXWFormatError("unverified QCD word-bit comment segment")
    offset, rows, seen = start + 12, [], set()
    for _ in range(groups):
        if len(raw) - offset < 8:
            raise GXWFormatError("truncated QCD word-bit device group")
        code, words = struct.unpack_from("<II", raw, offset)
        offset += 8
        if not words or words > (len(raw) - offset) // 6:
            raise GXWFormatError("invalid QCD word-bit device count")
        for _ in range(words):
            if len(raw) - offset < 6:
                raise GXWFormatError("truncated QCD word-bit device identity")
            index, count = struct.unpack_from("<IH", raw, offset)
            offset += 6
            if not 1 <= count <= 16:
                raise GXWFormatError("invalid QCD word-bit comment count")
            for _ in range(count):
                first = offset
                if len(raw) - offset < 4:
                    raise GXWFormatError("truncated QCD word-bit comment string")
                bit, chars = struct.unpack_from("<HH", raw, offset)
                offset += 4 + chars * 2
                identity = code, index, bit
                if bit > 15 or not chars or offset > len(raw) or raw[offset - 2:offset] != b"\0\0":
                    raise GXWFormatError("unbounded QCD word-bit comment string")
                if identity in seen:
                    raise GXWFormatError("duplicate QCD word-bit comment identity")
                seen.add(identity)
                try:
                    text = raw[first + 4:offset - 2].decode("utf-16le")
                except UnicodeError as exc:
                    raise GXWFormatError("invalid QCD word-bit comment UTF-16") from exc
                if "\0" in text:
                    raise GXWFormatError("embedded NUL in QCD word-bit comment")
                rows.append(DeviceCommentRow(code, index, _address(code, index, plc_model, bit),
                                              text, b"", raw[first:offset], first, bit))
    if offset != len(raw):
        raise GXWFormatError("unverified QCD word-bit comment remainder")
    return tuple(rows)


def parse_device_comments(raw: bytes, *, logical_name: str = "COMMENT.qcd",
                          plc_model: str = "FX3U") -> DeviceCommentDocument:
    """Parse the bounded range/UTF-16 layout without guessing unknown codes."""
    if not isinstance(raw, bytes):
        raise GXWFormatError("QCD source must be immutable bytes")
    model = normalize_target_cpu(plc_model)
    if len(raw) < 60 or raw[:28] != _SIGNATURE:
        raise GXWFormatError("unverified QCD device-comment envelope")
    primary_end = 50 + struct.unpack_from("<I", raw, 50)[0]
    if not 60 <= primary_end <= len(raw) or raw[56:58] != b"\x20\x0a":
        raise GXWFormatError("invalid QCD device-comment envelope length or marker")
    count = struct.unpack_from("<H", raw, 58)[0]
    table_end = 60 + count * 10
    if table_end > primary_end:
        raise GXWFormatError("truncated QCD device range directory")
    directory = [struct.unpack_from("<HII", raw, 60 + number * 10) for number in range(count)]
    total = sum(size for _, _, size in directory)
    if any(size == 0 or first + size > 0x100000000 for _, first, size in directory):
        raise GXWFormatError("invalid QCD device range bounds")
    if total > (primary_end - table_end) // 10:
        raise GXWFormatError("truncated QCD device-comment rows")
    offset, groups, seen = table_end, [], set()
    for device_code, first, size in directory:
        rows = []
        for index in range(first, first + size):
            identity = (device_code, index)
            if identity in seen:
                raise GXWFormatError("overlapping QCD device comment ranges")
            seen.add(identity)
            if primary_end - offset < 8:
                raise GXWFormatError("truncated QCD device-comment string count")
            chars = struct.unpack_from("<I", raw, offset + 4)[0]
            end = offset + 8 + chars * 2
            if not chars or end > primary_end or raw[end - 2:end] != b"\0\0":
                raise GXWFormatError("unbounded QCD device-comment string")
            try:
                text = raw[offset + 8:end - 2].decode("utf-16le")
            except UnicodeError as exc:
                raise GXWFormatError("invalid QCD comment UTF-16") from exc
            if "\0" in text:
                raise GXWFormatError("embedded NUL in QCD comment")
            rows.append(DeviceCommentRow(device_code, index, _address(device_code, index, model),
                                         text, raw[offset:offset + 4], raw[offset:end], offset))
            offset = end
        groups.append(DeviceCommentRange(device_code, first, tuple(rows)))
    extensions, bit_rows, bit_supported = raw[primary_end:], (), True
    if extensions:
        try:
            bit_rows = _parse_bit_rows(raw, primary_end, model)
        except GXWFormatError:
            # An unknown independent segment must not hide the ordinary table.
            # Its exact bytes remain bound; edits need a proved segment layout.
            bit_supported = False
    return DeviceCommentDocument(logical_name, model, tuple(groups), raw[:60], raw[offset:primary_end], raw,
                                 extensions, bit_rows, bit_supported)


def _runs(rows: list[DeviceCommentRow]) -> tuple[DeviceCommentRange, ...]:
    groups, current = [], []
    for row in sorted(rows, key=lambda item: item.index):
        if current and row.index != current[-1].index + 1:
            groups.append(DeviceCommentRange(current[0].device_code, current[0].index, tuple(current)))
            current = []
        current.append(row)
    if current:
        groups.append(DeviceCommentRange(current[0].device_code, current[0].index, tuple(current)))
    return tuple(groups)


def edit_device_comments(document: DeviceCommentDocument, updates: Mapping[str, str | None], *,
                         expected: Mapping[str, str | None] | None = None) -> DeviceCommentDocument:
    """Edit an existing table. ``None`` deletes a row; ``''`` keeps an empty row.

    Existing rows bind to their stored identity even outside Core's current
    address subset. New addresses use the Core CPU validator when available.
    A missing CPU profile leaves annotation address validity unverified; the
    bounded QCD identity can still be stored without granting device access.
    ``expected`` binds changes to the inspected values;
    an expected ``None`` means the address must be absent. No file is written.
    """
    if not isinstance(updates, Mapping) or (expected is not None and not isinstance(expected, Mapping)):
        raise GXWFormatError("QCD comment updates and expected values must be mappings")
    source = parse_device_comments(document.raw, logical_name=document.logical_name, plc_model=document.plc_model)
    original = {row.identity: row for row in source.rows}
    current = {row.identity: row for row in document.rows}
    if expected is not None:
        checked = set()
        for address, text in expected.items():
            identity = _identity(address, document.plc_model)
            if identity in checked or (text is not None and not isinstance(text, str)):
                raise GXWFormatError("ambiguous QCD comment source expectation")
            checked.add(identity)
            observed = current.get(identity)
            if (observed.text if observed is not None else None) != text:
                raise GXWFormatError(f"stale QCD comment source: {address}")
    normalized = {}
    for address, text in updates.items():
        identity = _identity(address, document.plc_model)
        if identity in normalized:
            raise GXWFormatError(f"duplicate QCD comment address aliases: {address}")
        if text is not None:
            _text_bytes(text)
            if identity not in current and identity not in original:
                _validate_new_address(address, identity, document.plc_model)
        normalized[identity] = text
    changed_types = set()
    for identity, text in normalized.items():
        previous = current.get(identity)
        if previous is None and text is None or previous is not None and previous.text == text:
            continue
        if document.write_blockers:
            raise GXWFormatError("QCD comment editing unavailable: " + ", ".join(document.write_blockers))
        if previous is not None and not previous.editable:
            raise GXWFormatError(f"unverified QCD row metadata: {previous.address}")
        code, index, bit = identity
        if text is None:
            del current[identity]
            if bit is None:
                changed_types.add(code)
        elif previous is None:
            if identity in original:
                current[identity] = replace(original[identity], text=text)
            else:
                current[identity] = DeviceCommentRow(code, index, _address(code, index, document.plc_model, bit),
                                                     text, b"\0" * 4 if bit is None else b"", b"", bit_index=bit)
            if bit is None:
                changed_types.add(code)
        else:
            current[identity] = replace(previous, text=text)
    groups, emitted = [], set()
    for group in document.ranges:
        code = group.device_code
        if code in changed_types:
            if code not in emitted:
                groups.extend(_runs([row for (kind, _, bit), row in current.items() if kind == code and bit is None]))
                emitted.add(code)
        else:
            groups.append(replace(group, rows=tuple(current[(code, row.index, None)] for row in group.rows)))
    for code in sorted(changed_types - emitted):
        groups.extend(_runs([row for (kind, _, bit), row in current.items() if kind == code and bit is None]))
    retained_bits = [current[row.identity] for row in document.bit_rows if row.identity in current]
    previous_bits = {row.identity for row in document.bit_rows}
    retained_bits.extend(row for identity, row in sorted(current.items(), key=lambda item: item[0][:2] + (item[0][2] or 0,))
                         if identity[2] is not None and identity not in previous_bits)
    bit_groups = {}
    for row in retained_bits:
        bit_groups.setdefault(row.device_code, {}).setdefault(row.index, []).append(row)
    ordered_bits = tuple(row for words in bit_groups.values() for rows in words.values() for row in rows)
    result = replace(document, ranges=tuple(groups), bit_rows=ordered_bits)
    if current == original:
        result = replace(document, ranges=source.ranges, bit_rows=source.bit_rows)
    # Validate the whole transaction before returning a candidate to its caller.
    serialize_device_comments(result)
    return result


def _serialize_bit_rows(document: DeviceCommentDocument, source: DeviceCommentDocument) -> bytes:
    if document.bit_rows == source.bit_rows:
        return source.extensions
    original = {row.identity: row for row in source.bit_rows}
    seen, groups = set(), {}
    for row in document.bit_rows:
        identity = row.identity
        if (row.bit_index is None or not 0 <= row.bit_index <= 15 or identity in seen
                or row.address != _address(row.device_code, row.index, document.plc_model, row.bit_index)):
            raise GXWFormatError("invalid or duplicate QCD word-bit comment identity")
        seen.add(identity)
        previous = original.get(identity)
        if previous is not None:
            if (row.raw, row.metadata, row.offset) != (previous.raw, previous.metadata, previous.offset):
                raise GXWFormatError("QCD word-bit row is not bound to its original bytes")
            if row.text == previous.text:
                encoded = previous.raw
            else:
                encoded = None
        else:
            encoded = None
        if encoded is None:
            if not row.editable or previous is None and row.raw:
                raise GXWFormatError("unverified QCD word-bit device type or row metadata")
            if previous is None:
                _validate_new_address(row.address, identity, document.plc_model)
            text = _text_bytes(row.text)
            if len(text) // 2 > 0xFFFF:
                raise GXWFormatError("QCD word-bit comment exceeds its stored string count")
            encoded = struct.pack("<HH", row.bit_index, len(text) // 2) + text
        groups.setdefault(row.device_code, {}).setdefault(row.index, []).append(encoded)
    if any(identity not in seen and not row.editable for identity, row in original.items()):
        raise GXWFormatError("cannot remove an opaque QCD word-bit comment row")
    if not groups:
        return b""
    if len(groups) > 0xFFFF:
        raise GXWFormatError("too many QCD word-bit device groups")
    pieces = [struct.pack("<IIHH", 0, 0, 1, len(groups))]
    for code, words in groups.items():
        pieces.append(struct.pack("<II", code, len(words)))
        for index, entries in words.items():
            pieces.append(struct.pack("<IH", index, len(entries)))
            pieces.extend(entries)
    result = b"".join(pieces)
    if len(result) > 0xFFFFFFFF:
        raise GXWFormatError("QCD word-bit comment segment is too large")
    return struct.pack("<I", len(result)) + result[4:]


def serialize_device_comments(document: DeviceCommentDocument) -> bytes:
    """Preserve all original bytes except requested rows, directory and size."""
    source = parse_device_comments(document.raw, logical_name=document.logical_name, plc_model=document.plc_model)
    if (document.header, document.trailer, document.extensions, document.bit_extension_supported) != (
            source.header, source.trailer, source.extensions, source.bit_extension_supported):
        raise GXWFormatError("QCD source envelope is not bound to its original bytes")
    if document.ranges == source.ranges and document.bit_rows == source.bit_rows:
        return document.raw
    if document.write_blockers:
        raise GXWFormatError("QCD comment editing unavailable: " + ", ".join(document.write_blockers))
    original = {row.identity: row for group in source.ranges for row in group.rows}
    directory, payload, seen = [], [], set()
    if len(document.ranges) > 0xFFFF:
        raise GXWFormatError("too many QCD device comment ranges")
    for group in document.ranges:
        if not group.rows or group.first_index + len(group.rows) > 0x100000000:
            raise GXWFormatError("invalid QCD device range bounds")
        directory.append(struct.pack("<HII", group.device_code, group.first_index, len(group.rows)))
        for position, row in enumerate(group.rows):
            identity = row.identity
            if (row.device_code != group.device_code or row.index != group.first_index + position
                    or row.bit_index is not None or identity in seen
                    or row.address != _address(row.device_code, row.index, document.plc_model)):
                raise GXWFormatError("invalid or duplicate QCD device comment row identity")
            seen.add(identity)
            previous = original.get(identity)
            if previous is not None:
                if (row.raw, row.metadata, row.offset) != (previous.raw, previous.metadata, previous.offset):
                    raise GXWFormatError("QCD row is not bound to its original bytes")
                if row.text == previous.text:
                    payload.append(previous.raw)
                    continue
            if not row.editable or previous is None and row.raw:
                raise GXWFormatError("unverified QCD device type or row metadata")
            if previous is None:
                _validate_new_address(row.address, identity, document.plc_model)
            encoded = _text_bytes(row.text)
            payload.append(row.metadata + struct.pack("<I", len(encoded) // 2) + encoded)
    # A direct dataclass replacement may not silently delete opaque source rows.
    if any(identity not in seen and not row.editable for identity, row in original.items()):
        raise GXWFormatError("cannot remove an opaque QCD device comment row")
    body = b"".join(directory + payload) + document.trailer
    header = bytearray(document.header)
    # Independent native short-text Save of an original legacy table clears
    # this option. Exact no-op above preserves its original 00ff bytes.
    header[54:56] = b"\0\0"
    if len(body) + 10 > 0xFFFFFFFF:
        raise GXWFormatError("QCD device comment table is too large")
    struct.pack_into("<I", header, 50, len(body) + 10)
    struct.pack_into("<H", header, 58, len(document.ranges))
    raw = bytes(header) + body + _serialize_bit_rows(document, source)
    reread = parse_device_comments(raw, logical_name=document.logical_name, plc_model=document.plc_model)
    if [(row.identity, row.text, row.metadata) for row in reread.rows] != [
            (row.identity, row.text, row.metadata) for row in document.rows]:
        raise GXWFormatError("QCD device comment readback differs from requested rows")
    return raw
