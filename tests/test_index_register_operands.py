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


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC', 'FX3U/FX3UC'])
@pytest.mark.parametrize('address', ['X0', 'X0Z0', 'X0V7'])
def test_input_write_policy_cannot_be_bypassed_by_an_index_register(cpu, address):
    from plc.validation import _validate_writable_device
    with pytest.raises(PLCJsonValidationError, match='input device'):
        _validate_writable_device(address, '$.output', cpu)


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


def test_issue22_ql_indexed_w_r_b_operands_validate_and_track_ir(tmp_path):
    from agent_runtime.messages import ToolCall
    from application.engineering_session import EngineeringSession
    from plc.core import PLCCore
    from plc.ir import validate_plc_ir
    from plc.ordered_source import validate_ordered_rung

    issue22_instructions = [
        {"op": "LD", "args": ["B600Z0"]},
        {"op": "AND", "args": ["B600Z0"]},
        {"op": "MOV", "args": ["D0", "W900Z0"]},
        {"op": "MOV", "args": ["D0", "W10Z1"]},
        {"op": "MOV", "args": ["D0", "W1A0Z0"]},
        {"op": "MOV", "args": ["D0", "R100Z0"]},
        {"op": "MOV", "args": ["W107Z0", "D0"]},
        {"op": "MOV", "args": ["R100Z0", "D0"]},
        {"op": "-", "args": ["D3730Z0", "K1800", "W930Z0"]},
        {"op": "OUT", "args": ["B910Z0"]},
        {"op": "OUT", "args": ["B9AFZ0"]},
        {"op": "MOV", "args": ["D0", "K4B0Z0"]},
        {"op": "MOV", "args": ["D0", "W900ZZ0"]},
    ]
    ordered_rung = {"rung_id": 1, "ordered_instructions": issue22_instructions}
    validate_ordered_rung(ordered_rung, plc_model="Q03UDV")

    typed_coil_rung = {
        "rung_id": 2,
        "branches": [
            {
                "branch_id": 1,
                "inputs": [{"id": "e1", "type": "NO", "address": "M0"}],
                "outputs": [{"id": "e2", "type": "COIL", "address": "B910Z0"}],
            }
        ],
    }
    typed_combo_rung = {
        "rung_id": 3,
        "branches": [
            {
                "branch_id": 1,
                "inputs": [{"id": "e3", "type": "NO", "address": "B600Z0"}],
                "outputs": [
                    {"id": "e4", "type": "COIL", "address": "B9AFZ0"},
                    app("MOV", "D0", "W900Z0"),
                ],
            }
        ],
    }
    ladder = {"device_comments": {}, "rungs": [ordered_rung, typed_coil_rung, typed_combo_rung]}
    validate_ladder_full(ladder, plc_model="Q03UDV")
    program = build_plc_ir(ladder, plc_model="Q03UDV")
    validate_plc_ir(program, validate_ladder=True)

    net0, net1, net2 = program["networks"]
    assert set(net0["reads"]) == {"B600", "D0", "D3730", "R100", "W107", "Z0", "Z1"}
    assert set(net0["writes"]) == {"B0", "B910", "B9AF", "D0", "R100", "W10", "W1A0", "W900", "W930"}
    assert "Z0" not in net0["writes"]

    assert net1["reads"] == ["M0", "Z0"]
    assert net1["writes"] == ["B910"]

    assert set(net2["reads"]) == {"B600", "D0", "Z0"}
    assert set(net2["writes"]) == {"B9AF", "W900"}

    # Full program with K4B0Z0 and W900ZZ0 produces 0 errors (only unverified preservation findings).
    core = PLCCore()
    full_validation = core.validate_project(program)
    assert full_validation["counts"]["error"] == 0

    # Single-index W/R/B operands pass PLCCore cleanly with valid=True.
    single_index_ladder = {
        "device_comments": {},
        "rungs": [
            {"rung_id": 1, "ordered_instructions": issue22_instructions[:-2]},
            typed_coil_rung,
            typed_combo_rung,
        ],
    }
    single_validation = core.validate_project(build_plc_ir(single_index_ladder, plc_model="Q03UDV"))
    assert single_validation["valid"] is True
    assert single_validation["counts"]["error"] == 0

    # Project/session loading must succeed without CONTEXT_UNAVAILABLE.
    with EngineeringSession(tmp_path / "ws", state_dir=tmp_path / "state") as session:
        store = session.service.projects.store
        created = session.invoke(
            ToolCall("c1", "create_project", {"name": "Issue22", "plc_model": "Q03UDV", "target_mode": "ladder"})
        )
        assert not created.is_error, created.content
        pid = session.project_id
        vid, vdir = store.prepare_version(pid)
        store._write_json(vdir / "program.ir.json", program)
        meta = dict(
            store._ir_metadata(program),
            artifacts={"ir": "program.ir.json"},
            target_mode="ladder",
            plc_model="Q03UDV",
            summary="issue22",
            source="test",
        )
        store.complete_version(pid, vid, meta, activate=True)
        session.version_id = vid
        selected = session.invoke(ToolCall("c2", "select_project", {"project_id": pid}))
        artifacts = session.invoke(ToolCall("c3", "get_project_artifacts", {}))
        loaded_ir = session.service.projects.program(pid, vid)
        assert not selected.is_error and selected.data.get("ok")
        assert not artifacts.is_error and artifacts.data.get("ok")
        assert len(loaded_ir["networks"]) == 3


def test_valid_ql_and_fx_indexed_devices_and_ir_dependencies():
    from plc.ir import validate_plc_ir
    from plc.ordered_source import validate_ordered_rung

    for model in ("Q03UDV", "L02"):
        ladder = {
            "device_comments": {},
            "rungs": [
                {
                    "rung_id": 1,
                    "ordered_instructions": [
                        {"op": "LD", "args": ["X1FZ0"]},
                        {"op": "AND", "args": ["V0Z1"]},
                        {"op": "AND", "args": ["SB10Z0"]},
                        {"op": "AND", "args": ["F10Z0"]},
                        {"op": "MOV", "args": ["K4X10Z0", "ZR100Z0"]},
                        {"op": "MOV", "args": ["SW10Z0", "W100Z0"]},
                        {"op": "OUT", "args": ["L100Z0"]},
                        {"op": "MOV", "args": ["D0", "K4L100Z0"]},
                        {"op": "OUT", "args": ["Y2AFZ0"]},
                    ],
                }
            ],
        }
        validate_ordered_rung(ladder["rungs"][0], plc_model=model)
        validate_ladder_full(ladder, plc_model=model)
        ir = build_plc_ir(ladder, plc_model=model)
        validate_plc_ir(ir, validate_ladder=True)
        assert set(ir["networks"][0]["reads"]) == {"D0", "F10", "SB10", "SW10", "V0", "X10", "X1F", "Z0", "Z1"}
        assert set(ir["networks"][0]["writes"]) == {"L100", "W100", "Y2AF", "ZR100"}

    for fx_model in ("FX3U", "FX3G"):
        fx_ladder = {
            "device_comments": {},
            "rungs": [
                {
                    "rung_id": 1,
                    "ordered_instructions": [
                        {"op": "LD", "args": ["M0"]},
                        {"op": "MOV", "args": ["D0", "R100Z0"]},
                        {"op": "MOV", "args": ["R100V0", "D10V1"]},
                    ],
                }
            ],
        }
        validate_ordered_rung(fx_ladder["rungs"][0], plc_model=fx_model)
        validate_ladder_full(fx_ladder, plc_model=fx_model)
        fx_ir = build_plc_ir(fx_ladder, plc_model=fx_model)
        validate_plc_ir(fx_ir, validate_ladder=True)
        assert set(fx_ir["networks"][0]["reads"]) == {"D0", "M0", "R100", "V0", "V1", "Z0"}
        assert set(fx_ir["networks"][0]["writes"]) == {"D10", "R100"}


def test_canonical_operand_normalizes_indexed_w_b_r_without_mangling_x00a():
    from plc.device_identity import canonical_operand

    assert canonical_operand("W0900Z00") == "W900Z0"
    assert canonical_operand("W01A0Z00") == "W1A0Z0"
    assert canonical_operand("B09AFZ00") == "B9AFZ0"
    assert canonical_operand("R0100Z00") == "R100Z0"
    assert canonical_operand("W01A0") == "W1A0"
    assert canonical_operand("B09AF") == "B9AF"
    assert canonical_operand("X00A") == "X00A"
    assert canonical_operand("bad") == "bad"


def test_forbidden_indexed_operands_remain_rejected():
    from plc.ordered_source import validate_ordered_rung
    from plc.validation import DEVICE_LIMITS

    w_max = format(DEVICE_LIMITS["Q03UDV"]["W"], "X")
    w_over = format(DEVICE_LIMITS["Q03UDV"]["W"] + 1, "X")
    b_max = format(DEVICE_LIMITS["Q03UDV"]["B"], "X")
    b_over = format(DEVICE_LIMITS["Q03UDV"]["B"] + 1, "X")

    validate_ordered_rung(
        {"rung_id": 1, "ordered_instructions": [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", f"W{w_max}Z0"]}, {"op": "OUT", "args": [f"B{b_max}Z0"]}]},
        plc_model="Q03UDV",
    )

    rejected_cases = [
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "D0V0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "W900V0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "K4B0V0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "D0Z20"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", f"W{w_over}Z0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "OUT", "args": [f"B{b_over}Z0"]}]),
        ("Q00J", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "R100Z0"]}]),
        ("Q00UJ", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "ZR100Z0"]}]),
        ("FX3U", [{"op": "LD", "args": ["V0Z0"]}, {"op": "OUT", "args": ["Y0"]}]),
        ("FX3U", [{"op": "LD", "args": ["X00AZ0"]}, {"op": "OUT", "args": ["Y0"]}]),
        ("FX3U", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "D0Z8"]}]),
        ("FX3U", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "D8512Z0"]}]),
        ("FX3U", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "W0Z0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "Z0Z1"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["D0.0Z0"]}, {"op": "OUT", "args": ["Y0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["D0Z0.0"]}, {"op": "OUT", "args": ["Y0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "OUT", "args": ["P0Z0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "OUT", "args": ["ER0Z0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "OUT", "args": ["X0Z0"]}]),
        ("Q03UDV", [{"op": "LD", "args": ["M0"]}, {"op": "MOV", "args": ["D0", "K4X0Z0"]}]),
        ("FX3U", [{"op": "LD", "args": ["M0"]}, {"op": "OUT", "args": ["M8000Z0"]}]),
    ]
    for model, instructions in rejected_cases:
        with pytest.raises(PLCJsonValidationError):
            validate_ordered_rung({"rung_id": 1, "ordered_instructions": instructions}, plc_model=model)

    for output in (
        {"id": "e2", "type": "COIL", "address": "SM0Z0"},
        {"id": "e2", "type": "COIL", "address": "SM400Z0"},
        app("MOV", "D0", "SD0Z0"),
    ):
        typed_ladder = {
            "device_comments": {},
            "rungs": [
                {
                    "rung_id": 1,
                    "branches": [
                        {"branch_id": 1, "inputs": [{"id": "e1", "type": "NO", "address": "M0"}], "outputs": [output]}
                    ],
                }
            ],
        }
        with pytest.raises(PLCJsonValidationError):
            validate_ladder_full(typed_ladder, plc_model="Q03UDV")


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
