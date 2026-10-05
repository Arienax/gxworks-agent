import copy

import pytest
from hypothesis import given, strategies as st

from application.model_api import _normalize_analysis_result
from plc.ir import PLCIRValidationError, build_plc_ir, validate_plc_ir
from plc.semantics import (
    SUPPORTED_EXECUTION_SEMANTICS,
    infer_semantic_requirements,
    semantic_requirements_from_spec,
    strict_semantic_gaps,
)


@pytest.mark.parametrize("target,operation,evidence", [
    ({"kind": "opcode", "values": ["SFTL"]}, "forbid", "禁止 SFTL。"),
    ({"kind": "device", "values": ["Y0"]}, "require", "指定 Y000 输出。"),
    ({"kind": "instruction_instance", "opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
     "require", "指定 SFTL M10 M100 K128 K1。"),
    ({"kind": "category", "value": "devices"}, "clear", "清除设备限制。"),
])
def test_explicit_target_protocol_uses_four_core_shapes(target, operation, evidence):
    from plc.specification.explicit_constraint_claims import (
        explicit_target_contracts, explicit_constraint_claim_details, compile_explicit_constraint_claims,
    )
    claim = {"operation": operation, "scope": "global", "target": target, "evidence": [evidence]}
    assert explicit_constraint_claim_details([claim]) == []
    assert compile_explicit_constraint_claims([claim], evidence)["rejected"] == []
    required = {"opcode": ["kind", "values"], "device": ["kind", "values"],
                "instruction_instance": ["kind", "opcode", "operands"], "category": ["kind", "value"]}
    contract = explicit_target_contracts()[target["kind"]]
    assert contract["required_fields"] == required[target["kind"]]
    assert operation in contract["allowed_operations"]


@pytest.mark.parametrize("operation,kind,fields", [
    ("require", "category", {"value": "all"}),
    ("forbid", "category", {"value": "devices"}),
    ("forbid", "instruction_instance", {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}),
])
def test_explicit_target_invalid_operation_has_path_actual_and_required_fields(operation, kind, fields):
    from plc.specification.explicit_constraint_claims import explicit_constraint_claim_details, explicit_constraint_claim_violations
    target = {"kind": kind, **fields}
    claim = {"operation": operation, "scope": "global", "target": target, "evidence": ["用户原文"]}
    details = explicit_constraint_claim_details([claim])
    assert len(details) == 1 and details[0]["path"] == "$.explicit_constraint_claims[0].target"
    assert details[0]["actual"] == target and "kind" in details[0]["required_fields"]
    assert operation not in details[0]["allowed_operations"]
    assert explicit_constraint_claim_violations([claim]) == [details[0]["path"] + ": " + details[0]["message"]]


def test_missing_target_kind_exposes_all_legal_contracts_without_guessing():
    from plc.specification.explicit_constraint_claims import explicit_constraint_claim_details
    claim = {"operation": "require", "scope": "global", "target": {"category": "opcodes", "values": ["SFTL"]}, "evidence": []}
    details = explicit_constraint_claim_details([claim])
    assert {d["path"] for d in details} == {"$.explicit_constraint_claims[0].evidence", "$.explicit_constraint_claims[0].target.kind"}
    error = next(d for d in details if d["path"].endswith(".kind"))
    assert error["actual"] == claim["target"]
    assert error["allowed_types"] == ["category", "device", "instruction_instance", "opcode"]
    assert error["contracts"]["instruction_instance"]["example"] == {
        "kind": "instruction_instance", "opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}


@given(prefix=st.sampled_from(["X", "Y", "M", "D", "SM", "SD"]), index=st.integers(0, 127),
       evidence_padding=st.integers(0, 5), target_padding=st.integers(0, 5), lower=st.booleans())
def test_device_evidence_aliases_use_identity_and_do_not_renumber(prefix, index, evidence_padding, target_padding, lower):
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims
    evidence_device = prefix + "0" * evidence_padding + str(index)
    target_device = prefix + "0" * target_padding + str(index)
    if lower:
        evidence_device, target_device = evidence_device.lower(), target_device.lower()
    text = "指定 " + evidence_device + "。"
    receipt = compile_explicit_constraint_claims([{
        "operation": "require", "scope": "global", "target": {"kind": "device", "values": [target_device]}, "evidence": [text],
    }], text)
    assert receipt["rejected"] == []
    assert receipt["operations"] == [{"operation": "require", "kind": "device", "values": [prefix + str(index)]}]


@pytest.mark.parametrize("text,evidence,values,reason", [
    ("禁止 M8011 和手工逐位移位。", "禁止 M8011 和手工逐位移位。", ["M8011", "手工逐位移位"], "target_not_grounded_in_evidence"),
    ("使用 Y000。", "使用 Y0。", ["Y0"], "evidence_not_in_current_request"),
    ("禁止 M8011。", "禁止 M8011。", ["M8012"], "target_not_grounded_in_evidence"),
    ("@Y000", "@Y000", ["Y0"], "target_not_grounded_in_evidence"),
    ("Y000+K1", "Y000+K1", ["Y0"], "target_not_grounded_in_evidence"),
    ("MY000", "MY000", ["Y0"], "target_not_grounded_in_evidence"),
    ("Y000Z0", "Y000Z0", ["Y0"], "target_not_grounded_in_evidence"),
])
def test_device_grounding_retains_exact_evidence_and_token_boundaries(text, evidence, values, reason):
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims
    receipt = compile_explicit_constraint_claims([{
        "operation": "forbid", "scope": "global", "target": {"kind": "device", "values": values}, "evidence": [evidence],
    }], text)
    assert receipt["operations"] == [] and receipt["rejected"][0]["reason"] == reason


@pytest.mark.parametrize('bits', [8, 16, 32, 64])
@pytest.mark.parametrize('signed', [False, True])
def test_instruction_expression_finite_word_roundtrip_and_bit_invariants(bits, signed):
    from plc.instruction_definition import Expression, ValueType, evaluate_expression
    kind = {'kind': 'int', 'bits': bits, 'signed': signed}
    value_type = ValueType.from_mapping(kind)
    minimum, maximum = value_type.bounds
    edges = {minimum, maximum, 0, 1, maximum // 2}
    if signed:
        edges.add(-1)
    parameter = {'op': 'parameter', 'name': 'input', 'type': kind}
    invert = {'op': 'bit_not', 'type': kind, 'overflow': 'wrap', 'args': [parameter]}
    double_invert = {'op': 'bit_not', 'type': kind, 'overflow': 'wrap', 'args': [invert]}
    for value in sorted(edges):
        assert value_type.decode_bits(value & ((1 << bits) - 1)) == value
        assert evaluate_expression(double_invert, {'input': value}) == value
        expr = Expression.from_mapping(double_invert)
        assert Expression.from_mapping(expr.as_mapping()) == expr


@pytest.mark.parametrize('bits', [8, 16, 32])
def test_instruction_expression_signed_comparison_and_overflow_are_separate(bits):
    from plc.instruction_definition import DefinitionError, UnknownInstructionSemantics, evaluate_expression
    kind = {'kind': 'int', 'bits': bits, 'signed': True}
    boolean = {'kind': 'bool'}
    left = {'op': 'parameter', 'name': 'left', 'type': kind}
    right = {'op': 'parameter', 'name': 'right', 'type': kind}
    maximum = (1 << (bits - 1)) - 1
    compare = {'op': 'lt', 'type': boolean, 'args': [left, right]}
    assert evaluate_expression(compare, {'left': (1 << bits) - 1, 'right': 0}) is True
    total = {'op': 'add', 'type': kind, 'args': [left, right]}
    with pytest.raises(UnknownInstructionSemantics):
        evaluate_expression(total, {'left': maximum, 'right': 1})
    with pytest.raises(DefinitionError):
        evaluate_expression({**total, 'overflow': 'reject'}, {'left': maximum, 'right': 1})
    assert evaluate_expression({**total, 'overflow': 'wrap'}, {'left': maximum, 'right': 1}) == -(1 << (bits - 1))


def test_instruction_expression_canonical_symmetry_does_not_change_direction_or_type():
    from plc.instruction_definition import canonical_expression
    kind = {'kind': 'int', 'bits': 16, 'signed': True}
    a = {'op': 'device', 'name': 'D1450', 'type': kind}
    b = {'op': 'constant', 'value': -37, 'type': kind}
    expr = lambda op, left, right: {'op': op, 'type': {'kind': 'bool'}, 'args': [left, right]}
    assert canonical_expression(expr('lt', a, b)) == canonical_expression(expr('gt', b, a))
    assert canonical_expression(expr('lt', a, b)) != canonical_expression(expr('gt', a, b))
    assert canonical_expression(expr('eq', a, b)) == canonical_expression(expr('eq', b, a))


@pytest.mark.parametrize('bits', [8, 16, 32])
def test_widening_product_preserves_full_signed_result_and_rejects_narrowing(bits):
    from itertools import product
    from plc.instruction_definition import Expression, DefinitionError, evaluate_expression
    source = {'kind': 'int', 'bits': bits, 'signed': True}
    result = {**source, 'bits': bits * 2}
    raw = {'op': 'mul', 'type': result, 'args': [
        {'op': 'parameter', 'type': source, 'name': name} for name in ('left', 'right')]}
    expression = Expression.from_mapping(raw)
    values = range(-128, 128) if bits == 8 else [
        -(1 << (bits - 1)), -(1 << (bits - 1)) + 1, -73, -1, 0, 1, 97, (1 << (bits - 1)) - 1]
    for left, right in product(values, repeat=2):
        assert evaluate_expression(expression, {'left': left, 'right': right}) == left * right
    assert Expression.from_mapping(expression.as_mapping()) == expression
    if bits > 8:
        with pytest.raises(DefinitionError, match='narrow'):
            Expression.from_mapping({**raw, 'type': {**source, 'bits': bits // 2}})
    with pytest.raises(DefinitionError, match='reinterpret'):
        Expression.from_mapping({**raw, 'type': {**result, 'signed': False}})


def _input(kind, address, label=""):
    return {"type": kind, "address": address, "label": label}


def _compare(expression, label=""):
    return {"type": "BLOCK_INPUT", "expression": expression, "label": label}


def _instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def _coil(address, label=""):
    return {"type": "COIL", "address": address, "label": label}


def _rung(rung_id, *, header=None, inputs=None, outputs=None, note=""):
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


def _ladder(*rungs, comments=None):
    return {"device_comments": comments or {}, "rungs": list(rungs)}


def test_explicit_execution_syntax_covers_all_six_scan_semantics_without_free_prose_nlp():
    requirements = infer_semantic_requirements(
        "X0 高电平；X1 上升沿；X2 下降沿；首扫初始化 D0；"
        "每隔 100ms 周期执行采样；X3 中断输入。"
    )

    assert {item["semantic"] for item in requirements} == set(
        SUPPORTED_EXECUTION_SEMANTICS
    )
    cyclic = next(item for item in requirements if item["semantic"] == "CYCLIC")
    assert cyclic["period_ms"] == 100.0
    rising = next(item for item in requirements if item["semantic"] == "RISING_EDGE")
    assert rising["devices"] == ["X1"]


def test_agent_a_execution_claim_handles_paraphrase_without_keyword_growth():
    from plc.execution_intent import compile_execution_intent_claims

    text = "X2 一亮就给 M1 打一拍，东西没离开传感器之前不要再打一拍。"
    receipt = compile_execution_intent_claims([{
        "trigger": {
            "kind": "transition",
            "source_devices": ["X2"],
            "from": "0",
            "to": "1",
        },
        "effect": {"kind": "one_shot", "devices": ["M1"]},
        "rearm": "required",
        "evidence": [text],
    }], text)

    assert receipt["rejected"] == []
    assert receipt["requirements"] == [{
        "semantic": "RISING_EDGE",
        "devices": ["X2"],
        "effect_devices": ["M1"],
        "effect_kind": "one_shot",
        "rearm": "required",
        "evidence": text,
        "source": "agent_a_claim",
        "intent_id": receipt["accepted"][0]["claim_id"],
        "strict": True,
    }]


def test_execution_claim_compiler_normalizes_flexible_effect_and_rearm_metadata():
    from plc.execution_intent import compile_execution_intent_claims

    text = "X2 每次从 0 变成 1 时，M1 只产生一个扫描周期事件。"
    receipt = compile_execution_intent_claims([{
        "trigger": {
            "kind": "transition",
            "source_devices": ["X2"],
            "from": 0,
            "to": 1,
        },
        "effect": {"kind": "pulse", "devices": ["M1"]},
        "rearm": True,
        "evidence": [text],
    }], text)

    assert receipt["rejected"] == []
    requirement = receipt["requirements"][0]
    assert requirement["semantic"] == "RISING_EDGE"
    assert requirement["effect_kind"] == "pulse"
    assert requirement["rearm"] == "required"


def test_level_claim_does_not_require_value_or_rearm_enum_to_be_protocol_valid():
    from plc.execution_intent import (
        compile_execution_intent_claims,
        execution_intent_claim_violations,
    )

    text = "X1 动作时必须立即解除 M0。"
    claim = {
        "trigger": {"kind": "level", "source_devices": ["X1"]},
        "effect": {"kind": "reset", "devices": ["M0"]},
        "rearm": True,
        "evidence": [text],
    }
    assert execution_intent_claim_violations([claim]) == []
    receipt = compile_execution_intent_claims([claim], text)
    assert receipt["rejected"] == []
    assert receipt["requirements"][0]["semantic"] == "LEVEL"


def test_transition_claims_only_promote_high_confidence_source_edges():
    from plc.execution_intent import compile_execution_intent_claims

    text = (
        "X2 一到料就产生 M1。"
        "M1 事件用于本扫描计算。"
        "T0 到时后 Y1 保持为 ON。"
        "T1 到时后 M2 解除。"
    )
    claims = [
        {
            "trigger": {"kind": "transition", "source_devices": ["X2"], "from": 0, "to": 1},
            "effect": {"kind": "pulse", "devices": ["M1"]},
            "evidence": ["X2 一到料就产生 M1。"],
        },
        {
            "trigger": {"kind": "transition", "source_devices": ["M1"], "from": 0, "to": 1},
            "effect": {"kind": "compute", "devices": []},
            "evidence": ["M1 事件用于本扫描计算。"],
        },
        {
            "trigger": {"kind": "transition", "source_devices": ["T0"], "from": 0, "to": 1},
            "effect": {"kind": "level", "devices": ["Y1"]},
            "evidence": ["T0 到时后 Y1 保持为 ON。"],
        },
        {
            "trigger": {"kind": "transition", "source_devices": ["T1"], "from": 0, "to": 1},
            "effect": {"kind": "clear", "devices": ["M2"]},
            "evidence": ["T1 到时后 M2 解除。"],
        },
    ]
    receipt = compile_execution_intent_claims(claims, text)

    assert receipt["rejected"] == []
    assert {
        (item["semantic"], tuple(item["devices"]), item["strict"])
        for item in receipt["requirements"]
    } == {
        ("RISING_EDGE", ("X2",), True),
        ("LEVEL", ("T0",), False),
        ("LEVEL", ("T1",), False),
    }
    m1 = next(
        item for item in receipt["accepted"]
        if item["trigger"]["source_devices"] == ["M1"]
    )
    assert m1["projection_status"] == "advisory_transition"


def test_internal_transition_with_explicit_edge_notation_remains_hard_edge():
    from plc.execution_intent import compile_execution_intent_claims

    text = "T0 上升沿触发一次记录。"
    receipt = compile_execution_intent_claims([{
        "trigger": {"kind": "transition", "source_devices": ["T0"], "from": 0, "to": 1},
        "effect": {"kind": "event", "devices": []},
        "evidence": [text],
    }], text)

    assert receipt["rejected"] == []
    assert receipt["requirements"][0]["semantic"] == "RISING_EDGE"
    assert receipt["requirements"][0]["devices"] == ["T0"]


def test_semantic_normalization_merges_claim_and_explicit_fast_path_for_same_trigger():
    from plc.semantics import normalize_semantic_requirements

    normalized = normalize_semantic_requirements([
        {
            "semantic": "RISING_EDGE",
            "devices": ["X2"],
            "effect_devices": ["M1"],
            "effect_kind": "pulse",
            "evidence": "X2 一到料",
            "source": "agent_a_claim",
            "strict": True,
        },
        {
            "semantic": "RISING_EDGE",
            "devices": ["X2"],
            "evidence": "X2 0 -> 1",
            "source": "current_request_explicit",
            "strict": True,
        },
    ])

    assert len(normalized) == 1
    assert normalized[0]["devices"] == ["X2"]
    assert normalized[0]["effect_devices"] == ["M1"]
    assert normalized[0]["effect_kind"] == "pulse"


def test_agent_a_execution_claim_accepts_unique_confirmed_label_grounding():
    from plc.execution_intent import compile_execution_intent_claims

    text = "物料检测改成下降沿，离开后才算一次。"
    receipt = compile_execution_intent_claims([{
        "trigger": {
            "kind": "transition",
            "source_devices": ["X2"],
            "from": "1",
            "to": "0",
        },
        "effect": {"kind": "event", "devices": []},
        "rearm": "required",
        "evidence": [text],
    }], text, confirmed_spec={
        "io_table": [{"address": "X2", "label": "物料检测"}],
    })

    assert receipt["rejected"] == []
    assert receipt["requirements"][0]["semantic"] == "FALLING_EDGE"
    assert receipt["requirements"][0]["devices"] == ["X2"]


def test_agent_a_execution_claim_rejects_ambiguous_confirmed_label_grounding():
    from plc.execution_intent import compile_execution_intent_claims

    text = "检测改成下降沿。"
    receipt = compile_execution_intent_claims([{
        "trigger": {
            "kind": "transition",
            "source_devices": ["X2"],
            "from": "1",
            "to": "0",
        },
        "effect": {"kind": "event", "devices": []},
        "rearm": "required",
        "evidence": [text],
    }], text, confirmed_spec={
        "io_table": [
            {"address": "X2", "label": "检测"},
            {"address": "X3", "label": "检测"},
        ],
    })

    assert receipt["requirements"] == []
    assert receipt["rejected"][0]["reason"] == "claimed_device_not_in_evidence"


def test_agent_a_execution_claim_requires_grounded_evidence_and_devices():
    from plc.execution_intent import compile_execution_intent_claims

    text = "X2 检测物料。"
    ungrounded = compile_execution_intent_claims([{
        "trigger": {"kind": "transition", "source_devices": ["X2"], "from": "0", "to": "1"},
        "effect": {"kind": "one_shot", "devices": ["M1"]},
        "rearm": "required",
        "evidence": ["模型自己补的句子"],
    }], text)
    assert ungrounded["requirements"] == []
    assert ungrounded["rejected"][0]["reason"] == "evidence_not_in_current_request"

    wrong_device = compile_execution_intent_claims([{
        "trigger": {"kind": "transition", "source_devices": ["X3"], "from": "0", "to": "1"},
        "effect": {"kind": "unspecified", "devices": []},
        "rearm": "required",
        "evidence": [text],
    }], text)
    assert wrong_device["requirements"] == []
    assert wrong_device["rejected"][0]["reason"] == "claimed_device_not_in_evidence"


def test_formal_binary_transition_is_a_narrow_explicit_fast_path():
    requirements = infer_semantic_requirements(
        "X2 从 0 变成 1；X4 从 1 变成 0。"
    )
    assert {
        (item["semantic"], tuple(item["devices"]))
        for item in requirements
    } == {
        ("RISING_EDGE", ("X2",)),
        ("FALLING_EDGE", ("X4",)),
    }


def test_free_form_hold_wording_is_not_reclassified_by_core_keywords():
    assert infer_semantic_requirements(
        "X2 持续为 1 时不得重复产生 M1。"
    ) == []


def test_requirement_parser_preserves_only_explicit_physical_input_pulse_width():
    requirements = infer_semantic_requirements(
        "X0 输入脉宽 50us 时 INC D0；Y0 输出脉宽 20ms；"
        "T0 延时 2ms；每隔 10ms 周期采样。"
    )

    pulse = [item for item in requirements if item.get("pulse_width_ms") is not None]
    assert pulse == [
        {
            "semantic": "RISING_EDGE",
            "devices": ["X0"],
            "evidence": "X0 输入脉宽 50us 时 INC D0",
            "source": "requirement",
            "strict": True,
            "pulse_width_ms": 0.05,
        }
    ]
    cyclic = next(item for item in requirements if item["semantic"] == "CYCLIC")
    assert cyclic["period_ms"] == 10.0


def test_ir_annotates_level_edges_first_scan_cycle_and_interrupt():
    ladder = _ladder(
        _rung(1, inputs=[_input("NO", "X0")], outputs=[_coil("Y0")]),
        _rung(2, inputs=[_input("P", "X1")], outputs=[_instruction("INC", ["D0"])]),
        _rung(3, inputs=[_input("F", "X2")], outputs=[_coil("M0")]),
        _rung(
            4,
            inputs=[_input("P", "M8002", "上电初始化")],
            outputs=[_instruction("MOV", ["K1", "D10"], "初始化状态")],
            note="开机初始化默认状态",
        ),
        _rung(5, inputs=[_input("NO", "M8012")], outputs=[_coil("M1")]),
        _rung(6, inputs=[_input("NO", "X3", "中断输入")], outputs=[_coil("M2")]),
    )
    program = build_plc_ir(ladder, plc_model="FX3U")

    by_id = {item["id"]: item for item in program["networks"]}
    assert by_id["N0001"]["execution"]["semantics"] == ["LEVEL"]
    assert by_id["N0002"]["execution"]["semantics"] == ["RISING_EDGE"]
    assert by_id["N0003"]["execution"]["semantics"] == ["FALLING_EDGE"]
    assert by_id["N0004"]["execution"]["semantics"] == ["FIRST_SCAN"]
    assert by_id["N0005"]["execution"]["semantics"] == ["CYCLIC"]
    assert by_id["N0006"]["execution"]["execution_context"] == "INTERRUPT"
    assert program["timing"]["first_scan_networks"] == ["N0004"]
    assert program["timing"]["cyclic_sources"][0]["period_ms"] == 100.0
    assert program["timing"]["interrupt_networks"] == ["N0006"]
    assert validate_plc_ir(program) is program


def test_strict_requirement_coverage_blocks_level_instead_of_requested_edge():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("NO", "X0")],
            outputs=[_instruction("INC", ["D0"])],
        )
    )
    requirements = infer_semantic_requirements("X0 上升沿，D0 加一")
    program = build_plc_ir(ladder, semantic_requirements=requirements)

    assert program["timing"]["coverage"][0]["status"] == "unresolved"
    assert strict_semantic_gaps(program) == [program["timing"]["coverage"][0]]

    edge_ladder = copy.deepcopy(ladder)
    edge_ladder["rungs"][0]["branches"][0]["inputs"][0]["type"] = "P"
    edge_program = build_plc_ir(edge_ladder, semantic_requirements=requirements)
    assert strict_semantic_gaps(edge_program) == []


def test_first_scan_requirement_rejects_m8000_continuous_initialization_semantically():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("NO", "M8000", "运行常通")],
            outputs=[_instruction("MOV", ["K100", "D100"], "初始化默认参数")],
            note="上电初始化默认参数",
        )
    )
    requirements = infer_semantic_requirements("首扫初始化 D100 默认参数")
    program = build_plc_ir(ladder, semantic_requirements=requirements)

    assert program["timing"]["initialization"] == [
        {
            "network": "N0001",
            "status": "continuous_overwrite_risk",
            "trigger_devices": ["M8000"],
            "writes": ["D100"],
        }
    ]
    assert strict_semantic_gaps(program)[0]["semantic"] == "FIRST_SCAN"


@pytest.mark.parametrize("opcode,contact_kind,expected", [
    ("PLS", "NO", "RISING_EDGE"), ("PLF", "NO", "FALLING_EDGE"),
    ("PLS", "NC", "FALLING_EDGE"), ("PLF", "NC", "RISING_EDGE"),
])
def test_single_contact_pulse_output_proves_source_edge_without_changing_network_enable(opcode, contact_kind, expected):
    output = {"type": opcode, "address": "M10"}
    data = _ladder(_rung(1, inputs=[_input(contact_kind, "X2")], outputs=[output]))
    requirements = [{"semantic": expected, "devices": ["X2"], "strict": True}]
    program = build_plc_ir(data, semantic_requirements=requirements)
    assert program["networks"][0]["execution"]["semantics"] == ["LEVEL"]
    assert program["timing"]["coverage"][0]["status"] == "satisfied"
    assert strict_semantic_gaps(program) == []
    opposite = "FALLING_EDGE" if expected == "RISING_EDGE" else "RISING_EDGE"
    wrong = build_plc_ir(data, semantic_requirements=[{"semantic": opposite, "devices": ["X2"], "strict": True}])
    assert wrong["timing"]["coverage"][0]["status"] == "unresolved"
    assert validate_plc_ir(program) is program


@pytest.mark.parametrize("extra_condition", ["series", "parallel", "other_device"])
def test_pulse_output_does_not_certify_an_unproven_source_edge(extra_condition):
    inputs = [_input("NO", "X2")]
    if extra_condition == "series":
        inputs.append(_input("NO", "X3"))
    elif extra_condition == "parallel":
        inputs = [{"type": "parallel_block", "branches": [inputs, [_input("NO", "X3")]]}]
    else:
        inputs = [_input("NO", "X3")]
    data = _ladder(_rung(1, inputs=inputs, outputs=[{"type": "PLS", "address": "M10"}]))
    program = build_plc_ir(data, semantic_requirements=[{"semantic": "RISING_EDGE", "devices": ["X2"], "strict": True}])
    assert program["timing"]["coverage"][0]["status"] == "unresolved"


def test_state_machine_is_structured_with_separate_transition_and_output_regions():
    ladder = _ladder(
        _rung(
            1,
            inputs=[_input("P", "M8002", "首扫")],
            outputs=[_instruction("MOV", ["K10", "D100"], "初始化 IDLE")],
        ),
        _rung(
            2,
            header=_compare("= D100 K10", "IDLE"),
            inputs=[_input("P", "X0", "启动沿")],
            outputs=[_instruction("MOV", ["K20", "D100"], "进入 CLAMP")],
        ),
        _rung(
            3,
            header=_compare("= D100 K20", "CLAMP"),
            inputs=[_input("NO", "X1", "夹紧到位")],
            outputs=[_instruction("MOV", ["K30", "D100"], "进入 PROCESS")],
        ),
        _rung(
            4,
            header=_compare("= D100 K20", "CLAMP"),
            outputs=[_coil("Y0", "夹紧输出")],
        ),
        _rung(
            5,
            header=_compare("= D100 K30", "PROCESS"),
            inputs=[_input("NO", "X2", "加工完成")],
            outputs=[_instruction("MOV", ["K10", "D100"], "返回 IDLE")],
        ),
    )
    program = build_plc_ir(ladder)
    state_machine = program["logic"]["state_machines"][0]

    assert state_machine["state_register"] == "D100"
    assert [item["value"] for item in state_machine["states"]] == [10, 20, 30]
    assert state_machine["initialization"][0]["target_state"] == 10
    assert {(item["from"], item["to"]) for item in state_machine["transitions"]} == {
        (10, 20),
        (20, 30),
        (30, 10),
    }
    assert state_machine["state_outputs"][0]["op"] == "COIL"
    transition_region = next(
        item for item in program["logic"]["regions"] if item["kind"] == "STATE_TRANSITION"
    )
    output_region = next(
        item for item in program["logic"]["regions"] if item["kind"] == "STATE_OUTPUT"
    )
    assert set(transition_region["network_refs"]) == {"N0001", "N0002", "N0003", "N0005"}
    assert output_region["network_refs"] == ["N0004"]
    assert state_machine["unreachable_state_candidates"] == []
    assert state_machine["dead_end_state_candidates"] == []


def test_ir_validation_detects_tampered_execution_or_logic_analysis():
    program = build_plc_ir(
        _ladder(_rung(1, inputs=[_input("P", "X0")], outputs=[_coil("M0")]))
    )
    tampered = copy.deepcopy(program)
    tampered["networks"][0]["execution"]["semantics"] = ["LEVEL"]
    with pytest.raises(PLCIRValidationError, match="networks is stale|networks.*stale"):
        validate_plc_ir(tampered)

    tampered = copy.deepcopy(program)
    tampered["logic"]["execution_model"] = "invented"
    with pytest.raises(PLCIRValidationError, match="logic is stale"):
        validate_plc_ir(tampered)


def test_confirmed_spec_semantics_are_not_reconstructed_from_summary_or_guide_prose():
    requirements = semantic_requirements_from_spec(
        {
            "summary": "每次按下 X9 一次，模型摘要里还有别的事件",
            "selected_approach": {"generation_guide": "X8 持续时执行"},
            "execution_semantics": [
                {
                    "semantic": "RISING_EDGE",
                    "devices": ["X0"],
                    "evidence": "用户确认",
                    "strict": True,
                }
            ],
        }
    )

    assert requirements == [{
        "semantic": "RISING_EDGE",
        "devices": ["X0"],
        "evidence": "用户确认",
        "source": "requirement",
        "strict": True,
    }]


def test_analysis_drops_model_invented_semantics_and_keeps_user_evidence():
    hallucinated = _normalize_analysis_result(
        {
            "summary": "普通输出控制",
            "execution_semantics": [
                {
                    "semantic": "FIRST_SCAN",
                    "devices": [],
                    "evidence": "模型自行假设",
                    "strict": True,
                }
            ],
        },
        user_text="X0 控制 Y0",
    )
    assert hallucinated["execution_semantics"] == []

    text = "按钮 X0 每来一下，只给 D0 记一次。"
    evidenced = _normalize_analysis_result(
        {
            "summary": "计数",
            "execution_intent_claims": [{
                "trigger": {"kind": "transition", "source_devices": ["X0"], "from": "0", "to": "1"},
                "effect": {"kind": "event", "devices": ["D0"]},
                "rearm": "required",
                "evidence": [text],
            }],
        },
        user_text=text,
    )
    assert evidenced["execution_semantics"][0]["semantic"] == "RISING_EDGE"
    assert evidenced["execution_semantics"][0]["devices"] == ["X0"]
    assert evidenced["execution_semantics"][0]["effect_devices"] == ["D0"]


# Retained serialized SFC requirements, independent of the retired canvas.
def test_saved_sfc_document_preserves_graph_properties_and_source():
    from copy import deepcopy
    from plc.sfc import document_graph, document_requirement, graph_requirement
    from shared.i18n import tr
    document = {"version": 1, "blocks": [
        {"temp_id": 1, "type": "step", "x": 0, "y": 0, "label": "启动 Y0", "properties": {"output": "Y0"}},
        {"temp_id": 2, "type": "transition", "x": 0, "y": 90, "label": "X1 到位"},
        {"temp_id": 3, "type": "step", "x": 0, "y": 180, "label": "停止 Y0"},
    ], "connections": [{"source_id": 1, "target_id": 2}, {"source_id": 2, "target_id": 3}],
       "io_config": {}, "future_metadata": {"kept": True}}
    before = deepcopy(document)
    graph = document_graph(document)
    text = document_requirement(document, translate=tr)
    assert text == graph_requirement(graph["nodes"], graph["edges"], {}, translate=tr)
    assert "启动 Y0" in text and "X1 到位" in text and "停止 Y0" in text
    graph["nodes"][0]["properties"]["output"] = "Y7"
    assert document == before


def test_saved_sfc_cli_is_read_only(tmp_path, capsys):
    import json
    from plc.sfc import main
    source = tmp_path / "control_flow.sfc"
    source.write_text(json.dumps({"version": 1, "blocks": [{"temp_id": 1, "type": "step", "x": 0, "y": 0, "label": "Y0"}], "connections": []}), encoding="utf-8")
    before = source.read_bytes()
    assert main([str(source)]) == 0
    assert "Y0" in capsys.readouterr().out
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]


def _effect_cases():
    import json
    from pathlib import Path
    return [json.loads(row) for row in (Path(__file__).resolve().parents[1] /
            'benchmarks/agent_b_instruction_effect_cases.jsonl').read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('case', _effect_cases(), ids=lambda c: c['case_id'])
def test_every_reviewed_form_binds_independent_fixed_native_answer(case):
    from plc.instruction_binding import bind_operation_intent, materialize_operation_references, check_operation_intents
    from application.compact_protocol import expand_compact_ladder
    from copy import deepcopy
    spec = case['confirmed_spec']
    before = deepcopy(spec)
    receipt = bind_operation_intent(spec['operation_intents'][0], target_model=case['plc_model'], confirmed_spec=spec)
    assert receipt['status'] == 'bound', receipt
    assert receipt['opcode'] == case['evaluation']['opcode']
    assert receipt['operands'] == case['evaluation']['operands']
    assert receipt['source_verification'] == 'source_checked' and receipt['hardware_effect'] == 'not_tested'
    gate = case['evaluation']['gate']
    contact = ('NC ' if gate['op'] == 'LDI' else 'NO ') + gate['args'][0]
    compact = {'r': [{'b': [{'i': [contact], 'o': ['OP operation']}]}]}
    native, diagnostic = materialize_operation_references(compact, spec, target_model=case['plc_model'])
    assert native['r'][0]['b'][0]['o'] == [' '.join([receipt['opcode'], *case['evaluation']['operands']])]
    assert diagnostic['stage'] == 'Core_operation_binding' and diagnostic['model_calls'] == 0
    assert compact['r'][0]['b'][0]['o'] == ['OP operation'] and spec == before
    rows, violations = check_operation_intents(expand_compact_ladder(native), spec, target_model=case['plc_model'])
    assert not violations and rows[0]['status'] == 'verified'
    # A displaced destination remains a real contradiction, including P forms.
    wrong = deepcopy(native)
    wrong['r'][0]['b'][0]['o'] = [native['r'][0]['b'][0]['o'][0] + ' K1']
    _, violations = check_operation_intents(expand_compact_ladder(wrong), spec, target_model=case['plc_model'])
    assert violations


def test_effect_matching_protects_user_confirmation_and_native_constraints():
    from copy import deepcopy
    from plc.instruction_binding import normalize_operation_intents, confirm_operation_intents, bind_operation_intent
    from plc.instruction_definition import DefinitionError
    intent = deepcopy(_effect_cases()[0]['confirmed_spec']['operation_intents'][0])
    candidate = normalize_operation_intents([intent], candidate=True, evidence_text='unrelated text')[0]
    assert candidate['status'] == 'candidate' and candidate['provenance']['source'] == 'model_candidate'
    assert candidate['provenance']['grounding_status'] == 'unresolved'
    assert bind_operation_intent(candidate, target_model='FX3U')['reason'] == 'intent_not_user_confirmed'
    confirmed = confirm_operation_intents([candidate], ['operation'])[0]
    assert bind_operation_intent(confirmed, target_model='FX3U')['operands'] == ['K-37', 'D1450', 'M610']
    assert bind_operation_intent(confirmed, target_model='FX5U')['status'] == 'unresolved'
    spoofed = {**candidate, 'status': 'confirmed'}
    with pytest.raises(DefinitionError, match='cannot confirm itself'):
        normalize_operation_intents([spoofed])
    spec = {'selected_approach': {'explicit_user_constraints': {'instruction_instances': [
        {'opcode': 'CMP', 'operands': ['D1450', 'K-37', 'M610']}]}}}
    assert bind_operation_intent(confirmed, target_model='FX3U', confirmed_spec=spec)['status'] == 'unresolved'
    bm = next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'BMOV')['confirmed_spec']['operation_intents'][0]
    assert bind_operation_intent({**bm, 'execution': {'trigger': 'level'}}, target_model='FX3U')['status'] == 'unresolved'


@pytest.mark.parametrize('extra', ['CMP K-37 D1450 M620', 'MOV K0 M611', 'RST M612', 'COIL M611', 'PLS M612'])
def test_native_effect_check_detects_extra_calls_and_neighbor_clobber(extra):
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    case = _effect_cases()[0]
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0'], 'o': ['CMP K-37 D1450 M610', extra]}]}]})
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert violations and rows[0]['status'] == 'violated'


def test_uninterpreted_enable_predicate_does_not_pass_as_a_plain_contact():
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    case = _effect_cases()[0]
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0', '> D1 K0'], 'o': ['CMP K-37 D1450 M610']}]}]})
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert not violations and rows[0]['status'] == 'unresolved'


@pytest.mark.parametrize('opcode,contact,inverted,status', [
    ('CMP', 'NO', False, 'verified'), ('CMP', 'P', False, 'violated'),
    ('CMPP', 'NO', False, 'verified'), ('CMPP', 'P', False, 'verified'),
    ('CMPP', 'F', False, 'violated'), ('CMPP', 'F', True, 'verified'),
    ('CMPP', 'P', True, 'violated'), ('CMPP', 'NC', True, 'verified'),
])
def test_instruction_trigger_gate_proof_covers_level_hold_and_both_edge_directions(opcode, contact, inverted, status):
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    from copy import deepcopy
    case = deepcopy(next(case for case in _effect_cases() if case['evaluation']['opcode'] == opcode))
    if inverted:
        intent = case['confirmed_spec']['operation_intents'][0]
        intent['enable'] = {'op': 'not', 'type': {'kind': 'bool'}, 'args': [intent['enable']]}
    ladder = expand_compact_ladder({'r': [{'b': [{'i': [contact + ' X0'], 'o': [' '.join([
        opcode, *case['evaluation']['operands']])]}]}]})
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == status
    assert bool(violations) == (status == 'violated')


@pytest.mark.parametrize('neighbor,status', [('D1711', 'violated'), ('D1712', 'violated'),
                                           ('D1713', 'violated'), ('D1714', 'verified')])
def test_double_multiplication_protects_all_four_result_words(neighbor, status):
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    case = next(case for case in _effect_cases() if case['evaluation']['opcode'] == 'DMUL')
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0'], 'o': [
        ' '.join(['DMUL', *case['evaluation']['operands']]), 'MOV K0 ' + neighbor]}]}]})
    rows, _ = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == status


@pytest.mark.parametrize('second,bound', [('D1411', False), ('D1412', True), ('D1410', False)])
def test_double_exchange_does_not_verify_overlapping_word_pairs(second, bound):
    from copy import deepcopy
    from plc.instruction_binding import bind_operation_intent
    from plc.instruction_effects import execute_behavior
    from plc.instruction_definition import UnknownInstructionSemantics
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    intent = deepcopy(next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'DXCH')[
        'confirmed_spec']['operation_intents'][0])
    intent['effects'][0]['value']['name'] = second
    intent['effects'][1]['target']['device'] = second
    receipt = bind_operation_intent(intent, target_model='FX3U')
    assert (receipt['status'] == 'bound') == bound
    behavior = next(g.value for g in registry.resolve('DXCH', cpu='FX3U').definition_facts
                    if g.dimension == 'effects.expression_write')
    if not bound:
        with pytest.raises(UnknownInstructionSemantics, match='Overlapping'):
            execute_behavior(behavior, {'D1': 'D1410', 'D2': second},
                             memory={'D1410': 5, second: 7}, state={'M8160': False})


@pytest.mark.parametrize('opcode,invalid', [('DIV', 'zero'), ('DDIV', 'zero'),
                                         ('DIV', 'overflow'), ('DDIV', 'overflow')])
def test_source_checked_known_division_precondition_is_a_violation(opcode, invalid):
    from copy import deepcopy
    from plc.instruction_binding import bind_operation_intent, check_operation_intents
    case = deepcopy(next(c for c in _effect_cases() if c['evaluation']['opcode'] == opcode))
    intent = case['confirmed_spec']['operation_intents'][0]
    for effect in intent['effects']:
        expression = effect['value']
        if invalid == 'zero':
            expression['args'][1]['value'] = 0
        else:
            expression['args'][0] = {'op': 'constant', 'type': expression['type'],
                                     'value': -(1 << (expression['type']['bits'] - 1))}
            expression['args'][1]['value'] = -1
    receipt = bind_operation_intent(intent, target_model='FX3U')
    assert receipt['reason'] == 'known_parameter_constraint_violation'
    rows, violations = check_operation_intents({'rungs': []}, case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == 'violated' and violations == rows


@pytest.mark.parametrize('operation,known,expected', [('and', False, False), ('and', True, None),
                                                    ('or', False, None), ('or', True, True)])
@pytest.mark.parametrize('reverse', [False, True])
def test_partial_boolean_conditions_only_resolve_a_decisive_known_value(operation, known, expected, reverse):
    from plc.instruction_definition import evaluate_expression, UnknownInstructionSemantics
    boolean = {'kind': 'bool'}
    arguments = [{'op': 'parameter', 'name': 'runtime', 'type': boolean},
                 {'op': 'constant', 'value': known, 'type': boolean}]
    expression = {'op': operation, 'type': boolean, 'args': arguments[::-1] if reverse else arguments}
    if expected is None:
        with pytest.raises(UnknownInstructionSemantics):
            evaluate_expression(expression, {})
    else:
        assert evaluate_expression(expression, {}) is expected


@pytest.mark.parametrize('opcode,initial,final', [('INC', 32767, -32768), ('INCP', 32767, -32768),
                                               ('DINC', 2147483647, -2147483648),
                                               ('DINCP', 2147483647, -2147483648)])
def test_reviewed_increment_reference_runs_a_state_sequence_with_retention(opcode, initial, final):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_effects import execute_behavior
    group = next(group for group in registry.resolve(opcode, cpu='FX3U').definition_facts
                 if group.dimension == 'effects.expression_write')
    pulse = opcode.endswith('P')
    value, previous = initial, False
    outputs = []
    for enabled in [False, True, True, False, True]:
        result = execute_behavior(group.value, {'D': 'D1710'}, memory={'D1710': value},
                                  enabled=enabled, previous_enabled=previous,
                                  trigger='rising' if pulse else 'level', disabled='retain')
        value = result['writes'].get(('D1710', 0), value)
        outputs.append(value)
        previous = enabled
    assert outputs == ([initial, final, final, final, final + 1] if pulse else
                       [initial, final, final + 1, final + 1, final + 2])


@pytest.mark.parametrize('extra,status', [
    ('BMOV D20 D99 K2', 'violated'), ('DMOV D20 D99', 'violated'),
    ('BMOV D20 D200 D30', 'unresolved'), ('BMOV D20 D200 K2', 'verified'),
])
def test_extra_instruction_checks_full_word_region_and_unknown_extent(extra, status):
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    case = next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'MOV')
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0'], 'o': ['MOV D20 D100', extra]}]}]})
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == status
    assert bool(violations) == (status == 'violated')


@pytest.mark.parametrize('gate,status', [('NC M8024', 'verified'), ('NO M8024', 'violated'),
                                       ('NC M10', 'violated')])
def test_enable_equivalence_uses_only_confirmed_bit_preconditions(gate, status):
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents
    case = next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'BMOV')
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0', gate], 'o': ['BMOV D20 D300 K7']}]}]})
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == status and rows[0]['confirmed_preconditions'] == {'M8024': False}
    assert bool(violations) == (status == 'violated')


def test_binding_preserves_aliases_and_rejects_out_of_scope_memory():
    from copy import deepcopy
    from plc.instruction_binding import bind_operation_intent
    intent = deepcopy(_effect_cases()[0]['confirmed_spec']['operation_intents'][0])
    intent['effects'][0]['target']['device'] = 'm0610'
    intent['effects'][0]['value']['args'][0]['name'] = 'd01450'
    assert bind_operation_intent(intent, target_model='FX3U')['operands'] == ['K-37', 'D1450', 'M610']
    mov = deepcopy(next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'DMOV')['confirmed_spec']['operation_intents'][0])
    for destination in ['M100', 'D7999', 'D999999']:
        mov['effects'][0]['target']['device'] = destination
        assert bind_operation_intent(mov, target_model='FX3U')['status'] == 'unresolved'


def test_native_compatibility_accepts_equivalent_whole_effects_without_swapping_comparison_results():
    from copy import deepcopy
    from application.compact_protocol import expand_compact_ladder
    from plc.instruction_binding import check_operation_intents, bind_operation_intent
    add = next(c for c in _effect_cases() if c['evaluation']['opcode'] == 'ADD')
    ladder = expand_compact_ladder({'r': [{'b': [{'i': ['NO X0'], 'o': ['ADD K3 D14 D100']}]}]})
    rows, violations = check_operation_intents(ladder, add['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == 'verified' and not violations
    cmp = deepcopy(_effect_cases()[0]['confirmed_spec']['operation_intents'][0])
    cmp['effects'][0]['target']['offset'] = 1
    cmp['effects'][0]['value']['op'] = 'eq'
    assert bind_operation_intent(cmp, target_model='FX3U')['reason'] == 'ambiguous_binding'


@pytest.mark.parametrize('opcode,positions', [('CMP', (0, 1)), ('DCMP', (0, 1)),
    ('SUB', (0, 1)), ('WSFL', (2, 3)), ('TCMP', (0, 2)), ('IVCK', (0, 3))])
@pytest.mark.parametrize('representation', ['compact', 'compact_alias', 'ladder_v1'])
def test_native_read_parameter_rebinding_corrects_confirmed_mapping_without_a_model_call(opcode, positions, representation):
    from copy import deepcopy
    from application.compact_protocol import expand_compact_ladder
    from application.generation_agent import _decode_generated_ladder
    from plc.instruction_binding import materialize_operation_references, check_operation_intents
    case = next(c for c in _effect_cases() if c['evaluation']['opcode'] == opcode)
    expected = case['evaluation']['operands']
    wrong = list(expected)
    a, b = positions
    wrong[a], wrong[b] = wrong[b], wrong[a]
    compact = {'r': [{'b': [{'i': ['NO X0'], 'o': [' '.join([opcode, *wrong])]}]}]}
    if representation == 'compact_alias':
        compact = {'rungs': [{'branches': [{'inputs': ['NO X0'], 'outputs': compact['r'][0]['b'][0]['o']}]}]}
    elif representation == 'ladder_v1':
        compact = expand_compact_ladder(compact)
    before = deepcopy(compact)
    native, receipt = materialize_operation_references(compact, case['confirmed_spec'], target_model='FX3U')
    assert compact == before and receipt['model_calls'] == 0
    assert len(receipt['receipts']) == 1
    change = receipt['receipts'][0]
    assert change['binding_mode'] == 'native_read_parameter_rebinding'
    assert change['input_call'] == {'opcode': opcode, 'operands': wrong}
    assert change['output_call'] == {'opcode': opcode, 'operands': expected}
    ladder, _ = _decode_generated_ladder(native, case['confirmed_spec'], 'FX3U')
    rows, violations = check_operation_intents(ladder, case['confirmed_spec'], target_model='FX3U')
    assert not violations and rows[0]['status'] == 'verified'
    assert ladder['rungs'][0]['branches'][0]['inputs'] == expand_compact_ladder(
        {'r': [{'b': [{'i': ['NO X0'], 'o': ['COIL Y0']}]}]})['rungs'][0]['branches'][0]['inputs']


@pytest.mark.parametrize('constraint', ['locked_wrong_call', 'candidate_intent', 'wrong_cpu',
    'duplicate_call', 'additional_operation_reference', 'changed_target', 'changed_constant', 'unknown_intent'])
def test_native_rebinding_never_overrides_confirmation_or_invents_a_different_call(constraint):
    from copy import deepcopy
    from plc.instruction_binding import materialize_operation_references
    spec = deepcopy(_effect_cases()[0]['confirmed_spec'])
    outputs = ['CMP D1450 K-37 M610']
    cpu = 'FX3U'
    if constraint == 'locked_wrong_call':
        spec['selected_approach']['explicit_user_constraints']['instruction_instances'] = [
            {'opcode': 'CMP', 'operands': ['D1450', 'K-37', 'M610']}]
    elif constraint == 'candidate_intent':
        spec['operation_intents'][0]['status'] = 'candidate'
        spec['operation_intents'][0]['provenance']['source'] = 'model_candidate'
    elif constraint == 'wrong_cpu':
        cpu = 'FX5U'
    elif constraint == 'duplicate_call':
        outputs *= 2
    elif constraint == 'additional_operation_reference':
        outputs.append('OP operation')
    elif constraint == 'changed_target':
        outputs[0] = 'CMP D1450 K-37 M620'
    elif constraint == 'changed_constant':
        outputs[0] = 'CMP D1450 K-38 M610'
    elif constraint == 'unknown_intent':
        spec.pop('operation_intents')
    compact = {'r': [{'b': [{'i': ['NC X1'], 'o': outputs}]}]}
    before = deepcopy(compact)
    native, diagnostic = materialize_operation_references(compact, spec, target_model=cpu)
    assert compact == before and native['r'][0]['b'][0]['o'][0] == outputs[0]
    assert all(r['binding_mode'] != 'native_read_parameter_rebinding' for r in diagnostic['receipts'])


@pytest.mark.parametrize('call,expected,status', [
    ('CMP HFFDB D1450 M610', 'CMP HFFDB D1450 M610', 'verified'),
    ('CMP D1450 HFFDB M610', 'CMP K-37 D1450 M610', 'verified'),
    ('CMP K65499 D1450 M610', 'CMP K65499 D1450 M610', 'violated'),
    ('CMP H1FFDB D1450 M610', 'CMP H1FFDB D1450 M610', 'violated'),
])
def test_native_constant_equivalence_uses_explicit_finite_width(call, expected, status):
    from plc.instruction_binding import materialize_operation_references, check_operation_intents
    from application.compact_protocol import expand_compact_ladder
    case = _effect_cases()[0]
    native, _ = materialize_operation_references({'r': [{'b': [{'i': ['NO X0'], 'o': [call]}]}]},
        case['confirmed_spec'], target_model='FX3U')
    assert native['r'][0]['b'][0]['o'] == [expected]
    rows, violations = check_operation_intents(expand_compact_ladder(native), case['confirmed_spec'], target_model='FX3U')
    assert rows[0]['status'] == status and bool(violations) == (status == 'violated')


def test_native_rebinding_and_conformance_require_source_checked_effect_dependencies():
    from dataclasses import replace
    from types import SimpleNamespace
    from application.compact_protocol import expand_compact_ladder
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_binding import materialize_operation_references, check_operation_intents
    from plc.instruction_definition import UnknownInstructionSemantics
    case = _effect_cases()[0]
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form('CMP', cpu='FX3U')
    candidate = replace(form, spec=replace(form.spec, definition_facts=tuple(
        replace(g, status='candidate_evidence') for g in form.spec.definition_facts)))
    registry = SimpleNamespace(resolve_form=lambda *a, **k: candidate)
    compact = {'r': [{'b': [{'i': ['NO X0'], 'o': ['CMP D1450 K-37 M610']}]}]}
    native, receipt = materialize_operation_references(compact, case['confirmed_spec'], target_model='FX3U', registry=registry)
    assert native == compact and not receipt['receipts']
    rows, violations = check_operation_intents(expand_compact_ladder(native), case['confirmed_spec'], target_model='FX3U', registry=registry)
    assert not violations and rows[0]['status'] == 'unresolved'
    with pytest.raises(UnknownInstructionSemantics, match='not_source_checked'):
        materialize_operation_references({'r': [{'b': [{'i': ['NO X0'], 'o': ['OP operation']}]}]},
            case['confirmed_spec'], target_model='FX3U', registry=registry)


@pytest.mark.parametrize('mode', ['snapshot', 'forward', 'backward'])
@pytest.mark.parametrize('count', range(1, 7))
def test_range_reference_handles_physical_overlap_and_keeps_inputs_immutable(mode, count):
    from copy import deepcopy
    from plc.instruction_effects import execute_behavior
    behavior = {'behavior': 'range_copy', 'source': 'S', 'destination': 'D',
                'element_type': {'kind': 'int', 'bits': 16, 'signed': False},
                'count': {'op': 'parameter', 'name': 'N', 'type': {'kind': 'int', 'bits': 16, 'signed': False}},
                'read_mode': mode, 'max_count': 16}
    memory = {'D' + str(10 + i): 101 + i for i in range(count + 1)}
    before = deepcopy(memory)
    actual = execute_behavior(behavior, {'S': 'D10', 'D': 'D11', 'N': count}, memory=memory)
    expected = [101] * count if mode == 'forward' else [101 + i for i in range(count)]
    assert [actual['writes'][('D11', i)] for i in range(count)] == expected
    assert memory == before


@pytest.mark.parametrize('direction', ['left', 'right'])
@pytest.mark.parametrize('count', range(1, 6))
def test_shift_reference_matches_independent_sequence_transform(direction, count):
    from plc.instruction_effects import execute_behavior
    from plc.instruction_definition import DefinitionError
    uint = {'kind': 'int', 'bits': 16, 'signed': False}
    behavior = {'behavior': 'range_shift', 'region': 'D', 'source': 'S', 'count': {'op': 'parameter', 'name': 'N', 'type': uint},
                'shift': {'op': 'parameter', 'name': 'Q', 'type': uint}, 'element_type': uint,
                'direction': direction, 'fill': 'source', 'source_must_differ': True}
    old = list(range(30, 30 + count))
    for shift in range(1, count + 1):
        fill = list(range(90, 90 + shift))
        memory = {**{'D' + str(10 + i): x for i, x in enumerate(old)},
                  **{'D' + str(100 + i): x for i, x in enumerate(fill)}}
        actual = execute_behavior(behavior, {'D': 'D10', 'S': 'D100', 'N': count, 'Q': shift}, memory=memory)
        expected = fill + old[:count - shift] if direction == 'left' else old[shift:] + fill
        assert [actual['writes'][('D10', i)] for i in range(count)] == expected
        with pytest.raises(DefinitionError, match='overlaps'):
            execute_behavior(behavior, {'D': 'D10', 'S': 'D10', 'N': count, 'Q': shift}, memory=memory)


def test_state_update_trigger_sequences_layout_and_external_contract_remain_distinct():
    from plc.instruction_effects import execute_behavior
    uint = {'kind': 'int', 'bits': 16, 'signed': False}
    update = {'behavior': 'state_update', 'outputs': [{'target': {'parameter': 'D', 'kind': 'state'},
        'expression': {'op': 'add', 'type': uint, 'args': [{'op': 'state', 'name': 'counter', 'type': uint},
                                                        {'op': 'constant', 'value': 1, 'type': uint}]}}]}
    levels = [False, True, True, False, True]
    for trigger, expected in [('level', 3), ('rising', 2), ('falling', 1)]:
        state, previous = {'counter': 0}, False
        for level in levels:
            result = execute_behavior(update, {'D': 'counter'}, state=state, enabled=level,
                                      previous_enabled=previous, trigger=trigger, disabled='no_action')
            state['counter'] = result['state_writes'].get(('counter', 0), state['counter'])
            previous = level
        assert state['counter'] == expected
    layout = execute_behavior({'behavior': 'parameter_layout', 'fields': [
        {'offset': 0, 'access': 'read_write', 'type': uint, 'bits': [0, 3, 15]}]}, {})
    assert layout['layout'][0]['bits'] == [0, 3, 15] and not layout['writes']
    external = execute_behavior({'behavior': 'external_action', 'resource_parameter': 'channel',
                                 'protocol': {'complete': 'M8029'}}, {'channel': 2})
    assert external['external_contract']['resource'] == 2 and external['hardware_effect'] == 'not_tested'
    assert external['writes'] == {}
