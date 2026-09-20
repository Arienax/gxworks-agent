"""Raw-preserved CGTable framing and observed POU/component references.

ECCompiler.dll 15.22 Restore/Save use fourteen length-bounded tables. Each
contains length-bounded records. References are byte offsets within a table,
not array indexes; distinct FB instances can have identically named POU rows.
All fields without independent interpretations remain numeric/raw.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError


@dataclass(frozen=True)
class CompilerTableRecord:
    offset: int
    table_offset: int
    raw: bytes

    @property
    def payload(self) -> bytes:
        return self.raw[4:]

    def named_fields(self) -> tuple[bytes, bytes]:
        value = self.payload
        if not value or value[0] == 255 or value[0] + 1 > len(value):
            raise GXWFormatError("unsupported compiler record name framing")
        end = value[0] + 1
        return value[1:end], value[end:]


@dataclass(frozen=True)
class CompilerTable:
    index: int
    offset: int
    raw: bytes
    records: tuple[CompilerTableRecord, ...]

    def reconstruct(self) -> bytes:
        return self.raw[:4] + b"".join(r.raw for r in self.records)

    def record_at(self, offset: int) -> CompilerTableRecord:
        for record in self.records:
            if record.table_offset == offset:
                return record
        raise GXWFormatError("compiler reference is not a record boundary")


@dataclass(frozen=True)
class CompilerPOU:
    record: CompilerTableRecord
    name_bytes: bytes
    fields: tuple[int, ...]

    @property
    def component_start(self) -> int:
        return self.fields[1]

    @property
    def component_end(self) -> int:
        return self.fields[2]

    @property
    def component_count(self) -> int:
        return self.fields[3]


@dataclass(frozen=True)
class CompilerComponent:
    record: CompilerTableRecord
    name_bytes: bytes
    fields_raw: bytes

    @property
    def scope_code(self) -> int:
        return struct.unpack_from("<I", self.fields_raw)[0]

    @property
    def global_offset(self) -> int | None:
        # Native CMP reader RVA 0x15BD5 branches to table 3 for these scopes.
        if self.scope_code in (9, 12, 18, 19):
            return struct.unpack_from("<I", self.fields_raw, 4)[0]
        return None

    @property
    def type_code(self) -> int | None:
        # External declarations store a global-row reference and reference
        # count here, not their type. Resolve through CompilerTables instead.
        return self.fields_raw[8] if self.global_offset is None else None

    @property
    def user_info(self) -> bytes:
        # The inspected native CgTab configuration uses 26 opaque UserInfo
        # bytes for table 2. Native CMP Read copies these separately from its
        # resolved struct; they are not a NUL-terminated string.
        if len(self.fields_raw) < 38:
            raise GXWFormatError("truncated compiler component UserInfo")
        return self.fields_raw[-26:]

    @property
    def instance_pou_offset(self) -> int | None:
        # Native TabsDump identifies TYPE_INSTANCE and its PouIdx at this slot.
        # Array/structure types have other layouts; do not reinterpret them.
        if self.type_code == 0x35:
            if len(self.fields_raw) < 16:
                raise GXWFormatError("truncated compiler instance reference")
            return struct.unpack_from("<I", self.fields_raw, 12)[0]
        return None


@dataclass(frozen=True)
class CompilerAddress:
    record: CompilerTableRecord
    location_code: int
    size_code: int
    name_bytes: bytes
    reference_count: int
    opaque_tail: bytes

    @property
    def iec_address_bytes(self) -> bytes | None:
        # ECCompiler 15.22 address formatter RVA 0x10FC0. Empty names denote
        # an unspecified declaration, even though its other codes are zero.
        if not self.name_bytes or self.location_code > 2 or self.size_code > 5:
            return None
        return (b"%" + b"IQM"[self.location_code:self.location_code + 1]
                + b"XBWDLU"[self.size_code:self.size_code + 1] + self.name_bytes)


@dataclass(frozen=True)
class CompilerArrayDimension:
    lower: int
    extent: int
    encoding: int

    @property
    def upper(self) -> int | None:
        return self.lower + self.extent - 1 if self.extent > 0 else None


@dataclass(frozen=True)
class CompilerArray:
    record: CompilerTableRecord
    element_type: int
    type_padding: bytes
    element_parameter: int | None
    total_count: int
    count_encoding: int
    dimensions: tuple[CompilerArrayDimension, ...]
    opaque_tail: bytes

    @property
    def count_matches_dimensions(self) -> bool:
        product = 1
        for dimension in self.dimensions:
            if dimension.extent <= 0:
                return False
            product *= dimension.extent
        return bool(self.dimensions) and product == self.total_count


def _array_number(raw: bytes, offset: int, kind: int) -> tuple[int, int]:
    # ECCompiler 15.22 A550 chooses physical widths; A3F0 converts these
    # six integer encodings. Kind 3 uses FOUR bytes, not a guessed int16.
    formats = {1: (1, True), 2: (1, False), 3: (4, True),
               4: (2, False), 5: (4, True), 6: (4, False)}
    if kind not in formats:
        raise GXWFormatError("unsupported compiler array numeric encoding")
    width, signed = formats[kind]
    end = offset + width
    if end > len(raw):
        raise GXWFormatError("truncated compiler array number")
    return int.from_bytes(raw[offset:end], "little", signed=signed), end


@dataclass(frozen=True)
class CompilerTables:
    raw: bytes
    tables: tuple[CompilerTable, ...]

    def reconstruct(self) -> bytes:
        return b"".join(t.reconstruct() for t in self.tables)

    def pou_at(self, offset: int) -> CompilerPOU:
        record = self.tables[1].record_at(offset)
        name, fields = record.named_fields()
        if len(fields) != 56:
            raise GXWFormatError("unsupported compiler POU record layout")
        return CompilerPOU(record, name, struct.unpack("<14I", fields))

    def components(self, pou: CompilerPOU) -> tuple[CompilerComponent, ...]:
        table = self.tables[2]
        start, end = pou.component_start, pou.component_end
        boundaries = {r.table_offset for r in table.records} | {len(table.raw) - 4}
        if start not in boundaries or end not in boundaries or end < start:
            raise GXWFormatError("compiler component range is not record-bounded")
        records = [r for r in table.records if start <= r.table_offset < end]
        if len(records) != pou.component_count:
            raise GXWFormatError("compiler component range/count disagree")
        return tuple(self.component_at(r.table_offset) for r in records)

    def component_at(self, offset: int) -> CompilerComponent:
        record = self.tables[2].record_at(offset)
        name, fields = record.named_fields()
        if len(fields) < 12:
            raise GXWFormatError("truncated compiler component fields")
        return CompilerComponent(record, name, fields)

    def component_type(self, component: CompilerComponent) -> int:
        if component.type_code is not None:
            return component.type_code
        record = self.tables[3].record_at(component.global_offset)
        _, fields = record.named_fields()
        if len(fields) < 8:
            raise GXWFormatError("truncated compiler global type")
        return fields[4]

    def component_address_offset(self, component: CompilerComponent) -> int:
        """Resolve the declaration's address reference, including externals.

        A declared address and the allocator's UserInfo are separate evidence.
        An empty address name must not be read as an assignment to address zero.
        """
        if component.global_offset is None:
            return struct.unpack_from("<i", component.fields_raw, 4)[0]
        _, fields = self.tables[3].record_at(component.global_offset).named_fields()
        if len(fields) < 8:
            raise GXWFormatError("truncated compiler global address reference")
        return struct.unpack_from("<i", fields)[0]

    def address_at(self, offset: int) -> CompilerAddress:
        record = self.tables[4].record_at(offset)
        raw = record.payload
        if len(raw) < 8:
            raise GXWFormatError("truncated compiler address")
        length = struct.unpack_from("<H", raw, 2)[0]
        end = 4 + length
        if end + 4 > len(raw):
            raise GXWFormatError("compiler address name exceeds record")
        return CompilerAddress(record, raw[0], raw[1], raw[4:end],
                               struct.unpack_from("<I", raw, end)[0], raw[end + 4:])

    def component_array_offset(self, component: CompilerComponent) -> int | None:
        if self.component_type(component) != 0x33:
            return None
        if component.global_offset is None:
            fields, offset = component.fields_raw, 12
        else:
            _, fields = self.tables[3].record_at(component.global_offset).named_fields()
            offset = 8
        if len(fields) < offset + 4:
            raise GXWFormatError("truncated compiler array reference")
        return struct.unpack_from("<I", fields, offset)[0]

    def array_at(self, offset: int) -> CompilerArray:
        """Read table 13 using the native 141C0 framing; retain all other bytes.

        The native source-type parser 130C0 converts [lower..upper] into
        (lower, upper - lower + 1); A990 multiplies extents into total_count.
        Restore keeps the stored total independently, even if inconsistent.
        """
        record = self.tables[13].record_at(offset)
        raw = record.payload
        if len(raw) < 6:
            raise GXWFormatError("truncated compiler array descriptor")
        element, cursor, parameter = raw[0], 4, None
        if element in (0x0F, 0x34, 0x35):
            if cursor + 6 > len(raw):
                raise GXWFormatError("truncated compiler array element parameter")
            parameter = struct.unpack_from("<I", raw, cursor)[0]
            cursor += 4
        count, encoding = raw[cursor:cursor + 2]
        total, cursor = _array_number(raw, cursor + 2, encoding)
        dimensions = []
        for _ in range(count):
            if cursor >= len(raw):
                raise GXWFormatError("truncated compiler array dimension")
            codes = raw[cursor]
            lower, cursor = _array_number(raw, cursor + 1, codes & 15)
            extent, cursor = _array_number(raw, cursor, codes >> 4)
            dimensions.append(CompilerArrayDimension(lower, extent, codes))
        return CompilerArray(record, element, raw[1:4], parameter, total, encoding,
                             tuple(dimensions), raw[cursor:])


def parse_compiler_tables(raw: bytes) -> CompilerTables:
    """Frame the observed fourteen-table form; never infer missing tables."""
    raw = bytes(raw)
    cursor, tables = 0, []
    for index in range(14):
        start = cursor
        if cursor + 4 > len(raw):
            raise GXWFormatError("truncated compiler table length")
        length = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        payload_start, end, records = cursor, cursor + length, []
        if end > len(raw):
            raise GXWFormatError("compiler table exceeds input")
        while cursor < end:
            if cursor + 4 > end:
                raise GXWFormatError("truncated compiler record length")
            record_end = cursor + 4 + struct.unpack_from("<I", raw, cursor)[0]
            if record_end > end:
                raise GXWFormatError("compiler record exceeds table")
            records.append(CompilerTableRecord(cursor, cursor - payload_start, raw[cursor:record_end]))
            cursor = record_end
        tables.append(CompilerTable(index, start, raw[start:end], tuple(records)))
    if cursor != len(raw):
        raise GXWFormatError("unsupported compiler table suffix")
    return CompilerTables(raw, tuple(tables))
