import json

from application.model_api import _normalize_analysis_result
from application.generation_agent import (
    _FirstJSONObjectProvider,
    _GENERATION_REQUEST,
    _build_agent_b_prompt,
    _compact_response_schema,
    _expand_compact_ladder,
    _strict_generation_projection,
)
from plc.specification.confirmed import build_review_draft
from model_runtime.provider import ModelRequest, TextDelta, Usage, UserMessage
from plc.validation import validate_ladder_candidate_structure


class _DuplicateJsonProvider:
    def __init__(self):
        self.profile = {"capabilities": {}}
        self.closed = False
        self.reached_tail = False

    def stream(self, request):
        try:
            yield TextDelta(' {"first":{"text":"a } brace"},"items":[1,')
            yield TextDelta('2]} {"second":"full rewrite"}')
            self.reached_tail = True
            yield TextDelta('{"third":"must never become the candidate"}')
            yield Usage(10, 20, 30, 12)
        finally:
            self.closed = True


def _bad_analysis():
    return {
        "summary": "模型只写了一个很粗的分类摘要",
        "control_type": ["顺序"],
        "approaches": [
            {
                "approach_id": "model_guess",
                "name": "模型猜测方案",
                "description": "模型自行补出的低层实现",
                "pros": "",
                "cons": "",
                "generation_guide": "LDP 触发后用 DMOV K0，并使用 hardware counter。",
                "generation_contract": {
                    "required_opcodes": ["LDP", "DMOV"],
                    "forbidden_opcodes": [],
                    "required_devices": ["D99"],
                    "forbidden_devices": [],
                    "required_structures": ["hardware_counter", "direct_logic"],
                    "forbidden_structures": [],
                    "any_of_opcode_groups": [],
                    "any_of_structure_groups": [],
                },
            }
        ],
        "missing_info": [],
        "suggested_io": {},
        "hardware_config": {},
        "assumptions": [],
        "format_diagnostics": [],
        "execution_semantics": [],
        "flowchart_steps": [{"type": "step", "label": "初始状态"}],
    }


def test_implementation_semantics_are_structure_only_and_core_projects_user_constraints():
    from plc.specification.approach import normalize_approach

    selected = normalize_approach({
        "approach_id": "semantic_contract",
        "name": "structured plan",
        "implementation_semantics": [
            {"kind": "structure", "status": "required", "value": "direct_logic"},
            {"kind": "structure", "status": "forbidden", "value": "set_reset_latch"},
            # Low-level model output is outside Agent-A's semantic vocabulary.
            {"kind": "opcode", "status": "required", "value": "MOV"},
            {"kind": "device", "status": "required", "value": "D99"},
        ],
        "explicit_user_constraints": {
            "required_opcodes": ["DMOV"],
            "required_devices": ["D10"],
            "instruction_instances": [
                {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
            ],
        },
    })
    assert selected["implementation_semantics"] == [
        {"kind": "structure", "status": "required", "value": "direct_logic"},
        {"kind": "structure", "status": "forbidden", "value": "set_reset_latch"},
    ]
    contract = selected["generation_contract"]
    assert contract["required_structures"] == ["direct_logic"]
    assert contract["forbidden_structures"] == ["set_reset_latch"]
    assert contract["required_opcodes"] == ["DMOV"]
    assert contract["required_devices"] == ["D10"]
    assert contract["instruction_instances"] == [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}
    ]


def test_core_extracts_low_level_constraints_without_agent_a_repeating_them():
    raw = {
        "summary": "structured plan",
        "approaches": [{
            "approach_id": "a1",
            "name": "one plan",
            "generation_guide": "",
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "direct_logic"},
                {"kind": "opcode", "status": "required", "value": "MOV"},
                {"kind": "device", "status": "required", "value": "D99"},
                {"kind": "instruction_instance", "status": "required",
                 "opcode": "MOV", "operands": ["K1", "D99"]},
            ],
        }],
        "missing_info": [], "suggested_io": {}, "hardware_config": {}, "assumptions": [],
    }
    normalized = _normalize_analysis_result(
        raw,
        plc_model="FX3U",
        user_text=(
            "明确使用 SFTL M10 M100 K8 K1。"
            "指定 D10 作为状态寄存器。"
        ),
    )
    selected = normalized["approaches"][0]
    assert selected["implementation_semantics"] == [
        {"kind": "structure", "status": "required", "value": "direct_logic"},
    ]
    contract = selected["generation_contract"]
    assert contract["required_opcodes"] == ["SFTL"]
    assert contract["required_devices"] == ["D10"]
    assert contract["instruction_instances"] == [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K8", "K1"]}
    ]
    assert "MOV" not in contract["required_opcodes"]
    assert "D99" not in contract["required_devices"]


def test_comparison_text_does_not_become_a_fixed_instruction_instance():
    from plc.specification.explicit_constraints import extract_explicit_user_constraints

    result = extract_explicit_user_constraints(
        "比较 SFTL M10 M100 K8 K1 和 WSFL 方案，暂未指定具体指令。",
        "FX3U",
    )
    assert result["constraints"]["instruction_instances"] == []
    assert result["constraints"]["required_opcodes"] == []


def test_explicit_constraint_scope_promotes_only_direct_global_objects():
    from plc.specification.explicit_constraints import extract_explicit_user_constraints

    cases = [
        ("禁止使用 SET/RST。", [], ["SET", "RST"], [], []),
        ("不要使用 M100、M101。", [], [], [], ["M100", "M101"]),
        ("不使用 SET/RST 实现 M0 保持。", [], [], [], []),
        ("不要为 Y1 另外增加保持状态。", [], [], [], []),
        ("禁止使用 RST 指令，计数器通过其他方式复位。", [], ["RST"], [], []),
        ("不要新增 X10/X11 作为外部输入。", [], [], [], ["X10", "X11"]),
        ("指定 D10 作为状态寄存器。", [], [], ["D10"], []),
    ]
    for text, required_opcodes, forbidden_opcodes, required_devices, forbidden_devices in cases:
        constraints = extract_explicit_user_constraints(text, "FX3U")["constraints"]
        assert constraints["required_opcodes"] == required_opcodes, text
        assert constraints["forbidden_opcodes"] == forbidden_opcodes, text
        assert constraints["required_devices"] == required_devices, text
        assert constraints["forbidden_devices"] == forbidden_devices, text


def test_scoped_negative_does_not_poison_generation_contract():
    raw = {
        "summary": "运行保持与批次控制",
        "approaches": [{
            "approach_id": "direct",
            "name": "直接结构",
            "generation_guide": "",
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "self_hold"},
                {"kind": "structure", "status": "forbidden", "value": "set_reset_latch"},
            ],
        }],
        "missing_info": [], "suggested_io": {}, "hardware_config": {}, "assumptions": [],
    }
    normalized = _normalize_analysis_result(
        raw,
        plc_model="FX3U",
        user_text=(
            "M0 为系统运行保持状态，不使用 SET/RST 实现 M0 保持。"
            "T0 到时后 Y1=ON；不要为 Y1 另外增加保持状态。"
        ),
    )
    selected = normalized["approaches"][0]
    explicit = selected["explicit_user_constraints"]
    assert explicit["forbidden_opcodes"] == []
    assert explicit["forbidden_devices"] == []
    contract = selected["generation_contract"]
    assert contract["required_structures"] == ["self_hold"]
    assert contract["forbidden_structures"] == ["set_reset_latch"]
    assert contract["forbidden_opcodes"] == []
    assert contract["forbidden_devices"] == []


def test_analysis_execution_claim_compiles_one_grounded_event_without_duplicate_level():
    text = (
        "X2 一到料就让 M1 打一拍。"
        "物料没离开 X2 之前不能再打一拍 M1。"
    )
    normalized = _normalize_analysis_result(
        {
            "summary": "物料检测",
            "approaches": [{
                "approach_id": "direct",
                "name": "直接逻辑",
                "implementation_semantics": [
                    {"kind": "structure", "status": "required", "value": "edge_trigger"},
                ],
            }],
            "execution_intent_claims": [{
                "trigger": {
                    "kind": "transition",
                    "source_devices": ["X2"],
                    "from": "0",
                    "to": "1",
                },
                "effect": {"kind": "one_shot", "devices": ["M1"]},
                "rearm": "required",
                "evidence": [
                    "X2 一到料就让 M1 打一拍",
                    "物料没离开 X2 之前不能再打一拍 M1",
                ],
            }],
            "missing_info": [],
            "suggested_io": {},
            "hardware_config": {},
            "assumptions": [],
        },
        plc_model="FX3U",
        user_text=text,
    )

    assert normalized["execution_semantics"] == [{
        "semantic": "RISING_EDGE",
        "devices": ["X2"],
        "evidence": "X2 一到料就让 M1 打一拍 | 物料没离开 X2 之前不能再打一拍 M1",
        "source": "agent_a_claim",
        "strict": True,
        "effect_devices": ["M1"],
        "effect_kind": "one_shot",
        "rearm": "required",
        "intent_id": normalized["execution_intent_receipt"]["accepted"][0]["claim_id"],
    }]


def test_pinned_execution_claim_replaces_only_touched_device_semantics():
    previous = {
        "execution_semantics": [
            {"semantic": "RISING_EDGE", "devices": ["X2"], "evidence": "old X2"},
            {"semantic": "FALLING_EDGE", "devices": ["X7"], "evidence": "keep X7"},
        ]
    }
    text = "X2 现在只看高电平，Y0 在它为高时保持输出。"
    raw = {
        "summary": "修改 X2",
        "approaches": [],
        "execution_intent_claims": [{
            "trigger": {"kind": "level", "source_devices": ["X2"], "value": "1"},
            "effect": {"kind": "while_true", "devices": ["Y0"]},
            "rearm": "not_required",
            "evidence": [text],
        }],
        "missing_info": [], "suggested_io": {}, "hardware_config": {}, "assumptions": [],
    }
    normalized = _normalize_analysis_result(raw, "FX3U", text, previous)
    assert {
        (item["semantic"], tuple(item["devices"]))
        for item in normalized["execution_semantics"]
    } == {
        ("LEVEL", ("X2",)),
        ("FALLING_EDGE", ("X7",)),
    }



def test_agent_b_frames_first_json_but_consumes_trailing_usage():
    base = _DuplicateJsonProvider()
    provider = _FirstJSONObjectProvider(base)
    request = ModelRequest.from_messages([UserMessage("json")], stream=True)

    events = list(provider.stream(request))
    content = "".join(event.text for event in events if isinstance(event, TextDelta))

    assert json.loads(content) == {"first": {"text": "a } brace"}, "items": [1, 2]}
    assert "second" not in content
    assert base.closed is True
    assert base.reached_tail is True
    assert "third" not in content
    assert [event.reasoning_tokens for event in events if isinstance(event, Usage)] == [12]


def test_agent_b_prompt_makes_input_or_and_single_json_rules_explicit():
    assert "输入条件的 OR" in _GENERATION_REQUEST
    assert "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches" in _GENERATION_REQUEST
    assert "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON" in _GENERATION_REQUEST


def test_compact_agent_b_expands_or_compare_timer_and_app_instruction():
    projected = {
        "io_table": [
            {"address": "X1", "label": "入口检测"},
            {"address": "Y3", "label": "转向臂"},
        ]
    }
    compact = {
        "r": [
            {
                "b": [
                    {
                        "i": [
                            "NO X1",
                            {"or": [["NO M20"], ["NO M21"], ["NO M22"]]},
                        ],
                        "o": ["COIL Y3"],
                    }
                ]
            },
            {"b": [{"i": [">= D0 K1", "<= D0 K3"], "o": ["MOV K1 D10"]}]},
            {"b": [{"i": ["NO X2"], "o": ["TIMER T0 K10"]}]},
        ]
    }

    ladder = _expand_compact_ladder(compact, projected)
    first = ladder["rungs"][0]["branches"][0]

    assert first["inputs"][1] == {
        "type": "parallel_block",
        "branches": [
            [{"type": "NO", "address": "M20"}],
            [{"type": "NO", "address": "M21"}],
            [{"type": "NO", "address": "M22"}],
        ],
    }
    assert ladder["rungs"][1]["branches"][0]["inputs"] == [
        {"type": "COMPARE", "expression": ">= D0 K1"},
        {"type": "COMPARE", "expression": "<= D0 K3"},
    ]
    assert ladder["rungs"][1]["branches"][0]["outputs"] == [
        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"]}
    ]
    assert ladder["rungs"][2]["branches"][0]["outputs"] == [
        {"type": "TIMER", "address": "T0", "value": "K10"}
    ]
    assert ladder["device_comments"] == {"X1": "入口检测", "Y3": "转向臂"}
    assert [rung["rung_id"] for rung in ladder["rungs"]] == [1, 2, 3]
    assert first["branch_id"] == 1 and first["y_offset_level"] == 0
    validate_ladder_candidate_structure(
        ladder, plc_model="FX3U", require_catalogued_instructions=True
    )


def test_compact_transport_schema_does_not_embed_full_opcode_catalog():
    schema_text = json.dumps(_compact_response_schema(), ensure_ascii=False, separators=(",", ":"))
    assert len(schema_text) < 2000
    assert "opcode" not in schema_text
    assert "device_comments" not in schema_text
    assert "branch_id" not in schema_text
    assert "y_offset_level" not in schema_text


def test_compact_agent_b_output_is_materially_smaller_than_expanded_ladder():
    compact = {
        "r": [
            {"b": [{"i": ["NO X1", "NC M1"], "o": [f"COIL M{i}"]}]}
            for i in range(1, 31)
        ]
    }
    full = _expand_compact_ladder(compact, {})
    compact_size = len(json.dumps(compact, separators=(",", ":")))
    full_size = len(json.dumps(full, separators=(",", ":")))
    assert compact_size < full_size * 0.55


def test_agent_b_prompt_is_confirmed_spec_scoped_not_generic_model_dump(monkeypatch):
    import application.generation_agent as agent

    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *args, **kwargs: "")
    projected = {
        "summary": "1~3 为 A 类，4~6 为 B 类，7~9 为 C 类",
        "selected_approach": {
            "approach_id": "direct",
            "generation_contract": {"required_structures": ["direct_logic"]},
        },
        "io_table": [{"address": "X1", "label": "检测"}, {"address": "Y0", "label": "输送带"}],
    }
    prompt = _build_agent_b_prompt(projected, "FX3U")

    actual, _ = json.JSONDecoder().raw_decode(prompt.split("# Confirmed project specification\n", 1)[1])
    assert actual == {**projected, "schema_version": 4}
    assert "FX3U-4DA" not in prompt
    assert "FX3U-2HSY-ADP" not in prompt
    assert "Machine-readable output schema" not in prompt
    assert len(prompt) < 5000


def test_agent_a_cannot_drop_verbatim_classification_or_promote_guessed_contract():
    requirement = "分类规则必须严格保留：1~3 为 A 类，4~6 为 B 类，7~9 为 C 类。"
    normalized = _normalize_analysis_result(
        _bad_analysis(), plc_model="FX3U", user_text=requirement
    )
    draft = build_review_draft(normalized)

    assert draft["intent_context"]["requests"][0]["text"] == requirement
    assert "【当前用户明确要求（逐字保留）】" not in draft["summary"]

    contract = draft["selected_approach"]["generation_contract"]
    assert contract["required_opcodes"] == []
    assert contract["required_devices"] == []
    # Structures are selected-approach semantics and all follow the same
    # provenance policy. Counter structures no longer have a keyword-only
    # exception that silently strips them from Agent A's selected plan.
    assert "data_register_counter" not in contract["required_structures"]
    assert contract["required_structures"] == ["hardware_counter", "direct_logic"]


def test_agent_b_receives_structured_approach_contract_but_not_agent_a_prose():
    """Keep the historical test ID; selected plans are not private Agent-A reasoning."""
    normalized = _normalize_analysis_result(
        _bad_analysis(),
        plc_model="FX3U",
        user_text="分类规则必须严格保留：1~3 为 A 类，4~6 为 B 类，7~9 为 C 类。",
    )
    draft = build_review_draft(normalized)
    projected = _strict_generation_projection(draft)
    selected = projected["selected_approach"]

    assert selected["approach_id"] == "model_guess"
    assert selected["generation_contract"]["required_structures"] == [
        "hardware_counter",
        "direct_logic",
    ]
    assert selected["name"] == draft["selected_approach"]["name"]
    assert selected["description"] == draft["selected_approach"]["description"]
    assert selected["generation_guide"] == draft["selected_approach"]["generation_guide"]
    assert selected["implementation_preferences"]["enforce"] is False
    assert "reasoning_content" not in selected
    assert "engineering_context" not in projected
    assert "decision_receipt" not in projected


def test_agent_a_low_level_opcode_survives_only_when_user_explicitly_names_it():
    normalized = _normalize_analysis_result(
        _bad_analysis(),
        plc_model="FX3U",
        user_text="明确要求使用 DMOV；不要把其他模型建议指令当成硬要求。",
    )
    draft = build_review_draft(normalized)
    contract = draft["selected_approach"]["generation_contract"]

    assert contract["required_opcodes"] == ["DMOV"]
    assert "LDP" not in contract["required_opcodes"]


def test_counter_structure_is_kept_when_request_really_is_counter_control():
    normalized = _normalize_analysis_result(
        _bad_analysis(),
        plc_model="FX3U",
        user_text="需要硬件计数器计数，每累计 9 次执行一次分类周期。",
    )
    draft = build_review_draft(normalized)
    contract = draft["selected_approach"]["generation_contract"]

    assert "hardware_counter" in contract["required_structures"]


def _structure_only_analysis():
    return {
        "summary": "固定方案",
        "approaches": [{
            "approach_id": "fixed_shift",
            "name": "固定结构",
            "description": "按确认结构实现",
            "pros": "",
            "cons": "",
            "generation_guide": "",
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "direct_logic"},
            ],
        }],
        "missing_info": [],
        "suggested_io": {},
        "hardware_config": {},
        "assumptions": [],
    }


def _legacy_instruction_instance_analysis(instances):
    return {
        "summary": "legacy fixed instruction",
        "approaches": [{
            "approach_id": "legacy_fixed_shift",
            "name": "legacy fixed shift",
            "description": "legacy compatibility",
            "pros": "",
            "cons": "",
            "generation_guide": "",
            "generation_contract": {
                "required_opcodes": ["SFTL"],
                "instruction_instances": instances,
            },
        }],
        "missing_info": [],
        "suggested_io": {},
        "hardware_config": {},
        "assumptions": [],
    }


def test_exact_instruction_instance_survives_agent_a_confirmation_to_agent_b():
    from application.confirmed_generation_context import build_confirmed_generation_context
    from knowledge.structured_facts import structured_fact_targets
    from plc.specification.approach import format_contract_summary
    from plc.specification.provenance import confirm_context

    instance = {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}
    normalized = _normalize_analysis_result(
        _structure_only_analysis(),
        plc_model="FX3U",
        user_text="明确使用 SFTL M10 M100 K128 K1 实现移位。",
    )
    draft = build_review_draft(normalized)
    assert draft["selected_approach"]["generation_contract"]["instruction_instances"] == [instance]
    assert "SFTL M10 M100 K128 K1" in format_contract_summary(draft["selected_approach"])

    confirmed = confirm_context(draft)
    projected = _strict_generation_projection(confirmed)
    assert projected["selected_approach"]["generation_contract"]["instruction_instances"] == [instance]

    context = build_confirmed_generation_context(
        confirmed, "FX3U", knowledge_builder=lambda *args, **kwargs: "",
    )
    assert context.confirmed_spec["selected_approach"]["generation_contract"]["instruction_instances"] == [instance]

    targets = structured_fact_targets("", context.confirmed_spec)
    exact = next(row for row in targets["instructions"] if row["opcode"] == "SFTL")
    assert exact["operands"] == instance["operands"]
    assert exact["instance_source"] == "generation_contract"

    prompt = _build_agent_b_prompt(projected, "FX3U", context=context)
    payload, _ = json.JSONDecoder().raw_decode(
        prompt.split("# Confirmed project specification\n", 1)[1]
    )
    assert payload["selected_approach"]["generation_contract"]["instruction_instances"] == [instance]
    assert "opcode 与 operands 必须逐项原样使用" in prompt


def test_pinned_reanalysis_preserves_instances_until_explicitly_cleared():
    from plc.specification.provenance import confirm_context

    instance = {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}
    first = _normalize_analysis_result(
        _structure_only_analysis(),
        plc_model="FX3U",
        user_text="明确使用 SFTL M10 M100 K128 K1。",
    )
    previous = confirm_context(build_review_draft(first))

    omitted = _structure_only_analysis()
    normalized = _normalize_analysis_result(
        omitted, plc_model="FX3U", user_text="只修改停止保持参数。", confirmed_spec=previous,
    )
    draft = build_review_draft(normalized, previous)
    assert draft["selected_approach"]["generation_contract"]["instruction_instances"] == [instance]

    cleared = _structure_only_analysis()
    normalized_clear = _normalize_analysis_result(
        cleared, plc_model="FX3U", user_text="清除原固定指令实例，重新开放具体调用。", confirmed_spec=previous,
    )
    cleared_draft = build_review_draft(normalized_clear, previous)
    assert cleared_draft["selected_approach"]["generation_contract"]["instruction_instances"] == []


def test_model_proposed_instruction_instance_stays_exact_as_selected_preference():
    from application.confirmed_generation_context import build_confirmed_generation_context
    from knowledge.structured_facts import structured_fact_targets
    from plc.specification.approach import format_contract_summary
    from plc.specification.provenance import confirm_context

    instance = {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}
    normalized = _normalize_analysis_result(
        _legacy_instruction_instance_analysis([instance]),
        plc_model="FX3U",
        user_text="做一个三路移位方案，具体调用由方案确定。",
    )
    draft = build_review_draft(normalized)

    # Agent-A implementation detail is not silently promoted to a user-authored
    # hard constraint, but the selected implementation still retains it exactly.
    assert draft["selected_approach"]["generation_contract"]["instruction_instances"] == []
    assert draft["selected_approach"]["implementation_preferences"]["instruction_instances"] == [instance]
    summary = format_contract_summary(draft["selected_approach"])
    assert "方案指令实例" in summary
    assert "SFTL M10 M100 K128 K1" in summary

    confirmed = confirm_context(draft)
    projected = _strict_generation_projection(confirmed)
    assert projected["selected_approach"]["implementation_preferences"]["instruction_instances"] == [instance]

    context = build_confirmed_generation_context(
        confirmed, "FX3U", knowledge_builder=lambda *args, **kwargs: "",
    )
    targets = structured_fact_targets("", context.confirmed_spec)
    exact = next(row for row in targets["instructions"] if row["opcode"] == "SFTL")
    assert exact["operands"] == instance["operands"]
    assert exact["instance_source"] == "implementation_preferences"

    prompt = _build_agent_b_prompt(projected, "FX3U", context=context)
    payload, _ = json.JSONDecoder().raw_decode(
        prompt.split("# Confirmed project specification\n", 1)[1]
    )
    assert payload["selected_approach"]["implementation_preferences"]["instruction_instances"] == [instance]


def test_compact_prompt_does_not_duplicate_instruction_contract_lane():
    from application.compact_protocol import compact_capability_prompt

    spec = {
        "selected_approach": {
            "generation_contract": {
                "required_opcodes": ["MOV"],
                "instruction_instances": [{"opcode": "MOV", "operands": ["K1", "D0"]}],
            }
        }
    }
    assert compact_capability_prompt("FX3U", spec) == ""
