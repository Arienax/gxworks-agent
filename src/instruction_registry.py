"""Data-driven PLC instruction catalogue used across import, IR and validation.

The public ladder JSON format intentionally remains unchanged.  APP_INSTR nodes
continue to use ``{"type": "APP_INSTR", "opcode": ..., "operands": [...]}``.
This module only centralizes metadata that used to be duplicated in the CSV
importer, JSON validator and PLC IR.

Unknown vendor instructions are representable.  Callers can therefore preserve
and round-trip a GX Works2 instruction even when its semantics have not yet been
added to the local catalogue.  Unknown instructions must be handled
conservatively: no write targets or other semantics are guessed.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


class OperandRole(str, Enum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    CONTROL = "control"


class InstructionCategory(str, Enum):
    CONDITION = "condition"
    ACTION = "action"
    BRANCH_CONTROL = "branch_control"
    PROGRAM_CONTROL = "program_control"


class SemanticKind(str, Enum):
    CONTACT = "contact"
    COIL = "coil"
    COMPARISON = "comparison"
    FUNCTION = "function"
    FUNCTION_BLOCK = "function_block"
    CONTROL = "control"
    VENDOR = "vendor"


@dataclass(frozen=True)
class OperandSpec:
    name: str
    role: OperandRole = OperandRole.READ
    data_type: str = "any"
    device_prefixes: Tuple[str, ...] = ()
    optional: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], index: int) -> "OperandSpec":
        role_text = str(payload.get("role", "read") or "read").strip().lower()
        try:
            role = OperandRole(role_text)
        except ValueError as exc:
            raise ValueError(f"invalid operand role {role_text!r}") from exc
        prefixes = tuple(
            str(item).strip().upper()
            for item in (payload.get("device_prefixes") or [])
            if str(item).strip()
        )
        return cls(
            name=str(payload.get("name") or f"operand_{index}").strip(),
            role=role,
            data_type=str(payload.get("data_type") or "any").strip().lower(),
            device_prefixes=prefixes,
            optional=bool(payload.get("optional", True)),
        )


@dataclass(frozen=True)
class InstructionSpec:
    mnemonic: str
    vendor: str = "mitsubishi"
    canonical_op: str = ""
    category: InstructionCategory = InstructionCategory.ACTION
    semantic_kind: SemanticKind = SemanticKind.VENDOR
    operands: Tuple[OperandSpec, ...] = ()
    min_operands: Optional[int] = None
    max_operands: Optional[int] = None
    cpu_support: frozenset[str] = field(default_factory=frozenset)
    notes: str = ""

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        default_vendor: str = "mitsubishi",
    ) -> "InstructionSpec":
        mnemonic = str(payload.get("mnemonic") or "").strip().upper()
        if not mnemonic:
            raise ValueError("instruction mnemonic is required")
        category_text = str(payload.get("category") or "action").strip().lower()
        semantic_text = str(payload.get("semantic_kind") or "vendor").strip().lower()
        try:
            category = InstructionCategory(category_text)
        except ValueError as exc:
            raise ValueError(
                f"{mnemonic}: invalid instruction category {category_text!r}"
            ) from exc
        try:
            semantic_kind = SemanticKind(semantic_text)
        except ValueError as exc:
            raise ValueError(
                f"{mnemonic}: invalid semantic kind {semantic_text!r}"
            ) from exc

        arity = payload.get("arity") or {}
        if not isinstance(arity, Mapping):
            raise ValueError(f"{mnemonic}: arity must be an object")
        minimum = arity.get("min")
        maximum = arity.get("max")
        min_operands = int(minimum) if minimum is not None else None
        max_operands = int(maximum) if maximum is not None else None
        if (
            min_operands is not None
            and max_operands is not None
            and min_operands > max_operands
        ):
            raise ValueError(f"{mnemonic}: arity min exceeds max")

        raw_operands = payload.get("operands") or []
        if not isinstance(raw_operands, list):
            raise ValueError(f"{mnemonic}: operands must be an array")
        operands = tuple(
            OperandSpec.from_mapping(item, index)
            for index, item in enumerate(raw_operands)
            if isinstance(item, Mapping)
        )
        return cls(
            mnemonic=mnemonic,
            vendor=str(payload.get("vendor") or default_vendor).strip().lower(),
            canonical_op=str(payload.get("canonical_op") or mnemonic).strip().upper(),
            category=category,
            semantic_kind=semantic_kind,
            operands=operands,
            min_operands=min_operands,
            max_operands=max_operands,
            cpu_support=frozenset(
                str(item).strip().upper()
                for item in (payload.get("cpu_support") or [])
                if str(item).strip()
            ),
            notes=str(payload.get("notes") or "").strip(),
        )

    def supports_cpu(self, cpu: Optional[str]) -> bool:
        if not cpu or not self.cpu_support:
            return True
        return str(cpu).strip().upper() in self.cpu_support

    def accepts_arity(self, count: int) -> bool:
        if self.min_operands is not None and count < self.min_operands:
            return False
        if self.max_operands is not None and count > self.max_operands:
            return False
        return True

    @property
    def write_indexes(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, operand in enumerate(self.operands)
            if operand.role in {OperandRole.WRITE, OperandRole.READ_WRITE}
        )

    @property
    def read_write_indexes(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, operand in enumerate(self.operands)
            if operand.role == OperandRole.READ_WRITE
        )

    @property
    def read_indexes(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, operand in enumerate(self.operands)
            if operand.role in {OperandRole.READ, OperandRole.READ_WRITE}
        )


class InstructionRegistry:
    """Immutable-by-convention lookup table for vendor instruction metadata."""

    def __init__(self, specs: Iterable[InstructionSpec] = ()) -> None:
        self._specs: Dict[Tuple[str, str], InstructionSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: InstructionSpec) -> None:
        key = (spec.vendor.lower(), spec.mnemonic.upper())
        if key in self._specs:
            raise ValueError(
                f"duplicate instruction definition {spec.vendor}:{spec.mnemonic}"
            )
        self._specs[key] = spec

    def resolve(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
    ) -> Optional[InstructionSpec]:
        key = (
            str(vendor or "mitsubishi").strip().lower(),
            str(mnemonic or "").strip().upper(),
        )
        return self._specs.get(key)

    def is_known(self, mnemonic: Any, *, vendor: str = "mitsubishi") -> bool:
        return self.resolve(mnemonic, vendor=vendor) is not None

    def category_of(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
    ) -> Optional[InstructionCategory]:
        spec = self.resolve(mnemonic, vendor=vendor)
        return spec.category if spec is not None else None

    def write_indexes(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
    ) -> Tuple[int, ...]:
        spec = self.resolve(mnemonic, vendor=vendor)
        return spec.write_indexes if spec is not None else ()

    def read_write_indexes(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
    ) -> Tuple[int, ...]:
        spec = self.resolve(mnemonic, vendor=vendor)
        return spec.read_write_indexes if spec is not None else ()

    def known_mnemonics(self, *, vendor: str = "mitsubishi") -> frozenset[str]:
        normalized = str(vendor or "mitsubishi").strip().lower()
        return frozenset(
            mnemonic
            for (item_vendor, mnemonic) in self._specs
            if item_vendor == normalized
        )

    @classmethod
    def from_files(cls, paths: Sequence[Path]) -> "InstructionRegistry":
        specs = []
        seen_schema_versions = set()
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}: catalogue root must be an object")
            schema_version = int(payload.get("schema_version", 1))
            seen_schema_versions.add(schema_version)
            if schema_version != 1:
                raise ValueError(
                    f"{path}: unsupported instruction catalogue schema {schema_version}"
                )
            vendor = str(payload.get("vendor") or "mitsubishi").strip().lower()
            instructions = payload.get("instructions") or []
            if not isinstance(instructions, list):
                raise ValueError(f"{path}: instructions must be an array")
            for item in instructions:
                if not isinstance(item, Mapping):
                    raise ValueError(f"{path}: instruction entry must be an object")
                specs.append(
                    InstructionSpec.from_mapping(item, default_vendor=vendor)
                )
        return cls(specs)


def _candidate_catalog_directories() -> Tuple[Path, ...]:
    candidates = []
    configured = os.environ.get("GXW2_INSTRUCTION_CATALOG")
    if configured:
        candidates.append(Path(configured).expanduser())

    # Source checkout: <repo>/src/instruction_registry.py -> <repo>/resources/...
    candidates.append(
        Path(__file__).resolve().parent.parent
        / "resources"
        / "instructions"
        / "mitsubishi"
    )

    # PyInstaller one-file/one-dir builds can expose bundled data via _MEIPASS.
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(
            Path(bundle_root) / "resources" / "instructions" / "mitsubishi"
        )

    # Useful for development launchers that set the repository as cwd.
    candidates.append(
        Path.cwd() / "resources" / "instructions" / "mitsubishi"
    )

    unique = []
    seen = set()
    for item in candidates:
        resolved = item.resolve()
        text = str(resolved).casefold()
        if text not in seen:
            seen.add(text)
            unique.append(resolved)
    return tuple(unique)


def load_default_instruction_registry() -> InstructionRegistry:
    required = ("common.json", "fx3u.json", "fx5u.json")
    for directory in _candidate_catalog_directories():
        paths = tuple(directory / name for name in required)
        if all(path.is_file() for path in paths):
            return InstructionRegistry.from_files(paths)
    searched = "\n - ".join(str(item) for item in _candidate_catalog_directories())
    raise RuntimeError(
        "Mitsubishi instruction catalogue not found. Searched:\n - " + searched
    )


DEFAULT_INSTRUCTION_REGISTRY = load_default_instruction_registry()


GENERATION_TYPED_OUTPUT_OPCODES = frozenset({"OUT", "PLS", "PLF", "END"})
GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES = frozenset(
    {InstructionCategory.CONDITION, InstructionCategory.BRANCH_CONTROL}
)


def generation_app_instr_mnemonics(cpu=None):
    """Opcodes the model may emit as APP_INSTR for the selected CPU."""
    model = str(cpu or "").strip().upper() or None
    result = []
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics():
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
        if spec is None:
            continue
        if mnemonic in GENERATION_TYPED_OUTPUT_OPCODES:
            continue
        if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
            continue
        if model and not spec.supports_cpu(model):
            continue
        result.append(mnemonic)
    return tuple(sorted(result))


def get_instruction_spec(
    mnemonic: Any,
    *,
    vendor: str = "mitsubishi",
) -> Optional[InstructionSpec]:
    return DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic, vendor=vendor)


def catalogued_write_indexes(mnemonic: Any) -> Tuple[int, ...]:
    return DEFAULT_INSTRUCTION_REGISTRY.write_indexes(mnemonic)


def catalogued_read_write_indexes(mnemonic: Any) -> Tuple[int, ...]:
    return DEFAULT_INSTRUCTION_REGISTRY.read_write_indexes(mnemonic)


__all__ = [
    "DEFAULT_INSTRUCTION_REGISTRY",
    "GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES",
    "GENERATION_TYPED_OUTPUT_OPCODES",
    "generation_app_instr_mnemonics",
    "InstructionCategory",
    "InstructionRegistry",
    "InstructionSpec",
    "OperandRole",
    "OperandSpec",
    "SemanticKind",
    "catalogued_read_write_indexes",
    "catalogued_write_indexes",
    "get_instruction_spec",
    "load_default_instruction_registry",
]
