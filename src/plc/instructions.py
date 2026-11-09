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

import copy
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from plc.instruction_definition import InstructionFactGroup


# Literal arithmetic spellings are ordinary vendor instruction names.
# Registration determines which of these syntactically valid forms exist.
LITERAL_ARITHMETIC_MNEMONIC_PATTERN = r'[A-Z]*[+*/-]P?'


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
class OperandUsageFact:
    """One independently sourced facet of an operand's meaning."""

    facet: str
    value: Any
    status: str = "declared_unverified"
    sources: Tuple[Mapping[str, Any], ...] = ()
    conditions: Tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OperandUsageFact":
        facet = str(payload.get("facet") or "").strip()
        if facet not in {"purpose", "unit", "encoding", "range", "condition"}:
            raise ValueError("Invalid operand usage facet")
        value = payload.get("value")
        if not isinstance(value, (str, Mapping)) or not value or isinstance(value, str) and not value.strip():
            raise ValueError("Operand usage value is required")
        status = str(payload.get("status") or "declared_unverified")
        if status not in {"declared_unverified", "candidate_evidence", "source_verified"}:
            raise ValueError("Invalid operand usage status")
        sources = payload.get("sources") or []
        if not isinstance(sources, (list, tuple)) or any(
            not isinstance(item, Mapping) or not any(item.get(key) for key in (
                "id", "manual_id", "reference", "path",
            )) for item in sources
        ):
            raise ValueError("Invalid operand usage sources")
        if status != "declared_unverified" and not sources:
            raise ValueError("Sourced operand usage requires its own evidence")
        conditions = payload.get("conditions") or []
        if not isinstance(conditions, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in conditions
        ):
            raise ValueError("Invalid operand usage conditions")
        return cls(facet, copy.deepcopy(value), status, tuple(copy.deepcopy(dict(item)) for item in sources), tuple(conditions))

    def as_mapping(self) -> Dict[str, Any]:
        result = {"facet": self.facet, "value": copy.deepcopy(self.value), "status": self.status}
        if self.sources:
            result["sources"] = [copy.deepcopy(dict(item)) for item in self.sources]
        if self.conditions:
            result["conditions"] = list(self.conditions)
        return result


@dataclass(frozen=True)
class OperandSpec:
    name: str
    role: OperandRole = OperandRole.READ
    data_type: str = "any"
    device_prefixes: Tuple[str, ...] = ()
    optional: bool = True
    usage_facts: Tuple[OperandUsageFact, ...] = ()
    allowed_devices: Tuple[str, ...] = ()
    word_count: int = 1
    semantic_name: str = ""
    digit_groups_max: int = 0
    bit_count: int = 1
    integer_literal_widths: Tuple[Tuple[str, int], ...] = ()
    address_forms: Tuple[str, ...] = ()
    address_form_limits: Tuple[Tuple[str, Tuple[Tuple[str, Any], ...]], ...] = ()

    @property
    def device_point_count(self) -> int:
        """Consecutive points for a plain bit or word device operand."""
        return self.bit_count if self.data_type in {'bit', 'bool'} else max(
            self.word_count, 2 if self.data_type == 'dword' else 1)

    def slot_metadata(self, position: int, symbol: str = "") -> Dict[str, Any]:
        return {"position": position,
                **({"symbol": symbol} if symbol else {}),
                **({"semantic_name": self.semantic_name} if self.semantic_name else {})}

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
        usage = payload.get("usage_facts") or []
        if not isinstance(usage, list) or any(not isinstance(item, Mapping) for item in usage):
            raise ValueError("Operand usage facts must be an array of objects")
        count = payload.get("word_count", 1)
        if type(count) is not int or not 1 <= count <= 65536:
            raise ValueError("Invalid operand memory extent")
        bit_count = payload.get('bit_count', 1)
        data_type = str(payload.get('data_type') or 'any').strip().lower()
        if (type(bit_count) is not int or not 1 <= bit_count <= 65536
                or bit_count != 1 and data_type not in {'bit', 'bool'}):
            raise ValueError('Invalid operand bit extent')
        literal_widths = payload.get('integer_literal_widths', {})
        if (not isinstance(literal_widths, Mapping) or set(literal_widths) - {'K', 'H'}
                or any(type(width) is not int or width not in {16, 32} for width in literal_widths.values())
                or literal_widths and (data_type not in {'any', 'word', 'dword'} or role != OperandRole.READ)):
            raise ValueError('Invalid reviewed integer literal formats')
        digits = payload.get("digit_groups_max", 0)
        if type(digits) is not int or not 0 <= digits <= 8:
            raise ValueError("Invalid digit-specified operand width")
        address_forms = payload.get('address_forms', [])
        if (not isinstance(address_forms, list)
                or any(not isinstance(form, str) or form not in {'buffer_memory', 'link_direct'} for form in address_forms)
                or len(set(address_forms)) != len(address_forms)
                or address_forms and data_type not in {'any', 'bit', 'bool', 'word', 'dword', 'real', 'lreal', 'string'}):
            raise ValueError('Invalid reviewed operand address forms')
        address_form_limits = payload.get('address_form_limits', {})
        if (not isinstance(address_form_limits, Mapping)
                or set(address_form_limits) - set(address_forms)):
            raise ValueError('Operand address limits require an authorized address form')
        for limits in address_form_limits.values():
            if (not isinstance(limits, Mapping) or not limits
                    or set(limits) - {'maximum_base_address', 'allowed_device_prefixes'}):
                raise ValueError('Invalid reviewed external-device limits')
            if 'maximum_base_address' in limits and (type(limits['maximum_base_address']) is not int
                    or not 0 <= limits['maximum_base_address'] <= 32767):
                raise ValueError('Invalid reviewed buffer-memory base address limit')
            if 'allowed_device_prefixes' in limits and (not isinstance(limits['allowed_device_prefixes'], list)
                    or not limits['allowed_device_prefixes'] or any(not isinstance(prefix, str)
                    or not re.fullmatch('[A-Z]+', prefix) for prefix in limits['allowed_device_prefixes'])):
                raise ValueError('Invalid reviewed external-device prefixes')
        name = str(payload.get("name") or f"operand_{index}").strip()
        semantic_name = str(payload.get("semantic_name") or name).strip()
        if re.fullmatch(r"operand_\d+|[A-Z]\d*", semantic_name, re.I):
            semantic_name = ""
        return cls(
            name=name,
            role=role,
            data_type=data_type,
            device_prefixes=prefixes,
            optional=bool(payload.get("optional", True)),
            usage_facts=tuple(OperandUsageFact.from_mapping(item) for item in usage),
            allowed_devices=tuple(str(x).upper() for x in payload.get("allowed_devices", ())),
            word_count=count,
            semantic_name=semantic_name,
            digit_groups_max=digits,
            bit_count=bit_count,
            integer_literal_widths=tuple(sorted(literal_widths.items())),
            address_forms=tuple(address_forms),
            address_form_limits=tuple(sorted((form, tuple(sorted((key, tuple(value) if isinstance(value, list) else value)
                                                               for key, value in limits.items())))
                                            for form, limits in address_form_limits.items())),
        )


@dataclass(frozen=True)
class CompletionSemantics:
    device: str
    placement: str = "same_rung_parallel_branch"


@dataclass(frozen=True)
class PulseOutputSemantics:
    frequency_operand_indexes: Tuple[int, ...] = ()
    pulse_output_operand_index: int = 0
    direction_output_operand_index: Optional[int] = None


@dataclass(frozen=True)
class NumericOperandBoundary:
    operand_index: int
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    absolute: bool = False
    unit: str = ""
    label: str = "operand"


@dataclass(frozen=True)
class DisjointBitRangeBoundary:
    source_operand_index: int
    destination_operand_index: int
    destination_length_operand_index: int
    source_length_operand_index: int
    same_device_prefix_only: bool = True
    error_code: str = ""


@dataclass(frozen=True)
class NativeOperandSignature:
    operands: Tuple[OperandSpec, ...]
    native_order: Tuple[str, ...]

    @classmethod
    def from_mapping(cls, payload):
        if not isinstance(payload, Mapping) or set(payload) != {"operands", "native_order"}:
            raise ValueError("Native overload needs operands and their native order")
        operands, order = payload["operands"], payload["native_order"]
        if (not isinstance(operands, list) or not operands
                or any(not isinstance(p, Mapping) for p in operands)
                or not isinstance(order, list) or len(order) != len(operands)
                or any(not isinstance(p, str) or not p for p in order)
                or len(set(order)) != len(order)):
            raise ValueError("Invalid native overload signature")
        return cls(tuple(OperandSpec.from_mapping(p, i) for i, p in enumerate(operands)), tuple(order))



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
    native_overloads: Tuple[NativeOperandSignature, ...] = ()
    contract_sources: Tuple[Mapping[str, Any], ...] = ()
    execution_form: Optional[str] = None
    instruction_width: Optional[int] = None
    completion: Optional[CompletionSemantics] = None
    pulse_output: Optional[PulseOutputSemantics] = None
    numeric_operand_boundaries: Tuple[NumericOperandBoundary, ...] = ()
    disjoint_bit_ranges: Tuple[DisjointBitRangeBoundary, ...] = ()
    cpu_replacements: Tuple[Tuple[str, str], ...] = ()
    # Build-produced facts extend this same owner. They never admit an opcode,
    # certify an unrelated field, or alter native operand order.
    definition_facts: Tuple[InstructionFactGroup, ...] = ()
    source_materials: Tuple[Mapping[str, Any], ...] = ()
    uninterpreted_content: Tuple[Mapping[str, Any], ...] = ()
    st_call: Optional[Mapping[str, Any]] = None
    modifier_cpu_support: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    def contract_coverage(self) -> Dict[str, str]:
        declared = {
            "arity": self.min_operands is not None or self.max_operands is not None,
            "operand_order": bool(self.operands),
            "operand_roles": bool(self.operands),
            "operand_types": bool(self.operands) and all(o.data_type != "any" for o in self.operands),
            "device_classes": bool(self.operands) and all(o.device_prefixes or o.address_forms for o in self.operands),
            "form_identity": bool(self.mnemonic),
            "cpu_applicability": bool(self.cpu_support),
            "hardware_applicability": self.pulse_output is not None,
            "execution_form": bool(self.execution_form),
            "instruction_width": self.instruction_width is not None,
            "execution_conditions": False,
            "completion_ownership": self.completion is not None,
            "numeric_and_memory_boundaries": bool(
                self.numeric_operand_boundaries or self.disjoint_bit_ranges
            ),
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
        overloads = payload.get("native_overloads", [])
        if not isinstance(overloads, list):
            raise ValueError(f"{mnemonic}: native_overloads must be an array")
        overloads = tuple(NativeOperandSignature.from_mapping(p) for p in overloads)
        sizes = [len(p.operands) for p in overloads]
        if overloads and (operands or len(set(sizes)) != len(sizes)
                or min_operands != min(sizes) or max_operands != max(sizes)):
            raise ValueError(f"{mnemonic}: native overloads need distinct, exact operand counts")

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

        form_models = modifier_rule.get('cpu_support_by_form', {})
        if not isinstance(form_models, Mapping):
            raise ValueError(f"{mnemonic}: modifier CPU scopes must be an object")
        forms = {double_mnemonic, double_pulse_mnemonic, mnemonic + 'P' if supports_pulse else ''} - {''}
        modifier_cpu_support = []
        for form, models in form_models.items():
            if (not isinstance(form, str) or form not in forms or not isinstance(models, list) or not models
                    or any(not isinstance(model, str) or not model.strip() for model in models)):
                raise ValueError(f"{mnemonic}: modifier CPU scopes need a registered form and nonempty model list")
            normalized = tuple(sorted(model.strip().upper() for model in models))
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"{mnemonic}: duplicate model in modifier CPU scope")
            modifier_cpu_support.append((form, normalized))

        return cls(
            mnemonic=mnemonic,
            vendor=str(payload.get("vendor") or default_vendor).strip().lower(),
            canonical_op=str(payload.get("canonical_op") or mnemonic).strip().upper(),
            category=category,
            semantic_kind=semantic_kind,
            operands=operands,
            native_overloads=overloads,
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
            modifier_cpu_support=tuple(sorted(modifier_cpu_support)),
            notes=str(payload.get("notes") or "").strip(),
            definition_facts=tuple(InstructionFactGroup.from_mapping(item) for item in payload.get("definition_facts", [])),
            source_materials=tuple(copy.deepcopy(payload.get("source_materials", []))),
            uninterpreted_content=tuple(copy.deepcopy(payload.get("uninterpreted_content", []))),
        )

    @property
    def supports_double(self) -> bool:
        return bool(self.double_mnemonic)

    def supports_cpu(self, cpu: Optional[str]) -> bool:
        if cpu and self.replacement_for_cpu(cpu):
            return False
        if not cpu or not self.cpu_support:
            return True
        model = str(cpu).strip().upper()
        if model in self.cpu_support:
            return True
        from plc.target_identity import target_identity_state
        members, reason = target_identity_state(model)
        return bool(reason is None and members and len(members) > 1
                    and all(member in self.cpu_support for member in members))

    def accepts_arity(self, count: int) -> bool:
        if self.native_overloads:
            return any(len(signature.operands) == count for signature in self.native_overloads)
        if self.min_operands is not None and count < self.min_operands:
            return False
        if self.max_operands is not None and count > self.max_operands:
            return False
        return True

    def for_operand_count(self, count: Optional[int]) -> "InstructionSpec":
        if count is not None:
            for signature in self.native_overloads:
                if len(signature.operands) == count:
                    return replace(self, operands=signature.operands, native_operand_order=signature.native_order,
                                   native_overloads=(), min_operands=count, max_operands=count)
        return self

    def replacement_for_cpu(self, cpu: Optional[str]) -> str:
        model = str(cpu or "").strip().upper()
        for target_cpu, replacement in self.cpu_replacements:
            if target_cpu == model:
                return replacement
        return ""

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


def reviewed_native_fact_groups(spec, opcode, operands, plc_model, *, dimensions=None):
    """Source-checked native facts whose full dependency closure may apply.

    This supplies storage and access descriptions, not execution admission.
    Missing runtime/firmware values keep a possible access conditional. Known
    scope exclusions, malformed conditions and missing evidence are retained as
    exclusions rather than being made into storage permissions.
    """
    if spec is None or not spec.supports_cpu(plc_model) or not spec.accepts_arity(len(operands)):
        return []
    from plc.instruction_definition import DefinitionError, UnknownInstructionSemantics, select_fact_dependencies
    from plc.instruction_constraints import _condition, literal_operand_values
    values = literal_operand_values(spec, operands, plc_model=plc_model, opcode=opcode)
    context = {'$PLC_MODEL': plc_model, '$LANGUAGE': 'ladder',
               'project_mode': 'native_ladder_instruction', 'program_context': 'ladder'}
    if 'operand_order' in spec.verified_fields and len(spec.native_operand_order) == len(operands):
        context['$OPERAND_ROLES'] = spec.native_operand_order

    def possible(group):
        if (group['status'] != 'source_verified' or group['verification'] != 'source_checked'
                or any(source.get('conditions') or source.get('applicability') for source in group['sources'])):
            return False
        for condition in group.get('conditions', []):
            try:
                if not _condition(condition, values, context):
                    return False
            except UnknownInstructionSemantics:
                continue
            except (DefinitionError, TypeError, ValueError, KeyError):
                return False
        return not (group['dimension'] == 'constraints.cpu_availability'
                    and group['value'].get('available') is False)

    selected = []
    for group in spec.definition_facts:
        if dimensions is not None and group.dimension not in dimensions:
            continue
        if any(group.scope.get(key) and value not in group.scope[key]
               for key, value in (('forms', opcode), ('models', plc_model), ('languages', 'ladder'))):
            continue
        closure = select_fact_dependencies(spec.definition_facts, [group.id], opcode=opcode,
            model=plc_model, language='ladder', allow_unknown_scope=True)
        if (any(gap['reason'] != 'scope_context_missing' for gap in closure['gaps'])
                or not closure['groups'] or not all(possible(item) for item in closure['groups'])):
            continue
        selected.append(group)
    return selected


def reviewed_native_operand_parameters(spec, opcode, operands, plc_model):
    """Return independently qualified native parameter descriptions per slot."""
    result = [[] for _ in operands]
    if spec is None or len(spec.native_operand_order) != len(operands):
        return result
    order = tuple(name.upper() for name in spec.native_operand_order)
    for group in reviewed_native_fact_groups(spec, opcode, operands, plc_model,
                                             dimensions={'parameters.typed_operands'}):
        parameters = group.value.get('operands')
        if (not isinstance(parameters, list) or len(parameters) != len(order)
                or any(not isinstance(item, Mapping) for item in parameters)
                or tuple(str(item.get('name', '')).upper() for item in parameters) != order):
            continue
        native = group.value.get('native_form') or group.value.get('representation') == 'native_ladder_instruction'
        if not native:
            continue
        for index, parameter in enumerate(parameters):
            if parameter not in result[index]:
                result[index].append(copy.deepcopy(parameter))
    return result


def reviewed_module_selector_value(spec, index, value, plc_model):
    """Decode a selector's storage ordinal, independently of its runtime value."""
    if (spec is None or not 0 <= index < len(spec.operands)
            or not str(plc_model).upper().startswith(('Q', 'L'))
            or not spec.supports_cpu(plc_model) or not spec.contract_sources
            or not {'form_identity', 'cpu_applicability', 'operand_order', 'operand_roles',
                    'operand_types', 'device_classes'} <= set(spec.verified_fields)):
        return None
    operand = spec.operands[index]
    if (operand.semantic_name != 'intelligent_module_IO_selector_or_value'
            or operand.data_type != 'word' or operand.word_count != 1
            or operand.role != OperandRole.READ
            or re.fullmatch(r'U[0-9A-F]+', str(value).strip().upper()) is None):
        return None
    from plc.storage.tables import storage_profile
    from plc.storage.q_encoder import encode_q_operand
    from plc.storage.operands_q import encoded_tokens
    from plc.storage.errors import StorageRuleError
    try:
        tokens = encoded_tokens(encode_q_operand(str(value).strip().upper(), op=spec.mnemonic,
            index=index, profile=storage_profile(plc_model), cpu=plc_model))
        if len(tokens) != 1 or tokens[0][1] != 0xd8:
            return None
        number = int.from_bytes(tokens[0][2:-1], 'little')
        return number if number < 65536 else None
    except (StorageRuleError, ValueError):
        return None


def reviewed_direct_access_output_identity(parameter):
    """Recognize a qualified direct output without assigning an image Y alias."""
    if not isinstance(parameter, Mapping):
        return False
    identity, limits = parameter.get('device_identity'), parameter.get('range')
    return (parameter.get('type') == 'BOOL'
        and parameter.get('direction') == 'output'
        and parameter.get('passing') == 'device_reference'
        and parameter.get('occupied_bit_count') == 1
        and isinstance(identity, Mapping)
        and identity.get('kind') == 'direct_access_output' and identity.get('prefix') == 'DY'
        and isinstance(limits, Mapping) and limits.get('device_prefixes') == ['DY']
        and not parameter.get('word_device_layout'))


def reviewed_program_pointer_identity(parameter):
    """Recognize a sourced P-label type, independently of indexed forms.

    The caller must qualify the fact's source, CPU and interface. A POINTER
    identifies a program label rather than a word-sized memory allocation;
    an absent index policy does not prove either permission or prohibition.
    """
    if not isinstance(parameter, Mapping):
        return False
    limits = parameter.get('range')
    return (parameter.get('type') == 'POINTER'
        and parameter.get('direction') == 'input'
        and parameter.get('passing') == 'device_reference'
        and parameter.get('device_class') == 'P'
        and parameter.get('role') in {'branch_target', 'program_pointer'}
        and isinstance(limits, Mapping)
        and limits.get('device_prefixes') == ['P']
        and limits.get('literal_prefixes') == []
        and limits.get('address_forms') == []
        and ('index_modification' not in limits or type(limits['index_modification']) is bool)
        and not any(key in parameter for key in ('word_device_layout', 'occupied_bit_count', 'encoding')))


def reviewed_comment_device_identity(parameter):
    """Recognize a comment selector without assigning it a memory width.

    The caller qualifies the source, CPU and Native interface. The device name
    selects metadata; its stored numeric/bit value is not an input value.
    """
    if not isinstance(parameter, Mapping):
        return False
    limits = parameter.get('range')
    return (parameter.get('type') == 'DEVICE_REFERENCE'
        and parameter.get('direction') == 'input'
        and parameter.get('passing') == 'device_reference'
        and parameter.get('role') == 'comment_device_selector'
        and parameter.get('selector_kind') == 'device_comment_identity'
        and parameter.get('device_value_read') is False
        and isinstance(limits, Mapping) and limits.get('literal_prefixes') == []
        and not any(key in parameter for key in ('word_device_layout', 'occupied_bit_count', 'encoding')))


def reviewed_remote_device_identity(parameter):
    """Recognize a source-qualified device name in another CPU's namespace.

    The selected target CPU and its configured range remain separate context;
    the operand neither reads nor allocates local device memory.
    """
    if not isinstance(parameter, Mapping):
        return False
    target = parameter.get('selector_CPU_parameter')
    forms = parameter.get('accepted_source_forms')
    return (parameter.get('type') == 'DEVICE_REFERENCE'
        and parameter.get('direction') == 'input'
        and parameter.get('passing') == 'device_reference'
        and parameter.get('role') == 'remote_device_selector'
        and parameter.get('selector_kind') == 'remote_CPU_device_identity'
        and isinstance(target, str) and bool(target)
        and parameter.get('device_value_read') is False
        and parameter.get('local_memory_access') is False
        and parameter.get('remote_access') in {'read', 'write'}
        and isinstance(forms, list) and bool(forms)
        and set(forms) <= {'device_name', 'quoted_device_name'}
        and not any(key in parameter for key in ('word_device_layout', 'occupied_bit_count', 'encoding')))


def operand_numeric_bound(bound, values):
    """Resolve a literal range bound, retaining its explicit operand reference."""
    if type(bound) is int or type(bound) is float and math.isfinite(bound):
        return bound, None
    if isinstance(bound, Mapping) and set(bound) in ({'parameter_ref'}, {'parameter'}):
        name = next(iter(bound.values()))
        if isinstance(name, str) and name:
            value = values.get(name.upper())
            return (value if type(value) is int or type(value) is float and math.isfinite(value) else None), name
    return None, None


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


def _validated_definition_documents(rows):
    if not isinstance(rows, list):
        raise ValueError("Definition documents must be an array")
    result = {}
    for document in rows:
        if (not isinstance(document, Mapping) or not isinstance(document.get("id"), str)
                or not document['id'] or document['id'] in result):
            raise ValueError("Invalid or duplicate definition document")
        result[document['id']] = copy.deepcopy(document)
    return result


def _validated_language_features(rows):
    """Validate reviewed feature structure without synthesizing owner metadata."""
    from knowledge.runtime_fact_publication import validate_language_feature
    if not isinstance(rows, list):
        raise ValueError("Language features must be an array")
    result = {}
    for feature in rows:
        validate_language_feature(feature, published=False)
        target = feature['target']
        if target in result:
            raise ValueError("Duplicate language feature identity")
        for fact in feature.get('facts', []):
            InstructionFactGroup.from_mapping(fact)
        result[target] = copy.deepcopy(feature)
    return result


class _DefinitionOwnerView(Mapping):
    """Identity/header view with detached, on-demand technical contents."""
    __slots__ = ("_header", "_load")

    def __init__(self, header, load):
        self._header = dict(header)
        self._load = load

    def materialize(self):
        entry = self._load()
        if not isinstance(entry, Mapping):
            raise ValueError("Missing compiled instruction owner")
        return entry

    def __getitem__(self, key):
        if key in self._header:
            return copy.deepcopy(self._header[key])
        return self.materialize()[key]

    def __iter__(self):
        return iter(self.materialize())

    def __len__(self):
        return len(self.materialize())



class InstructionRegistry:
    """Immutable-by-convention lookup table for vendor instruction metadata."""

    def __init__(self, specs: Iterable[InstructionSpec] = ()) -> None:
        self._specs: Dict[Tuple[str, str], InstructionSpec] = {}
        self._cpu_contracts: Dict[Tuple[str, str, str], InstructionSpec] = {}
        self._source_forms: Dict[Tuple[str, str, int], InstructionSpec] = {}
        self._variant_index: Optional[
            Dict[Tuple[str, str], InstructionResolution]
        ] = None
        self.definition_inventory: Tuple[Mapping[str, Any], ...] = ()
        self._definition_owners: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
        self.definition_documents: Mapping[str, Mapping[str, Any]] = {}
        self._definition_owner_store = None
        self._loaded_definition_contracts = set()
        self._definition_contract_bases = {}
        self._knowledge_profiles = ()
        self._family_knowledge = ()
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
            models = dict(base.modifier_cpu_support).get(token)
            if models is not None:
                effective = replace(effective, cpu_support=frozenset(models))
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

    def resolve_catalog_form(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
        cpu: Optional[str] = None,
    ) -> Optional[InstructionResolution]:
        """Resolve registered metadata without materializing new fact owners."""
        normalized_vendor = str(vendor or "mitsubishi").strip().lower()
        token = str(mnemonic or "").strip().upper()
        key = (normalized_vendor, token)
        variant = self._ensure_variant_index().get(key)
        if variant is not None:
            effective = self._target_contract(normalized_vendor, token, cpu)
            return replace(variant, spec=effective) if effective is not None else variant
        exact = self._specs.get(key)
        if exact is None:
            return None
        return InstructionResolution(
            opcode=token,
            spec=self._target_contract(normalized_vendor, token, cpu) or exact,
            base_spec=exact,
        )

    def _target_contract(self, vendor, opcode, cpu):
        model = str(cpu or "").strip().upper()
        exact = self._cpu_contracts.get((vendor, opcode, model))
        if exact is not None or not model:
            return exact
        from plc.target_identity import target_identity_state
        members, reason = target_identity_state(model)
        if reason is not None or not members or len(members) < 2:
            return None
        contracts = []
        for member in members:
            current = self._cpu_contracts.get((vendor, opcode, member))
            if current is None:
                return None
            # An exact owner's lazily attached facts are not a group contract.
            contracts.append(replace(current, definition_facts=(), source_materials=(), uninterpreted_content=()))
        def signature(spec):
            return replace(spec, cpu_support=frozenset(), contract_sources=(), notes="")
        if any(signature(spec) != signature(contracts[0]) for spec in contracts[1:]):
            return None
        sources = []
        notes = []
        for spec in contracts:
            sources.extend(s for s in spec.contract_sources if s not in sources)
            if spec.notes and spec.notes not in notes:
                notes.append(spec.notes)
        return replace(contracts[0], cpu_support=frozenset(members), contract_sources=tuple(sources),
                       notes="\n\n".join(notes))

    def resolve_form(self, mnemonic: Any, *, vendor: str = "mitsubishi", cpu: Optional[str] = None,
                     operand_count: Optional[int] = None):
        """Resolve a call and materialize only its exact definition contract."""
        form = self.resolve_catalog_form(mnemonic, vendor=vendor, cpu=cpu)
        if form is None:
            return None
        if cpu:
            owner = self.definition_header(form.opcode, cpu=cpu, vendor=vendor)
            interfaces = (owner or {}).get('call_admission_details', {}).get('interfaces')
            if interfaces is not None and 'ladder' not in interfaces and 'native_ladder_instruction' not in interfaces:
                return None
        spec = self._with_definition_facts(form.spec, form.opcode, cpu,
                                           str(vendor or "mitsubishi").strip().lower())
        spec = spec.for_operand_count(operand_count)
        return replace(form, spec=spec) if spec is not form.spec else form

    @property
    def loaded_definition_owner_count(self):
        """Number of exact owners materialized into callable typed contracts."""
        return len(self._loaded_definition_contracts)

    def _with_definition_facts(self, spec, opcode, cpu, vendor):
        key = (vendor, opcode, str(cpu or "").strip().upper())
        owner = self._definition_owners.get(key)
        if owner is None or not spec.supports_cpu(cpu):
            return spec
        if not self.fact_bundle_current():
            raise ValueError("Fact definitions changed; reload the checking context")
        if key in self._loaded_definition_contracts:
            return spec
        entry = owner.materialize() if isinstance(owner, _DefinitionOwnerView) else owner
        facts = tuple(InstructionFactGroup.from_mapping(f) for f in entry.get("facts", []))
        if any(f.scope.get("models") != [entry['target_model']]
               or f.scope.get("forms") != [entry['opcode']] for f in facts):
            raise ValueError("Compiled facts must have exact CPU/form scopes")
        attached = replace(spec, definition_facts=facts,
                           source_materials=tuple(copy.deepcopy(entry.get("source_materials", []))),
                           uninterpreted_content=tuple(copy.deepcopy(entry.get("uninterpreted_content", []))))
        self._definition_contract_bases[key] = spec
        self._cpu_contracts[key] = attached
        self._loaded_definition_contracts.add(key)
        return attached

    def _clear_definition_contracts(self):
        # Restore only the prior definition fields: subsequent capability
        # overlays must not be lost when replacing/withdrawing a fact bundle.
        for key, base in self._definition_contract_bases.items():
            current = self._cpu_contracts.get(key)
            if current is not None:
                self._cpu_contracts[key] = replace(current, definition_facts=base.definition_facts,
                    source_materials=base.source_materials, uninterpreted_content=base.uninterpreted_content)
        self._definition_contract_bases.clear()
        self._loaded_definition_contracts.clear()

    def resolve(
        self,
        mnemonic: Any,
        *,
        vendor: str = "mitsubishi",
        cpu: Optional[str] = None,
        operand_count: Optional[int] = None,
    ) -> Optional[InstructionSpec]:
        resolved = self.resolve_form(mnemonic, vendor=vendor, cpu=cpu, operand_count=operand_count)
        return resolved.spec if resolved is not None else None

    def resolve_source_form(self, mnemonic, operand_count, *, cpu):
        """Read saved-source operand roles without admitting generated code.

        The saved CPU's lexical catalogue must also contain this exact arity.
        Manual roles do not certify CPU applicability, execution or hardware.
        """
        from plc.target_identity import target_family
        token = str(mnemonic or '').strip().upper()
        source = self._source_forms.get((target_family(cpu), token, operand_count))
        if source is not None and operand_count in source_instruction_arities(token, cpu):
            return source
        return self.resolve(token, cpu=cpu)

    def load_target_contracts(self, entries) -> None:
        """Build-time reviewed manual contracts, scoped to a literal CPU/form.

        Runtime retrieval cannot call this admission path. Existing CPU owners
        remain unchanged, including when another CPU uses a different signature.
        """
        if not isinstance(entries, list):
            raise ValueError("Target contracts must be an array")
        pending, additions = {}, {}
        dimensions = set(InstructionSpec("_").contract_coverage()) | {"st_call"}
        expanded = []
        for row in entries:
            if isinstance(row, Mapping) and "cpus" in row:
                models = row["cpus"]
                if ("cpu" in row or not isinstance(models, list) or not models
                        or any(not isinstance(m, str) for m in models) or len(set(models)) != len(models)
                        or set(row.get("instruction", {}).get("cpu_support", [])) != set(models)):
                    raise ValueError("Grouped target contract needs its exact CPU scope")
                sources = row.get("sources", [])
                if (not sources or any(not isinstance(s, Mapping) or "target_model" in s
                        or not isinstance(s.get("models"), list) or set(s["models"]) != set(models) for s in sources)):
                    raise ValueError("Grouped target contract sources need the same CPU scope")
                for model in models:
                    item = copy.deepcopy(dict(row))
                    item.pop("cpus")
                    item["cpu"] = model
                    item["instruction"]["cpu_support"] = [model]
                    for source in item["sources"]:
                        source.pop("models")
                        source["target_model"] = model
                    expanded.append(item)
            else:
                expanded.append(row)
        for row in expanded:
            if not isinstance(row, Mapping) or row.get("method") != "reviewed-manual-contract-v1":
                raise ValueError("Unsupported target contract source method")
            model = row.get("cpu")
            if not isinstance(model, str) or not re.fullmatch(r"(?:FX|Q|L)[0-9A-Z/-]+", model):
                raise ValueError("Target contract needs a literal uppercase CPU")
            spec = InstructionSpec.from_mapping(row.get("instruction") or {})
            key = (spec.vendor, spec.mnemonic, model)
            fields = row.get("verified_fields")
            sources = row.get("sources")
            if (key in pending or key in self._cpu_contracts or spec.cpu_support != {model}
                    or not isinstance(fields, list) or not fields or set(fields) - dimensions
                    or not isinstance(sources, list) or not sources):
                raise ValueError("Invalid/duplicate target contract owner")
            for source in sources:
                if (not isinstance(source, Mapping) or source.get("target_model") != model
                        or any(not isinstance(source.get(k), str) or not source[k]
                               for k in ("manual_id", "manual", "revision", "url"))
                        or type(source.get("pdf_page")) is not int or source["pdf_page"] < 1):
                    raise ValueError("Target contract needs exact source/page/CPU evidence")
            order = row.get("native_order", [])
            if spec.native_overloads:
                if order:
                    raise ValueError("Overloaded target contract cannot choose a default native order")
            elif (not isinstance(order, list) or len(order) != len(spec.operands)
                    or any(not isinstance(x, str) or not x for x in order)
                    or spec.min_operands != len(order) or spec.max_operands != len(order)):
                raise ValueError("Target contract needs an exact native signature")
            execution = row.get("execution_form")
            if execution not in {"continuous", "pulse"}:
                raise ValueError("Invalid source execution form")
            call = row.get("st_call")
            if call is None and "st_call" not in fields:
                pass  # A reviewed native signature does not imply an ST call.
            elif (not isinstance(call, Mapping) or set(call) != {"name", "enable", "returns"}
                    or not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(call.get("name", "")))
                    or type(call.get("enable")) is not bool or call.get("returns") not in {"bit", "word", "void"}):
                raise ValueError("Invalid source ST call form")
            boundaries = tuple(NumericOperandBoundary(**item) for item in row.get("numeric_boundaries", []))
            if any(not 0 <= b.operand_index < len(order) for b in boundaries):
                raise ValueError("Invalid target operand boundary")
            evidence = tuple({**copy.deepcopy(s), "verified_fields": list(fields)} for s in sources)
            spec = replace(spec, verified_fields=tuple(fields), native_operand_order=tuple(order),
                           execution_form=execution, contract_sources=evidence,
                           numeric_operand_boundaries=boundaries, st_call=copy.deepcopy(dict(call)) if call else None)
            pending[key] = spec
            default_key = (spec.vendor, spec.mnemonic)
            if self.resolve_form(spec.mnemonic, vendor=spec.vendor) is None:
                additions.setdefault(default_key, spec)
        for spec in additions.values():
            self.register(spec)
        self._cpu_contracts.update(pending)

    def resolve_st_call(self, name, *, cpu):
        token = str(name or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", token):
            return None
        for opcode in self.known_mnemonics():
            form = self.resolve_form(opcode, cpu=cpu)
            if form and form.spec.supports_cpu(cpu) and form.spec.st_call and form.spec.st_call["name"] == token:
                return form
        form = self.resolve_form(token, cpu=cpu)
        if form is None or not form.spec.supports_cpu(cpu) or form.spec.st_call and form.spec.st_call["name"] != token:
            return None
        return form

    def load_instruction_definitions(self, path: Path) -> None:
        """Attach reproducible source facts to exact existing CPU/form owners.

        The inventory also includes manual-only and unsupported entries. Those
        remain inspectable inventory records, without registering them as calls.
        """
        before = path.stat()
        if path.suffix == ".gz":
            import gzip
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        from plc.instruction_definition_storage import expand_definitions
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            payload = expand_definitions(payload)
        from knowledge.runtime_fact_projection import SOURCE_SERIALIZATION, VALUE_SERIALIZATION, VerifiedCatalog, resolve_fact_sources
        pooled = payload.get("source_serialization")
        if ("source_serialization" in payload or "source_catalog" in payload) and pooled != SOURCE_SERIALIZATION:
            raise ValueError("Unsupported fact source serialization")
        if pooled and not isinstance(payload.get("source_catalog"), Mapping):
            raise ValueError("Invalid fact source catalog")
        if ("value_catalog" in payload or "value_serialization" in payload) and (
                not pooled or payload.get("value_serialization") != VALUE_SERIALIZATION
                or not isinstance(payload.get("value_catalog"), Mapping)):
            raise ValueError("Unsupported fact value serialization")
        if (not isinstance(payload, Mapping) or payload.get("schema_version") != 1
                or payload.get("method") != "instruction-material-compiler-v1"
                or not isinstance(payload.get("entries"), list)):
            raise ValueError("Unsupported compiled instruction definitions")
        # A load-owned immutable snapshot validates each unique object once.
        # No mutable input catalog or process-global trust cache is reused.
        source_catalog = VerifiedCatalog(payload.pop("source_catalog")) if pooled else None
        value_catalog = VerifiedCatalog(payload.pop("value_catalog")) if pooled and "value_catalog" in payload else None
        inventory, seen = [], set()
        for entry in payload["entries"]:
            if not isinstance(entry, Mapping):
                raise ValueError("Invalid compiled instruction entry")
            opcode, model = entry.get("opcode"), entry.get("target_model")
            vendor = entry.get("vendor", "mitsubishi")
            if any(not isinstance(value, str) or not value for value in (opcode, model, vendor)):
                raise ValueError("Compiled entry needs a literal form and model")
            key = (vendor.lower(), opcode.upper(), model.upper())
            if key in seen:
                raise ValueError("Duplicate compiled instruction owner")
            seen.add(key)
            if not pooled and any("source_refs" in f or "value_ref" in f for f in entry.get("facts", [])):
                raise ValueError("Pooled fact references require a declared transport")
            for raw in entry.get("facts", []):
                fact = resolve_fact_sources(raw, source_catalog, value_catalog) if pooled else raw
                parsed = InstructionFactGroup.from_mapping(fact)
                if parsed.scope.get("models") != [model] or parsed.scope.get("forms") != [opcode]:
                    raise ValueError("Compiled facts must have exact CPU/form scopes")
            materials, uninterpreted = entry.get("source_materials", []), entry.get("uninterpreted_content", [])
            if any(not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items)
                   for items in (materials, uninterpreted)):
                raise ValueError("Invalid compiled source processing receipt")
            if pooled:
                def load_owner(raw_entry=entry):
                    result = copy.deepcopy({k: v for k, v in raw_entry.items() if k != "facts"})
                    result["facts"] = [resolve_fact_sources(f, source_catalog, value_catalog)
                                       for f in raw_entry.get("facts", [])]
                    return result
                header = {k: entry[k] for k in ("vendor", "opcode", "target_model",
                          "call_available", "registry_member", "support_status",
                          "call_admission_reason", "call_admission_details") if k in entry}
                inventory.append(_DefinitionOwnerView(header, load_owner))
            else:
                inventory.append(copy.deepcopy(dict(entry)))
        features = _validated_language_features(payload.get('language_facts', []))
        documents = _validated_definition_documents(payload.get("documents", []))
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError('Fact definitions changed while loading')
        self._clear_definition_contracts()
        if self._definition_owner_store is not None:
            self._definition_owner_store.close()
        self._definition_owner_store = None
        self.definition_inventory = tuple(inventory)
        self._definition_owners = {(e.get('vendor', 'mitsubishi').lower(), e['opcode'].upper(), e['target_model'].upper()): e
                                   for e in inventory}
        self.definition_documents = documents
        self.language_facts = tuple(features.values())
        self._language_features = features
        self.fact_bundle_version = payload.get('fact_bundle_version')
        self._fact_resource = (path.resolve(), after.st_mtime_ns, after.st_size)

    def load_instruction_owner_store(self, path: Path) -> None:
        """Load an indexed publication manifest without expanding owner facts."""
        from plc.instruction_owner_store import OwnerDefinitionStore
        store = OwnerDefinitionStore(path)
        try:
            metadata = store.definition_metadata
            inventory, owners = [], {}
            for header in store.iter_owner_headers():
                vendor, opcode, model = header.get("vendor", "mitsubishi"), header.get("opcode"), header.get("target_model")
                if any(not isinstance(value, str) or not value for value in (vendor, opcode, model)):
                    raise ValueError("Compiled entry needs a literal form and model")
                key = (vendor.lower(), opcode.upper(), model.upper())
                if key in owners:
                    raise ValueError("Duplicate compiled instruction owner")
                view = _DefinitionOwnerView(header, lambda key=key: store.load_owner(key))
                owners[key] = view
                inventory.append(view)
            features = {header['target']: _DefinitionOwnerView(header,
                lambda target=header['target']: store.language_feature(target))
                for header in store.iter_language_headers()}
            documents = _validated_definition_documents(metadata.get("documents", []))
            if not store.current():
                raise ValueError("Fact definitions changed while loading")
        except Exception:
            store.close()
            raise
        self._clear_definition_contracts()
        if self._definition_owner_store is not None:
            self._definition_owner_store.close()
        self._definition_owner_store = store
        self.definition_inventory = tuple(inventory)
        self._definition_owners = owners
        self.definition_documents = documents
        self.language_facts = tuple(features.values())
        self._language_features = features
        self.fact_bundle_version = metadata.get("fact_bundle_version")
        self._fact_resource = None

    def selected_owner_database_path(self):
        """Return the startup-selected, authenticated owner DB without rehashing.

        A selected but withdrawn pair is an error, never permission to consult
        a different installed instruction corpus.
        """
        store = self._definition_owner_store
        if store is None:
            return None
        if not store.current():
            raise ValueError('Selected instruction owner bundle changed; reload the checking context')
        return store.database_path

    def fact_bundle_current(self):
        if self._definition_owner_store is not None:
            return self._definition_owner_store.current()
        bound = getattr(self, '_fact_resource', None)
        if bound is None:
            return True  # An explicitly constructed synthetic Registry.
        try:
            current = bound[0].stat()
            return (current.st_mtime_ns, current.st_size) == bound[1:]
        except OSError:
            return False

    def language_feature(self, target, *, language=None, cpu=None):
        """Inspect scoped language knowledge; never admit a related instruction.

        Missing owner language stays missing. Explicit language filtering uses
        the validated fact scopes, not a newly synthesized metadata field.
        CPU filtering is literal and cannot widen a feature, fact or reference.
        """
        feature = getattr(self, '_language_features', {}).get(str(target).upper())
        if feature is None:
            return None
        if isinstance(feature, _DefinitionOwnerView):
            feature = feature.materialize()
        if language is not None:
            languages = ({feature['language']} if 'language' in feature else
                         {item for fact in feature.get('facts', [])
                          for item in fact.get('scope', {}).get('languages', [])})
            if str(language).lower() not in languages:
                return None
        result = copy.deepcopy(feature)
        if cpu is not None:
            model = str(cpu).strip().upper()
            upper = set(feature.get('models', [])) if 'models' in feature else {
                item for fact in feature.get('facts', []) for item in fact.get('scope', {}).get('models', [])}
            if model not in upper:
                return None
            result['facts'] = [fact for fact in result.get('facts', [])
                               if model in fact.get('scope', {}).get('models', [])]
            if 'references' in result:
                result['references'] = [ref for ref in result['references'] if model in ref.get('models', [])]
        return result

    def resolve_fact_form(self, mnemonic, *, cpu=None):
        """Identify a catalogue owner without admitting a callable instruction."""
        form = self.resolve_form(mnemonic, cpu=cpu)
        if form is not None:
            return form
        token = str(mnemonic or '').strip().upper()
        owners = getattr(self, '_definition_owners', {})
        if any(opcode == token and (cpu is None or model == str(cpu).upper())
               for (_, opcode, model) in owners):
            from types import SimpleNamespace
            return SimpleNamespace(opcode=token, base_mnemonic=token)
        return None

    def instruction_knowledge(self, mnemonic, *, cpu, language=None, facts=None, context=None):
        """Compose documented family knowledge without changing call admission."""
        from plc.instruction_knowledge import compose_instruction_knowledge
        return compose_instruction_knowledge(self, mnemonic, cpu=cpu, language=language, facts=facts, context=context)

    def definition_header(self, opcode, *, cpu, vendor="mitsubishi"):
        """Inspect admission metadata without reading technical owner facts."""
        key = (str(vendor).lower(), str(opcode).strip().upper(), str(cpu).strip().upper())
        entry = self._definition_owners.get(key)
        if entry is None:
            return None
        if not self.fact_bundle_current():
            raise ValueError("Fact definitions changed; reload the checking context")
        if self._definition_owner_store is not None:
            return self._definition_owner_store.get_owner_header(key)
        if isinstance(entry, _DefinitionOwnerView):
            return copy.deepcopy(entry._header)
        return {k: copy.deepcopy(entry[k]) for k in ("vendor", "opcode", "target_model",
                "call_available", "registry_member", "support_status",
                "call_admission_reason", "call_admission_details") if k in entry}

    def definition_entry(self, opcode, *, cpu, vendor='mitsubishi'):
        """Inspect an exact compiled owner without admitting it as a call."""
        entry = self._definition_owners.get((str(vendor).lower(), str(opcode).strip().upper(), str(cpu).strip().upper()))
        if isinstance(entry, _DefinitionOwnerView):
            return entry.materialize()
        return copy.deepcopy(dict(entry)) if entry is not None else None

    def expression_entry(self, token, *, cpu, vendor='mitsubishi'):
        """Resolve an exact expression owner, then a unique declared token alias."""
        token, model, vendor = str(token).strip().upper(), str(cpu).strip().upper(), str(vendor).lower()

        def admitted(header):
            details = (header or {}).get('call_admission_details') or {}
            marker = details.get('structured_expression') or {}
            return (header and header.get('call_available') is True
                and 'st' in details.get('interfaces', [])
                and marker.get('available') is True and marker.get('call_kind') == 'expression'
                and marker.get('operator', header.get('opcode')) == token
                and marker.get('owner_opcode', header.get('opcode')) == header.get('opcode'))

        direct = self.definition_header(token, cpu=model, vendor=vendor)
        if admitted(direct):
            return self.definition_entry(token, cpu=model, vendor=vendor)
        if self._definition_owner_store is not None:
            keys = self._definition_owner_store.expression_owner_keys(token, cpu=model, vendor=vendor)
        else:
            keys = [key for key, entry in self._definition_owners.items()
                    if key[0] == vendor and key[2] == model and admitted(entry)]
        if len(keys) != 1:
            return None
        key = keys[0]
        if not admitted(self.definition_header(key[1], cpu=model, vendor=vendor)):
            return None
        return self.definition_entry(key[1], cpu=model, vendor=vendor)

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
            if digest and (not isinstance(digest, str) or len(digest) != 64
                           or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("Promotion source hash malformed")
            # A digest is development audit material. Its absence lowers the recorded
            # evidence grade, it does not reject the reviewed contract.
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
                if type(page) is not int or page < 1:
                    raise ValueError("Promotion page missing")
                if proof and (not isinstance(proof, str) or len(proof) != 64
                              or any(c not in "0123456789abcdef" for c in proof)):
                    raise ValueError("Promotion proof malformed")
                evidence.append({"manual_id": manual, "manual": sources[manual]["manual"],
                                 "revision": sources[manual]["revision"], "pdf_page": page,
                                 "source_sha256": sources[manual].get("sha256"),
                                 "proof_sha256": proof or None,
                                 "proof_status": "recorded" if proof else "not_recorded"})
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

    def load_operand_semantic_promotions(self, path: Path) -> None:
        """Overlay source-verified operand roles/types for one exact CPU/form.

        The signature promotion must already have verified arity/order. This
        loader therefore cannot create instructions, change arity, or invent an
        operand position.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("cpu") != "FX3U"
            or payload.get("method") != "signature-gated-native-operands-v1"
            or not isinstance(payload.get("entries"), list)
        ):
            raise ValueError("Unsupported operand-semantic promotion ledger")

        signature_digest = payload.get("signature_ledger_sha256")
        source = payload.get("source")
        digest = str(source.get("sha256")) if isinstance(source, Mapping) else ""
        if (
            (signature_digest and (not isinstance(signature_digest, str)
                                   or len(signature_digest) != 64
                                   or any(c not in "0123456789abcdef" for c in signature_digest)))
            or not isinstance(source, Mapping)
            or any(
                not isinstance(source.get(key), str) or not source.get(key)
                for key in ("manual", "revision", "url")
            )
            or (digest and (len(digest) != 64
                            or any(c not in "0123456789abcdef" for c in digest)))
        ):
            raise ValueError("Operand-semantic source identity missing")

        pending = {}
        for row in payload["entries"]:
            if not isinstance(row, Mapping):
                raise ValueError("Invalid operand-semantic promotion entry")
            opcode = str(row.get("form") or "").strip().upper()
            key = ("mitsubishi", opcode, "FX3U")
            if key in pending:
                raise ValueError("Duplicate operand-semantic promotion: " + opcode)
            current = self._cpu_contracts.get(key)
            if current is None:
                raise ValueError(
                    "Operand semantics require an existing signature promotion: "
                    + opcode
                )
            order = row.get("native_order")
            roles = row.get("operand_roles")
            verified = row.get("verified_fields")
            if (
                not isinstance(order, list)
                or tuple(order) != current.native_operand_order
                or not isinstance(roles, list)
                or len(roles) != len(current.operands)
                or not isinstance(verified, list)
                or "operand_roles" not in verified
                or len(set(verified)) != len(verified)
            ):
                raise ValueError("Invalid operand-semantic shape: " + opcode)
            try:
                role_values = tuple(OperandRole(str(value)) for value in roles)
            except ValueError as exc:
                raise ValueError("Invalid operand role in " + opcode) from exc

            types = row.get("operand_types")
            if types is not None:
                if (
                    not isinstance(types, list)
                    or len(types) != len(current.operands)
                    or "operand_types" not in verified
                    or any(not isinstance(value, str) or not value for value in types)
                ):
                    raise ValueError("Invalid operand types in " + opcode)
            elif "operand_types" in verified:
                raise ValueError("Verified operand types missing for " + opcode)

            # Promotion is corroboration only: never overwrite a conflicting
            # declared semantic. A conflict belongs in the audit report.
            for index, (operand, role) in enumerate(zip(current.operands, role_values)):
                if operand.role != role:
                    raise ValueError(
                        f"Operand role conflicts with registry {opcode}[{index}]"
                    )
                if types is not None and operand.data_type != types[index]:
                    raise ValueError(
                        f"Operand type conflicts with registry {opcode}[{index}]"
                    )

            proof = row.get("operand_proof")
            evidence = {
                "manual_id": "fx3_programming_r",
                "manual": source["manual"],
                "revision": source["revision"],
                "source_sha256": source.get("sha256"),
                "pdf_page": row.get("native_page"),
                "operand_page_start": row.get("operand_page_start"),
                "operand_page_end": row.get("operand_page_end"),
                "proof_sha256": proof or None,
                "proof_status": "recorded" if proof else "not_recorded",
                "method": payload.get("method"),
            }
            if (
                type(evidence["pdf_page"]) is not int
                or type(evidence["operand_page_start"]) is not int
                or type(evidence["operand_page_end"]) is not int
                or evidence["operand_page_start"] > evidence["pdf_page"]
                or evidence["pdf_page"] > evidence["operand_page_end"]
                or (proof and (not isinstance(proof, str) or len(proof) != 64
                               or any(c not in "0123456789abcdef" for c in proof)))
            ):
                raise ValueError("Operand-semantic evidence missing for " + opcode)

            merged_verified = list(current.verified_fields)
            for field_name in verified:
                if field_name not in {"operand_roles", "operand_types"}:
                    raise ValueError("Unsupported operand-semantic dimension")
                if field_name not in merged_verified:
                    merged_verified.append(field_name)
            pending[key] = replace(
                current,
                verified_fields=tuple(merged_verified),
                contract_sources=tuple(current.contract_sources) + (evidence,),
            )

        self._cpu_contracts.update(pending)

    def load_capability_contract(self, path: Path) -> None:
        """Overlay source-backed instruction semantics for one exact CPU/form."""

        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("entries"), list)
        ):
            raise ValueError("Unsupported instruction capability contract")
        vendor = str(payload.get("vendor") or "mitsubishi").strip().lower()
        allowed_verified = {
            "cpu_applicability",
            "hardware_applicability",
            "completion_ownership",
            "numeric_and_memory_boundaries",
        }

        def optional_index(value, label):
            if value is None:
                return None
            if type(value) is not int or value < 0:
                raise ValueError(f"Invalid {label}")
            return value

        for row in payload["entries"]:
            if not isinstance(row, Mapping):
                raise ValueError("Invalid instruction capability entry")
            cpu = str(row.get("cpu") or "").strip().upper()
            forms = row.get("forms")
            if not cpu or not isinstance(forms, list) or not forms:
                raise ValueError("Capability entry requires cpu and forms")
            completion = None
            raw_completion = row.get("completion")
            if raw_completion is not None:
                if not isinstance(raw_completion, Mapping):
                    raise ValueError("Invalid completion semantics")
                device = str(raw_completion.get("device") or "").strip().upper()
                placement = str(
                    raw_completion.get("placement") or "same_rung_parallel_branch"
                ).strip()
                if not device or placement != "same_rung_parallel_branch":
                    raise ValueError("Unsupported completion semantics")
                completion = CompletionSemantics(device=device, placement=placement)

            pulse_output = None
            raw_pulse = row.get("pulse_output")
            if raw_pulse is not None:
                if not isinstance(raw_pulse, Mapping):
                    raise ValueError("Invalid pulse-output semantics")
                frequencies = raw_pulse.get("frequency_operand_indexes") or []
                if (
                    not isinstance(frequencies, list)
                    or any(type(value) is not int or value < 0 for value in frequencies)
                ):
                    raise ValueError("Invalid pulse frequency indexes")
                output_index = optional_index(
                    raw_pulse.get("pulse_output_operand_index"),
                    "pulse output operand index",
                )
                if output_index is None:
                    raise ValueError("Pulse output operand index is required")
                pulse_output = PulseOutputSemantics(
                    frequency_operand_indexes=tuple(frequencies),
                    pulse_output_operand_index=output_index,
                    direction_output_operand_index=optional_index(
                        raw_pulse.get("direction_output_operand_index"),
                        "direction output operand index",
                    ),
                )

            numeric = []
            for boundary in row.get("numeric_operand_boundaries") or []:
                if not isinstance(boundary, Mapping):
                    raise ValueError("Invalid numeric operand boundary")
                operand_index = optional_index(
                    boundary.get("operand_index"), "numeric operand index"
                )
                if operand_index is None:
                    raise ValueError("Numeric operand index is required")
                minimum = boundary.get("minimum")
                maximum = boundary.get("maximum")
                if minimum is not None and type(minimum) is not int:
                    raise ValueError("Invalid numeric minimum")
                if maximum is not None and type(maximum) is not int:
                    raise ValueError("Invalid numeric maximum")
                numeric.append(
                    NumericOperandBoundary(
                        operand_index=operand_index,
                        minimum=minimum,
                        maximum=maximum,
                        absolute=bool(boundary.get("absolute", False)),
                        unit=str(boundary.get("unit") or ""),
                        label=str(boundary.get("label") or "operand"),
                    )
                )

            disjoint = []
            for boundary in row.get("disjoint_bit_ranges") or []:
                if not isinstance(boundary, Mapping):
                    raise ValueError("Invalid disjoint bit-range boundary")
                indexes = [
                    optional_index(boundary.get(name), name)
                    for name in (
                        "source_operand_index",
                        "destination_operand_index",
                        "destination_length_operand_index",
                        "source_length_operand_index",
                    )
                ]
                if any(value is None for value in indexes):
                    raise ValueError("Disjoint bit-range indexes are required")
                disjoint.append(
                    DisjointBitRangeBoundary(
                        source_operand_index=indexes[0],
                        destination_operand_index=indexes[1],
                        destination_length_operand_index=indexes[2],
                        source_length_operand_index=indexes[3],
                        same_device_prefix_only=bool(
                            boundary.get("same_device_prefix_only", True)
                        ),
                        error_code=str(boundary.get("error_code") or ""),
                    )
                )

            replacement_opcode = str(row.get("unsupported_replacement") or "").strip().upper()
            verified = row.get("verified_fields") or []
            if (
                not isinstance(verified, list)
                or any(item not in allowed_verified for item in verified)
            ):
                raise ValueError("Invalid capability verified_fields")
            sources = row.get("sources") or []
            if not isinstance(sources, list) or any(
                not isinstance(item, Mapping) for item in sources
            ):
                raise ValueError("Invalid capability sources")

            for raw_opcode in forms:
                opcode = str(raw_opcode or "").strip().upper()
                if not opcode or opcode != raw_opcode:
                    raise ValueError("Invalid capability opcode")
                base = self.resolve(opcode, cpu=cpu) or self.resolve(opcode)
                if base is None:
                    raise ValueError("Unknown capability target: " + opcode)
                key = (vendor, opcode, cpu)
                current = self._cpu_contracts.get(key, base)
                replacements = dict(current.cpu_replacements)
                if replacement_opcode:
                    replacements[cpu] = replacement_opcode
                merged_sources = [
                    *current.contract_sources,
                    *({**dict(source), "verified_fields": list(verified)} for source in sources),
                ]
                deduped_sources = []
                seen_sources = set()
                for source in merged_sources:
                    marker = json.dumps(dict(source), ensure_ascii=False, sort_keys=True)
                    if marker not in seen_sources:
                        seen_sources.add(marker)
                        deduped_sources.append(dict(source))
                self._cpu_contracts[key] = replace(
                    current,
                    mnemonic=opcode,
                    completion=completion if raw_completion is not None else current.completion,
                    pulse_output=pulse_output if raw_pulse is not None else current.pulse_output,
                    numeric_operand_boundaries=(
                        tuple(numeric) if "numeric_operand_boundaries" in row
                        else current.numeric_operand_boundaries
                    ),
                    disjoint_bit_ranges=(
                        tuple(disjoint) if "disjoint_bit_ranges" in row
                        else current.disjoint_bit_ranges
                    ),
                    cpu_replacements=tuple(sorted(replacements.items())),
                    verified_fields=tuple(dict.fromkeys([
                        *current.verified_fields, *verified
                    ])),
                    contract_sources=tuple(deduped_sources),
                )

    def _describe_reviewed_st_call(self, mnemonic: Any, *, cpu: str) -> Dict[str, Any]:
        entry = self.definition_entry(mnemonic, cpu=cpu)
        details = (entry or {}).get('call_admission_details') or {}
        marker = details.get('structured_call') or {}
        if (not entry or entry.get('call_available') is not True
                or 'st' not in details.get('interfaces', [])
                or not (entry.get('support_status') == 'reviewed_st_signature'
                        or marker.get('available') is True)):
            return {}
        from plc.structured_call_contract import resolve_structured_call_contract
        resolved = resolve_structured_call_contract(entry.get('facts', []),
            opcode=entry['opcode'], model=cpu)
        if not resolved.accepted:
            return {}
        contract = resolved.contract
        if contract.instance_required and marker.get('instance_interface_verified') is not True:
            return {}
        parameters = [{**copy.deepcopy(p),
            'role': {'input': 'read', 'output': 'write', 'in_out': 'read_write'}[p['direction']]}
            for p in contract.parameters]
        call = {'name': contract.name, 'returns': contract.returns,
            'return_type_alternatives': list(contract.return_type_alternatives),
            'enable': contract.enable, 'instance_required': contract.instance_required,
            'kind': contract.call_kind}
        if contract.variadic_parameter_name is not None:
            call.update(variadic_parameter_name=contract.variadic_parameter_name,
                minimum_variadic_arguments=contract.minimum_variadic_arguments,
                maximum_variadic_arguments=contract.maximum_variadic_arguments,
                argument_binding='positional')
        if contract.return_binding:
            call['return_binding'] = copy.deepcopy(dict(contract.return_binding))
        if contract.instance_required:
            call.update(instance_type=contract.opcode, argument_binding='named',
                        return_role='no_return_value',
                        instance_interface_verified=True,
                        instance_interface_source=copy.deepcopy(marker.get('instance_interface_source')))
        if contract.call_kind == 'statement':
            call['return_role'] = contract.return_role
            call['execution_outputs'] = copy.deepcopy(list(contract.execution_outputs))
        result = {'target_model': cpu, 'opcode': contract.opcode,
            'interfaces': list(details['interfaces']), 'st_contract_verified': True,
            'st_signature': contract.name + '(' + ', '.join(p['name'] for p in parameters) + ')',
            'st_parameters': parameters, 'st_call': call,
            'applicability_cases': copy.deepcopy(list(contract.conditions)),
            'conditions_evaluated': False, 'source_contract_fact_ids': list(contract.fact_ids),
            'contract_sources': copy.deepcopy(list(contract.sources))}
        if contract.instance_required:
            result['st_signature'] = '<declared_instance>(' + ', '.join(
                p['name'] + ' := <' + p['name'] + '>' for p in parameters) + ')'
        constraints = [{key: copy.deepcopy(fact[key]) for key in ('id', 'dimension', 'value', 'conditions')
                        if key in fact}
                       for fact in contract.facts if fact['dimension'] in {
                           'constraints.numeric_ranges', 'constraints.documented_requirements'}]
        if constraints:
            result['constraints'] = constraints
        return result

    def _describe_reviewed_st_expression(self, operator: Any, *, cpu: str) -> Dict[str, Any]:
        entry = self.definition_entry(operator, cpu=cpu)
        details = (entry or {}).get('call_admission_details') or {}
        if (details.get('structured_expression') or {}).get('available') is not True:
            entry = self.expression_entry(operator, cpu=cpu)
            details = (entry or {}).get('call_admission_details') or {}
        marker = details.get('structured_expression') or {}
        if (not entry or entry.get('call_available') is not True
                or 'st' not in details.get('interfaces', [])
                or marker.get('available') is not True
                or marker.get('call_kind') != 'expression'):
            return {}
        from plc.structured_expression_contract import resolve_structured_expression_contract
        feature = self.language_feature('ST.OPERATORS', language='st', cpu=cpu)
        if feature is None:
            return {}
        resolved = resolve_structured_expression_contract(entry.get('facts', []),
            operator=marker.get('operator', entry['opcode']), model=cpu,
            owner_opcode=entry['opcode'],
            language_feature=feature)
        if not resolved.accepted:
            return {}
        contract = resolved.contract
        expression = {'operator': contract.operator, 'representation': contract.representation,
            'owner_opcode': contract.owner_opcode or entry['opcode'],
            'operator_form': contract.operator_form, 'variadic': contract.variadic,
            'operands': [{'name': name, 'type': data_type}
                         for name, data_type in zip(contract.operand_names, contract.operand_types)],
            'minimum_operands': contract.minimum_operands,
            'maximum_operands': contract.maximum_operands,
            'result_type': contract.result_type,
            'operator_priority': contract.operator_priority,
            'same_priority_evaluation': contract.same_priority_evaluation,
            'associativity': contract.associativity,
            'grammar_fact_id': contract.grammar_fact_id}
        if contract.comparison_chain_intermediate_result_type is not None:
            expression.update(comparison_chain_intermediate_result_type=
                contract.comparison_chain_intermediate_result_type,
                comparison_operand_type_compatibility=contract.comparison_operand_type_compatibility)
        result = {'target_model': cpu, 'opcode': contract.owner_opcode or entry['opcode'],
            'interfaces': ['st'], 'call_kind': 'expression', 'st_expression': expression,
            'st_expression_verified': True,
            'applicability_cases': copy.deepcopy(list(contract.conditions)),
            'conditions_evaluated': False,
            'source_contract_fact_ids': list(contract.fact_ids),
            'grammar_fact_id': contract.grammar_fact_id,
            'contract_sources': copy.deepcopy(list(contract.sources))}
        return result

    @staticmethod
    def _include_expression_contract(call, expression):
        if not expression:
            return call
        if not call:
            return expression
        return {**call, 'interfaces': list(dict.fromkeys([*call.get('interfaces', []), 'st'])),
                'expression_contract': expression}

    def _describe_reviewed_fbd_call(self, mnemonic: Any, *, cpu: str) -> Dict[str, Any]:
        entry = self.definition_entry(mnemonic, cpu=cpu)
        details = (entry or {}).get('call_admission_details') or {}
        marker = details.get('fbd_call') or {}
        if (not entry or entry.get('call_available') is not True
                or 'fbd' not in details.get('interfaces', [])
                or marker.get('available') is not True
                or marker.get('call_kind') != 'instruction_block'
                or marker.get('graphical_interface_verified') is not True):
            return {}
        from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
        resolved = resolve_fbd_instruction_block_contract(entry, opcode=entry['opcode'], model=cpu)
        if not resolved.accepted:
            return {}
        contract = resolved.contract
        fbd = {'call_kind': 'instruction_block', 'representation': 'fbd_instruction_block',
               'pins': copy.deepcopy(list(contract.pins)),
               'conditions': copy.deepcopy(list(contract.conditions)),
               'conditions_evaluated': False,
               'source_contract_fact_ids': list(contract.fact_ids),
               'contract_sources': copy.deepcopy(list(contract.sources)),
               'graphical_interface_verified': True}
        if contract.operation is not None:
            fbd['operation'] = copy.deepcopy(dict(contract.operation))
        if marker.get('graphical_interface_source'):
            fbd['graphical_interface_source'] = copy.deepcopy(marker['graphical_interface_source'])
        if contract.conditions:
            fbd['applicability'] = 'conditional'
        return {'target_model': cpu, 'opcode': entry['opcode'], 'interfaces': ['fbd'],
                'call_kind': 'instruction_block', 'fbd_contract_verified': True,
                'fbd_contract': fbd}

    @staticmethod
    def _include_fbd_contract(call, fbd):
        if not fbd:
            return call
        if not call:
            return fbd
        interfaces = call.get('interfaces')
        if interfaces is None:
            interfaces = (['ladder'] if call.get('ladder_signature') else []) + (
                ['st'] if call.get('st_signature') else [])
        return {**call, 'interfaces': list(dict.fromkeys([*interfaces, 'fbd'])),
                'fbd_contract_verified': True, 'fbd_contract': fbd['fbd_contract']}

    def describe_call(self, mnemonic: Any, *, cpu: str) -> Dict[str, Any]:
        return self._include_fbd_contract(self._describe_call_without_fbd(mnemonic, cpu=cpu),
                                         self._describe_reviewed_fbd_call(mnemonic, cpu=cpu))

    def _describe_call_without_fbd(self, mnemonic: Any, *, cpu: str) -> Dict[str, Any]:
        """Source-bound language signatures; never infer ST arity from IL.

        This projection is shared by discovery and fact rendering. Parameter
        names are declarations, not a generated solution for a user request.
        """
        expression = self._describe_reviewed_st_expression(mnemonic, cpu=cpu)
        structured = self._describe_reviewed_st_call(mnemonic, cpu=cpu)
        form = self.resolve_form(mnemonic, cpu=cpu)
        if form is None or not form.spec.supports_cpu(cpu):
            return self._include_expression_contract(structured, expression)
        spec = form.spec
        if not spec.contract_sources:
            return self._include_expression_contract(structured, expression)
        if not spec.st_call:
            owner = self.definition_header(form.opcode, cpu=cpu) or {}
            if (owner.get('call_available') is not True
                    or owner.get('support_status') != 'reviewed_native_signature'):
                return self._include_expression_contract(structured, expression)
        parameters = [{"name": name, **operand.slot_metadata(index, name),
                       "role": operand.role.value, "type": operand.data_type,
                       **({"word_count": operand.word_count} if operand.word_count != 1 else {}),
                       **({"bit_count": operand.bit_count} if operand.bit_count != 1 else {}),
                       **({"integer_literal_widths": dict(operand.integer_literal_widths)} if operand.integer_literal_widths else {}),
                       **({"address_forms": list(operand.address_forms)} if operand.address_forms else {}),
                       **({"address_form_limits": {form: dict(limits) for form, limits in operand.address_form_limits}}
                          if operand.address_form_limits else {}),
                       **({"allowed_devices": list(operand.allowed_devices)} if operand.allowed_devices else {})}
                      for index, (name, operand) in enumerate(zip(spec.native_operand_order, spec.operands), start=1)]
        if not spec.st_call:
            for parameter, operand in zip(parameters, spec.operands):
                parameter.update(device_prefixes=list(operand.device_prefixes),
                                 digit_groups_max=operand.digit_groups_max,
                                 optional=operand.optional)
            result = {"target_model": cpu, "opcode": form.opcode,
                "call_kind": "instruction", "representation": "native_ladder_instruction",
                "interfaces": ["ladder"], "native_contract_verified": True,
                "ladder_signature": " ".join([form.opcode, *spec.native_operand_order]),
                "parameters": parameters, "execution_form": spec.execution_form,
                "instruction_width": spec.instruction_width, "conditions_evaluated": False,
                "applicability_cases": copy.deepcopy(
                    owner.get('call_admission_details', {}).get('applicability_cases', [])),
                "source_notes": spec.notes,
                "contract_sources": [dict(s) for s in spec.contract_sources]}
            if structured:
                result.update(structured)
            return self._include_expression_contract(result, expression)
        st_parameters = ([{"name": "EN", "role": "read", "type": "bit"}]
                         if spec.st_call["enable"] else []) + copy.deepcopy(parameters)
        result = {"target_model": cpu, "opcode": form.opcode,
                "ladder_signature": " ".join([form.opcode, *spec.native_operand_order]),
                "st_signature": spec.st_call["name"] + "(" + ", ".join(p["name"] for p in st_parameters) + ")",
                "parameters": parameters, "st_parameters": st_parameters,
                "st_call": dict(spec.st_call),
                "enable_rule": ("EN is a required BOOL first argument, including inside IF; the enclosing IF does not replace EN."
                                if spec.st_call["enable"] else "No EN argument in this source signature."),
                "literal_rule": "String operands retain their quotes in both languages; an unquoted device token is not a string.",
                "source_notes": spec.notes,
                "contract_sources": [dict(s) for s in spec.contract_sources]}
        if structured:
            result.update(structured)
        return self._include_expression_contract(result, expression)

    def describe_contract(self, mnemonic: Any, cpu: Optional[str] = None) -> Dict[str, Any]:
        result = self._describe_contract_without_fbd(mnemonic, cpu=cpu)
        fbd = self._describe_reviewed_fbd_call(mnemonic, cpu=cpu) if cpu else {}
        if fbd and result.get('contract_level') == 'unknown':
            result = {'opcode': fbd['opcode'], 'contract_level': 'signature_verified',
                      'call_kind': 'instruction_block', 'representation': 'fbd_instruction_block',
                      'verified_fields': ['fbd_signature', 'cpu_applicability', 'graphical_interface'],
                      'unverified_fields': ['execution_semantics']}
        return self._include_fbd_contract(result, fbd)

    def _describe_contract_without_fbd(self, mnemonic: Any, cpu: Optional[str] = None) -> Dict[str, Any]:
        """One compact Core view for analysis, generation, MCP and diagnostics."""
        expression = {}
        if cpu is not None:
            expression = self._describe_reviewed_st_expression(mnemonic, cpu=cpu)
            if expression and self.describe_call(mnemonic, cpu=cpu).get('call_kind') == 'expression':
                unverified = ['execution_semantics']
                if expression['st_expression'].get('comparison_operand_type_compatibility') == 'not_stated_in_reviewed_source':
                    unverified.append('comparison_operand_type_compatibility')
                return {'opcode': expression['opcode'], 'contract_level': 'expression_verified',
                    'call_kind': 'expression', 'representation': 'ST_expression',
                    'interfaces': ['st'], 'st_expression': expression['st_expression'],
                    'applicability_cases': expression['applicability_cases'],
                    'conditions_evaluated': False,
                    'verified_fields': ['source_expression', 'operator_binding', 'cpu_applicability'],
                    'unverified_fields': unverified, 'sources': expression['contract_sources']}
        form = self.resolve_form(mnemonic, cpu=cpu)
        if form is None:
            call = self.describe_call(mnemonic, cpu=cpu) if cpu else {}
            if call.get('call_kind') == 'expression':
                return {'opcode': call['opcode'], 'contract_level': 'expression_verified',
                    'call_kind': 'expression', 'representation': 'ST_expression',
                    'interfaces': ['st'], 'st_expression': call['st_expression'],
                    'applicability_cases': call['applicability_cases'],
                    'conditions_evaluated': False,
                    'verified_fields': ['source_expression', 'operator_binding', 'cpu_applicability'],
                    'unverified_fields': ['execution_semantics'], 'sources': call['contract_sources']}
            if call.get('st_contract_verified') is True and call.get('st_call'):
                return self._include_expression_contract({'opcode': call['opcode'], 'contract_level': 'signature_verified',
                    'interfaces': ['st'], 'st_call': call['st_call'], 'st_parameters': call['st_parameters'],
                    'applicability_cases': call['applicability_cases'], 'conditions_evaluated': False,
                    'verified_fields': ['st_signature', 'cpu_applicability'],
                    'unverified_fields': ['execution_semantics'], 'sources': call['contract_sources']}, expression)
            return {"opcode": str(mnemonic).upper(), "contract_level": "unknown"}
        spec = form.spec
        coverage = spec.contract_coverage()
        result = {"opcode": form.opcode, "base_mnemonic": form.base_mnemonic,
                "contract_level": "signature_verified" if spec.verified_fields else
                    "opcode_only" if spec.contract_level == "opcode_only" else "partial",
                "min_operands": spec.min_operands, "max_operands": spec.max_operands,
                "native_operand_order": list(spec.native_operand_order),
                "execution_form": spec.execution_form, "instruction_width": spec.instruction_width,
                 "st_call": copy.deepcopy(spec.st_call), "notes": spec.notes,
                "completion": (
                    {"device": spec.completion.device, "placement": spec.completion.placement}
                    if spec.completion else None
                ),
                "pulse_output": (
                    {
                        "frequency_operand_indexes": list(spec.pulse_output.frequency_operand_indexes),
                        "pulse_output_operand_index": spec.pulse_output.pulse_output_operand_index,
                        "direction_output_operand_index": spec.pulse_output.direction_output_operand_index,
                    }
                    if spec.pulse_output else None
                ),
                "numeric_operand_boundaries": [
                    {
                        "operand_index": item.operand_index,
                        "minimum": item.minimum,
                        "maximum": item.maximum,
                        "absolute": item.absolute,
                        "unit": item.unit,
                        "label": item.label,
                    }
                    for item in spec.numeric_operand_boundaries
                ],
                "disjoint_bit_ranges": [
                    {
                        "source_operand_index": item.source_operand_index,
                        "destination_operand_index": item.destination_operand_index,
                        "destination_length_operand_index": item.destination_length_operand_index,
                        "source_length_operand_index": item.source_length_operand_index,
                        "same_device_prefix_only": item.same_device_prefix_only,
                        "error_code": item.error_code,
                    }
                    for item in spec.disjoint_bit_ranges
                ],
                "cpu_replacements": dict(spec.cpu_replacements),
                "verified_fields": list(spec.verified_fields),
                "unverified_fields": [key for key, value in coverage.items() if value != "source_verified"],
                "operand_annotations": [{"name": o.name,
                    **o.slot_metadata(index, spec.native_operand_order[index - 1]
                                      if index <= len(spec.native_operand_order) else ""),
                    "role": o.role.value,
                    "data_type": o.data_type, "device_prefixes": list(o.device_prefixes),
                    **({"allowed_devices": list(o.allowed_devices)} if o.allowed_devices else {}),
                    **({"word_count": o.word_count} if o.word_count != 1 else {}),
                    **({"bit_count": o.bit_count} if o.bit_count != 1 else {}),
                    **({"integer_literal_widths": dict(o.integer_literal_widths)} if o.integer_literal_widths else {}),
                    **({"address_forms": list(o.address_forms)} if o.address_forms else {}),
                    **({"address_form_limits": {form: dict(limits) for form, limits in o.address_form_limits}}
                       if o.address_form_limits else {}),
                    **({"usage_facts": [fact.as_mapping() for fact in o.usage_facts]} if o.usage_facts else {})}
                    for index, o in enumerate(spec.operands, start=1)],
                "sources": [{k: v for k, v in source.items() if k not in {"proof_sha256", "source_sha256"}} for source in spec.contract_sources]}
        if cpu:
            structured = self._describe_reviewed_st_call(mnemonic, cpu=cpu)
            if structured:
                result.update({key: structured[key] for key in (
                    'interfaces', 'st_call', 'st_parameters', 'st_contract_verified',
                    'applicability_cases', 'conditions_evaluated', 'source_contract_fact_ids')})
                result['st_contract_sources'] = structured['contract_sources']
                if structured.get('constraints'):
                    result['st_constraints'] = structured['constraints']
        return self._include_expression_contract(result, expression)

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
        target_contracts = []
        source_forms = []
        modifier_rules: Dict[Tuple[str, str], Mapping[str, Any]] = {}
        knowledge_profiles = []
        family_knowledge = []
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
            from plc.instruction_knowledge import validate_knowledge_profiles, validate_family_knowledge
            knowledge_profiles.extend(validate_knowledge_profiles(payload.get('knowledge_profiles', []), vendor=vendor))
            family_knowledge.extend(validate_family_knowledge(payload.get('instruction_families', []), vendor=vendor))
            source_forms.extend(payload.get('source_forms', []))
            contracts = payload.get("cpu_contracts", [])
            if not isinstance(contracts, list):
                raise ValueError(f"{path}: cpu_contracts must be an array")
            target_contracts.extend(contracts)
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
        registry = cls(specs)
        registry._knowledge_profiles = tuple(knowledge_profiles)
        registry._family_knowledge = tuple(family_knowledge)
        for form in source_forms:
            for family in form.get('families', [form['family']]):
                operands = form['operands']
                for token in form['symbols']:
                    key = (family, token, len(operands))
                    if key in registry._source_forms:
                        raise ValueError('duplicate saved-source instruction form: ' + str(key))
                    spec = InstructionSpec.from_mapping(dict(
                        mnemonic=token, category=form.get('category', 'action'), semantic_kind='vendor',
                        arity=dict(min=len(operands), max=len(operands)), operands=operands,
                        source_materials=[dict(manual_id=form['manual_id'], pdf_page=form['pdf_page'])]))
                    registry._source_forms[key] = replace(spec, verified_fields=tuple(
                        ['arity', 'operand_order', 'operand_roles'] +
                        (['operand_types'] if form.get('operand_types_verified') else [])))
        registry.load_target_contracts(target_contracts)
        return registry


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
            family_knowledge = directory / 'instruction_families.json'
            if family_knowledge.is_file():
                paths = paths + (family_knowledge,)
            registry = InstructionRegistry.from_files(paths)
            promotions = directory / "fx3u_contract_promotions.json"
            if promotions.is_file():
                registry.load_contract_promotions(promotions)
            operand_promotions = directory / "fx3u_operand_semantic_promotions.json"
            if operand_promotions.is_file():
                registry.load_operand_semantic_promotions(operand_promotions)
            capabilities = directory / "instruction_capabilities.json"
            if capabilities.is_file():
                registry.load_capability_contract(capabilities)
            # Opt in to a candidate owner store without replacing the catalogue
            # that supplies opcode registration and CPU capability contracts.
            if "GXW2_INSTRUCTION_OWNER_MANIFEST" in os.environ:
                configured_manifest = os.environ["GXW2_INSTRUCTION_OWNER_MANIFEST"].strip()
                if not configured_manifest:
                    raise ValueError("GXW2_INSTRUCTION_OWNER_MANIFEST must name a manifest")
                registry.load_instruction_owner_store(Path(configured_manifest).expanduser())
                return registry
            manifest = directory / "instruction_definitions.manifest.json"
            if not (manifest.exists() or manifest.is_symlink()):
                from shared.paths import resource_path
                manifest = resource_path("knowledge/runtime/instruction_definitions.manifest.json")
            if manifest.exists() or manifest.is_symlink():
                registry.load_instruction_owner_store(manifest)
                return registry
            definitions = directory / "instruction_definitions.json.gz"
            if not definitions.exists():
                definitions = directory / "instruction_definitions.json"
            if definitions.is_file():
                registry.load_instruction_definitions(definitions)
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
    availability = {}
    members = []
    if model:
        from plc.target_identity import target_identity_state
        from plc.instruction_constraints import check_fact_constraints
        members, reason = target_identity_state(model)
        members = members if members and reason is None else [model]
        for definition in DEFAULT_INSTRUCTION_REGISTRY._family_knowledge:
            for fact in definition['facts']:
                if (fact['dimension'] == 'constraints.cpu_availability'
                        and set(fact['scope']['models']) & set(members)):
                    for opcode in fact['scope']['forms']:
                        availability.setdefault(opcode, {}).setdefault(definition['base_opcode'],
                            [definition['facts'], []])[1].append(fact['id'])
    result = []
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics():
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form(mnemonic, cpu=model)
        if form is None:
            continue
        spec = form.spec
        if mnemonic in GENERATION_TYPED_OUTPUT_OPCODES:
            continue
        if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
            continue
        if model and not spec.supports_cpu(model):
            continue
        if model:
            # A form the compiled build did not admit as a call must not be offered
            # to the generator either; the shared spec cannot answer this, because
            # an empty CPU scope there means "not narrowed", not "admitted".
            owners = [DEFAULT_INSTRUCTION_REGISTRY.definition_header(mnemonic, cpu=member)
                      for member in members]
            if not owners:
                owners = [DEFAULT_INSTRUCTION_REGISTRY.definition_header(mnemonic, cpu=model)]
            if any(isinstance(owner, Mapping) and "call_available" in owner and not owner["call_available"]
                   for owner in owners):
                continue
            if any(isinstance(owner, Mapping)
                   and owner.get('call_admission_details', {}).get('interfaces') is not None
                   and not {'ladder', 'native_ladder_instruction'} & set(owner['call_admission_details']['interfaces'])
                   for owner in owners):
                continue
            if any(check_fact_constraints(groups, {}, target=mnemonic, plc_model=member, language='ladder',
                    context={'project_mode': 'native_ladder_instruction'}, root_ids=roots)['status'] == 'failed'
                    for groups, roots in availability.get(mnemonic, {}).values() for member in members):
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
    "LITERAL_ARITHMETIC_MNEMONIC_PATTERN",
    "generation_app_instr_mnemonics",
    "CompletionSemantics",
    "DisjointBitRangeBoundary",
    "InstructionCategory",
    "InstructionRegistry",
    "InstructionResolution",
    "InstructionSpec",
    "NumericOperandBoundary",
    "OperandRole",
    "OperandSpec",
    "PulseOutputSemantics",
    "SemanticKind",
    "catalogued_read_write_indexes",
    "catalogued_write_indexes",
    "get_instruction_spec",
    "load_default_instruction_registry",
]


def source_instruction_arities(opcode, cpu):
    """Reuse the native storage catalogue when reading Q/L source signatures.

    These facts fill source-reading gaps without promoting unreviewed entries
    into the generation or execution catalogue.
    """
    from plc.target_identity import target_family, is_parameter_only
    if target_family(cpu) not in {'Q', 'L'} or is_parameter_only(cpu):
        return frozenset()
    from plc.storage.q_encoder import source_instruction_arities as stored_arities
    return stored_arities(opcode, cpu=cpu)

def condition_connector(opcode):
    """Accumulator connector of a stored contact or comparison, not execution support.

    Q/L use the same LD/AND/OR modifiers for word, double-word, floating, string
    and time comparisons, so catalogue completeness must not change the boundaries
    of their original circuits.
    """
    name = str(opcode or '').strip().upper()
    if name in {'ANI', 'ORI'}:
        return 'AND' if name == 'ANI' else 'OR'
    match = re.fullmatch(
        r'(LD|AND|OR)(?:I|P|F|PI|FI|PCHK|(?:ED|DT|TM|D|E|\$)?(?:<>|>=|<=|=|>|<))?',
        name,
    )
    return match[1] if match else None
