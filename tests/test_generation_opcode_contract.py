from instruction_registry import generation_app_instr_mnemonics
from plc_generation_contract import ladder_response_schema
from plc_generation_context import _select_system_prompt


def _opcode_rule(schema):
    full = schema["oneOf"][0] if "oneOf" in schema else schema
    return (
        full["properties"]["rungs"]["items"]["properties"]["branches"]["items"]
        ["properties"]["outputs"]["items"]["oneOf"][2]["properties"]["opcode"]
    )


def test_model_specific_app_instr_enum_matches_registry():
    fx3 = tuple(generation_app_instr_mnemonics("FX3U"))
    fx5 = tuple(generation_app_instr_mnemonics("FX5U"))
    assert fx3 and fx5
    assert _opcode_rule(ladder_response_schema(plc_model="FX3U"))["enum"] == list(fx3)
    assert _opcode_rule(ladder_response_schema(plc_model="FX5U"))["enum"] == list(fx5)
    for forbidden in ("OUT", "PLS", "PLF", "END", "NOT_A_REAL_OPCODE"):
        assert forbidden not in fx3
        assert forbidden not in fx5
    assert set(fx3) != set(fx5), "CPU-specific catalogue must constrain at least one opcode"


def test_normal_generation_prompt_exposes_only_catalogued_fx3u_opcodes():
    prompt = _select_system_prompt("ladder", plc_model="FX3U")
    rule = _opcode_rule(ladder_response_schema(plc_model="FX3U"))
    assert '"enum":[' in prompt
    assert "NOT_A_REAL_OPCODE" not in prompt
    assert "OUT" not in rule["enum"]
    assert len(rule["enum"]) >= 10
