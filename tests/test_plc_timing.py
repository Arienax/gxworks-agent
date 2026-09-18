from plc.ir import build_plc_ir
from plc.timing import (
    analyze_scan_timing,
    decode_scan_monitor_values,
    estimate_instruction,
    scan_monitor_profile,
)


def instruction(opcode, operands):
    return {"type": "APP_INSTR", "opcode": opcode, "operands": operands, "label": ""}


def rung(rung_id, inputs=None, outputs=None):
    return {
        "rung_id": rung_id,
        "debug_note": "",
        "header_element": None,
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


def ladder(*rungs):
    return {"device_comments": {}, "rungs": list(rungs)}


def test_fx3u_official_timing_profile_estimates_fixed_and_parameterized_ops():
    mov = estimate_instruction("MOV", ["K1", "D0"], plc_model="FX3U")
    bmov = estimate_instruction("BMOV", ["D0", "D100", "K10"], plc_model="FX3U")
    unknown = estimate_instruction("FUTURE_OP", [], plc_model="FX3U")

    assert mov["known"] is True
    assert mov["worst_us"] == 0.64
    assert mov["source_pdf_page"] == 938
    assert bmov["parameter_count"] == 10
    assert bmov["worst_us"] == 18.3
    assert bmov["confidence"] == "high"
    assert unknown["known"] is False
    assert unknown["confidence"] == "low"


def test_scan_monitor_uses_read_only_fx3u_d8010_to_d8012_profile():
    profile = scan_monitor_profile("FX3U")
    assert profile["read_only"] is True
    assert profile["devices"] == {
        "current": "D8010",
        "minimum": "D8011",
        "maximum": "D8012",
    }
    assert profile["unit_ms"] == 0.1
    assert decode_scan_monitor_values(
        {"D8010": 68, "D8011": 59, "D8012": 144}, "FX3U"
    ) == {"current_ms": 6.8, "minimum_ms": 5.9, "maximum_ms": 14.4}


def test_scan_budget_and_wcet_are_persisted_with_uncertainty_and_coverage():
    program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[{"type": "NO", "address": "X0", "label": ""}],
                outputs=[instruction("DRVI", ["K100", "K1000", "Y0", "Y4"])],
            )
        ),
        analysis_config={"timing": {"scan_budget_ms": 0.1}},
    )
    performance = program["timing"]["performance"]

    assert performance["supported"] is True
    assert performance["estimate"]["is_exact"] is False
    assert performance["estimate"]["instruction_coverage"] == 1.0
    assert performance["estimate"]["worst_ms"] > 0.1
    assert performance["scan_budget"]["status"] == "exceeded"
    assert performance["source"]["manual"] == "JY997D16601 Rev.R"
    assert "SCAN_BUDGET_WARNING" in [
        item["code"] for item in program["analysis"]["findings"]
    ]


def test_unknown_opcode_lowers_coverage_instead_of_claiming_exact_timing():
    result = analyze_scan_timing(
        [
            {
                "id": "N0001",
                "order": 0,
                "regions": ["CONTROL"],
                "instructions": [
                    {"op": "LD", "args": ["X0"], "path": "input"},
                    {"op": "FUTURE_OP", "args": [], "path": "output"},
                ],
            }
        ],
        plc_model="FX3U",
    )
    assert result["estimate"]["instruction_coverage"] == 0.5
    assert result["estimate"]["unknown_opcodes"] == ["FUTURE_OP"]
    assert result["estimate"]["is_exact"] is False


def test_fx5u_timing_is_unavailable_instead_of_reusing_fx3u_values():
    result = analyze_scan_timing([], plc_model="FX5U")
    assert result["supported"] is False
    assert result["estimate"]["worst_ms"] is None
    assert result["scan_monitor"]["available"] is False


def test_explicit_short_input_pulse_gets_evidence_backed_capture_warning():
    from plc.semantics import infer_semantic_requirements

    requirements = infer_semantic_requirements(
        "X0 输入脉宽 50us，每次上升沿触发一次计数，INC D0"
    )
    program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[{"type": "P", "address": "X0", "label": "高速脉冲"}],
                outputs=[instruction("INC", ["D0"])],
            )
        ),
        semantic_requirements=requirements,
    )

    assessment = program["timing"]["pulse_capture_assessments"][0]
    assert assessment["status"] == "pulse_loss_risk"
    assert assessment["devices"] == ["X0"]
    assert assessment["pulse_width_ms"] == 0.05
    assert assessment["comparison_basis"] == ["static_worst_scan"]
    assert assessment["is_guarantee"] is False
    finding = next(
        item
        for item in program["analysis"]["findings"]
        if item["code"] == "PULSE_LOSS_WARNING"
    )
    assert finding["addresses"] == ["X0"]
    assert any("pulse_width_ms=0.05" == item for item in finding["evidence"])


def test_pulse_capture_does_not_invent_risk_without_width_or_for_output_width():
    from plc.semantics import infer_semantic_requirements

    ladder_data = ladder(
        rung(
            1,
            inputs=[{"type": "P", "address": "X0", "label": "按钮"}],
            outputs=[instruction("INC", ["D0"])],
        )
    )
    no_width = build_plc_ir(
        ladder_data,
        semantic_requirements=infer_semantic_requirements("每次按下 X0 一次计数"),
    )
    output_width = build_plc_ir(
        ladder_data,
        semantic_requirements=infer_semantic_requirements("Y0 输出脉宽 20ms"),
    )

    for program in (no_width, output_width):
        assert program["timing"]["pulse_capture_assessments"] == []
        assert "PULSE_LOSS_WARNING" not in [
            item["code"] for item in program["analysis"]["findings"]
        ]


def test_scan_budget_participates_in_pulse_capture_decision_without_claiming_proof():
    from plc.semantics import infer_semantic_requirements

    program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[{"type": "P", "address": "X0", "label": "输入"}],
                outputs=[instruction("INC", ["D0"])],
            )
        ),
        semantic_requirements=infer_semantic_requirements("X0 输入脉宽 5ms"),
        analysis_config={"timing": {"scan_budget_ms": 10}},
    )

    assessment = program["timing"]["pulse_capture_assessments"][0]
    assert assessment["status"] == "pulse_loss_risk"
    assert assessment["comparison_bound_ms"] == 10.0
    assert assessment["comparison_basis"] == [
        "static_worst_scan",
        "configured_scan_budget",
    ]
    assert assessment["decision"] == "consider_verified_interrupt_or_high_speed_capture"
