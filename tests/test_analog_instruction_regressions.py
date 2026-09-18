import pytest

from plc.specification.approach import inspect_ladder_features
from plc.ir import build_plc_ir
from plc.validation import PLCJsonValidationError, validate_ladder_full


def _analog_ladder(opcode, operands):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "模拟量传送",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [
                            {"type": "NO", "address": "M8000", "label": "运行"}
                        ],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": operands,
                                "label": "模拟量读写",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_wr3a_is_a_supported_instruction_and_reads_its_source_operand():
    ladder = _analog_ladder("WR3A", ["K0", "K21", "D200"])

    assert validate_ladder_full(ladder, "FX3U") is ladder
    program = build_plc_ir(ladder, plc_model="FX3U")

    assert program["networks"][0]["reads"] == ["M8000", "D200"]
    assert program["networks"][0]["writes"] == []
    assert "analog_control" in inspect_ladder_features(ladder)["structures"]


def test_rd3a_third_operand_is_the_local_write_destination():
    ladder = _analog_ladder("RD3A", ["K0", "K21", "D100"])

    assert validate_ladder_full(ladder, "FX3U") is ladder
    program = build_plc_ir(ladder, plc_model="FX3U")

    assert program["networks"][0]["reads"] == ["M8000"]
    assert program["networks"][0]["writes"] == ["D100"]


@pytest.mark.parametrize(
    ("opcode", "operands"),
    (("RD3A", ["K0", "K21"]), ("WR3A", ["K0", "K21", "D0", "K1"])),
)
def test_rd3a_wr3a_require_the_documented_three_operands(opcode, operands):
    with pytest.raises(PLCJsonValidationError, match="requires exactly 3 operands"):
        validate_ladder_full(_analog_ladder(opcode, operands), "FX3U")


@pytest.mark.parametrize(
    ("opcode", "module"),
    (("RD3A", "FX3U-4AD-ADP"), ("WR3A", "FX3U-4DA-ADP")),
)
def test_rd3a_wr3a_report_precise_confirmed_module_mismatch(opcode, module):
    operands = ["K0", "K21", "D100"]
    confirmed = {
        "hardware_context": {
            "analog_module": {
                "input_module" if opcode == "RD3A" else "output_module": module,
                "input_channel" if opcode == "RD3A" else "output_channel": 1,
            }
        }
    }

    with pytest.raises(
        PLCJsonValidationError,
        match=r"valid instruction.*cannot access confirmed",
    ):
        validate_ladder_full(_analog_ladder(opcode, operands), "FX3U", confirmed)
