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
from model_runtime.provider import ModelRequest, TextDelta, UserMessage
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
            yield TextDelta('{"third":"must never be consumed"}')
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


def test_agent_b_stream_stops_after_first_complete_json_object():
    base = _DuplicateJsonProvider()
    provider = _FirstJSONObjectProvider(base)
    request = ModelRequest.from_messages([UserMessage("json")], stream=True)

    events = list(provider.stream(request))
    content = "".join(event.text for event in events if isinstance(event, TextDelta))

    assert json.loads(content) == {"first": {"text": "a } brace"}, "items": [1, 2]}
    assert "second" not in content
    assert base.closed is True
    assert base.reached_tail is False


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

    assert json.dumps(projected, ensure_ascii=False, separators=(",", ":")) in prompt
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

    assert draft["engineering_context"]["requests"][0]["text"] == requirement
    assert "【当前用户明确要求（逐字保留）】" not in draft["summary"]

    contract = draft["selected_approach"]["generation_contract"]
    assert contract["required_opcodes"] == []
    assert contract["required_devices"] == []
    assert "hardware_counter" not in contract["required_structures"]
    assert "data_register_counter" not in contract["required_structures"]
    assert contract["required_structures"] == ["direct_logic"]


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
    assert selected["generation_contract"]["required_structures"] == ["direct_logic"]
    assert selected["name"] == draft["selected_approach"]["name"]
    assert selected["description"] == draft["selected_approach"]["description"]
    assert selected["generation_guide"] == draft["selected_approach"]["generation_guide"]
    assert selected["implementation_preferences"]["enforce"] is False
    assert "reasoning_content" not in selected
    assert projected["engineering_context"]["proposals"][0]["source"] == "model_proposal"


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
