import pytest

from plc.specification.confirmed import _validate_device_address as validate_spec_device
from plc.ir import analyze_instruction_access, build_plc_ir, ir_to_ladder
from plc.validation import (
    PLCJsonValidationError,
    parse_device_address,
    parse_indexed_device_address,
    validate_ladder_full,
)
from plc.st_renderer import render_plc_ir_to_st


def app(opcode, *operands):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": list(operands),
        "label": "",
    }


def rung(rung_id, output):
    return {
        "rung_id": rung_id,
        "debug_note": "",
        "header_element": None,
        "shared_inputs": [{"type": "NO", "address": "M8000", "label": ""}],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [],
                "outputs": [output],
            }
        ],
    }


def indexed_ladder():
    return {
        "device_comments": {
            "Z0": "索引寄存器",
            "V7": "索引寄存器",
            "D100": "变址基址",
        },
        "rungs": [
            rung(1, app("MOV", "K1", "Z0")),
            rung(2, app("MOV", "D0", "D100Z0")),
            rung(3, app("MOV", "D100V7", "D200")),
        ],
    }


def test_fx3u_v_z_ranges_are_validated():
    assert parse_device_address("V0", "FX3U") == ("V", 0)
    assert parse_device_address("V7", "FX3U") == ("V", 7)
    assert parse_device_address("Z0", "FX3U") == ("Z", 0)
    assert parse_device_address("Z7", "FX3U") == ("Z", 7)
    assert parse_device_address("V8", "FX3U") is None
    assert parse_device_address("Z8", "FX3U") is None


def test_fx3u_indexed_operands_are_decomposed_without_changing_text():
    parsed = parse_indexed_device_address("D100Z0", "FX3U")
    assert parsed["base_text"] == "D100"
    assert parsed["index_text"] == "Z0"
    assert parse_indexed_device_address("D100Z8", "FX3U") is None


def test_ladder_validator_accepts_v_z_and_indexed_operands():
    ladder = indexed_ladder()
    assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


def test_index_registers_are_not_accepted_as_bit_contacts():
    ladder = indexed_ladder()
    ladder["rungs"][0]["shared_inputs"] = [
        {"type": "NO", "address": "Z0", "label": "非法位触点"}
    ]
    with pytest.raises(PLCJsonValidationError, match="expected prefix"):
        validate_ladder_full(ladder, plc_model="FX3U")


def test_invalid_index_range_is_not_silently_ignored():
    ladder = indexed_ladder()
    ladder["rungs"][1]["branches"][0]["outputs"] = [
        app("MOV", "D0", "D100Z8")
    ]
    with pytest.raises(PLCJsonValidationError, match="Z8"):
        validate_ladder_full(ladder, plc_model="FX3U")


def test_ir_tracks_index_register_as_read_dependency():
    reads, writes = analyze_instruction_access("MOV", ["D0", "D100Z0"])
    assert reads == ["D0", "Z0"]
    assert writes == ["D100"]

    reads, writes = analyze_instruction_access("MOV", ["D100V7", "D200"])
    assert reads == ["D100", "V7"]
    assert writes == ["D200"]

    reads, writes = analyze_instruction_access("MOV", ["K1", "Z0"])
    assert reads == []
    assert writes == ["Z0"]


def test_ir_and_st_renderer_preserve_indexed_operand_spelling():
    ladder = indexed_ladder()
    program = build_plc_ir(ladder, plc_model="FX3U")
    assert ir_to_ladder(program) == ladder
    assert program["devices"]["Z0"]["access"] == "read_write"
    assert program["devices"]["V7"]["access"] == "read"
    st_text = render_plc_ir_to_st(program)
    assert "D100Z0" in st_text
    assert "D100V7" in st_text


def test_confirmed_spec_accepts_fx3u_v_z_but_not_out_of_range_values():
    assert validate_spec_device("V0", "FX3U")[0] is None
    assert validate_spec_device("Z7", "FX3U")[0] is None
    assert validate_spec_device("Z8", "FX3U")[0] is not None
