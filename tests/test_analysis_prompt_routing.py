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


def route(text, spec=None, analysis_mode="direct"):
    return route_analysis_request(text, spec, analysis_mode=analysis_mode, resolve_opcode=resolve)


def build(text=SFTL_REQUEST, spec=None, evidence="FACT_EVIDENCE", profile=PROFILE, analysis_mode="direct"):
    calls, audits = [], []
    def knowledge(query, **kwargs):
        calls.append((query, kwargs))
        return evidence
    assembled = assemble_analysis_prompt(
        text, plc_model="FX3U", confirmed_context=spec, analysis_mode=analysis_mode,
        model_loader=lambda: profile, knowledge_builder=knowledge, resolve_opcode=resolve,
        audit=lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    return assembled, calls, audits


def test_direct_sftl_does_not_explore_despite_missing_stop_parameter():
    result = route(SFTL_REQUEST)
    assert result.mode == "direct"
    assert result.opcodes == ("SFTL", "LDP")
    assert not result.include_design
    assert result.topics == ()


@pytest.mark.parametrize("text", [
    "采用 SFTL 实现，长度还没给", "使用sftl实现三路同步移位", "SFTL M10 M100 K128 K1",
    "只整理以下需求为规格，传感器地址未给", "不要重新设计，只补充参数",
])
def test_fixed_or_extract_requests_do_not_require_complete_parameters_to_route(text):
    assert route(text).mode == "direct"


@pytest.mark.parametrize("text", [
    "FX3U 三工位依次执行，包含顺序和延时，如何组织控制架构", "设计一个普通起保停",
    "比较 SFTL 和 WSFL 方案", "使用 SFTL 或 WSFL 哪种方案更好", "推荐控制架构",
])
def test_keywords_never_open_design_without_explicit_mode(text):
    assert not route(text).include_design
    assert not route(text, analysis_mode="direct").include_design
    assert route(text, analysis_mode="design").include_design


def test_ordinary_how_to_implement_does_not_reopen_an_explicit_plan():
    assert route("使用 SFTL M10 M100 K128 K1，如何实现？").mode == "direct"


def test_only_explicit_mode_can_reopen_selected_approach():
    selected = {"selected_approach": {"name": "SFTL chain"}}
    assert route("参数改成K64", selected).mode == "pinned"
    assert not route("比较一下其他方案", selected).include_design
    assert route("参数改成K64", selected, analysis_mode="design").include_design


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
    assert result.mode == "direct"
    assert result.opcodes == ("WSFL",)


def test_lowercase_english_prepositions_are_not_fixed_opcodes():
    result = route("use a motor to open or close the gate")
    assert result.mode == "direct"
    assert result.opcodes == ()


def test_direct_prompt_contains_facts_not_generic_architecture_rules():
    result, calls, audits = build()
    prompt = result.system_prompt
    assert "Analysis mode: direct" in prompt
    assert "Direct substate: pinned" not in prompt
    for forbidden in ("PLC workflow router", "Scan cycle and output ownership review", "Analysis mode: design",
                      "Relevant questions: VFD", "Relevant questions: motion", "Relevant questions: pump",
                      "irrelevant_motion_table", "irrelevant_analog_table", "D8345"):
        assert forbidden not in prompt
    assert "M8012" in prompt and "100ms周期时钟" in prompt
    assert calls == [("FX3U\nSFTL LDP M8012", {
        "plc_model": "FX3U", "task_type": "analysis", "include_design": False, "design_query": None,
    })]
    assert any(args[0] == "system_prompt" and kw["reason"] == "analysis_direct" for args, kw in audits)


def test_open_design_has_a_separate_design_query():
    result, calls, _ = build("比较 SFTL 与 WSFL 分拣架构", analysis_mode="design")
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
    assert snapshot["intent_context"]["requests"] == spec["engineering_context"]["requests"]
    assert "approaches" not in snapshot
    assert "engineering_context" not in snapshot
    assert "proposals" not in snapshot["intent_context"]
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
    assert set(json.loads(example)) == {
        "summary", "approaches", "missing_info", "suggested_io", "hardware_config", "assumptions",
    }


def test_partly_fixed_request_can_delegate_the_remaining_design():
    assert route("使用 SFTL，其他控制结构由你设计", analysis_mode="design").include_design
    assert not route("使用 SFTL，其他控制结构由你设计").include_design


def test_model_capability_catalog_is_not_hardware_intent():
    spec = {"selected_approach": {"name": "SFTL chain"},
            "hardware_profile": {"capabilities": "可选变频器、伺服定位、三泵轮换"},
            "hardware_context": {"available_options": "PLSY ZRN DSZR"}}
    assert route("只修改K128", spec).topics == ()


def test_unknown_instruction_is_not_a_new_rejection_or_silent_deletion():
    result, calls, _ = build("只提取规格：MYINSTR D0 D2")
    assert result.route.mode == "direct"
    assert "MYINSTR D0 D2" in calls[0][0]


@pytest.mark.parametrize("text", [
    "三气缸顺序控制", "三泵启停", "十步顺控", "SET/RST 状态机", "SFTL 移位",
    "复杂伺服定位，采用 DRVI D0 K1000 Y0 Y1，目标还没给", "优化模拟量通信架构",
])
@pytest.mark.parametrize("selected", [None, {"selected_approach": {"name": "现有实现"}}])
def test_modes_change_exploration_not_the_fact_route(text, selected):
    direct, direct_calls, _ = build(text, selected, analysis_mode="direct")
    design, design_calls, _ = build(text, selected, analysis_mode="design")
    assert direct_calls[0][1]["include_design"] is False
    assert direct_calls[0][1]["design_query"] is None
    assert design_calls[0][1]["include_design"] is True
    assert direct.route.topics == design.route.topics
    assert direct.route.opcodes == design.route.opcodes
    assert direct.route.devices == design.route.devices
    assert "Analysis mode: direct" in direct.system_prompt
    assert ("Direct substate: pinned / extract" in direct.system_prompt) == bool(selected)
    assert "Analysis mode: design" not in direct.system_prompt
    assert "Direct substate: pinned" not in design.system_prompt
    assert "Analysis mode: direct" not in design.system_prompt


def test_direct_is_compact_without_losing_structured_engineering_facts():
    from application.prompts import ANALYSIS_DIRECT_PROMPT, ANALYSIS_PINNED_PROMPT
    assert "exactly one approach" in ANALYSIS_DIRECT_PROMPT
    assert "pros 和 cons 用空字符串" in ANALYSIS_DIRECT_PROMPT
    assert "generation_guide 只补结构化字段表达不了" in ANALYSIS_DIRECT_PROMPT
    assert "通常应为空字符串" in ANALYSIS_DIRECT_PROMPT
    assert "required_structures" in ANALYSIS_DIRECT_PROMPT
    assert "扫描周期" in ANALYSIS_DIRECT_PROMPT
    assert "不编造已确认答案" in ANALYSIS_DIRECT_PROMPT
    assert "原来为空就不要" in ANALYSIS_PINNED_PROMPT


def test_direct_core_does_not_duplicate_unknown_io_or_generic_plc_explanations():
    assert "未知地址不填 suggested_io" in ANALYSIS_SYSTEM_PROMPT
    assert "不同时预分配一个“建议地址”" in ANALYSIS_SYSTEM_PROMPT
    assert "原始用户请求由应用另行保留" in ANALYSIS_SYSTEM_PROMPT
    assert "没有这种差异就用空字符串" in ANALYSIS_SYSTEM_PROMPT
    assert "PLC 常识、常规扫描行为" in ANALYSIS_SYSTEM_PROMPT
    example = ANALYSIS_SYSTEM_PROMPT.split("返回纯JSON（不要```json包裹），格式：\n", 1)[1].split("\n# suggested_io", 1)[0]
    assert not {"control_type", "flowchart_steps", "format_diagnostics", "execution_semantics"} & set(json.loads(example))


@pytest.mark.parametrize("analysis_mode", ["direct", "design"])
def test_old_display_metadata_is_not_reintroduced_by_confirmed_baseline(analysis_mode):
    spec = {
        "selected_approach": {"name": "保留的实现", "generation_guide": "保留专有语义"},
        "control_type": ["old_type"], "flowchart_steps": [{"type": "step", "label": "old_display"}],
        "format_diagnostics": [{"message": "old_diagnostic"}],
        "execution_semantics": [{"semantic": "RISING_EDGE", "devices": ["X1"], "evidence": "X1上升沿"}],
        "engineering_context": {"requests": [{"text": "原始请求"}]},
    }
    before = copy.deepcopy(spec)
    result, _, _ = build("保留原有行为", spec, analysis_mode=analysis_mode)
    snapshot = json.loads(result.system_prompt.split("# Confirmed project specification\n", 1)[1])
    assert not {"control_type", "flowchart_steps", "format_diagnostics"} & snapshot.keys()
    assert snapshot["execution_semantics"] == spec["execution_semantics"]
    assert snapshot["selected_approach"] == spec["selected_approach"]
    assert snapshot["intent_context"]["requests"] == spec["engineering_context"]["requests"]
    assert spec == before


def test_design_structure_vocabulary_comes_from_core_contract():
    from application.prompts import ANALYSIS_DESIGN_PROMPT
    from plc.specification.approach import SUPPORTED_STRUCTURES
    vocabulary = ANALYSIS_DESIGN_PROMPT.split("结构名：", 1)[1].split("。", 1)[0]
    assert vocabulary.split("、") == sorted(SUPPORTED_STRUCTURES)


@pytest.mark.parametrize("text,rotation", [
    ("三泵分别由三个液位开关控制", False),
    ("三泵按累计运行时间交替工作", True),
    ("交替启动两台泵", True),
    ("pump rotation", True),
])
def test_pump_count_never_injects_a_rotation_implementation(text, rotation):
    result, _, _ = build(text)
    assert ("pump" in result.route.topics) is rotation
    assert "Relevant questions: pump" not in result.system_prompt
    assert "位移链/指针" not in result.system_prompt


def test_generic_debug_prompt_does_not_force_a_model_specific_checklist():
    from application.prompts import DEBUG_REPORT_SYSTEM_PROMPT, SIMULATOR_TEST_SUITE_SYSTEM_PROMPT
    assert "M8029 placement" not in DEBUG_REPORT_SYSTEM_PROMPT
    assert "FX3U 32-bit" not in DEBUG_REPORT_SYSTEM_PROMPT
    # The native simulator's real capability/scope restrictions are unchanged.
    assert "FX3U GX Simulator2" in SIMULATOR_TEST_SUITE_SYSTEM_PROMPT
    assert "Never write Y/T/C/S or M8xxx/D8xxx" in SIMULATOR_TEST_SUITE_SYSTEM_PROMPT
