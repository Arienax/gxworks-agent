"""Language-specific planning instructions and the portable engineering plan rows."""
from __future__ import annotations

import copy
import json
import re


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


def _analysis_rows(required_fields, **optional):
    text = {"type": "string", "pattern": r"\S"}
    return {"type": "array", "items": {"type": "object", "required": list(required_fields),
            "properties": {**dict.fromkeys(required_fields, text), **optional}}}


_OPTIONAL_TEXT = {"type": ["string", "null"]}
PLAN_ROW_SCHEMAS = {
    "requirements": _analysis_rows(("id", "text"), source=_OPTIONAL_TEXT, status=_OPTIONAL_TEXT),
    "implementation_items": _analysis_rows(("id", "language", "location", "method", "state_owner"),
        requirement_ids={"type": "array", "items": {"type": "string"}},
        instances={"type": "array", "items": {"type": "string", "pattern": r"\S"}},
        source=_OPTIONAL_TEXT, rationale=_OPTIONAL_TEXT),
    "open_questions": _analysis_rows(("id", "text"), source=_OPTIONAL_TEXT,
        required={"type": ["boolean", "null"]}),
}


def normalize_implementation_instances(rows):
    """Recover instance names and retain any details from older object lists."""
    if not isinstance(rows, list):
        return rows
    normalized = []
    for row in rows:
        instances = row.get("instances") if isinstance(row, dict) else None
        if isinstance(instances, list) and isinstance(row.get("method"), str):
            names = [item.get("name") if isinstance(item, dict) else item for item in instances]
            if names != instances and all(isinstance(name, str) and name.strip() for name in names):
                row = {**row, "instances": names}
                details = [item for item in instances if isinstance(item, dict) and len(item) > 1]
                if details:
                    row["method"] += "\n实例明细 " + json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        normalized.append(row)
    return normalized


_ANSWER_PROMPT = re.compile(
    r"^(?:请(?:填写|输入|提供|说明|补充|给出|选择)|填写|逐(?:项|点)提供|提供.*[，,]\s*(?:并|再)说明|"
    r"(?:其他|其它)[，,（( ]+(?:请)?(?:说明|填写|补充)|"
    r"please\s+(?:enter|specify|provide|describe|choose)\b|fill\s+in\b)",
    re.IGNORECASE,
)


def _approach_id(approach):
    if isinstance(approach, dict):
        value = approach.get("approach_id") or approach.get("id")
        if isinstance(value, str):
            return value.strip()
    return ""


def _option_key(value):
    return re.sub(r"\s*[:：]\s*", ":", value.strip()).casefold() if isinstance(value, str) else ""


def _condition_parameters(condition):
    if isinstance(condition, dict):
        if isinstance(condition.get("parameter"), str):
            yield condition["parameter"].strip()
        for key in ("all", "any"):
            children = condition.get(key)
            if isinstance(children, list):
                for child in children:
                    yield from _condition_parameters(child)


def normalize_review_questions(spec, *, recover_selected_approach=False):
    """Keep process questions separate from candidate selection on an editable copy."""
    proposals = spec.get("approaches")
    approaches, labels = {}, {}
    for approach in proposals if isinstance(proposals, list) else ():
        identity = _approach_id(approach)
        if not identity:
            continue
        approaches[identity] = approach
        title = approach.get("name") or approach.get("title")
        choices = [identity, title, f"{identity}:{title}"] if isinstance(title, str) and title.strip() else [identity]
        for label in choices:
            key = _option_key(label)
            labels[key] = identity if labels.get(key, identity) == identity else None

    sections = {key: spec[key] for key in ("missing_info", "parameters", "open_questions")
                if isinstance(spec.get(key), list)}
    dependent = {key for rows in sections.values() for row in rows if isinstance(row, dict)
                 for key in _condition_parameters(row.get("required_when"))}
    saved_choices = set()
    for section, rows in sections.items():
        normalized = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("options"), list):
                options = row["options"]
                option_ids = {labels.get(_option_key(value)) for value in options}
                answer = row.get("value" if section == "parameters" else "answer")
                choice = labels.get(_option_key(answer))
                identities = {row[key].strip() for key in ("id", "name", "question", "text", "semantic_key")
                              if isinstance(row.get(key), str)}
                if dependent.intersection(identities):
                    normalized.append(row)
                    continue
                if (len(option_ids) >= 2 and None not in option_ids
                        and (answer in (None, "") or choice in option_ids)
                        and not row.get("io_binding")):
                    if choice and row.get("source") == "user":
                        saved_choices.add(choice)
                    continue
                prompts = [value for value in options if isinstance(value, str)
                           and (_ANSWER_PROMPT.match(value.strip()) or value.rstrip().endswith(("?", "？")))]
                if prompts:
                    row = {**row, "options": [value for value in options if value not in prompts]}
                    for key in ("value", "answer", "default", "suggested_default"):
                        if row.get(key) in prompts:
                            row[key] = ""
            normalized.append(row)
        spec[section] = normalized

    selected = _approach_id(spec.get("selected_approach"))
    if recover_selected_approach and len(saved_choices) == 1 and selected in {"", next(iter(approaches), "")}:
        spec["selected_approach"] = copy.deepcopy(approaches[next(iter(saved_choices))])
    return spec


def normalize_open_questions(rows, *, parameters=None):
    """Keep independent questions and let linked parameter rows own the answer."""
    if not isinstance(rows, list):
        return rows
    parameter_ids = {
        row["id"].strip() for row in (parameters if isinstance(parameters, list) else [])
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()
    }
    normalized = []
    for row in rows:
        related = row.get("related_missing_info") if isinstance(row, dict) else None
        if isinstance(related, str) and related.strip() in parameter_ids:
            continue
        if isinstance(row, dict) and "text" not in row and isinstance(row.get("question"), str):
            row = row.copy()
            row["text"] = row.pop("question")
        normalized.append(row)
    return normalized


def planning_instructions(language):
    return (LANGUAGE_PLANNING.get(language, "") +
            " 先给用户看控制方案与实现分工。requirements 使用 {id,text,source}，source 为 user、project 或 model。"
            "implementation_items 每项写 id、requirement_ids、language、location、method、state_owner，"
            "适用时附确切的 template、instances、source、rationale。"
            "instances 使用实例名称字符串列表，实例参数和动作细节写入 method。"
            "FBD 的 location 使用工程内的程序名，拟新建的块写 source=new_custom 及理由。"
            "open_questions 使用 {id,text,required}，required 为布尔值。"
            "每个确认点只写一次。有选项或需要填写参数的题放 missing_info，open_questions 只写前面未覆盖的独立问题。"
            "沿用已有 id；未确定的接线和参数留待确认。用户明确修订后只改涉及的条目。")


def uncovered_requirements(spec):
    """Find requirement rows with no implementation item referencing their ID."""
    items, requirements = spec.get("implementation_items"), spec.get("requirements")
    if not isinstance(items, list) or not items or not isinstance(requirements, list):
        return []
    covered = {ref for item in items if isinstance(item, dict) and isinstance(item.get("requirement_ids"), list)
               for ref in item["requirement_ids"] if isinstance(ref, str)}
    missing = {row["id"]: row for row in requirements if isinstance(row, dict)
               and isinstance(row.get("id"), str) and row["id"].strip() and row["id"] not in covered}
    return [missing[identity] for identity in sorted(missing)]


def plan_issues(spec):
    """Report malformed or dangling plan entries before confirmation."""
    issues = []

    def issue(section, message):
        issues.append({"code": "invalid_project_plan", "path": "$." + section, "message": message})

    ids = {}
    for section in ROW_FIELDS:
        rows = spec.get(section, [])
        if section == "open_questions":
            rows = normalize_open_questions(rows, parameters=spec.get("parameters"))
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
    for row in uncovered_requirements(spec):
        issue("requirements", f"需求 {row['id']} 尚未安排实现分工")
    io_rows = spec.get("io_table")
    if isinstance(io_rows, list):
        io_ids = [r["id"] for r in io_rows if isinstance(r, dict) and isinstance(r.get("id"), str)]
        if len(set(io_ids)) != len(io_ids):
            issue("io_table", "I/O 条目标识重复")
    return issues
