import json

import pytest

import api
from ladder_repair import (
    normalize_app_instr_out_outputs,
    normalize_legacy_counter_outputs,
)
from pattern_library import classify_request, load_library
from plc_json_validator import PLCJsonValidationError, validate_ladder_full
from plc_workflow_review import review_ladder


def _output_rung(rung_id, output, *, inputs=None, header=None, note=""):
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
                "outputs": [output],
            }
        ],
    }


def _ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def test_timer_and_counter_have_distinct_json_types():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "延时"},
            inputs=[{"type": "NO", "address": "X0", "label": "运行"}],
        ),
        _output_rung(
            2,
            {"type": "COUNTER", "address": "C0", "value": "K5", "label": "计数"},
            inputs=[{"type": "P", "address": "X1", "label": "计数脉冲"}],
        ),
    )

    assert validate_ladder_full(data, "FX3U") is data


@pytest.mark.parametrize(
    ("output_type", "address"),
    (("TIMER", "C0"), ("COUNTER", "T0")),
)
def test_timer_counter_address_mismatch_is_rejected(output_type, address):
    data = _ladder(
        _output_rung(
            1,
            {"type": output_type, "address": address, "value": "K5", "label": ""},
            inputs=[{"type": "NO", "address": "X0", "label": ""}],
        )
    )

    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(data, "FX3U")


def test_legacy_timer_c_counter_is_converted_before_new_validation():
    legacy = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "C1", "value": "K10", "label": "累计"},
            inputs=[{"type": "P", "address": "X0", "label": "计数脉冲"}],
        )
    )

    normalized, addresses = normalize_legacy_counter_outputs(legacy)

    assert addresses == ["C1"]
    assert normalized["rungs"][0]["branches"][0]["outputs"][0]["type"] == "COUNTER"
    assert legacy["rungs"][0]["branches"][0]["outputs"][0]["type"] == "TIMER"
    validate_ladder_full(normalized, "FX3U")


@pytest.mark.parametrize(
    ("operands", "expected_type", "expected_address"),
    (
        (["Y0"], "COIL", "Y0"),
        (["M10"], "COIL", "M10"),
        (["T2", "K50"], "TIMER", "T2"),
        (["C3", "K8"], "COUNTER", "C3"),
    ),
)
def test_app_instr_out_is_normalized_to_typed_output(
    operands, expected_type, expected_address
):
    legacy = _ladder(
        _output_rung(
            1,
            {
                "type": "APP_INSTR",
                "opcode": "OUT",
                "operands": operands,
                "label": "模型误用的 OUT",
            },
            inputs=[{"type": "NO", "address": "X0", "label": "使能"}],
        )
    )

    normalized, converted = normalize_app_instr_out_outputs(legacy)
    output = normalized["rungs"][0]["branches"][0]["outputs"][0]

    assert output["type"] == expected_type
    assert output["address"] == expected_address
    assert output["label"] == "模型误用的 OUT"
    if len(operands) == 2:
        assert output["value"] == operands[1]
    assert converted
    assert legacy["rungs"][0]["branches"][0]["outputs"][0]["type"] == "APP_INSTR"
    validate_ladder_full(normalized, "FX3U")


def test_ambiguous_app_instr_out_is_left_for_precise_hard_rejection():
    malformed = _ladder(
        _output_rung(
            1,
            {
                "type": "APP_INSTR",
                "opcode": "OUT",
                "operands": ["X0"],
                "label": "输入不能作为线圈",
            },
        )
    )

    normalized, converted = normalize_app_instr_out_outputs(malformed)

    assert converted == []
    with pytest.raises(PLCJsonValidationError, match="typed COIL, TIMER, or COUNTER"):
        validate_ladder_full(normalized, "FX3U")


def test_m8000_timer_cannot_be_used_as_a_blink_oscillator():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T3", "value": "K5", "label": "1Hz闪烁"},
            inputs=[{"type": "NO", "address": "M8000", "label": "运行常通"}],
        ),
        comments={"T3": "闪烁定时器"},
    )

    with pytest.raises(PLCJsonValidationError, match="stays done instead of oscillating"):
        validate_ladder_full(data, "FX3U")

    findings = review_ladder(data, plc_model="FX3U")
    assert any(
        item.category == "timer_oscillator"
        and item.address == "T3"
        and item.severity == "error"
        for item in findings
    )


def test_m8000_power_on_delay_and_state_gated_delay_remain_valid():
    power_on_delay = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "上电延时"},
            inputs=[{"type": "NO", "address": "M8000", "label": "运行常通"}],
        )
    )
    state_delay = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T1", "value": "K30", "label": "步骤延时"},
            inputs=[{"type": "NO", "address": "M8000", "label": "运行常通"}],
            header={"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": "步骤1"},
        )
    )

    validate_ladder_full(power_on_delay, "FX3U")
    validate_ladder_full(state_delay, "FX3U")
    assert not any(item.category == "timer_oscillator" for item in review_ladder(power_on_delay))


def test_same_edge_complementary_set_reset_is_not_a_valid_toggle():
    data = _ladder(
        {
            "rung_id": 1,
            "debug_note": "用SET/RST模拟ALT翻转",
            "header_element": None,
            "shared_inputs": [
                {"type": "NO", "address": "Y12", "label": "使能"},
                {"type": "P", "address": "T3", "label": "到时上升沿"},
            ],
            "branches": [
                {
                    "branch_id": 1,
                    "y_offset_level": 0,
                    "inputs": [{"type": "NC", "address": "M30", "label": "当前OFF"}],
                    "outputs": [
                        {
                            "type": "APP_INSTR",
                            "opcode": "SET",
                            "operands": ["M30"],
                            "label": "置位",
                        }
                    ],
                },
                {
                    "branch_id": 2,
                    "y_offset_level": 1,
                    "inputs": [{"type": "NO", "address": "M30", "label": "当前ON"}],
                    "outputs": [
                        {
                            "type": "APP_INSTR",
                            "opcode": "RST",
                            "operands": ["M30"],
                            "label": "复位",
                        }
                    ],
                },
            ],
        }
    )

    with pytest.raises(PLCJsonValidationError, match="do not safely toggle M30"):
        validate_ladder_full(data, "FX3U")
    assert any(
        item.category == "set_reset_toggle" and item.address == "M30"
        for item in review_ladder(data)
    )


def test_clock_relay_contact_is_the_valid_one_hertz_blink_shape():
    data = _ladder(
        _output_rung(
            1,
            {"type": "COIL", "address": "M3", "label": "1Hz闪烁"},
            inputs=[{"type": "NO", "address": "M8013", "label": "1s时钟"}],
        ),
        comments={"M8013": "1s时钟", "M3": "1Hz闪烁"},
    )

    validate_ladder_full(data, "FX3U")
    assert not any(item.category == "timer_oscillator" for item in review_ladder(data))


def test_generation_prompt_keeps_timer_semantics_ahead_of_rag_context():
    classification = classify_request("FX3U M3 以1Hz闪烁，M4以0.5Hz闪烁")
    prompt = api._select_system_prompt(
        "ladder",
        user_requirement="FX3U M3 以1Hz闪烁，M4以0.5Hz闪烁",
        task_type="generate",
        plc_model="FX3U",
    )

    assert "普通 T 定时器必须存在会变 OFF 的使能/复位路径" in prompt
    assert "RUN 常 ON 继电器不能单独构成周期振荡" in prompt
    assert "TIMER" in prompt and "COUNTER" in prompt
    assert "example_timer_clock" in classification["matched_ids"]
    assert "pattern_d" not in classification["matched_ids"]


def test_counter_and_clock_examples_follow_the_new_schema_and_validate():
    examples = {item["id"]: item for item in load_library()["examples"]}

    counter = json.loads(examples["example_counter"]["content"])
    timer_clock = json.loads(examples["example_timer_clock"]["content"])
    alarm = json.loads(examples["example_alarm"]["content"])

    assert counter["rungs"][0]["branches"][0]["outputs"][0]["type"] == "COUNTER"
    assert any(
        input_element.get("address") == "M8013"
        for rung in alarm["rungs"]
        for branch in rung["branches"]
        for input_element in branch.get("inputs", [])
        if input_element.get("type") != "parallel_block"
    )
    validate_ladder_full(counter, "FX3U")
    validate_ladder_full(timer_clock, "FX3U")
    validate_ladder_full(alarm, "FX3U")
