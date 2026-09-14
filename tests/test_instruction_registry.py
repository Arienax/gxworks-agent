import pytest

from gxworks2.csv_importer import RawInstruction, _output_element
from instruction_registry import (
    DEFAULT_INSTRUCTION_REGISTRY,
    InstructionCategory,
    generation_app_instr_mnemonics,
)
from plc_ir import analyze_instruction_access
from plc_json_validator import (
    PLCJsonValidationError,
    find_unverified_app_instructions,
    validate_ladder_full,
)


def _ladder_with_app_instruction(opcode, operands):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 0,
                "debug_note": "",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": list(operands),
                                "label": "",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_registry_loads_common_and_model_specific_instructions():
    mov = DEFAULT_INSTRUCTION_REGISTRY.resolve("MOV")
    assert mov is not None
    assert mov.canonical_op == "MOVE"
    assert mov.category == InstructionCategory.ACTION
    assert mov.write_indexes == (1,)

    zrn = DEFAULT_INSTRUCTION_REGISTRY.resolve("ZRN")
    assert zrn is not None
    assert zrn.supports_cpu("FX3U")
    assert not zrn.supports_cpu("FX5U")

    drvtbl = DEFAULT_INSTRUCTION_REGISTRY.resolve("DRVTBL")
    assert drvtbl is not None
    assert drvtbl.supports_cpu("FX5U")
    assert not drvtbl.supports_cpu("FX3U")


def test_plc_ir_access_comes_from_registry_roles():
    reads, writes = analyze_instruction_access("MOV", ["D0", "D10"])
    assert reads == ["D0"]
    assert writes == ["D10"]

    reads, writes = analyze_instruction_access("INC", ["D20"])
    assert reads == ["D20"]
    assert writes == ["D20"]


def test_unknown_instruction_access_is_conservative():
    reads, writes = analyze_instruction_access(
        "FUTURE_VENDOR_OP", ["D0", "D10", "K1"]
    )
    assert reads == ["D0", "D10"]
    assert writes == []


def test_agent_validation_remains_strict_for_unknown_instruction():
    ladder = _ladder_with_app_instruction("FUTURE_VENDOR_OP", ["D0", "D10"])
    with pytest.raises(PLCJsonValidationError, match="unsupported APP_INSTR opcode"):
        validate_ladder_full(ladder, plc_model="FX3U")


def test_gx_import_validation_can_preserve_unknown_instruction():
    ladder = _ladder_with_app_instruction("FUTURE_VENDOR_OP", ["D0", "D10"])
    assert (
        validate_ladder_full(
            ladder,
            plc_model="FX3U",
            require_catalogued_instructions=False,
        )
        is ladder
    )
    findings = find_unverified_app_instructions(ladder)
    assert findings == [
        {
            "path": "$.rungs[0].branches[0].outputs[0]",
            "opcode": "FUTURE_VENDOR_OP",
            "operands": ["D0", "D10"],
            "status": "unverified",
            "edit_policy": "preserve_only",
        }
    ]


def test_known_instruction_still_checks_cpu_support():
    ladder = _ladder_with_app_instruction(
        "ZRN", ["K1000", "K100", "X0", "Y0"]
    )
    with pytest.raises(PLCJsonValidationError, match="ZRN is not supported by FX5U"):
        validate_ladder_full(ladder, plc_model="FX5U")


def test_csv_unknown_instruction_keeps_existing_app_instr_shape():
    raw = RawInstruction(
        step="10",
        op="FUTURE_VENDOR_OP",
        args=["D0", "D10"],
        label="vendor extension",
        source_row=4,
    )
    assert _output_element(raw, {}) == {
        "type": "APP_INSTR",
        "opcode": "FUTURE_VENDOR_OP",
        "operands": ["D0", "D10"],
        "label": "vendor extension",
    }


def test_mitsubishi_modifier_grammar_resolves_standard_and_irregular_forms():
    cases = {
        "MOV": ("MOV", False, False),
        "MOVP": ("MOV", False, True),
        "DMOV": ("MOV", True, False),
        "DMOVP": ("MOV", True, True),
        "ADDP": ("ADD", False, True),
        "DADDP": ("ADD", True, True),
        "DAND": ("WAND", True, False),
        "DANDP": ("WAND", True, True),
    }
    for opcode, (base, double, pulse) in cases.items():
        resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        assert resolved is not None, opcode
        assert resolved.base_mnemonic == base
        assert resolved.double is double
        assert resolved.pulse is pulse


def test_modifier_grammar_does_not_reinterpret_exact_d_prefixed_mnemonics():
    for opcode in ("DSW", "DECO", "DUTY"):
        resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        assert resolved is not None
        assert resolved.base_mnemonic == opcode
        assert resolved.double is False
        assert resolved.pulse is False
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_form("DDADDP") is None


def test_generation_enum_materializes_modifier_forms():
    fx3 = set(generation_app_instr_mnemonics("FX3U"))
    for opcode in (
        "MOVP",
        "DMOVP",
        "ADDP",
        "DADDP",
        "CMPP",
        "DCMPP",
        "BMOVP",
        "DFMOVP",
        "INCP",
        "DINCP",
        "DANDP",
    ):
        assert opcode in fx3
    assert "DDADDP" not in fx3


@pytest.mark.parametrize(
    "opcode,operands",
    [
        ("MOVP", ["D0", "D10"]),
        ("DMOVP", ["D0", "D10"]),
        ("ADDP", ["D0", "K1", "D10"]),
        ("DADDP", ["D0", "K1", "D10"]),
        ("DANDP", ["D0", "D2", "D10"]),
    ],
)
def test_validator_accepts_catalogued_modifier_forms(opcode, operands):
    ladder = _ladder_with_app_instruction(opcode, operands)
    assert validate_ladder_full(ladder, plc_model="FX3U") is ladder


def test_modifier_forms_reuse_registry_access_roles():
    reads, writes = analyze_instruction_access("DMOVP", ["D0", "D10"])
    assert reads == ["D0"]
    assert writes == ["D10"]

    reads, writes = analyze_instruction_access("DANDP", ["D0", "D2", "D10"])
    assert reads == ["D0", "D2"]
    assert writes == ["D10"]


def test_invalid_repeated_modifier_is_still_rejected():
    ladder = _ladder_with_app_instruction("DDADDP", ["D0", "K1", "D10"])
    with pytest.raises(PLCJsonValidationError, match="unsupported APP_INSTR opcode"):
        validate_ladder_full(ladder, plc_model="FX3U")
