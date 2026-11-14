"""Editable engineering document alongside the managed project and its approved spec."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

from application.workspace import ConflictError, atomic_json, canonical_hash
from plc.specification.confirmed import validate_spec_draft
from plc.specification.implementation import (
    ROW_FIELDS, normalize_implementation_instances, normalize_open_questions, normalize_review_questions,
)
from plc.specification.provenance import CONFIRMED_SPEC_FIELDS
from shared.records import contained


ROW_SECTIONS = {"parameters", "io_table", "operation_intents", "behavior_constraints", *ROW_FIELDS}
EDITABLE = (CONFIRMED_SPEC_FIELDS - {"schema_version", "plc_model", "target_mode"}) | {
    "approaches", "confirmed_behavior_ids", "confirmed_operation_intent_ids",
}


def document_path(store, project_id):
    directory = store.project_dir(project_id)
    return contained(directory / "plc-spec.json", directory)


def project_behavior_cpu(project, plc_model):
    from plc.target_identity import execution_target_member
    return execution_target_member(plc_model, project.get("plc_model"))


def project_acceptance_cases(project, plc_model):
    """Load cases alongside the confirmed spec, using only managed paths."""
    from simulator.io_example import load_acceptance_cases

    if "_acceptance_cases" in project:
        return project["_acceptance_cases"]
    document = project.get("_spec_document") or {}
    path = None
    if document.get("path"):
        directory = Path(document["path"]).parent
        try:
            path = contained(directory / "acceptance-cases.json", directory)
        except (OSError, ValueError):
            return None, "acceptance_file_unreadable"
    return load_acceptance_cases(project.get("id"), project_behavior_cpu(project, plc_model), project_file=path,
                                 spec_revision=document.get("base_revision"))


def _normalize_draft_rows(spec, *, recover_selected_approach=False):
    # Older I/O and parameter rows have an address/name but no editable identity.
    for section, key in (("io_table", "address"), ("parameters", "name")):
        rows = spec.get(section)
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and not row.get("id") and row.get(key):
                row["id"] = f"{section}-{row[key]}"
    if "open_questions" in spec:
        spec["open_questions"] = normalize_open_questions(spec["open_questions"], parameters=spec.get("parameters"))
    if "implementation_items" in spec:
        spec["implementation_items"] = normalize_implementation_instances(spec["implementation_items"])
    return normalize_review_questions(spec, recover_selected_approach=recover_selected_approach)


def editable_spec(project):
    draft = copy.deepcopy(project.get("confirmed_spec") or {})
    draft.setdefault("plc_model", project["plc_model"])
    draft.setdefault("target_mode", project.get("target_mode") or "ladder")
    return _normalize_draft_rows(draft)


def differences(before, after, path="$"):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [diff for key in sorted(before.keys() | after.keys())
                for diff in differences(before.get(key), after.get(key), f"{path}.{key}")]
    if isinstance(before, list) and isinstance(after, list):
        if all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in before + after):
            left, right = ({row["id"]: row for row in group} for group in (before, after))
            rows = [diff for key in dict.fromkeys([*left, *right])
                    for diff in differences(left.get(key), right.get(key), f"{path}[{key}]")]
            if rows:
                return rows
    return [{"path": path, "before": before, "after": after}]


def read_document(store, project, version=None):
    path = document_path(store, project["id"])
    baseline = editable_spec(project)
    draft = baseline
    file_error = None
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(parsed, dict):
                raise ValueError("规格顶层必须是 JSON 对象")
            draft = _normalize_draft_rows(parsed, recover_selected_approach=project.get("confirmed_spec") is None)
        except (ValueError, UnicodeError) as error:
            file_error = {"code": "invalid_spec_file", "path": "$",
                          "message": f"请在 plc-spec.json 修复格式后重读：{error}"}
    confirmed = project.get("confirmed_spec")
    base = canonical_hash(confirmed) if confirmed is not None else None
    try:
        issues = validate_spec_draft(draft, project["plc_model"])
    except (TypeError, AttributeError) as error:
        issues = {"errors": [{"code": "invalid_spec_field", "path": "$",
                              "message": f"规格字段类型无效：{error}"}], "warnings": []}
    if file_error:
        issues["errors"].append(file_error)
    if draft.get("plc_model") != project["plc_model"] or draft.get("target_mode") != project.get("target_mode", "ladder"):
        issues["errors"].append({"code": "spec_target_mismatch", "path": "$", "message": "草稿型号或语言与工程不符"})
    return {"project_id": project["id"], "plc_model": project["plc_model"],
            "target_mode": project.get("target_mode", "ladder"), "path": str(path),
            "draft": draft, "confirmed_spec": confirmed, "base_revision": base,
            "draft_revision": canonical_hash([base, draft, file_error]) if file_error else canonical_hash([base, draft]),
            "differences": differences(baseline, draft), "issues": issues,
            "version_spec": (version or {}).get("confirmed_spec_snapshot")}


def check_revision(document, expected_revision):
    for issue in document["issues"]["errors"]:
        if issue["code"] == "invalid_spec_file":
            raise ValueError(issue["message"])
    if expected_revision != document["draft_revision"]:
        raise ConflictError("工程规格已变化，请重新读取 plc-spec.json 后再修改或确认。")


def apply_changes(draft, changes):
    if not isinstance(changes, list) or not changes:
        raise ValueError("请提供本轮需要更新的条目")
    result = copy.deepcopy(draft)
    for change in changes:
        if not isinstance(change, Mapping):
            raise ValueError("规格变更必须是对象")
        section, identity = change.get("section"), change.get("id")
        if section not in EDITABLE:
            raise ValueError(f"不可编辑的规格分区：{section}")
        remove = change.get("remove", False)
        if not remove and "value" not in change:
            raise ValueError("规格变更缺少 value")
        value = change.get("value")
        if identity is None:
            if remove:
                result.pop(section, None)
            else:
                result[section] = copy.deepcopy(value)
            continue
        if section not in ROW_SECTIONS or not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"无效的规格条目标识：{section}/{identity}")
        rows = result.setdefault(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} 必须是条目列表")
        index = next((i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("id") == identity), None)
        if remove:
            if index is None:
                raise ValueError(f"未找到条目：{identity}")
            rows.pop(index)
        elif not isinstance(value, dict) or value.get("id", identity) != identity:
            raise ValueError("局部变更必须使用同一条目标识")
        elif index is None:
            rows.append({**value, "id": identity})
        else:
            rows[index] = {**rows[index], **value, "id": identity}
    return _normalize_draft_rows(result)


def write_document(document, draft):
    atomic_json(Path(document["path"]), draft)


def bind_plan_interfaces(context, draft):
    """Resolve selected callable signatures against the saved GXW or CPU template."""
    items = [row for row in draft.get("implementation_items", [])
             if row.get("language") == "fbd" and row.get("template")]
    if not items:
        return draft
    from application.fbd import _target_baseline, catalog_snapshot
    from gxw.offline_project import read_offline_project
    raw = _target_baseline(context.native_snapshot, context.plc_model)
    programs = [row["source_name"] for row in read_offline_project(raw).sources() if row.get("language") == "fbd"]
    catalogs = {}
    for row in items:
        bindings = (context.version or {}).get('source_program_bindings') or {}
        key = bindings.get(row['location'], row['location']).casefold().removesuffix(".pou")
        program = next((p for p in programs if p.casefold().removesuffix(".pou") == key), None)
        if not program:
            raise ValueError(f"{row['id']}：工程内没有可绑定的 FBD 程序 {row['location']}")
        if program not in catalogs:
            catalogs[program] = catalog_snapshot(raw, program)["nodes"]
        name = row["template"].casefold()
        matches = [n for n in catalogs[program] if n["template"].casefold() == name or
                   ":" not in name and n["template"].split(":")[-1].casefold() == name]
        if len(matches) != 1:
            if not matches and row.get("source") == "new_custom" and row.get("rationale"):
                row.pop("catalog_binding", None)
                continue
            raise ValueError(f"{row['id']}：{row['template']} 在 {program} 的目录中缺失或不唯一；请核对模板名或工程库依赖。")
        node, = matches
        row["template"] = node["template"]
        origin = node.get("origin") or {}
        row["catalog_binding"] = {
            "program": program, "plc_model": context.plc_model,
            "origin": {k: origin[k] for k in ("kind", "library", "stream", "name", "status") if k in origin},
            "ports": [{k: port[k] for k in ("name", "direction", "data_type", "type", "side") if k in port}
                      for port in node.get("ports", [])],
        }
    return draft


def merge_analysis(previous, reviewed, analysis, request):
    """Retain stable rows that a later model turn didn't mention."""
    result = {**copy.deepcopy(previous), **reviewed, "target_mode": previous["target_mode"],
              "plc_model": previous["plc_model"]}
    for section in ROW_FIELDS:
        values = analysis.get(section) or []
        if not isinstance(values, list):
            raise ValueError(f"{section} 必须是条目列表")
        for row in values:
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"{section} 需要带 id 的条目")
            result = apply_changes(result, [{"section": section, "id": row["id"], "value": row}])
    if not result.get("requirements") and request.strip():
        result["requirements"] = [{"id": "requirement-1", "text": request, "source": "user"}]
    return _normalize_draft_rows(result)


def analysis_inputs(context, request, document):
    mode = (context.version or {}).get("target_mode") or context.project["target_mode"]
    result = {"target_mode": mode, "plc_model": context.plc_model, "working_draft": document["draft"]}
    if mode == "fbd":
        from agent_runtime.native_tools import _get_fbd_catalog
        from application.fbd_context import model_catalog_nodes
        catalog = _get_fbd_catalog(context, {"catalog_scope": "task", "user_requirement": request})
        result.update(program=catalog.get("program"), available_calls=model_catalog_nodes(catalog["nodes"]),
                      task_scopes=catalog.get("task_scopes"))
    elif context.version:
        from agent_runtime.generation_tools import _engineering_generation_context
        view = _engineering_generation_context(context, {"user_requirement": request, "execution_mode": "background"})
        for field in ("current_model", "current_context", "selected_program", "output_contract"):
            if view.get(field):
                result[field] = view[field]
        if context.ladder:
            result["current_networks"] = [{k: rung[k] for k in ("rung_id", "comment") if k in rung}
                                          for rung in context.ladder.get("rungs", [])]
    elif mode == "sfc":
        result["missing_source"] = "先提供已有 SFC GXW，再规划对步骤、转换和动作的编辑。"
    return result
