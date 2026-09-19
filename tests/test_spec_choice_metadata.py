"""Candidate controls retain user choices across analysis, review and storage."""

import copy

from plc.specification.confirmed import (
    build_review_draft,
    canonicalize_confirmed_spec,
    restore_review_choices,
    validate_spec_draft,
)


def _analysis():
    return {
        "summary": "启动保持停止",
        "plc_model": "FX3U",
        "missing_info": [
            {"id": "start_input", "question": "启动按钮接哪个输入？", "required": True},
            {"id": "stop_input", "question": "停止按钮接哪个输入？（常开还是常闭）", "required": True},
            {"id": "output_coil", "question": "控制对象输出用哪个Y？", "required": True},
        ],
        "suggested_io": {"X": {"X0": "启动按钮", "X1": "停止按钮"}, "Y": {"Y0": "控制输出"}},
    }


def test_analysis_choices_and_suggestion_survive_merge_and_canonicalization():
    analysis = {"missing_info": [{"id": "interface", "question": "输出接口", "options": ["0-10V / 4-20mA", "RS-485"],
                                  "default": "RS-485", "required": True}]}
    draft = build_review_draft(analysis)
    parameter = draft["parameters"][0]
    assert parameter["options"] == ["0-10V / 4-20mA", "RS-485"]
    assert parameter["suggested_default"] == "RS-485"
    assert parameter["value"] == ""
    issues = validate_spec_draft(draft)
    assert issues["errors"] == []
    assert "required_parameter_missing" in {issue["code"] for issue in issues["warnings"]}

    parameter.update(value="用户自定义接口", source="user")
    merged = build_review_draft(analysis, draft)
    canonical = canonicalize_confirmed_spec(merged)
    assert canonical["parameters"][0] == parameter
    assert canonicalize_confirmed_spec(canonical)["parameters"] == canonical["parameters"]


def test_historical_choices_match_stable_id_before_wording_and_keep_user_answers():
    analysis = {"missing_info": [
        {"id": "interface", "question": "新的问题措辞", "options": ["FX3U-32MT/ES-A", "其他"], "default": "其他"},
        {"question": "定时方式", "options": ["每次启动", "每日一次"]},
    ]}
    draft = {"parameters": [
        {"id": "interface", "name": "原来的问题措辞", "value": "手动填写", "source": "user"},
        {"name": "定时方式", "value": "每日一次"},
    ]}
    before = copy.deepcopy(draft)
    restored = restore_review_choices(draft, analysis)
    assert draft == before
    assert restored["parameters"][0]["options"] == ["FX3U-32MT/ES-A", "其他"]
    assert restored["parameters"][0]["value"] == "手动填写"
    assert restored["parameters"][0]["source"] == "user"
    assert restored["parameters"][1]["options"] == ["每次启动", "每日一次"]


def test_missing_model_choices_offer_only_allocated_addresses_without_assuming_polarity():
    draft = build_review_draft(_analysis())
    start, stop, output = draft["parameters"]
    assert start["options"] == ["X0", "X1"]
    assert start["suggested_default"] == "X0"
    assert stop["options"] == ["X1，常开", "X1，常闭", "X0，常开", "X0，常闭"]
    assert "suggested_default" not in stop
    assert output["options"] == ["Y0"]
    assert [parameter["value"] for parameter in draft["parameters"]] == ["", "", ""]

    for parameter in draft["parameters"]:
        parameter.pop("options", None)
        parameter.pop("suggested_default", None)
    old_analysis = _analysis()
    old_analysis["io_allocation"] = old_analysis.pop("suggested_io")
    draft.pop("io_table")
    restored = restore_review_choices(draft, old_analysis)
    assert restored["parameters"][1]["options"] == ["X1，常开", "X1，常闭", "X0，常开", "X0，常闭"]


def test_address_and_contact_answer_remains_explicit_across_canonicalization():
    draft = build_review_draft(_analysis())
    for parameter, value in zip(draft["parameters"], ["X2", "X1，常闭", "Y0"]):
        parameter.update(value=value, source="user")
    assert validate_spec_draft(draft)["errors"] == []
    canonical = canonicalize_confirmed_spec(draft)
    assert {row["address"] for row in canonical["io_table"]} == {"X2", "X1", "Y0"}
    assert [(parameter["id"], parameter["value"]) for parameter in canonical["parameters"]] == [("stop_input", "X1，常闭")]
    assert canonicalize_confirmed_spec(canonical)["parameters"] == canonical["parameters"]


def test_address_alone_does_not_answer_a_combined_contact_question():
    draft = build_review_draft(_analysis())
    for parameter, value in zip(draft["parameters"], ["X0", "X1", "Y0"]):
        parameter["value"] = value
    issues = validate_spec_draft(draft)
    assert issues["errors"] == []
    assert any(
        issue["code"] == "contact_type_missing"
        and issue["path"] == "$.parameters[1].value"
        and issue.get("blocking") is False
        for issue in issues["warnings"]
    )


def test_unallocated_or_non_address_questions_do_not_gain_invented_options():
    analysis = {"missing_info": [
        {"id": "start_input", "question": "启动按钮接哪个输入？"},
        {"id": "mode", "question": "运行模式？"},
    ]}
    draft = build_review_draft(analysis)
    assert all(parameter["options"] == [] for parameter in draft["parameters"])
