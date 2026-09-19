import copy

import pytest

from plc.specification.confirmed import (
    build_review_draft,
    canonicalize_confirmed_spec,
    validate_spec_draft,
)
from plc.hardware_profiles import ensure_hardware_questions, hardware_requirement_flags, validate_hardware_spec
from plc.validation import PLCJsonValidationError, validate_ladder_full
from plc.generation_contract import generation_specification


HARDWARE_QUESTIONS = [
    {"id": "cpu_full_model", "question": "PLC CPU完整型号（含输出形式后缀）", "required": True},
    {"id": "output_type", "question": "PLC基本单元输出类型", "required": True},
    {"id": "firmware", "question": "PLC固件/硬件版本", "required": True},
    {"id": "modules", "question": "已安装扩展模块/适配器完整型号（无则填“无”）", "required": True},
    {"id": "drive_model", "question": "变频器/驱动器完整型号", "required": True},
    {"id": "control_method", "question": "变频器频率给定控制方式", "required": True},
    {"id": "wiring_mapping", "question": "变频器控制端子或信号映射", "required": True},
]


def pulse_ladder():
    return {
        "device_comments": {"X0": "启动", "Y0": "脉冲输出"},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": "启动"}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "PLSY",
                                "operands": ["K1000", "K100", "Y0"],
                                "label": "脉冲输出",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_hardware_flags_do_not_trigger_themselves_on_a_second_pass():
    first = ensure_hardware_questions(
        {"summary": "启动按钮控制普通电机", "missing_info": []},
        "FX3U",
        "启动按钮控制普通电机",
    )
    second = ensure_hardware_questions(first, "FX3U", "启动按钮控制普通电机")

    assert not any(first["hardware_requirements"].values())
    assert second["hardware_requirements"] == first["hardware_requirements"]
    assert second["missing_info"] == []


@pytest.mark.parametrize("field,values", [
    ("forbidden_structures", ["vfd_multi_speed", "pulse_positioning"]),
    ("forbidden_features", ["vfd_multi_speed", "pulse_positioning"]),
    ("forbidden_opcodes", ["PLSY", "PLSV", "DRVI", "DRVA"]),
    ("forbidden_instructions", ["PLSY", "PLSV", "DRVI", "DRVA"]),
])
def test_forbidden_generation_features_are_not_hardware_requirements(field, values):
    analysis = {
        "summary": "Two independent input/output channels",
        "approaches": [{"generation_contract": {"required_structures": ["direct_logic"], field: values}}],
        "missing_info": [],
    }
    original = copy.deepcopy(analysis)
    flags = hardware_requirement_flags(analysis, "X0 controls Y0; X1 controls Y1")
    assert not any(flags.values())
    assert analysis == original


@pytest.mark.parametrize("field,value", [
    ("required_structures", ["vfd_multi_speed"]),
    ("any_of_structure_groups", [["vfd_multi_speed", "analog_control"]]),
])
def test_model_proposed_drive_contract_does_not_establish_user_hardware(field, value):
    result = ensure_hardware_questions({
        "summary": "Speed selection",
        "approaches": [{"generation_contract": {field: value, "forbidden_opcodes": ["PLSY"]}}],
        "missing_info": [],
    }, "FX3U", "Select the motor speed")
    # Positive model proposals are not user-confirmed hardware. Generic motor
    # speed alone cannot choose a VFD over a servo/stepper/other implementation.
    assert result["hardware_requirements"]["vfd"] is False
    assert result["hardware_requirements"]["pulse"] is False
    assert result["missing_info"] == []


def test_level_follow_analysis_does_not_invent_required_vfd_question():
    from application.model_api import _normalize_analysis_result

    request = "FX3U，普通梯形图，两路独立电平控制：网络1使用常开X0直接控制Y0，网络2使用常开X1直接控制Y1。无需自锁、定时、计数或额外中间继电器；上电输出随输入。请分析I/O并给出待确认规格。"
    analysis = {
        "summary": "X0 常开直接驱动 Y0，X1 常开直接驱动 Y1，输出随输入实时变化。",
        "approaches": [{
            "name": "直接逻辑电平跟随",
            "generation_guide": "X0 常开驱动 Y0；X1 常开驱动 Y1。",
            "generation_contract": {
                "required_structures": ["direct_logic"],
                "required_devices": ["X0", "X1", "Y0", "Y1"],
                "forbidden_structures": [
                    "self_hold", "set_reset_latch", "edge_trigger", "bit_state_machine",
                    "register_state_machine", "state_initialization", "state_comparison",
                    "state_transition", "hardware_counter", "data_register_counter",
                    "analog_control", "serial_communication", "pid_control",
                    "pulse_positioning", "vfd_multi_speed",
                ],
            },
        }],
        "missing_info": [{"id": "stop_or_inhibit_condition", "question": "是否需要总停止条件？", "required": False}],
        "suggested_io": {"X": {"X0": "输入1", "X1": "输入2"}, "Y": {"Y0": "输出1", "Y1": "输出2"}},
        "hardware_config": {"output_type_note": "只做通断电平输出，不涉及高速脉冲"},
    }
    original = copy.deepcopy(analysis)
    normalized = _normalize_analysis_result(analysis, "FX3U", request)
    assert not normalized["hardware_requirements"]["vfd"]
    assert [item["id"] for item in normalized["missing_info"]] == ["stop_or_inhibit_condition"]
    draft = build_review_draft(normalized)
    assert [item["id"] for item in draft["parameters"]] == ["stop_or_inhibit_condition"]
    assert validate_spec_draft(draft, "FX3U")["errors"] == []
    assert "vfd_multi_speed" in draft["selected_approach"]["generation_contract"]["forbidden_structures"]
    assert analysis == original


def test_hardware_flags_survive_review_build_without_the_original_user_text():
    first = ensure_hardware_questions(
        {"summary": "speed control", "missing_info": []},
        "FX3U",
        "VFD speed control",
    )
    second = ensure_hardware_questions(first, "FX3U")

    assert first["hardware_requirements"]["vfd"] is True
    assert second["hardware_requirements"] == first["hardware_requirements"]


def test_only_plc_profile_questions_are_removed_while_drive_questions_remain():
    analysis = {
        "missing_info": HARDWARE_QUESTIONS
        + [
            {"question": "请填写所接驱动设备的具体订货号", "required": True},
            {"question": "启动传感器接哪个输入点？", "required": True},
        ],
        "hardware_config": {
            "confirmation_required": True,
            "capability_source": "deterministic_profile_before_ai",
        },
    }

    result = ensure_hardware_questions(analysis, "FX3U", "变频器多段速控制")

    assert [item["question"] for item in result["missing_info"]] == [
        "变频器/驱动器完整型号",
        "变频器频率给定控制方式",
        "变频器控制端子或信号映射",
        "请填写所接驱动设备的具体订货号",
        "启动传感器接哪个输入点？",
    ]
    assert [item.get("id") for item in result["missing_info"][:4]] == [
        "drive_model",
        "control_method",
        "wiring_mapping",
        "drive_model",
    ]
    assert "hardware_config" not in result


def test_vfd_control_question_from_reasoning_survives_analysis_normalization():
    analysis = {
        "missing_info": [
            {
                "question": "变频器频率给定方式？",
                "options": ["多段速端子", "模拟量给定", "RS-485/Modbus通信"],
                "default": "多段速端子",
            },
            {
                "question": "手动点动与自动运行是否互锁？",
                "options": ["是", "否"],
            },
            {
                "question": "M3/M4/M5指示灯在手动期间是否指示？",
                "options": ["都指示", "仅自动指示"],
            },
        ]
    }

    result = ensure_hardware_questions(analysis, "FX3U", "触摸屏控制变频器")
    draft = build_review_draft(result)

    assert len(result["missing_info"]) == 3
    assert result["missing_info"][0]["id"] == "control_method"
    assert [item["name"] for item in draft["parameters"]] == [
        "变频器频率给定方式？",
        "手动点动与自动运行是否互锁？",
        "M3/M4/M5指示灯在手动期间是否指示？",
    ]
    assert draft["parameters"][0]["required"] is True


def test_legacy_deterministic_drive_question_is_not_removed_by_source_alone():
    analysis = {
        "missing_info": [
            {
                "id": "control_method",
                "question": "变频器频率给定控制方式",
                "source": "deterministic_hardware_rule",
                "required": True,
            }
        ]
    }

    result = ensure_hardware_questions(analysis, "FX3U", "变频器控制")

    assert result["missing_info"] == analysis["missing_info"]


def test_cached_derived_flags_alone_cannot_restore_a_required_vfd_question():
    analysis = {
        "hardware_requirements": {
            "hardware_dependent": True,
            "vfd": True,
            "pulse": False,
            "analog": True,
            "serial": True,
        },
        "missing_info": [
            {"question": "手动与自动是否互锁？", "options": ["是", "否"]},
            {"question": "速度指示灯是否同时显示？", "options": ["是", "否"]},
        ],
    }

    result = ensure_hardware_questions(analysis, "FX3U")

    assert result["missing_info"] == analysis["missing_info"]
    assert result["hardware_intent"]["source"] == "legacy_unknown"


@pytest.mark.parametrize(
    "explicit_method",
    [
        "采用STF和RH/RM/RL多段速端子控制",
        "采用FX3U-4DA模拟量给定",
        "采用RS-485 Modbus通讯给定",
    ],
)
def test_explicit_vfd_control_method_does_not_add_duplicate_question(explicit_method):
    result = ensure_hardware_questions(
        {"summary": "变频器调速", "missing_info": []},
        "FX3U",
        explicit_method,
    )

    assert result["missing_info"] == []


def test_cached_blank_hardware_rows_are_removed_but_filled_values_are_kept_optional():
    previous = {
        "plc_model": "FX3U",
        "parameters": [
            {
                "id": "cpu_full_model",
                "name": "PLC CPU完整型号（含输出形式后缀）",
                "value": "",
                "source": "deterministic_hardware_rule",
                "required": True,
            },
            {
                "id": "output_type",
                "name": "PLC基本单元输出类型",
                "value": "晶体管输出型",
                "source": "deterministic_hardware_rule",
                "required": True,
            },
            {
                "name": "PLC固件/硬件版本",
                "value": "",
                "source": "analysis",
                "required": True,
            },
        ],
        "io_table": [],
    }

    draft = build_review_draft({"plc_model": "FX3U", "missing_info": []}, previous)

    assert [item["name"] for item in draft["parameters"]] == ["PLC基本单元输出类型"]
    assert draft["parameters"][0]["value"] == "晶体管输出型"
    assert draft["parameters"][0]["required"] is False


def test_missing_hardware_context_never_blocks_requirement_confirmation():
    spec = {
        "plc_model": "FX3U",
        "parameters": [],
        "io_table": [],
        "hardware_requirements": {
            "hardware_dependent": True,
            "vfd": True,
            "pulse": True,
            "analog": True,
            "serial": True,
        },
    }

    assert validate_spec_draft(spec, "FX3U")["errors"] == []


def test_model_required_missing_information_is_advisory_not_blocking():
    draft = build_review_draft(
        {
            "plc_model": "FX3U",
            "missing_info": [
                {
                    "question": "启动传感器接哪个输入点？",
                    "options": ["X0", "X1"],
                }
            ],
        }
    )

    assert draft["parameters"][0]["required"] is True
    issues = validate_spec_draft(draft, "FX3U")
    assert issues["errors"] == []
    assert any(
        item["code"] == "required_parameter_missing"
        and item.get("blocking") is False
        for item in issues["warnings"]
    )


def test_unanswered_review_questions_are_not_generation_facts():
    projected = generation_specification(
        {
            "parameters": [
                {
                    "id": "unanswered",
                    "name": "模型提出但用户未回答的问题",
                    "value": "",
                    "source": "analysis",
                },
                {
                    "id": "confirmed",
                    "name": "已确认参数",
                    "value": "K30",
                    "source": "user",
                },
            ]
        }
    )

    assert projected["parameters"] == [
        {
            "id": "confirmed",
            "name": "已确认参数",
            "value": "K30",
            "source": "user",
        }
    ]


def test_unknown_output_type_does_not_block_pulse_generation():
    profile = {"hardware_profile": {"plc_family": "FX3U", "output_type": ""}}

    assert validate_ladder_full(pulse_ladder(), "FX3U", profile)


def test_explicit_relay_output_conflict_is_still_rejected():
    profile = {
        "hardware_profile": {"plc_family": "FX3U", "output_type": "继电器输出型"}
    }

    with pytest.raises(PLCJsonValidationError, match="confirmed relay output"):
        validate_ladder_full(pulse_ladder(), "FX3U", profile)


def test_parameter_merge_uses_stable_id_and_preserves_confirmed_value():
    previous = {
        "plc_model": "FX3U",
        "parameters": [
            {
                "id": "control_method",
                "name": "变频器频率给定方式",
                "value": "模拟量输出（0-10V）",
                "source": "user",
                "required": True,
            }
        ],
        "io_table": [],
    }
    analysis = {
        "plc_model": "FX3U",
        "missing_info": [
            {
                "id": "control_method",
                "question": "变频器频率给定控制方式？",
                "options": ["多段速端子", "模拟量输出", "RS-485通信"],
                "default": "多段速端子",
                "required": True,
            }
        ],
    }

    draft = build_review_draft(analysis, previous)

    rows = [item for item in draft["parameters"] if item.get("id") == "control_method"]
    assert len(rows) == 1
    assert rows[0]["value"] == "模拟量输出（0-10V）"


def test_canonicalization_repairs_conflicting_duplicate_control_method_rows():
    corrupted = {
        "plc_model": "FX3U",
        "parameters": [
            {
                "id": "control_method",
                "name": "变频器频率给定方式",
                "value": "模拟量输出（0-10V或4-20mA）",
                "source": "user",
                "required": True,
            },
            {
                "id": "control_method",
                "name": "变频器频率给定控制方式",
                "value": "Y输出多段速端子（STF/RH/RM/RL）",
                "source": "user",
                "required": True,
            },
        ],
        "io_table": [],
        "hardware_requirements": {
            "hardware_dependent": True,
            "vfd": True,
            "analog": True,
        },
        "selected_approach": {
            "approach_id": "constant_pressure_pid_v2",
            "name": "恒压供水PID控制法（模拟量0-10V）",
            "description": "PID闭环调节并通过模拟量输出控制变频器",
            "generation_guide": "PID D100 D8260 D110 D200；MOV D200 D8270",
        },
    }

    canonical = canonicalize_confirmed_spec(corrupted)

    rows = [
        item for item in canonical["parameters"]
        if item.get("id") == "control_method"
    ]
    assert len(rows) == 1
    assert rows[0]["value"] == "模拟量输出（0-10V或4-20mA）"
    assert canonical["hardware_profile"]["control_method"] == "analog"
    assert not any(
        item["code"] == "control_method_approach_conflict"
        for item in validate_spec_draft(canonical, "FX3U")["errors"]
    )


def test_conflicting_duplicate_stable_parameter_id_is_rejected_before_save():
    spec = {
        "plc_model": "FX3U",
        "parameters": [
            {"id": "control_method", "name": "控制方式", "value": "模拟量输出"},
            {"id": "control_method", "name": "给定方式", "value": "多段速端子"},
        ],
        "io_table": [],
    }

    errors = validate_spec_draft(spec, "FX3U")["errors"]

    assert any(item["code"] == "conflicting_parameter_id" for item in errors)


def test_selected_vfd_approach_must_match_confirmed_control_method():
    spec = {
        "plc_model": "FX3U",
        "parameters": [
            {
                "id": "control_method",
                "name": "变频器频率给定控制方式",
                "value": "Y输出多段速端子（STF/RH/RM/RL）",
            }
        ],
        "hardware_requirements": {
            "hardware_dependent": True,
            "vfd": True,
        },
        "selected_approach": {
            "name": "恒压供水PID控制法（模拟量0-10V）",
            "description": "使用模拟量给定控制变频器",
            "generation_guide": "MOV D200 D8270",
        },
    }

    errors = validate_hardware_spec(spec, "FX3U")["errors"]

    assert any(item["code"] == "control_method_approach_conflict" for item in errors)
