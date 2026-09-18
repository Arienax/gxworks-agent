import pytest

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
from plc.validation import PLCJsonValidationError, validate_ladder_full


def _ladder(opcode, operands):
    return {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [{
                    "type": "APP_INSTR",
                    "opcode": opcode,
                    "operands": list(operands),
                    "label": None,
                }],
            }],
        }],
    }


def test_fx3u_registry_exposes_zrst_with_exact_two_operand_arity():
    zrst = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRST")
    assert zrst is not None
    assert zrst.supports_cpu("FX3U")
    assert zrst.accepts_arity(2)
    assert not zrst.accepts_arity(1)
    assert not zrst.accepts_arity(16)
    assert "ZRST" in generation_app_instr_mnemonics("FX3U")


def test_fx3u_zrst_m100_m115_is_a_valid_generated_instruction():
    ladder = _ladder("ZRST", ["M100", "M115"])
    assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


def test_rst_does_not_masquerade_as_two_operand_zone_reset():
    rst = DEFAULT_INSTRUCTION_REGISTRY.resolve("RST")
    assert rst is not None
    assert rst.accepts_arity(1)
    assert not rst.accepts_arity(2)
    with pytest.raises(PLCJsonValidationError, match="RST requires exactly 1 operands"):
        validate_ladder_full(_ladder("RST", ["M100", "M115"]), plc_model="FX3U")
