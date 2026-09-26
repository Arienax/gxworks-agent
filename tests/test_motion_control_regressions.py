import copy
import json

import pytest

from plc.specification.confirmed import build_review_draft, validate_spec_draft
from plc.hardware_profiles import build_hardware_profile, ensure_hardware_questions
from knowledge.patterns import (
    KnowledgeRouter,
    build_workflow_prompt,
    classify_request,
    load_library,
)
from plc.validation import PLCJsonValidationError, validate_ladder_full


def _motion_ladder(opcode="DRVI", pulse="Y0", direction="Y7", speed="K2000"):
    operands = ["K5000", speed, pulse, direction]
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": "start"}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": opcode,
                                "operands": operands,
                                "label": "move",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _plsy_ladder(pulse="Y0", frequency="K1000"):
    return {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "header_element": None,
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [{"type": "NO", "address": "X0", "label": "start"}],
                        "outputs": [
                            {
                                "type": "APP_INSTR",
                                "opcode": "PLSY",
                                "operands": [frequency, "K100", pulse],
                                "label": "pulse",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _profile(**overrides):
    profile = {
        "plc_family": "FX3U",
        "output_type": "晶体管输出型",
        "motion_control_method": "pulse",
    }
    profile.update(overrides)
    return {"hardware_profile": profile}


def test_motion_design_questions_survive_generic_plc_profile_filter():
    analysis = {
        "missing_info": [
            {
                "id": "modules",
                "question": "已安装扩展模块/适配器完整型号（无则填‘无’）",
                "required": True,
            },
            {
                "id": "modules",
                "question": "定位扩展模块完整型号？",
                "required": True,
            },
            {"question": "伺服驱动器控制方式？", "required": True},
            {"question": "步进驱动器完整型号？", "required": True},
            {"question": "伺服驱动器端子映射？", "required": True},
            {"question": "脉冲输出轴？", "required": True},
            {"question": "方向输出端子？", "required": True},
            {"question": "高速输出适配器数量？", "required": True},
        ]
    }

    result = ensure_hardware_questions(analysis, "FX3U", "伺服和步进定位")

    assert [item["id"] for item in result["missing_info"]] == [
        "positioning_module_model",
        "motion_control_method",
        "motion_drive_model",
        "motion_wiring_mapping",
        "pulse_output_axis",
        "direction_output",
        "positioning_module_quantity",
    ]


def test_homing_method_is_required_only_when_homing_is_selected():
    draft = build_review_draft(
        {
            "missing_info": [
                {
                    "id": "homing_required",
                    "question": "是否需要回原点？",
                    "options": ["否（不需要）", "是（需要）"],
                    "required": True,
                },
                {
                    "id": "homing_method",
                    "question": "回原点方式？",
                    "options": ["ZRN简单回零", "DSZR带DOG搜索回零"],
                    "required": True,
                },
            ]
        }
    )
    method = next(item for item in draft["parameters"] if item["id"] == "homing_method")
    assert method["required_when"]["parameter"] == "是否需要回原点？"

    no_homing = copy.deepcopy(draft)
    next(item for item in no_homing["parameters"] if item["id"] == "homing_required")[
        "value"
    ] = "否（不需要）"
    assert validate_spec_draft(no_homing, "FX3U")["errors"] == []

    with_homing = copy.deepcopy(draft)
    next(item for item in with_homing["parameters"] if item["id"] == "homing_required")[
        "value"
    ] = "是（需要）"
    errors = validate_spec_draft(with_homing, "FX3U")["errors"]
    assert any(item["code"] == "required_parameter_missing" for item in errors)


def test_positioning_module_model_is_conditional_on_external_implementation():
    draft = build_review_draft(
        {
            "missing_info": [
                {
                    "id": "positioning_implementation",
                    "question": "运动控制实现方式？",
                    "options": ["基本单元内置高速脉冲", "外接定位模块/高速输出适配器"],
                    "required": True,
                },
                {
                    "id": "positioning_module_model",
                    "question": "定位模块/高速输出适配器完整型号？",
                    "options": ["FX3U-2HSY-ADP", "FX3U-1PG", "FX2N-10PG"],
                    "required": True,
                },
            ]
        }
    )

    base_cpu = copy.deepcopy(draft)
    next(
        item for item in base_cpu["parameters"] if item["id"] == "positioning_implementation"
    )["value"] = "基本单元内置高速脉冲"
    assert validate_spec_draft(base_cpu, "FX3U")["errors"] == []

    external = copy.deepcopy(draft)
    next(
        item for item in external["parameters"] if item["id"] == "positioning_implementation"
    )["value"] = "外接定位模块/高速输出适配器"
    errors = validate_spec_draft(external, "FX3U")["errors"]
    assert any(item["code"] == "required_parameter_missing" for item in errors)


def test_second_high_speed_adapter_is_required_only_for_y2_y3_axes():
    draft = build_review_draft(
        {
            "missing_info": [
                {
                    "id": "positioning_module_model",
                    "question": "定位模块型号？",
                    "required": True,
                },
                {
                    "id": "pulse_output_axis",
                    "question": "脉冲输出轴？",
                    "required": True,
                },
                {
                    "id": "positioning_module_quantity",
                    "question": "高速输出适配器数量？",
                    "required": True,
                },
            ]
        }
    )

    def set_value(target, item_id, value):
        next(item for item in target["parameters"] if item["id"] == item_id)[
            "value"
        ] = value

    first_adapter = copy.deepcopy(draft)
    set_value(first_adapter, "positioning_module_model", "FX3U-2HSY-ADP")
    set_value(first_adapter, "pulse_output_axis", "Y1")
    assert validate_spec_draft(first_adapter, "FX3U")["errors"] == []

    second_adapter = copy.deepcopy(draft)
    set_value(second_adapter, "positioning_module_model", "FX3U-2HSY-ADP")
    set_value(second_adapter, "pulse_output_axis", "Y3")
    errors = validate_spec_draft(second_adapter, "FX3U")["errors"]
    assert any(item["code"] == "required_parameter_missing" for item in errors)


@pytest.mark.parametrize(
    ("query", "expected", "forbidden", "expects_axis_question"),
    [
        ("FX3U步进电机运行", "pattern_h", "pattern_c", True),
        ("FX3U步进状态机运行", "pattern_c", "pattern_h", False),
        ("FX3U步进电机按三个步骤顺序定位", "pattern_h", None, True),
    ],
)
def test_stepper_motor_and_sequence_step_are_phrase_disambiguated(
    query, expected, forbidden, expects_axis_question
):
    result = classify_request(query)
    route = KnowledgeRouter.route(query)

    assert expected in result["matched_ids"]
    if forbidden:
        assert forbidden not in result["matched_ids"]
    if "步进电机按三个步骤" in query:
        assert "pattern_c" in result["matched_ids"]
    assert bool(route.open_points) is expects_axis_question


def test_workflow_prompt_no_longer_contains_stale_vfd_assumption_rule():
    vfd_prompt, _ = build_workflow_prompt("FX3U变频器20/50/60Hz多段速")
    motion_prompt, _ = build_workflow_prompt("FX3U步进电机定位")

    assert "required ``control_method``" in vfd_prompt
    assert "do not create a required hardware parameter" not in vfd_prompt
    assert "Servo / stepper motion requirement selection" in motion_prompt
    assert "not a fixed Y0->Y4" in motion_prompt


def test_motion_examples_are_schema_valid_and_do_not_reuse_m8336_as_completion():
    examples = {item["id"]: item for item in load_library()["examples"]}

    for example_id in ("example_plsy", "example_drvi", "example_zrn"):
        validate_ladder_full(json.loads(examples[example_id]["content"]), "FX3U")

    drvi = examples["example_drvi"]["content"]
    zrn = examples["example_zrn"]["content"]
    assert '"opcode":"DMOV","operands":["K5000","D8343"]' in drvi
    assert '"D8345"' not in drvi
    assert "M8336" not in zrn
    assert '"opcode":"ZRN","operands":["K5000","K500","X3","Y0"]' in zrn
    assert "M8029" in zrn


def test_direction_output_accepts_any_confirmed_nonconflicting_y_device():
    assert validate_ladder_full(_motion_ladder(direction="Y7"), "FX3U", _profile())

    with pytest.raises(PLCJsonValidationError, match="must not reuse pulse output"):
        validate_ladder_full(_motion_ladder(direction="Y0"), "FX3U", _profile())
    with pytest.raises(PLCJsonValidationError, match="valid FX3U Y device"):
        validate_ladder_full(_motion_ladder(direction="X1"), "FX3U", _profile())


def test_high_speed_output_adapter_requires_two_units_for_y3_and_200khz():
    base = _profile(output_type="晶体管输出型")
    with pytest.raises(PLCJsonValidationError, match="Y0, Y1, or Y2"):
        validate_ladder_full(_plsy_ladder("Y3", "K150000"), "FX3U", base)

    one_adapter = _profile(
        output_type="relay",
        positioning_module_model="FX3U-2HSY-ADP",
    )
    assert validate_ladder_full(
        _plsy_ladder("Y0", "K150000"), "FX3U", one_adapter
    )
    with pytest.raises(PLCJsonValidationError, match="requires two confirmed"):
        validate_ladder_full(
            _plsy_ladder("Y3", "K150000"), "FX3U", one_adapter
        )

    adapter = _profile(
        output_type="继电器输出型",
        positioning_module_model="FX3U-2HSY-ADP",
        positioning_module_quantity=2,
    )
    assert validate_ladder_full(
        _plsy_ladder("Y3", "K150000"), "FX3U", adapter
    )


def test_hardware_profile_preserves_motion_interface_and_positioning_module():
    spec = {
        "plc_model": "FX3U",
        "parameters": [
            {
                "id": "motion_control_method",
                "name": "伺服/步进驱动器控制方式",
                "value": "脉冲+方向",
            },
            {
                "id": "positioning_module_model",
                "name": "定位模块/高速输出适配器完整型号",
                "value": "FX3U-2HSY-ADP",
            },
            {
                "id": "positioning_module_quantity",
                "name": "高速输出适配器数量",
                "value": "2",
            },
        ],
    }

    profile = build_hardware_profile(spec)

    assert profile["motion_control_method"] == "pulse"
    assert profile["positioning_module_model"] == "FX3U-2HSY-ADP"
    assert profile["positioning_module_quantity"] == "2"


def test_vfd_and_servo_parameters_do_not_overwrite_each_other():
    spec = {
        "plc_model": "FX3U",
        "parameters": [
            {"id": "drive_model", "name": "VFD drive model", "value": "FR-D740"},
            {
                "id": "control_method",
                "name": "VFD command interface",
                "value": "RS485 Modbus",
            },
            {
                "id": "motion_drive_model",
                "name": "servo drive model",
                "value": "MR-J4",
            },
            {
                "id": "motion_control_method",
                "name": "servo command interface",
                "value": "pulse + direction",
            },
        ],
    }

    profile = build_hardware_profile(spec)

    assert profile["drive_model"] == "FR-D740"
    assert profile["control_method"] == "serial"
    assert profile["motion_drive_model"] == "MR-J4"
    assert profile["motion_control_method"] == "pulse"


def test_analysis_prompt_documents_instruction_aware_motion_questions():
    from application.prompts import ANALYSIS_MOTION_PROMPT, ANALYSIS_MOTION_FAMILY_PROMPTS

    pulse = ANALYSIS_MOTION_FAMILY_PROMPTS["pulse"]
    assert "PLSY/DPLSY/PLSV/DPLSV" in pulse
    assert "输出轴、频率、脉冲数或连续模式" in pulse
    assert "不追加相对/绝对、回零问题" in pulse
    assert "不套用所有定位/回零问题" in ANALYSIS_MOTION_PROMPT



def test_known_operand_count_is_checked_by_existing_registry_validation():
    from application.compact_protocol import expand_compact_ladder, compact_capability_prompt
    from plc.validation import validate_ladder_candidate_structure
    good = {"r":[{"b":[{"i":["NO M0"], "o":["ZRN K1000 K200 X0 Y0"]}]}]}
    ladder = expand_compact_ladder(good, {"plc_model": "FX3U"})
    validate_ladder_candidate_structure(ladder, plc_model="FX3U")
    bad = copy.deepcopy(ladder)
    bad["rungs"][0]["branches"][0]["outputs"][0]["operands"] = ["X0", "X0", "Y0", "K1000", "K200"]
    with pytest.raises(PLCJsonValidationError, match="exactly 4 operands"):
        validate_ladder_candidate_structure(bad, plc_model="FX3U")
    from knowledge.structured_facts import resolve_instruction_contract
    # Exact facts have one owner; the retired prompt snapshot must stay empty.
    assert compact_capability_prompt("FX3U", {}) == ""
    instruction = resolve_instruction_contract({"opcode": "ZRN"}, plc_model="FX3U")
    assert instruction["min_operands"] == instruction["max_operands"] == 4
    assert instruction["native_operand_order"] == ["S1", "S2", "S3", "D"]
    assert [r["name"] for r in instruction["operand_annotations"]] == [
        "return_speed", "creep_speed", "zero_signal", "pulse_output"]
    assert instruction["completion"]["device"] == "M8029"
    assert instruction["completion"]["placement"] == "same_rung_parallel_branch"
    assert instruction["sources"]
