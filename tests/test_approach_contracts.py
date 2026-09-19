import copy

import pytest

from application.model_api import _normalize_analysis_result, _routing_text_with_selected_approach
from plc.specification.approach import (
    format_contract_summary,
    inspect_ladder_features,
    normalize_approach,
    validate_ladder_against_selected_approach,
)
from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec, validate_spec_draft
from knowledge.patterns import classify_request
from plc.validation import PLCJsonValidationError, validate_ladder_full


def _branch(inputs, outputs):
    return {
        "branch_id": 1,
        "y_offset_level": 0,
        "inputs": inputs,
        "outputs": outputs,
    }


def _register_state_machine():
    return {
        "device_comments": {"D0": "主状态", "Y0": "运行输出"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "M8002", "label": "首扫"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["K1", "D0"],
                                "label": "初始化待机",
                            }
                        ],
                    )
                ],
            },
            {
                "rung_id": 2,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K1",
                    "label": "待机",
                },
                "branches": [
                    _branch(
                        [{"type": "P", "address": "X0", "label": "启动"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["K2", "D0"],
                                "label": "转运行",
                            }
                        ],
                    )
                ],
            },
            {
                "rung_id": 3,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K2",
                    "label": "运行",
                },
                "branches": [
                    _branch(
                        [{"type": "P", "address": "X1", "label": "停止"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["K1", "D0"],
                                "label": "回待机",
                            }
                        ],
                    )
                ],
            },
            {
                "rung_id": 4,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K2",
                    "label": "运行",
                },
                "branches": [
                    _branch(
                        [],
                        [{"type": "COIL", "address": "Y0", "label": "运行输出"}],
                    )
                ],
            },
        ],
    }


def _direct_logic():
    return {
        "device_comments": {"X0": "启动", "Y0": "输出"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "X0", "label": "启动"}],
                        [{"type": "COIL", "address": "Y0", "label": "输出"}],
                    )
                ],
            }
        ],
    }


def _hardware_counter():
    return {
        "device_comments": {"X2": "检测", "C0": "计数", "Y0": "满料"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "X2", "label": "检测"}],
                        [
                            {
                                "type": "COUNTER",
                                "address": "C0",
                                "value": "K5",
                                "label": "计5件",
                            }
                        ],
                    )
                ],
            },
            {
                "rung_id": 2,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "C0", "label": "到数"}],
                        [{"type": "COIL", "address": "Y0", "label": "满料"}],
                    )
                ],
            },
        ],
    }


def _data_register_counter():
    return {
        "device_comments": {"X2": "检测", "D0": "计数"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "P", "address": "X2", "label": "检测边沿"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "INC",
                                "operands": ["D0"],
                                "label": "计数加一",
                            }
                        ],
                    )
                ],
            }
        ],
    }


def _approach(name, guide, contract):
    return normalize_approach(
        {
            "name": name,
            "description": name,
            "generation_guide": guide,
            "generation_contract": contract,
        }
    )


def test_analysis_normalizes_every_candidate_into_an_enforced_contract():
    result = _normalize_analysis_result(
        {
            "summary": "计数",
            "approaches": [
                {
                    "name": "硬件计数器法",
                    "generation_guide": "NO X2驱动OUT C0 K5",
                    "generation_contract": {
                        "required_structures": ["hardware_counter"],
                        "forbidden_structures": ["data_register_counter"],
                    },
                }
            ],
            "suggested_io": {},
            "missing_info": [],
        },
        "FX3U",
        "X2每次检测计数",
    )

    candidate = result["approaches"][0]
    assert candidate["approach_id"].startswith("approach_")
    assert candidate["generation_contract"]["enforce"] is True
    assert "hardware_counter" in candidate["generation_contract"][
        "required_structures"
    ]


def test_confirmed_contract_survives_review_and_canonicalization():
    analysis = {
        "approaches": [
            {
                "approach_id": "counter_c0",
                "name": "硬件计数器法",
                "generation_guide": "使用C0硬件计数器",
                "generation_contract": {
                    "required_structures": ["hardware_counter"],
                    "forbidden_structures": ["data_register_counter"],
                },
            }
        ]
    }
    draft = build_review_draft(analysis)
    canonical = canonicalize_confirmed_spec(draft)

    assert canonical["selected_approach"]["approach_id"] == "counter_c0"
    assert canonical["selected_approach"]["generation_contract"] == canonical[
        "approaches"
    ][0]["generation_contract"]
    assert validate_spec_draft(canonical, "FX3U")["errors"] == []


def test_duplicate_candidate_contracts_do_not_block_confirmation():
    first = _approach(
        "方案一",
        "直接逻辑",
        {"required_structures": ["direct_logic"]},
    )
    second = copy.deepcopy(first)
    second["approach_id"] = "second"
    second["name"] = "方案二"
    second["generation_guide"] = "同一硬约束下使用不同的数据组织与生成步骤"
    spec = {
        "plc_model": "FX3U",
        "approaches": [first, second],
        "selected_approach": first,
        "parameters": [],
        "io_table": [],
    }

    issues = validate_spec_draft(spec, "FX3U")

    assert issues["errors"] == []
    assert not any(item["code"] == "duplicate_approach_contract" for item in issues["warnings"])


def test_unselected_candidate_contract_problem_is_advisory_only():
    selected = _approach(
        "可用方案",
        "直接逻辑",
        {"required_structures": ["direct_logic"]},
    )
    broken = _approach(
        "未选坏方案",
        "模型候选里出现自相矛盾的结构约束",
        {
            "required_structures": ["register_state_machine"],
            "forbidden_structures": ["register_state_machine"],
        },
    )
    spec = {
        "plc_model": "FX3U",
        "approaches": [selected, broken],
        "selected_approach": selected,
        "parameters": [],
        "io_table": [],
    }

    issues = validate_spec_draft(spec, "FX3U")

    assert issues["errors"] == []
    assert any(
        item["code"] == "candidate_approach_contract_warning"
        for item in issues["warnings"]
    )


def test_selected_approach_may_be_confirmed_even_if_not_in_candidate_list():
    candidate = _approach(
        "候选方案",
        "候选实现",
        {"required_structures": ["direct_logic"]},
    )
    selected = _approach(
        "用户当前方案",
        "用户直接修改后的独立实现",
        {"required_structures": ["register_state_machine"]},
    )
    selected["approach_id"] = "user-current"
    spec = {
        "plc_model": "FX3U",
        "approaches": [candidate],
        "selected_approach": selected,
        "parameters": [],
        "io_table": [],
    }

    issues = validate_spec_draft(spec, "FX3U")

    assert issues["errors"] == []
    assert any(
        item["code"] == "selected_approach_not_in_candidates"
        and item.get("blocking") is False
        for item in issues["warnings"]
    )


def test_selected_self_contradictory_contract_still_fails_before_model_call():
    selected = _approach(
        "矛盾方案",
        "用户明确写出的不可同时满足约束",
        {
            "required_structures": ["direct_logic"],
            "forbidden_structures": ["direct_logic"],
        },
    )
    issues = validate_spec_draft(
        {
            "plc_model": "FX3U",
            "approaches": [selected],
            "selected_approach": selected,
            "parameters": [],
            "io_table": [],
        },
        "FX3U",
    )

    assert any(item["code"] == "invalid_approach_contract" for item in issues["errors"])


def test_selected_register_state_machine_is_enforced_by_full_validator():
    selected = _approach(
        "步进状态机法",
        "M8002执行MOV K1 D0初始化；BLOCK_INPUT比较D0；MOV状态转移",
        {
            "required_opcodes": ["MOV"],
            "required_devices": ["M8002", "D0"],
            "required_structures": [
                "register_state_machine",
                "state_initialization",
                "state_comparison",
                "state_transition",
            ],
            "forbidden_structures": ["bit_state_machine"],
        },
    )
    spec = {"selected_approach": selected}

    assert validate_ladder_full(_register_state_machine(), "FX3U", spec)
    with pytest.raises(PLCJsonValidationError, match="不符合用户选择"):
        validate_ladder_full(_direct_logic(), "FX3U", spec)


def test_out_contract_summary_explains_the_typed_json_representation():
    approach = _approach(
        "普通输出法",
        "用输出线圈驱动 Y0",
        {"required_opcodes": ["OUT"]},
    )

    summary = format_contract_summary(approach)

    assert "OUT（用 COIL/TIMER/COUNTER 表示）" in summary


def test_legacy_prose_scheme_does_not_retroactively_invalidate_saved_version():
    legacy_approach = {
        "name": "状态机法",
        "description": "用M状态位区分待机、运行、满料三个状态",
        "generation_guide": (
            "用M1待机、M2运行、M3满料；状态转移用SET/RST或MOV"
        ),
    }

    inferred = normalize_approach(legacy_approach)["generation_contract"]
    assert "bit_state_machine" in inferred["required_structures"]
    assert inferred["required_devices"] == []

    # Historical confirmed specs did not persist generation_contract.  They
    # remain readable even if their old generated ladder used other state bits.
    assert validate_ladder_full(
        _direct_logic(),
        "FX3U",
        {"selected_approach": legacy_approach},
    )


def test_counter_methods_cannot_silently_substitute_for_each_other():
    hardware = _approach(
        "硬件计数器法",
        "X2驱动OUT C0 K5，使用硬件计数器",
        {
            "required_structures": ["hardware_counter"],
            "forbidden_structures": ["data_register_counter"],
        },
    )
    data = _approach(
        "D寄存器计数法",
        "X2上升沿执行INC D0",
        {
            "required_opcodes": ["INC"],
            "required_structures": ["data_register_counter", "edge_trigger"],
            "forbidden_structures": ["hardware_counter"],
        },
    )

    assert not validate_ladder_against_selected_approach(
        _hardware_counter(), {"selected_approach": hardware}
    )
    assert validate_ladder_against_selected_approach(
        _data_register_counter(), {"selected_approach": hardware}
    )
    assert not validate_ladder_against_selected_approach(
        _data_register_counter(), {"selected_approach": data}
    )
    assert validate_ladder_against_selected_approach(
        _hardware_counter(), {"selected_approach": data}
    )


def test_motion_instruction_choice_is_enforced_globally():
    drvi = _approach(
        "DRVI相对定位",
        "使用DRVI相对定位",
        {
            "required_opcodes": ["DRVI"],
            "forbidden_opcodes": ["PLSY"],
            "required_structures": ["pulse_positioning"],
        },
    )
    plsy_ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "X0", "label": "启动"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "PLSY",
                                "operands": ["K1000", "K200", "Y0"],
                                "label": "脉冲输出",
                            }
                        ],
                    )
                ],
            }
        ],
    }

    issues = validate_ladder_against_selected_approach(
        plsy_ladder, {"selected_approach": drvi}
    )

    assert any("DRVI" in item for item in issues)
    assert any("PLSY" in item for item in issues)


def test_selected_approach_participates_in_pattern_routing():
    selected = _approach(
        "步进状态机法",
        "M8002执行MOV K1 D0初始化，BLOCK_INPUT区分步骤",
        {"required_structures": ["register_state_machine"]},
    )
    routed_text = _routing_text_with_selected_approach(
        "传送带检测五个工件后停止",
        {"selected_approach": selected},
    )
    classification = classify_request(routed_text, target_mode="ladder")

    assert "pattern_c" in classification["matched_ids"]


def test_legacy_m_bit_state_plan_detects_substituted_state_device():
    selected = _approach(
        "状态机法",
        "用M1待机、M2运行、M3满料；状态转移用SET/RST",
        {},
    )
    ladder = _register_state_machine()
    features = inspect_ladder_features(ladder)

    assert "register_state_machine" in features["structures"]
    issues = validate_ladder_against_selected_approach(
        ladder, {"selected_approach": selected}
    )
    assert any("M/S位状态机" in item for item in issues)


def test_fx3u_adapter_buffer_registers_are_recognized_as_analog_control():
    ladder = {
        "device_comments": {
            "D200": "PID输出值",
            "D8270": "FX3U-4DA-ADP通道1输出数据",
        },
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    _branch(
                        [{"type": "NO", "address": "M8000", "label": "常通"}],
                        [
                            {
                                "type": "APP_INSTR",
                                "opcode": "MOV",
                                "operands": ["D200", "D8270"],
                                "label": "写入模拟量输出适配器",
                            }
                        ],
                    )
                ],
            }
        ],
    }

    features = inspect_ladder_features(ladder)

    assert "analog_control" in features["structures"]


def test_register_state_machine_does_not_treat_normal_latches_as_bit_state_machine():
    ladder = _register_state_machine()
    ladder["device_comments"].update(
        {
            "M0": "自动模式标志",
            "M1": "变频器运行锁存",
            "X2": "自动模式选择",
            "X3": "退出自动模式",
            "X4": "运行请求",
            "X5": "停止请求",
        }
    )
    ladder["rungs"].extend(
        [
            {
                "rung_id": 5,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X2", "label": "自动模式选择"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "SET",
                        "operands": ["M0"],
                        "label": "锁存自动模式",
                    }],
                )],
            },
            {
                "rung_id": 6,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X3", "label": "退出自动模式"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "RST",
                        "operands": ["M0"],
                        "label": "复位自动模式",
                    }],
                )],
            },
            {
                "rung_id": 7,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X4", "label": "运行请求"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "SET",
                        "operands": ["M1"],
                        "label": "锁存运行",
                    }],
                )],
            },
            {
                "rung_id": 8,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X5", "label": "停止请求"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "RST",
                        "operands": ["M1"],
                        "label": "复位运行",
                    }],
                )],
            },
            {
                "rung_id": 9,
                "header_element": None,
                "branches": [_branch(
                    [
                        {"type": "NO", "address": "M0", "label": "自动模式"},
                        {"type": "NO", "address": "M1", "label": "运行许可"},
                    ],
                    [{"type": "COIL", "address": "Y1", "label": "运行输出"}],
                )],
            },
        ]
    )

    features = inspect_ladder_features(ladder)

    assert "register_state_machine" in features["structures"]
    assert "bit_state_machine" not in features["structures"]
    assert features["state_bits"] == []


def test_real_bit_state_machine_is_still_detected_without_register_state_machine():
    ladder = {
        "device_comments": {"M1": "待机状态", "M2": "运行状态"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X0", "label": "启动"}],
                    [
                        {
                            "type": "APP_INSTR",
                            "opcode": "SET",
                            "operands": ["M2"],
                            "label": "进入运行",
                        },
                        {
                            "type": "APP_INSTR",
                            "opcode": "RST",
                            "operands": ["M1"],
                            "label": "退出待机",
                        },
                    ],
                )],
            },
            {
                "rung_id": 2,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "X1", "label": "停止"}],
                    [
                        {
                            "type": "APP_INSTR",
                            "opcode": "SET",
                            "operands": ["M1"],
                            "label": "进入待机",
                        },
                        {
                            "type": "APP_INSTR",
                            "opcode": "RST",
                            "operands": ["M2"],
                            "label": "退出运行",
                        },
                    ],
                )],
            },
            {
                "rung_id": 3,
                "header_element": None,
                "branches": [_branch(
                    [
                        {"type": "NO", "address": "M1", "label": "待机"},
                        {"type": "NC", "address": "M2", "label": "非运行"},
                    ],
                    [{"type": "COIL", "address": "Y0", "label": "待机灯"}],
                )],
            },
        ],
    }

    features = inspect_ladder_features(ladder)

    assert "bit_state_machine" in features["structures"]
    assert features["state_bits"] == ["M1", "M2"]


def test_pid_analog_register_state_contract_accepts_normal_mode_and_alarm_latches():
    selected = _approach(
        "恒压供水PID控制法（模拟量0-10V）",
        "D0寄存器状态机；PID D100 D8260 D110 D200；MOV D200 D8270",
        {
            "required_opcodes": ["MOV", "CMP", "PID", "SET", "RST", "INC"],
            "required_devices": ["M8002", "D0", "D100", "D110", "D200", "C0"],
            "required_structures": [
                "register_state_machine",
                "state_initialization",
                "state_comparison",
                "state_transition",
                "analog_control",
                "pid_control",
                "edge_trigger",
            ],
            "forbidden_structures": ["bit_state_machine", "vfd_multi_speed"],
        },
    )
    ladder = {
        "device_comments": {
            "D0": "主流程寄存器",
            "D200": "PID输出值",
            "D8270": "FX3U-4DA-ADP通道1输出",
            "M3": "低水位报警锁存",
            "M5": "手动运行锁存",
        },
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "NO", "address": "M8002", "label": "首次扫描"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "MOV",
                        "operands": ["K0", "D0"],
                        "label": "初始化主流程",
                    }],
                )],
            },
            {
                "rung_id": 2,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K0",
                    "label": "停止态",
                },
                "branches": [_branch(
                    [{"type": "P", "address": "X0", "label": "自动启动沿"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "MOV",
                        "operands": ["K1", "D0"],
                        "label": "进入启动延时",
                    }],
                )],
            },
            {
                "rung_id": 3,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K1",
                    "label": "启动延时态",
                },
                "branches": [_branch(
                    [{"type": "NO", "address": "T0", "label": "延时完成"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "MOV",
                        "operands": ["K2", "D0"],
                        "label": "进入PID运行",
                    }],
                )],
            },
            {
                "rung_id": 4,
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K2",
                    "label": "PID运行态",
                },
                "branches": [_branch(
                    [],
                    [
                        {
                            "type": "APP_INSTR",
                            "opcode": "CMP",
                            "operands": ["D0", "K2", "M10"],
                            "label": "流程状态比较",
                        },
                        {
                            "type": "APP_INSTR",
                            "opcode": "PID",
                            "operands": ["D100", "D8260", "D110", "D200"],
                            "label": "压力闭环调节",
                        },
                        {
                            "type": "APP_INSTR",
                            "opcode": "MOV",
                            "operands": ["D200", "D8270"],
                            "label": "输出0-10V给定",
                        },
                        {
                            "type": "APP_INSTR",
                            "opcode": "INC",
                            "operands": ["C0"],
                            "label": "运行计数",
                        },
                    ],
                )],
            },
            {
                "rung_id": 5,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "P", "address": "X2", "label": "低水位沿"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "SET",
                        "operands": ["M3"],
                        "label": "锁存报警",
                    }],
                )],
            },
            {
                "rung_id": 6,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "P", "address": "X3", "label": "故障复位沿"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "RST",
                        "operands": ["M3"],
                        "label": "复位报警",
                    }],
                )],
            },
            {
                "rung_id": 7,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "P", "address": "X4", "label": "手动启动沿"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "SET",
                        "operands": ["M5"],
                        "label": "锁存手动运行",
                    }],
                )],
            },
            {
                "rung_id": 8,
                "header_element": None,
                "branches": [_branch(
                    [{"type": "P", "address": "X5", "label": "手动停止沿"}],
                    [{
                        "type": "APP_INSTR",
                        "opcode": "RST",
                        "operands": ["M5"],
                        "label": "复位手动运行",
                    }],
                )],
            },
            {
                "rung_id": 9,
                "header_element": None,
                "branches": [_branch(
                    [
                        {"type": "NO", "address": "M5", "label": "手动运行"},
                        {"type": "NC", "address": "M3", "label": "无报警"},
                    ],
                    [{"type": "COIL", "address": "Y0", "label": "变频器运行"}],
                )],
            },
        ],
    }

    issues = validate_ladder_against_selected_approach(
        ladder, {"selected_approach": selected}
    )

    assert issues == []
