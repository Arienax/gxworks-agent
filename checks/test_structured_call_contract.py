import copy
import unittest
from unittest.mock import patch

from plc.structured_call_contract import resolve_structured_call_contract


MODEL = "Q03UDV"
MANUAL = "sh080784engn"
FX_MANUAL = "fxcpu_application_k"
FX_POSITIONING_MANUAL = "fxcpu_basic_applied_m"
FX_POSITIONING_MODELS = ("FX1S", "FX1N", "FX1NC", "FX3S", "FX3G", "FX3GC", "FX3U", "FX3UC")
FX_SUPPORTED_MODELS = (
    "FX0", "FX0N", "FX0S", "FX1N", "FX1NC", "FX1S", "FX2C", "FX2N", "FX2NC",
    "FX3G", "FX3GC", "FX3S", "FX3U", "FX3UC", "FXU",
)

# Compact fixtures copied from the exact source-verified Q03UDV owner facts in
# instruction_semantics_20261101_80.sqlite; they keep this focused check small.

def source(opcode):
    return {
        "manual_id": MANUAL,
        "manual_number": "SH080784ENG",
        "opcode": opcode,
        "pdf_pages": [125],
        "revision": "N",
        "source_ref": f"{MANUAL}:N:pages=125",
        "target_model": MODEL,
    }


def fact(identity, dimension, value, *, opcode, languages=("st",), depends_on=(), conditions=()):
    result = {
        "id": identity,
        "dimension": dimension,
        "value": value,
        "status": "source_verified",
        "verification": "source_checked",
        "scope": {"forms": [opcode], "languages": list(languages), "models": [MODEL]},
        "depends_on": list(depends_on),
        "sources": [source(opcode)],
    }
    if conditions:
        result["conditions"] = list(conditions)
    return result


def abs_facts():
    opcode = "ABS"
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability"
    return [
        fact(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {MODEL: "supported"},
            "rule": "documented_CPU_instruction_scope",
        }, opcode=opcode, languages=("st", "fbd", "ladder")),
        fact("authored:ql:st:parameters.st_signature", "parameters.st_signature", {
            "available": True, "call_kind": "function", "enable": False,
            "instance_required": False, "name": "ABS",
            "parameters": [{"direction": "input", "name": "s", "role": "source",
                            "source_aliases": ["_IN"], "symbol": "s", "type": "ANY_NUM"}],
            "returns": "ANY_NUM",
        }, opcode=opcode, depends_on=[cpu_id]),
        fact("authored:ql:literal_argument_ST_call_arguments:parameters.arity", "parameters.arity", {
            "count_kind": "ST_call_arguments", "minimum": 1, "maximum": 1,
        }, opcode=opcode, depends_on=[cpu_id]),
        fact("authored:ql:literal_argument_ST_call_arguments:parameters.order", "parameters.order", {
            "order_source": "ST_call_arguments", "symbols": ["s"],
        }, opcode=opcode, depends_on=[cpu_id]),
    ]


def abs_e_facts():
    opcode = "ABS_E"
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability"
    return [
        fact(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {MODEL: "supported"},
            "rule": "documented_CPU_instruction_scope",
        }, opcode=opcode, languages=("st", "fbd", "ladder")),
        fact("authored:ql:st:parameters.st_signature", "parameters.st_signature", {
            "available": True, "call_kind": "function", "enable": True,
            "instance_required": False, "name": "ABS_E",
            "parameters": [
                {"direction": "input", "name": "EN", "role": "enable", "symbol": "EN", "type": "BOOL"},
                {"direction": "input", "name": "s", "role": "source", "source_aliases": ["_IN"],
                 "symbol": "s", "type": "ANY_NUM"},
                {"direction": "output", "name": "d", "role": "result", "symbol": "d", "type": "ANY_NUM"},
            ],
            "returns": "BOOL",
        }, opcode=opcode, depends_on=[cpu_id]),
        fact("authored:ql:literal_argument_ST_call_arguments:parameters.arity", "parameters.arity", {
            "count_kind": "ST_call_arguments", "minimum": 3, "maximum": 3,
        }, opcode=opcode, depends_on=[cpu_id]),
        fact("authored:ql:literal_argument_ST_call_arguments:parameters.order", "parameters.order", {
            "order_source": "ST_call_arguments", "symbols": ["EN", "s", "d"],
        }, opcode=opcode, depends_on=[cpu_id]),
        # This fact is deliberately present as a negative control. It must never
        # be used to synthesize a missing formal in parameters.st_signature.
        fact("authored:ql:argument_table_st:parameters.typed_operands", "parameters.typed_operands", {
            "operands": [{"name": "EN", "type": "BOOL"}, {"name": "s", "type": "ANY_NUM"},
                         {"name": "d", "type": "ANY_NUM", "direction": "output"}],
        }, opcode=opcode),
    ]


def bcd_to_dint_facts(opcode="BCD_TO_DINT"):
    cpu_id = f"authored:ql:{opcode}:cpu_applicability:constraints.cpu_availability"
    parameters = [
        {"direction": "input", "name": "s", "role": "source", "symbol": "s",
         "type": "DWORD", "type_alternatives": ["WORD", "DWORD"],
         "documented_type_alternatives": ["WORD", "DWORD"]},
    ]
    enabled = opcode.endswith("_E")
    if enabled:
        parameters = [
            {"direction": "input", "name": "EN", "role": "enable", "symbol": "EN", "type": "BOOL"},
            *parameters,
            {"direction": "output", "name": "d", "role": "result", "symbol": "d",
             "type": "DINT", "type_alternatives": ["INT", "DINT"],
             "documented_type_alternatives": ["INT", "DINT"]},
        ]
    signature = {
        "available": True, "call_kind": "function", "enable": enabled,
        "instance_required": False, "name": opcode, "parameters": parameters,
        "returns": "BOOL" if enabled else "DINT",
    }
    order = [parameter["name"] for parameter in parameters]
    return [
        fact(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {MODEL: "supported"},
            "rule": "documented_CPU_instruction_scope",
        }, opcode=opcode, languages=("st", "fbd", "ladder")),
        fact(f"authored:ql:{opcode}:parameters.st_signature", "parameters.st_signature", signature,
             opcode=opcode, depends_on=[cpu_id]),
        fact(f"authored:ql:{opcode}:parameters.arity", "parameters.arity", {
            "count_kind": "ST_call_arguments", "minimum": len(parameters), "maximum": len(parameters),
        }, opcode=opcode, depends_on=[cpu_id]),
        fact(f"authored:ql:{opcode}:parameters.order", "parameters.order", {
            "order_source": "ST_call_arguments", "symbols": order,
        }, opcode=opcode, depends_on=[cpu_id]),
    ]


def fx_source(pages):
    page_values = list(pages)
    return {
        "manual_id": FX_MANUAL,
        "manual_number": "JY997D34801",
        "pdf_pages": page_values,
        "revision": "K",
        "source_ref": f"{FX_MANUAL}:K:pages={','.join(str(page) for page in page_values)}",
    }


def fx_fact(identity, dimension, value, *, opcode, model, pages=(160, 161), languages=("st",)):
    return {
        "id": identity,
        "dimension": dimension,
        "value": copy.deepcopy(value),
        "status": "source_verified",
        "verification": "source_checked",
        "scope": {"forms": [opcode], "languages": list(languages), "models": [model]},
        "depends_on": [],
        "sources": [fx_source(pages)],
    }


def fx_positioning_source(pages):
    page_values = list(pages)
    return {
        "manual_id": FX_POSITIONING_MANUAL,
        "manual_number": "JY997D34701",
        "pdf_pages": page_values,
        "revision": "M",
        "source_ref": f"{FX_POSITIONING_MANUAL}:M:pages={','.join(str(page) for page in page_values)}",
    }


def fx_positioning_fact(identity, dimension, value, *, opcode, model, pages=(537, 538),
                        depends_on=(), languages=("st",), conditions=()):
    result = {
        "id": identity,
        "dimension": dimension,
        "value": copy.deepcopy(value),
        "status": "source_verified",
        "verification": "source_checked",
        "scope": {"forms": [opcode], "languages": list(languages), "models": [model]},
        "depends_on": list(depends_on),
        "sources": [fx_positioning_source(pages)],
    }
    if conditions:
        result["conditions"] = copy.deepcopy(list(conditions))
    return result


def fx_positioning_facts(opcode="DDRVI", model="FX3U"):
    pages = (537, 538) if opcode == "DDRVI" else (539, 540, 541)
    signature_id = "authored:applied:st_signature@fxcpu_basic_applied_m"
    cpu_id = "authored:applied:cpu_availability@fxcpu_basic_applied_m"
    s1_range = {"binding": {"name": "S1", "call_argument_index": 1},
                "minimum": -999999, "maximum": 999999,
                "minimum_inclusive": True, "maximum_inclusive": True}
    if opcode == "DDRVI":
        s1_range["excluded_values"] = [0]
    return [
        fx_positioning_fact(cpu_id, "constraints.cpu_availability", {
            "rule": "CPU_INSTRUCTION_SUPPORT", "available": True,
            "model_states": {model: "supported"},
            "basis": "own_instruction_CPU_header_and_per_form_cells",
        }, opcode=opcode, model=model, pages=pages),
        fx_positioning_fact(signature_id, "parameters.st_signature", {
            "available": True, "call_kind": "statement", "enable": True,
            "call_form": opcode, "documented_form": f"{opcode}(EN,S1,S2,D1,D2)",
            "parameters": [
                {"name": "EN", "role": "execution_condition", "type": "BOOL", "direction": "input"},
                {"name": "S1", "type": "ANY32", "direction": "input"},
                {"name": "S2", "type": "ANY32", "direction": "input"},
                {"name": "D1", "type": "BOOL", "direction": "output"},
                {"name": "D2", "type": "BOOL", "direction": "output"},
            ],
            "returns": {"role": "not_assigned_in_documented_statement"},
            "execution_output": {"name": "ENO", "type": "BOOL", "direction": "output"},
        }, opcode=opcode, model=model, pages=pages),
        fx_positioning_fact("authored:applied:structured_st_positioning_numeric_ranges@fxcpu_basic_applied_m",
            "constraints.numeric_ranges", {
                "kind": "structured_call_argument_ranges", "representation": "st_statement_call",
                "ranges": [s1_range, {"binding": {"name": "S2", "call_argument_index": 2},
                    "unit": "Hz", "by_hardware_path": [
                        {"path": "basic_unit_transistor_output", "minimum": 10, "maximum": 100000},
                        {"path": "fx3u_high_speed_output_special_adapter", "minimum": 10, "maximum": 200000},
                    ], "path_selection_required": True}],
                "executor_capability": "not_established",
            }, opcode=opcode, model=model, pages=pages, depends_on=[signature_id]),
        fx_positioning_fact("authored:applied:structured_st_positioning_output_requirements@fxcpu_basic_applied_m",
            "constraints.documented_requirements", {
                "kind": "structured_positioning_output_path", "representation": "st_statement_call",
                "bindings": {"frequency": {"kind": "call_parameter", "name": "S2"},
                    "pulse_output": {"kind": "call_parameter", "name": "D1"},
                    "direction_signal": {"kind": "call_parameter", "name": "D2"}},
                "paths": [{"path": "basic_unit_transistor_output"},
                          {"path": "fx3u_high_speed_output_special_adapter"}],
                "current_hardware_profile_verified": False,
                "current_physical_direction_routing_verified": False,
                "current_axis_busy_verified": False,
                "hardware_validation_complete": False,
                "full_source_relation_complete": False,
                "executor_capability": "not_established",
            }, opcode=opcode, model=model, pages=pages, depends_on=[signature_id]),
    ]


QL_TRIG_ST_PAGES = {"SIN": 550, "COS": 555, "TAN": 561, "EXP": 609, "LOG": 615}


def ql_trig_source(opcode, model, page):
    return {
        "manual_id": "sh080783engp", "manual_number": "SH080783ENG",
        "opcode": opcode, "pdf_pages": [page], "revision": "P",
        "source_ref": f"sh080783engp:P:pages={page}", "target_model": model,
    }


def ql_trig_facts(opcode="SIN", model=MODEL, *, signature_conditions=(),
                  cpu_conditions=(), documented_call=None, witness_page=None):
    page = QL_TRIG_ST_PAGES[opcode]
    source = ql_trig_source(opcode, model, page)
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability:variant:2"
    signature_id = "authored:ql:argument_table_st:parameters.st_signature"
    signature = {
        "available": True, "call_kind": "instruction", "enable": True,
        "instance_required": False, "name": opcode, "source_panel_name": opcode,
        "documented_call": documented_call or f"ENO := {opcode}(EN,s,d);",
        "documented_call_source": {"manual_id": "sh080783engp",
                                    "pdf_page": page if witness_page is None else witness_page},
        "parameters": [
            {"direction": "input", "name": "EN", "role": "enable", "type": "BOOL"},
            {"direction": "input", "name": "s", "passing": "device_reference",
             "role": "math_argument", "type": "REAL"},
            {"addressing": "start_device", "direction": "output", "name": "d",
             "passing": "device_reference", "role": "result_data", "type": "REAL"},
        ],
        "returns": "BOOL",
    }
    facts = [
        {"id": cpu_id, "dimension": "constraints.cpu_availability",
         "value": {"available": True, "model_states": {model: "supported"},
                   "rule": "documented_CPU_instruction_scope"},
         "status": "source_verified", "verification": "source_checked",
         "scope": {"forms": [opcode], "languages": ["st", "fbd", "ladder"],
                   "models": [model]}, "depends_on": [], "sources": [copy.deepcopy(source)]},
        {"id": signature_id, "dimension": "parameters.st_signature", "value": signature,
         "status": "source_verified", "verification": "source_checked",
         "scope": {"forms": [opcode], "languages": ["st"], "models": [model]},
         "depends_on": [cpu_id], "sources": [copy.deepcopy(source)]},
        {"id": "authored:ql:argument_table_st:parameters.arity", "dimension": "parameters.arity",
         "value": {"count_kind": "call_arguments", "minimum": 3, "maximum": 3},
         "status": "source_verified", "verification": "source_checked",
         "scope": {"forms": [opcode], "languages": ["st"], "models": [model]},
         "depends_on": [cpu_id], "sources": [copy.deepcopy(source)]},
        {"id": "authored:ql:argument_table_st:parameters.order", "dimension": "parameters.order",
         "value": {"order_source": "literal_call_panel", "symbols": ["EN", "s", "d"]},
         "status": "source_verified", "verification": "source_checked",
         "scope": {"forms": [opcode], "languages": ["st"], "models": [model]},
         "depends_on": [cpu_id], "sources": [copy.deepcopy(source)]},
    ]
    if signature_conditions:
        facts[1]["conditions"] = copy.deepcopy(list(signature_conditions))
    if cpu_conditions:
        facts[0]["conditions"] = copy.deepcopy(list(cpu_conditions))
    return facts


def ql_bcddap_statement_facts(model=MODEL):
    """P486's concrete ST example, bound to its EN/s/d block parameters."""
    facts = ql_trig_facts(model=model)
    for item in facts:
        item["scope"]["forms"] = ["BCDDAP"]
        item["sources"] = [ql_trig_source("BCDDAP", model, 484)]
        if item["dimension"] != "constraints.cpu_availability":
            item["sources"].append(ql_trig_source("BCDDAP", model, 486))
    value = facts[1]["value"]
    value.update(name="BCDDAP", source_panel_name="BCDDAP", invocation_style="statement",
        documented_call="BCDDAP(SM400,Var_W0,Var_D0);",
        documented_call_source={"manual_id": "sh080783engp", "revision": "P", "pdf_page": 486},
        documented_parameter_panel_source={"manual_id": "sh080783engp", "revision": "P", "pdf_page": 484},
        documented_argument_bindings=[{"token": token, "parameter": parameter}
            for token, parameter in zip(("SM400", "Var_W0", "Var_D0"), ("EN", "s", "d"))],
        returns={"role": "not_assigned_in_documented_statement"},
        execution_output={"name": "ENO", "type": "BOOL", "direction": "output"})
    value["parameters"][1].update(type="ANY16", role="source")
    value["parameters"][2].update(type="STRING", role="destination", declared_capacity=6)
    facts[3]["value"]["order_source"] = "documented_example_binding"
    return facts


def ql_binhap_statement_facts(model=MODEL):
    """P483's BINHAP example with the explicitly shared BINHA panel on P481."""
    facts = ql_bcddap_statement_facts(model)
    for item in facts:
        item["scope"]["forms"] = ["BINHAP"]
        for source in item["sources"]:
            page = {484: 481, 486: 483}[source["pdf_pages"][0]]
            source.update(ql_trig_source("BINHAP", model, page))
    value = facts[1]["value"]
    value.update(name="BINHAP", source_panel_name="BINHA",
        source_group_panel_forms=["BINHA", "BINHAP", "DBINHA", "DBINHAP"],
        documented_call="BINHAP(SM400,Var_W0,Var_D0);")
    value["documented_call_source"]["pdf_page"] = 483
    value["documented_parameter_panel_source"]["pdf_page"] = 481
    return facts


def ql_string_call_facts(opcode="CONCAT", model=MODEL):
    """Compact source shape for the QL 2..28 optional STRING input family."""
    page = 159
    source_row = {
        "manual_id": "sh080784engn", "manual_number": "SH080784ENG",
        "opcode": opcode, "pdf_pages": [page], "revision": "N",
        "source_ref": f"sh080784engn:N:pages={page}", "target_model": model,
    }
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability"
    signature_id = "authored:ql:st:parameters.st_signature"
    parameters = []
    if opcode.endswith("_E"):
        parameters.append({"name": "EN", "symbol": "EN", "type": "BOOL",
                           "direction": "input", "role": "enable"})
    for index in range(1, 29):
        parameter = {"name": f"s{index}", "symbol": f"s{index}", "type": "STRING",
                     "direction": "input", "role": "source",
                     "declared_capacity_choices": [255]}
        if index >= 3:
            parameter["optional"] = True
        parameters.append(parameter)
    if opcode.endswith("_E"):
        parameters.append({"name": "d", "symbol": "d", "type": "STRING",
                           "direction": "output", "role": "destination"})
    signature = {"available": True, "call_kind": "function",
        "enable": opcode.endswith("_E"), "instance_required": False, "name": opcode,
        "parameters": parameters, "returns": "BOOL" if opcode.endswith("_E") else "STRING"}
    expected_minimum, expected_maximum = ((4, 30) if opcode.endswith("_E") else (2, 28))
    def row(identity, dimension, value, depends_on=(), languages=("st", "fbd", "ladder")):
        return {"id": identity, "dimension": dimension, "value": value,
            "status": "source_verified", "verification": "source_checked",
            "scope": {"forms": [opcode], "languages": list(languages), "models": [model]},
            "depends_on": list(depends_on), "sources": [copy.deepcopy(source_row)]}
    return [
        row(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {model: "supported"},
            "rule": "documented_CPU_instruction_scope"}),
        row(signature_id, "parameters.st_signature", signature, [cpu_id], ("st",)),
        row("authored:ql:literal_argument_ST_call_arguments:parameters.arity", "parameters.arity", {
            "count_kind": "ST_call_arguments", "minimum": expected_minimum,
            "maximum": expected_maximum}, [cpu_id], ("st",)),
        row("authored:ql:literal_argument_ST_call_arguments:parameters.order", "parameters.order", {
            "order_source": "ST_call_arguments",
            "symbols": [parameter["symbol"] for parameter in parameters]}, [cpu_id], ("st",)),
    ]


def ql_string_instruction_facts(opcode="RIGHT", model=MODEL):
    """Compact own-source instruction form for RIGHT and LEN."""
    page = 529 if opcode == "RIGHT" else 500
    source_row = {
        "manual_id": "sh080783engp", "manual_number": "SH080783ENG",
        "opcode": opcode, "pdf_pages": [page], "revision": "P",
        "source_ref": f"sh080783engp:P:pages={page}", "target_model": model,
    }
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability"
    signature_id = "authored:ql:argument_table_st:parameters.st_signature"
    parameters = [{"name": "EN", "type": "BOOL", "direction": "input", "role": "enable"},
                  {"name": "s", "type": "STRING", "direction": "input", "role": "source_data",
                   "passing": "device_reference", "addressing": "start_device"}]
    if opcode == "RIGHT":
        parameters.append({"name": "n", "type": "ANY16", "direction": "input", "role": "count"})
    parameters.append({"name": "d", "type": "ANY16" if opcode == "LEN" else "STRING",
                       "direction": "output", "role": "destination_data",
                       "passing": "device_reference", "addressing": "start_device"})
    signature = {"available": True, "call_kind": "instruction", "enable": True,
        "instance_required": False, "name": opcode, "source_panel_name": opcode,
        "documented_call": f"ENO:={opcode}({','.join(p['name'] for p in parameters)});",
        "documented_call_source": {"manual_id": "sh080783engp", "revision": "P", "pdf_page": page},
        "parameters": parameters, "returns": "BOOL"}
    def row(identity, dimension, value, depends_on=(), languages=("st",)):
        return {"id": identity, "dimension": dimension, "value": value,
            "status": "source_verified", "verification": "source_checked",
            "scope": {"forms": [opcode], "languages": list(languages), "models": [model]},
            "depends_on": list(depends_on), "sources": [copy.deepcopy(source_row)]}
    symbols = [parameter["name"] for parameter in parameters]
    return [
        row(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {model: "supported"},
            "rule": "documented_CPU_instruction_scope"}, languages=("st", "fbd", "ladder")),
        row(signature_id, "parameters.st_signature", signature, [cpu_id]),
        row("authored:ql:argument_table_st:parameters.arity", "parameters.arity", {
            "count_kind": "call_arguments", "minimum": len(parameters), "maximum": len(parameters)}, [cpu_id]),
        row("authored:ql:argument_table_st:parameters.order", "parameters.order", {
            "order_source": "literal_call_panel", "symbols": symbols}, [cpu_id]),
    ]


def ql_left_derived_instruction_facts(model=MODEL):
    """LEFT call shape derived from its grouped parameter table and ST rule."""
    panel_source = {
        "manual_id": "sh080783engp", "manual_number": "SH080783ENG", "revision": "P",
        "pdf_pages": [529, 530], "opcode": "LEFT", "target_model": model,
        "source_ref": "sh080783engp:P:pages=529,530",
    }
    st_rule_source = {
        "manual_id": "sh080782engo", "manual_number": "SH080782ENG", "revision": "O",
        "pdf_pages": [29, 83], "opcode": "LEFT", "target_model": model,
        "source_ref": "sh080782engo:O:pages=29,83",
    }
    cpu_id = "authored:ql:cpu_applicability:constraints.cpu_availability"
    signature_id = "authored:ql:argument_table_st:parameters.st_signature"
    arity_id = "authored:ql:argument_table_st:parameters.arity"
    order_id = "authored:ql:argument_table_st:parameters.order"
    parameters = [
        {"name": "EN", "type": "BOOL", "direction": "input", "role": "enable"},
        {"name": "s", "type": "STRING", "direction": "input", "role": "source_data",
         "passing": "device_reference", "addressing": "start_device"},
        {"name": "n", "type": "ANY16", "direction": "input", "role": "count"},
        {"name": "d", "type": "STRING", "direction": "output", "role": "destination_data",
         "passing": "device_reference", "addressing": "start_device"},
    ]
    signature = {
        "available": True, "call_kind": "instruction", "enable": True,
        "instance_required": False, "name": "LEFT", "source_panel_name": "RIGHT",
        "parameters": parameters, "returns": "BOOL",
        "derived_call_shape": {
            "name": "LEFT", "argument_order": ["EN", "s", "n", "d"],
            "call_kind": "instruction", "returns": "BOOL", "enable": True,
            "return_binding": "ENO",
            "derivation": "grouped_instruction_parameter_table_plus_general_st_call_rule",
        },
    }

    def row(identity, dimension, value, sources, depends_on=(), languages=("st",)):
        return {"id": identity, "dimension": dimension, "value": value,
            "status": "source_verified", "verification": "source_checked",
            "scope": {"forms": ["LEFT"], "languages": list(languages), "models": [model]},
            "depends_on": list(depends_on), "sources": copy.deepcopy(sources)}

    return [
        row(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {model: "supported"},
            "rule": "documented_CPU_instruction_scope"}, [panel_source], languages=("st", "fbd", "ladder")),
        row(signature_id, "parameters.st_signature", signature,
            [panel_source, st_rule_source], [cpu_id, arity_id, order_id]),
        row(arity_id, "parameters.arity", {
            "count_kind": "call_arguments", "minimum": 4, "maximum": 4},
            [panel_source], [cpu_id]),
        row(order_id, "parameters.order", {
            "order_source": "grouped_instruction_parameter_table_plus_general_st_call_rule",
            "symbols": ["EN", "s", "n", "d"]},
            [panel_source], [cpu_id]),
    ]


def fx_abs_facts(model="FX3U"):
    return [
        fx_fact("source:cpu_availability:fxcpu_application_k:2831",
                "constraints.cpu_availability", {
                    "code": "PLC_CPU_UNSUPPORTED",
                    "evidence_pages": [160],
                    "model_states": {name: "supported" for name in FX_SUPPORTED_MODELS},
                    "rule": "instruction_not_supported_on_selected_CPU",
                }, opcode="ABS", model=model, pages=(160,), languages=("st", "fbd")),
        fx_fact("structured:abs:st_signature@fxcpu_application_k", "parameters.st_signature", {
            "argument_order": ["_IN"],
            "arguments_are_named": False,
            "call_form_verification": "documented_arguments_verified",
            "documented_call": "ABS(_IN)",
            "enable": False,
            "parameters": [{"direction": "input", "name": "_IN",
                            "type": {"flexible_type": "ANY_NUM"},
                            "type_alternatives": ["INT", "DINT", "REAL"]}],
            "returns": {"name": "Output_label", "role": "function_result",
                        "type": {"flexible_type": "ANY_NUM"},
                        "type_alternatives": ["INT", "DINT", "REAL"]},
        }, opcode="ABS", model=model, pages=(160, 161)),
    ]


def fx_abs_e_facts(model="FX1N"):
    return [
        fx_fact("source:cpu_availability:fxcpu_application_k:2831",
                "constraints.cpu_availability", {
                    "code": "PLC_CPU_UNSUPPORTED",
                    "evidence_pages": [160],
                    "model_states": {name: "supported" for name in FX_SUPPORTED_MODELS},
                    "rule": "instruction_not_supported_on_selected_CPU",
                }, opcode="ABS_E", model=model, pages=(160,), languages=("st", "fbd")),
        fx_fact("structured:abs_e:enabled_st_signature@fxcpu_application_k",
                "parameters.st_signature", {
                    "argument_order": ["EN", "_IN", "Output_label"],
                    "arguments_are_named": False,
                    "call_form_verification": "documented_arguments_verified",
                    "documented_call": "ABS_E(EN,_IN,Output_label)",
                    "enable": True,
                    "parameters": [
                        {"direction": "input", "name": "EN", "type": {"kind": "bool"}},
                        {"direction": "input", "name": "_IN", "type": {"flexible_type": "ANY_NUM"},
                         "type_alternatives": ["INT", "DINT", "REAL"]},
                        {"direction": "output", "name": "Output_label",
                         "type": {"flexible_type": "ANY_NUM"},
                         "type_alternatives": ["INT", "DINT", "REAL"]},
                    ],
                    "returns": {"role": "ENO_execution_status", "type": {"kind": "bool"}},
                }, opcode="ABS_E", model=model, pages=(160, 161)),
    ]


def fx_k_typed_function_facts(opcode, model, *, parameter_names, parameter_types,
                              return_type, pages):
    """Small exact-shape FX K source fixture for shared IEC type binding."""
    cpu_id = f"structured:{opcode.lower()}:cpu_availability_header@{FX_MANUAL}"
    parameters = [
        {"direction": "input", "name": name, "type": copy.deepcopy(data_type)}
        for name, data_type in zip(parameter_names, parameter_types)
    ]
    signature = {
        "documented_call": f"{opcode}({','.join(parameter_names)})",
        "argument_order": list(parameter_names),
        "arguments_are_named": False,
        "call_form_verification": "documented_arguments_verified",
        "enable": False,
        "parameters": parameters,
        "returns": {"name": "Output_label", "role": "function_result",
                    "type": copy.deepcopy(return_type)},
    }
    return [
        fx_fact(cpu_id, "constraints.cpu_availability", {
            "available": True, "model_states": {model: "supported"},
        }, opcode=opcode, model=model, pages=pages),
        fx_fact(f"structured:{opcode.lower()}:st_signature@{FX_MANUAL}",
                "parameters.st_signature", signature, opcode=opcode, model=model,
                pages=pages),
    ]


def fx_int_to_real_conditional_facts(opcode="INT_TO_REAL", model="FX3G"):
    version_id = f"structured:{opcode.lower()}:limits_1@{FX_MANUAL}"
    cpu_id = f"structured:{opcode.lower()}:cpu_availability_header@{FX_MANUAL}"
    enabled = opcode.endswith("_E")
    parameters = ([{"direction": "input", "name": "EN", "type": {"kind": "bool"}},
                   {"direction": "input", "name": "_IN",
                    "type": {"kind": "int", "bits": 16, "signed": True}},
                   {"direction": "output", "name": "Output_label",
                    "type": {"kind": "real", "bits": 32}}]
                  if enabled else
                  [{"direction": "input", "name": "_IN",
                    "type": {"kind": "int", "bits": 16, "signed": True}}])
    signature = {
        "documented_call": ("INT_TO_REAL_E(EN,_IN,Output_label)" if enabled
                            else "INT_TO_REAL(_IN)"),
        "argument_order": [item["name"] for item in parameters],
        "arguments_are_named": False,
        "call_form_verification": "documented_arguments_verified",
        "enable": enabled,
        "parameters": parameters,
        "returns": ({"role": "ENO_execution_status", "type": {"kind": "bool"}}
                    if enabled else {"name": "Output_label", "role": "function_result",
                                     "type": {"kind": "real", "bits": 32}}),
    }
    facts = [
        fx_fact(version_id, "constraints.cpu_version", {
            "models": [model], "minimum_series_version": "Ver.1.10",
        }, opcode=opcode, model=model, pages=(62,)),
        fx_fact(cpu_id, "constraints.cpu_availability", {
            "model_states": {model: "conditional"},
        }, opcode=opcode, model=model, pages=(62,)),
        fx_fact(f"structured:{opcode.lower()}:{'enabled_st_signature' if enabled else 'st_signature'}@{FX_MANUAL}",
                "parameters.st_signature", signature, opcode=opcode, model=model, pages=(62,)),
    ]
    facts[1]["depends_on"] = [version_id]
    guard = {"context": "cpu_version", "minimum": "1.10"}
    facts[1]["conditions"] = [copy.deepcopy(guard)]
    facts[2]["depends_on"] = [cpu_id, version_id]
    facts[2]["conditions"] = [copy.deepcopy(guard)]
    return facts


def fx_k_add_time_facts(opcode="ADD_TIME", model="FX3U"):
    time_type = {"kind": "int", "bits": 32, "signed": True, "unit": "ms"}
    if opcode == "ADD_TIME":
        names = ["_IN1", "_IN2"]
        types = [time_type, time_type]
        result = time_type
        pages = (233,)
    else:
        names = ["EN", "_IN1", "_IN2", "Output_label"]
        types = [{"kind": "bool"}, time_type, time_type, time_type]
        result = {"kind": "bool"}
        pages = (233, 234)
    facts = fx_k_typed_function_facts(opcode, model, parameter_names=names,
        parameter_types=types, return_type=result, pages=pages)
    signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
    signature["value"]["enable"] = opcode.endswith("_E")
    if opcode.endswith("_E"):
        signature["value"]["parameters"][0]["role"] = "enable"
        signature["value"]["parameters"][-1]["direction"] = "output"
        signature["value"]["returns"]["role"] = "ENO_execution_status"
    return facts


def fx_k_concat_facts(opcode="CONCAT", model="FX3U"):
    enabled = opcode.endswith("_E")
    pin_name = "structured:concat_e:limits_1@fxcpu_application_k" if enabled else (
        "structured:concat:limits_1@fxcpu_application_k")
    signature_name = ("structured:concat_e:enabled_st_signature@fxcpu_application_k" if enabled else
                      "structured:concat:st_signature@fxcpu_application_k")
    parameters = []
    if enabled:
        parameters.append({"direction": "input", "name": "EN", "type": {"kind": "bool"}})
    parameters.extend([
        {"direction": "input", "name": "_IN", "type": {"kind": "string", "encoding": "binary"}},
        {"direction": "input", "name": "_IN", "type": {"kind": "string", "encoding": "binary"}},
    ])
    if enabled:
        parameters.append({"direction": "output", "name": "Output_label",
                           "type": {"kind": "string", "encoding": "binary"}})
    order = [parameter["name"] for parameter in parameters]
    signature = {
        "argument_order": order,
        "arguments_are_named": False,
        "call_form_verification": "documented_arguments_verified",
        "documented_call": f"{opcode}({','.join(order)})",
        "enable": enabled,
        "parameters": parameters,
        "returns": ({"role": "ENO_execution_status", "type": {"kind": "bool"}}
                    if enabled else {"name": "Output_label", "role": "function_result",
                                     "type": {"kind": "string", "encoding": "binary"}}),
    }
    return [
        fx_fact(f"structured:{opcode.lower()}:cpu_availability_header@{FX_MANUAL}",
                "constraints.cpu_availability", {
                    "available": True, "model_states": {model: "supported"},
                }, opcode=opcode, model=model, pages=(219,)),
        fx_fact(signature_name, "parameters.st_signature", signature,
                opcode=opcode, model=model, pages=(219,)),
        fx_fact(pin_name, "execution.operand_pins", {
            "edit_basis": "gxworks2_number_of_pins",
            "operand_pins": {"maximum": 28, "minimum": 2},
            "variable_arity": True,
        }, opcode=opcode, model=model, pages=(219,), languages=("st", "fbd")),
    ]


def inspect_fx(opcode, model, facts, source, *, project_context=None):
    from knowledge.instruction_compiler import refresh_reviewed_structured_call_admission
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionRegistry
    from plc.st_validation import inspect_st_program

    entry = {"opcode": opcode, "target_model": model, "call_available": False,
             "facts": copy.deepcopy(facts)}
    if not refresh_reviewed_structured_call_admission(entry, InstructionRegistry([])):
        raise AssertionError("FX structured call admission was refused: "
                             + repr(entry.get("call_admission_details")))

    def owner(name, *, cpu, vendor="mitsubishi"):
        return copy.deepcopy(entry) if name.upper() == opcode and cpu == model else None

    with patch.object(DEFAULT_INSTRUCTION_REGISTRY, "definition_entry", side_effect=owner):
        return inspect_st_program(source, plc_model=model, project_context=project_context), entry


def inspect_fx_positioning(opcode, model, facts, source, *, project_context=None):
    from knowledge.instruction_compiler import refresh_reviewed_structured_call_admission
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionRegistry
    from plc.st_validation import inspect_st_program

    entry = {"opcode": opcode, "target_model": model, "call_available": False,
             "facts": copy.deepcopy(facts)}
    if not refresh_reviewed_structured_call_admission(entry, InstructionRegistry([])):
        raise AssertionError("FX M structured call admission was refused: "
                             + repr(entry.get("call_admission_details")))

    def owner(name, *, cpu, vendor="mitsubishi"):
        return copy.deepcopy(entry) if name.upper() == opcode and cpu == model else None

    with patch.object(DEFAULT_INSTRUCTION_REGISTRY, "definition_entry", side_effect=owner):
        return inspect_st_program(source, plc_model=model, project_context=project_context), entry


class StructuredCallContractTests(unittest.TestCase):
    def test_ql_concat_optional_tail_uses_st_arity_and_order_sources(self):
        for opcode in ("CONCAT", "CONCAT_E"):
            with self.subTest(opcode=opcode):
                facts = ql_string_call_facts(opcode)
                result = resolve_structured_call_contract(facts, opcode=opcode, model=MODEL)
                self.assertTrue(result.accepted, result.refusal_reasons)
                contract = result.contract
                self.assertEqual(contract.call_kind, "function")
                self.assertEqual(contract.variadic_parameter_name, "s1")
                self.assertEqual(contract.minimum_variadic_arguments, 2)
                self.assertEqual(contract.maximum_variadic_arguments, 28)
                self.assertEqual(contract.returns, "BOOL" if opcode.endswith("_E") else "STRING")
                self.assertTrue({
                    "authored:ql:cpu_applicability:constraints.cpu_availability",
                    "authored:ql:literal_argument_ST_call_arguments:parameters.arity",
                    "authored:ql:literal_argument_ST_call_arguments:parameters.order",
                } <= set(contract.fact_ids))

                bad_arity = copy.deepcopy(facts)
                next(item for item in bad_arity if item["dimension"] == "parameters.arity")[
                    "value"]["minimum"] += 1
                refused = resolve_structured_call_contract(bad_arity, opcode=opcode, model=MODEL)
                self.assertFalse(refused.accepted)
                self.assertEqual(refused.refusal_reasons, ("optional_input_st_arity_mismatch",))

                bad_order = copy.deepcopy(facts)
                order_fact = next(item for item in bad_order if item["dimension"] == "parameters.order")
                order_fact["value"]["symbols"][1], order_fact["value"]["symbols"][2] = (
                    order_fact["value"]["symbols"][2], order_fact["value"]["symbols"][1])
                refused = resolve_structured_call_contract(bad_order, opcode=opcode, model=MODEL)
                self.assertFalse(refused.accepted)
                self.assertEqual(refused.refusal_reasons, ("optional_input_st_order_mismatch",))

    def test_fx_k_repeated_string_inputs_require_a_same_source_pin_range(self):
        for opcode in ("CONCAT", "CONCAT_E"):
            with self.subTest(opcode=opcode):
                facts = fx_k_concat_facts(opcode)
                result = resolve_structured_call_contract(facts, opcode=opcode, model="FX3U")
                self.assertTrue(result.accepted, result.refusal_reasons)
                self.assertEqual(result.contract.variadic_parameter_name, "_IN")
                self.assertEqual(result.contract.minimum_variadic_arguments, 2)
                self.assertEqual(result.contract.maximum_variadic_arguments, 28)
                self.assertIn("execution.operand_pins", {
                    fact["dimension"] for fact in result.contract.facts
                })
                self.assertEqual(result.contract.parameters[0]["name"],
                                 "EN" if opcode.endswith("_E") else "_IN")
                if opcode.endswith("_E"):
                    self.assertEqual(result.contract.parameters[-1]["name"], "Output_label")
                    self.assertEqual(result.contract.returns, "BOOL")
                else:
                    self.assertEqual(result.contract.returns, "STRING")

                missing_pin_fact = [fact for fact in facts
                                    if fact["dimension"] != "execution.operand_pins"]
                refused = resolve_structured_call_contract(missing_pin_fact,
                    opcode=opcode, model="FX3U")
                self.assertFalse(refused.accepted)
                self.assertEqual(refused.refusal_reasons,
                                 ("variadic_operand_pin_fact_missing",))

                inconsistent_range = copy.deepcopy(facts)
                pin_fact = next(fact for fact in inconsistent_range
                                if fact["dimension"] == "execution.operand_pins")
                pin_fact["value"]["operand_pins"]["minimum"] = 3
                refused = resolve_structured_call_contract(inconsistent_range,
                    opcode=opcode, model="FX3U")
                self.assertFalse(refused.accepted)
                self.assertEqual(refused.refusal_reasons,
                                 ("invalid_variadic_operand_pin_range",))

    def test_fx_k_string_and_time_source_mappings_resolve_without_type_erasure(self):
        string_facts = fx_k_typed_function_facts("INT_TO_STR", "FX3U",
            parameter_names=["_INT"],
            parameter_types=[{"kind": "int", "bits": 16, "signed": True}],
            return_type={"kind": "string", "encoding": "binary"}, pages=(66,))
        string_result = resolve_structured_call_contract(string_facts,
            opcode="INT_TO_STR", model="FX3U")
        self.assertTrue(string_result.accepted, string_result.refusal_reasons)
        self.assertEqual(string_result.contract.parameters[0]["type"], "INT")
        self.assertEqual(string_result.contract.returns, "STRING")

        time_facts = fx_k_typed_function_facts("BOOL_TO_TIME", "FX3U",
            parameter_names=["_BOOL"], parameter_types=[{"kind": "bool"}],
            return_type={"kind": "int", "bits": 32, "signed": True, "unit": "ms"},
            pages=(52,))
        time_result = resolve_structured_call_contract(time_facts,
            opcode="BOOL_TO_TIME", model="FX3U")
        self.assertTrue(time_result.accepted, time_result.refusal_reasons)
        self.assertEqual(time_result.contract.parameters[0]["type"], "BOOL")
        self.assertEqual(time_result.contract.returns, "TIME")

        # Unknown units must not collapse to a familiar DINT type.
        time_facts[1]["value"]["returns"]["type"]["unit"] = "ticks"
        unknown_time = resolve_structured_call_contract(time_facts,
            opcode="BOOL_TO_TIME", model="FX3U")
        self.assertFalse(unknown_time.accepted)
        self.assertIn("unsupported_fx_st_type", unknown_time.refusal_reasons)

        # The source identity for ADD_TIME and ADD_TIME_E keeps TIME distinct
        # from its representation-compatible DINT base type.
        for opcode in ("ADD_TIME", "ADD_TIME_E"):
            with self.subTest(opcode=opcode):
                result = resolve_structured_call_contract(fx_k_add_time_facts(opcode),
                    opcode=opcode, model="FX3U")
                self.assertTrue(result.accepted, result.refusal_reasons)
                expected_time_parameters = [parameter for parameter in result.contract.parameters
                                            if parameter["name"].casefold() != "en"]
                self.assertTrue(expected_time_parameters)
                self.assertTrue(all(parameter["type"] == "TIME"
                                    for parameter in expected_time_parameters))
                if opcode == "ADD_TIME":
                    self.assertEqual(result.contract.returns, "TIME")
                else:
                    self.assertEqual(result.contract.parameters[-1]["type"], "TIME")
                    self.assertEqual(result.contract.returns, "BOOL")

    def test_exact_abs_source_signature_and_cpu_dependency_resolve(self):
        result = resolve_structured_call_contract(abs_facts(), opcode="ABS", model=MODEL)

        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertEqual(contract.call_kind, "function")
        self.assertFalse(contract.enable)
        self.assertEqual(contract.returns, "ANY_NUM")
        self.assertEqual([(p["name"], p["direction"], p["type"]) for p in contract.parameters],
                         [("s", "input", "ANY_NUM")])
        self.assertIn("authored:ql:cpu_applicability:constraints.cpu_availability", contract.fact_ids)
        self.assertEqual(contract.sources[0]["source_ref"], f"{MANUAL}:N:pages=125")

    def test_exact_abs_e_signature_keeps_explicit_output_and_enable_formals(self):
        result = resolve_structured_call_contract(abs_e_facts(), opcode="ABS_E", model=MODEL)

        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertTrue(contract.enable)
        self.assertEqual(contract.returns, "BOOL")
        self.assertEqual([(p["name"], p["direction"], p["type"]) for p in contract.parameters], [
            ("EN", "input", "BOOL"), ("s", "input", "ANY_NUM"), ("d", "output", "ANY_NUM"),
        ])

    def test_missing_d_is_rejected_by_explicit_st_arity_order_not_inferred_from_typed_operands(self):
        facts = abs_e_facts()
        signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
        signature["value"]["parameters"].pop()

        result = resolve_structured_call_contract(facts, opcode="ABS_E", model=MODEL)

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("st_call_arity_mismatch",))

    def test_conditions_are_retained_and_input_facts_are_not_mutated(self):
        facts = abs_facts()
        signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
        signature["conditions"] = [{"context": "project_mode", "equals": "structured_text"}]
        before = copy.deepcopy(facts)

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertEqual(result.contract.conditions, ({
            "fact_id": "authored:ql:st:parameters.st_signature",
            "scope": signature["scope"],
            "conditions": [{"context": "project_mode", "equals": "structured_text"}],
        },))
        self.assertEqual(facts, before)

    def test_incomplete_or_unbound_relation_markers_block_a_signature(self):
        cases = [
            ("full_source_relation_complete", False),
            ("technical_relation_resolved", False),
            ("unresolved_items", ["result_binding"]),
            ("unbound_condition_markers", ["cpu_version"]),
        ]
        for marker, value in cases:
            with self.subTest(marker=marker):
                facts = abs_facts()
                signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
                signature["value"][marker] = value

                result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

                self.assertFalse(result.accepted)

    def test_same_scope_source_verified_cpu_denial_blocks_the_call(self):
        facts = abs_facts()
        facts.append(fact("authored:ql:cpu_applicability:unsupported", "constraints.cpu_availability", {
            "available": False, "model_states": {MODEL: "unsupported"},
            "rule": "documented_CPU_instruction_scope",
        }, opcode="ABS", languages=("st", "fbd", "ladder")))

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("conflicting_cpu_applicability:authored:ql:cpu_applicability:unsupported",))

    def test_complementary_cpu_version_denial_is_preserved_for_later_context_check(self):
        facts = abs_facts()
        positive_cpu = next(item for item in facts if item["dimension"] == "constraints.cpu_availability")
        positive_cpu["conditions"] = [{"context": "cpu_version", "minimum": "2.20"}]
        negative_id = "authored:ql:cpu_applicability:below_minimum"
        facts.append(fact(negative_id, "constraints.cpu_availability", {
            "available": False, "model_states": {MODEL: "unsupported"},
            "rule": "documented_CPU_instruction_scope",
        }, opcode="ABS", languages=("st", "fbd", "ladder"),
            conditions=[{"context": "cpu_version", "maximum_exclusive": "2.20"}]))

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertFalse(result.contract.conditions_evaluated)
        self.assertIn(negative_id, result.contract.fact_ids)
        self.assertIn({"context": "cpu_version", "maximum_exclusive": "2.20"},
                      next(item["conditions"] for item in result.contract.conditions
                           if item["fact_id"] == negative_id))

    def test_cpu_denial_with_unresolvable_guard_is_not_treated_as_complementary(self):
        facts = abs_facts()
        facts.append(fact("authored:ql:cpu_applicability:unresolved", "constraints.cpu_availability", {
            "available": False,
        }, opcode="ABS", conditions=[{"context": "cpu_version_not_resolved", "equals": True}]))

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("conflicting_cpu_applicability:authored:ql:cpu_applicability:unresolved",))

    def test_duplicate_applicable_signatures_are_refused(self):
        facts = abs_facts()
        duplicate = copy.deepcopy(next(item for item in facts if item["dimension"] == "parameters.st_signature"))
        duplicate["id"] += ":alternate"
        facts.append(duplicate)

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("ambiguous_st_signature",))

    def test_missing_cpu_availability_does_not_create_a_contract(self):
        facts = abs_facts()
        facts = [item for item in facts if item["dimension"] != "constraints.cpu_availability"]

        result = resolve_structured_call_contract(facts, opcode="ABS", model=MODEL)

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("dependency_gaps:missing_dependency",))

    def test_fx_abs_exact_scoped_signature_uses_its_k_manual_cpu_support_fact(self):
        facts = fx_abs_facts()

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U")

        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertEqual(contract.parameters[0]["symbol"], "_IN")
        self.assertEqual(contract.parameters[0]["type"], "ANY_NUM")
        self.assertEqual(contract.parameters[0]["type_alternatives"], ["INT", "DINT", "REAL"])
        self.assertEqual(contract.return_type_alternatives, ("INT", "DINT", "REAL"))
        self.assertIn("source:cpu_availability:fxcpu_application_k:2831", contract.fact_ids)
        self.assertFalse(contract.conditions_evaluated)

    def test_fx_signature_accepts_the_authored_manual_revision_page_source_shape(self):
        facts = fx_abs_facts()
        for item in facts:
            for source in item["sources"]:
                source.pop("source_ref")

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U")

        self.assertTrue(result.accepted, result.refusal_reasons)

    def test_fx_abs_e_keeps_explicit_enable_and_output_bindings(self):
        result = resolve_structured_call_contract(fx_abs_e_facts(), opcode="ABS_E", model="FX1N")

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertTrue(result.contract.enable)
        self.assertEqual(result.contract.returns, "BOOL")
        self.assertEqual([(item["name"], item["direction"], item["type"])
                          for item in result.contract.parameters], [
                              ("EN", "input", "BOOL"),
                              ("_IN", "input", "ANY_NUM"),
                              ("Output_label", "output", "ANY_NUM"),
                          ])

    def test_fx_abs_and_abs_e_keep_the_source_cpu_scope_for_all_listed_fx_models(self):
        for model in FX_SUPPORTED_MODELS:
            with self.subTest(opcode="ABS", model=model):
                result = resolve_structured_call_contract(fx_abs_facts(model), opcode="ABS", model=model)
                self.assertTrue(result.accepted, result.refusal_reasons)
            with self.subTest(opcode="ABS_E", model=model):
                result = resolve_structured_call_contract(fx_abs_e_facts(model), opcode="ABS_E", model=model)
                self.assertTrue(result.accepted, result.refusal_reasons)

    def test_fx_signature_without_same_scope_cpu_support_fact_is_refused(self):
        facts = [item for item in fx_abs_facts() if item["dimension"] != "constraints.cpu_availability"]

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U")

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("fx_cpu_availability_missing_or_ambiguous",))

    def test_fx_cpu_availability_requires_the_exact_model_to_be_supported(self):
        facts = fx_abs_facts()
        cpu = next(item for item in facts if item["dimension"] == "constraints.cpu_availability")
        cpu["value"]["model_states"]["FX3U"] = "unsupported"

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U")

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("cpu_model_state_not_supported",))

    def test_fx_conditional_cpu_availability_requires_its_exact_source_version_guard(self):
        for model in ("FX3G", "FX3GC"):
            for opcode in ("INT_TO_REAL", "INT_TO_REAL_E"):
                with self.subTest(opcode=opcode, model=model):
                    facts = fx_int_to_real_conditional_facts(opcode, model)
                    result = resolve_structured_call_contract(facts, opcode=opcode, model=model)
                    self.assertTrue(result.accepted, result.refusal_reasons)
                    cpu = next(item for item in result.contract.facts
                               if item["dimension"] == "constraints.cpu_availability")
                    self.assertEqual(cpu["value"]["model_states"], {model: "conditional"})
                    self.assertNotIn("available", cpu["value"])
                    self.assertIn({"context": "cpu_version", "minimum": "1.10"}, cpu["conditions"])

    def test_fx_conditional_header_uses_its_source_bound_footnote_refinement(self):
        for model in ("FX3G", "FX3GC"):
            for opcode in ("INT_TO_REAL", "INT_TO_REAL_E"):
                with self.subTest(opcode=opcode, model=model):
                    facts = fx_int_to_real_conditional_facts(opcode, model)
                    header = fx_fact("source:cpu_availability:fxcpu_application_k:2736",
                        "constraints.cpu_availability", {"model_states": {model: "conditional"}},
                        opcode=opcode, model=model, pages=(62,))
                    original_header = copy.deepcopy(header)
                    facts.append(header)
                    result = resolve_structured_call_contract(facts, opcode=opcode, model=model)
                    self.assertTrue(result.accepted, result.refusal_reasons)
                    self.assertNotIn(header['id'], result.contract.fact_ids)
                    self.assertIn(header, facts)
                    self.assertEqual(header, original_header)
                    self.assertTrue(any({'context': 'cpu_version', 'minimum': '1.10'}
                                        in item.get('conditions', ()) for item in result.contract.facts))

        for guard in ({'context': 'cpu_version', 'minimum': '1.09'},
                      {'context': 'cpu_version_not_resolved'}):
            with self.subTest(raw_header_guard=guard):
                facts = fx_int_to_real_conditional_facts()
                header = fx_fact('source:cpu_availability:fxcpu_application_k:2736',
                    'constraints.cpu_availability', {'model_states': {'FX3G': 'conditional'}},
                    opcode='INT_TO_REAL', model='FX3G', pages=(62,))
                header['conditions'] = [guard]
                facts.append(header)
                self.assertFalse(resolve_structured_call_contract(
                    facts, opcode='INT_TO_REAL', model='FX3G').accepted)

    def test_fx_conditional_cpu_availability_rejects_missing_or_mismatched_guard(self):
        facts = fx_int_to_real_conditional_facts()
        cpu = next(item for item in facts if item["dimension"] == "constraints.cpu_availability")
        cpu["depends_on"] = []
        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_conditional_cpu_version_fact_missing_or_ambiguous",))

        facts = fx_int_to_real_conditional_facts()
        cpu = next(item for item in facts if item["dimension"] == "constraints.cpu_availability")
        cpu["conditions"] = [{"context": "cpu_version", "minimum": "1.00"}]
        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_conditional_cpu_version_guard_mismatch",))

        facts = fx_int_to_real_conditional_facts()
        version = next(item for item in facts if item["dimension"] == "constraints.cpu_version")
        version["value"]["minimum_series_version"] = "Ver.1.11"
        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_conditional_cpu_version_guard_mismatch",))

        facts = fx_int_to_real_conditional_facts()
        cpu = next(item for item in facts if item["dimension"] == "constraints.cpu_availability")
        cpu["conditions"] = [{"context": "cpu_version_not_resolved",
                              "basis": "instruction_header_icon"}]
        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_conditional_cpu_version_guard_unresolved",))

        facts = fx_int_to_real_conditional_facts()
        signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
        signature["conditions"] = [{"context": "cpu_version_not_resolved",
                                    "basis": "instruction_header_icon"}]
        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_conditional_cpu_version_guard_unresolved",))

    def test_fx_conditional_cpu_availability_rejects_an_overlapping_cpu_denial(self):
        facts = fx_int_to_real_conditional_facts()
        facts.append(fx_fact("structured:int_to_real:cpu_denial@fxcpu_application_k",
            "constraints.cpu_availability", {
                "available": False, "model_states": {"FX3G": "unsupported"},
            }, opcode="INT_TO_REAL", model="FX3G", pages=(62,)))

        result = resolve_structured_call_contract(facts, opcode="INT_TO_REAL", model="FX3G")

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_cpu_availability_conflicting_claims",))

    def test_fx_equivalent_cpu_support_facts_preserve_all_dependency_closures(self):
        facts = fx_abs_facts()
        original_cpu = next(item for item in facts
                            if item["dimension"] == "constraints.cpu_availability")
        conditions = [
            {"context": "cpu_version", "minimum": "1.10"},
            {"context": "cpu_version", "maximum": "3.00"},
        ]
        original_cpu["conditions"] = copy.deepcopy(conditions)
        original_cpu["depends_on"] = ["fx:cpu_support_witness:original"]
        duplicate_cpu = copy.deepcopy(original_cpu)
        duplicate_cpu["id"] = "source:cpu_availability:fxcpu_application_k:equivalent"
        duplicate_cpu["conditions"] = list(reversed(conditions))
        duplicate_cpu["depends_on"] = ["fx:cpu_support_witness:duplicate"]
        facts.extend([
            duplicate_cpu,
            fx_fact("fx:cpu_support_witness:original", "identity.form",
                    {"documented_form": "ABS"}, opcode="ABS", model="FX3U"),
            fx_fact("fx:cpu_support_witness:duplicate", "identity.form",
                    {"documented_form": "ABS"}, opcode="ABS", model="FX3U"),
        ])

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U",
                                                  version="2.20")

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertTrue({
            original_cpu["id"], duplicate_cpu["id"],
            "fx:cpu_support_witness:original", "fx:cpu_support_witness:duplicate",
        } <= set(result.contract.fact_ids))
        support_conditions = [row for row in result.contract.conditions
                              if row["fact_id"] in {original_cpu["id"], duplicate_cpu["id"]}]
        self.assertEqual({row["fact_id"] for row in support_conditions},
                         {original_cpu["id"], duplicate_cpu["id"]})
        retained_by_id = {row["fact_id"]: row["conditions"] for row in support_conditions}
        self.assertEqual(retained_by_id[original_cpu["id"]], conditions)
        self.assertEqual(retained_by_id[duplicate_cpu["id"]], list(reversed(conditions)))

    def test_fx_duplicate_cpu_support_with_different_conditions_is_refused(self):
        facts = fx_abs_facts()
        original_cpu = next(item for item in facts
                            if item["dimension"] == "constraints.cpu_availability")
        original_cpu["conditions"] = [{"context": "cpu_version", "minimum": "1.10"}]
        duplicate_cpu = copy.deepcopy(original_cpu)
        duplicate_cpu["id"] += ":different-condition"
        duplicate_cpu["conditions"] = [{"context": "cpu_version", "minimum": "1.20"}]
        facts.append(duplicate_cpu)

        result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U",
                                                  version="2.20")

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_cpu_availability_conditions_differ",))

    def test_fx_duplicate_cpu_support_conflict_or_uncertain_state_is_refused(self):
        for state in ("unsupported", "conditional"):
            with self.subTest(state=state):
                facts = fx_abs_facts()
                duplicate_cpu = copy.deepcopy(next(item for item in facts
                                                   if item["dimension"] == "constraints.cpu_availability"))
                duplicate_cpu["id"] += ":conflicting-state"
                duplicate_cpu["value"]["model_states"]["FX3U"] = state
                facts.append(duplicate_cpu)

                result = resolve_structured_call_contract(facts, opcode="ABS", model="FX3U")

                self.assertFalse(result.accepted)
                self.assertEqual(result.refusal_reasons,
                                 ("fx_cpu_availability_conflicting_claims",))

    def test_fx_signature_is_not_reused_for_a_different_cpu_scope(self):
        result = resolve_structured_call_contract(fx_abs_facts(), opcode="ABS", model="FX1N")

        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("no_applicable_st_signature",))

    def test_fx_m_statement_contract_binds_only_as_a_statement_and_preserves_eno(self):
        declarations = ('VAR\n  enabled : BOOL;\n  distance : DINT;\n  speed : DINT;\n'
                        '  pulse : BOOL;\n  direction : BOOL;\nEND_VAR\n')
        source = declarations + 'DDRVI(enabled, distance, speed, pulse, direction);'
        report, entry = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(), source)

        self.assertIn(report['status'], {'passed', 'unverified'}, report['findings'])
        call, = report['calls']
        self.assertEqual(call['kind'], 'structured_statement')
        self.assertNotIn('return_type', call)
        self.assertEqual(call['return_role'], 'not_assigned_in_documented_statement')
        self.assertEqual(call['execution_outputs'][0]['name'], 'ENO')
        self.assertEqual(call['parameters'], ['EN', 'S1', 'S2', 'D1', 'D2'])
        self.assertTrue(entry['call_available'])

        named, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'DDRVI(EN := enabled, S1 := distance, S2 := speed, '
            'D1 => pulse, D2 => direction);')
        self.assertIn(named['status'], {'passed', 'unverified'}, named['findings'])
        self.assertEqual(named['calls'][0]['parameters'], ['EN', 'S1', 'S2', 'D1', 'D2'])

        assignment, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'enabled := DDRVI(enabled, distance, speed, pulse, direction);')
        self.assertEqual(assignment['status'], 'failed')
        self.assertIn('ST_CALL_CONTEXT', {finding['code'] for finding in assignment['findings']})
        self.assertFalse(assignment['calls'])

    def test_fx_m_statement_enforces_any32_and_output_binding(self):
        declarations = ('VAR\n  enabled : BOOL;\n  distance : DINT;\n  speed : DWORD;\n'
                        '  pulse : BOOL;\n  direction : BOOL;\n  real_speed : REAL;\nEND_VAR\n')
        mixed, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'DDRVI(enabled, distance, speed, pulse, direction);')
        self.assertIn(mixed['status'], {'passed', 'unverified'}, mixed['findings'])
        self.assertEqual(mixed['calls'][0]['kind'], 'structured_statement')
        self.assertFalse(any(finding['code'] == 'ST_TYPE' for finding in mixed['findings']))

        wrong_width, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'DDRVI(enabled, distance, real_speed, pulse, direction);')
        self.assertEqual(wrong_width['status'], 'failed')
        self.assertTrue(any(finding['code'] == 'ST_TYPE' and 'ANY32' in finding['message']
                            for finding in wrong_width['findings']))

        no_output, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'DDRVI(enabled, distance, distance, pulse);')
        self.assertEqual(no_output['status'], 'failed')
        self.assertIn('ST_CALL_ARGUMENT', {finding['code'] for finding in no_output['findings']})

        invalid_output, _ = inspect_fx_positioning('DDRVI', 'FX3U', fx_positioning_facts(),
            declarations + 'DDRVI(enabled, distance, distance, pulse OR FALSE, direction);')
        self.assertEqual(invalid_output['status'], 'failed')
        self.assertIn('ST_WRITE_TARGET', {finding['code'] for finding in invalid_output['findings']})

    def test_fx_m_statement_contract_preserves_unassigned_return_eno_and_source_constraints(self):
        facts = fx_positioning_facts("DDRVI", "FX3U")
        result = resolve_structured_call_contract(facts, opcode="DDRVI", model="FX3U")

        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertEqual(contract.call_kind, "statement")
        self.assertIsNone(contract.returns)
        self.assertEqual(contract.return_role, "not_assigned_in_documented_statement")
        self.assertEqual(contract.documented_form, "DDRVI(EN,S1,S2,D1,D2)")
        self.assertTrue(contract.positional_arguments_verified)
        self.assertEqual([(item["name"], item["direction"], item["type"])
                          for item in contract.parameters], [
                              ("EN", "input", "BOOL"), ("S1", "input", "ANY32"),
                              ("S2", "input", "ANY32"), ("D1", "output", "BOOL"),
                              ("D2", "output", "BOOL"),
                          ])
        self.assertEqual(contract.execution_outputs[0]["name"], "ENO")
        self.assertEqual(contract.execution_outputs[0]["type"], "BOOL")
        self.assertIn("authored:applied:structured_st_positioning_numeric_ranges@fxcpu_basic_applied_m",
                      contract.fact_ids)
        output_fact = next(fact for fact in contract.facts
                           if fact["dimension"] == "constraints.documented_requirements")
        self.assertFalse(output_fact["value"]["full_source_relation_complete"])
        self.assertFalse(contract.conditions_evaluated)

    def test_fx_m_statement_contracts_require_all_exact_cpu_members_and_keep_ddrva_zero(self):
        for model in FX_POSITIONING_MODELS:
            for opcode in ("DDRVI", "DDRVA"):
                with self.subTest(model=model, opcode=opcode):
                    result = resolve_structured_call_contract(
                        fx_positioning_facts(opcode, model), opcode=opcode, model=model)
                    self.assertTrue(result.accepted, result.refusal_reasons)
                    ranges = next(fact["value"]["ranges"] for fact in result.contract.facts
                                  if fact["dimension"] == "constraints.numeric_ranges")
                    s1 = ranges[0]
                    self.assertEqual(s1.get("excluded_values"), [0] if opcode == "DDRVI" else None)

    def test_fx_m_statement_rejects_missing_hardware_or_range_contract(self):
        for dimension in ("constraints.numeric_ranges", "constraints.documented_requirements"):
            with self.subTest(dimension=dimension):
                facts = [item for item in fx_positioning_facts()
                         if item["dimension"] != dimension]
                result = resolve_structured_call_contract(facts, opcode="DDRVI", model="FX3U")
                self.assertFalse(result.accepted)
                self.assertEqual(result.refusal_reasons,
                    ("fx_positioning_constraint_missing_or_ambiguous:" + dimension,))

    def test_fx_m_statement_never_infers_a_return_or_missing_eno(self):
        for mutation in ("typed_return", "missing_execution_output"):
            with self.subTest(mutation=mutation):
                facts = fx_positioning_facts()
                signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
                if mutation == "typed_return":
                    signature["value"]["returns"]["type"] = "BOOL"
                else:
                    signature["value"].pop("execution_output")
                result = resolve_structured_call_contract(facts, opcode="DDRVI", model="FX3U")
                self.assertFalse(result.accepted)
                self.assertIn("execution_output" if mutation == "missing_execution_output" else "return",
                              result.refusal_reasons[0])

    def test_fx_m_modern_source_shape_keeps_version_condition_without_synthesizing_form(self):
        facts = fx_positioning_facts("DDRVI", "FX3S")
        signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
        signature["value"].pop("documented_form")
        signature["value"]["argument_order"] = ["EN", "S1", "S2", "D1", "D2"]
        signature["conditions"] = [{"context": "cpu_version", "minimum_by_model": {
            "FX3S": "1.00", "FX3G": "1.00", "FX3GC": "1.40", "FX3U": "2.20", "FX3UC": "1.00"}}]

        result = resolve_structured_call_contract(facts, opcode="DDRVI", model="FX3S")

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertIsNone(result.contract.documented_form)
        self.assertTrue(result.contract.positional_arguments_verified)
        self.assertTrue(any(row["fact_id"] == signature["id"]
                            and row["conditions"] == signature["conditions"]
                            for row in result.contract.conditions))

    def test_fx_m_statement_requires_explicit_st_cpu_availability(self):
        facts = [item for item in fx_positioning_facts()
                 if item["dimension"] != "constraints.cpu_availability"]
        result = resolve_structured_call_contract(facts, opcode="DDRVI", model="FX3U")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons,
                         ("fx_positioning_cpu_availability_missing_or_ambiguous",))

        denied = fx_positioning_facts()
        cpu = next(item for item in denied if item["dimension"] == "constraints.cpu_availability")
        cpu["value"]["available"] = False
        cpu["value"]["model_states"]["FX3U"] = "unsupported"
        result = resolve_structured_call_contract(denied, opcode="DDRVI", model="FX3U")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("cpu_model_state_not_supported",))

    def test_fx_m_statement_does_not_admit_an_unimplemented_applied_family(self):
        facts = fx_positioning_facts("OTHER", "FXU")
        signature = next(item for item in facts if item["dimension"] == "parameters.st_signature")
        # Even a normalized-looking name must not bypass the family-specific
        # source and hardware requirements through the generic ST branch.
        signature["value"]["name"] = "OTHER"
        result = resolve_structured_call_contract(facts, opcode="OTHER", model="FXU")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("fx_applied_statement_form_not_supported",))


class QLInstructionCallContractTests(unittest.TestCase):
    def test_ql_grouped_statement_panel_requires_explicit_forms_and_exact_sources(self):
        facts = ql_binhap_statement_facts()
        result = resolve_structured_call_contract(facts, opcode="BINHAP", model=MODEL)
        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertEqual(result.contract.call_kind, "statement")
        self.assertIsNone(result.contract.returns)
        self.assertEqual(result.contract.execution_outputs[0]["name"], "ENO")
        self.assertEqual(facts[1]["value"]["call_kind"], "instruction")
        cases = [
            ("missing_group", lambda f: f[1]["value"].pop("source_group_panel_forms")),
            ("missing_form", lambda f: f[1]["value"].update(source_group_panel_forms=["BINHA"])),
            ("missing_panel", lambda f: f[1]["value"].update(source_group_panel_forms=["BINHAP"])),
            ("string_group", lambda f: f[1]["value"].update(source_group_panel_forms="BINHA BINHAP")),
            ("missing_locator", lambda f: f[1]["value"].pop("documented_parameter_panel_source")),
            ("wrong_page", lambda f: f[1]["value"]["documented_parameter_panel_source"].update(pdf_page=482)),
            ("wrong_revision", lambda f: f[1]["value"]["documented_parameter_panel_source"].update(revision="X")),
            ("wrong_owner", lambda f: f[1]["sources"][0].update(opcode="BINHA")),
            ("wrong_model", lambda f: f[1]["sources"][0].update(target_model="L02")),
            ("returning_call", lambda f: f[1]["value"].update(invocation_style="returning",
                returns="BOOL", documented_call="ENO:=BINHAP(EN,s,d);")),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                changed = copy.deepcopy(facts)
                mutate(changed)
                result = resolve_structured_call_contract(changed, opcode="BINHAP", model=MODEL)
                self.assertFalse(result.accepted, name)

    def test_ql_statement_binds_own_example_without_inventing_a_bool_return(self):
        result = resolve_structured_call_contract(
            ql_bcddap_statement_facts(), opcode="BCDDAP", model=MODEL)
        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertEqual(contract.call_kind, "statement")
        self.assertIsNone(contract.returns)
        self.assertEqual(contract.return_role, "not_assigned_in_documented_statement")
        self.assertEqual(contract.documented_form, "BCDDAP(SM400,Var_W0,Var_D0);")
        self.assertEqual(contract.execution_outputs[0]["name"], "ENO")
        self.assertEqual([parameter["name"] for parameter in contract.parameters], ["EN", "s", "d"])

    def test_ql_statement_rejects_unbound_examples_and_inferred_return_shapes(self):
        cases = [
            (lambda facts: facts[1]["value"].pop("documented_argument_bindings"),
             "instruction_statement_argument_binding_mismatch"),
            (lambda facts: facts[1]["value"]["documented_argument_bindings"][1].update(token="D0"),
             "instruction_statement_argument_binding_mismatch"),
            (lambda facts: facts[1]["value"]["documented_argument_bindings"][1].update(parameter="d"),
             "instruction_statement_argument_binding_mismatch"),
            (lambda facts: facts[1]["value"].update(documented_call="BCDDA(SM400,Var_W0,Var_D0);"),
             "instruction_call_witness_mismatch"),
            (lambda facts: facts[1]["value"].update(returns="BOOL"),
             "statement_return_must_remain_unassigned"),
            (lambda facts: facts[1]["value"]["execution_output"].update(type="WORD"),
             "instruction_statement_execution_output_mismatch"),
            (lambda facts: facts[1]["value"]["documented_call_source"].update(pdf_page=487),
             "instruction_call_witness_source_mismatch"),
            (lambda facts: facts[1]["value"].update(derived_call_shape={}),
             "instruction_statement_literal_witness_required"),
            (lambda facts: facts[1]["value"].pop("documented_parameter_panel_source"),
             "instruction_statement_parameter_panel_missing"),
            (lambda facts: facts[1]["value"]["documented_parameter_panel_source"].update(pdf_page=485),
             "instruction_statement_parameter_panel_mismatch"),
            (lambda facts: facts[0].update(sources=[ql_trig_source("BCDDAP", MODEL, 485)]),
             "instruction_call_cpu_source_mismatch"),
            (lambda facts: facts[3].update(sources=[ql_trig_source("BCDDAP", MODEL, 486)]),
             "instruction_statement_parameter_panel_mismatch:authored:ql:argument_table_st:parameters.order"),
        ]
        for mutate, reason in cases:
            with self.subTest(reason=reason):
                facts = ql_bcddap_statement_facts()
                mutate(facts)
                result = resolve_structured_call_contract(facts, opcode="BCDDAP", model=MODEL)
                self.assertFalse(result.accepted)
                self.assertEqual(result.refusal_reasons, (reason,))

    def test_ql_left_uses_derived_group_table_shape_without_faking_a_literal_witness(self):
        facts = ql_left_derived_instruction_facts()
        result = resolve_structured_call_contract(facts, opcode="LEFT", model=MODEL)
        self.assertTrue(result.accepted, result.refusal_reasons)
        contract = result.contract
        self.assertEqual(contract.call_kind, "instruction")
        self.assertEqual(contract.returns, "BOOL")
        self.assertIsNone(contract.documented_form)
        self.assertTrue(contract.positional_arguments_verified)
        self.assertEqual([parameter["name"] for parameter in contract.parameters],
                         ["EN", "s", "n", "d"])
        self.assertEqual({source["manual_id"] for source in contract.sources},
                         {"sh080783engp", "sh080782engo"})

        cases = [
            ("missing_language_source", lambda values: values[1]["sources"].pop(),
             "derived_instruction_call_supporting_sources_incomplete"),
            ("order_mismatch", lambda values: values[1]["value"]["derived_call_shape"].update(
                argument_order=["EN", "s", "d", "n"]),
             "derived_instruction_call_order_mismatch"),
            ("literal_witness_claim", lambda values: values[1]["value"].update(
                documented_call="ENO:=LEFT(EN,s,n,d);"),
             "derived_instruction_call_must_not_claim_literal_witness"),
            ("wrong_arity_fact", lambda values: values[2]["value"].update(maximum=3),
             "derived_instruction_arity_mismatch"),
            ("wrong_order_fact", lambda values: values[3]["value"].update(
                symbols=["EN", "s", "d", "n"]), "derived_instruction_order_mismatch"),
            ("literal_order_claim", lambda values: values[3]["value"].update(
                order_source="literal_call_panel"),
             "derived_instruction_parameters_order_missing"),
            ("wrong_direction", lambda values: values[1]["value"]["parameters"][3].update(
                direction="input"), "instruction_call_output_parameter_missing"),
            ("no_cpu_dependency", lambda values: values[1].update(depends_on=[]),
             "derived_instruction_call_cpu_dependency_missing"),
            ("missing_order_dependency", lambda values: values[1]["depends_on"].remove(
                "authored:ql:argument_table_st:parameters.order"),
             "derived_instruction_parameters_order_dependency_missing"),
        ]
        for label, mutate, reason in cases:
            with self.subTest(label=label):
                changed = ql_left_derived_instruction_facts()
                mutate(changed)
                refused = resolve_structured_call_contract(changed, opcode="LEFT", model=MODEL)
                self.assertFalse(refused.accepted)
                self.assertEqual(refused.refusal_reasons, (reason,))

    def test_ql_string_instructions_use_their_complete_owner_st_signature(self):
        for opcode, expected_types, expected_order in (
                ("RIGHT", ["BOOL", "STRING", "ANY16", "STRING"], ["EN", "s", "n", "d"]),
                ("LEN", ["BOOL", "STRING", "ANY16"], ["EN", "s", "d"])):
            with self.subTest(opcode=opcode):
                result = resolve_structured_call_contract(
                    ql_string_instruction_facts(opcode), opcode=opcode, model=MODEL)
                self.assertTrue(result.accepted, result.refusal_reasons)
                contract = result.contract
                self.assertEqual(contract.call_kind, "instruction")
                self.assertEqual(contract.returns, "BOOL")
                self.assertTrue(contract.positional_arguments_verified)
                self.assertEqual([parameter["name"] for parameter in contract.parameters], expected_order)
                self.assertEqual([parameter["type"] for parameter in contract.parameters], expected_types)
                self.assertEqual(contract.documented_form,
                                 f"ENO:={opcode}({','.join(expected_order)});")
                self.assertIn("authored:ql:argument_table_st:parameters.arity", contract.fact_ids)
                self.assertIn("authored:ql:argument_table_st:parameters.order", contract.fact_ids)

        wrong_source = ql_string_instruction_facts("RIGHT")
        signature = next(item for item in wrong_source if item["dimension"] == "parameters.st_signature")
        signature["value"]["documented_call_source"]["pdf_page"] = 528
        refused = resolve_structured_call_contract(wrong_source, opcode="RIGHT", model=MODEL)
        self.assertFalse(refused.accepted)
        self.assertEqual(refused.refusal_reasons, ("instruction_call_witness_source_mismatch",))

        wrong_type = ql_string_instruction_facts("LEN")
        signature = next(item for item in wrong_type if item["dimension"] == "parameters.st_signature")
        signature["value"]["parameters"][1]["type"] = "POINTER"
        refused = resolve_structured_call_contract(wrong_type, opcode="LEN", model=MODEL)
        self.assertFalse(refused.accepted)
        self.assertEqual(refused.refusal_reasons,
                         ("instruction_call_parameter_contract_mismatch:s",))

    def test_exact_ql_instruction_witness_keeps_bool_return_and_output_parameter(self):
        for opcode, page in QL_TRIG_ST_PAGES.items():
            with self.subTest(opcode=opcode):
                result = resolve_structured_call_contract(
                    ql_trig_facts(opcode), opcode=opcode, model=MODEL)
                self.assertTrue(result.accepted, result.refusal_reasons)
                contract = result.contract
                self.assertEqual(contract.call_kind, "instruction")
                self.assertEqual(contract.returns, "BOOL")
                self.assertEqual(contract.documented_form, f"ENO := {opcode}(EN,s,d);")
                self.assertTrue(contract.positional_arguments_verified)
                self.assertEqual([(p["name"], p["direction"], p["type"])
                                  for p in contract.parameters], [
                                      ("EN", "input", "BOOL"),
                                      ("s", "input", "REAL"),
                                      ("d", "output", "REAL"),
                                  ])
                self.assertIn("authored:ql:cpu_applicability:constraints.cpu_availability:variant:2",
                              contract.fact_ids)
                self.assertIn("authored:ql:argument_table_st:parameters.arity", contract.fact_ids)
                self.assertIn("authored:ql:argument_table_st:parameters.order", contract.fact_ids)
                self.assertEqual(contract.sources[0]["pdf_pages"], [page])
                self.assertFalse(contract.conditions_evaluated)

    def test_ql_instruction_requires_exact_panel_witness_and_bound_manual_page(self):
        cases = [
            ("wrong_assignment", lambda facts: facts[1]["value"].update(
                documented_call="EN := SIN(EN,s,d);"), "instruction_call_witness_mismatch"),
            ("wrong_witness_page", lambda facts: facts[1]["value"].update(
                documented_call_source={"manual_id": "sh080783engp", "pdf_page": 551}),
             "instruction_call_witness_source_mismatch"),
            ("unsupported_output_type", lambda facts: facts[1]["value"]["parameters"][2].update(
                type="UNKNOWN_TYPE"), "instruction_call_parameter_contract_mismatch:d"),
            ("wrong_return_type", lambda facts: facts[1]["value"].update(
                returns="REAL"), "instruction_call_return_contract_incomplete"),
        ]
        for label, mutate, expected_reason in cases:
            with self.subTest(label=label):
                facts = ql_trig_facts()
                mutate(facts)
                result = resolve_structured_call_contract(facts, opcode="SIN", model=MODEL)
                self.assertFalse(result.accepted)
                self.assertEqual(result.refusal_reasons, (expected_reason,))

        facts = ql_trig_facts()
        facts[1]["sources"][0]["pdf_pages"] = [551]
        facts[1]["sources"][0]["source_ref"] = "sh080783engp:P:pages=551"
        result = resolve_structured_call_contract(facts, opcode="SIN", model=MODEL)
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("instruction_call_witness_source_mismatch",))

        wrong_revision = ql_trig_facts()
        signature_source = wrong_revision[1]["sources"][0]
        signature_source["revision"] = "R"
        signature_source["source_ref"] = "sh080783engp:R:pages=550"
        result = resolve_structured_call_contract(wrong_revision, opcode="SIN", model=MODEL)
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("instruction_call_cpu_source_mismatch",))

        for index, mutate, expected in (
                (2, lambda fact: fact["value"].update(maximum=4), "instruction_call_arity_mismatch"),
                (3, lambda fact: fact["value"].update(symbols=["EN", "d", "s"]),
                 "instruction_call_order_mismatch")):
            with self.subTest(expected=expected):
                invalid = ql_trig_facts()
                mutate(invalid[index])
                result = resolve_structured_call_contract(invalid, opcode="SIN", model=MODEL)
                self.assertFalse(result.accepted)
                self.assertEqual(result.refusal_reasons, (expected,))

    def test_ql_instruction_keeps_owner_conditions_and_refuses_duplicate_signatures(self):
        serial = {"kind": "serial_number_minimum", "digits": 5, "minimum": "04122"}
        facts = ql_trig_facts("SIN", "Q00U", signature_conditions=[serial], cpu_conditions=[serial])
        result = resolve_structured_call_contract(facts, opcode="SIN", model="Q00U")
        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertTrue(any(row["fact_id"] == facts[1]["id"] and serial in row["conditions"]
                            for row in result.contract.conditions))
        self.assertTrue(any(row["fact_id"] == facts[0]["id"] and serial in row["conditions"]
                            for row in result.contract.conditions))

        duplicate = copy.deepcopy(facts[1])
        duplicate["id"] = "family:SIN:ql:transcendental:st_signature"
        facts.append(duplicate)
        result = resolve_structured_call_contract(facts, opcode="SIN", model="Q00U")
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("ambiguous_st_signature",))

    def test_source_instruction_call_shape_is_reusable_for_another_exact_owner(self):
        facts = ql_trig_facts("LOG")
        manual_id, revision, page = "sample_manual", "R", 777
        for item in facts:
            item["scope"]["forms"] = ["OWN_FN"]
            item["sources"][0].update(manual_id=manual_id, manual_number="SAMPLE",
                opcode="OWN_FN", pdf_pages=[page], revision=revision,
                source_ref=f"{manual_id}:{revision}:pages={page}")
        facts[1]["value"].update(name="OWN_FN", source_panel_name="OWN_FN",
            documented_call="ENO:=OWN_FN(EN,s,d);",
            documented_call_source={"manual_id": manual_id, "revision": revision,
                                    "pdf_page": page})

        result = resolve_structured_call_contract(facts, opcode="OWN_FN", model=MODEL)

        self.assertTrue(result.accepted, result.refusal_reasons)
        self.assertEqual(result.contract.opcode, "OWN_FN")
        self.assertEqual(result.contract.call_kind, "instruction")
        self.assertEqual(result.contract.documented_form, "ENO:=OWN_FN(EN,s,d);")

        facts[1]["value"].pop("documented_call_source")
        result = resolve_structured_call_contract(facts, opcode="OWN_FN", model=MODEL)
        self.assertFalse(result.accepted)
        self.assertEqual(result.refusal_reasons, ("instruction_call_witness_source_missing",))


class StructuredCallBindingTests(unittest.TestCase):
    def inspect(self, opcode, facts, source, *, admitted=True, project_context=None, entry_metadata=None):
        from knowledge.instruction_compiler import refresh_reviewed_structured_call_admission
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionRegistry
        from plc.st_validation import inspect_st_program

        entry = {'opcode': opcode, 'target_model': MODEL, 'call_available': False,
                 'facts': copy.deepcopy(facts)}
        entry.update(copy.deepcopy(entry_metadata or {}))
        if admitted:
            self.assertTrue(refresh_reviewed_structured_call_admission(entry, InstructionRegistry([])))

        def owner(name, *, cpu, vendor='mitsubishi'):
            return copy.deepcopy(entry) if name.upper() == opcode and cpu == MODEL else None

        with patch.object(DEFAULT_INSTRUCTION_REGISTRY, 'definition_entry', side_effect=owner):
            report = inspect_st_program(source, plc_model=MODEL, project_context=project_context)
        return report, entry

    def test_ql_statement_uses_actual_st_binding_and_rejects_assignment(self):
        declarations = ('VAR\n  enabled : BOOL;\n  src : WORD;\n'
                        '  dst : STRING[6];\nEND_VAR\n')
        report, entry = self.inspect('BCDDAP', ql_bcddap_statement_facts(),
            declarations + 'BCDDAP(enabled,src,dst);')
        self.assertIn(report['status'], {'passed', 'unverified'}, report['findings'])
        call, = report['calls']
        self.assertEqual(call['kind'], 'structured_statement')
        self.assertNotIn('return_type', call)
        self.assertEqual(call['parameters'], ['EN', 'S', 'D'])
        self.assertTrue(entry['call_available'])
        invalid, _ = self.inspect('BCDDAP', ql_bcddap_statement_facts(),
            declarations + 'enabled := BCDDAP(enabled,src,dst);')
        self.assertEqual(invalid['status'], 'failed')
        self.assertIn('ST_CALL_CONTEXT', {finding['code'] for finding in invalid['findings']})
        self.assertFalse(invalid['calls'])

    def test_ql_grouped_statement_uses_the_same_admission_and_st_binding(self):
        declarations = ('VAR\n  enabled : BOOL;\n  src : WORD;\n'
                        '  dst : STRING[6];\nEND_VAR\n')
        report, entry = self.inspect('BINHAP', ql_binhap_statement_facts(),
            declarations + 'BINHAP(enabled,src,dst);')
        self.assertIn(report['status'], {'passed', 'unverified'}, report['findings'])
        call, = report['calls']
        self.assertEqual(call['kind'], 'structured_statement')
        self.assertNotIn('return_type', call)
        self.assertEqual(call['execution_outputs'][0]['name'], 'ENO')
        self.assertTrue(entry['call_available'])
        for body in ('enabled := BINHAP(enabled,src,dst);',
                     'BINHAP(enabled,src,6);', 'BINHAP(enabled,src);'):
            with self.subTest(body=body):
                invalid, _ = self.inspect('BINHAP', ql_binhap_statement_facts(), declarations + body)
                self.assertEqual(invalid['status'], 'failed', invalid['findings'])
                self.assertFalse(invalid['calls'])

    def test_abs_binds_through_the_existing_st_inspection_entry_and_returns_actual_type(self):
        report, entry = self.inspect('ABS', abs_facts(),
            'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);')

        self.assertEqual(report['status'], 'passed', report['findings'])
        call, = report['calls']
        self.assertEqual(call['kind'], 'structured_function')
        self.assertEqual(call['return_type'], 'REAL')
        self.assertEqual(call['source'], 'reviewed_st_signature')
        self.assertTrue(call['structured_call'])
        self.assertNotIn('call_available', call)
        self.assertIs(entry['call_available'], True)
        self.assertEqual(entry['support_status'], 'reviewed_st_signature')

    def test_ql_left_binds_the_derived_instruction_shape_as_an_eno_assignment(self):
        source = ('VAR\n  en : BOOL;\n  src : STRING;\n  count : WORD;\n'
                  '  dst : STRING;\n  status : BOOL;\nEND_VAR\n'
                  'status := LEFT(en, src, count, dst);')
        report, _ = self.inspect("LEFT", ql_left_derived_instruction_facts(), source)
        self.assertEqual(report["status"], "passed", report["findings"])
        call, = report["calls"]
        self.assertEqual(call["kind"], "structured_instruction")
        self.assertEqual(call["return_type"], "BOOL")

        statement = source.rsplit("status := ", 1)[0] + "LEFT(en, src, count, dst);"
        report, _ = self.inspect("LEFT", ql_left_derived_instruction_facts(), statement)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_CALL_CONTEXT", {finding["code"] for finding in report["findings"]})

    def test_ql_concat_binds_only_the_source_documented_optional_string_range(self):
        for opcode in ("CONCAT", "CONCAT_E"):
            facts = ql_string_call_facts(opcode)
            for count in (2, 3, 28):
                with self.subTest(opcode=opcode, count=count):
                    declarations = ["  en : BOOL;"] if opcode.endswith("_E") else []
                    declarations.extend(f"  s{index} : STRING;" for index in range(1, count + 1))
                    if opcode.endswith("_E"):
                        declarations.extend(["  d : STRING;", "  status : BOOL;"])
                        target = "status"
                        call_arguments = ["en", *(f"s{index}" for index in range(1, count + 1)), "d"]
                    else:
                        declarations.append("  result : STRING;")
                        target = "result"
                        call_arguments = [f"s{index}" for index in range(1, count + 1)]
                    source = ("VAR\n" + "\n".join(declarations) + "\nEND_VAR\n"
                              + f"{target} := {opcode}({', '.join(call_arguments)});")
                    report, _ = self.inspect(opcode, facts, source)
                    self.assertEqual(report["status"], "passed", report["findings"])
                    call, = report["calls"]
                    self.assertEqual(call["kind"], "structured_function")
                    self.assertEqual(call["return_type"], "BOOL" if opcode.endswith("_E") else "STRING")
                    self.assertEqual(call["parameters"], [argument.upper() for argument in call_arguments])

            for count in (1, 29):
                with self.subTest(opcode=opcode, rejected_count=count):
                    declarations = ["  en : BOOL;"] if opcode.endswith("_E") else []
                    declarations.extend(f"  s{index} : STRING;" for index in range(1, count + 1))
                    if opcode.endswith("_E"):
                        declarations.extend(["  d : STRING;", "  status : BOOL;"])
                        target = "status"
                        call_arguments = ["en", *(f"s{index}" for index in range(1, count + 1)), "d"]
                    else:
                        declarations.append("  result : STRING;")
                        target = "result"
                        call_arguments = [f"s{index}" for index in range(1, count + 1)]
                    source = ("VAR\n" + "\n".join(declarations) + "\nEND_VAR\n"
                              + f"{target} := {opcode}({', '.join(call_arguments)});")
                    report, _ = self.inspect(opcode, facts, source)
                    self.assertEqual(report["status"], "failed")
                    self.assertIn("ST_CALL_ARITY", {finding["code"] for finding in report["findings"]})
                    self.assertFalse(report["calls"])

        wrong_type = ('VAR\n  s1 : BOOL;\n  s2 : STRING;\n  result : STRING;\nEND_VAR\n'
                      'result := CONCAT(s1, s2);')
        report, _ = self.inspect("CONCAT", ql_string_call_facts("CONCAT"), wrong_type)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_TYPE", {finding["code"] for finding in report["findings"]})
        self.assertFalse(report["calls"])

        bad_output = ('VAR\n  en : BOOL;\n  s1 : STRING;\n  s2 : STRING;\n  status : BOOL;\nEND_VAR\n'
                      "status := CONCAT_E(en, s1, s2, 'constant');")
        report, _ = self.inspect("CONCAT_E", ql_string_call_facts("CONCAT_E"), bad_output)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_WRITE_TARGET", {finding["code"] for finding in report["findings"]})
        self.assertFalse(report["calls"])

    def test_ql_right_and_len_bind_any16_only_to_16_bit_writable_outputs(self):
        for opcode in ("RIGHT", "LEN"):
            with self.subTest(opcode=opcode):
                output_type = "STRING" if opcode == "RIGHT" else "INT"
                declarations = ["  en : BOOL;", "  src : STRING;"]
                arguments = ["en", "src"]
                if opcode == "RIGHT":
                    declarations.append("  count : WORD;")
                    arguments.append("count")
                declarations.extend([f"  dst : {output_type};", "  status : BOOL;"])
                arguments.append("dst")
                source = ("VAR\n" + "\n".join("  " + item.removeprefix("  ") for item in declarations)
                          + "\nEND_VAR\nstatus := " + opcode + "(" + ", ".join(arguments) + ");")
                report, _ = self.inspect(opcode, ql_string_instruction_facts(opcode), source)
                self.assertEqual(report["status"], "passed", report["findings"])
                call, = report["calls"]
                self.assertEqual(call["kind"], "structured_instruction")
                self.assertEqual(call["return_type"], "BOOL")

        invalid_width = ('VAR\n  en : BOOL;\n  src : STRING;\n  count : DINT;\n'
                         '  dst : STRING;\n  status : BOOL;\nEND_VAR\n'
                         'status := RIGHT(en, src, count, dst);')
        report, _ = self.inspect("RIGHT", ql_string_instruction_facts("RIGHT"), invalid_width)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_TYPE", {finding["code"] for finding in report["findings"]})
        self.assertFalse(report["calls"])

        non_writable = ('VAR\n  en : BOOL;\n  src : STRING;\n  count : WORD;\n'
                        '  status : BOOL;\nEND_VAR\n'
                        "status := RIGHT(en, src, count, 'constant');")
        report, _ = self.inspect("RIGHT", ql_string_instruction_facts("RIGHT"), non_writable)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_WRITE_TARGET", {finding["code"] for finding in report["findings"]})
        self.assertFalse(report["calls"])

        wrong_output_width = ('VAR\n  en : BOOL;\n  src : STRING;\n  dst : DINT;\n'
                              '  status : BOOL;\nEND_VAR\nstatus := LEN(en, src, dst);')
        report, _ = self.inspect("LEN", ql_string_instruction_facts("LEN"), wrong_output_width)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ST_TYPE", {finding["code"] for finding in report["findings"]})
        self.assertFalse(report["calls"])

    def test_fx_k_string_and_time_types_bind_to_exact_st_declarations(self):
        string_facts = fx_k_typed_function_facts('INT_TO_STR', 'FX3U',
            parameter_names=['_INT'],
            parameter_types=[{'kind': 'int', 'bits': 16, 'signed': True}],
            return_type={'kind': 'string', 'encoding': 'binary'}, pages=(66,))
        string_source = ('VAR\n  _INT : INT;\n  function_result : STRING[8];\nEND_VAR\n'
                         'function_result := INT_TO_STR(_INT);')
        string_report, _ = inspect_fx('INT_TO_STR', 'FX3U', string_facts, string_source)
        self.assertEqual(string_report['status'], 'passed', string_report['findings'])
        self.assertEqual(string_report['calls'][0]['return_type'], 'STRING')

        wrong_string_input = ('VAR\n  _INT : DINT;\n  function_result : STRING;\nEND_VAR\n'
                              'function_result := INT_TO_STR(_INT);')
        rejected, _ = inspect_fx('INT_TO_STR', 'FX3U', string_facts, wrong_string_input)
        self.assertEqual(rejected['status'], 'failed', rejected['findings'])
        self.assertIn('ST_TYPE', {finding['code'] for finding in rejected['findings']})
        self.assertFalse(rejected['calls'])

        wrong_string_result = ('VAR\n  _INT : INT;\n  function_result : DINT;\nEND_VAR\n'
                               'function_result := INT_TO_STR(_INT);')
        rejected, _ = inspect_fx('INT_TO_STR', 'FX3U', string_facts, wrong_string_result)
        self.assertEqual(rejected['status'], 'failed', rejected['findings'])
        self.assertIn('ST_TYPE', {finding['code'] for finding in rejected['findings']})

    def test_fx_k_concat_variadic_strings_bind_by_position_with_source_limits(self):
        for count in (2, 3, 28):
            with self.subTest(opcode="CONCAT", count=count):
                names = [f's{index}' for index in range(1, count + 1)]
                declarations = ''.join(f'  {name} : STRING;\n' for name in names)
                source = ('VAR\n' + declarations + '  result : STRING;\nEND_VAR\n'
                          + 'result := CONCAT(' + ', '.join(names) + ');')
                report, _ = inspect_fx('CONCAT', 'FX3U', fx_k_concat_facts('CONCAT'), source)
                self.assertEqual(report['status'], 'passed', report['findings'])
                self.assertEqual(report['calls'][0]['parameters'], ['_IN'] * count)
                self.assertEqual(report['calls'][0]['return_type'], 'STRING')

        for count in (1, 29):
            with self.subTest(opcode="CONCAT", rejected_count=count):
                names = [f's{index}' for index in range(1, count + 1)]
                declarations = ''.join(f'  {name} : STRING;\n' for name in names)
                source = ('VAR\n' + declarations + '  result : STRING;\nEND_VAR\n'
                          + 'result := CONCAT(' + ', '.join(names) + ');')
                report, _ = inspect_fx('CONCAT', 'FX3U', fx_k_concat_facts('CONCAT'), source)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('ST_CALL_ARITY', {item['code'] for item in report['findings']})
                self.assertFalse(report['calls'])

        wrong_type = ('VAR\n  s1 : STRING;\n  number : DINT;\n  result : STRING;\nEND_VAR\n'
                      'result := CONCAT(s1, number);')
        report, _ = inspect_fx('CONCAT', 'FX3U', fx_k_concat_facts('CONCAT'), wrong_type)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_TYPE', {item['code'] for item in report['findings']})
        self.assertFalse(report['calls'])

    def test_fx_k_concat_e_keeps_enable_and_output_at_source_positions(self):
        facts = fx_k_concat_facts('CONCAT_E')
        declarations = ('VAR\n  enabled : BOOL;\n  s1 : STRING;\n  s2 : STRING;\n'
                        '  s3 : STRING;\n  result : STRING;\n  status : BOOL;\nEND_VAR\n')
        for names in (('s1', 's2'), ('s1', 's2', 's3')):
            with self.subTest(input_count=len(names)):
                source = (declarations + 'status := CONCAT_E(enabled, '
                          + ', '.join(names) + ', result);')
                report, _ = inspect_fx('CONCAT_E', 'FX3U', facts, source)
                self.assertEqual(report['status'], 'passed', report['findings'])
                self.assertEqual(report['calls'][0]['parameters'],
                    ['EN'] + ['_IN'] * len(names) + ['OUTPUT_LABEL'])
                self.assertEqual(report['calls'][0]['return_type'], 'BOOL')

        cases = [
            ('status := CONCAT_E(enabled, s1, result);', 'ST_CALL_ARITY'),
            ('status := CONCAT_E(EN := enabled, _IN := s1, _IN := s2, Output_label => result);', 'ST_CALL_ARGUMENT'),
        ]
        for body, expected in cases:
            with self.subTest(expected=expected):
                report, _ = inspect_fx('CONCAT_E', 'FX3U', facts, declarations + body)
                self.assertEqual(report['status'], 'failed')
                self.assertIn(expected, {item['code'] for item in report['findings']})
                self.assertFalse(report['calls'])

        too_many = ('status := CONCAT_E(enabled, '
                    + ', '.join(['s1'] * 29) + ', result);')
        report, _ = inspect_fx('CONCAT_E', 'FX3U', facts, declarations + too_many)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_CALL_ARITY', {item['code'] for item in report['findings']})
        self.assertFalse(report['calls'])

        wrong_type = ('VAR\n  enabled : BOOL;\n  s1 : STRING;\n  s2 : DINT;\n'
                      '  result : STRING;\n  status : BOOL;\nEND_VAR\n'
                      'status := CONCAT_E(enabled, s1, s2, result);')
        report, _ = inspect_fx('CONCAT_E', 'FX3U', facts, wrong_type)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_TYPE', {item['code'] for item in report['findings']})
        self.assertFalse(report['calls'])

        unwritable = ('VAR\n  enabled : BOOL;\n  s1 : STRING;\n  s2 : STRING;\n'
                      '  status : BOOL;\nEND_VAR\n'
                      'status := CONCAT_E(enabled, s1, s2, s1 + s2);')
        report, _ = inspect_fx('CONCAT_E', 'FX3U', facts, unwritable)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_WRITE_TARGET', {item['code'] for item in report['findings']})
        self.assertFalse(report['calls'])

    def test_fx_k_time_function_and_enabled_output_reject_dint_substitutions(self):
        add_facts = fx_k_add_time_facts('ADD_TIME')
        add_source = ('VAR\n  _IN1 : TIME;\n  _IN2 : TIME;\n  result : TIME;\nEND_VAR\n'
                      'result := ADD_TIME(_IN1, _IN2);')
        report, _ = inspect_fx('ADD_TIME', 'FX3U', add_facts, add_source)
        self.assertEqual(report['status'], 'passed', report['findings'])
        self.assertEqual(report['calls'][0]['return_type'], 'TIME')

        wrong_time_argument = ('VAR\n  _IN1 : DINT;\n  _IN2 : TIME;\n  result : TIME;\nEND_VAR\n'
                              'result := ADD_TIME(_IN1, _IN2);')
        rejected, _ = inspect_fx('ADD_TIME', 'FX3U', add_facts, wrong_time_argument)
        self.assertEqual(rejected['status'], 'failed', rejected['findings'])
        self.assertIn('ST_TYPE', {finding['code'] for finding in rejected['findings']})
        self.assertFalse(rejected['calls'])

        enabled_facts = fx_k_add_time_facts('ADD_TIME_E')
        enabled_source = ('VAR\n  enabled : BOOL;\n  _IN1 : TIME;\n  _IN2 : TIME;\n'
                          '  Output_label : TIME;\n  eno : BOOL;\nEND_VAR\n'
                          'eno := ADD_TIME_E(enabled, _IN1, _IN2, Output_label);')
        report, _ = inspect_fx('ADD_TIME_E', 'FX3U', enabled_facts, enabled_source)
        self.assertEqual(report['status'], 'passed', report['findings'])
        self.assertEqual(report['calls'][0]['return_type'], 'BOOL')

        wrong_output = ('VAR\n  enabled : BOOL;\n  _IN1 : TIME;\n  _IN2 : TIME;\n'
                        '  Output_label : DINT;\n  eno : BOOL;\nEND_VAR\n'
                        'eno := ADD_TIME_E(enabled, _IN1, _IN2, Output_label);')
        rejected, _ = inspect_fx('ADD_TIME_E', 'FX3U', enabled_facts, wrong_output)
        self.assertEqual(rejected['status'], 'failed', rejected['findings'])
        self.assertIn('ST_TYPE', {finding['code'] for finding in rejected['findings']})
        self.assertFalse(rejected['calls'])

    def test_abs_e_binds_named_enable_source_output_and_bool_result(self):
        declarations = ('VAR\n  src : DINT;\n  dst : DINT;\n  enabled : BOOL;\nEND_VAR\n')
        report, _ = self.inspect('ABS_E', abs_e_facts(), declarations
            + 'enabled := ABS_E(EN := TRUE, s := src, d => dst);')

        self.assertEqual(report['status'], 'passed', report['findings'])
        call, = report['calls']
        self.assertEqual(call['parameters'], ['EN', 'S', 'D'])
        self.assertEqual(call['return_type'], 'BOOL')

        positional, _ = self.inspect('ABS_E', abs_e_facts(), declarations
            + 'enabled := ABS_E(TRUE, src, dst);')
        self.assertEqual(positional['status'], 'passed', positional['findings'])
        self.assertEqual(positional['calls'][0]['parameters'], ['EN', 'S', 'D'])
        self.assertEqual(positional['calls'][0]['return_type'], 'BOOL')

    def test_ql_instruction_binds_documented_positional_and_named_output_forms(self):
        declarations = ('VAR\n  enabled : BOOL;\n  input_value : REAL;\n'
                        '  output_value : REAL;\n  eno : BOOL;\nEND_VAR\n')
        positional, entry = self.inspect('SIN', ql_trig_facts(), declarations
            + 'eno := SIN(enabled, input_value, output_value);')

        self.assertEqual(positional['status'], 'passed', positional['findings'])
        call, = positional['calls']
        self.assertEqual(call['kind'], 'structured_instruction')
        self.assertEqual(call['parameters'], ['EN', 'S', 'D'])
        self.assertEqual(call['return_type'], 'BOOL')
        self.assertIn('authored:ql:argument_table_st:parameters.st_signature', call['contract_fact_ids'])
        self.assertTrue(entry['call_available'])

        named, _ = self.inspect('SIN', ql_trig_facts(), declarations
            + 'eno := SIN(EN := enabled, s := input_value, d => output_value);')
        self.assertEqual(named['status'], 'passed', named['findings'])
        self.assertEqual(named['calls'][0]['return_type'], 'BOOL')
        self.assertEqual(named['calls'][0]['parameters'], ['EN', 'S', 'D'])

    def test_ql_instruction_requires_assignment_complete_inputs_and_writable_output(self):
        declarations = ('VAR\n  enabled : BOOL;\n  input_value : REAL;\n  wrong_input : DINT;\n'
                        '  output_value : REAL;\n  eno : BOOL;\nEND_VAR\n')
        cases = [
            ('SIN(enabled, input_value, output_value);', 'ST_CALL_CONTEXT'),
            ('eno := SIN(enabled, input_value);', 'ST_CALL_ARGUMENT'),
            ('eno := SIN(enabled, input_value, input_value + 1.0);', 'ST_WRITE_TARGET'),
            ('eno := SIN(enabled, wrong_input, output_value);', 'ST_TYPE'),
            ('output_value := SIN(enabled, input_value, output_value);', 'ST_TYPE'),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                report, _ = self.inspect('SIN', ql_trig_facts(), declarations + body)
                self.assertEqual(report['status'], 'failed', report['findings'])
                self.assertIn(expected, {finding['code'] for finding in report['findings']})

    def test_ql_instruction_signature_stays_unavailable_without_reviewed_metadata(self):
        declarations = ('VAR\n  enabled : BOOL;\n  input_value : REAL;\n'
                        '  output_value : REAL;\n  eno : BOOL;\nEND_VAR\n')
        report, entry = self.inspect('SIN', ql_trig_facts(), declarations
            + 'eno := SIN(enabled, input_value, output_value);', admitted=False)

        self.assertNotEqual(report['status'], 'passed')
        self.assertFalse(any(call.get('structured_call') for call in report['calls']))
        self.assertFalse(entry['call_available'])

    def test_generic_return_keeps_exact_type_and_rejects_a_different_destination_type(self):
        report, _ = self.inspect('ABS', abs_facts(),
            'VAR\n  src : INT;\n  dst : DINT;\nEND_VAR\ndst := ABS(src);')

        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_TYPE', {finding['code'] for finding in report['findings']})
        self.assertEqual(report['calls'][0]['return_type'], 'INT')

    def test_bcd_to_dint_source_type_alternatives_bind_word_and_dword_only(self):
        for opcode in ('BCD_TO_DINT', 'BCD_TO_DINT_E'):
            with self.subTest(opcode=opcode):
                facts = bcd_to_dint_facts(opcode)
                resolution = resolve_structured_call_contract(facts, opcode=opcode, model=MODEL)
                self.assertTrue(resolution.accepted, resolution.refusal_reasons)
                source_parameter = next(parameter for parameter in resolution.contract.parameters
                                        if parameter.get('role') == 'source')
                self.assertEqual(source_parameter['type'], 'DWORD')
                self.assertEqual(source_parameter['type_alternatives'], ['WORD', 'DWORD'])

                for input_type in ('WORD', 'DWORD'):
                    if opcode.endswith('_E'):
                        declarations = ('VAR\n  enabled : BOOL;\n  src : ' + input_type +
                                        ';\n  dst : DINT;\n  status : BOOL;\nEND_VAR\n')
                        body = ('status := ' + opcode +
                                '(EN := enabled, s := src, d => dst);')
                        source = declarations + body
                    else:
                        source = ('VAR\n  src : ' + input_type +
                                  ';\n  dst : DINT;\nEND_VAR\n'
                                  + 'dst := ' + opcode + '(src);')
                    report, _ = self.inspect(opcode, facts, source)
                    self.assertEqual(report['status'], 'passed', report['findings'])
                    call, = report['calls']
                    self.assertEqual(call['kind'], 'structured_function')

                if opcode.endswith('_E'):
                    declarations = ('VAR\n  enabled : BOOL;\n  src : BOOL;\n'
                                    '  dst : DINT;\n  status : BOOL;\nEND_VAR\n')
                    source = declarations + ('status := ' + opcode +
                        '(EN := enabled, s := src, d => dst);')
                else:
                    source = ('VAR\n  src : BOOL;\n  dst : DINT;\nEND_VAR\n'
                              + 'dst := ' + opcode + '(src);')
                rejected, _ = self.inspect(opcode, facts, source)
                self.assertEqual(rejected['status'], 'failed')
                self.assertIn('ST_TYPE', {finding['code'] for finding in rejected['findings']})
                self.assertFalse(rejected['calls'])

                if opcode.endswith('_E'):
                    int_output = ('VAR\n  enabled : BOOL;\n  src : DWORD;\n'
                                  '  dst : INT;\n  status : BOOL;\nEND_VAR\n'
                                  'status := ' + opcode +
                                  '(EN := enabled, s := src, d => dst);')
                    report, _ = self.inspect(opcode, facts, int_output)
                    self.assertEqual(report['status'], 'passed', report['findings'])

    def test_abs_rejects_non_numeric_actual_type(self):
        report, _ = self.inspect('ABS', abs_facts(),
            'VAR\n  src : BOOL;\n  dst : BOOL;\nEND_VAR\ndst := ABS(src);')

        self.assertEqual(report['status'], 'failed')
        self.assertTrue(any(f['code'] == 'ST_TYPE' and 'ANY_NUM' in f['message']
                            for f in report['findings']))
        self.assertFalse(report['calls'])

    def test_abs_e_rejects_wrong_enable_type_missing_output_and_unwritable_output(self):
        declarations = 'VAR\n  src : REAL;\n  dst : REAL;\n  enabled : BOOL;\nEND_VAR\n'
        cases = [
            ('enabled := ABS_E(EN := src, s := src, d => dst);', 'ST_TYPE'),
            ('enabled := ABS_E(EN := TRUE, s := src);', 'ST_CALL_ARGUMENT'),
            ('enabled := ABS_E(EN := TRUE, s := src, d => src + 1.0);', 'ST_WRITE_TARGET'),
            ('enabled := ABS_E(TRUE, src);', 'ST_CALL_ARGUMENT'),
            ('enabled := ABS_E(TRUE, src, src + 1.0);', 'ST_WRITE_TARGET'),
        ]
        for body, expected in cases:
            with self.subTest(expected=expected):
                report, _ = self.inspect('ABS_E', abs_e_facts(), declarations + body)
                self.assertEqual(report['status'], 'failed')
                self.assertIn(expected, {finding['code'] for finding in report['findings']})

    def test_abs_e_generic_output_uses_the_input_exact_type(self):
        declarations = 'VAR\n  src : INT;\n  dst : DINT;\n  enabled : BOOL;\nEND_VAR\n'
        report, _ = self.inspect('ABS_E', abs_e_facts(), declarations
            + 'enabled := ABS_E(EN := TRUE, s := src, d => dst);')

        self.assertEqual(report['status'], 'failed')
        self.assertTrue(any(f['code'] == 'ST_TYPE' and 'exact types' in f['message']
                            for f in report['findings']))

        positional, _ = self.inspect('ABS_E', abs_e_facts(), declarations
            + 'enabled := ABS_E(TRUE, src, dst);')
        self.assertEqual(positional['status'], 'failed')
        self.assertTrue(any(f['code'] == 'ST_TYPE' and 'exact types' in f['message']
                            for f in positional['findings']))

    def test_unadmitted_source_signature_does_not_enable_a_structured_call(self):
        report, entry = self.inspect('ABS', abs_facts(),
            'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);', admitted=False)

        self.assertNotEqual(report['status'], 'passed')
        self.assertFalse(any(call.get('structured_call') for call in report['calls']))
        self.assertIs(entry['call_available'], False)

    def test_call_available_without_reviewed_st_marker_does_not_enable_the_new_branch(self):
        report, entry = self.inspect('ABS', abs_facts(),
            'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);', admitted=False,
            entry_metadata={'call_available': True, 'support_status': 'supported',
                            'call_admission_details': {'interfaces': ['st']}})

        self.assertNotEqual(report['status'], 'passed')
        self.assertFalse(any(call.get('structured_call') for call in report['calls']))
        self.assertIs(entry['call_available'], True)

    def test_contract_conditions_are_checked_against_the_project_context(self):
        facts = abs_facts()
        signature = next(item for item in facts if item['dimension'] == 'parameters.st_signature')
        signature['conditions'] = [{'context': 'project_mode', 'equals': 'structured_text'}]
        source = 'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);'

        report, _ = self.inspect('ABS', facts, source,
            project_context={'project_mode': 'structured_ladder_block'})

        self.assertEqual(report['status'], 'failed')
        self.assertIn('ST_CALL_CONDITION', {finding['code'] for finding in report['findings']})
        self.assertFalse(report['calls'])

    def test_disjoint_cpu_version_denial_is_ignored_only_when_its_guard_is_false(self):
        facts = abs_facts()
        positive = next(item for item in facts if item['dimension'] == 'constraints.cpu_availability')
        positive['conditions'] = [{'context': 'cpu_version', 'minimum': '2.20'}]
        facts.append(fact('authored:ql:cpu_applicability:below_minimum', 'constraints.cpu_availability', {
            'available': False, 'model_states': {MODEL: 'unsupported'},
            'rule': 'documented_CPU_instruction_scope',
        }, opcode='ABS', languages=('st', 'fbd', 'ladder'),
            conditions=[{'context': 'cpu_version', 'maximum_exclusive': '2.20'}]))
        source = 'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);'

        current = self.inspect('ABS', facts, source, project_context={'cpu_version': '2.20'})[0]
        below = self.inspect('ABS', facts, source, project_context={'cpu_version': '2.19'})[0]

        self.assertEqual(current['status'], 'passed', current['findings'])
        self.assertEqual(below['status'], 'failed')

    def test_fx_abs_binds_through_inspection_with_documented_numeric_alternatives(self):
        report, entry = inspect_fx('ABS', 'FX3U', fx_abs_facts(),
            'VAR\n  src : REAL;\n  dst : REAL;\nEND_VAR\ndst := ABS(src);')

        self.assertEqual(report['status'], 'passed', report['findings'])
        call, = report['calls']
        self.assertEqual(call['return_type'], 'REAL')
        self.assertEqual(call['parameters'], ['_IN'])
        self.assertIn('source:cpu_availability:fxcpu_application_k:2831', call['contract_fact_ids'])
        self.assertIs(entry['call_available'], True)
        self.assertEqual(entry['support_status'], 'reviewed_st_signature')

    def test_fx_conditional_cpu_version_is_required_for_int_to_real_calls(self):
        cases = [
            ("INT_TO_REAL",
             "VAR\n  src : INT;\n  dst : REAL;\nEND_VAR\ndst := INT_TO_REAL(src);"),
            ("INT_TO_REAL_E",
             "VAR\n  EN : BOOL;\n  src : INT;\n  dst : REAL;\n  eno : BOOL;\nEND_VAR\neno := INT_TO_REAL_E(EN, src, dst);"),
        ]
        for model in ("FX3G", "FX3GC"):
            for opcode, source in cases:
                with self.subTest(opcode=opcode, model=model, version="1.10"):
                    report, entry = inspect_fx(opcode, model,
                        fx_int_to_real_conditional_facts(opcode, model), source,
                        project_context={"cpu_version": "1.10"})
                    self.assertEqual(report["status"], "passed", report["findings"])
                    self.assertIs(entry["call_available"], True)
                with self.subTest(opcode=opcode, model=model, version="1.09"):
                    report, _ = inspect_fx(opcode, model,
                        fx_int_to_real_conditional_facts(opcode, model), source,
                        project_context={"cpu_version": "1.09"})
                    self.assertEqual(report["status"], "failed", report["findings"])
                with self.subTest(opcode=opcode, model=model, version="missing"):
                    report, _ = inspect_fx(opcode, model,
                        fx_int_to_real_conditional_facts(opcode, model), source)
                    self.assertEqual(report["status"], "unverified", report["findings"])

    def test_fx_source_version_upper_bound_preserves_the_complementary_denial(self):
        # Synthetic version variants exercise the shared binder, not a claim
        # that this conversion was retired in a particular real firmware.
        facts = fx_int_to_real_conditional_facts()
        version, cpu, signature = facts
        version['value'] = {'models': ['FX3G'], 'maximum_series_version_exclusive': 'Ver.3.30'}
        guard = {'context': 'cpu_version', 'maximum_exclusive': '3.30'}
        cpu['conditions'] = [copy.deepcopy(guard)]
        signature['conditions'] = [copy.deepcopy(guard)]
        denial = fx_fact('structured:int_to_real:retired', 'constraints.cpu_availability',
                        {'available': False, 'model_states': {'FX3G': 'unsupported'}},
                        opcode='INT_TO_REAL', model='FX3G', pages=(62,))
        denial['conditions'] = [{'context': 'cpu_version', 'minimum': '3.30'}]
        denial['depends_on'] = [version['id']]
        facts.append(denial)
        resolved = resolve_structured_call_contract(facts, opcode='INT_TO_REAL', model='FX3G')
        self.assertTrue(resolved.accepted, resolved.refusal_reasons)
        self.assertTrue(any(guard in row['conditions'] for row in resolved.contract.conditions))
        self.assertIn(denial['id'], resolved.contract.fact_ids)
        source = 'VAR\n src : INT;\n dst : REAL;\nEND_VAR\ndst := INT_TO_REAL(src);'
        for firmware, expected in [('3.29', 'passed'), ('3.30', 'failed'), ('3.31', 'failed'), (None, 'unverified')]:
            with self.subTest(firmware=firmware):
                context = {'cpu_version': firmware} if firmware else {}
                report, _ = inspect_fx('INT_TO_REAL', 'FX3G', facts, source, project_context=context)
                self.assertEqual(report['status'], expected, report['findings'])
        denial['conditions'] = [{'context': 'cpu_version', 'minimum': '3.29'}]
        self.assertFalse(resolve_structured_call_contract(facts, opcode='INT_TO_REAL', model='FX3G').accepted)

    def test_fx_source_version_upper_bound_requires_its_exact_source_value(self):
        for documented in ('3.31', None, 'unresolved'):
            with self.subTest(documented=documented):
                facts = fx_int_to_real_conditional_facts()
                facts[0]['value'] = {'models': ['FX3G'], 'maximum_series_version_exclusive': documented}
                for row in facts[1:]:
                    row['conditions'] = [{'context': 'cpu_version', 'maximum_exclusive': '3.30'}]
                self.assertFalse(resolve_structured_call_contract(facts, opcode='INT_TO_REAL', model='FX3G').accepted)

    def test_fx_abs_rejects_lreal_outside_its_documented_alternatives(self):
        report, _ = inspect_fx('ABS', 'FX3U', fx_abs_facts(),
            'VAR\n  src : LREAL;\n  dst : LREAL;\nEND_VAR\ndst := ABS(src);')

        self.assertEqual(report['status'], 'failed')
        self.assertTrue(any(item['code'] == 'ST_TYPE' and 'INT, DINT, REAL' in item['message']
                            for item in report['findings']))

    def test_fx_abs_e_binds_bool_eno_and_rejects_wrong_or_unwritable_numeric_output(self):
        declarations = ('VAR\n  src : DINT;\n  dst : DINT;\n  other : INT;\n'
                        '  status : BOOL;\nEND_VAR\n')
        valid, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(EN := TRUE, _IN := src, Output_label => dst);')
        self.assertEqual(valid['status'], 'passed', valid['findings'])
        self.assertEqual(valid['calls'][0]['return_type'], 'BOOL')
        self.assertEqual(valid['calls'][0]['parameters'], ['EN', '_IN', 'OUTPUT_LABEL'])

        positional, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(TRUE, src, dst);')
        self.assertEqual(positional['status'], 'passed', positional['findings'])
        self.assertEqual(positional['calls'][0]['return_type'], 'BOOL')
        self.assertEqual(positional['calls'][0]['parameters'], ['EN', '_IN', 'OUTPUT_LABEL'])

        wrong_type, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(EN := TRUE, _IN := src, Output_label => other);')
        self.assertEqual(wrong_type['status'], 'failed')
        self.assertIn('ST_TYPE', {item['code'] for item in wrong_type['findings']})

        missing_output, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(EN := TRUE, _IN := src);')
        self.assertEqual(missing_output['status'], 'failed')
        self.assertIn('ST_CALL_ARGUMENT', {item['code'] for item in missing_output['findings']})

        not_writable, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(EN := TRUE, _IN := src, Output_label => src + 1);')
        self.assertEqual(not_writable['status'], 'failed')
        self.assertIn('ST_WRITE_TARGET', {item['code'] for item in not_writable['findings']})

        missing_positional_output, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(TRUE, src);')
        self.assertEqual(missing_positional_output['status'], 'failed')
        self.assertIn('ST_CALL_ARGUMENT', {item['code'] for item in missing_positional_output['findings']})

        wrong_positional_output_type, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(TRUE, src, other);')
        self.assertEqual(wrong_positional_output_type['status'], 'failed')
        self.assertIn('ST_TYPE', {item['code'] for item in wrong_positional_output_type['findings']})

        not_writable_positional_output, _ = inspect_fx('ABS_E', 'FX1N', fx_abs_e_facts(), declarations
            + 'status := ABS_E(TRUE, src, src + 1);')
        self.assertEqual(not_writable_positional_output['status'], 'failed')
        self.assertIn('ST_WRITE_TARGET', {item['code'] for item in not_writable_positional_output['findings']})


class StructuredAnalogModuleConstraintTests(unittest.TestCase):
    def fact(self, opcode="ANRD", io=None):
        return {
            "id": "authored:applied:analog_module_io@fxcpu_basic_applied_m",
            "dimension": "constraints.documented_requirements",
            "value": {
                "kind": "structured_analog_module_io",
                "representation": "st_statement_call",
                "interface_module": "FX2-24EI",
                "analog_module": "F2-6A",
                "io": io if io is not None else [
                    {"binding": {"name": "s"}, "direction": "input",
                     "device_prefix": "X", "points": 16},
                    {"binding": {"name": "d1"}, "direction": "output",
                     "device_prefix": "Y", "points": 8},
                ],
            },
            "status": "source_verified",
            "verification": "source_checked",
            "scope": {"forms": [opcode], "languages": ["st"], "models": ["FX3U"]},
            "depends_on": [],
            "sources": [{"manual_id": "fxcpu_basic_applied_m", "revision": "M",
                         "pdf_pages": [423], "opcode": opcode, "target_model": "FX3U"}],
        }

    def values(self):
        return {"$PARAMETER_NAMES": ["EN", "s", "n", "d1", "d2"],
                "$OPERANDS": ["EN", "X20", "K16", "Y20", "Y40"]}

    def interface(self, **overrides):
        return {"interface_module": "FX2-24EI", "analog_module": "F2-6A",
                "input_head": "X20", "output_head": "Y20",
                "input_points": 16, "output_points": 8, **overrides}

    def evaluate(self, fact, context, *, values=None):
        from plc.instruction_constraints import check_fact_constraints
        report = check_fact_constraints([fact], self.values() if values is None else values,
            target=fact["scope"]["forms"][0], plc_model="FX3U", language="st", context=context)
        return report["checks"][0]

    def test_anrd_requires_one_matching_module_row_for_both_heads_and_spans(self):
        check = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [self.interface()]}})
        self.assertEqual(check["status"], "passed", check)

    def test_missing_context_stays_unverified(self):
        check = self.evaluate(self.fact(), {})
        self.assertEqual(check["status"], "unverified")

        missing_head = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [
            self.interface(output_head=None) ]}})
        self.assertEqual(missing_head["status"], "unverified")

    def test_wrong_module_and_wrong_head_fail(self):
        wrong_module = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [
            self.interface(analog_module="F2-6AD") ]}})
        self.assertEqual(wrong_module["status"], "failed")

        wrong_head = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [
            self.interface(input_head="X21") ]}})
        self.assertEqual(wrong_head["status"], "failed")

    def test_input_and_output_cannot_be_combined_from_separate_rows(self):
        rows = [self.interface(output_head="Y10"), self.interface(input_head="X21")]
        check = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": rows}})
        self.assertEqual(check["status"], "failed")

    def test_source_point_count_must_match_and_device_span_must_be_valid(self):
        for count in (15, 32):
            with self.subTest(input_points=count):
                mismatch = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [
                    self.interface(input_points=count)]}})
                self.assertEqual(mismatch["status"], "failed")
                self.assertEqual(mismatch["reason"], "analog_module_io_configured_points_mismatch")

        outside = self.evaluate(self.fact(), {"hardware": {"analog_interfaces": [
            self.interface(input_head="X367") ]}},
            values={"$PARAMETER_NAMES": ["EN", "s", "n", "d1", "d2"],
                    "$OPERANDS": ["EN", "X367", "K16", "Y20", "Y40"]})
        self.assertEqual(outside["status"], "failed")
        self.assertEqual(outside["reason"], "analog_module_io_device_span_outside_model_range")

    def test_anwr_without_sourced_points_only_checks_explicit_heads(self):
        fact = self.fact("ANWR", [
            {"binding": {"name": "s2"}, "direction": "input", "device_prefix": "X"},
            {"binding": {"name": "d"}, "direction": "output", "device_prefix": "Y"},
        ])
        interface = self.interface()
        interface.pop("input_points")
        interface.pop("output_points")
        check = self.evaluate(fact, {"hardware": {"analog_interfaces": [interface]}}, values={
                "$PARAMETER_NAMES": ["EN", "s1", "s2", "n", "d"],
                "$OPERANDS": ["EN", "X10", "X20", "K16", "Y20"],
            })
        self.assertEqual(check["status"], "passed", check)


class ConditionalCpuVersionConstraintTests(unittest.TestCase):
    model = "FXU"
    opcode = "ANWR"
    header_id = "authored:applied:source_cpu_header@fxcpu_basic_applied_m"
    version_id = "authored:applied:cpu_version:header_maximum@fxcpu_basic_applied_m"
    positive_id = "authored:applied:cpu_availability:header@fxcpu_basic_applied_m"
    negative_id = "authored:applied:cpu_availability:at_or_above_header_version@fxcpu_basic_applied_m"

    def fact(self, identity, dimension, value, *, depends_on=(), conditions=()):
        row = {
            "id": identity, "dimension": dimension, "value": value,
            "status": "source_verified", "verification": "source_checked",
            "scope": {"forms": [self.opcode], "languages": ["st"], "models": [self.model]},
            "depends_on": list(depends_on),
            "sources": [{"manual_id": "fxcpu_basic_applied_m", "revision": "M",
                         "pdf_pages": [36, 423], "opcode": self.opcode,
                         "target_model": self.model}],
        }
        if conditions:
            row["conditions"] = list(conditions)
        return row

    def facts(self, *, header_max="3.30", version_max="Ver.3.30",
              guard_max="3.30", cpu_dependencies=None, include_version=True):
        rows = [self.fact(self.header_id, "constraints.source_cpu_header", {
            "basis": "own_instruction_applicability_table_row", "model": self.model,
            "state": "conditional", "model_group": "FXU",
            "source_mark": "conditional", "maximum_version_exclusive": header_max,
        })]
        if include_version:
            rows.append(self.fact(self.version_id, "constraints.cpu_version", {
                "models": [self.model],
                "maximum_series_version_exclusive": version_max,
                "basis": "own_instruction_retirement_footnote",
            }, depends_on=[self.header_id]))
        dependencies = ([self.header_id, self.version_id] if include_version else [self.header_id]
                        ) if cpu_dependencies is None else list(cpu_dependencies)
        rows.append(self.fact(self.positive_id, "constraints.cpu_availability", {
            "model_states": {self.model: "conditional"},
            "rule": "instruction_header_version_condition",
        }, depends_on=dependencies,
            conditions=[{"context": "cpu_version", "maximum_exclusive": guard_max}]))
        if include_version:
            rows.append(self.fact(self.negative_id, "constraints.cpu_availability", {
                "available": False, "model_states": {self.model: "unsupported"},
                "rule": "instruction_not_available_after_retirement",
            }, depends_on=[self.header_id, self.version_id],
                conditions=[{"context": "cpu_version", "minimum": "3.30"}]))
        return rows

    def evaluate(self, facts, cpu_version):
        from plc.instruction_constraints import check_fact_constraints
        context = {} if cpu_version is None else {"cpu_version": cpu_version}
        return check_fact_constraints(facts, {}, target=self.opcode, plc_model=self.model,
            language="st", context=context)

    def test_conditional_availability_uses_exact_header_dependency_and_version_guard(self):
        for version, expected in (("3.29", "passed"), ("3.30", "failed"), (None, "unverified")):
            with self.subTest(version=version):
                report = self.evaluate(self.facts(), version)
                self.assertEqual(report["status"], expected, report["findings"])

    def test_missing_or_mismatched_direct_version_evidence_stays_unverified(self):
        no_version_dependency = self.facts(cpu_dependencies=[self.header_id])
        missing = self.evaluate(no_version_dependency, "3.29")
        positive = next(row for row in missing["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")

        mismatched_guard = self.evaluate(self.facts(guard_max="3.29"), "3.28")
        positive = next(row for row in mismatched_guard["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")

        mismatched_header = self.evaluate(self.facts(header_max="3.31"), "3.29")
        positive = next(row for row in mismatched_header["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")

        missing_version_fact = self.evaluate(self.facts(include_version=False), "3.29")
        positive = next(row for row in missing_version_fact["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")

        malformed_version = self.facts()
        version_fact = next(row for row in malformed_version if row["id"] == self.version_id)
        version_fact["value"].pop("basis")
        malformed = self.evaluate(malformed_version, "3.29")
        positive = next(row for row in malformed["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")

        mixed_bounds = self.facts()
        version_fact = next(row for row in mixed_bounds if row["id"] == self.version_id)
        version_fact["value"]["minimum_series_version"] = "Ver.1.00"
        mixed = self.evaluate(mixed_bounds, "3.29")
        positive = next(row for row in mixed["checks"] if row["fact_id"] == self.positive_id)
        self.assertEqual(positive["status"], "unverified")


if __name__ == "__main__":
    unittest.main()

