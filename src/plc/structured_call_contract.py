"""Source-backed ST call contracts kept separate from native instruction admission.

This module only resolves and validates the authored ST signature and its
source-backed applicability facts.  It does not register instructions, change
``call_available``, or claim that a simulator can execute the call.
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
    ValueType,
    select_fact_dependencies,
)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GENERIC_ST_TYPES = frozenset({"ANY_REAL", "ANY_INT", "ANY_NUM", "ANY_BIT", "ANY_SIMPLE", "ANY16", "ANY32"})
_DIRECTIONS = frozenset({"input", "output", "in_out"})
_FX_STRUCTURED_MANUAL = "fxcpu_application_k"
_FX_STRUCTURED_REVISION = "K"
_FX_POSITIONING_MANUAL = "fxcpu_basic_applied_m"
_FX_POSITIONING_MANUAL_NUMBER = "JY997D34701"
_FX_POSITIONING_REVISION = "M"


@dataclass(frozen=True)
class StructuredCallContract:
    """A validated, source-preserving description for a static ST binder.

    ``conditions_evaluated`` is always false here; the eventual binder must
    check every retained applicability condition against the project context.
    """

    opcode: str
    model: str
    name: str
    call_kind: str
    enable: bool
    instance_required: bool
    parameters: tuple[Mapping[str, Any], ...]
    returns: str | None
    return_type_alternatives: tuple[str, ...]
    return_role: str | None
    execution_outputs: tuple[Mapping[str, Any], ...]
    documented_form: str | None
    positional_arguments_verified: bool
    variadic_parameter_name: str | None
    minimum_variadic_arguments: int | None
    maximum_variadic_arguments: int | None
    fact_ids: tuple[str, ...]
    facts: tuple[Mapping[str, Any], ...]
    conditions: tuple[Mapping[str, Any], ...]
    conditions_evaluated: bool
    sources: tuple[Mapping[str, Any], ...]
    return_binding: Mapping[str, Any] | None = None

    def as_mapping(self) -> dict[str, Any]:
        """Return a detached mapping suitable for a later binding layer."""
        return {
            "opcode": self.opcode,
            "model": self.model,
            "name": self.name,
            "call_kind": self.call_kind,
            "enable": self.enable,
            "instance_required": self.instance_required,
            "parameters": copy.deepcopy(list(self.parameters)),
            "returns": self.returns,
            "return_type_alternatives": list(self.return_type_alternatives),
            "return_role": self.return_role,
            "execution_outputs": copy.deepcopy(list(self.execution_outputs)),
            "documented_form": self.documented_form,
            "positional_arguments_verified": self.positional_arguments_verified,
            "variadic_parameter_name": self.variadic_parameter_name,
            "minimum_variadic_arguments": self.minimum_variadic_arguments,
            "maximum_variadic_arguments": self.maximum_variadic_arguments,
            "fact_ids": list(self.fact_ids),
            "facts": copy.deepcopy(list(self.facts)),
            "conditions": copy.deepcopy(list(self.conditions)),
            "conditions_evaluated": self.conditions_evaluated,
            "sources": copy.deepcopy(list(self.sources)),
            "return_binding": copy.deepcopy(self.return_binding),
        }


@dataclass(frozen=True)
class StructuredCallResolution:
    """Either a complete structured-call contract or explicit refusal reasons."""

    contract: StructuredCallContract | None
    refusal_reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.contract is not None and not self.refusal_reasons


class _Refusal(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _is_supported_type(raw: Any) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    token = raw.strip().upper()
    if token in _GENERIC_ST_TYPES:
        return True
    try:
        ValueType.from_iec(token)
    except (DefinitionError, AttributeError):
        return False
    return True


def _has_source_for(group: InstructionFactGroup, *, opcode: str, model: str) -> bool:
    """Require a page-backed source for this exact opcode and target model.

    The FX structured-application facts bind applicability in the reviewed
    fact scope; their page references intentionally do not repeat opcode and
    model fields.  That narrow source shape is accepted only when its exact
    manual/revision/page locator and ST scope are present.
    """
    for source in group.sources:
        pages = source.get("pdf_pages")
        source_opcode = source.get("opcode")
        if (isinstance(source.get("manual_id"), str) and source.get("manual_id")
                and isinstance(source.get("revision"), str) and source.get("revision")
                and isinstance(source_opcode, str) and source_opcode.upper() == opcode.upper()
                and source.get("target_model") == model
                and isinstance(pages, (list, tuple)) and pages
                and all(type(page) is int and page > 0 for page in pages)):
            return True
        if _has_fx_manual_source(source) and _source_signature_matches_scope(
                group, opcode=opcode, model=model):
            if _fx_source_ref_matches(source):
                return True
        if (_has_fx_positioning_manual_source(source)
                and _source_signature_matches_scope(group, opcode=opcode, model=model)
                and _fx_positioning_source_ref_matches(source)):
            return True
    return False


def _has_fx_manual_source(source: Mapping[str, Any]) -> bool:
    pages = source.get("pdf_pages")
    return (source.get("manual_id") == _FX_STRUCTURED_MANUAL
            and source.get("revision") == _FX_STRUCTURED_REVISION
            and isinstance(pages, (list, tuple)) and bool(pages)
            and all(type(page) is int and page > 0 for page in pages))


def _fx_source_ref(source: Mapping[str, Any]) -> str:
    pages = source.get("pdf_pages", ())
    return "%s:%s:pages=%s" % (source.get("manual_id"), source.get("revision"),
                                 ",".join(str(page) for page in pages))


def _fx_source_ref_matches(source: Mapping[str, Any]) -> bool:
    # Authored source rows may still be in _source_reference's raw shape,
    # which has exact manual/revision/pages but no derived source_ref field.
    return ("source_ref" not in source
            or source.get("source_ref") == _fx_source_ref(source))


def _has_fx_positioning_manual_source(source: Mapping[str, Any]) -> bool:
    pages = source.get("pdf_pages")
    return (source.get("manual_id") == _FX_POSITIONING_MANUAL
            and source.get("manual_number") == _FX_POSITIONING_MANUAL_NUMBER
            and source.get("revision") == _FX_POSITIONING_REVISION
            and isinstance(pages, (list, tuple)) and bool(pages)
            and all(type(page) is int and page > 0 for page in pages))


def _fx_positioning_source_ref_matches(source: Mapping[str, Any]) -> bool:
    pages = source.get("pdf_pages", ())
    expected = "%s:%s:pages=%s" % (_FX_POSITIONING_MANUAL, _FX_POSITIONING_REVISION,
                                      ",".join(str(page) for page in pages))
    return "source_ref" not in source or source.get("source_ref") == expected


def _is_fx_structured_signature(group: InstructionFactGroup) -> bool:
    return (group.dimension == "parameters.st_signature"
            and any(_has_fx_manual_source(source) and _fx_source_ref_matches(source)
                    for source in group.sources))


def _is_fx_applied_statement_signature(group: InstructionFactGroup) -> bool:
    return (group.dimension == "parameters.st_signature"
            and group.value.get("call_kind") == "statement"
            and any(_has_fx_positioning_manual_source(source)
                    and _fx_positioning_source_ref_matches(source)
                    for source in group.sources))


def _is_fx_positioning_statement_signature(group: InstructionFactGroup) -> bool:
    return (_is_fx_applied_statement_signature(group)
            and str(group.value.get("call_form", "")).upper()
                in {"DRVI", "DRVA", "DDRVI", "DDRVA"})


def _is_fx_analog_statement_signature(group: InstructionFactGroup) -> bool:
    return (_is_fx_applied_statement_signature(group)
            and str(group.value.get("call_form", "")).upper()
                in {"ANRD", "ANRDP", "ANWR", "ANWRP"})


def _is_source_bound_instruction_signature(group: InstructionFactGroup) -> bool:
    """Route source-declared instruction calls through their strict validator."""
    return (group.dimension == "parameters.st_signature"
            and group.value.get("call_kind") == "instruction")


def _instruction_witness_source_keys(group: InstructionFactGroup, witness: Mapping[str, Any], *,
                                     opcode: str, model: str) -> set[tuple[str, str, int]]:
    """Return exact owner-source identities for a documented call witness page."""
    manual_id, page = witness.get("manual_id"), witness.get("pdf_page")
    if (not isinstance(manual_id, str) or not manual_id.strip()
            or type(page) is not int or page < 1
            or not _source_signature_matches_scope(group, opcode=opcode, model=model)):
        return set()
    keys = set()
    for source in group.sources:
        pages = source.get("pdf_pages")
        revision = source.get("revision")
        source_opcode = source.get("opcode")
        if (source.get("manual_id") != manual_id
                or not isinstance(revision, str) or not revision.strip()
                or not isinstance(pages, (list, tuple))
                or any(type(item) is not int or item < 1 for item in pages)
                or page not in pages
                or not isinstance(source_opcode, str) or source_opcode.upper() != opcode.upper()
                or source.get("target_model") != model):
            continue
        if witness.get("revision") is not None and witness.get("revision") != revision:
            continue
        if witness.get("manual_number") is not None and witness.get("manual_number") != source.get("manual_number"):
            continue
        if witness.get("source_ref") is not None and witness.get("source_ref") != source.get("source_ref"):
            continue
        keys.add((manual_id, revision, page))
    return keys


def _normalise_source_bound_instruction(value: Mapping[str, Any], signature: InstructionFactGroup,
                                        *, opcode: str, model: str
                                        ) -> tuple[dict[str, Any], str | None, bool]:
    """Validate a source-witnessed statement or returning instruction call."""
    if (value.get("source_syntax_conflicts") or value.get("source_variable_conflicts")
            or value.get("unresolved_items")):
        raise _Refusal("instruction_call_source_conflict")
    invocation_style = value.get("invocation_style", "returning")
    if invocation_style not in {"returning", "statement"}:
        raise _Refusal("instruction_call_invocation_style_invalid")
    statement = invocation_style == "statement"
    derived_shape = value.get("derived_call_shape")
    if statement and derived_shape is not None:
        raise _Refusal("instruction_statement_literal_witness_required")
    if derived_shape is not None:
        _validate_derived_instruction_call_shape(value, signature, derived_shape,
                                                 opcode=opcode, model=model)
    else:
        witness = value.get("documented_call_source")
        if not isinstance(witness, Mapping):
            raise _Refusal("instruction_call_witness_source_missing")
        source_keys = _instruction_witness_source_keys(signature, witness, opcode=opcode, model=model)
        if not source_keys:
            raise _Refusal("instruction_call_witness_source_mismatch")
        if len(source_keys) != 1:
            raise _Refusal("instruction_call_witness_source_ambiguous")
    panel_name = value.get("source_panel_name")
    group_forms = value.get("source_group_panel_forms")
    # A literal statement example can use a form whose parameter panel is
    # shared by an explicitly documented group. The cross-check below still
    # binds that panel, its CPU scope and the example to this exact owner.
    grouped_statement_panel = (statement
        and isinstance(group_forms, (list, tuple)) and bool(group_forms)
        and all(isinstance(form, str) and _IDENTIFIER.fullmatch(form) for form in group_forms)
        and isinstance(panel_name, str)
        and {opcode.upper(), panel_name.upper()} <= {form.upper() for form in group_forms})
    if (value.get("available") is not True
            or not isinstance(value.get("name"), str) or value["name"].upper() != opcode.upper()
            or not isinstance(value.get("source_panel_name"), str)
            or not _IDENTIFIER.fullmatch(value["source_panel_name"])
            or (derived_shape is None and value["source_panel_name"].upper() != opcode.upper()
                and not grouped_statement_panel)
            or value.get("enable") is not True
            or value.get("instance_required") is not False):
        raise _Refusal("instruction_call_return_contract_incomplete")
    if statement:
        if value.get("returns") != {"role": "not_assigned_in_documented_statement"}:
            raise _Refusal("statement_return_must_remain_unassigned")
        output = value.get("execution_output")
        if (not isinstance(output, Mapping) or output.get("name") != "ENO"
                or output.get("type") != "BOOL" or output.get("direction") != "output"):
            raise _Refusal("instruction_statement_execution_output_mismatch")
    elif not isinstance(value.get("returns"), str) or value["returns"].upper() != "BOOL":
        raise _Refusal("instruction_call_return_contract_incomplete")
    raw_parameters = value.get("parameters")
    if not isinstance(raw_parameters, (list, tuple)) or len(raw_parameters) < 2:
        raise _Refusal("instruction_call_parameters_incomplete")
    parameters = []
    names = set()
    for index, raw in enumerate(raw_parameters):
        if not isinstance(raw, Mapping):
            raise _Refusal(f"invalid_parameter:{index}")
        name = raw.get("name")
        if (not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)
                or not isinstance(raw.get("type"), str)
                or raw.get("direction") not in _DIRECTIONS
                or not _is_supported_type(raw["type"])):
            raise _Refusal("instruction_call_parameter_contract_mismatch:" + str(name))
        if name.casefold() in names:
            raise _Refusal("instruction_call_parameter_contract_mismatch:" + name)
        names.add(name.casefold())
        if index == 0 and (name.casefold() != "en" or raw.get("direction") != "input"
                           or raw["type"].upper() != "BOOL" or raw.get("role") != "enable"):
            raise _Refusal("instruction_call_enable_parameter_mismatch")
        if (index > 0 and raw.get("passing") is not None
                and raw.get("passing") != "device_reference"):
            raise _Refusal("instruction_call_parameter_passing_mismatch:" + name)
        if not isinstance(raw.get("role"), str) or not raw["role"].strip():
            raise _Refusal("missing_parameter_role:" + name)
        parameters.append({**copy.deepcopy(dict(raw)), "symbol": name,
                           "source_aliases": list(raw.get("source_aliases", ()))})
    if not any(parameter["direction"] == "output" for parameter in parameters):
        raise _Refusal("instruction_call_output_parameter_missing")
    if derived_shape is not None:
        expected_order = [str(parameter["name"]).casefold() for parameter in parameters]
        order = derived_shape.get("argument_order")
        if (not isinstance(order, (list, tuple))
                or any(not isinstance(item, str) for item in order)
                or [item.casefold() for item in order] != expected_order):
            raise _Refusal("derived_instruction_call_order_mismatch")
        if "documented_call" in value or "documented_call_source" in value:
            raise _Refusal("derived_instruction_call_must_not_claim_literal_witness")
        normalized = {**copy.deepcopy(dict(value)), "call_kind": "instruction",
                      "parameters": parameters, "returns": "BOOL"}
        return normalized, None, True

    argument_names = [parameter["name"] for parameter in parameters]
    call = value.get("documented_call")
    if not isinstance(call, str):
        raise _Refusal("instruction_call_witness_missing")
    prefix = r"\s*" if statement else r"\s*ENO\s*:=\s*"
    match = re.fullmatch(
        prefix + r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^()]*)\s*\)\s*;\s*",
        call, re.IGNORECASE)
    if not match or match.group(1).upper() != opcode.upper():
        raise _Refusal("instruction_call_witness_mismatch")
    witnessed_arguments = [item.strip() for item in match.group(2).split(",")]
    if statement:
        bindings = value.get("documented_argument_bindings")
        if (not isinstance(bindings, (list, tuple)) or len(bindings) != len(parameters)
                or any(not isinstance(binding, Mapping)
                       or not isinstance(binding.get("token"), str)
                       or not _IDENTIFIER.fullmatch(binding["token"])
                       or not isinstance(binding.get("parameter"), str)
                       for binding in bindings)
                or [binding["token"].casefold() for binding in bindings]
                    != [argument.casefold() for argument in witnessed_arguments]
                or [binding["parameter"].casefold() for binding in bindings]
                    != [argument.casefold() for argument in argument_names]):
            raise _Refusal("instruction_statement_argument_binding_mismatch")
        # The source example uses concrete devices/labels. Keep its literal
        # spelling while binding positions to the same source's formal table.
        witnessed_arguments = [binding["parameter"] for binding in bindings]
    if ([item.casefold() for item in witnessed_arguments]
            != [item.casefold() for item in argument_names]):
        raise _Refusal("instruction_call_witness_order_mismatch")
    normalized = {**copy.deepcopy(dict(value)),
                  "call_kind": "statement" if statement else "instruction",
                  "parameters": parameters,
                  "returns": copy.deepcopy(value["returns"]) if statement else "BOOL"}
    return normalized, call.strip(), True


def _validate_derived_instruction_call_shape(value: Mapping[str, Any],
                                             signature: InstructionFactGroup,
                                             shape: Any, *, opcode: str, model: str) -> None:
    """Check a derived call form against the same fact's explicit sources and parameters."""
    if not isinstance(shape, Mapping):
        raise _Refusal("invalid_derived_instruction_call_shape")
    shape_name = shape.get("name")
    if (not isinstance(shape_name, str) or shape_name.upper() != opcode.upper()
            or shape.get("call_kind") != "instruction"
            or shape.get("returns") != "BOOL"
            or shape.get("enable") is not True
            or str(shape.get("return_binding", "")).upper() != "ENO"
            or shape.get("derivation") != "grouped_instruction_parameter_table_plus_general_st_call_rule"):
        raise _Refusal("invalid_derived_instruction_call_shape")
    if not _source_signature_matches_scope(signature, opcode=opcode, model=model):
        raise _Refusal("derived_instruction_call_scope_not_explicit")
    if not _has_source_for(signature, opcode=opcode, model=model):
        raise _Refusal("derived_instruction_call_source_missing")

    # This derivation explicitly combines the owner's parameter panel with the
    # ST language rule. Keep every page-backed source attached to the signature;
    # require both source documents and reject any source that claims another
    # owner/model instead of reinterpreting it as the selected instruction.
    source_keys = set()
    for source in signature.sources:
        manual_id, revision, pages = (source.get("manual_id"), source.get("revision"),
                                      source.get("pdf_pages"))
        if (not isinstance(manual_id, str) or not manual_id.strip()
                or not isinstance(revision, str) or not revision.strip()
                or not isinstance(pages, (list, tuple)) or not pages
                or any(type(page) is not int or page < 1 for page in pages)):
            continue
        if (source.get("opcode") is not None and str(source.get("opcode")).upper() != opcode.upper()
                or source.get("target_model") is not None and source.get("target_model") != model):
            raise _Refusal("derived_instruction_call_source_mismatch")
        source_keys.add((manual_id, revision))
    if len(source_keys) < 2:
        raise _Refusal("derived_instruction_call_supporting_sources_incomplete")


def _fx_type_token(raw: Any) -> str:
    """Normalize only the typed source forms emitted by the FX fact producer."""
    if isinstance(raw, str):
        token = raw.strip().upper()
    elif isinstance(raw, Mapping):
        flexible = raw.get("flexible_type")
        if isinstance(flexible, str):
            token = flexible.strip().upper()
        elif raw == {"kind": "bool"}:
            token = "BOOL"
        else:
            scalar_types = {
                ("int", 16, True): "INT", ("int", 16, False): "WORD",
                ("int", 32, True): "DINT", ("int", 32, False): "DWORD",
                ("real", 32, None): "REAL", ("real", 64, None): "LREAL",
            }
            kind = raw.get("kind")
            if kind == "string":
                # The FX author emits this exact mapping for the manual's
                # unbounded Set-data String type.  Storage words and separate
                # string-domain facts are not a STRING[n] declaration bound.
                token = ("STRING" if raw == {"kind": "string", "encoding": "binary"}
                         else "")
            elif kind == "int" and "unit" in raw:
                # TIME shares DINT's finite representation, so never discard
                # a unit while normalizing an integer type.  The exact source
                # shape is the author's TIME token; declarations still have to
                # bind to IEC TIME in st_semantics.
                token = ("TIME" if raw == {"kind": "int", "bits": 32,
                                           "signed": True, "unit": "ms"}
                         else "")
            else:
                signed = raw.get("signed") if kind == "int" else None
                token = scalar_types.get((kind, raw.get("bits"), signed), "")
    else:
        token = ""
    if not _is_supported_type(token):
        raise _Refusal("unsupported_fx_st_type")
    return token


def _fx_type_alternatives(value: Mapping[str, Any], *, label: str) -> tuple[str, ...]:
    raw = value.get("type_alternatives", ())
    if not isinstance(raw, (list, tuple)):
        raise _Refusal("invalid_type_alternatives:" + label)
    result = tuple(_fx_type_token(item) for item in raw)
    if (any(item in _GENERIC_ST_TYPES for item in result)
            or len({item.casefold() for item in result}) != len(result)):
        raise _Refusal("invalid_type_alternatives:" + label)
    return result


def _normalise_fx_instance_signature(signature: InstructionFactGroup, *, opcode: str) -> dict[str, Any]:
    """Bind the K manual's named instance example to its own typed interface."""
    from plc.st_frontend import parse_body

    value = signature.value
    _require_complete_signature_relation(value)
    witness = value.get('source_witness')
    if (value.get('name') != opcode or value.get('instance_required') is not True
            or value.get('arguments_are_named') is not True
            or value.get('call_form_verification') != 'documented_arguments_verified'
            or not isinstance(witness, Mapping)
            or witness.get('instance_type') != opcode):
        raise _Refusal('fx_instance_contract_incomplete')
    pages = witness.get('pdf_pages')
    source_pages = {page for source in signature.sources
                    if _has_fx_manual_source(source) and _fx_source_ref_matches(source)
                    for page in source.get('pdf_pages', ())}
    quote = witness.get('quote')
    call = value.get('documented_call')
    if (witness.get('manual_id') != _FX_STRUCTURED_MANUAL
            or witness.get('revision') != _FX_STRUCTURED_REVISION
            or not isinstance(pages, (list, tuple)) or not pages
            or not set(pages).issubset(source_pages)
            or not isinstance(quote, Mapping) or quote.get('call') != call
            or not isinstance(quote.get('instance_type_label'), str)
            or opcode not in quote['instance_type_label'].split()
            or not isinstance(call, str)):
        raise _Refusal('fx_instance_source_witness_invalid')
    try:
        body = parse_body(call).body
    except ValueError:
        raise _Refusal('fx_instance_documented_call_invalid')
    if (len(body) != 1 or body[0][0] != 'invoke' or body[0][1][0] != 'call'
            or body[0][1][1] != str(witness.get('instance_name', '')).upper()):
        raise _Refusal('fx_instance_documented_call_invalid')
    arguments = body[0][1][2]
    raw_parameters = value.get('parameters')
    if (not isinstance(raw_parameters, (list, tuple)) or not raw_parameters
            or any(argument[0] != 'named' for argument in arguments)):
        raise _Refusal('fx_instance_named_parameters_required')
    parameters = []
    for raw in raw_parameters:
        if not isinstance(raw, Mapping):
            raise _Refusal('fx_instance_parameter_invalid')
        name, direction = raw.get('name'), raw.get('direction')
        if (not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)
                or direction not in _DIRECTIONS):
            raise _Refusal('fx_instance_parameter_invalid')
        parameters.append({**copy.deepcopy(dict(raw)), 'symbol': name,
            'type': _fx_type_token(raw.get('type')),
            'role': 'enable' if name.upper() == 'EN' and direction == 'input' else
                    'output' if direction == 'output' else 'argument'})
    names = [parameter['name'].upper() for parameter in parameters]
    formals = [argument[1] for argument in arguments]
    if (len(set(names)) != len(names) or len(set(formals)) != len(formals)
            or set(names) != set(formals)):
        raise _Refusal('fx_instance_example_interface_mismatch')
    directions = {parameter['name'].upper(): parameter['direction'] for parameter in parameters}
    if any(argument[2] == '=>' and directions[argument[1]] != 'output' for argument in arguments):
        raise _Refusal('fx_instance_example_direction_mismatch')
    returns = value.get('returns')
    if (not isinstance(returns, Mapping) or returns.get('role') != 'no_return_value'
            or returns.get('outputs_via_arguments') is not True or 'type' in returns):
        raise _Refusal('fx_instance_must_not_return_value')
    return {'available': True, 'name': opcode, 'call_kind': 'function_block',
            'instance_required': True, 'enable': value.get('enable'),
            'parameters': parameters, 'returns': None}


def _fx_reference_input_domains(groups, signature, *, opcode, model, version):
    """Resolve ANY only from this signature's own start-data source table."""
    raw_parameters = signature.value.get('parameters', ())
    parameters = [p for p in raw_parameters if isinstance(p, Mapping)
                  and p.get('type') == {'flexible_type': 'ANY'}]
    if not parameters:
        return {}
    result = signature.value.get('returns')
    if (len(parameters) != 1 or not isinstance(result, Mapping)
            or result.get('result_semantics') != 'start_data_reference'
            or result.get('address_source_parameter') != parameters[0].get('name')
            or parameters[0].get('direction') != 'input'):
        raise _Refusal('fx_any_reference_contract_missing')

    def own_fact(dimension, required=True):
        candidates = [g for g in groups if g.dimension == dimension
                      and g.applies_to(opcode=opcode, model=model, language='st', version=version)]
        if not candidates and not required:
            return None
        if len(candidates) != 1:
            raise _Refusal('fx_reference_domain_missing_or_ambiguous:' + dimension)
        group = candidates[0]
        if (group.id not in signature.depends_on
                or group.status != 'source_verified' or group.verification != 'source_checked'
                or not _source_signature_matches_scope(group, opcode=opcode, model=model)
                or not _has_source_for(group, opcode=opcode, model=model)
                or not _shared_manual_source(signature, group)):
            raise _Refusal('fx_reference_domain_source_mismatch:' + dimension)
        return group

    domain = own_fact('constraints.accepted_input_types')
    tokens = domain.value.get('input')
    scalar_tokens = {'INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME'}
    array_tokens = {'INT', 'DINT', 'WORD', 'DWORD', 'REAL', 'TIME'}
    if (not isinstance(tokens, (list, tuple)) or not tokens
            or any(not isinstance(t, str) for t in tokens) or len(set(tokens)) != len(tokens)
            or domain.value.get('literal_or_expression_support') != 'not_stated'
            or domain.value.get('output') != _fx_type_token(result.get('type'))):
        raise _Refusal('fx_reference_input_domain_invalid')
    scalars, arrays = [], []
    for token in tokens:
        if token == 'BIT':
            scalars.append('BOOL')
        elif token in scalar_tokens:
            scalars.append(token)
        elif token.startswith('BITARR_') and token[7:] in array_tokens:
            arrays.append(token[7:])
        elif token != 'BIT_STRING':
            raise _Refusal('fx_reference_input_domain_unknown:' + token)
    # BIT_STRING has no width/array spelling on this page. Keep it visible;
    # it cannot establish a particular WORD/DWORD/array branch by itself.
    binding = {'kind': 'start_data_reference', 'scalar_types': scalars,
               'array_element_types': arrays, 'source_type_domains': list(tokens),
               'literal_or_expression_support': 'not_stated'}
    labels = own_fact('constraints.label_requirement', required=bool(arrays or
                      set(scalars) & {'DINT', 'REAL', 'TIME'}))
    if labels is not None:
        expected = {'applies_to': 'array_and_32bit_data', 'sixteen_bit_device_directly': False,
                    'global_label_required': True, 'direct_32bit_counter': True}
        if dict(labels.value) != expected:
            raise _Refusal('fx_reference_label_requirement_unsupported')
        binding['label_requirement'] = copy.deepcopy(expected)
    return {parameters[0]['name']: binding}


def _normalise_fx_signature(value: Mapping[str, Any], *, opcode: str,
                            reference_domains=None) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Translate the reviewed FX K signature shape into the shared binder form.

    This uses only ``parameters.st_signature`` fields produced by
    ``fact_structured_behavior_review._st_fact``.  It never consults Native
    operands, FBD pins, mnemonics outside the fact scope, or inferred aliases.
    """
    call = value.get("documented_call")
    if not isinstance(call, str):
        raise _Refusal("fx_documented_call_missing")
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)?\s*\)\s*", call)
    if not match or match.group(1).upper() != opcode.upper():
        raise _Refusal("fx_documented_call_mismatch")
    if value.get("call_form_verification") != "documented_arguments_verified":
        raise _Refusal("fx_call_form_not_fully_verified")
    if value.get("arguments_are_named") is not False:
        raise _Refusal("unsupported_fx_named_call_form")
    enable = value.get("enable")
    if type(enable) is not bool:
        raise _Refusal("invalid_enable_contract")
    raw_parameters = value.get("parameters")
    if not isinstance(raw_parameters, (list, tuple)) or not raw_parameters:
        raise _Refusal("st_signature_parameters_missing")
    call_arguments = ([item.strip() for item in match.group(2).split(",")]
                      if match.group(2) else [])
    argument_order = value.get("argument_order")
    if (not isinstance(argument_order, (list, tuple))
            or any(not isinstance(item, str) for item in argument_order)):
        raise _Refusal("fx_argument_order_missing")
    if list(argument_order) != call_arguments:
        raise _Refusal("fx_documented_call_order_mismatch")

    parameters = []
    for index, raw in enumerate(raw_parameters):
        if not isinstance(raw, Mapping):
            raise _Refusal(f"invalid_parameter:{index}")
        name, direction = raw.get("name"), raw.get("direction")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise _Refusal(f"invalid_parameter_name:{index}")
        if not isinstance(direction, str) or direction not in _DIRECTIONS:
            raise _Refusal("invalid_parameter_direction:" + name)
        reference = (reference_domains or {}).get(name)
        data_type = 'ANY' if reference else _fx_type_token(raw.get("type"))
        role = ("enable" if enable and index == 0 and name.casefold() == "en"
                else "result" if direction == "output" else "argument")
        parameter = {**copy.deepcopy(dict(raw)), "symbol": name, "role": role,
                     "type": data_type}
        if reference:
            parameter['input_reference'] = copy.deepcopy(reference)
        alternatives = _fx_type_alternatives(raw, label=name)
        if alternatives:
            parameter["type_alternatives"] = list(alternatives)
        parameters.append(parameter)

    returns_value = value.get("returns")
    if not isinstance(returns_value, Mapping):
        raise _Refusal("invalid_st_return_type")
    return_type = _fx_type_token(returns_value.get("type"))
    return_alternatives = _fx_type_alternatives(returns_value, label="return")
    expected_return_role = "ENO_execution_status" if enable else "function_result"
    if returns_value.get("role") != expected_return_role:
        raise _Refusal("fx_return_role_mismatch")
    if not enable:
        if (not isinstance(returns_value.get("name"), str)
                or not _IDENTIFIER.fullmatch(returns_value["name"])
                or any(parameter["direction"] != "input" for parameter in parameters)):
            raise _Refusal("invalid_fx_plain_function_result")
    else:
        if return_type != "BOOL":
            raise _Refusal("invalid_fx_enable_status_type")
        if (len(parameters) < 2 or parameters[0]["name"].casefold() != "en"
                or parameters[0]["role"] != "enable" or parameters[0]["direction"] != "input"
                or parameters[0]["type"] != "BOOL"
                or sum(parameter["direction"] == "output" for parameter in parameters) != 1):
            raise _Refusal("invalid_fx_enable_parameters")
    if len(parameters) != len(call_arguments):
        raise _Refusal("fx_call_arity_mismatch")
    normalized = {
        "available": True, "call_kind": "function", "enable": enable,
        "instance_required": False, "name": opcode,
        "parameters": parameters, "returns": return_type,
    }
    return normalized, return_alternatives


def _normalise_fx_positioning_statement(value: Mapping[str, Any], *, opcode: str
                                        ) -> tuple[dict[str, Any], Mapping[str, Any]]:
    """Normalize the FX basic/applied M manual's explicit ST statement form.

    Source facts may emit ``documented_form`` or the modern ``call_form`` plus
    ``argument_order`` shape. A separate ``execution_output`` is retained as
    ENO metadata; it is not a call argument or a function result. This path
    deliberately does not use typed-operands facts to fill gaps in the
    documented ST signature.
    """
    if str(value.get("call_form", "")).upper() != opcode.upper():
        raise _Refusal("fx_statement_form_mismatch")
    if value.get("source_syntax_conflicts") or value.get("source_variable_conflicts"):
        raise _Refusal("fx_statement_source_conflict")
    if value.get("unresolved_items"):
        raise _Refusal("unresolved_signature_items")
    enable = value.get("enable")
    if type(enable) is not bool:
        raise _Refusal("invalid_enable_contract")
    raw_parameters = value.get("parameters")
    if not isinstance(raw_parameters, (list, tuple)) or not raw_parameters:
        raise _Refusal("st_signature_parameters_missing")
    documented_form = value.get("documented_form")
    call_arguments = None
    if isinstance(documented_form, str):
        match = re.fullmatch(
            r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)?\s*\)\s*",
            documented_form)
        if not match or match.group(1).upper() != opcode.upper():
            raise _Refusal("fx_documented_statement_mismatch")
        call_arguments = ([item.strip() for item in match.group(2).split(",")]
                          if match.group(2) else [])
    argument_order = value.get("argument_order")
    if call_arguments is None:
        if (not isinstance(argument_order, (list, tuple)) or not argument_order
                or any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                       for item in argument_order)):
            raise _Refusal("fx_statement_argument_order_missing")
        call_arguments = list(argument_order)
    elif argument_order is not None:
        if (not isinstance(argument_order, (list, tuple))
                or [str(item).casefold() for item in argument_order]
                   != [item.casefold() for item in call_arguments]):
            raise _Refusal("fx_documented_statement_order_mismatch")
    if len(call_arguments) != len(raw_parameters):
        raise _Refusal("fx_call_arity_mismatch")

    parameters = []
    for index, raw in enumerate(raw_parameters):
        if not isinstance(raw, Mapping):
            raise _Refusal(f"invalid_parameter:{index}")
        name, direction = raw.get("name"), raw.get("direction")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise _Refusal(f"invalid_parameter_name:{index}")
        if name.casefold() != call_arguments[index].casefold():
            raise _Refusal("fx_documented_statement_order_mismatch")
        if not isinstance(direction, str) or direction not in _DIRECTIONS:
            raise _Refusal("invalid_parameter_direction:" + name)
        data_type = _fx_type_token(raw.get("type"))
        source_role = raw.get("role")
        role = ("enable" if source_role == "execution_condition" else
                "output" if direction == "output" else "argument")
        if source_role == "execution_condition" and direction != "input":
            raise _Refusal("invalid_enable_parameter")
        parameter = {**copy.deepcopy(dict(raw)), "symbol": name, "role": role,
                     "type": data_type, "source_aliases": []}
        parameters.append(parameter)

    if enable:
        enables = [parameter for parameter in parameters if parameter["role"] == "enable"]
        if (len(enables) != 1 or parameters[0] is not enables[0]
                or enables[0]["direction"] != "input"
                or enables[0]["type"].upper() != "BOOL"):
            raise _Refusal("invalid_enable_parameter")
    elif any(parameter["role"] == "enable" for parameter in parameters):
        raise _Refusal("unexpected_enable_parameter")

    returns = value.get("returns")
    if (not isinstance(returns, Mapping)
            or returns.get("role") != "not_assigned_in_documented_statement"
            or "type" in returns or "name" in returns):
        raise _Refusal("fx_statement_return_must_remain_unassigned")
    execution_output = value.get("execution_output")
    if not isinstance(execution_output, Mapping):
        raise _Refusal("fx_statement_execution_output_missing")
    output_name = execution_output.get("name")
    if (not isinstance(output_name, str) or not _IDENTIFIER.fullmatch(output_name)
            or execution_output.get("direction") != "output"
            or _fx_type_token(execution_output.get("type")) != "BOOL"):
        raise _Refusal("invalid_fx_statement_execution_output")
    normalized_output = {**copy.deepcopy(dict(execution_output)),
                         "role": "execution_output", "type": "BOOL"}
    normalized = {
        "available": True,
        "call_kind": "statement",
        "enable": enable,
        "instance_required": False,
        "name": opcode,
        "parameters": parameters,
        "returns": {"role": "not_assigned_in_documented_statement"},
        "execution_output": normalized_output,
        "documented_form": documented_form,
        "argument_order": list(call_arguments),
    }
    return normalized, normalized_output


def _fx_positioning_cpu_availability(all_groups: list[InstructionFactGroup],
                                    signature: InstructionFactGroup, *, opcode: str,
                                    model: str, version: str | None
                                    ) -> list[InstructionFactGroup]:
    """Require the exact M-manual CPU support fact for the documented ST form."""
    signature_sources = [source for source in signature.sources
                         if _has_fx_positioning_manual_source(source)
                         and _fx_positioning_source_ref_matches(source)]
    candidates = []
    for group in all_groups:
        if (group.dimension != "constraints.cpu_availability"
                or not group.applies_to(opcode=opcode, model=model, language="st", version=version)
                or not _source_signature_matches_scope(group, opcode=opcode, model=model)
                or group.status != "source_verified" or group.verification != "source_checked"):
            continue
        cpu_sources = [source for source in group.sources
                       if _has_fx_positioning_manual_source(source)
                       and _fx_positioning_source_ref_matches(source)]
        if not any(left["manual_id"] == right["manual_id"]
                   and left["revision"] == right["revision"]
                   and set(left["pdf_pages"]) & set(right["pdf_pages"])
                   for left in signature_sources for right in cpu_sources):
            continue
        candidates.append(group)
    if len(candidates) != 1:
        raise _Refusal("fx_positioning_cpu_availability_missing_or_ambiguous")
    selected = candidates[0]
    model_states = selected.value.get("model_states")
    if (not isinstance(model_states, Mapping) or model not in model_states
            or model_states.get(model) != "supported"
            or _cpu_availability_claim(selected, model=model) != "supported"):
        raise _Refusal("cpu_model_state_not_supported")
    return _dependency_closure(all_groups, selected.id, opcode=opcode,
                               model=model, version=version)


def _fx_statement_constraint_facts(all_groups: list[InstructionFactGroup],
                                     signature: InstructionFactGroup, *, opcode: str,
                                     model: str, version: str | None, analog: bool = False
                                     ) -> list[InstructionFactGroup]:
    """Keep the source's exact ST range and hardware constraints with its call.

    These facts describe call applicability; they do not certify that the
    current hardware wiring or axis state has been verified.
    """
    expected = {
        "constraints.numeric_ranges": "structured_call_argument_ranges",
        "constraints.documented_requirements": "structured_positioning_output_path",
    }
    prefix = "fx_positioning"
    if analog:
        prefix = "fx_analog"
        expected = {"constraints.documented_requirements": "structured_analog_module_io"}
        if opcode.upper() in {"ANRD", "ANRDP"}:
            expected["constraints.numeric_ranges"] = "structured_call_argument_ranges"
    selected = []
    for dimension, kind in expected.items():
        candidates = [group for group in all_groups
                      if group.dimension == dimension
                      and signature.id in group.depends_on
                      and group.applies_to(opcode=opcode, model=model, language="st", version=version)
                      and _source_signature_matches_scope(group, opcode=opcode, model=model)]
        if len(candidates) != 1:
            raise _Refusal(prefix + "_constraint_missing_or_ambiguous:" + dimension)
        group = candidates[0]
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal(prefix + "_constraint_not_source_verified:" + group.id)
        if not _has_source_for(group, opcode=opcode, model=model):
            raise _Refusal("source_scope_mismatch:" + group.id)
        if analog and not any(
                _has_fx_positioning_manual_source(left) and _fx_positioning_source_ref_matches(left)
                and _has_fx_positioning_manual_source(right) and _fx_positioning_source_ref_matches(right)
                and set(left['pdf_pages']) & set(right['pdf_pages'])
                for left in signature.sources for right in group.sources):
            raise _Refusal("fx_analog_constraint_source_mismatch:" + group.id)
        if (group.value.get("kind") != kind
                or group.value.get("representation") != "st_statement_call"):
            raise _Refusal(prefix + "_constraint_kind_mismatch:" + group.id)
        selected.extend(_dependency_closure(all_groups, group.id, opcode=opcode,
                                            model=model, version=version))
    deduped = {}
    for group in selected:
        deduped[group.id] = group
    return list(deduped.values())


def _fx_cpu_availability(all_groups: list[InstructionFactGroup], signature: InstructionFactGroup,
                         *, opcode: str, model: str, version: str | None
                         ) -> tuple[list[InstructionFactGroup], set[str]]:
    """Bind FX ST use to source-backed CPU support and conditional version guards."""
    def source_matches(source):
        if _is_fx_analog_statement_signature(signature):
            return (_has_fx_positioning_manual_source(source)
                    and _fx_positioning_source_ref_matches(source))
        return _has_fx_manual_source(source) and _fx_source_ref_matches(source)

    signature_sources = [source for source in signature.sources
                         if source_matches(source)]
    candidates = []
    for group in all_groups:
        if (group.dimension != "constraints.cpu_availability"
                or not group.applies_to(opcode=opcode, model=model, language="st", version=version)
                or not _source_signature_matches_scope(group, opcode=opcode, model=model)
                or group.status != "source_verified" or group.verification != "source_checked"):
            continue
        cpu_sources = [source for source in group.sources
                       if source_matches(source)]
        if not any(left["manual_id"] == right["manual_id"]
                   and left["revision"] == right["revision"]
                   and set(left["pdf_pages"]) & set(right["pdf_pages"])
                   for left in signature_sources for right in cpu_sources):
            continue
        if not _has_source_for(group, opcode=opcode, model=model):
            continue
        # A documented firmware retirement is a separate, negative region.
        # Keep it in all_groups so _check_other_cpu_applicability retains its
        # source closure; it must not contradict a provably earlier ST call.
        if (_cpu_availability_claim(group, model=model) == "denied"
                and _version_ranges_disjoint(_conditions_for_group(signature),
                                             _conditions_for_group(group), model=model)):
            continue
        candidates.append(group)
    if not candidates:
        raise _Refusal("fx_cpu_availability_missing_or_ambiguous")

    from plc.instruction_constraints import documented_version_parts, version_condition_bounds

    def conditional_guard(candidate, closure):
        """Accept conditional only when its own FX source binds the firmware guard."""
        value = candidate.value
        if "available" in value:
            raise _Refusal("fx_conditional_cpu_availability_must_not_be_unconditional")
        if any(isinstance(condition, Mapping)
               and condition.get("context") == "cpu_version_not_resolved"
               for condition in _conditions_for_group(signature)):
            raise _Refusal("fx_conditional_cpu_version_guard_unresolved")

        direct_version_ids = set(candidate.depends_on)
        version_facts = [group for group in closure
                         if group.id in direct_version_ids
                         and group.dimension == "constraints.cpu_version"]
        if len(version_facts) != 1:
            raise _Refusal("fx_conditional_cpu_version_fact_missing_or_ambiguous")
        version_fact = version_facts[0]
        if not _source_signature_matches_scope(version_fact, opcode=opcode, model=model):
            raise _Refusal("fx_conditional_cpu_version_scope_mismatch")
        source_keys = [key for key in ("minimum_series_version", "maximum_series_version_exclusive")
                       if key in version_fact.value]
        if len(source_keys) != 1:
            raise _Refusal("fx_conditional_cpu_version_source_incomplete")
        source_key = source_keys[0]
        source_value = version_fact.value[source_key]
        match = (re.fullmatch(r"(?:Ver\.?\s*)?(\d+(?:\.\d+)*)", source_value,
                              flags=re.IGNORECASE)
                 if isinstance(source_value, str) else None)
        if match is None:
            raise _Refusal("fx_conditional_cpu_version_source_incomplete")
        try:
            source_bound = documented_version_parts(match.group(1))
        except (DefinitionError, TypeError, ValueError):
            raise _Refusal("fx_conditional_cpu_version_source_incomplete")
        scoped_models = version_fact.value.get("models")
        if (not isinstance(scoped_models, (list, tuple)) or model not in scoped_models
                or not _has_source_for(version_fact, opcode=opcode, model=model)):
            raise _Refusal("fx_conditional_cpu_version_source_mismatch")
        version_sources = [source for source in version_fact.sources
                           if source_matches(source)]
        if not any(left["manual_id"] == right["manual_id"]
                   and left["revision"] == right["revision"]
                   and set(left["pdf_pages"]) & set(right["pdf_pages"])
                   for left in cpu_sources_for(candidate)
                   for right in version_sources):
            raise _Refusal("fx_conditional_cpu_version_source_mismatch")

        conditions = _conditions_for_group(candidate)
        if not conditions:
            raise _Refusal("fx_conditional_cpu_version_guard_missing")
        for condition in conditions:
            try:
                bounds = version_condition_bounds(condition, model=model)
            except (DefinitionError, TypeError, ValueError, KeyError):
                raise _Refusal("fx_conditional_cpu_version_guard_unresolved")
            if bounds is None:
                raise _Refusal("fx_conditional_cpu_version_guard_unresolved")
            path, minimum, maximum, minimum_exclusive = bounds
            expected = ((source_bound, None, False) if source_key == "minimum_series_version"
                        else (None, source_bound, False))
            if path != "cpu_version" or (minimum, maximum, minimum_exclusive) != expected:
                raise _Refusal("fx_conditional_cpu_version_guard_mismatch")

    def cpu_sources_for(candidate):
        return [source for source in candidate.sources
                if source_matches(source)]

    candidate_claims = []
    for candidate in candidates:
        model_states = candidate.value.get("model_states")
        state = model_states.get(model) if isinstance(model_states, Mapping) else None
        claim = _cpu_availability_claim(candidate, model=model)
        candidate_claims.append((state, claim))
    if len(set(candidate_claims)) > 1:
        raise _Refusal("fx_cpu_availability_conflicting_claims")
    if any(claim not in {"supported", "conditional"}
           for _, claim in candidate_claims):
        raise _Refusal("cpu_model_state_not_supported")

    def refined_conditional_header(candidate):
        # The catalogue header records the conditional cell before the author
        # binds its footnote. It is not a second, guard-free support contract.
        # Keep the raw fact in the owner, and use the signature's explicit
        # refinement only when it cites the very same manual page and claim.
        if (not candidate.id.startswith("source:cpu_availability:")
                or candidate.depends_on or _conditions_for_group(candidate)
                or "available" in candidate.value):
            return False
        for refined in candidates:
            if (refined.id not in signature.depends_on
                    or not _conditions_for_group(refined)
                    or _cpu_availability_claim(refined, model=model) != "conditional"):
                continue
            if any(left["manual_id"] == right["manual_id"]
                   and left["revision"] == right["revision"]
                   and set(left["pdf_pages"]) == set(right["pdf_pages"])
                   for left in cpu_sources_for(candidate)
                   for right in cpu_sources_for(refined)):
                return True
        return False

    closures = []
    refined_header_ids = set()
    for candidate, (state, claim) in zip(candidates, candidate_claims):
        if claim == "conditional" and refined_conditional_header(candidate):
            refined_header_ids.add(candidate.id)
            continue
        closure = _dependency_closure(all_groups, candidate.id, opcode=opcode,
                                      model=model, version=version)
        if claim == "conditional" and state == "conditional":
            conditional_guard(candidate, closure)
        elif claim != "supported" or state != "supported":
            raise _Refusal("cpu_model_state_not_supported")
        closures.append(closure)

    condition_signatures = [
        _fx_applicability_signature(closure, opcode=opcode, model=model)
        for closure in closures
    ]
    if any(item != condition_signatures[0] for item in condition_signatures[1:]):
        raise _Refusal("fx_cpu_availability_conditions_differ")

    # Preserve each complete support witness and its dependency closure.
    # Raw conditional headers remain available in the owner's source facts.
    selected_by_id = {}
    for closure in closures:
        for group in closure:
            selected_by_id[group.id] = group
    return list(selected_by_id.values()), refined_header_ids


def _fx_applicability_signature(groups: list[InstructionFactGroup], *,
                                opcode: str, model: str) -> tuple[str, ...]:
    """Canonicalize each selected group's exact applicability for this ST owner."""
    signatures = set()
    for group in groups:
        scope = copy.deepcopy(dict(group.scope or {}))
        for key, selected in (("forms", opcode), ("models", model), ("languages", "st")):
            values = scope.get(key)
            if not isinstance(values, (list, tuple)) or selected not in values:
                raise _Refusal("fx_cpu_availability_scope_mismatch")
            # These are selector sets; normalize them to the exact resolver
            # context so ST and ST+FBD witnesses can prove the same ST call.
            scope[key] = [selected]
        for key, values in tuple(scope.items()):
            if key not in {"forms", "models", "languages"} and isinstance(values, (list, tuple)):
                scope[key] = sorted(set(values), key=str)
        try:
            condition_rows = sorted(json.dumps(condition, ensure_ascii=True, sort_keys=True,
                                               separators=(",", ":"))
                                    for condition in _conditions_for_group(group))
            payload = {"scope": scope, "conditions": condition_rows}
            signatures.add(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":")))
        except (TypeError, ValueError):
            raise _Refusal("fx_cpu_availability_conditions_uncomparable")
    return tuple(sorted(signatures))


def _source_signature_matches_scope(group: InstructionFactGroup, *, opcode: str, model: str) -> bool:
    scope = group.scope or {}
    return (opcode in scope.get("forms", ())
            and model in scope.get("models", ())
            and "st" in scope.get("languages", ()))


def _dependency_closure(all_groups: list[InstructionFactGroup], root_id: str, *,
                        opcode: str, model: str, version: str | None) -> list[InstructionFactGroup]:
    closure = select_fact_dependencies(all_groups, [root_id], opcode=opcode, model=model,
                                       language="st", version=version)
    if closure["gaps"]:
        reasons = ",".join(sorted({str(gap.get("reason")) for gap in closure["gaps"]}))
        raise _Refusal("dependency_gaps:" + reasons)
    selected = [InstructionFactGroup.from_mapping(item) for item in closure["groups"]]
    for group in selected:
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal("unverified_fact:" + group.id)
        if not _has_source_for(group, opcode=opcode, model=model):
            raise _Refusal("source_scope_mismatch:" + group.id)
    return selected


def _require_complete_signature_relation(value: Mapping[str, Any]) -> None:
    if value.get("full_source_relation_complete") is False:
        raise _Refusal("incomplete_source_relation")
    if value.get("technical_relation_resolved") is False:
        raise _Refusal("technical_relation_unresolved")
    if value.get("unresolved_items"):
        raise _Refusal("unresolved_signature_items")
    if value.get("unbound_condition_markers"):
        raise _Refusal("unbound_signature_conditions")


def _check_parameter(raw: Any, index: int, seen_names: set[str], seen_symbols: set[str], *,
                     allow_repeated_name: bool = False,
                     allow_reference_any: bool = False) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise _Refusal(f"invalid_parameter:{index}")
    name, symbol = raw.get("name"), raw.get("symbol")
    role, direction, data_type = raw.get("role"), raw.get("direction"), raw.get("type")
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise _Refusal(f"invalid_parameter_name:{index}")
    if not isinstance(symbol, str) or not _IDENTIFIER.fullmatch(symbol):
        raise _Refusal(f"missing_parameter_symbol:{name}")
    if not isinstance(role, str) or not role.strip():
        raise _Refusal(f"missing_parameter_role:{name}")
    if not isinstance(direction, str) or direction not in _DIRECTIONS:
        raise _Refusal(f"invalid_parameter_direction:{name}")
    if 'input_reference' in raw and not (allow_reference_any and data_type == 'ANY'):
        raise _Refusal(f"unsupported_parameter_reference:{name}")
    if not _is_supported_type(data_type) and not (allow_reference_any and data_type == 'ANY'
            and isinstance(raw.get('input_reference'), Mapping)
            and raw['input_reference'].get('kind') == 'start_data_reference'):
        raise _Refusal(f"unsupported_parameter_type:{name}")
    branches = raw.get("source_verified_type_branches")
    if branches is not None or "other_type_branches" in raw:
        if (not isinstance(branches, (list, tuple)) or not branches
                or raw.get("other_type_branches") != "unresolved"
                or any(not _is_supported_type(item)
                       or item.upper() in _GENERIC_ST_TYPES for item in branches)):
            raise _Refusal(f"invalid_verified_type_branches:{name}")
    aliases = raw.get("source_aliases", [])
    if (not isinstance(aliases, (list, tuple))
            or any(not isinstance(alias, str) or not _IDENTIFIER.fullmatch(alias) for alias in aliases)):
        raise _Refusal(f"invalid_parameter_aliases:{name}")
    name_key, symbol_key = name.casefold(), symbol.casefold()
    if ((name_key in seen_names or symbol_key in seen_symbols)
            and not allow_repeated_name):
        raise _Refusal("ambiguous_parameter_binding:" + name)
    seen_names.add(name_key)
    seen_symbols.add(symbol_key)
    return copy.deepcopy(dict(raw))


def _fx_variadic_input_contract(all_groups: list[InstructionFactGroup],
                                signature: InstructionFactGroup,
                                parameters: tuple[Mapping[str, Any], ...], *,
                                opcode: str, model: str, version: str | None
                                ) -> tuple[str, int, int, list[InstructionFactGroup]] | None:
    """Validate a repeated source input name against its authored pin-count fact.

    A repeated ST formal is accepted only when the exact owner has one
    source-verified variable-pin fact cited from the same manual/revision/pages.
    The signature's repeated placeholders establish the documented minimum;
    the pin fact supplies the explicit maximum.  This keeps ordinary duplicate
    formals ambiguous and does not infer pin names from Native/FBD operands.
    """
    positions_by_name: dict[str, list[int]] = {}
    for index, parameter in enumerate(parameters):
        positions_by_name.setdefault(str(parameter["name"]).casefold(), []).append(index)
    repeated = [(name, positions) for name, positions in positions_by_name.items()
                if len(positions) > 1]
    if not repeated:
        return None
    if len(repeated) != 1:
        raise _Refusal("ambiguous_variadic_parameter_names")
    name, positions = repeated[0]
    start, end = positions[0], positions[-1]
    if positions != list(range(start, end + 1)):
        raise _Refusal("noncontiguous_variadic_parameter")
    templates = [parameters[index] for index in positions]
    if (any(parameter.get("direction") != "input"
            or str(parameter.get("role", "argument")).casefold() != "argument"
            for parameter in templates)
            or any(dict(parameter) != dict(templates[0]) for parameter in templates[1:])):
        raise _Refusal("inconsistent_variadic_parameter_template")

    candidates = [group for group in all_groups
                  if group.dimension == "execution.operand_pins"
                  and group.applies_to(opcode=opcode, model=model, language="st", version=version)]
    if not candidates:
        raise _Refusal("variadic_operand_pin_fact_missing")
    if len(candidates) != 1:
        raise _Refusal("ambiguous_variadic_operand_pin_fact")
    pin_fact = candidates[0]
    if (pin_fact.status != "source_verified" or pin_fact.verification != "source_checked"):
        raise _Refusal("variadic_operand_pin_fact_not_source_verified")
    if (not _source_signature_matches_scope(pin_fact, opcode=opcode, model=model)
            or not _has_source_for(pin_fact, opcode=opcode, model=model)
            or not _shared_manual_source(signature, pin_fact)):
        raise _Refusal("variadic_operand_pin_source_mismatch")
    value = pin_fact.value
    pin_count = value.get("operand_pins")
    minimum = pin_count.get("minimum") if isinstance(pin_count, Mapping) else None
    maximum = pin_count.get("maximum") if isinstance(pin_count, Mapping) else None
    if (value.get("variable_arity") is not True
            or not isinstance(value.get("edit_basis"), str) or not value["edit_basis"].strip()
            or type(minimum) is not int or type(maximum) is not int
            or minimum != len(positions) or maximum < minimum):
        raise _Refusal("invalid_variadic_operand_pin_range")
    closure = _dependency_closure(all_groups, pin_fact.id, opcode=opcode,
                                  model=model, version=version)
    return str(parameters[start]["name"]), minimum, maximum, closure


def _source_optional_trailing_contract(all_groups: list[InstructionFactGroup],
                                       signature: InstructionFactGroup,
                                       parameters: tuple[Mapping[str, Any], ...], *,
                                       opcode: str, model: str, version: str | None
                                       ) -> tuple[str, int, int, list[InstructionFactGroup]] | None:
    """Validate source-marked optional trailing formals as a bounded ST input range.

    This is for signatures that name each optional input separately.  A range is
    enabled only when source-verified ST arity and order facts agree with the
    exact signature and its CPU dependency; optional flags alone are not enough.
    """
    if not any("optional" in parameter for parameter in parameters):
        return None
    if any(type(parameter.get("optional")) is not bool
           for parameter in parameters if "optional" in parameter):
        raise _Refusal("invalid_optional_parameter_marker")
    optional_positions = [index for index, parameter in enumerate(parameters)
                          if parameter.get("optional") is True]
    if not optional_positions:
        return None
    if (signature.value.get("call_kind") != "function"
            or optional_positions != list(range(optional_positions[0], optional_positions[-1] + 1))):
        raise _Refusal("invalid_optional_trailing_signature")
    optional_start, optional_end = optional_positions[0], optional_positions[-1] + 1
    optional_parameters = list(parameters[optional_start:optional_end])
    if any(parameter.get("direction") != "input" for parameter in optional_parameters):
        raise _Refusal("optional_parameters_must_be_inputs")

    def template_key(parameter: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(parameter.get(key) for key in (
            "type", "direction", "role", "passing", "addressing", "type_alternatives",
            "declared_capacity_choices"))

    optional_key = template_key(optional_parameters[0])
    if any(template_key(parameter) != optional_key for parameter in optional_parameters[1:]):
        raise _Refusal("inconsistent_optional_input_templates")
    if any(parameter.get("name") is None or parameter.get("symbol") is None
           for parameter in optional_parameters):
        raise _Refusal("optional_input_identity_missing")

    variable_start = optional_start
    while variable_start > 0:
        candidate = parameters[variable_start - 1]
        if (candidate.get("optional") is True or candidate.get("direction") != "input"
                or template_key(candidate) != optional_key):
            break
        variable_start -= 1
    if variable_start == optional_start:
        raise _Refusal("optional_inputs_have_no_required_source_prefix")
    variable_parameters = list(parameters[variable_start:optional_end])
    if any(parameter.get("direction") != "input" for parameter in variable_parameters):
        raise _Refusal("optional_input_group_contains_non_input")
    suffix = parameters[optional_end:]
    if any(parameter.get("direction") != "output" for parameter in suffix):
        raise _Refusal("optional_inputs_are_not_trailing_before_outputs")
    minimum = optional_start - variable_start
    maximum = optional_end - variable_start
    if (minimum < 1 or maximum <= minimum
            or any(parameter.get("optional") is True for parameter in variable_parameters[:minimum])
            or any(parameter.get("optional") is not True for parameter in variable_parameters[minimum:])):
        raise _Refusal("invalid_optional_input_range")

    arities = [group for group in all_groups
               if group.dimension == "parameters.arity"
               and group.applies_to(opcode=opcode, model=model, language="st", version=version)
               and group.value.get("count_kind") == "ST_call_arguments"]
    orders = [group for group in all_groups
              if group.dimension == "parameters.order"
              and group.applies_to(opcode=opcode, model=model, language="st", version=version)
              and group.value.get("order_source") == "ST_call_arguments"]
    if len(arities) != 1 or len(orders) != 1:
        raise _Refusal("optional_input_st_arity_or_order_missing_or_ambiguous")
    signature_cpu_closure = _dependency_closure(all_groups, signature.id, opcode=opcode,
                                                model=model, version=version)
    signature_cpu_ids = {group.id for group in signature_cpu_closure
                         if group.dimension == "constraints.cpu_availability"}
    if not signature_cpu_ids:
        raise _Refusal("optional_input_cpu_dependency_missing")
    selected = []
    for fact, dimension in ((arities[0], "arity"), (orders[0], "order")):
        if (fact.status != "source_verified" or fact.verification != "source_checked"
                or not _source_signature_matches_scope(fact, opcode=opcode, model=model)
                or not _has_source_for(fact, opcode=opcode, model=model)
                or not _shared_manual_source(signature, fact)):
            raise _Refusal("optional_input_st_" + dimension + "_source_mismatch")
        closure = _dependency_closure(all_groups, fact.id, opcode=opcode,
                                      model=model, version=version)
        closure_cpu_ids = {group.id for group in closure
                           if group.dimension == "constraints.cpu_availability"}
        if closure_cpu_ids != signature_cpu_ids:
            raise _Refusal("optional_input_st_" + dimension + "_cpu_dependency_mismatch")
        if dimension == "arity":
            fact_minimum, fact_maximum = fact.value.get("minimum"), fact.value.get("maximum")
            fixed_count = len(parameters) - maximum
            if (type(fact_minimum) is not int or type(fact_maximum) is not int
                    or fact_minimum != fixed_count + minimum
                    or fact_maximum != fixed_count + maximum):
                raise _Refusal("optional_input_st_arity_mismatch")
        else:
            symbols = fact.value.get("symbols")
            expected_symbols = [str(parameter["symbol"]).casefold() for parameter in parameters]
            if (not isinstance(symbols, (list, tuple))
                    or [str(symbol).casefold() for symbol in symbols] != expected_symbols):
                raise _Refusal("optional_input_st_order_mismatch")
        selected.extend(closure)
    return str(parameters[variable_start]["name"]), minimum, maximum, selected


def _cross_check_st_call_arguments(all_groups: list[InstructionFactGroup], signature: InstructionFactGroup,
                                   parameters: tuple[Mapping[str, Any], ...], *, opcode: str,
                                   model: str, version: str | None) -> list[InstructionFactGroup]:
    """Use explicit ST-call arity/order facts only to reject omissions or ambiguity.

    These facts never supply parameters.  In particular, Native/FBD operands and
    ``typed_operands`` are not consulted to construct an ST signature.
    """
    result: list[InstructionFactGroup] = []
    checked: set[str] = set()
    for dimension, marker, expected_marker in (
        ("parameters.arity", "count_kind", "ST_call_arguments"),
        ("parameters.order", "order_source", "ST_call_arguments"),
    ):
        candidates = [group for group in all_groups
                      if group.dimension == dimension
                      and group.applies_to(opcode=opcode, model=model, language="st", version=version)
                      and group.value.get(marker) == expected_marker]
        if len(candidates) > 1:
            raise _Refusal("ambiguous_" + dimension.replace(".", "_"))
        if not candidates:
            continue
        group = candidates[0]
        checked.add(dimension)
        if not _source_signature_matches_scope(group, opcode=opcode, model=model):
            raise _Refusal("st_call_cross_check_scope_not_explicit:" + group.id)
        if not _has_source_for(group, opcode=opcode, model=model):
            raise _Refusal("source_scope_mismatch:" + group.id)
        if not _shared_manual_source(signature, group):
            raise _Refusal("signature_source_mismatch:" + group.id)
        result.extend(_dependency_closure(all_groups, group.id, opcode=opcode,
                                          model=model, version=version))
        if dimension == "parameters.arity":
            minimum, maximum = group.value.get("minimum"), group.value.get("maximum")
            if type(minimum) is not int or type(maximum) is not int or minimum != maximum:
                raise _Refusal("non_exact_st_call_arity")
            if minimum != len(parameters):
                raise _Refusal("st_call_arity_mismatch")
        else:
            symbols = group.value.get("symbols")
            if (not isinstance(symbols, (list, tuple))
                    or any(not isinstance(symbol, str) for symbol in symbols)):
                raise _Refusal("invalid_st_call_order")
            actual = [str(parameter["symbol"]).casefold() for parameter in parameters]
            if [symbol.casefold() for symbol in symbols] != actual:
                raise _Refusal("st_call_parameter_order_mismatch")
    required_checks = {"parameters.arity", "parameters.order"}
    missing = required_checks - checked
    if missing:
        raise _Refusal("st_call_cross_check_missing:" + ",".join(sorted(missing)))
    return result


def _source_bound_instruction_cross_checks(all_groups: list[InstructionFactGroup],
                                           signature: InstructionFactGroup,
                                           parameters: tuple[Mapping[str, Any], ...], *,
                                           opcode: str, model: str, version: str | None
                                           ) -> list[InstructionFactGroup]:
    """Require same-witness-source arity/order rows without using them as formals."""
    witness = signature.value.get("documented_call_source")
    if not isinstance(witness, Mapping):
        raise _Refusal("instruction_call_witness_source_missing")
    signature_source_keys = _instruction_witness_source_keys(
        signature, witness, opcode=opcode, model=model)
    if len(signature_source_keys) != 1:
        raise _Refusal("instruction_call_witness_source_ambiguous")
    statement = signature.value.get("invocation_style") == "statement"
    cpu_witness = witness
    cpu_source_keys = signature_source_keys
    if statement:
        # A concrete program example may follow its parameter/CPU panel on
        # another page. Require both exact locators on this same owner fact;
        # page proximity alone never establishes this relationship.
        cpu_witness = signature.value.get("documented_parameter_panel_source")
        if not isinstance(cpu_witness, Mapping):
            raise _Refusal("instruction_statement_parameter_panel_missing")
        cpu_source_keys = _instruction_witness_source_keys(
            signature, cpu_witness, opcode=opcode, model=model)
        if (len(cpu_source_keys) != 1
                or {key[:2] for key in cpu_source_keys}
                    != {key[:2] for key in signature_source_keys}):
            raise _Refusal("instruction_statement_parameter_panel_mismatch")
    signature_closure = _dependency_closure(all_groups, signature.id, opcode=opcode,
                                            model=model, version=version)
    signature_cpu_groups = [group for group in signature_closure
                            if group.dimension == "constraints.cpu_availability"]
    if (not signature_cpu_groups
            or any(_instruction_witness_source_keys(group, cpu_witness, opcode=opcode, model=model)
                   != cpu_source_keys for group in signature_cpu_groups)):
        raise _Refusal("instruction_call_cpu_source_mismatch")
    signature_cpu_ids = {group.id for group in signature_cpu_groups}
    result: list[InstructionFactGroup] = []
    order_source = "documented_example_binding" if statement else "literal_call_panel"
    for dimension, marker, expected_marker in (
        ("parameters.arity", "count_kind", "call_arguments"),
        ("parameters.order", "order_source", order_source),
    ):
        candidates = [group for group in all_groups
                      if group.dimension == dimension
                      and group.applies_to(opcode=opcode, model=model, language="st", version=version)
                      and group.value.get(marker) == expected_marker
                      and _instruction_witness_source_keys(
                          group, witness, opcode=opcode, model=model) == signature_source_keys]
        if len(candidates) != 1:
            raise _Refusal("instruction_call_" + dimension.replace(".", "_")
                           + ("_missing" if not candidates else "_ambiguous"))
        group = candidates[0]
        if statement and _instruction_witness_source_keys(
                group, cpu_witness, opcode=opcode, model=model) != cpu_source_keys:
            raise _Refusal("instruction_statement_parameter_panel_mismatch:" + group.id)
        if (group.status != "source_verified" or group.verification != "source_checked"
                or not _source_signature_matches_scope(group, opcode=opcode, model=model)
                or not _has_source_for(group, opcode=opcode, model=model)):
            raise _Refusal("instruction_call_source_mismatch:" + group.id)
        closure = _dependency_closure(all_groups, group.id, opcode=opcode,
                                      model=model, version=version)
        check_cpu_ids = {fact.id for fact in closure
                          if fact.dimension == "constraints.cpu_availability"}
        if check_cpu_ids != signature_cpu_ids:
            raise _Refusal("instruction_call_cpu_dependency_mismatch:" + group.id)
        if dimension == "parameters.arity":
            minimum, maximum = group.value.get("minimum"), group.value.get("maximum")
            if (minimum != len(parameters) or maximum != len(parameters)
                    or type(minimum) is not int or type(maximum) is not int):
                raise _Refusal("instruction_call_arity_mismatch")
        else:
            symbols = group.value.get("symbols")
            actual = [str(parameter["symbol"]).casefold() for parameter in parameters]
            if (not isinstance(symbols, (list, tuple))
                    or [str(symbol).casefold() for symbol in symbols] != actual):
                raise _Refusal("instruction_call_order_mismatch")
        result.extend(closure)
    return result


def _derived_source_bound_instruction_cross_checks(all_groups: list[InstructionFactGroup],
                                                  signature: InstructionFactGroup,
                                                  parameters: tuple[Mapping[str, Any], ...], *,
                                                  opcode: str, model: str, version: str | None
                                                  ) -> list[InstructionFactGroup]:
    """Retain exact owner/CPU closure for a source-declared derived call shape."""
    shape = signature.value.get("derived_call_shape")
    if not isinstance(shape, Mapping):
        raise _Refusal("derived_instruction_call_shape_missing")
    expected_symbols = [str(parameter["symbol"]).casefold() for parameter in parameters]
    order = shape.get("argument_order")
    if (not isinstance(order, (list, tuple))
            or [str(symbol).casefold() for symbol in order] != expected_symbols):
        raise _Refusal("derived_instruction_call_order_mismatch")

    signature_closure = _dependency_closure(all_groups, signature.id, opcode=opcode,
                                            model=model, version=version)
    signature_cpu_ids = {group.id for group in signature_closure
                         if group.dimension == "constraints.cpu_availability"}
    if not signature_cpu_ids:
        raise _Refusal("derived_instruction_call_cpu_dependency_missing")
    result = list(signature_closure)

    signature_closure_ids = {group.id for group in signature_closure}
    # The derived call-shape marker is grounded by the owner's separate ST
    # arity/order facts. Both are dependencies of the signature, not a literal
    # call sample, and their exact values must agree with its formal list.
    checks = (
        ("parameters.arity", "count_kind", {"call_arguments", "ST_call_arguments"}),
        ("parameters.order", "order_source", {
            "grouped_instruction_parameter_table_plus_general_st_call_rule",
        }),
    )
    for dimension, marker, accepted_markers in checks:
        candidates = [group for group in all_groups
                     if group.dimension == dimension
                     and group.applies_to(opcode=opcode, model=model, language="st", version=version)
                     and group.value.get(marker) in accepted_markers]
        if not candidates:
            raise _Refusal("derived_instruction_" + dimension.replace(".", "_") + "_missing")
        if len(candidates) != 1:
            raise _Refusal("ambiguous_derived_instruction_" + dimension.replace(".", "_"))
        group = candidates[0]
        if group.id not in signature_closure_ids:
            raise _Refusal("derived_instruction_" + dimension.replace(".", "_") + "_dependency_missing")
        if (group.status != "source_verified" or group.verification != "source_checked"
                or not _source_signature_matches_scope(group, opcode=opcode, model=model)
                or not _has_source_for(group, opcode=opcode, model=model)
                or not _shares_a_page_source(signature, group)):
            raise _Refusal("derived_instruction_source_mismatch:" + group.id)
        closure = _dependency_closure(all_groups, group.id, opcode=opcode,
                                      model=model, version=version)
        closure_cpu_ids = {fact.id for fact in closure
                           if fact.dimension == "constraints.cpu_availability"}
        if closure_cpu_ids != signature_cpu_ids:
            raise _Refusal("derived_instruction_cpu_dependency_mismatch:" + group.id)
        if dimension == "parameters.arity":
            minimum, maximum = group.value.get("minimum"), group.value.get("maximum")
            if (type(minimum) is not int or type(maximum) is not int
                    or minimum != maximum or minimum != len(parameters)):
                raise _Refusal("derived_instruction_arity_mismatch")
        else:
            symbols = group.value.get("symbols")
            if (not isinstance(symbols, (list, tuple))
                    or [str(symbol).casefold() for symbol in symbols] != expected_symbols):
                raise _Refusal("derived_instruction_order_mismatch")
        result.extend(closure)
    return result


def _shares_a_page_source(left: InstructionFactGroup, right: InstructionFactGroup) -> bool:
    left_pages = {(source.get("manual_id"), source.get("revision"), page)
                  for source in left.sources
                  for page in source.get("pdf_pages", ())
                  if isinstance(source.get("manual_id"), str)
                  and isinstance(source.get("revision"), str)
                  and type(page) is int}
    right_pages = {(source.get("manual_id"), source.get("revision"), page)
                   for source in right.sources
                   for page in source.get("pdf_pages", ())
                   if isinstance(source.get("manual_id"), str)
                   and isinstance(source.get("revision"), str)
                   and type(page) is int}
    return bool(left_pages & right_pages)


def _shared_manual_source(left: InstructionFactGroup, right: InstructionFactGroup) -> bool:
    def keys(group):
        result = set()
        for source in group.sources:
            pages = source.get("pdf_pages")
            if (isinstance(source.get("manual_id"), str) and source.get("manual_id")
                    and isinstance(source.get("revision"), str) and source.get("revision")
                    and isinstance(pages, (list, tuple))):
                result.add((source["manual_id"], source["revision"], tuple(pages)))
        return result
    left_keys, right_keys = keys(left), keys(right)
    return bool(left_keys & right_keys)


def _conditions_for_group(group: InstructionFactGroup) -> list[Any]:
    conditions = copy.deepcopy(list(group.conditions))
    for source in group.sources:
        for key in ("conditions", "applicability"):
            value = source.get(key)
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                conditions.extend(copy.deepcopy(list(value)))
            else:
                conditions.append(copy.deepcopy(value))
    return conditions


def _merged_context(groups: list[InstructionFactGroup], *, model: str) -> Mapping[str, Any]:
    from plc.instruction_relations import fact_context_intersection

    context: Mapping[str, Any] = {"scope": {}, "conditions": []}
    for group in groups:
        try:
            intersection = fact_context_intersection(context, {
                "scope": copy.deepcopy(dict(group.scope or {})),
                "conditions": _conditions_for_group(group),
            })
        except (DefinitionError, TypeError, ValueError, KeyError):
            raise _Refusal("cpu_applicability_context_ambiguous")
        if intersection is None:
            raise _Refusal("inconsistent_contract_conditions")
        context = {"scope": intersection[0], "conditions": intersection[1]}
    if _version_ranges_disjoint((), context["conditions"], model=model):
        raise _Refusal("inconsistent_contract_conditions")
    return context


def _version_ranges_disjoint(left_conditions, right_conditions, *, model: str) -> bool:
    """Return true only when known version guards prove no shared version."""
    from plc.instruction_constraints import documented_version_parts, version_condition_bounds

    intervals: dict[str, dict[str, Any]] = {}

    def tighten(path: str, *, minimum=None, minimum_inclusive=True, maximum=None, maximum_inclusive=False):
        bounds = intervals.setdefault(path, {"minimum": None, "minimum_inclusive": True,
                                               "maximum": None, "maximum_inclusive": True})
        if minimum is not None:
            if (bounds["minimum"] is None or minimum > bounds["minimum"]
                    or minimum == bounds["minimum"] and not minimum_inclusive):
                bounds["minimum"] = minimum
                bounds["minimum_inclusive"] = minimum_inclusive
            elif minimum == bounds["minimum"]:
                bounds["minimum_inclusive"] &= minimum_inclusive
        if maximum is not None:
            if (bounds["maximum"] is None or maximum < bounds["maximum"]
                    or maximum == bounds["maximum"] and not maximum_inclusive):
                bounds["maximum"] = maximum
                bounds["maximum_inclusive"] = maximum_inclusive
            elif maximum == bounds["maximum"]:
                bounds["maximum_inclusive"] &= maximum_inclusive

    for condition in [*left_conditions, *right_conditions]:
        try:
            bounds = version_condition_bounds(condition, model=model)
        except (DefinitionError, TypeError, ValueError, KeyError):
            # An invalid or model-unbound version condition cannot establish
            # that two applicability regions are complementary.
            return False
        if bounds is not None:
            path, minimum, maximum, minimum_exclusive = bounds
            tighten(path, minimum=minimum, minimum_inclusive=not minimum_exclusive,
                    maximum=maximum, maximum_inclusive=False)
            continue
        if (isinstance(condition, Mapping)
                and set(condition) == {"context", "equals"}
                and isinstance(condition.get("context"), str)
                and "version" in condition["context"].casefold()):
            try:
                equal = documented_version_parts(condition["equals"])
            except (DefinitionError, TypeError, ValueError):
                return False
            tighten(condition["context"], minimum=equal, minimum_inclusive=True,
                    maximum=equal, maximum_inclusive=True)

    for bounds in intervals.values():
        minimum, maximum = bounds["minimum"], bounds["maximum"]
        if minimum is not None and maximum is not None:
            if minimum > maximum:
                return True
            if (minimum == maximum
                    and not (bounds["minimum_inclusive"] and bounds["maximum_inclusive"])):
                return True
    return False


def _contexts_disjoint(left: Mapping[str, Any], right: Mapping[str, Any], *, model: str) -> bool:
    """Recognize explicit scope/equality/predicate and firmware complements only."""
    from plc.instruction_relations import fact_context_intersection

    try:
        if fact_context_intersection(left, right) is None:
            return True
    except (DefinitionError, TypeError, ValueError, KeyError):
        return False
    return _version_ranges_disjoint(left.get("conditions", ()), right.get("conditions", ()), model=model)


def _cpu_availability_claim(group: InstructionFactGroup, *, model: str) -> str | None:
    value = group.value
    states = value.get("model_states")
    has_claim = "available" in value or isinstance(states, Mapping) and model in states
    if not has_claim:
        return None
    available = value.get("available")
    state = states.get(model) if isinstance(states, Mapping) else None
    state_available = {"supported": True, "unsupported": False}.get(state)
    if type(available) is bool:
        if state == "conditional" or type(state_available) is bool and available is not state_available:
            return "inconsistent"
        return "supported" if available else "denied"
    if available is not None:
        return "uncertain"
    if state == "unsupported":
        return "denied"
    if state == "supported":
        return "supported"
    if state == "conditional":
        return "conditional"
    return "uncertain"


def _check_other_cpu_applicability(all_groups: list[InstructionFactGroup],
                                   contract_groups: list[InstructionFactGroup], *,
                                   opcode: str, model: str, version: str | None,
                                   refined_conditional_headers: frozenset[str] = frozenset()
                                   ) -> list[InstructionFactGroup]:
    """Reject overlapping verified CPU denials; retain proven-disjoint evidence."""
    selected_ids = {group.id for group in contract_groups}
    base_context = _merged_context(contract_groups, model=model)
    supplemental: dict[str, InstructionFactGroup] = {}
    for group in contract_groups:
        if group.dimension not in {"constraints.cpu_availability", "identity.applicability"}:
            continue
        claim = _cpu_availability_claim(group, model=model)
        if claim in {"denied", "uncertain", "inconsistent"}:
            raise _Refusal("contract_cpu_applicability_" + claim + ":" + group.id)
    for group in all_groups:
        if (group.id in selected_ids or group.id in refined_conditional_headers
                or group.dimension not in {"constraints.cpu_availability", "identity.applicability"}
                or not group.applies_to(opcode=opcode, model=model, language="st", version=version)):
            continue
        claim = _cpu_availability_claim(group, model=model)
        if claim is None or claim == "supported":
            continue
        if group.status != "source_verified" or group.verification != "source_checked":
            raise _Refusal("unverified_cpu_applicability:" + group.id)
        if not _has_source_for(group, opcode=opcode, model=model):
            raise _Refusal("cpu_applicability_source_mismatch:" + group.id)
        if claim == "inconsistent":
            raise _Refusal("inconsistent_cpu_applicability:" + group.id)
        negative_context = {"scope": copy.deepcopy(dict(group.scope or {})),
                            "conditions": _conditions_for_group(group)}
        if _contexts_disjoint(base_context, negative_context, model=model):
            negative_closure = _dependency_closure(all_groups, group.id, opcode=opcode,
                                                   model=model, version=version)
            _merged_context(negative_closure, model=model)
            for item in negative_closure:
                supplemental[item.id] = item
            continue
        if claim == "uncertain":
            raise _Refusal("uncertain_cpu_applicability:" + group.id)
        raise _Refusal("conflicting_cpu_applicability:" + group.id)
    return list(supplemental.values())


def resolve_structured_call_contract(facts, *, opcode: str, model: str,
                                     version: str | None = None) -> StructuredCallResolution:
    """Resolve one exact, reviewed ST signature for static binding.

    ``facts`` must be the extracted facts belonging to one instruction owner.
    Source status, CPU applicability, any attached conditions, and the full
    dependency closure are retained.  Conditions are not evaluated here, so
    callers must check them against the project context before binding a call.
    """
    if not isinstance(opcode, str) or not opcode.strip() or not isinstance(model, str) or not model.strip():
        return StructuredCallResolution(None, ("opcode_and_model_required",))
    opcode = opcode.strip()
    model = model.strip()
    try:
        groups = [item if isinstance(item, InstructionFactGroup) else InstructionFactGroup.from_mapping(item)
                  for item in facts]
        ids = [group.id for group in groups]
        if len(ids) != len(set(ids)):
            raise _Refusal("duplicate_fact_id")
        signatures = [group for group in groups
                      if group.dimension == "parameters.st_signature"
                      and group.applies_to(opcode=opcode, model=model, language="st", version=version)
                      and (str(group.value.get("name", "")).upper() == opcode.upper()
                           or _is_fx_structured_signature(group)
                           or _is_fx_positioning_statement_signature(group)
                           or _is_fx_analog_statement_signature(group))]
        if not signatures:
            raise _Refusal("no_applicable_st_signature")
        if len(signatures) != 1:
            raise _Refusal("ambiguous_st_signature")
        signature = signatures[0]
        if not _source_signature_matches_scope(signature, opcode=opcode, model=model):
            raise _Refusal("st_signature_scope_not_explicit")
        if signature.status != "source_verified" or signature.verification != "source_checked":
            raise _Refusal("st_signature_not_source_verified")
        if not _has_source_for(signature, opcode=opcode, model=model):
            raise _Refusal("st_signature_source_missing")
        is_fx_signature = _is_fx_structured_signature(signature)
        is_fx_instance = is_fx_signature and signature.value.get('call_kind') == 'function_block'
        is_fx_positioning_statement = _is_fx_positioning_statement_signature(signature)
        is_fx_analog_statement = _is_fx_analog_statement_signature(signature)
        if (_is_fx_applied_statement_signature(signature)
                and not (is_fx_positioning_statement or is_fx_analog_statement)):
            raise _Refusal("fx_applied_statement_form_not_supported")
        is_source_bound_instruction = _is_source_bound_instruction_signature(signature)
        source_value = signature.value
        raw_result = source_value.get('returns')
        return_binding = source_value.get('return_binding')
        if isinstance(raw_result, Mapping) and raw_result.get('array_element_reference'):
            return_binding = {key: copy.deepcopy(raw_result[key]) for key in
                              ('argument_passing', 'array_element_reference') if key in raw_result}
        if return_binding is not None and not isinstance(return_binding, Mapping):
            raise _Refusal('invalid_return_binding')
        value = source_value
        return_type_alternatives: tuple[str, ...] = ()
        execution_outputs: tuple[Mapping[str, Any], ...] = ()
        documented_form = None
        positional_arguments_verified = False
        if is_fx_instance:
            value = _normalise_fx_instance_signature(signature, opcode=opcode)
            return_role = 'no_return_value'
            documented_form = source_value['documented_call']
        elif is_fx_signature:
            reference_domains = _fx_reference_input_domains(
                groups, signature, opcode=opcode, model=model, version=version)
            value, return_type_alternatives = _normalise_fx_signature(
                value, opcode=opcode, reference_domains=reference_domains)
            raw_returns = source_value.get("returns")
            return_role = raw_returns.get("role") if isinstance(raw_returns, Mapping) else None
            if reference_domains:
                return_binding = {'kind': 'start_data_reference',
                    'source_parameter': raw_returns['address_source_parameter'],
                    'result_type': value['returns']}
            documented_form = source_value.get("documented_call")
        elif is_fx_positioning_statement or is_fx_analog_statement:
            value, execution_output = _normalise_fx_positioning_statement(value, opcode=opcode)
            return_role = "not_assigned_in_documented_statement"
            execution_outputs = (execution_output,)
            documented_form = value["documented_form"]
            positional_arguments_verified = True
        elif is_source_bound_instruction:
            value, documented_form, positional_arguments_verified = _normalise_source_bound_instruction(
                value, signature, opcode=opcode, model=model)
            return_role = None
        else:
            return_role = None
            documented_form = (source_value.get("documented_call")
                               if isinstance(source_value.get("documented_call"), str) else None)
            return_type_alternatives = _fx_type_alternatives(
                {"type_alternatives": source_value.get("return_type_alternatives", ())},
                label="return")
        _require_complete_signature_relation(value)
        if value.get("available") is not True:
            raise _Refusal("st_signature_unavailable")
        if value.get("name", "").upper() != opcode.upper():
            raise _Refusal("st_signature_name_mismatch")
        call_kind = value.get("call_kind")
        if call_kind not in {"function", "statement"} and not is_fx_instance and not (
                call_kind == "instruction" and is_source_bound_instruction):
            raise _Refusal("unsupported_call_kind")
        if value.get("instance_required") is not is_fx_instance:
            raise _Refusal("instance_contract_unavailable")
        enable = value.get("enable")
        if type(enable) is not bool:
            raise _Refusal("invalid_enable_contract")
        raw_parameters = value.get("parameters")
        if not isinstance(raw_parameters, (list, tuple)) or not raw_parameters:
            raise _Refusal("st_signature_parameters_missing")
        variadic_contract = (_fx_variadic_input_contract(
            groups, signature, tuple(value["parameters"]), opcode=opcode,
            model=model, version=version) if is_fx_signature else
            _source_optional_trailing_contract(
                groups, signature, tuple(value["parameters"]), opcode=opcode,
                model=model, version=version))
        variadic_parameter_name = variadic_contract[0] if variadic_contract else None
        variadic_minimum = variadic_contract[1] if variadic_contract else None
        variadic_maximum = variadic_contract[2] if variadic_contract else None
        if variadic_contract is not None:
            positional_arguments_verified = True
        seen_names: set[str] = set()
        seen_symbols: set[str] = set()
        parameters = tuple(_check_parameter(raw, index, seen_names, seen_symbols,
                              allow_reference_any=is_fx_signature,
                              allow_repeated_name=(variadic_parameter_name is not None
                                  and str(raw.get("name", "")).casefold()
                                      == variadic_parameter_name.casefold()))
                           for index, raw in enumerate(raw_parameters))
        if enable:
            enables = [parameter for parameter in parameters
                       if parameter["name"].casefold() == "en" or parameter["role"].casefold() == "enable"]
            if (len(enables) != 1 or parameters[0] is not enables[0]
                    or enables[0]["name"].casefold() != "en"
                    or enables[0]["role"].casefold() != "enable"
                    or enables[0]["direction"] != "input"
                    or str(enables[0]["type"]).upper() != "BOOL"):
                raise _Refusal("invalid_enable_parameter")
        elif any(parameter["name"].casefold() == "en" or parameter["role"].casefold() == "enable"
                 for parameter in parameters):
            raise _Refusal("unexpected_enable_parameter")
        returns_value = value.get("returns")
        if is_fx_instance:
            returns = None
        elif call_kind == "statement":
            if (not isinstance(returns_value, Mapping)
                    or returns_value.get("role") != "not_assigned_in_documented_statement"
                    or "type" in returns_value or "name" in returns_value):
                raise _Refusal("statement_return_must_remain_unassigned")
            returns = None
            return_role = "not_assigned_in_documented_statement"
            if not execution_outputs:
                raw_execution_output = value.get("execution_output")
                if not isinstance(raw_execution_output, Mapping):
                    raise _Refusal("statement_execution_output_missing")
                execution_outputs = (copy.deepcopy(dict(raw_execution_output)),)
        else:
            if not _is_supported_type(returns_value):
                raise _Refusal("invalid_st_return_type")
            returns = str(returns_value).strip()
        output_parameters = [parameter for parameter in parameters if parameter["direction"] == "output"]
        result_outputs = [parameter for parameter in output_parameters
                          if parameter["role"].casefold() in {"result", "return"}]
        if len(result_outputs) > 1:
            raise _Refusal("ambiguous_output_binding")

        base_closure = _dependency_closure(groups, signature.id, opcode=opcode,
                                           model=model, version=version)
        if variadic_contract is not None:
            base_ids = {group.id for group in base_closure}
            base_closure.extend(group for group in variadic_contract[3]
                                if group.id not in base_ids)
        fx_cpu_availability_ids = set()
        refined_conditional_headers = frozenset()
        if is_fx_signature or is_fx_analog_statement:
            fx_cpu_closure, refined_header_ids = _fx_cpu_availability(
                groups, signature, opcode=opcode, model=model, version=version)
            refined_conditional_headers = frozenset(refined_header_ids)
            fx_cpu_availability_ids = {group.id for group in fx_cpu_closure
                                       if group.dimension == "constraints.cpu_availability"}
            base_ids = {group.id for group in base_closure}
            base_closure.extend(group for group in fx_cpu_closure if group.id not in base_ids)
        if is_fx_positioning_statement:
            fx_cpu_closure = _fx_positioning_cpu_availability(
                groups, signature, opcode=opcode, model=model, version=version)
            base_ids = {group.id for group in base_closure}
            base_closure.extend(group for group in fx_cpu_closure
                                if group.id not in base_ids)
        if is_fx_positioning_statement or is_fx_analog_statement:
            statement_constraints = _fx_statement_constraint_facts(
                groups, signature, opcode=opcode, model=model, version=version,
                analog=is_fx_analog_statement)
            base_ids = {group.id for group in base_closure}
            base_closure.extend(group for group in statement_constraints
                                if group.id not in base_ids)
        if is_source_bound_instruction:
            if isinstance(signature.value.get("derived_call_shape"), Mapping):
                instruction_call_checks = _derived_source_bound_instruction_cross_checks(
                    groups, signature, parameters, opcode=opcode, model=model, version=version)
            else:
                instruction_call_checks = _source_bound_instruction_cross_checks(
                    groups, signature, parameters, opcode=opcode, model=model, version=version)
            base_ids = {group.id for group in base_closure}
            base_closure.extend(group for group in instruction_call_checks if group.id not in base_ids)
        availability = [group for group in base_closure
                        if group.dimension == "constraints.cpu_availability"]
        if is_fx_signature or is_fx_analog_statement:
            if not availability:
                raise _Refusal("cpu_availability_missing_or_ambiguous")
            if any(group.id not in fx_cpu_availability_ids for group in availability):
                raise _Refusal("cpu_availability_missing_or_ambiguous")
            if any(_cpu_availability_claim(group, model=model)
                   not in {"supported", "conditional"} for group in availability):
                raise _Refusal("cpu_unavailable")
        else:
            if len(availability) != 1:
                raise _Refusal("cpu_availability_missing_or_ambiguous")
            if _cpu_availability_claim(availability[0], model=model) != "supported":
                raise _Refusal("cpu_unavailable")

        cross_checks = ([] if is_fx_signature or is_fx_positioning_statement or is_fx_analog_statement
                        or is_source_bound_instruction or variadic_contract is not None else
                        _cross_check_st_call_arguments(groups, signature, parameters,
                                                       opcode=opcode, model=model, version=version))
        selected_by_id = {group.id: group for group in base_closure}
        for group in cross_checks:
            selected_by_id[group.id] = group
        for group in _check_other_cpu_applicability(
                groups, list(selected_by_id.values()), opcode=opcode, model=model, version=version,
                refined_conditional_headers=refined_conditional_headers):
            selected_by_id[group.id] = group
        selected_ids = set(selected_by_id)
        ordered_closure = [group for group in groups if group.id in selected_ids]
        # Preserve dependencies that appeared only through selector output even
        # if a caller's original owner list had an unusual order.
        ordered_closure.extend(group for identity, group in selected_by_id.items()
                               if identity not in {item.id for item in ordered_closure})
        condition_rows = []
        for group in ordered_closure:
            if group.conditions:
                condition_rows.append({"fact_id": group.id,
                                       "scope": copy.deepcopy(dict(group.scope or {})),
                                       "conditions": copy.deepcopy(list(group.conditions))})
            for source in group.sources:
                for source_field in ("conditions", "applicability"):
                    source_conditions = source.get(source_field)
                    if not source_conditions:
                        continue
                    values = (list(source_conditions) if isinstance(source_conditions, (list, tuple))
                              else [source_conditions])
                    condition_rows.append({"fact_id": group.id,
                                           "source_ref": copy.deepcopy(source.get("source_ref")),
                                           "condition_origin": source_field,
                                           "scope": copy.deepcopy(dict(group.scope or {})),
                                           "conditions": copy.deepcopy(values)})
        conditions = tuple(condition_rows)
        source_values = []
        for group in ordered_closure:
            for source in group.sources:
                detached = copy.deepcopy(dict(source))
                if detached not in source_values:
                    source_values.append(detached)
        contract = StructuredCallContract(
            opcode=opcode,
            model=model,
            name=str(value["name"]),
            call_kind=call_kind,
            enable=enable,
            instance_required=is_fx_instance,
            parameters=parameters,
            returns=returns,
            return_type_alternatives=return_type_alternatives,
            return_role=return_role,
            execution_outputs=execution_outputs,
            documented_form=documented_form,
            positional_arguments_verified=positional_arguments_verified,
            variadic_parameter_name=variadic_parameter_name,
            minimum_variadic_arguments=variadic_minimum,
            maximum_variadic_arguments=variadic_maximum,
            fact_ids=tuple(group.id for group in ordered_closure),
            facts=tuple(group.as_mapping() for group in ordered_closure),
            conditions=conditions,
            conditions_evaluated=False,
            sources=tuple(source_values),
            return_binding=copy.deepcopy(return_binding),
        )
        return StructuredCallResolution(contract)
    except _Refusal as refusal:
        return StructuredCallResolution(None, (refusal.reason,))
    except (DefinitionError, TypeError, ValueError, KeyError, AttributeError) as error:
        return StructuredCallResolution(None, ("invalid_source_facts:" + str(error),))

