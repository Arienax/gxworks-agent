"""Strict source contracts for graphical FBD instruction blocks.

This is a language-specific view of existing instruction owner facts.  It
does not admit an ST call, native instruction, or serialized operator alias.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .instruction_definition import (
    DefinitionError,
    InstructionFactGroup,
    UnknownInstructionSemantics,
    ValueType,
    evaluate_expression,
    select_fact_dependencies,
)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GENERIC_TYPES = frozenset({
    "ANY", "ANY_BIT", "ANY_BOOL", "ANY_NUM", "ANY_REAL", "ANY_INT",
    "ANY_SIMPLE", "ANY16", "ANY32", "ANY64",
})
_UNSUPPORTED_STATES = frozenset({"unsupported", "not_supported", "unavailable", "not_applicable"})
_ANDD_COMPARISON = re.compile(r"^ANDD(<=|>=|<>|=|>|<)$")
_REAL_COMPARISON = re.compile(r"^ANDED?(<=|>=|<>|=|>|<)$")
_ANDTM_COMPARISON = re.compile(r"^ANDTM(<=|>=|<>|=|>|<)$")


@dataclass(frozen=True)
class FBDInstructionBlockContract:
    """A source-verified FBD block interface and its unevaluated conditions."""

    opcode: str
    model: str
    pins: tuple[Mapping[str, Any], ...]
    fact_ids: tuple[str, ...]
    facts: tuple[Mapping[str, Any], ...]
    conditions: tuple[Mapping[str, Any], ...]
    conditions_status: str
    sources: tuple[Mapping[str, Any], ...]
    operation: Mapping[str, Any] | None = None
    operation_fact_id: str | None = None

    def as_mapping(self) -> dict[str, Any]:
        result = {
            "opcode": self.opcode,
            "model": self.model,
            "call_kind": "instruction_block",
            "representation": "fbd_instruction_block",
            "pins": copy.deepcopy(list(self.pins)),
            "fact_ids": list(self.fact_ids),
            "facts": copy.deepcopy(list(self.facts)),
            "conditions": copy.deepcopy(list(self.conditions)),
            "conditions_status": self.conditions_status,
            "conditions_evaluated": self.conditions_status in {"not_required", "satisfied"},
            "sources": copy.deepcopy(list(self.sources)),
        }
        if self.operation is not None:
            result["operation"] = copy.deepcopy(dict(self.operation))
            result["operation_fact_id"] = self.operation_fact_id
        return result


@dataclass(frozen=True)
class FBDInstructionBlockResolution:
    contract: FBDInstructionBlockContract | None
    refusal_reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.contract is not None and not self.refusal_reasons


class _Refusal(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _type_is_supported(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    token = value.strip().upper()
    if token in _GENERIC_TYPES or token.startswith("ANY_"):
        return True
    try:
        ValueType.from_iec(token)
    except (DefinitionError, AttributeError):
        return False
    return True


def _source_matches_owner(source: Mapping[str, Any], *, opcode: str, model: str) -> bool:
    """Accept page references after normalization while keeping owner scope exact."""
    manual = source.get("manual_id")
    revision = source.get("revision")
    pages = source.get("pdf_pages")
    if not (isinstance(manual, str) and manual.strip()
            and isinstance(revision, str) and revision.strip()
            and isinstance(pages, (list, tuple)) and pages
            and all(type(page) is int and page > 0 for page in pages)):
        reference = source.get("source_ref")
        if isinstance(reference, str):
            match = re.fullmatch(r"([^:]+):([^:]+):pages=([1-9][0-9]*(?:,[1-9][0-9]*)*)", reference)
            if match:
                manual, revision = match.group(1), match.group(2)
                pages = [int(page) for page in match.group(3).split(",")]
    if not (isinstance(manual, str) and manual.strip()
            and isinstance(revision, str) and revision.strip()
            and isinstance(pages, (list, tuple)) and pages
            and all(type(page) is int and page > 0 for page in pages)):
        return False
    source_opcode, source_model = source.get("opcode"), source.get("target_model")
    if source_opcode is not None and str(source_opcode).upper() != opcode:
        return False
    if source_model is not None and str(source_model).upper() != model:
        return False
    expected_ref = "%s:%s:pages=%s" % (manual, revision, ",".join(str(page) for page in pages))
    return source.get("source_ref") in (None, expected_ref)


def _has_exact_scope(group: InstructionFactGroup, *, opcode: str, model: str,
                     language: str = "fbd") -> bool:
    scope = group.scope or {}
    return (opcode in scope.get("forms", ())
            and model in scope.get("models", ())
            and language in scope.get("languages", ()))


def _has_source(group: InstructionFactGroup, *, opcode: str, model: str) -> bool:
    return any(_source_matches_owner(source, opcode=opcode, model=model)
               for source in group.sources)


def _conditions_status(conditions: tuple[Mapping[str, Any], ...], context: Mapping[str, Any] | None) -> str:
    if not conditions:
        return "not_required"
    state = dict(context or {})
    unresolved = False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            unresolved = True
            continue
        predicate = condition.get("predicate")
        if isinstance(predicate, Mapping):
            try:
                result = evaluate_expression(predicate, {}, state=state)
            except UnknownInstructionSemantics:
                unresolved = True
                continue
            except DefinitionError as error:
                raise _Refusal("invalid_source_condition") from error
            if result is not True:
                return "not_satisfied"
            continue
        field = condition.get("context", condition.get("field"))
        if isinstance(field, str) and "equals" in condition:
            if field not in state:
                unresolved = True
            elif state[field] != condition["equals"]:
                return "not_satisfied"
            continue
        unresolved = True
    return "unresolved" if unresolved else "satisfied"


def _negative_applicability(groups: list[InstructionFactGroup], *, opcode: str, model: str) -> bool:
    for group in groups:
        if (group.dimension != "constraints.cpu_availability"
                or group.status != "source_verified" or group.verification != "source_checked"
                or not _has_exact_scope(group, opcode=opcode, model=model)):
            continue
        if not _has_source(group, opcode=opcode, model=model):
            continue
        value = group.value
        state = value.get("model_states", {}).get(model)
        if value.get("available") is False or state in _UNSUPPORTED_STATES:
            return True
    return False


def _cpu_availability_state(group: InstructionFactGroup, *, opcode: str, model: str) -> str | None:
    if (group.dimension != "constraints.cpu_availability"
            or group.status != "source_verified" or group.verification != "source_checked"
            or not _has_exact_scope(group, opcode=opcode, model=model)
            or not _has_source(group, opcode=opcode, model=model)):
        return None
    value = group.value
    state = value.get("model_states", {}).get(model)
    if value.get("available") is False or state in _UNSUPPORTED_STATES:
        return "unsupported"
    if value.get("available") is True or state in {"supported", "explicitly_supported"}:
        return "supported"
    return None


def _condition_key(conditions) -> str:
    return json.dumps(list(conditions or ()), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _andd_comparison_operation(groups: list[InstructionFactGroup], *, opcode: str,
                               model: str, pins: list[Mapping[str, Any]]) -> InstructionFactGroup | None:
    """Require the comparison operation fact for the six ANDD block forms.

    The FBD output pin is ENO (execution status).  The source operation fact
    separately describes the comparison contact and its AND composition; it
    must not be reinterpreted as a comparison-result output pin.
    """
    match = _ANDD_COMPARISON.fullmatch(opcode)
    if match is None:
        return None

    scoped = [group for group in groups
              if group.dimension == "effects.operation"
              and _has_exact_scope(group, opcode=opcode, model=model)
              and group.status == "source_verified"
              and group.verification == "source_checked"]
    if not scoped:
        raise _Refusal("fbd_comparison_operation_missing")
    sourced = [group for group in scoped if _has_source(group, opcode=opcode, model=model)]
    if not sourced:
        raise _Refusal("fbd_comparison_operation_source_mismatch")
    if len(sourced) != 1:
        raise _Refusal("ambiguous_fbd_comparison_operation")
    operation = sourced[0]
    value = operation.value
    if (value.get("operation") != "compose_comparison_contact"
            or value.get("comparison") != match.group(1)
            or value.get("data_domain") != "signed_BIN32"
            or str(value.get("left", "")).casefold() != "s1"
            or str(value.get("right", "")).casefold() != "s2"
            or value.get("composition") != "and"):
        raise _Refusal("fbd_comparison_operation_mismatch")

    by_name = {str(pin.get("name", "")).casefold(): pin for pin in pins}
    expected = {
        "en": ("input", "BOOL", "enable"),
        "eno": ("output", "BOOL", "execution_status"),
        "s1": ("input", "ANY32", "comparison_value"),
        "s2": ("input", "ANY32", "comparison_value"),
    }
    if set(by_name) != set(expected):
        raise _Refusal("fbd_comparison_pin_set_mismatch")
    for name, (direction, pin_type, role) in expected.items():
        pin = by_name[name]
        if (pin.get("direction") != direction
                or str(pin.get("type", "")).upper() != pin_type
                or pin.get("role") != role):
            raise _Refusal("fbd_comparison_pin_semantics_mismatch:" + name)
    return operation


def _andd_comparison_operand_fact(groups: list[InstructionFactGroup], *, opcode: str,
                                  model: str, operation: InstructionFactGroup
                                  ) -> tuple[InstructionFactGroup, dict[str, Mapping[str, Any]]]:
    """Resolve the source-bound typed relation for the implicit comparison result."""
    candidates = []
    for group in groups:
        if (group.dimension != "parameters.typed_operands"
                or group.status != "source_verified" or group.verification != "source_checked"
                or not _has_exact_scope(group, opcode=opcode, model=model)
                or not _has_source(group, opcode=opcode, model=model)):
            continue
        operands = group.value.get("operands")
        if not isinstance(operands, (list, tuple)):
            continue
        by_name = {str(row.get("name", "")).casefold(): row
                   for row in operands if isinstance(row, Mapping)}
        if all(name in by_name and isinstance(by_name[name].get("operation_value_type"), Mapping)
               for name in ("s1", "s2")):
            candidates.append((group, by_name))
    if not candidates:
        raise _Refusal("fbd_comparison_operand_relation_missing")
    if len(candidates) != 1:
        raise _Refusal("ambiguous_fbd_comparison_operand_relation")
    group, by_name = candidates[0]

    for name, direction, role, purpose in (
            ("en", "input", "enable", "execution_condition"),
            ("eno", "output", "execution_status", "execution_status")):
        operand = by_name.get(name)
        uses = (operand.get("purpose") or {}).get("uses", ()) if operand else ()
        if (not operand or operand.get("direction") != direction
                or str(operand.get("type", "")).upper() != "BOOL"
                or operand.get("role") != role
                or not any(isinstance(use, Mapping) and use.get("execution_role") == purpose
                           for use in uses)):
            raise _Refusal("fbd_comparison_execution_pin_semantics_mismatch:" + name)

    for name, position in (("s1", 1), ("s2", 2)):
        operand = by_name[name]
        if (operand.get("direction") != "input"
                or str(operand.get("type", "")).upper() != "ANY32"
                or operand.get("role") != "comparison_value"
                or operand.get("passing") != "device_reference"
                or operand.get("addressing") != "start_device"
                or dict(operand["operation_value_type"]) != {
                    "kind": "int", "bits": 32, "signed": True}):
            raise _Refusal("fbd_comparison_operand_semantics_mismatch:" + name)
        uses = (operand.get("purpose") or {}).get("uses", ())
        if not isinstance(uses, (list, tuple)):
            raise _Refusal("fbd_comparison_result_use_missing:" + name)
        operation_uses = [use for use in uses if isinstance(use, Mapping)
                          and use.get("operation") == "compose_comparison_contact"]
        state_uses = [use for use in uses if isinstance(use, Mapping)
                      and use.get("used_in") == "and"
                      and use.get("result") == {
                          "kind": "state", "offset": 0, "operand": "operation_result"}]
        if (len(operation_uses) != 1
                or operation_uses[0].get("input_position") != position
                or operation_uses[0].get("result") != "operation_result"
                or len(state_uses) != 1):
            raise _Refusal("fbd_comparison_result_use_mismatch:" + name)

    closure = select_fact_dependencies(groups, [group.id], opcode=opcode,
                                       model=model, language="fbd")
    if closure["gaps"]:
        raise _Refusal("fbd_comparison_operand_dependency_gaps")
    closure_ids = {fact.get("id") for fact in closure["groups"]}
    if (operation.id not in closure_ids
            or not any(InstructionFactGroup.from_mapping(fact).dimension == "constraints.cpu_availability"
                       for fact in closure["groups"])):
        raise _Refusal("fbd_comparison_operand_dependency_missing")
    return group, by_name


def _real_comparison_operation(groups: list[InstructionFactGroup], *, opcode: str,
                               model: str, pins: list[Mapping[str, Any]]) -> InstructionFactGroup | None:
    """Resolve only the source-backed REAL/LREAL ANDE comparison blocks."""
    match = _REAL_COMPARISON.fullmatch(opcode)
    if match is None:
        return None
    domain = "LREAL" if opcode.startswith("ANDED") else "REAL"
    scoped = [group for group in groups
              if group.dimension == "effects.operation"
              and _has_exact_scope(group, opcode=opcode, model=model)
              and group.status == "source_verified"
              and group.verification == "source_checked"]
    if not scoped:
        raise _Refusal("fbd_comparison_operation_missing")
    sourced = [group for group in scoped if _has_source(group, opcode=opcode, model=model)]
    if not sourced:
        raise _Refusal("fbd_comparison_operation_source_mismatch")
    if len(sourced) != 1:
        raise _Refusal("ambiguous_fbd_comparison_operation")
    operation = sourced[0]
    value = operation.value
    if (value.get("operation") != "compose_comparison_contact"
            or value.get("comparison") != match.group(1)
            or value.get("data_domain") != domain
            or str(value.get("left", "")).casefold() != "s1"
            or str(value.get("right", "")).casefold() != "s2"
            or value.get("composition") != "and"):
        raise _Refusal("fbd_comparison_operation_mismatch")

    by_name = {str(pin.get("name", "")).casefold(): pin for pin in pins}
    expected = {
        "en": ("input", "BOOL", "enable"),
        "eno": ("output", "BOOL", "execution_status"),
        "s1": ("input", domain, "comparison_value"),
        "s2": ("input", domain, "comparison_value"),
    }
    if set(by_name) != set(expected):
        raise _Refusal("fbd_comparison_pin_set_mismatch")
    for name, (direction, pin_type, role) in expected.items():
        pin = by_name[name]
        if (pin.get("direction") != direction
                or str(pin.get("type", "")).upper() != pin_type
                or pin.get("role") != role):
            raise _Refusal("fbd_comparison_pin_semantics_mismatch:" + name)
    return operation


def _real_comparison_operand_fact(groups: list[InstructionFactGroup], *, opcode: str,
                                  model: str, operation: InstructionFactGroup
                                  ) -> tuple[InstructionFactGroup, dict[str, Mapping[str, Any]]]:
    """Bind REAL/LREAL operand types from this owner's FBD typed table."""
    domain = "LREAL" if opcode.startswith("ANDED") else "REAL"
    expected_type = {"kind": "real", "bits": 64 if domain == "LREAL" else 32, "signed": False}
    candidates = []
    for group in groups:
        if (group.dimension != "parameters.typed_operands"
                or group.status != "source_verified" or group.verification != "source_checked"
                or not _has_exact_scope(group, opcode=opcode, model=model)
                or "fbd" not in group.scope.get("languages", ())
                or not _has_source(group, opcode=opcode, model=model)):
            continue
        operands = group.value.get("operands")
        if not isinstance(operands, (list, tuple)):
            continue
        rows = [row for row in operands if isinstance(row, Mapping)]
        by_name = {str(row.get("name", "")).casefold(): row for row in rows}
        if len(by_name) != len(rows) or set(by_name) != {"en", "eno", "s1", "s2"}:
            continue
        closure = select_fact_dependencies(groups, [group.id], opcode=opcode,
                                           model=model, language="fbd")
        closure_ids = {fact.get("id") for fact in closure["groups"]}
        if (closure["gaps"] or operation.id not in closure_ids
                or not any(InstructionFactGroup.from_mapping(fact).dimension == "constraints.cpu_availability"
                           for fact in closure["groups"])):
            continue
        candidates.append((group, by_name))
    if not candidates:
        raise _Refusal("fbd_comparison_operand_relation_missing")
    if len(candidates) != 1:
        raise _Refusal("ambiguous_fbd_comparison_operand_relation")
    group, by_name = candidates[0]

    expected_rows = {
        "en": ("input", "BOOL", "enable"),
        "eno": ("output", "BOOL", "execution_status"),
        "s1": ("input", domain, "comparison_value"),
        "s2": ("input", domain, "comparison_value"),
    }
    for name, (direction, type_name, role) in expected_rows.items():
        operand = by_name[name]
        if (operand.get("direction") != direction
                or str(operand.get("type", "")).upper() != type_name
                or operand.get("role") != role):
            raise _Refusal("fbd_comparison_operand_semantics_mismatch:" + name)
        declared_relation = operand.get("operation_value_type")
        if declared_relation is not None:
            relation = dict(declared_relation) if isinstance(declared_relation, Mapping) else {}
            if (relation.get("kind") != "real" or relation.get("bits") != expected_type["bits"]
                    or relation.get("unit") not in (None, "")
                    or relation.get("signed", False) is not False
                    or set(relation) - {"kind", "bits", "signed"}):
                raise _Refusal("fbd_comparison_operand_type_relation_mismatch:" + name)

    closure = select_fact_dependencies(groups, [group.id], opcode=opcode,
                                       model=model, language="fbd")
    if closure["gaps"]:
        raise _Refusal("fbd_comparison_operand_dependency_gaps")
    closure_ids = {fact.get("id") for fact in closure["groups"]}
    if (operation.id not in closure_ids
            or not any(InstructionFactGroup.from_mapping(fact).dimension == "constraints.cpu_availability"
                       for fact in closure["groups"])):
        raise _Refusal("fbd_comparison_operand_dependency_missing")
    return group, by_name


def _source_page_keys(group: InstructionFactGroup, *, opcode: str, model: str) -> set[tuple[str, str, int]]:
    """Return exact source page identities after the existing source-ref normalization."""
    result = set()
    for source in group.sources:
        if not _source_matches_owner(source, opcode=opcode, model=model):
            continue
        manual, revision, pages = source.get("manual_id"), source.get("revision"), source.get("pdf_pages")
        if not (isinstance(manual, str) and manual and isinstance(revision, str) and revision
                and isinstance(pages, (list, tuple)) and pages):
            reference = source.get("source_ref")
            match = (re.fullmatch(r"([^:]+):([^:]+):pages=([1-9][0-9]*(?:,[1-9][0-9]*)*)", reference)
                     if isinstance(reference, str) else None)
            if match is None:
                continue
            manual, revision = match.group(1), match.group(2)
            pages = [int(page) for page in match.group(3).split(",")]
        result.update((manual, revision, page) for page in pages
                      if type(page) is int and page > 0)
    return result


def _andtm_time_operation_is_supported(value: Mapping[str, Any], comparison: str) -> bool:
    domain = value.get("domain_comparison")
    expected_components = [
        {"name": "hour", "word_offset": 0, "select_bit": 2, "range": [0, 23]},
        {"name": "minute", "word_offset": 1, "select_bit": 1, "range": [0, 59]},
        {"name": "second", "word_offset": 2, "select_bit": 0, "range": [0, 59]},
    ]
    invalid = domain.get("invalid_components", {}) if isinstance(domain, Mapping) else {}
    return (
        value.get("operation") == "compose_comparison_contact"
        and value.get("comparison") == comparison
        and value.get("composition") == "and"
        and str(value.get("left", "")).casefold() == "s1"
        and str(value.get("right", "")).casefold() == "s2"
        and str(value.get("selector", "")).casefold() == "n"
        and value.get("data_domain") == "time_hour_minute_second"
        and isinstance(domain, Mapping)
        and domain.get("kind") == "selected_calendar_components"
        and str(domain.get("selector", "")).casefold() == "n"
        and domain.get("component_encoding") == "BIN16"
        and domain.get("components") == expected_components
        and domain.get("comparison_order") == ["hour", "minute", "second"]
        and domain.get("comparison_span_words") == 3
        and domain.get("current_clock_source") == "current_time"
        and domain.get("current_clock_select_bit") == 15
        and str(domain.get("current_clock_ignores", "")).casefold() == "s2"
        and domain.get("reserved_zero_bit_range") == {"first": 3, "last": 14}
        and domain.get("reserved_bits_nonzero_result") is False
        and domain.get("empty_selection_result") is False
        and domain.get("invalid_selector_result") is False
        and domain.get("equality") == "all_selected_components_equal"
        and invalid.get("state") == "SM709"
        and invalid.get("when") == "component_outside_declared_range_or_device_span_out_of_range"
        and invalid.get("latch") == "until_reset_or_power_off"
        and invalid.get("comparison_result") is False
        and invalid.get("explicit_clear_allowed") is True
    )


def _andtm_comparison_operation(groups: list[InstructionFactGroup], *, opcode: str,
                                model: str, signature: InstructionFactGroup,
                                pins: list[Mapping[str, Any]]) -> InstructionFactGroup | None:
    """Resolve the time-component contact only through this FBD signature's source chain."""
    match = _ANDTM_COMPARISON.fullmatch(opcode)
    if match is None:
        return None
    by_id = {group.id: group for group in groups}
    signature_cpu_ids = {
        dependency for dependency in signature.depends_on
        if dependency in by_id
        and by_id[dependency].dimension == "constraints.cpu_availability"
        and _cpu_availability_state(by_id[dependency], opcode=opcode, model=model) == "supported"
    }
    if not signature_cpu_ids:
        raise _Refusal("fbd_time_cpu_dependency_missing")
    signature_pages = _source_page_keys(signature, opcode=opcode, model=model)
    if not signature_pages:
        raise _Refusal("fbd_time_signature_source_pages_missing")

    scoped = [group for group in groups
              if group.dimension == "effects.operation"
              and _has_exact_scope(group, opcode=opcode, model=model)
              and group.status == "source_verified"
              and group.verification == "source_checked"]
    if not scoped:
        raise _Refusal("fbd_time_operation_missing")
    sourced = [group for group in scoped if _has_source(group, opcode=opcode, model=model)]
    if not sourced:
        raise _Refusal("fbd_time_operation_source_mismatch")
    selected = []
    for group in sourced:
        if (not set(group.depends_on).intersection(signature_cpu_ids)
                or not _source_page_keys(group, opcode=opcode, model=model).intersection(signature_pages)):
            continue
        if _condition_key(group.conditions) != _condition_key(signature.conditions):
            raise _Refusal("fbd_time_operation_condition_mismatch")
        selected.append(group)
    if len(selected) != 1:
        raise _Refusal("ambiguous_fbd_time_operation" if selected
                       else "fbd_time_operation_not_linked_to_signature")
    operation = selected[0]
    if not _andtm_time_operation_is_supported(operation.value, match.group(1)):
        raise _Refusal("fbd_time_operation_mismatch")

    by_name = {str(pin.get("name", "")).casefold(): pin for pin in pins}
    expected = {
        "en": ("input", "BOOL", "enable"),
        "eno": ("output", "BOOL", "execution_status"),
        "s1": ("input", "ANY16", "comparison_value"),
        "s2": ("input", "ANY16", "comparison_value"),
        "n": ("input", "ANY16", "comparison_selector"),
    }
    if set(by_name) != set(expected):
        raise _Refusal("fbd_time_pin_set_mismatch")
    for name, shape in expected.items():
        pin = by_name[name]
        if ((pin.get("direction"), str(pin.get("type", "")).upper(), pin.get("role")) != shape
                or "operation_value_type" in pin):
            raise _Refusal("fbd_time_pin_semantics_mismatch:" + name)
    return operation


def _andtm_time_operand_fact(groups: list[InstructionFactGroup], *, opcode: str, model: str,
                             signature: InstructionFactGroup, operation: InstructionFactGroup,
                             pins: list[Mapping[str, Any]]) -> tuple[InstructionFactGroup, dict[str, Mapping[str, Any]]]:
    signature_cpu_ids = {
        dependency for dependency in signature.depends_on
        if any(group.id == dependency and group.dimension == "constraints.cpu_availability"
               and _cpu_availability_state(group, opcode=opcode, model=model) == "supported"
               for group in groups)
    }
    signature_pages = _source_page_keys(signature, opcode=opcode, model=model)
    candidates = []
    for group in groups:
        if (group.dimension != "parameters.typed_operands"
                or group.status != "source_verified" or group.verification != "source_checked"
                or not _has_exact_scope(group, opcode=opcode, model=model)
                or not _has_source(group, opcode=opcode, model=model)
                or not signature_cpu_ids.intersection(group.depends_on)
                or not _source_page_keys(group, opcode=opcode, model=model).intersection(signature_pages)):
            continue
        if _condition_key(group.conditions) != _condition_key(signature.conditions):
            raise _Refusal("fbd_time_operand_condition_mismatch")
        operands = group.value.get("operands")
        if not isinstance(operands, (list, tuple)):
            continue
        rows = [row for row in operands if isinstance(row, Mapping)]
        by_name = {str(row.get("name", "")).casefold(): row for row in rows}
        if len(by_name) != len(rows) or set(by_name) != {"en", "eno", "s1", "s2", "n"}:
            continue
        closure = select_fact_dependencies(groups, [group.id], opcode=opcode,
                                           model=model, language="fbd")
        closure_ids = {fact.get("id") for fact in closure["groups"]}
        if (closure["gaps"] or not signature_cpu_ids.intersection(closure_ids)):
            continue
        candidates.append((group, by_name))
    if not candidates:
        raise _Refusal("fbd_time_operand_relation_missing")
    if len(candidates) != 1:
        raise _Refusal("ambiguous_fbd_time_operand_relation")
    group, by_name = candidates[0]

    expected = {
        "en": ("input", "BOOL", "enable"),
        "eno": ("output", "BOOL", "execution_status"),
        "s1": ("input", "ANY16", "comparison_value"),
        "s2": ("input", "ANY16", "comparison_value"),
        "n": ("input", "ANY16", "comparison_selector"),
    }
    pin_by_name = {str(pin.get("name", "")).casefold(): pin for pin in pins}
    for name, shape in expected.items():
        operand, pin = by_name[name], pin_by_name[name]
        if ((operand.get("direction"), str(operand.get("type", "")).upper(), operand.get("role")) != shape
                or any(operand.get(key) != pin.get(key) for key in (
                    "direction", "type", "role", "passing", "addressing", "word_device_layout",
                    "read_layout", "referent_fields", "direction_resolution", "table_declared_direction"))
                or "operation_value_type" in operand or "operation_value_type" in pin):
            raise _Refusal("fbd_time_operand_semantics_mismatch:" + name)
    for name in ("s1", "s2"):
        operand = by_name[name]
        layout = operand.get("word_device_layout")
        read_layout = operand.get("read_layout")
        fields = operand.get("referent_fields")
        if (operand.get("passing") != "device_reference"
                or operand.get("addressing") != "start_device"
                or layout != {"device_unit_bits": 16, "element_bits": 16,
                              "element_count": 3, "kind": "independent_elements",
                              "single_scalar": False, "word_count": 3}
                or read_layout != {"element_count": 3,
                                   "field_order": ["hour", "minute", "second"],
                                   "head": name, "stride_words": 1}
                or not isinstance(fields, (list, tuple))
                or [(field.get("role"), field.get("offset"), field.get("minimum"),
                     field.get("maximum"), field.get("type"), field.get("encoding"))
                    for field in fields if isinstance(field, Mapping)] != [
                        ("hour", 0, 0, 23, "ANY16", "binary"),
                        ("minute", 1, 0, 59, "ANY16", "binary"),
                        ("second", 2, 0, 59, "ANY16", "binary")]):
            raise _Refusal("fbd_time_word_layout_mismatch:" + name)
    selector = by_name["n"]
    if (selector.get("word_device_layout") != {"device_unit_bits": 16, "value_bits": 16, "word_count": 1}
            or selector.get("direction_resolution") != "effective_input_from_explicit_selection_usage"
            or selector.get("table_declared_direction") != "output"):
        raise _Refusal("fbd_time_selector_layout_mismatch")
    closure = select_fact_dependencies(groups, [group.id], opcode=opcode,
                                       model=model, language="fbd")
    closure_ids = {fact.get("id") for fact in closure["groups"]}
    if closure["gaps"] or not any(
            InstructionFactGroup.from_mapping(fact).dimension == "constraints.cpu_availability"
            for fact in closure["groups"]):
        raise _Refusal("fbd_time_operand_dependency_gaps")
    return group, by_name


def resolve_fbd_instruction_block_contract(entry: Mapping[str, Any], *, opcode: str | None = None,
                                           model: str | None = None,
                                           condition_context: Mapping[str, Any] | None = None
                                           ) -> FBDInstructionBlockResolution:
    """Resolve one exact FBD source block without promoting other interfaces.

    Source conditions are evaluated only when the caller supplies the fields
    named by those conditions. Missing version or project context stays
    ``unresolved`` and is surfaced by the FBD source checker.
    """
    try:
        if not isinstance(entry, Mapping):
            raise _Refusal("owner_entry_missing")
        owner_opcode = str(opcode or entry.get("opcode") or "").strip().upper()
        owner_model = str(model or entry.get("target_model") or "").strip().upper()
        if not owner_opcode or not owner_model:
            raise _Refusal("owner_identity_missing")
        if str(entry.get("opcode") or "").strip().upper() != owner_opcode:
            raise _Refusal("owner_opcode_mismatch")
        if str(entry.get("target_model") or "").strip().upper() != owner_model:
            raise _Refusal("owner_model_mismatch")
        raw_facts = entry.get("facts")
        if not isinstance(raw_facts, (list, tuple)):
            raise _Refusal("owner_facts_missing")
        groups = [InstructionFactGroup.from_mapping(raw) for raw in raw_facts]
        by_id = {group.id: group for group in groups}
        if len(by_id) != len(groups):
            raise _Refusal("duplicate_fact_identity")

        candidates = [group for group in groups
                      if group.dimension == "parameters.fbd_signature"
                      and _has_exact_scope(group, opcode=owner_opcode, model=owner_model)]
        if not candidates:
            if _negative_applicability(groups, opcode=owner_opcode, model=owner_model):
                raise _Refusal("fbd_cpu_not_supported")
            raise _Refusal("fbd_signature_missing")
        if len(candidates) != 1:
            raise _Refusal("ambiguous_fbd_signature")
        signature = candidates[0]
        if signature.status != "source_verified" or signature.verification != "source_checked":
            raise _Refusal("fbd_signature_not_source_verified")
        if not _has_source(signature, opcode=owner_opcode, model=owner_model):
            raise _Refusal("fbd_signature_source_mismatch")
        value = signature.value
        if value.get("available") is not True:
            raise _Refusal("fbd_signature_not_available")
        if (value.get("name", "").upper() != owner_opcode
                or value.get("call_kind") != "instruction_block"
                or value.get("representation") != "fbd_instruction_block"
                or value.get("instance_required") is not False):
            raise _Refusal("unsupported_fbd_block_representation")
        if (value.get("full_source_relation_complete") is False
                or value.get("technical_relation_resolved") is False
                or value.get("unresolved_items")
                or value.get("unbound_condition_markers")
                or value.get("source_syntax_conflicts")
                or value.get("source_variable_conflicts")):
            raise _Refusal("incomplete_fbd_source_relation")
        raw_pins = value.get("pins")
        if not isinstance(raw_pins, (list, tuple)) or not raw_pins:
            raise _Refusal("fbd_pin_contract_missing")
        pins, names = [], set()
        for index, raw_pin in enumerate(raw_pins):
            if not isinstance(raw_pin, Mapping):
                raise _Refusal(f"invalid_fbd_pin:{index}")
            name = raw_pin.get("name")
            direction = raw_pin.get("direction")
            pin_type = raw_pin.get("type")
            if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
                raise _Refusal(f"invalid_fbd_pin_name:{index}")
            if name.casefold() in names:
                raise _Refusal("ambiguous_fbd_pin:" + name)
            names.add(name.casefold())
            if direction not in {"input", "output"}:
                raise _Refusal("unsupported_fbd_pin_direction:" + name)
            if not _type_is_supported(pin_type):
                raise _Refusal("unsupported_fbd_pin_type:" + name)
            if not isinstance(raw_pin.get("role"), str) or not raw_pin["role"].strip():
                raise _Refusal("fbd_pin_role_missing:" + name)
            pins.append(copy.deepcopy(dict(raw_pin)))

        operation_fact = _andd_comparison_operation(
            groups, opcode=owner_opcode, model=owner_model, pins=pins)
        operand_relation_fact = None
        operand_relation = None
        root_fact_ids = [signature.id]
        if operation_fact is None and _REAL_COMPARISON.fullmatch(owner_opcode):
            operation_fact = _real_comparison_operation(
                groups, opcode=owner_opcode, model=owner_model, pins=pins)
        if operation_fact is None and _ANDTM_COMPARISON.fullmatch(owner_opcode):
            operation_fact = _andtm_comparison_operation(
                groups, opcode=owner_opcode, model=owner_model, signature=signature, pins=pins)
        if operation_fact is not None:
            if _ANDD_COMPARISON.fullmatch(owner_opcode):
                operand_relation_fact, operand_relation = _andd_comparison_operand_fact(
                    groups, opcode=owner_opcode, model=owner_model, operation=operation_fact)
            elif _ANDTM_COMPARISON.fullmatch(owner_opcode):
                operand_relation_fact, operand_relation = _andtm_time_operand_fact(
                    groups, opcode=owner_opcode, model=owner_model, signature=signature,
                    operation=operation_fact, pins=pins)
            else:
                operand_relation_fact, operand_relation = _real_comparison_operand_fact(
                    groups, opcode=owner_opcode, model=owner_model, operation=operation_fact)
            root_fact_ids.extend((operation_fact.id, operand_relation_fact.id))
            for pin in pins:
                operand = operand_relation.get(str(pin.get("name", "")).casefold())
                if operand is not None and str(pin.get("name", "")).casefold() in {"s1", "s2"}:
                    if operand.get("operation_value_type") is not None:
                        pin["operation_value_type"] = copy.deepcopy(dict(operand["operation_value_type"]))
                    elif _REAL_COMPARISON.fullmatch(owner_opcode):
                        domain = "LREAL" if owner_opcode.startswith("ANDED") else "REAL"
                        pin["operation_value_type"] = {
                            "kind": "real", "bits": 64 if domain == "LREAL" else 32,
                            "signed": False,
                        }
                    if operand.get("purpose") is not None:
                        pin["purpose"] = copy.deepcopy(operand["purpose"])

        closure = select_fact_dependencies(groups, root_fact_ids, opcode=owner_opcode,
                                           model=owner_model, language="fbd")
        if closure["gaps"]:
            reasons = ",".join(sorted({str(gap.get("reason")) for gap in closure["gaps"]}))
            raise _Refusal("fbd_dependency_gaps:" + reasons)
        selected = [InstructionFactGroup.from_mapping(raw) for raw in closure["groups"]]
        selected_cpu = [group for group in selected
                        if group.dimension == "constraints.cpu_availability"]
        all_cpu = [group for group in groups if group.dimension == "constraints.cpu_availability"
                   and _has_exact_scope(group, opcode=owner_opcode, model=owner_model)
                   and _has_source(group, opcode=owner_opcode, model=owner_model)
                   and group.status == "source_verified" and group.verification == "source_checked"]
        positive_cpu = [group for group in all_cpu
                        if _cpu_availability_state(group, opcode=owner_opcode, model=owner_model) == "supported"]
        if not selected_cpu:
            raise _Refusal("fbd_cpu_applicability_dependency_missing"
                           if positive_cpu else "fbd_cpu_applicability_evidence_missing")

        supported_conditions = {_condition_key(group.conditions) for group in selected_cpu
                                if _cpu_availability_state(group, opcode=owner_opcode,
                                                           model=owner_model) == "supported"}
        for group in all_cpu:
            if (_cpu_availability_state(group, opcode=owner_opcode, model=owner_model) == "unsupported"
                    and _condition_key(group.conditions) in supported_conditions):
                raise _Refusal("fbd_cpu_applicability_conflict:" + group.id)
        for group in selected:
            if group.status != "source_verified" or group.verification != "source_checked":
                raise _Refusal("unverified_fbd_fact:" + group.id)
            if not _has_exact_scope(group, opcode=owner_opcode, model=owner_model):
                raise _Refusal("fbd_dependency_scope_mismatch:" + group.id)
            if not _has_source(group, opcode=owner_opcode, model=owner_model):
                raise _Refusal("fbd_dependency_source_mismatch:" + group.id)
            if (group.value.get("full_source_relation_complete") is False
                    or group.value.get("technical_relation_resolved") is False
                    or group.value.get("unresolved_items")
                    or group.value.get("unbound_condition_markers")):
                raise _Refusal("incomplete_fbd_dependency:" + group.id)
            if group.dimension == "constraints.cpu_availability":
                state = _cpu_availability_state(group, opcode=owner_opcode, model=owner_model)
                if state == "unsupported":
                    raise _Refusal("fbd_cpu_not_supported")
                if state != "supported":
                    raise _Refusal("fbd_cpu_applicability_unresolved")

        condition_rows = {}
        for group in selected:
            for condition in group.conditions:
                encoded = json.dumps(condition, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                condition_rows[encoded] = copy.deepcopy(condition)
        conditions = tuple(condition_rows[key] for key in sorted(condition_rows))
        conditions_status = _conditions_status(conditions, condition_context)
        if conditions_status == "not_satisfied":
            raise _Refusal("fbd_condition_not_satisfied")
        facts = tuple(copy.deepcopy(group.as_mapping()) for group in selected)
        fact_ids = tuple(group.id for group in selected)
        sources = tuple(copy.deepcopy(dict(source)) for group in selected for source in group.sources)
        operation = copy.deepcopy(dict(operation_fact.value)) if operation_fact is not None else None
        contract = FBDInstructionBlockContract(owner_opcode, owner_model, tuple(pins), fact_ids,
                                               facts, conditions, conditions_status, sources,
                                               operation, operation_fact.id if operation_fact else None)
        return FBDInstructionBlockResolution(contract)
    except _Refusal as error:
        return FBDInstructionBlockResolution(None, (error.reason,))
    except (DefinitionError, TypeError, ValueError, AttributeError) as error:
        return FBDInstructionBlockResolution(None, ("invalid_fbd_source_contract:" + type(error).__name__,))
