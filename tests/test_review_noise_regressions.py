import pytest

from inspection.engine import run_local_inspection
from inspection.models import merge_inspection_reports, normalize_finding
from plc.review import review_ladder


def rung(rung_id, inputs=None, outputs=None, header=None, shared=None, note=""):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": header,
        "shared_inputs": shared or [],
        "branches": [
            {
                "branch_id": 1,
                "inputs": inputs or [],
                "outputs": outputs or [],
            }
        ],
    }


def instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def categories(findings):
    return [item.category for item in findings]


def test_multiple_set_and_reset_writers_are_not_duplicate_output_owners():
    data = ladder(
        rung(1, outputs=[instruction("SET", ["M10"])]),
        rung(2, outputs=[instruction("SET", ["M10"])]),
        rung(3, outputs=[instruction("RST", ["M10"])]),
        rung(4, outputs=[instruction("RST", ["M10"])]),
    )

    findings = review_ladder(data)

    assert not any(item.address == "M10" for item in findings)


def test_coil_mixed_with_set_reset_remains_an_ownership_warning():
    data = ladder(
        rung(1, outputs=[{"type": "COIL", "address": "Y0", "label": ""}]),
        rung(2, outputs=[instruction("SET", ["Y0"])]),
        rung(3, outputs=[instruction("RST", ["Y0"])]),
    )

    findings = review_ladder(data)

    assert any(
        item.category == "output_ownership"
        and item.address == "Y0"
        and item.severity == "warning"
        for item in findings
    )


@pytest.mark.parametrize("model,first_scan,reset", [
    ("FX3U", "M8002", "RST"), ("FX3U", "M8002", "ZRST"), ("FX5U", "SM402", "RST")])
def test_first_scan_reset_does_not_return_as_a_legacy_ownership_warning(model, first_scan, reset):
    args = ["Y0"] if reset == "RST" else ["Y0", "Y1"]
    data = ladder(rung(1, inputs=[{"type": "NO", "address": first_scan}], outputs=[instruction(reset, args)]),
                  rung(2, inputs=[{"type": "NO", "address": "X0"}], outputs=[{"type": "COIL", "address": "Y0"}]))
    assert not any(item.address == "Y0" and "ownership" in item.category for item in review_ladder(data, plc_model=model))
    assert not any("Y0" in item["addresses"] and "ownership" in item["category"]
                   for item in run_local_inspection(data, plc_model=model)["findings"])


def test_range_reset_covers_interior_held_bits_in_both_reviewers():
    data = ladder(rung(1, inputs=[{"type": "NO", "address": "X0"}], outputs=[instruction("SET", ["M11"])]),
                  rung(2, inputs=[{"type": "NO", "address": "X1"}], outputs=[instruction("ZRST", ["M10", "M19"])]))
    assert not any(item.address == "M11" and "ownership" in item.category for item in review_ladder(data))
    assert not any("M11" in item["addresses"] and item["code"] in {"MULTIPLE_WRITER", "LATCH_WITHOUT_RESET"}
                   for item in run_local_inspection(data)["findings"])


def test_range_reset_does_not_require_set_owners_for_spare_bits():
    data = ladder(rung(1, inputs=[{"type": "NO", "address": "M8002"}],
                       outputs=[instruction("ZRST", ["M100", "M199"])]))
    assert not any(item.category == "set_reset_ownership" for item in review_ladder(data))


def test_external_or_hmi_owned_one_sided_latch_is_not_reported():
    data = ladder(
        rung(
            1,
            outputs=[instruction("SET", ["M20"], "由 HMI 复位")],
            note="M20 由上位机负责 RST",
        ),
        comments={"M20": "HMI 保持命令"},
    )

    assert review_ladder(data) == []


def test_inc_counter_value_for_hmi_does_not_require_ladder_read():
    data = ladder(
        rung(
            1,
            inputs=[{"type": "P", "address": "X0", "label": "计数脉冲"}],
            outputs=[instruction("INC", ["C1"], "累计")],
        ),
        comments={"C1": "HMI 累计数量"},
    )

    assert not any("counter" in item.category for item in review_ladder(data))


def test_pulse_driven_counter_without_ladder_reader_is_valid():
    data = ladder(
        rung(
            1,
            inputs=[{"type": "P", "address": "X0", "label": "计数脉冲"}],
            outputs=[{"type": "COUNTER", "address": "C1", "value": "K10", "label": ""}],
        ),
        comments={"C1": "HMI 显示计数"},
    )

    assert review_ladder(data) == []


def test_edge_only_timer_is_still_reported():
    data = ladder(
        rung(
            1,
            inputs=[{"type": "P", "address": "X0", "label": "启动脉冲"}],
            outputs=[{"type": "TIMER", "address": "T0", "value": "K10", "label": ""}],
        )
    )

    findings = review_ladder(data)

    assert any(
        item.category == "timer_path" and item.address == "T0"
        for item in findings
    )


def test_plain_data_register_multiwrite_is_not_inferred_as_state_machine():
    data = ladder(
        rung(1, outputs=[instruction("MOV", ["K100", "D100"], "配方一")]),
        rung(2, outputs=[instruction("MOV", ["K200", "D100"], "配方二")]),
        comments={"D100": "HMI 配方设定值"},
    )

    assert not any(item.category.startswith("state_") for item in review_ladder(data))


def test_real_state_machine_without_initialization_is_still_reported():
    data = ladder(
        rung(
            1,
            header={"type": "BLOCK_INPUT", "expression": "= D0 K0", "label": "状态0"},
            outputs=[instruction("MOV", ["K1", "D0"], "下一步")],
        ),
        rung(
            2,
            header={"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": "状态1"},
            outputs=[instruction("MOV", ["K2", "D0"], "下一步")],
        ),
        comments={"D0": "主状态"},
    )

    assert "state_initialization" in categories(review_ladder(data))


def test_motion_without_m8029_is_not_automatically_a_warning():
    data = ladder(
        rung(
            1,
            outputs=[instruction("DRVI", ["K100", "K1000", "Y0", "Y4"])],
        )
    )

    assert "motion_completion" not in categories(review_ladder(data, plc_model="FX3U"))


def test_internal_hmi_device_is_not_treated_as_missing_physical_io():
    confirmed = {
        "io_table": [
            {"kind": "C", "address": "C1", "label": "HMI 累计数量"},
            {"kind": "Y", "address": "Y0", "label": "主电机"},
        ]
    }

    findings = review_ladder(ladder(), confirmed_spec=confirmed)

    assert not any(item.address == "C1" for item in findings)
    assert any(item.category == "confirmed_io" and item.address == "Y0" for item in findings)


def test_alarm_io_is_reported_once_not_as_two_categories():
    confirmed = {
        "io_table": [
            {"kind": "X", "address": "X0", "label": "急停输入"},
        ]
    }
    data = ladder(comments={"X0": "急停"})

    findings = [item for item in review_ladder(data, confirmed_spec=confirmed) if item.address == "X0"]

    assert len(findings) == 1
    assert findings[0].category == "alarm_logic"


def test_hard_validation_failure_does_not_cascade_into_advisory_findings():
    data = ladder(
        rung(1, outputs=[{"type": "COIL", "address": "Y0", "label": ""}]),
        rung(
            2,
            outputs=[
                {"type": "COIL", "address": "Y0", "label": ""},
                instruction("SET", ["M1"]),
            ],
        ),
    )

    report = run_local_inspection(data)

    assert [(item["severity"], item["category"]) for item in report["findings"]] == [
        ("error", "hard_validation")
    ]


def test_ai_high_or_error_severity_cannot_become_a_hard_error():
    base = ladder(rung(1), comments={"Y0": "输出"})

    high = normalize_finding(
        {"source": "local", "severity": "high", "message": "风险", "evidence": [{"rung_id": 1}]},
        base_json=base,
        default_source="ai",
    )
    error = normalize_finding(
        {"severity": "error", "message": "风险", "evidence": [{"rung_id": 1}]},
        base_json=base,
        default_source="ai",
    )

    assert high["source"] == "ai"
    assert high["severity"] == "warning"
    assert error["severity"] == "warning"


def test_strategy_mismatch_keeps_hard_error_and_independent_logic_review():
    from plc.validation import ApproachContractValidationError, validate_ladder_full
    data = ladder(rung(1, outputs=[instruction("SET", ["M10"])]))
    confirmed = {"selected_approach": {"name": "CMP strategy", "generation_contract": {
        "required_opcodes": ["CMP"]}}}
    with pytest.raises(ApproachContractValidationError):
        validate_ladder_full(data, confirmed_spec=confirmed)
    report = run_local_inspection(data, confirmed_spec=confirmed, base_version_id="v4")
    assert report["base_version_id"] == "v4"
    assert any(item["category"] == "hard_validation" and item["severity"] == "error"
               for item in report["findings"])
    assert any(item["code"] == "LATCH_WITHOUT_RESET" and "M10" in item["addresses"]
               for item in report["findings"])


def test_ai_finding_without_valid_location_is_only_low_confidence_info():
    base = ladder(rung(1))

    finding = normalize_finding(
        {
            "severity": "error",
            "message": "无法定位的问题",
            "evidence": [{"rung_id": 999, "json_path": "$.rungs[99]", "address": "Y999"}],
            "fixable": True,
            "fix_instruction": "修改",
        },
        base_json=base,
        default_source="ai",
    )

    assert finding["severity"] == "info"
    assert finding["confidence"] == "low"
    assert finding["fixable"] is False


def test_ai_cannot_turn_missing_in_ladder_reader_into_an_actionable_defect():
    base = ladder(
        rung(1, outputs=[instruction("INC", ["C1"], "供 HMI 显示")]),
        comments={"C1": "累计数量"},
    )

    finding = normalize_finding(
        {
            "severity": "error",
            "message": "C1 在程序中写入后未找到读取指令",
            "evidence": [{"rung_id": 1, "address": "C1"}],
            "fixable": True,
            "fix_instruction": "增加 C1 触点",
        },
        base_json=base,
        default_source="ai",
    )

    assert finding["severity"] == "info"
    assert finding["confidence"] == "low"
    assert finding["fixable"] is False


def test_ai_pure_set_rst_duplicate_claim_is_only_an_info_note():
    base = ladder(
        rung(1, outputs=[instruction("SET", ["M10"])]),
        rung(2, outputs=[instruction("SET", ["M10"])]),
        rung(3, outputs=[instruction("RST", ["M10"])]),
    )

    finding = normalize_finding(
        {
            "severity": "error",
            "message": "M10 存在多处 SET/RST 写入冲突",
            "evidence": [{"rung_id": 1, "address": "M10"}],
            "fixable": True,
            "fix_instruction": "合并所有 SET/RST",
        },
        base_json=base,
        default_source="ai",
    )

    assert finding["severity"] == "info"
    assert finding["confidence"] == "low"
    assert finding["fixable"] is False


def test_ai_reusing_local_finding_id_merges_instead_of_duplicating():
    base = ladder(rung(1), comments={"Y0": "输出"})
    local = run_local_inspection(
        ladder(),
        confirmed_spec={"io_table": [{"kind": "Y", "address": "Y0", "label": "输出"}]},
        base_version_id="v1",
    )
    local_finding = local["findings"][0]
    ai = {
        "report_type": "program_review",
        "base_version_id": "v1",
        "base_json_hash": local["base_json_hash"],
        "status": "complete",
        "findings": [
            {
                "finding_id": local_finding["finding_id"],
                "severity": "error",
                "category": "different_ai_wording",
                "message": "同一问题的 AI 补充",
                "evidence": ["确认输出未使用"],
                "address": "Y0",
            }
        ],
    }

    merged = merge_inspection_reports(local, ai)

    assert len(merged["findings"]) == 1


@pytest.mark.parametrize("shape,expected", [("direct_edge", False), ("source_pulse", False),
                                            ("gated_pulse", True), ("level", True)])
def test_local_review_keeps_confirmed_source_edge_requirements(shape, expected):
    if shape == "source_pulse":
        data = ladder(rung(1, inputs=[{"type": "NO", "address": "X5"}],
                           outputs=[{"type": "PLS", "address": "M115"}]))
    elif shape == "gated_pulse":
        data = ladder(rung(1, inputs=[{"type": "NO", "address": "M103"}, {"type": "NO", "address": "X5"}],
                           outputs=[{"type": "PLS", "address": "M115"}]))
    else:
        data = ladder(rung(1, inputs=[{"type": "P" if shape == "direct_edge" else "NO", "address": "X5"}],
                           outputs=[instruction("INC", ["D0"])]))
    spec = {"plc_model": "FX3U", "execution_semantics": [
        {"semantic": "RISING_EDGE", "devices": ["X5"], "strict": True, "source": "user_review"}]}
    report = run_local_inspection(data, confirmed_spec=spec)
    findings = [f for f in report["findings"] if f["code"] == "EDGE_MISUSE"]
    assert bool(findings) is expected
    if expected:
        assert findings[0]["addresses"] == ["X5"]
        assert findings[0]["severity"] == "error"
        assert findings[0]["fixable"] is False
    without_contract = run_local_inspection(data, confirmed_spec={"user_notes": "X5必须上升沿"})
    assert not any(f["code"] == "EDGE_MISUSE" for f in without_contract["findings"])


def test_identical_missing_edge_proof_is_shown_once_without_dropping_requirements():
    from inspection.engine import _static_findings
    data = ladder(rung(1, inputs=[{"type": "NO", "address": "X5"}],
                       outputs=[instruction("INC", ["D0"])]))
    spec = {"execution_semantics": [
        {"semantic": "RISING_EDGE", "devices": ["X5"], "strict": True},
        {"semantic": "RISING_EDGE", "devices": ["X5"], "strict": True, "pulse_width_ms": 50.0}]}
    program, findings = _static_findings(data, spec, "FX3U")
    assert len(program["timing"]["coverage"]) == 2
    assert len([f for f in findings if f["code"] == "EDGE_MISUSE"]) == 1


@pytest.mark.parametrize("difference,expected_count", [
    ("explanation", 1), ("other_code", 2), ("other_rung", 2),
    ("missing_address", 2), ("missing_path", 2), ("path_prefix", 2),
    ("ambiguous_proofs", 3),
])
def test_ai_explanation_merges_only_a_unique_complete_local_proof(difference, expected_count):
    local_finding = {
        "finding_id": "local_guard", "source": "local", "severity": "warning",
        "category": "repeated_guard_invalidated", "code": "REPEATED_GUARD_INVALIDATED",
        "message": "前一分支复位 M10，后一分支再次判断 M10。",
        "rung_ids": [1], "addresses": ["M10"],
        "json_paths": ["$.rungs[0].branches[0].outputs[0]", "$.rungs[0].branches[1].inputs"],
        "evidence": ["guard=M10; write=M10=0"],
    }
    ai_finding = {
        "source": "ai", "severity": "warning", "category": "repeated_guard_invalidated",
        "message": "第二个复位动作会被前一分支阻断。", "rung_ids": [1], "addresses": ["M10", "M11"],
        "json_paths": ["$.rungs[0].branches[0].outputs[0]", "$.rungs[0].branches[1].inputs[0]",
                       "$.rungs[0].branches[1].outputs[0]"], "evidence": ["M11 的复位不能执行。"],
    }
    local_findings = [local_finding]
    if difference == "other_code":
        ai_finding["code"] = "OTHER_PROOF"
    elif difference == "other_rung":
        ai_finding["rung_ids"] = [2]
    elif difference == "missing_address":
        ai_finding["addresses"] = ["M11"]
    elif difference == "missing_path":
        ai_finding["json_paths"] = ai_finding["json_paths"][1:]
    elif difference == "path_prefix":
        ai_finding["json_paths"][0] = "$.rungs[0].branches[0].outputs[01]"
    elif difference == "ambiguous_proofs":
        local_findings.append({**local_finding, "finding_id": "other_guard", "addresses": ["M11"]})
    merged = merge_inspection_reports(
        {"findings": local_findings}, {"status": "complete", "findings": [ai_finding]})
    assert len(merged["findings"]) == expected_count
    if expected_count == 1:
        finding = merged["findings"][0]
        assert finding["finding_id"] == "local_guard"
        assert finding["code"] == "REPEATED_GUARD_INVALIDATED"
        assert finding["addresses"] == ["M10", "M11"]
        assert finding["evidence"] == ["guard=M10; write=M10=0", "M11 的复位不能执行。"]
        assert finding["fixable"] is False
