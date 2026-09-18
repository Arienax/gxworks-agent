import copy

from plc.ir import apply_network_patch, build_plc_ir, canonical_sha256
from plc.semantics import infer_semantic_requirements
from plc.static_analysis import trace_upstream


def contact(kind, address, label=""):
    return {"type": kind, "address": address, "label": label}


def compare(expression, label=""):
    return {"type": "BLOCK_INPUT", "expression": expression, "label": label}


def instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def coil(address, label=""):
    return {"type": "COIL", "address": address, "label": label}


def timer(address, value, label=""):
    return {"type": "TIMER", "address": address, "value": value, "label": label}


def rung(rung_id, *, inputs=None, outputs=None, header=None, note=""):
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


def ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def finding_codes(program):
    return [item["code"] for item in program["analysis"]["findings"]]


def test_dependency_graph_supports_forward_and_reverse_root_cause_trace():
    program = build_plc_ir(
        ladder(
            rung(1, inputs=[contact("NO", "X0")], outputs=[coil("M0")]),
            rung(
                2,
                inputs=[contact("NO", "M0")],
                outputs=[instruction("MOV", ["K1", "D0"])],
            ),
            rung(3, header=compare("= D0 K1"), outputs=[coil("Y0")]),
        )
    )

    graph = program["analysis"]["dependency_graph"]
    assert graph["forward"]["X0"] == ["M0"]
    assert "D0" in graph["forward"]["M0"]
    assert graph["forward"]["D0"] == ["Y0"]
    assert any(
        item["device"] == "M0"
        and item["relation"] == "same_scan_write_before_read"
        for item in graph["network_edges"]
    )

    trace = trace_upstream(program["analysis"], "Y0")
    assert set(trace["devices"]) == {"X0", "M0", "D0", "Y0"}
    assert trace["roots"] == ["X0"]


def test_scan_dependencies_are_facts_and_only_explicit_expectation_becomes_warning():
    data = ladder(
        rung(1, inputs=[contact("NO", "M0")], outputs=[coil("Y0")]),
        rung(2, inputs=[contact("NO", "X0")], outputs=[coil("M0")]),
    )
    ordinary = build_plc_ir(data)
    assert any(
        edge["device"] == "M0" and edge["relation"] == "read_before_later_write"
        for edge in ordinary["analysis"]["dependency_graph"]["network_edges"]
    )
    assert "SAME_SCAN_READ_BEFORE_WRITE" not in finding_codes(ordinary)

    contracted = build_plc_ir(
        data,
        analysis_config={
            "same_scan_expectations": [
                {
                    "device": "M0",
                    "reader_network": "N0001",
                    "writer_network": "N0002",
                }
            ]
        },
    )
    assert "SAME_SCAN_READ_BEFORE_WRITE" in finding_codes(contracted)


def test_multiple_set_rst_is_valid_and_hmi_owned_latch_is_not_reported():
    normal = build_plc_ir(
        ladder(
            rung(1, outputs=[instruction("SET", ["M10"])]),
            rung(2, outputs=[instruction("SET", ["M10"])]),
            rung(3, outputs=[instruction("RST", ["M10"])]),
            rung(4, outputs=[instruction("RST", ["M10"])]),
        )
    )
    assert "MULTIPLE_WRITER" not in finding_codes(normal)
    assert "LATCH_WITHOUT_RESET" not in finding_codes(normal)

    hmi_owned = build_plc_ir(
        ladder(
            rung(1, outputs=[instruction("SET", ["M20"], "由 HMI 复位")]),
            comments={"M20": "HMI 保持命令"},
        )
    )
    assert "LATCH_WITHOUT_RESET" not in finding_codes(hmi_owned)

    program_owned = build_plc_ir(
        ladder(rung(1, outputs=[instruction("SET", ["Y0"], "运行保持")]))
    )
    assert "LATCH_WITHOUT_RESET" in finding_codes(program_owned)


def test_mixed_writer_and_unprotected_direction_pair_are_reported():
    data = ladder(
        rung(1, inputs=[contact("NO", "X0")], outputs=[coil("Y0", "正转")]),
        rung(2, inputs=[contact("NO", "X1")], outputs=[instruction("SET", ["Y0"])]),
        rung(
            3,
            inputs=[contact("NO", "X2"), contact("NC", "Y0", "正转互锁")],
            outputs=[coil("Y1", "反转")],
        ),
        comments={"Y0": "正转", "Y1": "反转"},
    )
    program = build_plc_ir(data)
    assert "MULTIPLE_WRITER" in finding_codes(program)
    assert "MUTEX_NOT_ENFORCED" in finding_codes(program)
    assert program["analysis"]["mutex"][0]["source"] == "unambiguous_labels"


def test_edge_first_scan_and_timer_semantics_have_deterministic_findings():
    edge_requirement = infer_semantic_requirements("每次按下 X0 一次，D0 加一")
    edge_program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[contact("NO", "X0")],
                outputs=[instruction("INC", ["D0"])],
            )
        ),
        semantic_requirements=edge_requirement,
    )
    assert "EDGE_MISUSE" in finding_codes(edge_program)

    init_program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[contact("NO", "M8000", "运行常通")],
                outputs=[instruction("MOV", ["K100", "D100"], "初始化默认参数")],
                note="上电初始化默认参数",
            )
        )
    )
    assert "INIT_VALUE_OVERWRITE_WARNING" in finding_codes(init_program)

    timer_program = build_plc_ir(
        ladder(
            rung(
                1,
                inputs=[contact("P", "X0")],
                outputs=[timer("T0", "K10")],
            )
        )
    )
    assert "TIMER_CANNOT_COMPLETE" in finding_codes(timer_program)


def test_unreachable_and_dead_end_states_respect_explicit_terminal_contract():
    data = ladder(
        rung(
            1,
            inputs=[contact("P", "M8002")],
            outputs=[instruction("MOV", ["K0", "D0"], "初始状态")],
        ),
        rung(
            2,
            header=compare("= D0 K0", "待机"),
            inputs=[contact("P", "X0")],
            outputs=[instruction("MOV", ["K1", "D0"], "进入运行")],
        ),
        rung(
            3,
            header=compare("= D0 K1", "运行"),
            inputs=[contact("NO", "X1")],
            outputs=[instruction("MOV", ["K2", "D0"], "进入停止态")],
        ),
        rung(4, header=compare("= D0 K2", "停止态"), outputs=[coil("Y0")]),
        rung(5, header=compare("= D0 K3", "备用态"), outputs=[coil("Y1")]),
    )
    program = build_plc_ir(
        data,
        analysis_config={"terminal_states": {"D0": [2]}},
    )

    state = program["analysis"]["state_analysis"][0]
    assert state["unreachable_states"] == [3]
    assert state["dead_end_states"] == [3]
    assert state["terminal_states"] == [2]
    assert "UNREACHABLE_STATE" in finding_codes(program)
    assert "DEAD_END_STATE" in finding_codes(program)


def test_network_patch_recomputes_dependency_graph_and_findings():
    program = build_plc_ir(
        ladder(rung(1, inputs=[contact("NO", "X0")], outputs=[coil("M0")])),
        revision=5,
    )
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["outputs"] = [coil("Y0")]
    updated = apply_network_patch(
        program,
        {
            "base_revision": 5,
            "base_ir_sha256": canonical_sha256(program),
            "operations": [
                {
                    "operation": "modify_network",
                    "network": "N0001",
                    "ladder": replacement,
                }
            ],
        },
    )

    assert "M0" not in updated["analysis"]["dependency_graph"]["nodes"]
    assert updated["analysis"]["dependency_graph"]["forward"]["X0"] == ["Y0"]
