"""Compare a candidate's materialized sources with its frozen implementation plan."""
from __future__ import annotations

import json

from plc.specification.source_plan import check_source_plan
from shared.records import contained


def attach_source_check(validation, spec, language, **source):
    if not any(row.get("language") == language for row in (spec or {}).get("implementation_items", [])):
        return
    report = check_source_plan(spec, language=language, **source)
    validation.setdefault("source_plan", {})[language] = report
    if report["status"] == "violated":
        errors = [f"{row['implementation_id']}：{row['message']}" for row in report["issues"] if row["severity"] == "error"]
        raise ValueError("候选源码与确认方案冲突：" + "；".join(errors))


def check_version_sources(store, project_id, version_id, metadata, spec):
    root = store.version_dir(project_id, version_id)
    files = {k: contained(root / name, root) for k, name in metadata.get("artifacts", {}).items()
             if k in {"ir", "st", "sfc", "gxw"}}
    mode = metadata.get("target_mode")
    validation = metadata.setdefault("validation", {})
    languages = {row.get("language") for row in spec["implementation_items"]}
    if mode == "ladder" and "ladder" in languages:
        model = json.loads(files["ir"].read_bytes()) if "ir" in files else None
        attach_source_check(validation, spec, mode, model=model)
    if "st" in languages and (mode == "st" or "gxw" in files):
        source = files["st"].read_text(encoding="utf-8-sig") if "st" in files else None
        declarations = None
        if "gxw" in files:
            from gxw.st_program import project_context_snapshot
            declarations = project_context_snapshot(files["gxw"].read_bytes(), metadata.get("program_name"), include_graphs=False)
            if source is None:
                source = next((p["st_text"] for p in declarations["pous"] if p.get("st_text")), None)
        attach_source_check(validation, spec, "st", source=source, declarations=declarations)
    if mode == "sfc" and "sfc" in languages:
        model = json.loads(files["sfc"].read_bytes()) if "sfc" in files else None
        base_id = metadata.get("parent_version_id")
        baseline = None
        if base_id:
            base = store.get_version(project_id, base_id) or {}
            if "sfc" in (base.get("artifacts") or {}):
                directory = store.version_dir(project_id, base_id)
                baseline = json.loads(contained(directory / base["artifacts"]["sfc"], directory).read_bytes())
        attach_source_check(validation, spec, mode, model=model, baseline=baseline)
