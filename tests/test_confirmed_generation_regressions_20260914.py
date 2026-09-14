import json

from api import _normalize_analysis_result
from application.generation_agent import (
    _FirstJSONObjectProvider,
    _GENERATION_REQUEST,
)
from confirmed_spec import build_review_draft
from model_provider import ModelRequest, TextDelta, UserMessage


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
    assert "parallel_block" in _GENERATION_REQUEST
    assert "输入条件的 OR" in _GENERATION_REQUEST
    assert "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches" in _GENERATION_REQUEST
    assert "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON" in _GENERATION_REQUEST


def test_agent_a_cannot_drop_verbatim_classification_or_promote_guessed_contract():
    requirement = "分类规则必须严格保留：1~3 为 A 类，4~6 为 B 类，7~9 为 C 类。"
    normalized = _normalize_analysis_result(
        _bad_analysis(), plc_model="FX3U", user_text=requirement
    )
    draft = build_review_draft(normalized)

    assert requirement in draft["summary"]
    assert "【当前用户明确要求（逐字保留）】" in draft["summary"]

    contract = draft["selected_approach"]["generation_contract"]
    assert contract["required_opcodes"] == []
    assert contract["required_devices"] == []
    assert "hardware_counter" not in contract["required_structures"]
    assert "data_register_counter" not in contract["required_structures"]
    assert contract["required_structures"] == ["direct_logic"]


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
