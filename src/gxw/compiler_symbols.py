"""Compiler symbol paths and exact lexical operand occurrences.

These cross-references describe stored artifacts. They preserve multiple
aliases and timer roles, and do not infer liveness or current-source validity.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .compiler_assignment import CompilerAssignment, parse_compiler_assignment
from .compiler_tables import CompilerArray, CompilerComponent, CompilerTables
from .declarations import DeclarationDocument
from .models import GXWFormatError
from .token_listing import TokenInstruction, TokenListing


@dataclass(frozen=True)
class CompilerSymbol:
    pou_offset: int
    component: CompilerComponent
    assignment: CompilerAssignment
    declared_address_offset: int


@dataclass(frozen=True)
class CompilerSymbolPath:
    root_pou_offset: int
    names: tuple[bytes, ...]
    component_offsets: tuple[int, ...]
    symbol: CompilerSymbol


@dataclass(frozen=True)
class CompilerDeclarationCandidate:
    pou_offset: int
    component_offset: int
    compiled_type_code: int
    instance_type_name: bytes | None
    instance_type_agrees: bool | None


@dataclass(frozen=True)
class CompilerDeclarationBinding:
    declaration_offset: int
    name: str
    declared_type: str
    owner_offsets: tuple[int, ...]
    candidates: tuple[CompilerDeclarationCandidate, ...]

    @property
    def status(self) -> str:
        if not self.owner_offsets:
            return "no-compiled-owner"
        if len(self.owner_offsets) != 1:
            return "ambiguous-compiled-owner"
        if not self.candidates:
            return "no-compiled-component"
        if len(self.candidates) != 1:
            return "ambiguous-compiled-component"
        agrees = self.candidates[0].instance_type_agrees
        if agrees is not None:
            return "instance-type-agrees" if agrees else "instance-type-differs"
        return "name-match"


def compiler_declaration_bindings(tables: CompilerTables, document: DeclarationDocument,
                                  *, encoding: str = "cp936") -> tuple[CompilerDeclarationBinding, ...]:
    """Associate local source declarations with stored compiler components.

    Compare FB type names only when both sides provide that reference. Scalar
    type equivalence, liveness and source freshness are not inferred. All
    competing owners/components survive; non-ASCII names require exact bytes.
    """
    if document.scope != "local" or document.owner_name is None:
        raise GXWFormatError("compiler declaration binding requires a local owner")
    def key(raw):
        return raw.upper() if raw.isascii() else raw
    try:
        owner = key(document.owner_name.encode(encoding))
        names = [key(row.name.encode(encoding)) for row in document.rows]
        references = [key(row.type_reference.encode(encoding)) if row.type_reference else None
                      for row in document.rows]
    except UnicodeError as exc:
        raise GXWFormatError("source declaration name cannot use compiler encoding") from exc
    owners = [tables.pou_at(r.table_offset) for r in tables.tables[1].records]
    owners = [pou for pou in owners if key(pou.name_bytes) == owner]
    by_name = {}
    for pou in owners:
        for component in tables.components(pou):
            by_name.setdefault(key(component.name_bytes), []).append((pou, component))
    offsets = tuple(pou.record.table_offset for pou in owners)
    result = []
    for row, name, expected in zip(document.rows, names, references):
        candidates = []
        for pou, component in by_name.get(name, ()):
            reference = tables.component_instance_pou_offset(component)
            actual = tables.pou_at(reference).name_bytes if reference is not None else None
            agrees = key(actual) == expected if actual is not None and expected is not None else None
            candidates.append(CompilerDeclarationCandidate(pou.record.table_offset, component.record.table_offset,
                tables.component_type(component), actual, agrees))
        result.append(CompilerDeclarationBinding(row.offset, row.name, row.data_type, offsets, tuple(candidates)))
    return tuple(result)


def compiler_symbols(tables: CompilerTables) -> tuple[CompilerSymbol, ...]:
    result = []
    for record in tables.tables[1].records:
        pou = tables.pou_at(record.table_offset)
        for component in tables.components(pou):
            result.append(CompilerSymbol(record.table_offset, component,
                parse_compiler_assignment(component.user_info), tables.component_address_offset(component)))
    return tuple(result)


def compiler_symbol_paths(tables: CompilerTables, root_pou_offset: int, *, limit: int = 10000) -> tuple[CompilerSymbolPath, ...]:
    """Follow explicit FB references; fail visibly on cycles or expansion limit.

    A root is supplied by the caller; unowned library POUs are not guessed to
    be program roots. Arrays/structures are retained as leaf declarations.
    """
    root = tables.pou_at(root_pou_offset)
    pending = [(root, (root.name_bytes,), (), frozenset())]
    result = []
    while pending:
        pou, names, offsets, ancestors = pending.pop()
        if pou.record.table_offset in ancestors:
            raise GXWFormatError("cycle in compiler instance references")
        ancestors = ancestors | {pou.record.table_offset}
        children = []
        for component in tables.components(pou):
            if len(result) >= limit:
                raise GXWFormatError("compiler symbol path expansion exceeds limit")
            symbol = CompilerSymbol(pou.record.table_offset, component,
                parse_compiler_assignment(component.user_info), tables.component_address_offset(component))
            path = CompilerSymbolPath(root_pou_offset, names + (component.name_bytes,),
                                      offsets + (component.record.table_offset,), symbol)
            result.append(path)
            reference = tables.component_instance_pou_offset(component)
            if reference is not None:
                children.append((tables.pou_at(reference), path.names, path.component_offsets, ancestors))
        pending.extend(reversed(children))
    return tuple(result)


@dataclass(frozen=True)
class CompilerArrayReference:
    """A derived element address with the original cache records attached.

    This is stored-cache geometry, not a claim about source freshness or PLC
    capacity. No UserInfo or declaration bytes are rewritten by resolution.
    Widths and strides count bits for M and words for D.
    """
    component: CompilerComponent
    descriptor: CompilerArray
    indices: tuple[int, ...]
    linear_index: int
    member: CompilerComponent | None
    member_descriptor: CompilerArray | None
    member_indices: tuple[int, ...]
    member_linear_index: int
    type_pou_offset: int | None
    base_assignment: CompilerAssignment
    family_stride: int
    primitive_type: int
    primitive_width: int
    number: int

    @property
    def operand(self) -> str:
        return f"{self.base_assignment.device_family}{self.number}"

    @property
    def iec_address(self) -> str:
        prefix = "%MX0" if self.base_assignment.device_family == "M" else "%MW0"
        return f"{prefix}.{self.number}"


def _array_linear_index(descriptor: CompilerArray, indices: tuple[int, ...], rank_limit: int) -> int:
    if (not descriptor.count_matches_dimensions or descriptor.total_count > 65536
            or len(descriptor.dimensions) > rank_limit):
        raise GXWFormatError("compiler array geometry outside resolved reference profile")
    if len(indices) != len(descriptor.dimensions) or any(type(i) is not int for i in indices):
        raise GXWFormatError("compiler array reference requires one integer per dimension")
    relative = 0
    for index, dimension in zip(indices, descriptor.dimensions):
        if not dimension.lower <= index <= dimension.upper:
            raise GXWFormatError("compiler array reference index outside stored bounds")
        relative = relative * dimension.extent + index - dimension.lower
    return relative


def compiler_array_reference(tables: CompilerTables, component_offset: int, indices: tuple[int, ...],
                             *, member_offset: int | None = None,
                             member_indices: tuple[int, ...] = ()) -> CompilerArrayReference:
    """Resolve measured primitive or flat structure-array storage by identity.

    BOOL/INT/WORD/DWORD/DINT/REAL/TIME and STRING root arrays use compact M/D
    assignments. Flat structure members cover BOOL/INT/WORD/DWORD.
    Structure arrays use the separate word/bit strides in form 0x0c and the
    descriptor's explicit POU reference. Global type references resolve through
    their explicit table row; same-named type POUs are never merged. Nested
    types, STRING members, inline operands and other allocation forms
    remain unsupported here.
    """
    component = tables.component_at(component_offset)
    array_offset = tables.component_array_offset(component)
    if array_offset is None:
        raise GXWFormatError("unsupported compiler array reference owner")
    descriptor = tables.array_at(array_offset)
    indices, member_indices = tuple(indices), tuple(member_indices)
    relative = _array_linear_index(descriptor, indices, 3)
    primitive_type = descriptor.element_type
    member = member_descriptor = None
    type_pou_offset = None
    member_relative = 0
    if primitive_type == 0x34:
        if descriptor.element_parameter is None or member_offset is None:
            raise GXWFormatError("compiler structure array requires an explicit member identity")
        type_pou_offset = descriptor.element_parameter
        target = tables.pou_at(type_pou_offset)
        members = {m.record.table_offset: m for m in tables.components(target)}
        if member_offset not in members:
            raise GXWFormatError("compiler member does not belong to the referenced type POU")
        member = members[member_offset]
        primitive_type = tables.component_type(member)
        if primitive_type == 0x33:
            member_descriptor = tables.array_at(tables.component_array_offset(member))
            member_relative = _array_linear_index(member_descriptor, member_indices, 2)
            primitive_type = member_descriptor.element_type
        elif member_indices:
            raise GXWFormatError("compiler scalar member cannot have array indices")
        raw = component.user_info
        if (raw[0] != 0x0c or any(raw[1:5]) or any(raw[9:13]) or any(raw[17:])):
            raise GXWFormatError("compiler aggregate allocation outside split-stride profile")
        base = parse_compiler_assignment(member.user_info)
        stride = struct.unpack_from("<I", raw, 13 if primitive_type == 0 else 5)[0]
    else:
        if member_offset is not None or member_indices:
            raise GXWFormatError("compiler primitive array cannot have a member reference")
        base = parse_compiler_assignment(component.user_info)
        stride = 0
    profile = {0: ("M", 1), 2: ("D", 1), 17: ("D", 1), 18: ("D", 2)}
    if member is None:
        profile.update({3: ("D", 2), 9: ("D", 2), 11: ("D", 2)})
    if primitive_type == 15:
        if member is not None:
            raise GXWFormatError("unsupported compiler string member allocation")
        parameter = descriptor.element_parameter
        if parameter is None or not 0 <= parameter <= 32767:
            raise GXWFormatError("compiler string extent outside resolved reference profile")
        # ECCompiler_IEC 15.50 RVA 0x519f0 returns parameter + 1 bytes;
        # 0x52f7e rounds each element to words before multiplying its count.
        family, width = "D", (parameter + 2) // 2
    else:
        if primitive_type not in profile:
            raise GXWFormatError("unsupported compiler array primitive type")
        family, width = profile[primitive_type]
    if (base.status != "decoded" or base.device_family != family
            or base.role != ("bit" if family == "M" else "word")):
        raise GXWFormatError("compiler array requires a compact primitive base assignment")
    if member is None:
        stride = width
        if base.reserved_count != descriptor.total_count * width:
            raise GXWFormatError("compiler primitive array extent disagrees with reservation")
    else:
        member_count = member_descriptor.total_count if member_descriptor is not None else 1
        if stride < width * member_count or stride > 0x7fffff:
            raise GXWFormatError("compiler member extent exceeds aggregate family stride")
    number = base.number + relative * stride + member_relative * width
    if number < 0 or number + width - 1 > 0x7fffff:
        raise GXWFormatError("compiler array reference exceeds compact address width")
    return CompilerArrayReference(component, descriptor, indices, relative, member,
        member_descriptor, member_indices, member_relative, type_pou_offset,
        base, stride, primitive_type, width, number)


@dataclass(frozen=True)
class CompilerOperandOccurrence:
    instruction_offset: int
    step: int | None
    mnemonic: str
    operand_index: int
    operand_offset: int
    text: str
    component_offsets: tuple[int, ...]


def compiler_operand_occurrences(symbols: tuple[CompilerSymbol, ...], listing: TokenListing) -> tuple[CompilerOperandOccurrence, ...]:
    """Match complete FX operand spellings, retaining every possible alias.

    Does not expand indexed operands, word extents, implicit outputs or digit
    groups. An unmatched argument remains an occurrence with no candidate.
    Timer status/coil/value aliases remain distinct symbols at the same Tn.
    """
    by_operand: dict[str, list[int]] = {}
    for symbol in symbols:
        operand = symbol.assignment.fx_operand
        if operand is not None:
            by_operand.setdefault(operand, []).append(symbol.component.record.table_offset)
    result = []
    for record in listing.records:
        if not isinstance(record, TokenInstruction):
            continue
        for index, operand in enumerate(record.operands):
            result.append(CompilerOperandOccurrence(record.tokens[0].offset, record.step, record.mnemonic,
                index, operand.tokens[0].offset, operand.text, tuple(by_operand.get(operand.text, ()))))
    return tuple(result)
