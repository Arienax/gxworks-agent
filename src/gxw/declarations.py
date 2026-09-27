"""Lossless local/global GX Works2 label tables and declaration edits.

The native 2026-09-10 CSV controls establish the scalar/array/type-reference
layout. Header/trailer and uninterpreted row fields are retained, not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
import struct

from .models import GXWFormatError
from .source_header import source_payload_offset


# Native saved basic-type codes, not PLC instruction opcodes.
BASIC_TYPES = {"BOOL": (1, "FALSE"), "INT": (2, "0"), "DINT": (3, "0"),
               "WORD": (4, "0"), "DWORD": (5, "0"), "REAL": (6, "0.0"),
               "TIME": (7, "T#0s"), "STRING": (8, "''")}
CLASS_CODES = {"VAR": 1, "VAR_CONSTANT": 2, "VAR_GLOBAL": 8, "VAR_GLOBAL_CONSTANT": 9}


class _Reader:
    def __init__(self, raw, offset=0):
        self.raw, self.offset = raw, offset

    def take(self, length):
        if length < 0 or length > len(self.raw) - self.offset:
            raise GXWFormatError("truncated GXW declaration table")
        result = self.raw[self.offset:self.offset + length]
        self.offset += length
        return result

    def uint(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self):
        count = self.uint()
        if not count:
            raise GXWFormatError("missing declaration string terminator")
        raw = self.take(count * 2)
        if raw[-2:] != b"\0\0":
            raise GXWFormatError("unterminated declaration string")
        try:
            result = raw[:-2].decode("utf-16le")
        except UnicodeError as exc:
            raise GXWFormatError("invalid declaration UTF-16") from exc
        if "\0" in result:
            raise GXWFormatError("embedded NUL in declaration string")
        return result


def _string(value):
    if not isinstance(value, str) or "\0" in value:
        raise GXWFormatError("invalid declaration string")
    raw = (value + "\0").encode("utf-16le")
    return struct.pack("<I", len(raw) // 2) + raw


@dataclass(frozen=True)
class LabelRecord:
    name: str
    data_type: str
    class_code: int
    device: str
    iec_address: str
    unknown_u32: int
    initial_value: str
    unknown_text: str
    record_id: int
    comment: str
    array_marker: int
    type_code: int
    type_reference: str
    offset: int = 0
    raw: bytes = field(default=b"", repr=False)
    # Structure members / structure-array address storage precedes the two
    # value strings. Keep it verbatim; scalar device fields do not describe it.
    value_extension: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class DeclarationDocument:
    logical_name: str
    scope: str
    owner_name: str | None
    count_offset: int
    rows: tuple[LabelRecord, ...]
    header: bytes = field(repr=False)
    trailer: bytes = field(repr=False)
    raw: bytes = field(repr=False)
    owner_return_type: str | None = None


@dataclass(frozen=True)
class StructureMember:
    name: str
    data_type: str
    initial_value: str
    unknown_text: str
    record_id: int
    comment: str
    array_marker: int
    type_code: int
    type_reference: str
    offset: int
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class StructureDocument:
    logical_name: str
    members: tuple[StructureMember, ...]
    header: bytes = field(repr=False)
    trailer: bytes = field(repr=False)
    raw: bytes = field(repr=False)

    def reconstruct(self) -> bytes:
        return self.raw


def parse_structure_declarations(raw: bytes, *, logical_name: str) -> StructureDocument:
    """Read observed library type members; this does not assign PLC addresses."""
    if not logical_name.endswith(".lns"):
        raise GXWFormatError("not an observed library structure source")
    reader = _Reader(raw, source_payload_offset(raw))
    count = reader.uint()
    header = raw[:reader.offset]
    if count > (len(raw) - reader.offset) // 48:
        raise GXWFormatError("structure member count cannot fit the stream")
    members = []
    for _ in range(count):
        start = reader.offset
        values = [reader.string(), reader.string(), reader.string(), reader.string(), reader.uint(),
                  reader.string(), reader.uint(), reader.uint(), reader.string()]
        members.append(StructureMember(*values, offset=start, raw=raw[start:reader.offset]))
    return StructureDocument(logical_name, tuple(members), header, raw[reader.offset:], raw)


def parse_declarations(raw: bytes, *, logical_name: str) -> DeclarationDocument:
    if logical_name.endswith((".Labels.lh", ".lnl")):
        scope = "local"
    elif logical_name.endswith((".gh", ".lng")):
        scope = "global"
    else:
        raise GXWFormatError("not a local/global declaration stream")
    reader = _Reader(raw)
    # Common native header; the timestamp/status bytes remain opaque.
    reader.take(source_payload_offset(raw))
    owner = reader.string() if scope == "local" else None
    return_type = None
    if scope == "local":
        reader.take(8)
        reader.string()  # Retained in header; no meaning assigned.
        return_type = reader.string() or None
    count_offset = reader.offset
    count = reader.uint()
    header = raw[:reader.offset]
    # Each row has eight counted, terminated strings and five uint32 values.
    minimum_row_size = 8 * (4 + 2) + 5 * 4
    if count > (len(raw) - reader.offset) // minimum_row_size:
        raise GXWFormatError("declaration count cannot fit the stream")
    rows = []
    for _ in range(count):
        start = reader.offset
        values = [reader.string(), reader.string(), reader.uint(), reader.string(), reader.string(), reader.uint()]
        extension_start = reader.offset
        if values[5] == 1:
            members = reader.uint()
            if members > (len(raw) - reader.offset) // 12:
                raise GXWFormatError("structure member address count cannot fit the stream")
            for _ in range(members):
                reader.string()
                reader.string()
        elif values[5] == 2:
            reader.string()
            reader.string()
            if reader.uint() != 0:
                raise GXWFormatError("unobserved structure array address variant")
            reader.string()
            reader.string()
        extension = raw[extension_start:reader.offset]
        values.extend([reader.string(), reader.string(), reader.uint(), reader.string(),
                       reader.uint(), reader.uint(), reader.string()])
        if values[5] in (1, 2) and (values[11] != 9 or values[10] != int(values[5] == 2)):
            raise GXWFormatError("address extension outside observed structure declaration types")
        rows.append(LabelRecord(*values, offset=start, raw=raw[start:reader.offset], value_extension=extension))
    result = DeclarationDocument(logical_name, scope, owner, count_offset, tuple(rows),
                                 header, raw[reader.offset:], raw, return_type)
    if serialize_declarations(result) != raw:
        raise GXWFormatError("declaration table is not losslessly serializable")
    return result


def serialize_label(row: LabelRecord) -> bytes:
    return b"".join((_string(row.name), _string(row.data_type), struct.pack("<I", row.class_code),
                     _string(row.device), _string(row.iec_address), struct.pack("<I", row.unknown_u32),
                     row.value_extension,
                     _string(row.initial_value), _string(row.unknown_text), struct.pack("<I", row.record_id),
                     _string(row.comment), struct.pack("<II", row.array_marker, row.type_code),
                     _string(row.type_reference)))


def serialize_declarations(document: DeclarationDocument) -> bytes:
    header = bytearray(document.header)
    struct.pack_into("<I", header, document.count_offset, len(document.rows))
    return bytes(header) + b"".join(serialize_label(row) for row in document.rows) + document.trailer


def _type_parts(data_type):
    if not isinstance(data_type, str) or not data_type.strip():
        raise GXWFormatError("declaration type is required")
    text = data_type.strip()
    array = re.fullmatch(r"ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)", text, re.I)
    bounds = []
    if array:
        for part in array[1].split(","):
            bound = re.fullmatch(r"\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*", part)
            if not bound or int(bound[1]) > int(bound[2]):
                raise GXWFormatError("invalid declaration array bounds")
            bounds.append((int(bound[1]), int(bound[2])))
        text = array[2].strip()
    sized_string = re.fullmatch(r"STRING[ \t]*\[[ \t]*([0-9]+)[ \t]*\]", text)
    if sized_string:
        # Native ParseDataTypeString accepts larger lengths than the compiler.
        # Used Q03UDV ST declarations compile at 255 and reject 256; lowercase
        # string[n] is rejected too. Keep the caller's spelling in data_type.
        if not 1 <= int(sized_string[1]) <= 255:
            raise GXWFormatError("sized STRING length must be between 1 and 255")
        return "STRING", bounds
    if not re.fullmatch(r"[^\W\d]\w*", text, re.UNICODE):
        raise GXWFormatError("invalid declaration base type")
    return text, bounds


def edit_declarations(document: DeclarationDocument, *, upserts=(), renames=None, remove=()) -> DeclarationDocument:
    """Apply declarations by name; untouched rows retain all source fields.

    Each upsert has name/data_type plus optional kind=function_block, class_name,
    initial_value, device, iec_address, comment. A missing field on an existing
    row retains its value, except dependent type fields on a type change.
    """
    if (not isinstance(upserts, (list, tuple)) or not isinstance(remove, (list, tuple))
            or renames is not None and not isinstance(renames, dict)
            or any(not isinstance(n, str) for n in remove)
            or any(not isinstance(n, str) for n in (renames or {}))
            or any(not isinstance(item, dict) or any(not isinstance(v, str) for v in item.values()) for item in upserts)):
        raise GXWFormatError("declaration edits require string-valued rows, renames and names")
    rows = list(document.rows)
    touched = {n.casefold() for n in remove} | {n.casefold() for n in (renames or {})}
    touched.update(item.get("name", "").casefold() for item in upserts)
    if any(r.value_extension and r.name.casefold() in touched for r in rows):
        raise GXWFormatError("structure address extensions are read-only")
    names = [r.name.casefold() for r in rows]
    if len(set(names)) != len(names):
        raise GXWFormatError("ambiguous declaration names")
    ids = [r.record_id for r in rows]
    if len(set(ids)) != len(ids):
        raise GXWFormatError("duplicate declaration record IDs")
    next_id = max(ids, default=0) + 1
    renames = renames or {}
    for old, new in renames.items():
        if not isinstance(new, str) or not re.fullmatch(r"[^\W\d]\w*", new, re.UNICODE):
            raise GXWFormatError("invalid declaration name")
        hits = [i for i, row in enumerate(rows) if row.name.casefold() == old.casefold()]
        if len(hits) != 1:
            raise GXWFormatError(f"declaration not found: {old}")
        rows[hits[0]] = replace(rows[hits[0]], name=new)
    for name in remove:
        hits = [r for r in rows if r.name.casefold() == name.casefold()]
        if len(hits) != 1:
            raise GXWFormatError(f"declaration not found: {name}")
        rows.remove(hits[0])
    for item in upserts:
        allowed = {"name", "data_type", "kind", "class_name", "initial_value", "device", "iec_address", "comment"}
        if set(item) - allowed:
            raise GXWFormatError("unknown declaration edit fields")
        name = item.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[^\W\d]\w*", name, re.UNICODE):
            raise GXWFormatError("invalid declaration name")
        hits = [i for i, row in enumerate(rows) if row.name.casefold() == name.casefold()]
        if len(hits) > 1:
            raise GXWFormatError("ambiguous declaration edit")
        old = rows[hits[0]] if hits else None
        data_type = item.get("data_type", old.data_type if old else "")
        type_changed = not old or old.data_type != data_type or "kind" in item
        if not type_changed:
            code, initial, reference = old.type_code, old.initial_value, old.type_reference
            array_marker = old.array_marker
        else:
            base, bounds = _type_parts(data_type)
            basic = BASIC_TYPES.get(base.upper())
            kind = item.get("kind", "function_block" if old and old.type_code == 15 else "variable")
            if kind not in ("variable", "function_block"):
                raise GXWFormatError("unsupported declaration kind")
            if basic:
                if kind == "function_block":
                    raise GXWFormatError("a basic type cannot be a function block")
                code, initial = basic
                reference = ""
            elif kind == "function_block":
                code, initial, reference = 15, "", base
            else:
                raise GXWFormatError(f"declare an explicit function_block kind for type {base}")
            if bounds:
                initial = f"[{math.prod(high - low + 1 for low, high in bounds)}({initial})]"
            array_marker = int(bool(bounds))
        class_name = item.get("class_name")
        if class_name and ((document.scope == "global") != class_name.startswith("VAR_GLOBAL")):
            raise GXWFormatError("declaration class does not match table scope")
        class_code = CLASS_CODES.get(class_name) if class_name else old.class_code if old else CLASS_CODES["VAR" if document.scope == "local" else "VAR_GLOBAL"]
        if class_code is None:
            raise GXWFormatError("unsupported declaration class")
        # Zero/empty opaque defaults are repeated across all native type controls.
        row = old or LabelRecord(name, data_type, class_code, "", "", 0, initial, "", next_id, "", array_marker, code, reference)
        row = replace(row, name=name, data_type=data_type, class_code=class_code,
                      initial_value=item.get("initial_value", initial if type_changed else row.initial_value),
                      type_code=code, type_reference=reference, array_marker=array_marker,
                      **{k: item[k] for k in ("device", "iec_address", "comment") if k in item})
        if hits:
            rows[hits[0]] = row
        else:
            rows.append(row)
            next_id += 1
    final_names = [r.name.casefold() for r in rows]
    if len(set(final_names)) != len(final_names):
        raise GXWFormatError("duplicate/invalid declaration name after edits")
    result = replace(document, rows=tuple(rows))
    parse_declarations(serialize_declarations(result), logical_name=document.logical_name)
    return result
