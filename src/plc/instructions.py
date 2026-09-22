"""Data-driven PLC instruction catalogue used across import, IR and validation.

The public ladder JSON format intentionally remains unchanged. APP_INSTR nodes
continue to use ``{"type": "APP_INSTR", "opcode": ..., "operands": [...]}``.
This module centralizes instruction metadata and Mitsubishi applied-instruction
modifier grammar so generation, validation, import and IR analysis share one
source of truth.

Unknown vendor instructions are representable. Callers can therefore preserve
and round-trip a GX Works2 instruction even when its semantics have not yet been
added to the local catalogue. Unknown instructions must be handled
conservatively: no write targets or other semantics are guessed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
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
    contract_level: str = "full"
    supports_pulse: bool = False
    double_mnemonic: str = ""
    double_pulse_mnemonic: str = ""
    notes: str = ""
    # Independently corroborated dimensions, scoped to the resolved CPU/form.
    # Legacy `full` is a compatibility label, not evidence of complete semantics.
    verified_fields: Tuple[str, ...] = ()
    native_operand_order: Tuple[str, ...] = ()
    contract_sources: Tuple[Mapping[str, Any], ...] = ()
    execution_form: Optional[str] = None
    instruction_width: Optional[int] = None

    def contract_coverage(self) -> Dict[str, str]:
        declared = {
            "arity": self.min_operands is not None or self.max_operands is not None,
            "operand_order": bool(self.operands),
            "operand_roles": bool(self.operands),
            "operand_types": bool(self.operands) and all(o.data_type != "any" for o in self.operands),
            "device_classes": bool(self.operands) and all(o.device_prefixes for o in self.operands),
            "form_identity": bool(self.mnemonic),
            "cpu_applicability": bool(self.cpu_support), "hardware_applicability": False,
            "execution_form": bool(self.execution_form), "instruction_width": self.instruction_width is not None,
            "execution_conditions": False, "completion_ownership": False,
            "numeric_and_memory_boundaries": False,
        }
        return {key: "source_verified" if key in self.verified_fields else
                "declared_unverified" if value else "unresolved"
                for key, value in declared.items()}

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
        contract_level = str(payload.get("contract_level") or "full").strip().lower()
        if contract_level not in {"full", "opcode_only", "signature"}:
            raise ValueError(f"{mnemonic}: invalid contract_level {contract_level!r}")

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

        modifier_rule = payload.get("modifier_rule") or {}
        if not isinstance(modifier_rule, Mapping):
            raise ValueError(f"{mnemonic}: modifier_rule must be an object")
        supports_pulse = bool(modifier_rule.get("pulse", False))
        double_rule = modifier_rule.get("double", False)
        if double_rule is True:
            double_mnemonic = "D" + mnemonic
        elif double_rule in (False, None, ""):
            double_mnemonic = ""
        elif isinstance(double_rule, str):
            double_mnemonic = double_rule.strip().upper()
        else:
            raise ValueError(
                f"{mnemonic}: modifier_rule.double must be bool or mnemonic"
            )
        double_pulse_rule = modifier_rule.get("double_pulse")
        if double_pulse_rule in (None, ""):
            double_pulse_mnemonic = (
                double_mnemonic + "P"
                if double_mnemonic and supports_pulse
                else ""
            )
        elif isinstance(double_pulse_rule, str):
            double_pulse_mnemonic = double_pulse_rule.strip().upper()
        else:
            raise ValueError(
                f"{mnemonic}: modifier_rule.double_pulse must be a mnemonic"
            )
        if double_pulse_mnemonic and not double_mnemonic:
            raise ValueError(f"{mnemonic}: double_pulse requires a double form")

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
            contract_level=contract_level,
            supports_pulse=supports_pulse,
            double_mnemonic=double_mnemonic,
            double_pulse_mnemonic=double_pulse_mnemonic,
            notes=str(payload.get("notes") or "").strip(),
        )

    @property
    def supports_double(self) -> bool:
        return bool(self.double_mnemonic)

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


@dataclass(frozen=True)
class InstructionResolution:
    """One opcode resolved to a base applied instruction plus D/P modifiers."""

    opcode: str
    spec: InstructionSpec
    base_spec: InstructionSpec
    double: bool = False
    pulse: bool = False

    @property
    def base_mnemonic(self) -> str:
        return self.base_spec.mnemonic


class InstructionRegistry:
    """Immutable-by-convention lookup table for vendor instruction metadata."""

    def __init__(self, specs: Iterable[InstructionSpec] = ()) -> None:
        self._specs: Dict[Tuple[str, str], InstructionSpec] = {}
        self._cpu_contracts: Dict[Tuple[str, str, str], InstructionSpec] = {}
        self._variant_index: Optional[
            Dict[Tuple[str, str], InstructionResolution]
        ] = None
        for spec in specs:
            self.register(spec)

    def register(self, spec: InstructionSpec) -> None:
        key = (spec.vendor.lower(), spec.mnemonic.upper())
        if key in self._specs:
            raise ValueError(
                f"duplicate instruction definition {spec.vendor}:{spec.mnemonic}"
            )
        self._specs[key] = spec
        self._variant_index = None

    def _ensure_variant_index(
        self,
    ) -> Dict[Tuple[str, str], InstructionResolution]:
        if self._variant_index is not None:
            return self._variant_index
        variants: Dict[Tuple[str, str], InstructionResolution] = {}

        def add_variant(
            base: InstructionSpec,
            opcode: str,
            *,
            double: bool,
            pulse: bool,
        ) -> None:
            token = str(opcode or "").strip().upper()
            if not token or token == base.mnemonic:
                return
            key = (base.vendor.lower(), token)
            existing = variants.get(key)
            if (
                existing is not None
                and existing.base_spec.mnemonic != base.mnemonic
            ):
                raise ValueError(
                    f"ambiguous generated instruction form {token}: "
                    f"{existing.base_spec.mnemonic} vs {base.mnemonic}"
                )
            exact = self._specs.get(key)
            effective = exact
            if effective is None and double and base.double_mnemonic:
                effective = self._specs.get(
                    (base.vendor.lower(), base.double_mnemonic)
                )
            if effective is None:
                effective = base
            variants[key] = InstructionResolution(
                opcode=token,
                spec=effective,
                base_spec=base,
                double=double,
                pulse=pulse,
            )

        for spec in self._specs.values():
            if spec.supports_pulse:
                add_variant(
                    spec,
                    spec.mnemonic + "P",
                    double=False,
                    pulse=True,
                )
            if spec.double_mnemonic:
                add_variant(
                    spec,
                    spec.double_mnemonic,
                    double=True,
                    pulse=False,
                )
            if spec.double_pulse_mnemonic:
                add_variant(
                    spec,
                    spec.double_pulse_mnemonic,
                    double=True,
                    pulse=True,
                )
        self._variant_index = variants
        return variants

    def resolve_form(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
        cpu: Optional[str] = None,
    ) -> Optional[InstructionResolution]:
        normalized_vendor = str(vendor or "mitsubishi").strip().lower()
        token = str(mnemonic or "").strip().upper()
        key = (normalized_vendor, token)
        variant = self._ensure_variant_index().get(key)
        if variant is not None:
            effective = self._cpu_contracts.get((normalized_vendor, token, str(cpu or "").strip().upper()))
            return replace(variant, spec=effective) if effective is not None else variant
        exact = self._specs.get(key)
        if exact is None:
            return None
        return InstructionResolution(
            opcode=token,
            spec=self._cpu_contracts.get((normalized_vendor, token, str(cpu or "").strip().upper()), exact),
            base_spec=exact,
        )

    def resolve(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
        cpu: Optional[str] = None,
    ) -> Optional[InstructionSpec]:
        resolved = self.resolve_form(mnemonic, vendor=vendor, cpu=cpu)
        return resolved.spec if resolved is not None else None

    def load_contract_promotions(self, path: Path) -> None:
        """Load build-time corroborated facts; never discover/promote at runtime.

        Overrides apply only to the exact CPU and literal form in the ledger.
        No opcode admission, write-role guessing or modifier inference occurs.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                or type(payload.get("schema_version")) is not int or payload.get("cpu") != "FX3U"
                or payload.get("method") != "native-table-geometry+independent-ST-signature-v1"):
            raise ValueError("Unsupported instruction promotion ledger")
        sources = payload.get("sources", {})
        if not isinstance(sources, dict) or not isinstance(payload.get("entries"), list):
            raise ValueError("Invalid promotion sources/entries")
        manual_ids = ("fx3_programming_r", "fxcpu_basic_applied_m")
        for manual in manual_ids:
            source = sources.get(manual, {})
            if not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k] for k in ("manual", "revision", "url")):
                raise ValueError("Promotion source identity missing")
            digest = source.get("sha256", "")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Promotion source hash missing")
        pending = {}
        for row in payload["entries"]:
            if not isinstance(row, dict):
                raise ValueError("Invalid promotion entry")
            count, order, forms = row.get("arity"), row.get("native_order"), row.get("forms")
            if (type(count) is not int or count < 0 or not isinstance(order, list)
                    or len(order) != count
                    or any(not isinstance(symbol, str) or not re.fullmatch(r"[SDNM]\d?", symbol) for symbol in order)
                    or len(set(order)) != count
                    or not isinstance(forms, list) or not forms):
                raise ValueError("Invalid corroborated instruction signature")
            if any(not isinstance(row.get(k, {}), dict) for k in ("execution_forms", "instruction_widths")):
                raise ValueError("Invalid form metadata")
            evidence = []
            for manual, prefix in zip(manual_ids, ("native", "structured")):
                page, proof = row.get(prefix + "_page"), row.get(prefix + "_proof", "")
                if type(page) is not int or page < 1 or not isinstance(proof, str) or len(proof) != 64 or any(c not in "0123456789abcdef" for c in proof):
                    raise ValueError("Promotion page/proof missing")
                evidence.append({"manual_id": manual, "manual": sources[manual]["manual"],
                                 "revision": sources[manual]["revision"], "pdf_page": page,
                                 "source_sha256": sources[manual]["sha256"],
                                 "proof_sha256": proof})
            for opcode in forms:
                if not isinstance(opcode, str) or opcode != opcode.upper():
                    raise ValueError("Invalid promotion opcode")
                base = self.resolve(opcode)
                key = ("mitsubishi", opcode, "FX3U")
                if base is None or not base.supports_cpu("FX3U") or key in pending or key in self._cpu_contracts:
                    raise ValueError("Unknown/duplicate promotion target: " + opcode)
                if not base.accepts_arity(count):
                    raise ValueError("Promotion conflicts with existing arity: " + opcode)
                verified = ["arity", "operand_order", "form_identity"]
                execution = row.get("execution_forms", {}).get(opcode)
                width = row.get("instruction_widths", {}).get(opcode)
                if execution is not None:
                    if execution not in {"continuous", "pulse"}:
                        raise ValueError("Invalid instruction execution form")
                    verified.append("execution_form")
                if width is not None:
                    if type(width) is not int or width not in {16, 32}:
                        raise ValueError("Invalid instruction width")
                    verified.append("instruction_width")
                pending[key] = replace(base, mnemonic=opcode, min_operands=count, max_operands=count,
                    contract_level="signature" if base.contract_level == "opcode_only" else base.contract_level,
                    native_operand_order=tuple(order), verified_fields=tuple(verified),
                    execution_form=execution, instruction_width=width, contract_sources=tuple(evidence))
        self._cpu_contracts.update(pending)

    def describe_contract(self, mnemonic: Any, cpu: Optional[str] = None) -> Dict[str, Any]:
        """One compact Core view for analysis, generation, MCP and diagnostics."""
        form = self.resolve_form(mnemonic, cpu=cpu)
        if form is None:
            return {"opcode": str(mnemonic).upper(), "contract_level": "unknown"}
        spec = form.spec
        coverage = spec.contract_coverage()
        return {"opcode": form.opcode, "base_mnemonic": form.base_mnemonic,
                "contract_level": "signature_verified" if spec.verified_fields else
                    "opcode_only" if spec.contract_level == "opcode_only" else "partial",
                "min_operands": spec.min_operands, "max_operands": spec.max_operands,
                "native_operand_order": list(spec.native_operand_order),
                "execution_form": spec.execution_form, "instruction_width": spec.instruction_width,
                "verified_fields": list(spec.verified_fields),
                "unverified_fields": [key for key, value in coverage.items() if value != "source_verified"],
                "operand_annotations": [{"name": o.name, "role": o.role.value,
                    "data_type": o.data_type, "device_prefixes": list(o.device_prefixes)} for o in spec.operands],
                "sources": [{k: v for k, v in source.items() if k not in {"proof_sha256", "source_sha256"}} for source in spec.contract_sources]}

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

    def known_mnemonics(
        self,
        *,
        vendor: str = "mitsubishi",
        include_generated: bool = True,
    ) -> frozenset[str]:
        normalized = str(vendor or "mitsubishi").strip().lower()
        result = {
            mnemonic
            for (item_vendor, mnemonic) in self._specs
            if item_vendor == normalized
        }
        if include_generated:
            result.update(
                mnemonic
                for (item_vendor, mnemonic) in self._ensure_variant_index()
                if item_vendor == normalized
            )
        return frozenset(result)

    @classmethod
    def from_files(cls, paths: Sequence[Path]) -> "InstructionRegistry":
        entries = []
        modifier_rules: Dict[Tuple[str, str], Mapping[str, Any]] = {}
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}: catalogue root must be an object")
            schema_version = int(payload.get("schema_version", 1))
            if schema_version != 1:
                raise ValueError(
                    f"{path}: unsupported instruction catalogue schema {schema_version}"
                )
            vendor = str(payload.get("vendor") or "mitsubishi").strip().lower()
            raw_rules = payload.get("modifier_rules") or {}
            if not isinstance(raw_rules, Mapping):
                raise ValueError(f"{path}: modifier_rules must be an object")
            for mnemonic, rule in raw_rules.items():
                token = str(mnemonic or "").strip().upper()
                if not token:
                    continue
                if not isinstance(rule, Mapping):
                    raise ValueError(
                        f"{path}: modifier rule for {token} must be an object"
                    )
                key = (vendor, token)
                previous = modifier_rules.get(key)
                normalized_rule = dict(rule)
                if previous is not None and dict(previous) != normalized_rule:
                    raise ValueError(
                        f"{path}: conflicting modifier rule for {vendor}:{token}"
                    )
                modifier_rules[key] = normalized_rule

            instructions = payload.get("instructions") or []
            if not isinstance(instructions, list):
                raise ValueError(f"{path}: instructions must be an array")
            for item in instructions:
                if not isinstance(item, Mapping):
                    raise ValueError(f"{path}: instruction entry must be an object")
                entries.append((vendor, dict(item)))

        specs = []
        seen_rule_targets = set()
        for vendor, item in entries:
            mnemonic = str(item.get("mnemonic") or "").strip().upper()
            rule = modifier_rules.get((vendor, mnemonic))
            if rule is not None:
                item["modifier_rule"] = dict(rule)
                seen_rule_targets.add((vendor, mnemonic))
            specs.append(
                InstructionSpec.from_mapping(item, default_vendor=vendor)
            )
        dangling = sorted(set(modifier_rules) - seen_rule_targets)
        if dangling:
            vendor, mnemonic = dangling[0]
            raise ValueError(
                f"modifier rule targets unknown instruction {vendor}:{mnemonic}"
            )
        return cls(specs)


def _candidate_catalog_directories() -> Tuple[Path, ...]:
    candidates = []
    configured = os.environ.get("GXW2_INSTRUCTION_CATALOG")
    if configured:
        candidates.append(Path(configured).expanduser())

    candidates.append(
        Path(__file__).resolve().parents[2]
        / "resources"
        / "instructions"
        / "mitsubishi"
    )

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(
            Path(bundle_root) / "resources" / "instructions" / "mitsubishi"
        )

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
            verified = directory / "fx3u_verified_opcodes.json"
            if verified.is_file():
                paths = paths + (verified,)
            modifier_rules = directory / "modifier_rules.json"
            if modifier_rules.is_file():
                paths = paths + (modifier_rules,)
            registry = InstructionRegistry.from_files(paths)
            promotions = directory / "fx3u_contract_promotions.json"
            if promotions.is_file():
                registry.load_contract_promotions(promotions)
            return registry
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
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic, cpu=model)
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
    "InstructionResolution",
    "InstructionSpec",
    "OperandRole",
    "OperandSpec",
    "SemanticKind",
    "catalogued_read_write_indexes",
    "catalogued_write_indexes",
    "get_instruction_spec",
    "load_default_instruction_registry",
]
