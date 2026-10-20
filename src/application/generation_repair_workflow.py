"""Prepare one bounded repair from an immutable rejected generation snapshot."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from application.jobs import JobManager
from application.projects import ProjectService
from application.workspace import WorkspaceWriterLock

import copy
import json
import re
from application.projects import contained, record_id
from application.workspace import ConflictError
from application.generation_repair_prompts import generation_repair_prompt


@dataclass(frozen=True)
class RepairServices:
    writable: Callable[[], None]
    lock: WorkspaceWriterLock
    jobs: JobManager
    projects: ProjectService
    state_dir: Path
    validate_snapshot: Callable[[dict], None]
    output: Callable[[str], dict]
    direct_user_fact_text: Callable[[dict], str]
    submit: Callable[[dict], dict]


def repair_scope(snapshot, candidate_text, violations):
    # A parseable rejected ladder is a safe repair baseline. Keep the
    # model on a partial replacement contract and enforce the same scope
    # again after materialization. Syntax-broken JSON has no trustworthy
    # rung identity and therefore uses the separate full-format path.
    from plc.candidate_repair import candidate_base
    from application.field_repair import plan as plan_field_patch
    inherited_repair_base = (
        candidate_base(snapshot.get("repair_baseline"))
        if snapshot.get("repair_mode") else None
    )
    inherited_repair_plan = (copy.deepcopy(snapshot.get("repair_plan"))
                             if snapshot.get("repair_mode") and isinstance(snapshot.get("repair_plan"), dict)
                             else None)
    inherited_local_repair = inherited_repair_base is not None
    if inherited_local_repair:
        # A failed repair attempt is still repairing the same immutable
        # baseline. Never widen it to a full-program regeneration merely
        # because the partial response itself had invalid JSON/shape.
        repair_base = inherited_repair_base
        local_repair = True
        allowed_rung_ids = {
            int(item) for item in (snapshot.get("allowed_rung_ids") or [])
            if isinstance(item, int) and not isinstance(item, bool)
        }
        allowed_addresses = {
            str(item).strip().upper()
            for item in (snapshot.get("allowed_addresses") or [])
            if isinstance(item, str) and item.strip()
        }
        repair_plan = inherited_repair_plan
    else:
        try:
            parsed_candidate = json.loads(candidate_text)
        except (TypeError, ValueError):
            parsed_candidate = None
        repair_base = candidate_base(parsed_candidate)
        local_repair = repair_base is not None
        allowed_rung_ids = set()
        allowed_addresses = set()
        repair_plan = plan_field_patch(
            repair_base, violations, snapshot.get("project", {}).get("plc_model", "FX3U")
        ) if local_repair else None
    if local_repair and isinstance(repair_plan, dict):
        target = repair_plan.get("target") or {}
        if target.get("strategy") == "blocked":
            raise ConflictError(
                "该校验错误涉及指令、地址或参数语义，局部修复不会猜测修改；请重新生成候选或手动修正。"
            )
    if local_repair and not inherited_local_repair and repair_plan is None:
        rungs = repair_base["rungs"]
        by_index = {index: rung for index, rung in enumerate(rungs)}
        saw_rung_path = False
        saw_comment_path = False
        unresolved_rung_path = False
        for item in violations or []:
            path = item.get("path") if isinstance(item, dict) else None
            if not isinstance(path, str):
                continue
            saw_comment_path |= "device_comments" in path
            if "rungs" not in path:
                continue
            saw_rung_path = True
            match = re.search(r"rungs(?:\.|\[)(\d+)", path)
            if match:
                rung = by_index.get(int(match.group(1)))
                rung_id = rung.get("rung_id") if isinstance(rung, dict) else None
                if isinstance(rung_id, int) and not isinstance(rung_id, bool):
                    allowed_rung_ids.add(rung_id)
                else:
                    unresolved_rung_path = True
            else:
                unresolved_rung_path = True
        if (unresolved_rung_path or (not saw_rung_path and not saw_comment_path)) and not allowed_rung_ids:
            allowed_rung_ids = {
                rung["rung_id"] for rung in rungs
                if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int)
                and not isinstance(rung.get("rung_id"), bool)
            }
        selected = [rung for rung in rungs if rung.get("rung_id") in allowed_rung_ids]
        from plc.specification.repair import patch_device_addresses
        allowed_addresses.update(patch_device_addresses({
            "mode": "partial", "rungs": selected,
            "delete_rung_ids": [], "device_comments": {},
        }))
        if saw_comment_path:
            allowed_addresses.update(
                str(address).strip().upper()
                for address in repair_base.get("device_comments", {})
                if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\d+", address.strip())
            )
    return repair_base, local_repair, allowed_rung_ids, allowed_addresses, repair_plan, inherited_local_repair


def repair_generation(service: RepairServices, job_id, request_id):
    """Submit one operator-confirmed repair without re-running normal generation."""
    service.writable()
    record_id(request_id)
    with service.lock.thread_lock:
        if not service.jobs:
            raise KeyError(job_id)
        record = service.jobs.record(record_id(job_id))
        failed_validation = (
            record.get("kind") in {"generation", "direct_generation"}
            and record.get("status") == "failed"
            and record.get("error_code") == "generation_validation_failed"
        )
        saved_invalid = (
            record.get("kind") in {"generation", "direct_generation"}
            and record.get("status") == "completed"
            and isinstance(record.get("result"), dict)
            and record["result"].get("status") == "saved_invalid"
            and isinstance(record["result"].get("version_id"), str)
        )
        if not (failed_validation or saved_invalid):
            raise ConflictError("Only a rejected generation candidate can be repaired")
        snapshot = copy.deepcopy(record.get("snapshot") or {})
        project_id = snapshot["project_id"]
        if saved_invalid:
            # Preserving an invalid candidate intentionally activates a
            # diagnostic version. Accept that one known state transition,
            # but reject unrelated project/spec changes before repairing.
            current = service.projects.raw_project(project_id)
            frozen = snapshot.get("project") or {}
            preserved_id = record["result"]["version_id"]
            if current.get("active_version_id") != preserved_id or any(
                current.get(key) != frozen.get(key)
                for key in ("confirmed_spec", "target_mode", "plc_model")
            ):
                raise ConflictError("错误候选保存后工程状态已变化，请重新生成或选择当前候选。")
        else:
            service.validate_snapshot(snapshot)
        version_id = snapshot.get("version_id")
        root = contained(service.state_dir / "staging" / record_id(job_id), service.state_dir / "staging")
        candidate_path = contained(root / "repair_candidate.json", root)
        if not candidate_path.is_file():
            raise ConflictError("The rejected candidate is no longer available for repair")
        candidate_text = candidate_path.read_text(encoding="utf-8")
        if not candidate_text.strip() or len(candidate_text) > 512000:
            raise ConflictError("The rejected candidate is too large or empty")
        handoff_path = contained(root / "generation_handoff.json", root)
        source_handoff = (json.loads(handoff_path.read_text(encoding="utf-8"))
                          if handoff_path.is_file() else snapshot.get("repair_source_handoff"))
        details = record.get("error_details") or {}
        if saved_invalid and not details:
            try:
                preserved_output = service.output(job_id)
                preserved_validation = ((preserved_output.get("generation") or {}).get("validation") or {})
                if isinstance(preserved_validation, dict):
                    details = preserved_validation
            except (KeyError, ValueError, OSError):
                details = {}
        violations = details.get("violations") if isinstance(details, dict) else []
        locations = []
        for item in violations or []:
            if isinstance(item, dict):
                path = item.get("path")
                reason = item.get("reason")
                if isinstance(path, str):
                    locations.append(path + (f" ({reason})" if isinstance(reason, str) else ""))
        language = snapshot.get("response_language") if snapshot.get("response_language") in ("zh-CN", "en", "ja") else "zh-CN"

        repair_base, local_repair, allowed_rung_ids, allowed_addresses, repair_plan, inherited_local_repair = repair_scope(snapshot, candidate_text, violations)

    repair_text = generation_repair_prompt(locations, local_repair=local_repair, repair_plan=repair_plan,
        allowed_rung_ids=allowed_rung_ids, inherited_local_repair=inherited_local_repair, candidate_text=candidate_text)
    command = {
        "kind": "generation",
        "project_id": project_id,
        "version_id": version_id,
        "request_id": request_id,
        "text": repair_text,
        "response_language": language,
        "attachment_ids": [],
        "change_scope": snapshot.get("change_scope"),
        "repair_origin_job_id": job_id,
        "repair_mode": local_repair,
        "format_repair": not local_repair,
        "task_type": "contract_repair",
        "repair_source_handoff": source_handoff,
    }
    if record.get("kind") == "direct_generation" or "user_fact_text" in snapshot:
        # Keep original human facts across repairs. Model questions/options
        # remain context only and must never become declared I/O facts.
        command["user_fact_text"] = service.direct_user_fact_text(snapshot)
        command["repair_requirement"] = snapshot.get("repair_requirement",
            snapshot.get("raw_requirement", snapshot.get("text", "")))
    if local_repair:
        command.update(
            repair_baseline=repair_base,
            allowed_rung_ids=sorted(allowed_rung_ids),
            allowed_addresses=sorted(allowed_addresses),
        )
        if repair_plan is not None:
            command["repair_plan"] = copy.deepcopy(repair_plan)
    return service.submit(command)
