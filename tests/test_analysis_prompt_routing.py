"""Offline unit tests: routing and the actual assembled system prompt."""
import copy
import json
from types import SimpleNamespace

import pytest

from knowledge.analysis_router import route_analysis_request
from application.analysis_context import assemble_analysis_prompt, minimal_analysis_profile
from application.prompts import ANALYSIS_SYSTEM_PROMPT

SFTL_REQUEST = """FX3U，使用三路并行 SFTL，不要重新设计。
SFTL M10 M100 K128 K1
SFTL M11 M300 K128 K1
SFTL M12 M500 K128 K1
同一个 encoder pulse 用 LDP M8012 同步触发，每100ms移位一次。
M108、M331、M555 分别控制 Y003、Y005、Y007。
X001置位M0，X003复位M0；停止后关闭Y输出，移位链是否清零尚未决定。
"""
PROFILE = {"FX3U": {
    "addressing": "octal", "soft_limits": {"X": "X000-X367", "Y": "Y000-Y367", "M": "M0-M7679", "D": "D0-D7999"},
    "special_m": {"clock_01s": "M8012", "init_pulse_on": "M8002"},
    "special_m_reference": {"clock": {"M8012": "100ms周期时钟，ON 50ms/OFF 50ms，只读。"}},
    "special_d": {"unrelated": "D8345"},
    "positioning": {"irrelevant_motion_table": "do not inject into a shift chain"},
    "analog_output": {"irrelevant_analog_table": "not needed for preset speed terminals"},
    "hsc": {"irrelevant_counter_table": "not needed for a shift chain"},
}}
KNOWN = {"SFTL", "SFTLP", "WSFL", "LDP", "MOV", "PLSY", "DPLSY", "PLSV", "DRVI", "DDRVI", "DRVA", "ZRN", "DSZR", "DVIT", "TO", "OR", "SET"}


def resolve(opcode):
    opcode = str(opcode).upper()
    if opcode not in KNOWN:
        return None
    base = {"SFTLP": "SFTL", "DPLSY": "PLSY", "DDRVI": "DRVI"}.get(opcode, opcode)
    return SimpleNamespace(base_mnemonic=base)


def route(text, spec=None):
    return route_analysis_request(text, spec, resolve_opcode=resolve)


def build(text=SFTL_REQUEST, spec=None, evidence="FACT_EVIDENCE", profile=PROFILE):
    calls, audits = [], []
    def knowledge(query, **kwargs):
        calls.append((query, kwargs))
        return evidence
    assembled = assemble_analysis_prompt(
        text, plc_model="FX3U", confirmed_context=spec,
        model_loader=lambda: profile, knowledge_builder=knowledge, resolve_opcode=resolve,
        audit=lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    return assembled, calls, audits


def test_fixed_sftl_is_extraction_even_with_missing_stop_parameter():
    result = route(SFTL_REQUEST)
    assert result.mode == "pinned"
    assert result.opcodes == ("SFTL", "LDP")
    assert not result.include_design
    assert result.topics == ()


@pytest.mark.parametrize("text", [
    "采用 SFTL 实现，长度还没给", "使用sftl实现三路同步移位", "SFTL M10 M100 K128 K1",
    "只整理以下需求为规格，传感器地址未给", "不要重新设计，只补充参数",
])
def test_fixed_or_extract_requests_do_not_require_complete_parameters_to_route(text):
    assert route(text).mode == "pinned"


@pytest.mark.parametrize("text", [
    "FX3U 三工位依次执行，包含顺序和延时，如何组织控制架构", "设计一个普通起保停",
    "比较 SFTL 和 WSFL 方案", "使用 SFTL 或 WSFL 哪种方案更好", "推荐控制架构",
])
def test_open_requests_retain_design(text):
    assert route(text).include_design


def test_ordinary_how_to_implement_does_not_reopen_an_explicit_plan():
    assert route("使用 SFTL M10 M100 K128 K1，如何实现？").mode == "pinned"


def test_current_request_can_reopen_selected_approach():
    selected = {"selected_approach": {"name": "SFTL chain"}}
    assert route("参数改成K64", selected).mode == "pinned"
    assert route("比较一下其他方案", selected).include_design


def test_unselected_candidates_history_and_model_questions_cannot_route():
    spec = {
        "selected_approach": {"name": "三路移位", "generation_guide": "SFTL M10 M100 K128 K1"},
        "approaches": [{"name": "变频器伺服三泵", "generation_guide": "PLSY ZRN DSZR"}],
        "missing_info": [{"question": "变频器型号？"}],
        "engineering_context": {"requests": [{"text": "以前想比较伺服变频器三泵方案"}]},
        "reasoning_content": "必须比较所有架构",
    }
    result = route("周期修改为100ms", spec)
    assert result.mode == "pinned"
    assert result.topics == ()
    assert "PLSY" not in result.opcodes
    assert "以前" not in result.query_text


@pytest.mark.parametrize("text", [
    "M8012 clock pulse drives SFTL M10 M100 K128 K1", "编码器脉冲触发三路移位，不涉及伺服",
    "普通电机启停，没有变频器，不需要运动控制", "步进状态机依次经过三个阶段",
])
def test_input_pulses_and_sequence_steps_are_not_output_motion(text):
    assert "motion" not in route(text).topics
    assert "vfd" not in route(text).topics


def test_negated_old_hardware_is_not_reintroduced_from_baseline():
    selected = {"selected_approach": {"name": "变频器多段速控制"}}
    assert "vfd" not in route("不用变频器，改为普通接触器启停", selected).topics


def test_negative_old_opcode_does_not_erase_the_new_choice():
    result = route("不用 SFTL 改用 WSFL 实现")
    assert result.mode == "pinned"
    assert result.opcodes == ("WSFL",)


def test_lowercase_english_prepositions_are_not_fixed_opcodes():
    assert route("use a motor to open or close the gate").mode == "design"


def test_pinned_prompt_contains_facts_not_generic_architecture_rules():
    result, calls, audits = build()
    prompt = result.system_prompt
    assert "Analysis mode: pinned / extract" in prompt
    for forbidden in ("PLC workflow router", "Scan cycle and output ownership review", "Analysis mode: design",
                      "Relevant questions: VFD", "Relevant questions: motion", "Relevant questions: pump",
                      "irrelevant_motion_table", "irrelevant_analog_table", "D8345"):
        assert forbidden not in prompt
    assert "M8012" in prompt and "100ms周期时钟" in prompt
    assert calls == [("FX3U\nSFTL LDP M8012", {
        "plc_model": "FX3U", "task_type": "analysis", "include_design": False, "design_query": None,
    })]
    assert any(args[0] == "system_prompt" and kw["reason"] == "analysis_pinned" for args, kw in audits)


def test_open_design_has_a_separate_design_query():
    result, calls, _ = build("比较 SFTL 与 WSFL 分拣架构")
    assert "Analysis mode: design / open" in result.system_prompt
    assert calls[0][1]["include_design"] is True
    assert "分拣架构" in calls[0][1]["design_query"]
    assert "SFTL WSFL" in calls[0][0]


def test_pulse_family_does_not_inject_all_homing_families():
    result, _, _ = build("采用 PLSY K1000 K2000 Y0 驱动步进电机")
    assert "Selected pulse family" in result.system_prompt
    for item in ("Selected zero-return family", "Selected DOG-search family", "Selected positioning family"):
        assert item not in result.system_prompt
    assert "DRVI" not in result.system_prompt


def test_vfd_terminals_do_not_inject_motion_or_analog_tables():
    result, _, _ = build("变频器多段速：Y0 STF，Y1 RH，Y2 RM")
    assert "Relevant questions: VFD" in result.system_prompt
    assert "Relevant questions: motion" not in result.system_prompt
    assert "irrelevant_analog_table" not in result.system_prompt


def test_no_evidence_does_not_restore_the_large_profile():
    result, _, _ = build(evidence="")
    assert "M8012" in result.system_prompt
    assert "irrelevant_motion_table" not in result.system_prompt
    assert "D8345" not in result.system_prompt


def test_full_contract_guide_and_raw_user_request_are_preserved_without_mutation():
    spec = {
        "selected_approach": {
            "name": "三路移位", "generation_guide": SFTL_REQUEST,
            "generation_contract": {"required_opcodes": ["SFTL"], "required_devices": ["M100", "M300", "M500"],
                                    "instruction_instances": [{"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]}]},
        },
        "parameters": {"stop_clears_chain": None},
        "engineering_context": {"requests": [{"text": SFTL_REQUEST}], "proposals": [{"huge": "unselected"}],
                                "analysis_evidence": {"huge": "retrieval manifest"}},
        "approaches": [{"name": "unselected architecture"}],
    }
    original = copy.deepcopy(spec)
    result, _, _ = build("只补充停止参数", spec)
    snapshot = json.loads(result.system_prompt.split("# Confirmed project specification\n", 1)[1])
    assert snapshot["selected_approach"] == spec["selected_approach"]
    assert snapshot["engineering_context"]["requests"] == spec["engineering_context"]["requests"]
    assert "approaches" not in snapshot
    assert "proposals" not in snapshot["engineering_context"]
    assert spec == original


def test_fx5u_profile_does_not_inherit_fx3u_special_devices():
    registry = {"FX5U": {"addressing": "decimal", "special_m": {"clock": "SM8012"},
                        "soft_limits": {"X": "decimal X range"}}}
    result = minimal_analysis_profile("FX5U", registry, route("使用 SFTL M10 M100 K128 K1 和 SM8012"))
    assert result["addressing"] == "decimal"
    assert "SM8012" in result["special_devices"]
    assert "M8012" not in result["special_devices"]


def test_missing_model_profile_does_not_borrow_fx3u_data():
    result = minimal_analysis_profile("UNKNOWN", PROFILE, route(SFTL_REQUEST))
    assert result["model"] == "UNKNOWN"
    assert "profile_status" in result
    assert "special_devices" not in result


def test_core_shape_and_required_parameter_instructions_remain():
    assert len(ANALYSIS_SYSTEM_PROMPT) < 2000
    example = ANALYSIS_SYSTEM_PROMPT.split("返回纯JSON（不要```json包裹），格式：\n", 1)[1].split("\n# suggested_io", 1)[0]
    assert isinstance(json.loads(example), dict)
    assert "必要输入仍为 required" in ANALYSIS_SYSTEM_PROMPT
    assert "default 不是已确认答案" in ANALYSIS_SYSTEM_PROMPT
    assert "完整操作数" in ANALYSIS_SYSTEM_PROMPT
    flow = ANALYSIS_SYSTEM_PROMPT.split("- 示例：", 1)[1].split("\n", 1)[0]
    assert all("type" in step and "label" in step for step in json.loads(flow))


def test_partly_fixed_request_can_delegate_the_remaining_design():
    assert route("使用 SFTL，其他控制结构由你设计").include_design


def test_model_capability_catalog_is_not_hardware_intent():
    spec = {"selected_approach": {"name": "SFTL chain"},
            "hardware_profile": {"capabilities": "可选变频器、伺服定位、三泵轮换"},
            "hardware_context": {"available_options": "PLSY ZRN DSZR"}}
    assert route("只修改K128", spec).topics == ()


def test_unknown_instruction_is_not_a_new_rejection_or_silent_deletion():
    result, calls, _ = build("只提取规格：MYINSTR D0 D2")
    assert result.route.mode == "pinned"
    assert "MYINSTR D0 D2" in calls[0][0]
