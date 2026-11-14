"""Focused validation for source-bound literal ST expression contracts."""
from __future__ import annotations

import copy

import pytest

from knowledge.instruction_compiler import refresh_reviewed_structured_expression_admission
from plc.instructions import InstructionRegistry
from plc.structured_expression_contract import resolve_structured_expression_contract


MODEL = "Q03UDV"
PRIORITY = {"&": 3, "**": 9, "<": 5, "<=": 5, "<>": 4, "=": 4, ">": 5, ">=": 5}
N_PAGE = {"&": 215, "**": 214, "<": 217, "<=": 217, "<>": 217,
          "=": 217, ">": 217, ">=": 217}
DEPENDENCIES = (
    "authored:ql:literal_typed:parameters.typed_operands",
    "authored:ql:literal_argument_ST_expression_operands:parameters.arity",
    "authored:ql:literal_argument_ST_expression_operands:parameters.order",
    "authored:ql:cpu_applicability:constraints.cpu_availability",
)


def _source(opcode, model, manual, revision, pages):
    return {
        "manual_id": manual,
        "revision": revision,
        "pdf_pages": list(pages),
        "opcode": opcode,
        "target_model": model,
        "source_ref": f"{manual}:{revision}:pages={','.join(str(page) for page in pages)}",
    }


def _fact(identity, dimension, value, *, operator, model=MODEL, pages=None,
          manual="sh080784engn", revision="N", depends_on=(), conditions=(),
          languages=("st",)):
    return {
        "id": identity,
        "dimension": dimension,
        "value": value,
        "scope": {"forms": [operator], "models": [model], "languages": list(languages)},
        "conditions": list(conditions),
        "depends_on": list(depends_on),
        "status": "source_verified",
        "verification": "source_checked",
        "sources": [_source(operator, model, manual, revision,
                            pages if pages is not None else [N_PAGE[operator]])],
    }


def _expression_facts(operator, *, model=MODEL, cpu_state="supported", cpu_conditions=()):
    maximum = 2 if operator in {"**", "<>"} else 28
    minimum = 2
    if operator == "&":
        operand_types, result = ["ANY_BIT"] * maximum, "ANY_BIT"
        grammar_key = "logical"
        expression_n_pages = [215, 216]
    elif operator == "**":
        operand_types, result = ["ANY_REAL", "ANY_NUM"], "ANY_REAL"
        grammar_key = "power"
        expression_n_pages = [214]
    else:
        operand_types, result = ["ANY_SIMPLE"] * maximum, "BOOL"
        grammar_key = "comparison"
        expression_n_pages = [217]
    operands = [
        {"name": f"s{index}", "type": data_type, "direction": "input",
         **({"optional": True} if index > minimum else {})}
        for index, data_type in enumerate(operand_types, start=1)
    ]
    expression = _fact(
        "authored:ql:literal_expression:parameters.language_expression",
        "parameters.language_expression",
        {"operator": operator, "operands": [row["name"] for row in operands],
         "returns": result, "available": True, "representation": "ST_expression",
         "operator_priority": PRIORITY[operator],
         "same_priority_evaluation": "leftmost_operator_first",
         **({"comparison_chain_intermediate_result_type": "Bit",
             "comparison_operand_type_compatibility": "not_stated_in_reviewed_source"}
            if operator in {"<", "<=", "<>", "=", ">", ">="} else {})},
        operator=operator, model=model, pages=expression_n_pages,
        depends_on=DEPENDENCIES,
    )
    expression["sources"].append(_source(operator, model, "structured_fundamentals_o", "O", [76]))
    typed = _fact(DEPENDENCIES[0], "parameters.typed_operands",
                  {"operands": operands, "operand_count": maximum,
                   "representation": "ST_expression"}, operator=operator, model=model)
    arity = _fact(DEPENDENCIES[1], "parameters.arity",
                  {"minimum": minimum, "maximum": maximum,
                   "count_kind": "ST_expression_operands"}, operator=operator, model=model)
    order = _fact(DEPENDENCIES[2], "parameters.order",
                  {"symbols": [row["name"] for row in operands],
                   "order_source": "ST_expression_operands"}, operator=operator, model=model)
    cpu = _fact(DEPENDENCIES[3], "constraints.cpu_availability",
                {"available": True if cpu_state == "supported" else None,
                 "model_states": {model: cpu_state}, "rule": "documented_CPU_instruction_scope"},
                operator=operator, model=model, conditions=cpu_conditions)
    refusal = _fact(
        "authored:ql:literal_not_function:parameters.st_signature",
        "parameters.st_signature",
        {"available": False, "reason": "literal_ST_operator_is_an_expression"},
        operator=operator, model=model)
    facts = [expression, typed, arity, order, cpu, refusal]
    return facts, grammar_key


def _grammar_feature(operator, grammar_key, *, model=MODEL):
    rule = {
        "logical": {"inputs": ["ANY_BIT"], "result": "ANY_BIT",
                    "operators": ["AND", "&", "XOR", "OR", "NOT"]},
        "power": {"inputs": ["ANY_REAL", "ANY_NUM"], "result": "ANY_REAL",
                  "operators": ["**"]},
        "comparison": {"inputs": ["ANY_SIMPLE"], "result": "BOOL",
                       "operators": ["<", ">", "<=", ">=", "=", "<>"]},
    }[grammar_key]
    precedence = dict(PRIORITY)
    fact = {
        "id": "st:operator_binding",
        "dimension": "parameters.operator_binding",
        "value": {"precedence": precedence, "associativity": "left",
                  "types": {grammar_key: rule}},
        "scope": {"forms": ["ST.OPERATORS"], "models": [model], "languages": ["st"]},
        "conditions": [], "depends_on": [], "status": "source_verified",
        "verification": "source_checked",
        "sources": [{"manual_id": "structured_fundamentals_o", "revision": "O",
                     "pdf_pages": [76], "source_ref": "structured_fundamentals_o:O:pages=76"}],
    }
    return {"target": "ST.OPERATORS", "facts": [fact]}


@pytest.mark.parametrize("operator", ("&", "**", "<", "<=", "<>", "=", ">", ">="))
def test_exact_source_expression_contract_keeps_st_only_shape_and_bounds(operator):
    facts, grammar_key = _expression_facts(operator)
    original = copy.deepcopy(facts)

    result = resolve_structured_expression_contract(
        facts, operator=operator, model=MODEL,
        language_feature=_grammar_feature(operator, grammar_key))

    assert result.accepted, result.refusal_reasons
    contract = result.contract.as_mapping()
    assert contract["call_kind"] == "expression"
    assert contract["language"] == "st"
    assert contract["interfaces"] == ["st"]
    assert contract["source_available"] is True
    assert contract["representation"] == "ST_expression"
    assert contract["conditions_evaluated"] is False
    assert contract["maximum_operands"] == (2 if operator in {"**", "<>"} else 28)
    assert contract["minimum_operands"] == 2
    assert contract["operator_priority"] == PRIORITY[operator]
    assert contract["associativity"] == "left"
    assert facts == original
    if operator in {"<", "<=", "<>", "=", ">", ">="}:
        assert contract["comparison_chain_intermediate_result_type"] == "Bit"
        assert contract["comparison_operand_type_compatibility"] == "not_stated_in_reviewed_source"
    else:
        assert contract["comparison_chain_intermediate_result_type"] is None


def test_conditions_remain_unresolved_in_returned_expression_contract():
    condition = {"kind": "serial_number_minimum", "minimum": "04122", "digits": 5}
    facts, grammar_key = _expression_facts("<", cpu_state="conditional", cpu_conditions=[condition])

    result = resolve_structured_expression_contract(
        facts, operator="<", model=MODEL,
        language_feature=_grammar_feature("<", grammar_key))

    assert result.accepted, result.refusal_reasons
    assert condition in result.contract.as_mapping()["conditions"]
    assert result.contract.conditions_evaluated is False


@pytest.mark.parametrize("operator", ("**", "<>", "<", "&"))
def test_missing_source_dependency_or_arity_disagreement_is_refused(operator):
    facts, grammar_key = _expression_facts(operator)
    facts[0]["depends_on"].remove(DEPENDENCIES[2])
    result = resolve_structured_expression_contract(
        facts, operator=operator, model=MODEL,
        language_feature=_grammar_feature(operator, grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_dependency_contract_incomplete",)

    facts, grammar_key = _expression_facts(operator)
    facts[2]["value"]["maximum"] += 1
    result = resolve_structured_expression_contract(
        facts, operator=operator, model=MODEL,
        language_feature=_grammar_feature(operator, grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_arity_contract_mismatch",)


def test_source_scope_cpu_denial_and_callable_signature_are_refused():
    facts, grammar_key = _expression_facts("&")
    facts[1]["sources"][0]["target_model"] = "Q06UDV"
    result = resolve_structured_expression_contract(
        facts, operator="&", model=MODEL,
        language_feature=_grammar_feature("&", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("source_scope_mismatch:" + DEPENDENCIES[0],)

    facts, grammar_key = _expression_facts("&")
    facts[4]["value"].update(available=False, model_states={MODEL: "unsupported"})
    result = resolve_structured_expression_contract(
        facts, operator="&", model=MODEL,
        language_feature=_grammar_feature("&", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("cpu_applicability_denied:" + DEPENDENCIES[3],)

    facts, grammar_key = _expression_facts("&")
    facts[5]["value"]["available"] = True
    result = resolve_structured_expression_contract(
        facts, operator="&", model=MODEL,
        language_feature=_grammar_feature("&", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_conflicts_with_callable_signature",)


def test_grammar_precedence_and_comparison_chain_metadata_must_match_source():
    facts, grammar_key = _expression_facts("<")
    facts[0]["value"]["operator_priority"] = 6
    result = resolve_structured_expression_contract(
        facts, operator="<", model=MODEL,
        language_feature=_grammar_feature("<", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_precedence_conflicts_with_st_grammar",)

    facts, grammar_key = _expression_facts("<")
    facts[0]["value"]["comparison_operand_type_compatibility"] = "adjacent_pairwise"
    result = resolve_structured_expression_contract(
        facts, operator="<", model=MODEL,
        language_feature=_grammar_feature("<", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("comparison_chain_contract_incomplete",)


def test_ampersand_historical_panel_is_accepted_only_with_its_resolving_example():
    facts, grammar_key = _expression_facts("&")
    facts[0]["value"]["source_syntax_history"] = {
        "historical_page": 215,
        "resolution": "unresolved",
    }
    result = resolve_structured_expression_contract(
        facts, operator="&", model=MODEL,
        language_feature=_grammar_feature("&", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_source_syntax_history_unresolved",)


def test_non_expression_symbols_and_missing_general_st_grammar_are_refused():
    facts, grammar_key = _expression_facts("<")
    result = resolve_structured_expression_contract(
        facts, operator="UNREVIEWED_OPERATOR", model=MODEL,
        language_feature=_grammar_feature("<", grammar_key))
    assert not result.accepted
    assert result.refusal_reasons == ("unsupported_st_expression_operator",)

    result = resolve_structured_expression_contract(
        facts, operator="<", model=MODEL, language_feature=None)
    # Supplying the default registry here is deliberately avoided in the
    # synthetic test; an explicit malformed feature verifies fail-closed input.
    assert result.accepted  # The installed registry supplies the reviewed grammar fact.

    bad_feature = {"target": "ST.OPERATORS", "facts": []}
    result = resolve_structured_expression_contract(
        facts, operator="<", model=MODEL, language_feature=bad_feature)
    assert not result.accepted
    assert result.refusal_reasons == ("st_operator_grammar_missing_or_ambiguous",)


class _ExpressionRegistry:
    def __init__(self, entry, feature):
        self.entry = entry
        self.feature = feature

    def language_feature(self, target, *, language=None, cpu=None):
        if (target == "ST.OPERATORS" and language in (None, "st")
                and cpu in (None, self.entry["target_model"])):
            return copy.deepcopy(self.feature)
        return None

    def resolve_catalog_form(self, opcode, *, cpu):
        return None

    def definition_entry(self, opcode, *, cpu, vendor="mitsubishi"):
        if opcode.upper() == self.entry["opcode"].upper() and cpu == self.entry["target_model"]:
            return copy.deepcopy(self.entry)
        return None

    def expression_entry(self, token, *, cpu, vendor="mitsubishi"):
        details = self.entry.get("call_admission_details", {})
        marker = details.get("structured_expression") if isinstance(details, dict) else None
        if (cpu == self.entry["target_model"] and isinstance(marker, dict)
                and marker.get("available") is True
                and str(marker.get("operator", "")).upper() == token.upper()):
            return copy.deepcopy(self.entry)
        return None

    def fact_bundle_current(self):
        return True


def _entry_for_expression(operator="&"):
    facts, grammar_key = _expression_facts(operator)
    return ({"vendor": "mitsubishi", "opcode": operator, "target_model": MODEL,
             "registry_member": False, "call_available": False,
             "support_status": "definition_only", "facts": facts},
            _grammar_feature(operator, grammar_key))


FX_MODEL = "FX3U"
FX_PAGES = {"POWER": [270], "MOD": [269], "AND": [271], "XOR": [273, 274], "NOT": [275]}
FX_TOKENS = {"POWER": "**", "MOD": "MOD", "AND": "AND", "XOR": "XOR", "NOT": "NOT"}
FX_PRIORITY = {"**": 9, "MOD": 7, "AND": 3, "XOR": 2, "NOT": 8}


def _fx_source(owner, model, pages):
    return _source(owner, model, "fxcpu_application_k", "K", pages)


def _fx_fact(identity, dimension, value, *, owner, model=FX_MODEL, pages=None,
             conditions=(), languages=("st",)):
    pages = list(FX_PAGES[owner] if pages is None else pages)
    return {
        "id": identity,
        "dimension": dimension,
        "value": value,
        "scope": {"forms": [owner], "models": [model], "languages": list(languages)},
        "conditions": list(conditions),
        "depends_on": [],
        "status": "source_verified",
        "verification": "source_checked",
        "sources": [_fx_source(owner, model, pages)],
    }


def _fx_expression_facts(owner, *, model=FX_MODEL, cpu_state="supported", cpu_conditions=()):
    token = FX_TOKENS[owner]
    if owner == "POWER":
        form, infix = ["s1", "**", "s2"], True
        rows = [("s1", "input", {"kind": "real", "bits": 32}),
                ("s2", "input", {"flexible_type": "ANY_NUM"})]
        result_type, input_names = {"kind": "real", "bits": 32}, ["s1", "s2"]
    elif owner == "MOD":
        form, infix = ["s1", "MOD", "s2"], True
        rows = [("s1", "input", {"flexible_type": "ANY_INT"}),
                ("s2", "input", {"flexible_type": "ANY_INT"})]
        result_type, input_names = {"flexible_type": "ANY_INT"}, ["s1", "s2"]
    elif owner in {"AND", "XOR"}:
        form, infix = ["s1", token, "s2"], True
        rows = [("s1", "input", {"flexible_type": "ANY_BIT"})]
        result_type, input_names = {"flexible_type": "ANY_BIT"}, ["s1"]
    else:
        form, infix = "NOT(s)", False
        rows = [("s", "input", {"flexible_type": "ANY_BIT"})]
        result_type, input_names = {"flexible_type": "ANY_BIT"}, ["s"]
    pages = FX_PAGES[owner]
    output_type = copy.deepcopy(result_type)
    typed_operands = [
        {"name": name, "marker": name, "direction": direction, "data_type": data_type}
        for name, direction, data_type in rows]
    typed_operands.append({"name": "d", "marker": "d", "direction": "output",
                           "data_type": output_type})
    expression = _fx_fact(
        f"structured:{owner.lower()}:operator_operator_form@fxcpu_application_k",
        "parameters.operator_expression",
        {"symbol": {"name": token.lower(), "ascii_hex": token.encode("ascii").hex()},
         "expression_form": form, "infix": infix},
        owner=owner, model=model, pages=pages)
    typed = _fx_fact(
        f"structured:{owner.lower()}:operator_operands@fxcpu_application_k",
        "parameters.typed_operands",
        {"operands": typed_operands,
         "return_shape": {"value_type": copy.deepcopy(result_type),
                          "infix_expression": infix}},
        owner=owner, model=model, pages=pages,
        languages=("st", "fbd") if owner in {"AND", "XOR"} else ("st",))
    result_value = {"result_type": copy.deepcopy(result_type)}
    if owner in {"AND", "XOR"}:
        result_value["operand_pins"] = {"minimum": 2, "maximum": 28}
    result = _fx_fact(
        f"structured:{owner.lower()}:operator_result@fxcpu_application_k",
        "effects.result_rule", result_value, owner=owner, model=model, pages=pages)
    cpu = _fx_fact(
        f"structured:{owner.lower()}:cpu_availability_header@fxcpu_application_k",
        "constraints.cpu_availability",
        {"available": True if cpu_state == "supported" else None,
         "model_states": {model: cpu_state}},
        owner=owner, model=model, pages=pages, conditions=cpu_conditions)
    identity = _fx_fact(
        f"structured:{owner.lower()}:form@fxcpu_application_k", "identity.form",
        {"category": "operator", "callable_kind": "operator",
         "call_basis": "operator_expression"}, owner=owner, model=model, pages=pages)
    language = _fx_fact(
        f"structured:{owner.lower()}:language_st@fxcpu_application_k",
        "identity.language_availability", {"language": "st", "available": True},
        owner=owner, model=model, pages=pages)
    return [expression, typed, result, cpu, identity, language]


def _fx_grammar_feature(owner, *, model=FX_MODEL):
    token = FX_TOKENS[owner]
    key = {"POWER": "power", "MOD": "integer", "AND": "logical", "XOR": "logical", "NOT": "logical"}[owner]
    rule = {
        "power": {"inputs": ["ANY_REAL", "ANY_NUM"], "result": "ANY_REAL", "operators": ["**"]},
        "integer": {"inputs": ["ANY_INT"], "result": "ANY_INT", "operators": ["MOD"]},
        "logical": {"inputs": ["ANY_BIT"], "result": "ANY_BIT", "operators": ["AND", "XOR", "NOT"]},
    }[key]
    fact = {
        "id": "st:operator_binding", "dimension": "parameters.operator_binding",
        "value": {"precedence": dict(FX_PRIORITY), "associativity": "left", "types": {key: rule}},
        "scope": {"forms": ["ST.OPERATORS"], "models": [model], "languages": ["st"]},
        "conditions": [], "depends_on": [], "status": "source_verified",
        "verification": "source_checked",
        "sources": [{"manual_id": "structured_fundamentals_o", "revision": "O",
                     "pdf_pages": [76], "source_ref": "structured_fundamentals_o:O:pages=76"}],
    }
    return {"target": "ST.OPERATORS", "facts": [fact]}


@pytest.mark.parametrize("owner,token,form,minimum,maximum,types,result_type", [
    ("MOD", "MOD", "infix", 2, 2, ["ANY_INT", "ANY_INT"], "ANY_INT"),
    ("XOR", "XOR", "infix", 2, 28, ["ANY_BIT"], "ANY_BIT"),
    ("AND", "AND", "infix", 2, 28, ["ANY_BIT"], "ANY_BIT"),
    ("NOT", "NOT", "prefix", 1, 1, ["ANY_BIT"], "ANY_BIT"),
    ("POWER", "**", "infix", 2, 2, ["REAL", "ANY_NUM"], "REAL"),
])
def test_fx_k_operator_facts_resolve_without_function_aliasing(
        owner, token, form, minimum, maximum, types, result_type):
    facts = _fx_expression_facts(owner)
    if owner in {"MOD", "NOT"}:
        facts.append(_fx_fact(
            f"structured:{owner.lower()}:function_signature@fxcpu_application_k",
            "parameters.st_signature", {"available": True, "call_kind": "function"},
            owner=owner, pages=[171 if owner == "MOD" else 189]))
    result = resolve_structured_expression_contract(
        facts, operator=token, owner_opcode=owner, model=FX_MODEL,
        language_feature=_fx_grammar_feature(owner))

    assert result.accepted, result.refusal_reasons
    contract = result.contract.as_mapping()
    assert contract["operator"] == token
    assert contract["owner_opcode"] == owner
    assert contract["operator_form"] == form
    assert contract["minimum_operands"] == minimum
    assert contract["maximum_operands"] == maximum
    assert contract["variadic"] is (maximum > minimum)
    assert contract["operand_types"] == types
    assert contract["result_type"] == result_type
    assert contract["call_kind"] == "expression" and contract["interfaces"] == ["st"]
    assert contract["conditions_evaluated"] is False


def test_fx_k_candidate_uses_primary_operands_and_keeps_equivalent_cpu_witnesses():
    facts = _fx_expression_facts("MOD")
    expression, typed, result, _cpu, _identity, _language = facts

    # The compiler may append an assembly relation copy of the typed source
    # fact. It must not compete with that source fact as a second signature.
    derived = copy.deepcopy(typed)
    derived["id"] = "assembly:parameters:structured:mod:operator_operands@fxcpu_application_k"
    derived["depends_on"] = [typed["id"], result["id"]]
    facts.append(derived)

    # The composed candidate also carries the manual-header CPU claim from
    # source compilation alongside the authored structured header fact.
    catalogue_cpu = _fx_fact(
        "source:cpu_availability:fxcpu_application_k:2939",
        "constraints.cpu_availability",
        {"model_states": {FX_MODEL: "supported"}, "rule": "instruction_not_supported_on_selected_CPU"},
        owner="MOD", pages=[269], languages=("st", "fbd"))
    facts.append(catalogue_cpu)

    result_resolution = resolve_structured_expression_contract(
        facts, operator="MOD", owner_opcode="MOD", model=FX_MODEL,
        language_feature=_fx_grammar_feature("MOD"))
    assert result_resolution.accepted, result_resolution.refusal_reasons
    contract = result_resolution.contract.as_mapping()
    assert typed["id"] in contract["fact_ids"]
    assert derived["id"] not in contract["fact_ids"]
    assert catalogue_cpu["id"] in contract["fact_ids"]
    assert facts[3]["id"] in contract["fact_ids"]

    # Identical positive states do not authorize different applicability.
    facts[-1]["conditions"] = [{"context": "project_mode", "equals": "different"}]
    conflicted = resolve_structured_expression_contract(
        facts, operator="MOD", owner_opcode="MOD", model=FX_MODEL,
        language_feature=_fx_grammar_feature("MOD"))
    assert not conflicted.accepted
    assert conflicted.refusal_reasons == ("cpu_applicability_conflicting_claims",)


def test_fx_k_expression_refuses_wrong_owner_type_and_cpu_claim():
    facts = _fx_expression_facts("POWER")
    result = resolve_structured_expression_contract(
        facts, operator="**", owner_opcode="MOD", model=FX_MODEL,
        language_feature=_fx_grammar_feature("POWER"))
    assert not result.accepted
    assert result.refusal_reasons == ("source_operator_owner_mismatch",)

    facts = _fx_expression_facts("MOD")
    facts[1]["value"]["operands"][0]["data_type"] = {"kind": "real", "bits": 32}
    result = resolve_structured_expression_contract(
        facts, operator="MOD", owner_opcode="MOD", model=FX_MODEL,
        language_feature=_fx_grammar_feature("MOD"))
    assert not result.accepted
    assert result.refusal_reasons == ("expression_types_conflict_with_st_grammar",)

    facts = _fx_expression_facts("XOR")
    facts[3]["value"].update(available=False, model_states={FX_MODEL: "unsupported"})
    result = resolve_structured_expression_contract(
        facts, operator="XOR", owner_opcode="XOR", model=FX_MODEL,
        language_feature=_fx_grammar_feature("XOR"))
    assert not result.accepted
    assert result.refusal_reasons == ("cpu_applicability_denied:" + facts[3]["id"],)


@pytest.mark.parametrize("operator", ("&", "**", "<", "<=", "<>", "=", ">", ">="))
def test_expression_admission_and_describe_are_st_only_and_not_callable(operator):
    entry, feature = _entry_for_expression(operator)
    registry = _ExpressionRegistry(entry, feature)

    assert refresh_reviewed_structured_expression_admission(entry, registry)
    assert entry["call_available"] is True
    assert entry["support_status"] == "reviewed_st_expression"
    assert entry["call_admission_details"]["interfaces"] == ["st"]
    assert entry["call_admission_details"]["structured_expression"]["call_kind"] == "expression"
    assert not any(fact["dimension"] == "parameters.st_signature"
                   and fact["value"].get("available") is True for fact in entry["facts"])

    described_registry = InstructionRegistry([])
    described_registry._definition_owners[("mitsubishi", operator, MODEL)] = copy.deepcopy(entry)
    described_registry._language_features = {"ST.OPERATORS": copy.deepcopy(feature)}
    call = described_registry.describe_call(operator, cpu=MODEL)
    contract = described_registry.describe_contract(operator, cpu=MODEL)

    assert call["call_kind"] == "expression"
    assert call["interfaces"] == ["st"]
    assert "st_signature" not in call and "st_parameters" not in call and "st_call" not in call
    assert contract["call_kind"] == "expression"
    assert contract["representation"] == "ST_expression"
    assert described_registry.resolve_st_call(operator, cpu=MODEL) is None
    assert described_registry.resolve_form(operator, cpu=MODEL) is None


def test_expression_refresher_does_not_load_grammar_for_non_expression_owner():
    class Registry:
        def language_feature(self, *args, **kwargs):
            raise AssertionError("non-expression owner must not load ST.OPERATORS")

    entry = {"target_model": MODEL, "opcode": "MOV", "facts": [],
             "call_available": False, "support_status": "definition_only"}
    assert not refresh_reviewed_structured_expression_admission(entry, Registry())
    assert entry["call_available"] is False


def test_expression_refresher_clears_stale_marker_after_source_removal():
    class Registry:
        def language_feature(self, *args, **kwargs):
            return None

    entry = {"target_model": MODEL, "opcode": "&", "facts": [],
             "call_available": True, "support_status": "reviewed_st_expression",
             "call_admission_details": {"interfaces": ["st"], "structured_expression": {
                 "available": True, "call_kind": "expression"}}}
    assert not refresh_reviewed_structured_expression_admission(entry, Registry())
    assert entry["call_available"] is False
    assert entry["support_status"] == "definition_only"
    assert entry["call_admission_details"]["interfaces"] == []
    assert "structured_expression" not in entry["call_admission_details"]


def test_inspector_binds_only_admitted_expression_and_enforces_source_types(monkeypatch):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _entry_for_expression()
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding", lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    valid = inspect_st_program(
        "VAR a : WORD; b : WORD; out : WORD; END_VAR out := a & b;",
        plc_model=MODEL)
    assert valid["valid"], valid["findings"]
    assert not any(f["code"].startswith("ST_CALL_") for f in valid["findings"])

    entry["call_available"] = False
    entry["support_status"] = "definition_only"
    entry["call_admission_details"] = {"interfaces": []}
    registry.entry = entry
    unadmitted = inspect_st_program(
        "VAR a : WORD; b : WORD; out : WORD; END_VAR out := a & b;",
        plc_model=MODEL)
    assert unadmitted["status"] == "unverified"
    assert any(f["code"] == "ST_EXPRESSION_COVERAGE" for f in unadmitted["findings"])

    entry, feature = _entry_for_expression()
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    registry.entry = entry
    monkeypatch.setattr(st_frontend, "operator_binding", lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)
    mistyped = inspect_st_program(
        "VAR a : REAL; b : REAL; out : REAL; END_VAR out := a & b;",
        plc_model=MODEL)
    assert mistyped["status"] == "failed"
    assert any(f["code"] == "ST_TYPE" and "requires ANY_BIT" in f["message"]
               for f in mistyped["findings"])


def test_inspector_rejects_source_operator_arity_overflow(monkeypatch):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _entry_for_expression()
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding", lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    declarations = " ".join(f"v{index} : WORD;" for index in range(1, 30))
    expression = " & ".join(f"v{index}" for index in range(1, 30))
    source = f"VAR {declarations} out : WORD; END_VAR out := {expression};"
    result = inspect_st_program(source, plc_model=MODEL)
    assert result["status"] == "failed"
    assert any(f["code"] == "ST_EXPRESSION_ARITY" and "29 operands" in f["message"]
               for f in result["findings"])


def _fx_entry_for_expression(owner):
    facts = _fx_expression_facts(owner)
    entry = {"vendor": "mitsubishi", "opcode": owner, "target_model": FX_MODEL,
             "registry_member": True, "call_available": True,
             "support_status": "reviewed_st_signature", "facts": facts,
             "call_admission_details": {"interfaces": ["st"],
                 "structured_call": {"available": True}}}
    return entry, _fx_grammar_feature(owner)


@pytest.mark.parametrize("owner,source", [
    ("MOD", "VAR a : INT; b : INT; out : INT; END_VAR out := a MOD b;"),
    ("XOR", "VAR a : WORD; b : WORD; c : WORD; out : WORD; END_VAR out := a XOR b XOR c;"),
    ("AND", "VAR a : WORD; b : WORD; c : WORD; out : WORD; END_VAR out := a AND b AND c;"),
    ("NOT", "VAR a : WORD; out : WORD; END_VAR out := NOT(a);"),
    ("POWER", "VAR a : REAL; b : DINT; out : REAL; END_VAR out := a ** b;"),
])
def test_fx_k_admitted_expression_binds_in_its_source_form(monkeypatch, owner, source):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression(owner)
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    assert entry["call_admission_details"]["structured_expression"]["owner_opcode"] == owner
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    result = inspect_st_program(source, plc_model=FX_MODEL)
    assert result["valid"], result["findings"]
    assert not any(finding["code"].startswith("ST_CALL_") for finding in result["findings"])


@pytest.mark.parametrize("owner,source,expected_finding", [
    ("MOD", "VAR a : REAL; b : REAL; out : REAL; END_VAR out := a MOD b;", "ST_TYPE"),
    ("AND", "VAR a : REAL; b : REAL; out : REAL; END_VAR out := a AND b;", "ST_TYPE"),
    ("NOT", "VAR a : REAL; out : REAL; END_VAR out := NOT(a);", "ST_TYPE"),
    ("POWER", "VAR a : DINT; b : DINT; out : REAL; END_VAR out := a ** b;", "ST_TYPE"),
])
def test_fx_k_admitted_expression_rejects_source_type_mismatch(
        monkeypatch, owner, source, expected_finding):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression(owner)
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    result = inspect_st_program(source, plc_model=FX_MODEL)
    assert result["status"] == "failed"
    assert any(finding["code"] == expected_finding for finding in result["findings"])


@pytest.mark.parametrize("owner", ["AND", "XOR"])
@pytest.mark.parametrize("count", [28, 29])
def test_fx_k_bit_expression_enforces_source_variadic_limit(monkeypatch, owner, count):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression(owner)
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    declarations = " ".join(f"v{index} : WORD;" for index in range(1, count + 1))
    expression = f" {owner} ".join(f"v{index}" for index in range(1, count + 1))
    source = f"VAR {declarations} out : WORD; END_VAR out := {expression};"
    result = inspect_st_program(source, plc_model=FX_MODEL)
    if count == 28:
        assert result["valid"], result["findings"]
        return
    assert result["status"] == "failed"
    assert any(finding["code"] == "ST_EXPRESSION_ARITY"
               and "29 operands" in finding["message"] for finding in result["findings"])


def test_shared_and_grammar_does_not_require_fx_operator_admission(monkeypatch):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression("AND")
    entry.update(call_available=False, facts=[], call_admission_details={})
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY",
                        _ExpressionRegistry(entry, feature))
    result = inspect_st_program(
        "VAR a : BOOL; b : BOOL; out : BOOL; END_VAR out := a AND b;", plc_model=FX_MODEL)
    assert result["valid"], result["findings"]


def test_fx_k_not_requires_its_documented_parenthesized_prefix(monkeypatch):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression("NOT")
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)

    result = inspect_st_program(
        "VAR a : WORD; out : WORD; END_VAR out := NOT a;", plc_model=FX_MODEL)
    assert result["status"] == "failed"
    assert any(finding["code"] == "ST_EXPRESSION_FORM"
               and "parenthesized prefix operand" in finding["message"]
               for finding in result["findings"])


def test_fx_k_source_condition_is_bound_to_its_fact_and_checked_in_project(monkeypatch):
    import plc.st_frontend as st_frontend
    import plc.st_semantics as st_semantics
    from plc.st_validation import inspect_st_program

    entry, feature = _fx_entry_for_expression("MOD")
    condition = {"context": "project.software_version", "minimum": "1.10"}
    entry["facts"][1]["conditions"] = [condition]
    registry = _ExpressionRegistry(entry, feature)
    assert refresh_reviewed_structured_expression_admission(entry, registry)
    monkeypatch.setattr(st_frontend, "operator_binding",
                        lambda: copy.deepcopy(feature["facts"][0]["value"]))
    monkeypatch.setattr(st_semantics, "DEFAULT_INSTRUCTION_REGISTRY", registry)
    source = "VAR a : INT; b : INT; out : INT; END_VAR out := a MOD b;"

    unresolved = inspect_st_program(source, plc_model=FX_MODEL)
    assert unresolved["status"] == "unverified"
    assert any(finding["code"] == "ST_EXPRESSION_CONDITION"
               and "cannot be checked" in finding["message"]
               for finding in unresolved["findings"])

    applicable = inspect_st_program(
        source, plc_model=FX_MODEL, project_context={"software_version": "1.10"})
    assert applicable["valid"], applicable["findings"]
