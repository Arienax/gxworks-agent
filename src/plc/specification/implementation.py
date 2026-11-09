"""Language-specific planning instructions and the portable engineering plan rows."""
from __future__ import annotations


LANGUAGE_PLANNING = {
    "ladder": "按启停、模式、顺序、互锁和输出划分网络。说明网络顺序、边沿、保持和复位责任，使用本 CPU 的指令。",
    "st": "区分主程序、无状态 FUNCTION 和保存状态的 FB；引用实际 POU、任务、实例及接口。独立设备分别持有状态。",
    "fbd": "把信号流和联锁安排在主图，先匹配当前 CPU 的标准调用与工程已引用的库块。"
           "写明确切模板和独立实例；专有算法确需 ST FB 时说明理由、接口及状态归属。",
    "sfc": "在现有 SFC 图上绑定步骤、转换、动作、分支及进入、退出和复位行为。新工程须先提供可编辑的 SFC 工程。",
}

ROW_FIELDS = {
    "requirements": dict.fromkeys(("id", "text", "source", "status")),
    "open_questions": dict.fromkeys(("id", "text", "source", "required", "answer", "status")),
    "implementation_items": {
        **dict.fromkeys(("id", "language", "location", "method", "state_owner", "template", "source", "rationale")),
        "requirement_ids": [None], "instances": [None],
        "catalog_binding": {
            "program": None, "plc_model": None,
            "origin": dict.fromkeys(("kind", "library", "stream", "name", "status")),
            "ports": [dict.fromkeys(("name", "direction", "data_type", "type", "side"))],
        },
    },
}


def planning_instructions(language):
    return (LANGUAGE_PLANNING.get(language, "") +
            " 先给用户看控制方案与实现分工。requirements 使用 {id,text,source}，source 为 user、project 或 model。"
            "implementation_items 每项写 id、requirement_ids、language、location、method、state_owner，"
            "适用时附确切的 template、instances、source、rationale。FBD 的 location 使用工程内的程序名，"
            "拟新建的块写 source=new_custom 及理由。open_questions 记录待确定事项。"
            "沿用已有 id；未确定的接线和参数留待确认。用户明确修订后只改涉及的条目。")


def plan_issues(spec):
    """Report malformed or dangling plan entries before confirmation."""
    issues = []

    def issue(section, message):
        issues.append({"code": "invalid_project_plan", "path": "$." + section, "message": message})

    ids = {}
    for section in ROW_FIELDS:
        rows = spec.get(section, [])
        ids[section] = set()
        if not isinstance(rows, list):
            issue(section, "需要条目列表")
            continue
        for i, row in enumerate(rows):
            path = f"{section}[{i}]"
            if not isinstance(row, dict):
                issue(path, "条目必须是对象")
                continue
            identity = row.get("id")
            if not isinstance(identity, str) or not identity.strip() or identity in ids[section]:
                issue(path + ".id", "需要唯一的条目标识")
            else:
                ids[section].add(identity)
            if section == "implementation_items":
                for field in ("method", "location", "state_owner"):
                    if not isinstance(row.get(field), str) or not row[field].strip():
                        issue(path + "." + field, "实现项需要填写职责、位置和状态归属")
                if not isinstance(row.get("language"), str) or row["language"] not in LANGUAGE_PLANNING:
                    issue(path + ".language", "请选择已有工程支持的语言")
                refs = row.get("requirement_ids")
                if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in ids["requirements"] for r in refs):
                    issue(path + ".requirement_ids", "实现项必须引用存在的需求")
                instances = row.get("instances", [])
                if not isinstance(instances, list) or any(not isinstance(v, str) or not v.strip() for v in instances):
                    issue(path + ".instances", "实例必须是名称列表")
                elif len({v.casefold() for v in instances}) != len(instances):
                    issue(path + ".instances", "同一分配中的独立实例需要不同名称")
                if row.get("template") is not None and not isinstance(row["template"], str):
                    issue(path + ".template", "调用模板必须是工程目录中的名称")
            elif not isinstance(row.get("text"), str) or not row["text"].strip():
                issue(path + ".text", "需要填写条目内容")
            elif row.get("source") is not None and row["source"] not in ("user", "project", "model"):
                issue(path + ".source", "来源请选择 user、project 或 model")
            elif section == "open_questions" and row.get("required", True) and not row.get("answer"):
                issue(path, "请先回答或移除这个必要工艺问题")
    items = spec.get("implementation_items")
    if isinstance(items, list) and items:
        covered = {ref for item in items if isinstance(item, dict) and isinstance(item.get("requirement_ids"), list)
                   for ref in item["requirement_ids"] if isinstance(ref, str)}
        for missing in sorted(ids["requirements"] - covered):
            issue("requirements", f"需求 {missing} 尚未安排实现分工")
    io_rows = spec.get("io_table")
    if isinstance(io_rows, list):
        io_ids = [r["id"] for r in io_rows if isinstance(r, dict) and isinstance(r.get("id"), str)]
        if len(set(io_ids)) != len(io_ids):
            issue("io_table", "I/O 条目标识重复")
    return issues
