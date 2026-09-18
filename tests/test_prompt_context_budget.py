import json

import application.generation_context as compact
import application.generation_context_support as legacy
from shared.context_policy import context_policy_scope


def _without_knowledge(*_args, **_kwargs):
    return ""


def _without_profile(*_args, **_kwargs):
    return ""


def _without_confirmed(prompt, _context=None):
    return prompt


def _program(count=24):
    comments = {}
    rungs = []
    for number in range(1, count + 1):
        comments[f"X{number}"] = f"Input {number}"
        comments[f"Y{number}"] = f"Output {number}"
        rungs.append({
            "rung_id": number,
            "debug_note": f"DETAIL_{number}_" + ("x" * 80),
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": f"X{number}", "label": f"Input {number}"}],
                "outputs": [{"type": "COIL", "address": f"Y{number}", "label": f"Output {number}"}],
            }],
        })
    return {"device_comments": comments, "rungs": rungs}


def _instructions(module, request, *, mode="ladder", edit=False, current=None):
    return module.build_generation_instructions(
        request,
        plc_model="FX3U",
        target_mode=mode,
        is_edit_mode=edit,
        current_version_json=current,
        knowledge_builder=_without_knowledge,
        profile_builder=_without_profile,
        confirmed_builder=_without_confirmed,
    )


def test_normal_ladder_context_is_less_than_half_of_legacy_without_external_context():
    with context_policy_scope("legacy"):
        before = _instructions(legacy, "X0 启动 Y0，X1 停止")
        after = _instructions(compact, "X0 启动 Y0，X1 停止")
    assert len(after) < len(before) * 0.50
    assert "经典多模范例" in before
    assert "经典多模范例" not in after


def test_normal_st_context_is_less_than_half_of_legacy_without_external_context():
    with context_policy_scope("legacy"):
        before = _instructions(legacy, "X0 启动 Y0，X1 停止", mode="st")
        after = _instructions(compact, "X0 启动 Y0，X1 停止", mode="st")
    assert len(after) < len(before) * 0.50
    assert "工业常识模式库" in before
    assert "工业常识模式库" not in after


def test_large_local_edit_no_longer_replays_the_full_pretty_printed_program():
    baseline = _program()
    request = "只修改 Y12 对应梯级，把 X12 改成常闭"
    with context_policy_scope("minimal"):
        before = _instructions(legacy, request, edit=True, current=baseline)
        after = _instructions(compact, request, edit=True, current=baseline)
    assert len(after) < len(before) * 0.55
    assert "DETAIL_12_" in after
    assert "DETAIL_11_" in after and "DETAIL_13_" in after
    assert "DETAIL_1_" not in after and "DETAIL_24_" not in after
    assert "# Current version JSON for review/debug context" in before
    assert "# Current program edit context" in after


def test_repair_system_context_has_hard_upper_bounds_without_full_generation_prompt():
    baseline = _program(4)
    contract = compact.build_generation_instructions(
        "允许修改的 rung_id：2\n失败位置：content$.rungs.1.shared_inputs",
        plc_model="FX3U",
        target_mode="ladder",
        is_edit_mode=True,
        task_type="contract_repair",
        current_version_json=baseline,
    )
    format_only = compact.build_generation_instructions(
        "这是用户明确确认的一次 JSON 格式修复。",
        plc_model="FX3U",
        target_mode="ladder",
        task_type="format_repair",
        current_version_json=baseline,
    )
    assert len(contract) < 5000
    assert len(format_only) < 1200
    assert "经典多模范例" not in contract + format_only
    assert "Selected PLC model profile" not in contract + format_only
    assert "Retrieved PLC evidence" not in contract + format_only
