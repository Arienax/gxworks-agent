import copy

import pytest

import knowledge.patterns as pattern_library
from knowledge.patterns import classify_request, load_library


@pytest.mark.parametrize(
    ("query", "forbidden_instruction", "forbidden_pattern"),
    [
        ("FX3U motor start stop", "TO", "pattern_k"),
        ("FX3U monitor alarm", "TO", "pattern_k"),
        ("FX3U restore output", "TO", "pattern_k"),
        ("FX3U offset calculation", "SET", None),
        ("FX3U version register", "RS", "pattern_j"),
        ("FX3U rapid speed loop", "PID", "pattern_g"),
        ("FX3U movement command", "MOV", None),
    ],
)
def test_short_instruction_names_do_not_match_inside_words(
    query, forbidden_instruction, forbidden_pattern
):
    result = classify_request(query)

    assert forbidden_instruction not in result["selected_instructions"]
    if forbidden_pattern:
        assert forbidden_pattern not in result["matched_ids"]


@pytest.mark.parametrize(
    ("query", "expected_instruction"),
    [
        ("FX3U TO K0 K10 D100 K4", "TO"),
        ("FX3U RS(D100 K8 D200 K8)", "RS"),
        ("FX3U SET M10", "SET"),
        ("FX3U rst M10", "RST"),
        ("FX3U 使用MOV传送D0到D10", "MOV"),
        ("FX3U PID参数初始化", "PID"),
    ],
)
def test_real_instruction_tokens_still_match(query, expected_instruction):
    result = classify_request(query)

    assert expected_instruction in result["selected_instructions"]


def test_instruction_mapping_prefers_longer_tokens(monkeypatch):
    library = copy.deepcopy(load_library())
    library["instruction_mapping"]["RS2"] = {
        "completion_flag": "M8123",
        "example": None,
        "pattern": "pattern_j",
        "note": "test-only longer opcode",
    }
    monkeypatch.setattr(pattern_library, "load_library", lambda: library)

    result = classify_request("FX3U RS2 D100 K8; RS D200 K8")

    assert result["selected_instructions"][:2] == ["RS2", "RS"]


def test_longer_instruction_does_not_also_select_shorter_prefix(monkeypatch):
    library = copy.deepcopy(load_library())
    library["instruction_mapping"]["RS2"] = {
        "completion_flag": "M8123",
        "example": None,
        "pattern": "pattern_j",
        "note": "test-only longer opcode",
    }
    monkeypatch.setattr(pattern_library, "load_library", lambda: library)

    result = classify_request("FX3U RS2 D100 K8")

    assert result["selected_instructions"] == ["RS2"]


def test_to_and_rs_still_pull_their_expected_patterns():
    to_result = classify_request("FX3U TO K0 K10 D100 K4")
    rs_result = classify_request("FX3U RS D100 K8 D200 K8")

    assert "pattern_k" in to_result["matched_ids"]
    assert "pattern_j" in rs_result["matched_ids"]


def test_vfd_pattern_keeps_drive_design_questions_out_of_plc_field_filter():
    library = load_library()
    pattern = next(
        item for item in library["patterns"] if item.get("id") == "pattern_vfd"
    )
    description = pattern["description"]

    assert "在 missing_info 中询问 control_method" in description
    assert "不得因为它们位于 PLC 外部而删除" in description
    assert "不得放入 missing_info" not in description


def test_words_that_contain_to_do_not_pull_analog_module_pattern():
    result = classify_request("FX3U motor start stop")

    assert "TO" not in result["selected_instructions"]
    assert "pattern_k" not in result["matched_ids"]


def test_y000_does_not_pull_examples_that_only_mention_y0():
    result = classify_request("FX3U PLSY K1000 K0 Y000")

    assert "example_plsy" in result["matched_ids"]
    assert "example_drvi" not in result["matched_ids"]
    assert "example_zrn" not in result["matched_ids"]


@pytest.mark.parametrize(
    ("query", "expected_example", "forbidden_examples"),
    [
        (
            "FX3U PLSY K1000 K0 Y0",
            "example_plsy",
            {"example_drvi", "example_zrn"},
        ),
        (
            "FX3U DRVI K100 K1000 Y0 Y4",
            "example_drvi",
            {"example_plsy", "example_zrn"},
        ),
        (
            "FX3U ZRN K1000 K5000 X3 Y0",
            "example_zrn",
            {"example_plsy", "example_drvi"},
        ),
    ],
)
def test_y0_operand_does_not_cross_route_motion_examples(
    query, expected_example, forbidden_examples
):
    result = classify_request(query)

    assert expected_example in result["matched_ids"]
    assert forbidden_examples.isdisjoint(result["matched_ids"])


@pytest.mark.parametrize("include_core", [False, True])
def test_assembled_prompt_budget_keeps_whole_required_output_contract(include_core):
    result = classify_request(
        "FX3U PLSY DRVI ZRN PID RS2 TO FROM high-speed counter serial positioning analog"
    )
    prompt = pattern_library.assemble_prompt(
        result,
        target_mode="ladder",
        include_core=include_core,
        plc_model="FX3U",
    )

    assert len(prompt) <= pattern_library.MAX_ASSEMBLED_CHARS
    assert "device_comments" in prompt
    assert "rungs" in prompt
    assert prompt.count("{") == prompt.count("}")
