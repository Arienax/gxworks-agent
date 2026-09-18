import copy

import pytest

from application.model_workflows import _normalize_analysis_result
from plc.ir import PLCIRValidationError, build_plc_ir, validate_plc_ir
from plc.semantics import (
    SUPPORTED_EXECUTION_SEMANTICS,
    infer_semantic_requirements,
    semantic_requirements_from_spec,
    strict_semantic_gaps,
)


def _input(kind, address, label=""):
    return {"type": kind, "address": address, "label": label}


def _compare(expression, label=""):
    return {"type": "BLOCK_INPUT", "expression": expression, "label": label}


def _instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def _coil(address, label=""):
    return {"type": "COIL", "address": address, "label": label}


def _rung(rung_id, *, header=None, inputs=None, outputs=None, note=""):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": header,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": inputs or [],
                "outputs": outputs or [],
            }
        ],
    }


def _ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def test_requirement_parser_distinguishes_all_six_scan_semantics():
    requirements = infer_semantic_requirements(
        "X0 持续接通时 Y0 输出；每次按下 X1 一次计数；"
        "每次松开 X2 一次记录；上电初始化 D0；"
        "每隔 100ms 周期执行采样；X3 触发中断任务。"
    )

    assert {item["semantic"] for item in requirements} == set(
        SUPPORTED_EXECUTION_SEMANTICS
    )
    cyclic = next(item for item in requirements if item["semantic"] == "CYCLIC")
    assert cyclic["period_ms"] == 100.0
    rising = next(item for item in requirements if item["semantic"] == "RISING_EDGE")
    assert rising["devices"] == ["X1"]


def test_requirement_parser_preserves_only_explicit_physical_input_pulse_width():
    requirements = infer_semantic_requirements(
        "X0 输入脉宽 50us 时 INC D0；Y0 输出脉宽 20ms；"
        "T0 延时 2ms；每隔 10ms 周期采样。"
    )

    pulse = [item for item in requirements if item.get("pulse_width_ms") is not None]
    assert pulse == [
        {
            "semantic": "RISING_EDGE",
            "devices": ["X0"],
            "evidence": "X0 输入脉宽 50us 时 INC D0",
            "source": "requirement",
            "strict": True,
            "pulse_width_ms": 0.05,
        }
    ]
    cyclic = next(item for item in requirements if item["semantic"] == "CYCLIC")
    assert cyclic["period_ms"] == 10.0


def test_ir_annotates_level_edges_first_scan_cycle_and_interrupt():
    ladder = _ladder(
        _rung(1, inputs=[_input("NO", "X0")], outputs=[_coil("Y0")]),
        _rung(2, inputs=[_input("P", "X1")], outputs=[_instruction("INC", ["D0"])]),
        _rung(3, inputs=[_input("F", "X2")], outputs=[_coil("M0")]),
        _rung(
            4,
            inputs=[_input("P", "M8002", "上电初始化")],
            outputs=[_instruction("MOV", ["K1", "D10"], "初始化状态")],
            note="开机初始化默认状态",
        ),
        _rung(5, inputs=[_input("NO", "M8012")], outputs=[_coil("M1")]),
        _rung(6, inputs=[_input("NO", "X3", "中断输入")], outputs=[_coil("M2")]),
    )
    program = build_plc_ir(ladder, plc_model="FX3U")

    by_id = {item["id"]: item for item in program["networks"]}
    assert by_id["N0001"]["execution"]["semantics"] == ["LEVEL"]
    assert by_id["N0002"]["execution"]["semantics"] == ["RISING_EDGE"]
    assert by_id["N0003"]["execution"]["semantics"] == ["FALLING_EDGE"]
    assert by_id["N0004"]["execution"]["semantics"] == ["FIRST_SCAN"]
    assert by_id["N0005"]["execution"]["semantics"] == ["CYCLIC"]
    assert by_id["N0006"]["execution"]["execution_context"] == "INTERRUPT"
    assert program["timing"]["first_scan_networks"] == ["N0004"]
    assert program["timing"]["cyclic_sources"][0]["period_ms"] == 100.0
    assert program["timing"]["interrupt_networks"] == ["N0006"]
    assert validate_plc_ir(program) is program


def test_strict_requirement_coverage_blocks_level_instead_of_requested_edge():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("NO", "X0")],
            outputs=[_instruction("INC", ["D0"])],
        )
    )
    requirements = infer_semantic_requirements("每次按下 X0 一次，D0 加一")
    program = build_plc_ir(ladder, semantic_requirements=requirements)

    assert program["timing"]["coverage"][0]["status"] == "unresolved"
    assert strict_semantic_gaps(program) == [program["timing"]["coverage"][0]]

    edge_ladder = copy.deepcopy(ladder)
    edge_ladder["rungs"][0]["branches"][0]["inputs"][0]["type"] = "P"
    edge_program = build_plc_ir(edge_ladder, semantic_requirements=requirements)
    assert strict_semantic_gaps(edge_program) == []


def test_first_scan_requirement_rejects_m8000_continuous_initialization_semantically():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("NO", "M8000", "运行常通")],
            outputs=[_instruction("MOV", ["K100", "D100"], "初始化默认参数")],
            note="上电初始化默认参数",
        )
    )
    requirements = infer_semantic_requirements("上电初始化 D100 默认参数")
    program = build_plc_ir(ladder, semantic_requirements=requirements)

    assert program["timing"]["initialization"] == [
        {
            "network": "N0001",
            "status": "continuous_overwrite_risk",
            "trigger_devices": ["M8000"],
            "writes": ["D100"],
        }
    ]
    assert strict_semantic_gaps(program)[0]["semantic"] == "FIRST_SCAN"


def test_state_machine_is_structured_with_separate_transition_and_output_regions():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("P", "M8002", "首扫")],
            outputs=[_instruction("MOV", ["K10", "D100"], "初始化 IDLE")],
        ),
        _rung(
            2,
            header=_compare("= D100 K10", "IDLE"),
            inputs=[_input("P", "X0", "启动沿")],
            outputs=[_instruction("MOV", ["K20", "D100"], "进入 CLAMP")],
        ),
        _rung(
            3,
            header=_compare("= D100 K20", "CLAMP"),
            inputs=[_input("NO", "X1", "夹紧到位")],
            outputs=[_instruction("MOV", ["K30", "D100"], "进入 PROCESS")],
        ),
        _rung(
            4,
            header=_compare("= D100 K20", "CLAMP"),
            outputs=[_coil("Y0", "夹紧输出")],
        ),
        _rung(
            5,
            header=_compare("= D100 K30", "PROCESS"),
            inputs=[_input("NO", "X2", "加工完成")],
            outputs=[_instruction("MOV", ["K10", "D100"], "返回 IDLE")],
        ),
    )
    program = build_plc_ir(ladder)
    state_machine = program["logic"]["state_machines"][0]

    assert state_machine["state_register"] == "D100"
    assert [item["value"] for item in state_machine["states"]] == [10, 20, 30]
    assert state_machine["initialization"][0]["target_state"] == 10
    assert {(item["from"], item["to"]) for item in state_machine["transitions"]} == {
        (10, 20),
        (20, 30),
        (30, 10),
    }
    assert state_machine["state_outputs"][0]["op"] == "COIL"
    transition_region = next(
        item for item in program["logic"]["regions"] if item["kind"] == "STATE_TRANSITION"
    )
    output_region = next(
        item for item in program["logic"]["regions"] if item["kind"] == "STATE_OUTPUT"
    )
    assert set(transition_region["network_refs"]) == {"N0001", "N0002", "N0003", "N0005"}
    assert output_region["network_refs"] == ["N0004"]
    assert state_machine["unreachable_state_candidates"] == []
    assert state_machine["dead_end_state_candidates"] == []


def test_ir_validation_detects_tampered_execution_or_logic_analysis():
    program = build_plc_ir(
        _ladder(_rung(1, inputs=[_input("P", "X0")], outputs=[_coil("M0")]))
    )
    tampered = copy.deepcopy(program)
    tampered["networks"][0]["execution"]["semantics"] = ["LEVEL"]
    with pytest.raises(PLCIRValidationError, match="networks is stale|networks.*stale"):
        validate_plc_ir(tampered)

    tampered = copy.deepcopy(program)
    tampered["logic"]["execution_model"] = "invented"
    with pytest.raises(PLCIRValidationError, match="logic is stale"):
        validate_plc_ir(tampered)


def test_confirmed_spec_semantics_survive_without_becoming_parameters():
    requirements = semantic_requirements_from_spec(
        {
            "summary": "每次按下 X0 一次，计数加一",
            "execution_semantics": [
                {
                    "semantic": "RISING_EDGE",
                    "devices": ["X0"],
                    "evidence": "用户确认",
                    "strict": True,
                }
            ],
        }
    )

    assert len(requirements) == 2
    assert all(item["semantic"] == "RISING_EDGE" for item in requirements)
    assert {item["source"] for item in requirements} == {"requirement", "confirmed_spec"}


def test_analysis_drops_model_invented_semantics_and_keeps_user_evidence():
    hallucinated = _normalize_analysis_result(
        {
            "summary": "普通输出控制",
            "execution_semantics": [
                {
                    "semantic": "FIRST_SCAN",
                    "devices": [],
                    "evidence": "模型自行假设",
                    "strict": True,
                }
            ],
        },
        user_text="X0 控制 Y0",
    )
    assert hallucinated["execution_semantics"] == []

    evidenced = _normalize_analysis_result(
        {"summary": "计数", "execution_semantics": []},
        user_text="每次按下 X0 一次，INC D0",
    )
    assert evidenced["execution_semantics"][0]["semantic"] == "RISING_EDGE"
    assert evidenced["execution_semantics"][0]["devices"] == ["X0"]
