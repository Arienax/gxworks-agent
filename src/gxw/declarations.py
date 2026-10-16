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
CLASS_CODES = {"VAR": 1, "VAR_CONSTANT": 2, "VAR_INPUT": 3, "VAR_OUTPUT": 4,
               "VAR_IN_OUT": 5, "VAR_GLOBAL": 8, "VAR_GLOBAL_CONSTANT": 9,
               "VAR_INPUT_CONSTANT": 11, "VAR_IN_EXT": 12}
_ARRAY_TYPE = re.compile(r"ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)", re.I)


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
    # Workspace GetPOUType/GetPOUStatus correspondence. These describe the
    # declaration owner, independently of the source compile-pending header.
    owner_pou_type: int | None = None
    owner_pou_status: int | None = None
    owner_sfc_block_number: int | None = None


def local_table_for(documents, program_name, source_groups=None):
    """Resolve one local owner through current native metadata when supplied."""
    if source_groups is None:
        # Standalone source models retain their existing canonical-name API.
        name = program_name.removesuffix('.Program.pou') + '.Labels.lh'
    else:
        name = source_groups.get(program_name, {}).get('labels')
    if name not in documents:
        return None
    document = documents[name]
    if source_groups is not None and (document.scope != 'local' or not document.owner_name
            or not program_name.startswith(document.owner_name + '.')
            or not name.startswith(document.owner_name + '.')):
        raise GXWFormatError('project declaration owner and native source group differ')
    return name


def resolve_label(documents, program_name, symbol, *, global_variable_hiding=False, local_table=...):
    """Resolve one source name in the selected POU's local/global scope.

    A unique local label hides a unique global label only when the project's
    native option enables it. Duplicate declarations within a scope and an
    unknown option remain ambiguous. No declaration is removed or rewritten.
    """
    local_name = local_table_for(documents, program_name) if local_table is ... else local_table
    local = [(local_name, row) for row in documents[local_name].rows
             if row.name.casefold() == symbol.casefold()] if local_name in documents else []
    globals_ = [(name, row) for name, doc in documents.items() if doc.scope == 'global'
                for row in doc.rows if row.name.casefold() == symbol.casefold()]
    if global_variable_hiding is True and len(local) == len(globals_) == 1:
        return local[0]
    matched = local + globals_
    if len(matched) > 1:
        raise GXWFormatError('ambiguous local/global source declaration name: ' + symbol)
    return matched[0] if matched else None


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
    """Read project/library type members without assigning owners or addresses.

    The current project directory supplies ownership. Native-created .str
    members can retain zero type/array caches after cold compilation; these
    fields remain opaque here rather than overriding the declared type text.
    """
    if not logical_name.endswith((".str", ".lns")):
        raise GXWFormatError("not an observed structure source")
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


def parse_declarations(raw: bytes, *, logical_name: str, source_kind: int | None = None) -> DeclarationDocument:
    if logical_name.endswith((".lh", ".lnl")):
        scope = "local"
    elif logical_name.endswith((".gh", ".lng")):
        scope = "global"
    else:
        raise GXWFormatError("not a local/global declaration stream")
    reader = _Reader(raw)
    # Common native header; the timestamp/status bytes remain opaque.
    reader.take(source_payload_offset(raw))
    owner = reader.string() if scope == "local" else None
    return_type = pou_type = pou_status = None
    if scope == "local":
        pou_type, pou_status = reader.uint(), reader.uint()
        reader.string()  # Retained in header; no meaning assigned.
        return_type = reader.string() or None
    block_number = None
    if source_kind == 240:
        if scope != "local":
            raise GXWFormatError("SFC block metadata requires a local declaration owner")
        # SFCBlock2 inserts its block number and six counted property strings
        # before the variable count. Source kind comes from the paired POU;
        # the ordinary POU type word cannot distinguish this derived layout.
        block_number = reader.uint()
        for _ in range(6):
            reader.string()  # Preserve properties verbatim in the header.
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
            if reader.uint() not in (0, 1):
                raise GXWFormatError("unobserved structure array address variant")
            reader.string()
            reader.string()
        extension = raw[extension_start:reader.offset]
        values.extend([reader.string(), reader.string(), reader.uint(), reader.string(),
                       reader.uint(), reader.uint(), reader.string()])
        if values[5] in (1, 2):
            cached_structure = values[11] == 9 and values[10] == int(values[5] == 2)
            # Original global16 creation saves zero type caches. Q06UDV
            # establishes scalar storage; Q04UDV establishes rank1 arrays.
            # L02 project FB creation also saves a zero cache for scalar
            # IN_OUT structure locals. The declared type and storage mode
            # remain independent of these caches.
            created_structure = False
            created_scope = (scope == "global" and values[2] == CLASS_CODES["VAR_GLOBAL"]
                or logical_name.endswith(".lh") and scope == "local" and pou_type == 0x1000002
                and values[2] == CLASS_CODES["VAR_IN_OUT"] and values[5] == 1)
            if (created_scope
                    and values[10] == values[11] == 0 and values[12] == ""):
                try:
                    base_type, bounds = _type_parts(values[1])
                    created_structure = (base_type.upper() not in BASIC_TYPES
                        and (not bounds if values[5] == 1 else len(bounds) == 1
                             and -2**31 <= bounds[0][0] <= bounds[0][1] < 2**31))
                except (GXWFormatError, ValueError):
                    pass
            # Original SaveUserLibrary/CreateUserLibrary on the authored
            # Motion library retains each scalar IN_OUT address extension
            # while replacing its type cache with 100 and clearing the cached
            # reference. Preserve that imported .lnl record; the current
            # library structure still owns type resolution and edit support.
            imported_structure = False
            if (logical_name.endswith(".lnl") and scope == "local"
                    and pou_type == 0x1000002 and values[2] == CLASS_CODES["VAR_IN_OUT"]
                    and values[5] == 1 and values[10] == 0 and values[11] == 100
                    and values[12] == ""):
                try:
                    base_type, bounds = _type_parts(values[1])
                    imported_structure = not bounds and base_type.upper() not in BASIC_TYPES
                except (GXWFormatError, ValueError):
                    pass
            if not (cached_structure or created_structure or imported_structure):
                raise GXWFormatError("address extension outside observed structure declaration types")
        rows.append(LabelRecord(*values, offset=start, raw=raw[start:reader.offset], value_extension=extension))
    result = DeclarationDocument(logical_name, scope, owner, count_offset, tuple(rows),
                                 header, raw[reader.offset:], raw, return_type, pou_type, pou_status, block_number)
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
    array = _ARRAY_TYPE.fullmatch(text)
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


def _is_array_bound_edit(old_type, new_type, new_bounds):
    old_array = _ARRAY_TYPE.fullmatch(old_type.strip())
    new_array = _ARRAY_TYPE.fullmatch(new_type.strip())
    if not old_array or not new_array or old_array[2].strip() != new_array[2].strip():
        return False
    try:
        _, old_bounds = _type_parts(old_type)
    except GXWFormatError:
        return False
    return len(old_bounds) == len(new_bounds)


def edit_declarations(document: DeclarationDocument, *, upserts=(), renames=None, remove=(),
                      native_fbd_locals=False) -> DeclarationDocument:
    """Apply declarations by name; untouched rows retain all source fields.

    Each upsert has name/data_type plus optional kind=function_block/structure, class_name,
    initial_value, device, iec_address, comment. A missing field on an existing
    row retains its value, except dependent type fields on a type change.
    Array bound edits of the same element type and rank retain saved type fields
    and the initial value; supply initial_value explicitly when resizing it.
    The selected FBD source context supplies native_fbd_locals for original
    typed local creation; plain table editing retains its existing defaults.
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
    # This saved field is not the native ObjectId. In the observed FX0N
    # workspace, several newly created BOOL rows legitimately retain zero.
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
        class_name = item.get("class_name")
        if class_name and ((document.scope == "global") != class_name.startswith("VAR_GLOBAL")):
            raise GXWFormatError("declaration class does not match table scope")
        class_code = CLASS_CODES.get(class_name) if class_name else old.class_code if old else CLASS_CODES["VAR" if document.scope == "local" else "VAR_GLOBAL"]
        if class_code is None:
            raise GXWFormatError("unsupported declaration class")
        if class_name and class_code in (3, 4, 5, 11, 12) and document.owner_pou_type not in (0x1000002, 0x1000003):
            raise GXWFormatError('formal declaration class requires a function or function-block owner')
        type_changed = not old or old.data_type != data_type or "kind" in item
        if not type_changed:
            code, initial, reference = old.type_code, old.initial_value, old.type_reference
            array_marker = old.array_marker
        else:
            base, bounds = _type_parts(data_type)
            basic = BASIC_TYPES.get(base.upper())
            kind = item.get("kind", "function_block" if old and old.type_code == 15 else "variable")
            if kind not in ("variable", "function_block", "structure"):
                raise GXWFormatError("unsupported declaration kind")
            if basic:
                if kind != "variable":
                    raise GXWFormatError("a basic type requires a variable declaration kind")
                code, initial = basic
                reference = ""
            elif kind == "function_block":
                code, initial, reference = 15, "", base
            elif kind == "structure":
                local_root = (native_fbd_locals and document.owner_pou_type in (0x1000000, 0x1000002)
                              and class_code == CLASS_CODES['VAR'])
                local_inout = (document.owner_pou_type == 0x1000002
                               and class_code == CLASS_CODES['VAR_IN_OUT'])
                if document.scope != 'local' or not (local_root or local_inout) or len(bounds) > 1:
                    raise GXWFormatError('structure creation requires a local Program/FB VAR or FB IN_OUT declaration')
                code, initial, reference = 0, "", ""
            else:
                raise GXWFormatError(f"declare an explicit function_block kind for type {base}")
            if bounds:
                initial = f"[{math.prod(high - low + 1 for low, high in bounds)}({initial})]"
            array_marker = int(bool(bounds))
            if (not old and document.owner_pou_type == 0x1000002 and class_code == CLASS_CODES['VAR_IN_OUT']
                    and (kind == 'structure' or base in ('BOOL', 'WORD') and (not bounds or base == 'WORD' and len(bounds) == 1))):
                # Original typed creation on component1.635 retains the declared
                # type text, including bounds, with empty initialization and
                # zero caches. These are not scalar/array/type identities.
                code, initial, reference, array_marker = 0, '', '', 0
            if (native_fbd_locals and not old and document.scope == 'local' and document.owner_pou_type in (0x1000000, 0x1000002)
                    and class_code == CLASS_CODES['VAR'] and (kind == 'structure'
                        or base in ('BOOL', 'WORD') and len(bounds) == 1)):
                # Original FBD local creation retains the complete type text,
                # but does not save a scalar/array/type cache or initializer.
                code, initial, reference, array_marker = 0, '', '', 0
            if old and basic and "kind" not in item and _is_array_bound_edit(old.data_type, data_type, bounds):
                # FX3G 1.635 native bound edits retain zero-valued array/type
                # fields as well as the existing formal's empty initializer.
                # Do not replace a saved representation with creation defaults.
                code, initial, reference = old.type_code, old.initial_value, old.type_reference
                array_marker = old.array_marker
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
    parse_declarations(serialize_declarations(result), logical_name=document.logical_name,
                       source_kind=240 if document.owner_sfc_block_number is not None else None)
    return result
