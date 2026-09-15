import json

import pytest

import plc_generation_context as compact
import plc_generation_context_legacy as legacy
from prompt_context_policy import context_policy_scope


def _rung(identifier):
    return {
        "rung_id": identifier,
        "debug_note": f"RUNG_{identifier}_DETAIL_SENTINEL",
        "header_element": None,
        "shared_inputs": [],
        "branches": [{
            "branch_id": 1,
            "y_offset_level": 0,
            "inputs": [{"type": "NO", "address": f"X{identifier}"}],
            "outputs": [{"type": "COIL", "address": f"Y{identifier}"}],
        }],
    }


def _program(count=6):
    comments = {}
    for identifier in range(1, count + 1):
        comments[f"X{identifier}"] = f"Input {identifier}"
        comments[f"Y{identifier}"] = f"Output {identifier}"
    return {"device_comments": comments, "rungs": [_rung(i) for i in range(1, count + 1)]}


def _forbidden(*_args, **_kwargs):
    raise AssertionError("repair prompt must not load normal generation context")


def test_fixed_generation_kernels_are_small_and_have_no_base_few_shot():
    assert len(compact.LADDER_SYSTEM_PROMPT) < len(legacy.LADDER_SYSTEM_PROMPT) * 0.25
    assert len(compact.ST_SYSTEM_PROMPT) < len(legacy.ST_SYSTEM_PROMPT) * 0.40
    assert "经典多模范例" not in compact.LADDER_SYSTEM_PROMPT
    assert "M8029 正例" not in compact.LADDER_SYSTEM_PROMPT
    assert "工业常识模式库" not in compact.ST_SYSTEM_PROMPT
    assert "# 范例" not in compact.ST_SYSTEM_PROMPT
    assert "# Authority" in compact.LADDER_SYSTEM_PROMPT
    assert "# Authority" in compact.ST_SYSTEM_PROMPT


def test_contract_repair_bypasses_profile_rag_spec_and_only_exposes_allowed_rung():
    baseline = _program()
    prompt = compact.build_generation_instructions(
        "这是局部结构修复。\n允许修改的 rung_id：3\n失败位置：content$.rungs.2.shared_inputs",
        plc_model="FX3U",
        target_mode="ladder",
        is_edit_mode=True,
        task_type="contract_repair",
        confirmed_context={"summary": "CONFIRMED_SPEC_SENTINEL"},
        current_version_json=baseline,
        prompt_builder=_forbidden,
        knowledge_builder=_forbidden,
        profile_builder=_forbidden,
        confirmed_builder=_forbidden,
    )
    assert "Local ladder contract repair" in prompt
    assert "RUNG_3_DETAIL_SENTINEL" in prompt
    assert "RUNG_2_DETAIL_SENTINEL" not in prompt
    assert "RUNG_4_DETAIL_SENTINEL" not in prompt
    assert "CONFIRMED_SPEC_SENTINEL" not in prompt
    assert "Selected PLC model profile" not in prompt
    assert "Retrieved PLC evidence" not in prompt
    assert "Machine-readable output schema" not in prompt


def test_comment_only_contract_repair_exposes_comments_without_rungs():
    baseline = _program(2)
    prompt = compact.build_generation_instructions(
        "允许修改的 rung_id：无（仅允许修复注释字段）\n失败位置：content$.device_comments.Y1",
        plc_model="FX3U",
        target_mode="ladder",
        is_edit_mode=True,
        task_type="contract_repair",
        current_version_json=baseline,
        prompt_builder=_forbidden,
        knowledge_builder=_forbidden,
        profile_builder=_forbidden,
        confirmed_builder=_forbidden,
    )
    assert '"rungs":[]' in prompt
    assert '"Y1":"Output 1"' in prompt
    assert "RUNG_1_DETAIL_SENTINEL" not in prompt


def test_format_repair_is_json_only_and_loads_no_plc_context():
    prompt = compact.build_generation_instructions(
        "repair malformed json",
        plc_model="FX3U",
        target_mode="ladder",
        task_type="format_repair",
        confirmed_context={"summary": "CONFIRMED_SPEC_SENTINEL"},
        current_version_json=_program(),
        prompt_builder=_forbidden,
        knowledge_builder=_forbidden,
        profile_builder=_forbidden,
        confirmed_builder=_forbidden,
    )
    assert prompt == compact.FORMAT_REPAIR_SYSTEM_PROMPT
    assert "CONFIRMED_SPEC_SENTINEL" not in prompt
    assert "Selected PLC model profile" not in prompt
    assert "Retrieved PLC evidence" not in prompt
    assert "Machine-readable output schema" not in prompt


def test_normal_edit_uses_rung_index_plus_local_detail_instead_of_full_baseline():
    baseline = _program()
    captured = {}

    def knowledge(_query, **kwargs):
        captured["evidence"] = kwargs.get("evidence")
        return ""

    with context_policy_scope("minimal"):
        prompt = compact.build_generation_instructions(
            "把 Y3 的控制条件改为常闭 X3",
            plc_model="FX3U",
            target_mode="ladder",
            is_edit_mode=True,
            current_version_json=baseline,
            knowledge_builder=knowledge,
            profile_builder=lambda *_a, **_k: "",
            confirmed_builder=lambda value, _ctx=None: value,
        )
    assert "Current program edit context" in prompt
    assert "RUNG_2_DETAIL_SENTINEL" in prompt
    assert "RUNG_3_DETAIL_SENTINEL" in prompt
    assert "RUNG_4_DETAIL_SENTINEL" in prompt
    assert "RUNG_1_DETAIL_SENTINEL" not in prompt
    assert "RUNG_6_DETAIL_SENTINEL" not in prompt
    assert captured["evidence"]["rung_index"]
    assert [r["rung_id"] for r in captured["evidence"]["rungs"]] == [2, 3, 4]


def test_user_turn_keeps_only_the_short_edit_hint():
    request = "把 X0 改为上升沿"
    assert compact.generation_user_input(request) == request
    edit = compact.generation_user_input(request, is_edit_mode=True)
    assert '优先返回 mode="partial"' in edit
    assert "不要重复输出未修改梯级" in edit
    assert "输出协议纪律" not in edit
    assert "debug_note 是可选字段" not in edit
    assert "目标不超过48字符" not in edit
    assert len(edit) < len(request) + 100


def test_simple_minimal_generation_does_not_reinject_generic_workflow_bundles():
    with context_policy_scope("minimal"):
        prompt = compact.build_generation_instructions(
            "X0 控制 Y0",
            plc_model="FX3U",
            target_mode="ladder",
            knowledge_builder=lambda *_a, **_k: "",
            profile_builder=lambda *_a, **_k: "",
            confirmed_builder=lambda value, _ctx=None: value,
        )
    assert "## Knowledge priority" not in prompt
    assert "## Scan cycle and output ownership review" not in prompt
    assert "## PLC execution semantics" not in prompt
    assert "经典多模范例" not in prompt
