"""Compiler symbol paths and exact lexical operand occurrences.

These cross-references describe stored artifacts. They preserve multiple
aliases and timer roles, and do not infer liveness or current-source validity.
"""
from __future__ import annotations

from dataclasses import dataclass

from .compiler_assignment import CompilerAssignment, parse_compiler_assignment
from .compiler_tables import CompilerComponent, CompilerTables
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
            reference = component.instance_pou_offset
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
            reference = component.instance_pou_offset
            if reference is not None:
                children.append((tables.pou_at(reference), path.names, path.component_offsets, ancestors))
        pending.extend(reversed(children))
    return tuple(result)


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
