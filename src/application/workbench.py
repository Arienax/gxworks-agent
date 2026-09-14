"""Public Workbench service with explicit repair kept outside the core service."""
from __future__ import annotations

import copy
import json
import re

from application.workbench_impl import *  # noqa: F401,F403
from application.workbench_impl import WorkbenchService as _WorkbenchService
from application.projects import contained, record_id
from application.workspace import ConflictError


class WorkbenchService(_WorkbenchService):
    def repair_generation(self, job_id, request_id):
        """Submit one path-addressed repair derived from this failure only."""
        self.writable()
        record_id(request_id)
        with self.lock.thread_lock:
            if not self.jobs:
                raise KeyError(job_id)
            record = self.jobs._load(record_id(job_id))
            if (record.get("kind") != "generation" or record.get("status") != "failed"
                    or record.get("error_code") != "generation_validation_failed"):
                raise ConflictError("Only a failed structural generation can be repaired")
            snapshot = copy.deepcopy(record.get("snapshot") or {})
            self._check_snapshot(snapshot)
            project_id = snapshot["project_id"]
            version_id = snapshot.get("version_id")
            root = contained(
                self.state_dir / "staging" / record_id(job_id),
                self.state_dir / "staging",
            )
            candidate_path = contained(root / "repair_candidate.json", root)
            if not candidate_path.is_file():
                raise ConflictError("The rejected candidate is no longer available for repair")
            candidate_text = candidate_path.read_text(encoding="utf-8")
            if not candidate_text.strip() or len(candidate_text) > 512000:
                raise ConflictError("The rejected candidate is too large or empty")

            details = record.get("error_details") or {}
            violations = details.get("violations") if isinstance(details, dict) else []
            violations = [item for item in (violations or []) if isinstance(item, dict)]
            locations = []
            for item in violations:
                path = item.get("path")
                reason = item.get("reason")
                if isinstance(path, str):
                    locations.append(path + (f" ({reason})" if isinstance(reason, str) else ""))
            language = (
                snapshot.get("response_language")
                if snapshot.get("response_language") in ("zh-CN", "en", "ja")
                else "zh-CN"
            )

            from application.repair_scope import derive, RepairScopeError
            try:
                repair_base, allowed_rung_ids, allowed_addresses = derive(
                    candidate_text, snapshot, violations
                )
            except RepairScopeError as error:
                raise ConflictError(str(error)) from error

        location_text = "；".join(locations) if locations else "ladder schema"
        local_repair = repair_base is not None
        if local_repair:
            rung_text = ", ".join(map(str, sorted(allowed_rung_ids)))
            repair_text = (
                "这是用户明确确认的一次局部结构修复。完整失败候选由后端持有，模型只能返回 path-addressed patch。"
                "禁止返回完整梯级、完整 branches、partial ladder 或完整程序。"
                "只修改失败位置所需的最小 JSON 子树，保持其余控制逻辑、地址、参数和触点极性不变。\n"
                f"本次允许修改的 rung_id：{rung_text}\n"
                f"本次失败位置：{location_text}"
            )
        else:
            repair_text = (
                "这是用户明确确认的一次 JSON 格式修复。失败候选无法安全解析，"
                "因此只能修复 JSON 语法/闭合结构，不得改变 PLC 逻辑。"
                "返回完整 ladder JSON，不要输出解释文本。\n"
                f"失败位置：{location_text}\n\n失败候选 JSON：\n{candidate_text}"
            )

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
            "task_type": "contract_repair" if local_repair else "generate",
            "repair_violations": copy.deepcopy(violations),
        }
        if local_repair:
            command.update(
                repair_baseline=repair_base,
                allowed_rung_ids=sorted(allowed_rung_ids),
                allowed_addresses=sorted(allowed_addresses),
            )
        return self.submit(command)
