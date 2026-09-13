import copy
import json

import pytest

import api
from inspection_engine import review_ladder
from pattern_library import classify_request, load_library
from plc_json_validator import PLCJsonValidationError, validate_ladder_full


def _ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def _output_rung(rung_id, output, inputs=None):
    return {
        "rung_id": rung_id,
        "header_element": None,
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


def test_timer_must_have_reachable_reset_path():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "常开定时器"},
            inputs=[{"type": "NO", "address": "M8000", "label": "RUN常ON"}],
        ),
        comments={"M8000": "RUN常ON", "T0": "常开定时器"},
    )

    with pytest.raises(PLCJsonValidationError, match="cannot reset"):
        validate_ladder_full(data, "FX3U")


def test_timer_reset_path_can_be_created_by_control_state():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "定时器"},
            inputs=[{"type": "NO", "address": "M0", "label": "阶段"}],
        ),
        comments={"M0": "阶段", "T0": "定时器"},
    )
    validate_ladder_full(data, "FX3U")


def test_timer_self_nc_oscillator_is_rejected_as_scan_dependent():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "闪烁定时"},
            inputs=[{"type": "NC", "address": "T0", "label": "自复位"}],
        ),
        comments={"T0": "闪烁定时"},
    )

    with pytest.raises(PLCJsonValidationError, match="scan-dependent"):
        validate_ladder_full(data, "FX3U")
    assert any(item.category == "timer_oscillator" for item in review_ladder(data))


def test_timer_enable_from_its_own_done_contact_is_rejected():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K10", "label": "错误定时"},
            inputs=[{"type": "NO", "address": "T0", "label": "完成"}],
        ),
        comments={"T0": "错误定时"},
    )

    with pytest.raises(PLCJsonValidationError, match="self-reference"):
        validate_ladder_full(data, "FX3U")


def test_two_phase_timer_oscillator_has_explicit_state_reset_paths():
    data = _ladder(
        _output_rung(
            1,
            {"type": "TIMER", "address": "T0", "value": "K5", "label": "OFF相"},
            inputs=[{"type": "NC", "address": "M0", "label": "OFF相"}],
        ),
        _output_rung(
            2,
            {"type": "APP_INSTR", "opcode": "SET", "operands": ["M0"], "label": "进入ON相"},
            inputs=[{"type": "NO", "address": "T0", "label": "OFF相完成"}],
        ),
        _output_rung(
            3,
            {"type": "TIMER", "address": "T1", "value": "K5", "label": "ON相"},
            inputs=[{"type": "NO", "address": "M0", "label": "ON相"}],
        ),
        _output_rung(
            4,
            {"type": "APP_INSTR", "opcode": "RST", "operands": ["M0"], "label": "返回OFF相"},
            inputs=[{"type": "NO", "address": "T1", "label": "ON相完成"}],
        ),
        comments={"M0": "闪烁相位", "T0": "OFF相", "T1": "ON相"},
    )

    validate_ladder_full(data, "FX3U")
    findings = review_ladder(data)
    assert not any(item.category == "timer_oscillator" for item in findings)


def test_held_coil_toggle_is_rejected_as_fake_scan_toggle():
    data = _ladder(
        _output_rung(
            1,
            {"type": "APP_INSTR", "opcode": "SET", "operands": ["M30"], "label": "置位"},
            inputs=[{"type": "NC", "address": "M30", "label": "反相"}],
        ),
        _output_rung(
            2,
            {"type": "APP_INSTR", "opcode": "RST", "operands": ["M30"], "label": "复位"},
            inputs=[{"type": "NO", "address": "M30", "label": "本身"}],
        ),
        comments={"M30": "翻转状态"},
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


def test_generation_prompt_keeps_timer_semantics_without_legacy_run_relay_example():
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
