"""Prompts for the bounded generation repair contracts."""


def generation_repair_prompt(locations, *, local_repair, repair_plan, allowed_rung_ids, inherited_local_repair, candidate_text):
    location_text = "；".join(locations) if locations else "ladder schema"
    if local_repair and repair_plan is not None:
        target = repair_plan.get("target") or {}
        repair_text = (
            "这是用户明确确认的一次字段级 JSON 修复。不要返回梯级、分支或完整程序。"
            "只返回 field_patch 协议对象，并且只能修改 target.path 指定的一个字段。"
            "不要改变其他地址、参数、触点极性或结构。\n"
            f"目标字段：{target.get('diagnostic_path') or target.get('path')}\n"
            f"失败位置：{location_text}"
        )
    elif local_repair:
        rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"
        retry_note = (
            "\n上一次局部修复回复仍未通过校验。只修正下面这个 partial patch 的 JSON/结构问题，"
            "不要扩大修改范围，也不要改成完整程序。\n上一次失败的局部 patch：\n" + candidate_text
            if inherited_local_repair else ""
        )
        repair_text = (
            "这是用户明确确认的一次局部结构修复。系统已把失败候选作为 Current version JSON 提供给你。"
            "不要重新分析需求，不要重新生成完整程序，不要改变控制逻辑、地址、参数、触点极性或未出错梯级。"
            "只返回一个完整可解析的 JSON 对象，并且必须使用 mode=\"partial\"。"
            "rungs 只包含需要替换的完整梯级，delete_rung_ids 必须为空，device_comments 只列确实需要修复的现有地址。"
            "debug_note 是可选字段，默认删除；label、debug_note 单条不得超过64字符；device_comment 只写用途短名称，最多16字符。\n"
            f"允许修改的 rung_id：{rung_text}\n"
            f"失败位置：{location_text}"
            + retry_note
        )
    else:
        repair_text = (
            "这是用户明确确认的一次局部 JSON 格式修复。完整失败候选由系统持有，严禁模型重写完整程序。"
            "先执行确定性语法恢复；只有仍有歧义时才允许模型返回 format_patch 的短 before/after 文本替换。"
            "不得返回 ladder、rungs、branch 或任何完整候选，不得改变地址、指令、参数、触点极性或控制语义。\n"
            f"失败位置：{location_text}\n\n失败候选 JSON：\n{candidate_text}"
        )
    return repair_text
