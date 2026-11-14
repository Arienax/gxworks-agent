"""Source-bound contracts for literal ST operator expressions.

Expressions are intentionally kept apart from callable ST signatures. This
resolver joins exact owner's reviewed expression facts and separately checks
the shared ``ST.OPERATORS`` grammar. Q/L literal operators and FX K source
operator forms retain their own fact shapes. It does not bind a program or
claim execution support.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from plc.instruction_definition import (
    DefinitionError,
    InstructionFactGroup,
    select_fact_dependencies,
)


_SOURCE_EXPRESSION_FACT = "authored:ql:literal_expression:parameters.language_expression"
_TYPED_OPERANDS_FACT = "authored:ql:literal_typed:parameters.typed_operands"
_ARITY_FACT = "authored:ql:literal_argument_ST_expression_operands:parameters.arity"
_ORDER_FACT = "authored:ql:literal_argument_ST_expression_operands:parameters.order"
_CPU_FACT = "authored:ql:cpu_applicability:constraints.cpu_availability"
_GRAMMAR_TARGET = "ST.OPERATORS"
_GRAMMAR_FACT = "st:operator_binding"
_GRAMMAR_DIMENSION = "parameters.operator_binding"
_SUPPORTED_OPERATORS = frozenset({"&", "**", "<", "<=", "<>", "=", ">", ">=",
                                  "MOD", "AND", "XOR", "NOT", "POWER"})
_COMPARISONS = frozenset({"<", "<=", "<>", "=", ">", ">="})


@dataclass(frozen=True)
class StructuredExpressionContract:
    """A validated expression description for a later ST binding path.

    Conditions are preserved and ``conditions_evaluated`` is always false.
    The contract is an ST expression, never a function or a Native instruction.
    """

    operator: str
    model: str
    language: str
    interfaces: tuple[str, ...]
    call_kind: str
    source_available: bool
    representation: str
    operands: tuple[Mapping[str, Any], ...]
    operand_names: tuple[str, ...]
    operand_types: tuple[str, ...]
    minimum_operands: int
    maximum_operands: int
    result_type: str
    operator_priority: int
    same_priority_evaluation: str
    associativity: str
    comparison_chain_intermediate_result_type: str | None
    comparison_operand_type_compatibility: str | None
    fact_ids: tuple[str, ...]
    facts: tuple[Mapping[str, Any], ...]
    applicability_scopes: Mapping[str, Mapping[str, Any]]
    grammar_fact_id: str
    grammar_binding: Mapping[str, Any]
    conditions: tuple[Mapping[str, Any] | str, ...]
    conditions_evaluated: bool
    sources: tuple[Mapping[str, Any], ...]
    operator_form: str = "infix"
    owner_opcode: str | None = None
    variadic: bool = False

    def as_mapping(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "model": self.model,
            "language": self.language,
            "interfaces": list(self.interfaces),
            "call_kind": self.call_kind,
            "source_available": self.source_available,
            "representation": self.representation,
            "operands": copy.deepcopy(list(self.operands)),
            "operand_names": list(self.operand_names),
            "operand_types": list(self.operand_types),
            "minimum_operands": self.minimum_operands,
            "maximum_operands": self.maximum_operands,
            "result_type": self.result_type,
            "operator_priority": self.operator_priority,
            "same_priority_evaluation": self.same_priority_evaluation,
            "associativity": self.associativity,
            "comparison_chain_intermediate_result_type": self.comparison_chain_intermediate_result_type,
            "comparison_operand_type_compatibility": self.comparison_operand_type_compatibility,
            "fact_ids": list(self.fact_ids),
            "facts": copy.deepcopy(list(self.facts)),
            "applicability_scopes": copy.deepcopy(dict(self.applicability_scopes)),
            "grammar_fact_id": self.grammar_fact_id,
            "grammar_binding": copy.deepcopy(dict(self.grammar_binding)),
            "conditions": copy.deepcopy(list(self.conditions)),
            "conditions_evaluated": self.conditions_evaluated,
            "sources": copy.deepcopy(list(self.sources)),
            "operator_form": self.operator_form,
            "owner_opcode": self.owner_opcode or self.operator,
            "variadic": self.variadic,
        }


@dataclass(frozen=True)
class StructuredExpressionResolution:
    """Either a complete source-bound expression contract or refusal reasons."""

    contract: StructuredExpressionContract | None
    refusal_reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.contract is not None and not self.refusal_reasons


class _Refusal(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _scope_matches(group: InstructionFactGroup, *, operator: str, model: str,
                   exact_owner: bool = False) -> bool:
    scope = group.scope or {}
    forms, models, languages = scope.get("forms"), scope.get("models"), scope.get("languages")
    if not (isinstance(forms, (list, tuple)) and operator in forms
            and isinstance(models, (list, tuple)) and model in models
            and isinstance(languages, (list, tuple)) and "st" in languages):
        return False
    if exact_owner and (list(forms) != [operator] or list(models) != [model]):
        return False
    return True


def _page_sources(group: InstructionFactGroup, *, operator: str, model: str,
                  manual_id: str | None = None, revision: str | None = None,
                  required_pages: set[int] | None = None) -> set[tuple[str, str, int]]:
    pages_found: set[tuple[str, str, int]] = set()
    for source in group.sources:
        source_manual, source_revision = source.get("manual_id"), source.get("revision")
        pages = source.get("pdf_pages")
        if (not isinstance(source_manual, str) or not source_manual
                or not isinstance(source_revision, str) or not source_revision
                or not isinstance(pages, (list, tuple)) or not pages
                or any(type(page) is not int or page < 1 for page in pages)):
            continue
        if source.get("opcode") != operator or source.get("target_model") != model:
            continue
        if manual_id is not None and source_manual != manual_id:
            continue
        if revision is not None and source_revision != revision:
            continue
        for page in pages:
            if required_pages is None or page in required_pages:
                pages_found.add((source_manual, source_revision, page))
    return pages_found


def _require_owner_sources(group: InstructionFactGroup, *, operator: str, model: str,
                           manual_id: str | None = None, revision: str | None = None,
                           required_pages: set[int] | None = None) -> None:
    refs = _page_sources(group, operator=operator, model=model,
                         manual_id=manual_id, revision=revision,
                         required_pages=required_pages)
    if required_pages is None:
        if not refs:
            raise _Refusal("source_scope_mismatch:" + group.id)
        return
    if (manual_id is None or revision is None
            or not required_pages.issubset({page for _manual, _revision, page in refs})):
        raise _Refusal("source_pages_incomplete:" + group.id)


def _require_complete_fact(group: InstructionFactGroup, *, operator: str, model: str,
                           exact_owner: bool = True) -> None:
    if group.status != "source_verified" or group.verification != "source_checked":
        raise _Refusal("fact_not_source_verified:" + group.id)
    if not _scope_matches(group, operator=operator, model=model, exact_owner=exact_owner):
        raise _Refusal("fact_scope_not_exact:" + group.id)
    if (group.dimension != "constraints.cpu_availability"
            and list((group.scope or {}).get("languages", ())) != ["st"]):
        raise _Refusal("fact_language_scope_not_st_only:" + group.id)
    value = group.value
    if (value.get("source_conflict") or value.get("source_syntax_conflict")
            or value.get("source_syntax_conflicts") or value.get("unresolved_items")
            or value.get("unbound_condition_markers")
            or value.get("full_source_relation_complete") is False
            or value.get("technical_relation_resolved") is False):
        raise _Refusal("fact_has_unresolved_source_relation:" + group.id)


def _grammar_fact(feature: Mapping[str, Any], *, model: str) -> InstructionFactGroup:
    if feature.get("target", "").upper() != _GRAMMAR_TARGET:
        raise _Refusal("st_operator_grammar_target_mismatch")
    facts = feature.get("facts")
    if not isinstance(facts, (list, tuple)):
        raise _Refusal("st_operator_grammar_missing")
    matches = [InstructionFactGroup.from_mapping(item) for item in facts
               if isinstance(item, Mapping) and item.get("dimension") == _GRAMMAR_DIMENSION]
    matches = [group for group in matches if group.id == _GRAMMAR_FACT]
    if len(matches) != 1:
        raise _Refusal("st_operator_grammar_missing_or_ambiguous")
    group = matches[0]
    scope = group.scope or {}
    if (group.status != "source_verified" or group.verification != "source_checked"
            or _GRAMMAR_TARGET not in scope.get("forms", ())
            or "st" not in scope.get("languages", ())
            or model not in scope.get("models", ())):
        raise _Refusal("st_operator_grammar_not_source_verified_for_model")
    value = group.value
    sources = [source for source in group.sources
               if source.get("manual_id") == "structured_fundamentals_o"
               and source.get("revision") == "O"
               and isinstance(source.get("pdf_pages"), (list, tuple))
               and 76 in source.get("pdf_pages", ())]
    if not sources:
        raise _Refusal("st_operator_grammar_source_missing")
    if value.get("associativity") != "left":
        raise _Refusal("st_operator_associativity_mismatch")
    return group


def _matching_grammar_rule(value: Mapping[str, Any], operator: str) -> Mapping[str, Any]:
    types = value.get("types")
    if not isinstance(types, Mapping):
        raise _Refusal("st_operator_grammar_types_missing")
    rules = [rule for rule in types.values()
             if isinstance(rule, Mapping) and operator in rule.get("operators", ())]
    if len(rules) != 1:
        raise _Refusal("st_operator_grammar_rule_missing_or_ambiguous")
    return rules[0]


def _cpu_state(group: InstructionFactGroup, model: str) -> str:
    states = group.value.get("model_states")
    state = states.get(model) if isinstance(states, Mapping) else None
    available = group.value.get("available")
    if state not in {"supported", "conditional", "unsupported"}:
        raise _Refusal("cpu_applicability_state_missing:" + group.id)
    if available is False or state == "unsupported":
        raise _Refusal("cpu_applicability_denied:" + group.id)
    if available is True and state != "supported":
        raise _Refusal("cpu_applicability_inconsistent:" + group.id)
    if available not in {None, True}:
        raise _Refusal("cpu_applicability_uncertain:" + group.id)
    if state == "conditional" and not group.conditions:
        raise _Refusal("cpu_applicability_conditions_missing:" + group.id)
    return state


def _check_other_cpu_claims(groups: list[InstructionFactGroup], selected: list[InstructionFactGroup],
                            *, operator: str, model: str) -> None:
    selected_ids = {group.id for group in selected}
    selected_cpu = next(group for group in selected if group.dimension == "constraints.cpu_availability")
    selected_context = (list(selected_cpu.conditions), list(selected_cpu.scope.get("versions", ())))
    for group in groups:
        if (group.id in selected_ids
                or group.dimension not in {"constraints.cpu_availability", "identity.applicability"}
                or not _scope_matches(group, operator=operator, model=model, exact_owner=True)):
            continue
        states = group.value.get("model_states")
        has_claim = ("available" in group.value
                     or isinstance(states, Mapping) and model in states)
        if not has_claim:
            continue
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal("unverified_cpu_applicability:" + group.id)
        _require_owner_sources(group, operator=operator, model=model)
        claim = _cpu_state(group, model)
        context = (list(group.conditions), list(group.scope.get("versions", ())))
        if claim != "supported" or context != selected_context:
            raise _Refusal("conflicting_cpu_applicability:" + group.id)


def _normalise_facts(facts: Any) -> tuple[list[InstructionFactGroup], list[Mapping[str, Any]]]:
    if not isinstance(facts, (list, tuple)):
        raise _Refusal("owner_facts_required")
    groups: list[InstructionFactGroup] = []
    raw_groups: list[Mapping[str, Any]] = []
    for raw in facts:
        if not isinstance(raw, Mapping):
            raise _Refusal("invalid_fact_group")
        groups.append(InstructionFactGroup.from_mapping(raw))
        raw_groups.append(raw)
    ids = [group.id for group in groups]
    if len(ids) != len(set(ids)):
        raise _Refusal("duplicate_fact_id")
    if any(raw.get("source_conflict") for raw in raw_groups):
        raise _Refusal("owner_source_conflict")
    return groups, raw_groups


def _select_one(groups: list[InstructionFactGroup], *, dimension: str,
                operator: str, model: str, value_key: str | None = None) -> InstructionFactGroup:
    found = [group for group in groups if group.dimension == dimension
             and _scope_matches(group, operator=operator, model=model, exact_owner=True)]
    if value_key is not None:
        found = [group for group in found if group.value.get(value_key) == operator]
    if len(found) != 1:
        raise _Refusal(f"{dimension.replace('.', '_')}_missing_or_ambiguous")
    return found[0]


def _validate_non_callable_signature(groups: list[InstructionFactGroup], *,
                                     operator: str, model: str) -> None:
    signatures = [group for group in groups if group.dimension == "parameters.st_signature"
                  and _scope_matches(group, operator=operator, model=model, exact_owner=True)]
    callable_signatures = [group for group in signatures if group.value.get("available") is True]
    if callable_signatures:
        raise _Refusal("expression_conflicts_with_callable_signature")
    if len(signatures) != 1:
        raise _Refusal("expression_non_callable_signature_missing_or_ambiguous")
    for group in signatures:
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal("st_signature_state_unverified:" + group.id)
        if group.value.get("available") is not False:
            raise _Refusal("st_signature_state_ambiguous:" + group.id)
        if group.value.get("reason") != "literal_ST_operator_is_an_expression":
            raise _Refusal("st_signature_not_expression_marker:" + group.id)
        _require_owner_sources(group, operator=operator, model=model)


def _default_grammar_feature(model: str) -> Mapping[str, Any]:
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    feature = DEFAULT_INSTRUCTION_REGISTRY.language_feature(
        _GRAMMAR_TARGET, language="st", cpu=model)
    if not isinstance(feature, Mapping):
        raise _Refusal("st_operator_grammar_missing")
    return feature


def _source_languages_match(group: InstructionFactGroup, *, operator: str,
                           model: str, exact_owner: bool = True) -> bool:
    if not _scope_matches(group, operator=operator, model=model, exact_owner=exact_owner):
        return False
    languages = (group.scope or {}).get("languages", ())
    return isinstance(languages, (list, tuple)) and "st" in languages


def _fx_k_pages(group: InstructionFactGroup) -> set[int]:
    pages: set[int] = set()
    for source in group.sources:
        if source.get("manual_id") != "fxcpu_application_k" or source.get("revision") != "K":
            continue
        raw_pages = source.get("pdf_pages")
        if isinstance(raw_pages, (list, tuple)):
            pages.update(page for page in raw_pages if type(page) is int and page > 0)
    return pages


def _fx_k_form(group: InstructionFactGroup) -> tuple[str, str, list[str]]:
    """Return (token, form, placeholders) from the owner's literal ST form."""
    value = group.value
    form_value, infix = value.get("expression_form"), value.get("infix")
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if type(infix) is not bool:
        raise _Refusal("operator_expression_form_missing")
    form_kind = "infix" if infix else "prefix"
    if infix:
        if isinstance(form_value, (list, tuple)):
            if (len(form_value) != 3 or not all(isinstance(item, str) and item for item in form_value)
                    or not re.fullmatch(identifier, form_value[0].strip())
                    or not re.fullmatch(identifier, form_value[2].strip())):
                raise _Refusal("operator_expression_infix_shape_invalid")
            token = form_value[1].strip().upper()
            placeholders = [form_value[0].strip(), form_value[2].strip()]
        elif isinstance(form_value, str):
            match = re.fullmatch(r"\s*(" + identifier + r")\s*(.+?)\s*(" + identifier + r")\s*", form_value)
            if not match:
                raise _Refusal("operator_expression_infix_shape_invalid")
            token = match.group(2).strip().upper()
            placeholders = [match.group(1), match.group(3)]
        else:
            raise _Refusal("operator_expression_infix_shape_invalid")
    else:
        if not isinstance(form_value, str):
            raise _Refusal("operator_expression_prefix_shape_invalid")
        match = re.fullmatch(
            r"\s*(" + identifier + r")\s*\(\s*(" + identifier + r")\s*\)\s*",
            form_value)
        if not match:
            raise _Refusal("operator_expression_prefix_shape_invalid")
        token, placeholders = match.group(1).upper(), [match.group(2)]

    symbol = value.get("symbol")
    if not isinstance(symbol, Mapping):
        raise _Refusal("operator_expression_symbol_missing")
    symbol_name = symbol.get("name")
    ascii_hex = symbol.get("ascii_hex")
    if ascii_hex is not None:
        if not isinstance(ascii_hex, str) or len(ascii_hex) % 2:
            raise _Refusal("operator_expression_symbol_invalid")
        try:
            symbol_token = bytes.fromhex(ascii_hex).decode("ascii").upper()
        except (ValueError, UnicodeDecodeError):
            raise _Refusal("operator_expression_symbol_invalid")
        if symbol_token != token:
            raise _Refusal("operator_expression_symbol_form_mismatch")
    elif not isinstance(symbol_name, str) or symbol_name.upper() != token:
        raise _Refusal("operator_expression_symbol_form_mismatch")
    return token, form_kind, placeholders


def _fx_type_token(value: Any) -> str:
    if isinstance(value, str) and value:
        return value.upper()
    if not isinstance(value, Mapping):
        raise _Refusal("operator_expression_type_missing")
    flexible = value.get("flexible_type")
    if isinstance(flexible, str) and flexible:
        return flexible.upper()
    kind, bits = value.get("kind"), value.get("bits")
    if kind == "real" and bits == 32:
        return "REAL"
    if kind == "real" and bits == 64:
        return "LREAL"
    if kind == "bool" and bits in {None, 1}:
        return "BOOL"
    if kind == "int" and type(bits) is int:
        signed = value.get("signed", True)
        return ({True: {8: "SINT", 16: "INT", 32: "DINT", 64: "LINT"},
                 False: {8: "USINT", 16: "UINT", 32: "UDINT", 64: "ULINT"}}
                .get(signed, {}).get(bits, "")) or _raise_type_token()
    raise _Refusal("operator_expression_type_unsupported")


def _raise_type_token():
    raise _Refusal("operator_expression_type_unsupported")


_FX_TYPE_FAMILIES = {
    "ANY_BIT": {"BOOL", "BIT", "WORD", "DWORD", "ANY_BIT"},
    "ANY_INT": {"SINT", "USINT", "INT", "UINT", "DINT", "UDINT", "LINT", "ULINT", "ANY_INT"},
    "ANY_NUM": {"SINT", "USINT", "INT", "UINT", "DINT", "UDINT", "LINT", "ULINT",
                 "REAL", "LREAL", "ANY_INT", "ANY_REAL", "ANY_NUM"},
    "ANY_REAL": {"REAL", "LREAL", "ANY_REAL"},
    "ANY_SIMPLE": {"BOOL", "BIT", "WORD", "DWORD", "SINT", "USINT", "INT", "UINT", "DINT",
                   "UDINT", "LINT", "ULINT", "REAL", "LREAL", "TIME", "STRING", "ANY_SIMPLE"},
}


def _fx_type_matches(source_type: str, grammar_type: str) -> bool:
    return source_type == grammar_type or source_type in _FX_TYPE_FAMILIES.get(grammar_type, set())


def _fx_candidate_groups(groups: list[InstructionFactGroup], *, dimension: str,
                         owner: str, model: str, page_set: set[int],
                         value_check=None) -> list[InstructionFactGroup]:
    matches = []
    for group in groups:
        if group.dimension != dimension or not _source_languages_match(
                group, operator=owner, model=model, exact_owner=True):
            continue
        overlap = page_set.intersection(_fx_k_pages(group))
        if not overlap:
            continue
        if value_check is not None and not value_check(group.value):
            continue
        matches.append(group)
    return matches


def _fx_primary_groups(candidates: list[InstructionFactGroup], *, owner: str,
                       expression: InstructionFactGroup) -> list[InstructionFactGroup]:
    """Prefer explicit expression dependencies, then its source namespace.

    The composer may add ``assembly:`` copies of source facts to express a
    relation. Those copies are useful in the graph, but they do not provide a
    second independent source contract and must not compete with the original
    owner fact.
    """
    dependencies = set(expression.depends_on)
    declared = [group for group in candidates if group.id in dependencies]
    if declared:
        return declared
    namespace = f"structured:{owner.lower()}:"
    return [group for group in candidates
            if group.id.startswith(namespace) and not group.id.startswith("assembly:")]


def _fx_exact_group(groups: list[InstructionFactGroup], *, dimension: str,
                    owner: str, model: str, page_set: set[int],
                    expression: InstructionFactGroup, value_check=None) -> InstructionFactGroup:
    candidates = _fx_candidate_groups(groups, dimension=dimension, owner=owner,
                                      model=model, page_set=page_set,
                                      value_check=value_check)
    matches = _fx_primary_groups(candidates, owner=owner, expression=expression)
    if matches:
        strongest = max(len(page_set.intersection(_fx_k_pages(group))) for group in matches)
        matches = [group for group in matches
                   if len(page_set.intersection(_fx_k_pages(group))) == strongest]
    if len(matches) != 1:
        raise _Refusal(dimension.replace(".", "_") + "_missing_or_ambiguous")
    return matches[0]


def _fx_dependency_closure(groups: list[InstructionFactGroup], group: InstructionFactGroup,
                           *, owner: str, model: str, version: str | None
                           ) -> list[InstructionFactGroup]:
    if not group.depends_on:
        return [group]
    closure_result = select_fact_dependencies(
        groups, [group.id], opcode=owner, model=model, language="st", version=version)
    if closure_result["gaps"]:
        reasons = ",".join(sorted({str(gap.get("reason")) for gap in closure_result["gaps"]}))
        raise _Refusal("dependency_gaps:" + reasons)
    closure = [InstructionFactGroup.from_mapping(item) for item in closure_result["groups"]]
    if not closure_result["source_verification_complete"]:
        raise _Refusal("dependency_closure_not_source_verified")
    if not any(item.id == group.id for item in closure):
        raise _Refusal("required_expression_dependency_missing:" + group.id)
    return closure


def _fx_applicability_signature(groups: list[InstructionFactGroup], *, owner: str,
                                model: str) -> tuple[str, ...]:
    signatures = []
    for group in groups:
        scope = copy.deepcopy(dict(group.scope or {}))
        for key, selected in (("forms", owner), ("models", model), ("languages", "st")):
            values = scope.get(key)
            if not isinstance(values, (list, tuple)) or selected not in values:
                raise _Refusal("cpu_applicability_scope_mismatch:" + group.id)
            scope[key] = [selected]
        for key, values in tuple(scope.items()):
            if key not in {"forms", "models", "languages"} and isinstance(values, (list, tuple)):
                scope[key] = sorted(set(values), key=str)
        try:
            conditions = sorted(json.dumps(condition, ensure_ascii=True, sort_keys=True,
                                           separators=(",", ":"))
                                for condition in group.conditions)
            signatures.append(json.dumps({"scope": scope, "conditions": conditions},
                                         ensure_ascii=True, sort_keys=True,
                                         separators=(",", ":")))
        except (TypeError, ValueError):
            raise _Refusal("cpu_applicability_conditions_uncomparable:" + group.id)
    return tuple(sorted(signatures))


def _fx_cpu_groups(groups: list[InstructionFactGroup], *, owner: str, model: str,
                   page_set: set[int], expression: InstructionFactGroup,
                   version: str | None) -> list[InstructionFactGroup]:
    candidates = _fx_candidate_groups(groups, dimension="constraints.cpu_availability",
                                      owner=owner, model=model, page_set=page_set)
    primary = _fx_primary_groups(candidates, owner=owner, expression=expression)
    if not primary:
        raise _Refusal("constraints_cpu_availability_missing_or_ambiguous")

    # Keep every source-backed claim for the exact expression page, but only
    # when it agrees with the primary owner record on CPU state and all
    # applicability conditions. A conflicting duplicate remains a refusal.
    claim_closures = []
    for candidate in candidates:
        if candidate.status != "source_verified" or candidate.verification != "source_checked":
            raise _Refusal("cpu_applicability_not_source_verified:" + candidate.id)
        closure = _fx_dependency_closure(groups, candidate, owner=owner,
                                         model=model, version=version)
        for fact in closure:
            if fact.status != "source_verified" or fact.verification != "source_checked":
                raise _Refusal("dependency_closure_not_source_verified")
        state = _cpu_state(candidate, model)
        if state not in {"supported", "conditional"}:
            raise _Refusal("cpu_applicability_not_admitted:" + candidate.id)
        claim_closures.append((state, closure,
                               _fx_applicability_signature(closure, owner=owner, model=model)))

    primary_rows = [row for row in claim_closures
                    if row[1][0].id in {group.id for group in primary}]
    if len(primary_rows) != len(primary):
        raise _Refusal("constraints_cpu_availability_missing_or_ambiguous")
    expected_state = primary_rows[0][0]
    expected_signature = primary_rows[0][2]
    if any(state != expected_state or signature != expected_signature
           for state, _closure, signature in claim_closures):
        raise _Refusal("cpu_applicability_conflicting_claims")

    retained = {}
    for _state, closure, _signature in claim_closures:
        for fact in closure:
            retained[fact.id] = fact
    return list(retained.values())


def _fx_check_other_cpu_claims(groups: list[InstructionFactGroup],
                               selected: list[InstructionFactGroup], *, owner: str,
                               model: str, page_set: set[int], version: str | None) -> None:
    """Reject conflicting applicability claims tied to this operator's page.

    FX owners may also have CPU claims for the same mnemonic's function form
    on a different K-manual page. Those claims do not decide availability of
    the literal operator expression, so only exact-owner K claims bound to
    this expression's own page participate here.
    """
    selected_ids = {group.id for group in selected}
    selected_cpu = [group for group in selected
                    if group.dimension == "constraints.cpu_availability"]
    if not selected_cpu:
        raise _Refusal("constraints_cpu_availability_missing_or_ambiguous")
    baseline = selected_cpu[0]
    baseline_state = _cpu_state(baseline, model)
    baseline_closure = _fx_dependency_closure(groups, baseline, owner=owner,
                                              model=model, version=version)
    baseline_signature = _fx_applicability_signature(baseline_closure,
                                                      owner=owner, model=model)
    for group in groups:
        if (group.id in selected_ids
                or group.dimension not in {"constraints.cpu_availability", "identity.applicability"}
                or not _source_languages_match(group, operator=owner, model=model, exact_owner=True)
                or not page_set.intersection(_fx_k_pages(group))):
            continue
        states = group.value.get("model_states")
        has_claim = ("available" in group.value
                     or isinstance(states, Mapping) and model in states)
        if not has_claim:
            continue
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal("unverified_cpu_applicability:" + group.id)
        closure = _fx_dependency_closure(groups, group, owner=owner,
                                         model=model, version=version)
        state = _cpu_state(group, model)
        signature = _fx_applicability_signature(closure, owner=owner, model=model)
        if state != baseline_state or signature != baseline_signature:
            raise _Refusal("cpu_applicability_conflicting_claims:" + group.id)


def _find_fx_k_expression(groups: list[InstructionFactGroup], *, operator: str,
                          owner_opcode: str | None, model: str) -> InstructionFactGroup | None:
    requested = str(operator).strip().upper()
    matches = []
    for group in groups:
        if (group.dimension != "parameters.operator_expression"
                or not isinstance(group.scope, Mapping)):
            continue
        scope_forms = group.scope.get("forms")
        if not isinstance(scope_forms, (list, tuple)) or len(scope_forms) != 1:
            continue
        owner = scope_forms[0]
        if not _source_languages_match(group, operator=owner, model=model, exact_owner=True):
            continue
        if owner_opcode is not None and owner.upper() != str(owner_opcode).strip().upper():
            continue
        if "fxcpu_application_k" not in {source.get("manual_id") for source in group.sources}:
            continue
        token, _form_kind, _placeholders = _fx_k_form(group)
        if requested in {token, owner.upper()}:
            matches.append(group)
    if len(matches) > 1:
        raise _Refusal("source_operator_expression_missing_or_ambiguous")
    return matches[0] if matches else None


def _resolve_fx_k_expression_contract(
    groups: list[InstructionFactGroup], *, operator: str, model: str,
    owner_opcode: str | None, grammar: InstructionFactGroup,
    grammar_rule: Mapping[str, Any], version: str | None,
) -> StructuredExpressionContract:
    expression_rows = []
    for group in groups:
        if (group.dimension != "parameters.operator_expression"
                or not _source_languages_match(group, operator=(owner_opcode or operator),
                    model=model, exact_owner=True)
                or "fxcpu_application_k" not in {source.get("manual_id") for source in group.sources}):
            continue
        expression_rows.append(group)
    if len(expression_rows) != 1:
        raise _Refusal("source_operator_expression_missing_or_ambiguous")
    expression = expression_rows[0]
    owner = (expression.scope or {}).get("forms", [None])[0]
    if not isinstance(owner, str) or not owner:
        raise _Refusal("source_operator_owner_missing")
    token, operator_form, placeholders = _fx_k_form(expression)
    if token not in _SUPPORTED_OPERATORS | {"MOD", "XOR", "NOT"}:
        raise _Refusal("unsupported_st_expression_operator")
    requested = str(operator).strip().upper()
    if requested not in {token, owner.upper()}:
        raise _Refusal("source_operator_token_mismatch")
    if owner_opcode is not None and str(owner_opcode).strip().upper() != owner.upper():
        raise _Refusal("source_operator_owner_mismatch")
    if operator_form == "prefix" and token != "NOT":
        raise _Refusal("unsupported_source_prefix_operator")
    if operator_form == "infix" and token == "NOT":
        raise _Refusal("source_operator_form_mismatch")

    page_set = _fx_k_pages(expression)
    if not page_set:
        raise _Refusal("source_operator_page_missing")
    typed = _fx_exact_group(groups, dimension="parameters.typed_operands", owner=owner,
                            model=model, page_set=page_set, expression=expression)
    result = _fx_exact_group(groups, dimension="effects.result_rule", owner=owner,
                             model=model, page_set=page_set, expression=expression)
    cpu_groups = _fx_cpu_groups(groups, owner=owner, model=model, page_set=page_set,
                                expression=expression, version=version)
    identity = _fx_exact_group(groups, dimension="identity.form", owner=owner,
        model=model, page_set=page_set, expression=expression,
        value_check=lambda value: value.get("category") == "operator"
            and value.get("callable_kind") == "operator"
            and value.get("call_basis") == "operator_expression")
    language = _fx_exact_group(groups, dimension="identity.language_availability", owner=owner,
        model=model, page_set=page_set, expression=expression,
        value_check=lambda value: value.get("language") == "st" and value.get("available") is True)

    selected = [expression, typed, result, *cpu_groups, identity, language]
    common_pages = set.intersection(*(_fx_k_pages(group) for group in selected))
    if not common_pages:
        raise _Refusal("source_operator_page_binding_mismatch")
    for group in selected:
        if (group.status != "source_verified" or group.verification != "source_checked"
                or not _source_languages_match(group, operator=owner, model=model, exact_owner=True)):
            raise _Refusal("fact_not_source_verified:" + group.id)
        value = group.value
        if (value.get("source_conflict") or value.get("source_syntax_conflict")
                or value.get("source_syntax_conflicts") or value.get("unresolved_items")
                or value.get("unbound_condition_markers")
                or value.get("full_source_relation_complete") is False
                or value.get("technical_relation_resolved") is False):
            raise _Refusal("fact_has_unresolved_source_relation:" + group.id)

    # Any declared dependency must remain resolvable in the same exact owner.
    roots = [group.id for group in selected]
    if any(group.depends_on for group in selected):
        closure_result = select_fact_dependencies(
            groups, roots, opcode=owner, model=model, language="st", version=version)
        if closure_result["gaps"]:
            reasons = ",".join(sorted({str(gap.get("reason")) for gap in closure_result["gaps"]}))
            raise _Refusal("dependency_gaps:" + reasons)
        closure = [InstructionFactGroup.from_mapping(item) for item in closure_result["groups"]]
        if not closure_result["source_verification_complete"]:
            raise _Refusal("dependency_closure_not_source_verified")
        by_id = {group.id: group for group in closure}
        if any(group.id not in by_id for group in selected):
            raise _Refusal("required_expression_dependency_missing")
        selected = closure

    resolved_cpu_groups = [group for group in selected
                           if group.dimension == "constraints.cpu_availability"]
    if (not resolved_cpu_groups
            or any(_cpu_state(group, model) not in {"supported", "conditional"}
                   for group in resolved_cpu_groups)):
        raise _Refusal("cpu_applicability_not_admitted")
    _fx_check_other_cpu_claims(groups, selected, owner=owner, model=model,
                               page_set=page_set, version=version)

    typed_rows = typed.value.get("operands")
    return_shape = typed.value.get("return_shape")
    if not isinstance(typed_rows, (list, tuple)) or not isinstance(return_shape, Mapping):
        raise _Refusal("operator_expression_typed_operands_missing")
    input_rows = [row for row in typed_rows if isinstance(row, Mapping) and row.get("direction") == "input"]
    output_rows = [row for row in typed_rows if isinstance(row, Mapping)
                   and row.get("direction") in {"output", "output_variable"}]
    if len(input_rows) == 0 or len(output_rows) != 1 or len(input_rows) + len(output_rows) != len(typed_rows):
        raise _Refusal("operator_expression_operand_directions_incomplete")
    input_names, input_types = [], []
    for index, row in enumerate(input_rows):
        name, marker = row.get("name"), row.get("marker")
        if not isinstance(name, str) or not name or not isinstance(marker, str) or not marker:
            raise _Refusal(f"invalid_operator_expression_operand:{index}")
        input_names.append(marker)
        input_types.append(_fx_type_token(row.get("data_type")))
    result_type = _fx_type_token(return_shape.get("value_type"))
    output_type = _fx_type_token(output_rows[0].get("data_type"))
    if result_type != output_type:
        raise _Refusal("operator_expression_result_type_mismatch")
    if return_shape.get("infix_expression") is not (operator_form == "infix"):
        raise _Refusal("operator_expression_return_form_mismatch")

    if operator_form == "prefix":
        if placeholders != input_names or len(input_names) != 1:
            raise _Refusal("operator_expression_prefix_arity_mismatch")
        minimum = maximum = 1
    elif token in {"AND", "XOR"}:
        pins = result.value.get("operand_pins")
        if (not isinstance(pins, Mapping) or type(pins.get("minimum")) is not int
                or type(pins.get("maximum")) is not int
                or pins["minimum"] < 2 or pins["maximum"] < pins["minimum"]
                or len(input_names) != 1 or placeholders != [input_names[0], "s2"]):
            raise _Refusal("operator_expression_variadic_arity_mismatch")
        minimum, maximum = pins["minimum"], pins["maximum"]
    else:
        if placeholders != input_names:
            raise _Refusal("operator_expression_order_contract_mismatch")
        minimum = maximum = len(input_names)

    grammar_inputs = grammar_rule.get("inputs")
    if not isinstance(grammar_inputs, (list, tuple)) or not grammar_inputs:
        raise _Refusal("st_operator_grammar_input_types_missing")
    if len(grammar_inputs) > 1 and len(input_types) != len(grammar_inputs):
        raise _Refusal("expression_types_conflict_with_st_grammar")
    if any(not _fx_type_matches(actual, grammar_inputs[min(i, len(grammar_inputs) - 1)])
           for i, actual in enumerate(input_types)):
        raise _Refusal("expression_types_conflict_with_st_grammar")
    grammar_result = grammar_rule.get("result")
    if not isinstance(grammar_result, str) or not _fx_type_matches(result_type, grammar_result):
        raise _Refusal("expression_result_type_conflicts_with_st_grammar")
    if result.value.get("operand_pins") is not None and token not in {"AND", "XOR"}:
        raise _Refusal("unexpected_variadic_operator_result_contract")

    applicable_conditions: list[Mapping[str, Any] | str] = []
    all_sources = []
    for group in selected:
        for condition in group.conditions:
            if condition not in applicable_conditions:
                applicable_conditions.append(copy.deepcopy(condition))
        for source in group.sources:
            if source not in all_sources:
                all_sources.append(copy.deepcopy(dict(source)))
    for source in grammar.sources:
        if source not in all_sources:
            all_sources.append(copy.deepcopy(dict(source)))
    grammar_value = grammar.value
    priority_table = grammar_value.get("precedence")
    priority = priority_table.get(token) if isinstance(priority_table, Mapping) else None
    if type(priority) is not int:
        raise _Refusal("st_operator_precedence_missing")
    feature_copy = {"target": _GRAMMAR_TARGET, "fact_id": grammar.id,
                    "value": copy.deepcopy(dict(grammar_value)),
                    "scope": copy.deepcopy(dict(grammar.scope or {})),
                    "sources": copy.deepcopy(list(grammar.sources))}
    return StructuredExpressionContract(
        operator=token, model=model, language="st", interfaces=("st",), call_kind="expression",
        source_available=True, representation="ST_expression", operands=tuple(copy.deepcopy(input_rows)),
        operand_names=tuple(input_names), operand_types=tuple(input_types),
        minimum_operands=minimum, maximum_operands=maximum, result_type=result_type,
        operator_priority=priority, same_priority_evaluation="leftmost_operator_first",
        associativity=str(grammar_value.get("associativity")),
        comparison_chain_intermediate_result_type=None,
        comparison_operand_type_compatibility=None,
        fact_ids=tuple(group.id for group in selected),
        facts=tuple(group.as_mapping() for group in selected),
        applicability_scopes={group.id: copy.deepcopy(dict(group.scope or {})) for group in selected},
        grammar_fact_id=grammar.id, grammar_binding=feature_copy,
        conditions=tuple(applicable_conditions), conditions_evaluated=False,
        sources=tuple(all_sources), operator_form=operator_form, owner_opcode=owner,
        variadic=maximum > minimum)


def resolve_structured_expression_contract(
    facts,
    *,
    operator: str,
    model: str,
    owner_opcode: str | None = None,
    version: str | None = None,
    language_feature: Mapping[str, Any] | None = None,
) -> StructuredExpressionResolution:
    """Resolve one reviewed literal operator for an exact model and ST context.

    ``language_feature`` may be supplied from the registry's
    ``language_feature('ST.OPERATORS', language='st', cpu=model)``.  When
    omitted, the current default registry is used.  Conditions are retained,
    never evaluated here.
    """
    if not isinstance(operator, str) or operator.strip().upper() not in _SUPPORTED_OPERATORS:
        return StructuredExpressionResolution(None, ("unsupported_st_expression_operator",))
    if not isinstance(model, str) or not model.strip():
        return StructuredExpressionResolution(None, ("exact_model_required",))
    model = model.strip().upper()
    operator = operator.strip().upper()
    try:
        groups, _raw_groups = _normalise_facts(facts)
        fx_expression = _find_fx_k_expression(groups, operator=operator,
                                               owner_opcode=owner_opcode, model=model)
        if fx_expression is None and owner_opcode is not None:
            matching_token = _find_fx_k_expression(groups, operator=operator,
                                                    owner_opcode=None, model=model)
            if matching_token is not None:
                raise _Refusal("source_operator_owner_mismatch")
        if fx_expression is not None:
            fx_owner = (fx_expression.scope or {}).get("forms", [None])[0]
            token, _operator_form, _placeholders = _fx_k_form(fx_expression)
            if language_feature is None:
                language_feature = _default_grammar_feature(model)
            grammar = _grammar_fact(language_feature, model=model)
            grammar_value = grammar.value
            priority_table = grammar_value.get("precedence")
            if not isinstance(priority_table, Mapping) or type(priority_table.get(token)) is not int:
                raise _Refusal("st_operator_precedence_missing")
            grammar_rule = _matching_grammar_rule(grammar_value, token)
            contract = _resolve_fx_k_expression_contract(
                groups, operator=operator, model=model,
                owner_opcode=str(owner_opcode or fx_owner), grammar=grammar,
                grammar_rule=grammar_rule, version=version)
            return StructuredExpressionResolution(contract, ())
        if operator == "POWER":
            raise _Refusal("source_operator_expression_missing_or_ambiguous")
        if language_feature is None:
            language_feature = _default_grammar_feature(model)
        grammar = _grammar_fact(language_feature, model=model)
        grammar_value = grammar.value
        priority_table = grammar_value.get("precedence")
        if not isinstance(priority_table, Mapping) or type(priority_table.get(operator)) is not int:
            raise _Refusal("st_operator_precedence_missing")
        grammar_rule = _matching_grammar_rule(grammar_value, operator)

        expression = _select_one(groups, dimension="parameters.language_expression",
                                  operator=operator, model=model, value_key="operator")
        typed = _select_one(groups, dimension="parameters.typed_operands",
                            operator=operator, model=model)
        arity = _select_one(groups, dimension="parameters.arity", operator=operator, model=model)
        order = _select_one(groups, dimension="parameters.order", operator=operator, model=model)
        cpu = _select_one(groups, dimension="constraints.cpu_availability",
                          operator=operator, model=model)
        if not {_TYPED_OPERANDS_FACT, _ARITY_FACT, _ORDER_FACT, _CPU_FACT}.issubset(expression.depends_on):
            raise _Refusal("expression_dependency_contract_incomplete")
        roots = [expression.id]
        closure_result = select_fact_dependencies(
            groups, roots, opcode=operator, model=model, language="st", version=version)
        if closure_result["gaps"]:
            reasons = ",".join(sorted({str(gap.get("reason")) for gap in closure_result["gaps"]}))
            raise _Refusal("dependency_gaps:" + reasons)
        closure = [InstructionFactGroup.from_mapping(item) for item in closure_result["groups"]]
        if not closure_result["source_verification_complete"]:
            raise _Refusal("dependency_closure_not_source_verified")
        by_id = {group.id: group for group in closure}
        for required in (typed, arity, order, cpu):
            if required.id not in by_id:
                raise _Refusal("required_expression_dependency_missing:" + required.id)
        if (expression.id != _SOURCE_EXPRESSION_FACT or typed.id != _TYPED_OPERANDS_FACT
                or arity.id != _ARITY_FACT or order.id != _ORDER_FACT or cpu.id != _CPU_FACT):
            raise _Refusal("expression_fact_namespace_mismatch")
        for group in closure:
            _require_complete_fact(group, operator=operator, model=model)
            _require_owner_sources(group, operator=operator, model=model)
        required_n_pages = {214} if operator == "**" else {215} if operator == "&" else {217}
        required_expression_pages = set(required_n_pages)
        if operator == "&":
            required_expression_pages.add(216)
        _require_owner_sources(expression, operator=operator, model=model,
                              manual_id="sh080784engn", revision="N",
                              required_pages=required_expression_pages)
        _require_owner_sources(expression, operator=operator, model=model,
                              manual_id="structured_fundamentals_o", revision="O",
                              required_pages={76})
        for group in (typed, arity, order):
            _require_owner_sources(group, operator=operator, model=model,
                                  manual_id="sh080784engn", revision="N",
                                  required_pages=required_n_pages)
        if _cpu_state(cpu, model) not in {"supported", "conditional"}:
            raise _Refusal("cpu_applicability_not_admitted")
        _check_other_cpu_claims(groups, closure, operator=operator, model=model)

        _validate_non_callable_signature(groups, operator=operator, model=model)
        expression_value, typed_value = expression.value, typed.value
        if (expression_value.get("available") is not True
                or expression_value.get("representation") != "ST_expression"
                or expression_value.get("operator") != operator):
            raise _Refusal("expression_not_available_as_st_expression")
        syntax_history = expression_value.get("source_syntax_history")
        if (syntax_history is not None
                and (operator != "&" or not isinstance(syntax_history, Mapping)
                     or syntax_history.get("resolution") != "resolved_by_own_page_216_ST_program_example")):
            raise _Refusal("expression_source_syntax_history_unresolved")
        priority = expression_value.get("operator_priority")
        if type(priority) is not int or priority != priority_table[operator]:
            raise _Refusal("expression_precedence_conflicts_with_st_grammar")
        if expression_value.get("same_priority_evaluation") != "leftmost_operator_first":
            raise _Refusal("expression_associativity_not_source_bound")

        operands = typed_value.get("operands")
        if not isinstance(operands, (list, tuple)) or not operands:
            raise _Refusal("expression_operands_missing")
        names, types = [], []
        for index, operand in enumerate(operands):
            if (not isinstance(operand, Mapping) or not isinstance(operand.get("name"), str)
                    or not operand.get("name") or operand.get("direction") != "input"
                    or not isinstance(operand.get("type"), str) or not operand.get("type")):
                raise _Refusal(f"invalid_expression_operand:{index}")
            names.append(operand["name"])
            types.append(operand["type"])
            optional = operand.get("optional")
            if optional is not None and type(optional) is not bool:
                raise _Refusal(f"invalid_expression_operand_optionality:{index}")
        arity_value, order_value = arity.value, order.value
        minimum, maximum = arity_value.get("minimum"), arity_value.get("maximum")
        expected_maximum = 2 if operator in {"**", "<>"} else 28
        if (type(minimum) is not int or type(maximum) is not int
                or minimum != 2 or maximum != expected_maximum
                or arity_value.get("count_kind") != "ST_expression_operands"):
            raise _Refusal("expression_arity_contract_mismatch")
        if (len(operands) != maximum
                or sum(operand.get("optional") is not True for operand in operands) != minimum
                or typed_value.get("operand_count") != maximum):
            raise _Refusal("typed_expression_arity_mismatch")
        if (order_value.get("symbols") != names
                or order_value.get("order_source") != "ST_expression_operands"
                or expression_value.get("operands") != names):
            raise _Refusal("expression_order_contract_mismatch")
        if (not isinstance(expression_value.get("returns"), str)
                or not expression_value["returns"]):
            raise _Refusal("expression_result_type_missing")
        grammar_inputs = grammar_rule.get("inputs")
        if not isinstance(grammar_inputs, (list, tuple)) or not grammar_inputs:
            raise _Refusal("st_operator_grammar_input_types_missing")
        expected_types = list(grammar_inputs) if len(grammar_inputs) == len(types) else [grammar_inputs[0]] * len(types)
        if types != expected_types or expression_value["returns"] != grammar_rule.get("result"):
            raise _Refusal("expression_types_conflict_with_st_grammar")
        if (operator in {"**", "<>"} and minimum != maximum
                or operator not in {"**", "<>"} and maximum != 28):
            raise _Refusal("expression_operator_arity_limit_mismatch")

        chain_type = expression_value.get("comparison_chain_intermediate_result_type")
        compatibility = expression_value.get("comparison_operand_type_compatibility")
        if operator in _COMPARISONS:
            if chain_type != "Bit" or compatibility != "not_stated_in_reviewed_source":
                raise _Refusal("comparison_chain_contract_incomplete")
        elif chain_type is not None or compatibility is not None:
            raise _Refusal("non_comparison_has_chain_contract")

        applicable_conditions: list[Mapping[str, Any] | str] = []
        for group in closure:
            for condition in group.conditions:
                if condition not in applicable_conditions:
                    applicable_conditions.append(copy.deepcopy(condition))
        feature_copy = {
            "target": _GRAMMAR_TARGET,
            "fact_id": grammar.id,
            "value": copy.deepcopy(dict(grammar_value)),
            "scope": copy.deepcopy(dict(grammar.scope or {})),
            "sources": copy.deepcopy(list(grammar.sources)),
        }
        all_sources = []
        for group in closure:
            for source in group.sources:
                if source not in all_sources:
                    all_sources.append(copy.deepcopy(dict(source)))
        for source in grammar.sources:
            if source not in all_sources:
                all_sources.append(copy.deepcopy(dict(source)))
        contract = StructuredExpressionContract(
            operator=operator,
            model=model,
            language="st",
            interfaces=("st",),
            call_kind="expression",
            source_available=True,
            representation="ST_expression",
            operands=tuple(copy.deepcopy(list(operands))),
            operand_names=tuple(names),
            operand_types=tuple(types),
            minimum_operands=minimum,
            maximum_operands=maximum,
            result_type=expression_value["returns"],
            operator_priority=priority,
            same_priority_evaluation=expression_value["same_priority_evaluation"],
            associativity=str(grammar_value.get("associativity")),
            comparison_chain_intermediate_result_type=chain_type,
            comparison_operand_type_compatibility=compatibility,
            fact_ids=tuple(group.id for group in closure),
            facts=tuple(group.as_mapping() for group in closure),
            applicability_scopes={group.id: copy.deepcopy(dict(group.scope or {}))
                                  for group in closure},
            grammar_fact_id=grammar.id,
            grammar_binding=feature_copy,
            conditions=tuple(applicable_conditions),
            conditions_evaluated=False,
            sources=tuple(all_sources),
            operator_form="infix",
            owner_opcode=operator,
            variadic=maximum > minimum,
        )
        return StructuredExpressionResolution(contract, ())
    except _Refusal as error:
        return StructuredExpressionResolution(None, (error.reason,))
    except (DefinitionError, TypeError, ValueError, KeyError, AttributeError) as error:
        return StructuredExpressionResolution(None, ("invalid_expression_contract:" + str(error),))
