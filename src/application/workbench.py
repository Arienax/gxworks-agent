"""Application commands shared by the HTTP operator and connected MCP agents."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import tempfile
import threading
import uuid
import shared.diagnostics as diagnostics
from datetime import datetime, timezone
from pathlib import Path

from application.projects import ProjectService, contained, public, record_id
from application.workspace import WorkspaceWriterLock, ConflictError, atomic_json, canonical_hash, read_json
from agent_runtime.messages import ToolCall
from agent_runtime.runtime import public_tool_result_data
from plc.change_scope import ChangeScopeError
from shared.context_audit import ContextAudit, context_audit_scope


def _native_generation_metadata(cpu, receipt, summary):
    """Retain the selected menu and observed native identity on saved versions."""
    from plc.target_support import require_native_program_target
    selected = require_native_program_target(cpu)
    identity = receipt.get('identity') or {}
    if identity.get('cpu_menu') is not None:
        actual = require_native_program_target(identity['cpu_menu'])
        if actual.cpu != selected.cpu:
            raise ValueError('Native engineering receipt CPU differs from the saved target')
    result = {'native_engineering': summary, 'native_cpu_menu': selected.cpu}
    result.update({key: copy.deepcopy(identity[key]) for key in
                   ('software_version', 'native_ui_language', 'project_codepage') if key in identity})
    return result


class WorkbenchService:
    def __init__(self, workspace, state_dir, *, read_only=False, settings=None, model_factory=None):
        self.projects = ProjectService(workspace)
        self.store = self.projects.store
        self.state_dir = Path(state_dir).resolve()
        self.read_only = read_only
        if settings is None:
            from application.settings import SettingsService
            settings = SettingsService()
        self.settings = settings
        self.model_factory = model_factory or self.settings.model_snapshot
        self.lock = None
        self.jobs = None
        self.proposals = None
        self.execution = None
        self._lifecycle_lock = threading.RLock()
        self._started = False
        self._mcp_activity = {}
        from application.approval import ApprovalPolicy
        self.approval = ApprovalPolicy(self.state_dir)
        from application.fbd import FBDService
        self.fbd = FBDService(self)
        from application.sfc import SFCService
        self.sfc = SFCService(self)

    def start(self):
        with self._lifecycle_lock:
            if self._started:
                return self
            if not self.read_only:
                from application.jobs import JobManager
                from application.proposals import ProposalService
                from application.execution import GXExecutionCoordinator
                self.lock = WorkspaceWriterLock(self.store.base_dir).acquire()
                try:
                    with self.lock.thread_lock:
                        self.spec_migration = self.store.migrate_confirmed_specs()
                    self.jobs = JobManager(self.state_dir, self.lock)
                    self.proposals = ProposalService(self.store, self.state_dir, self.lock)
                    self.execution = GXExecutionCoordinator(self.store)
                except Exception:
                    self.close()
                    raise
            self._started = True
            return self

    def close(self):
        with self._lifecycle_lock:
            # Keep ownership until outstanding workers reach their checkpoints.
            try:
                if getattr(self, 'hardware', None):
                    hardware, self.hardware = self.hardware, None
                    hardware.close()
            finally:
                try:
                    if self.jobs:
                        self.jobs.shutdown(wait=True)
                finally:
                    try:
                        if self.execution:
                            self.execution.close(wait=True)
                    finally:
                        if self.lock:
                            self.lock.release()
                        self.jobs = self.proposals = self.execution = self.lock = None
                        self._started = False

    def writable(self):
        if self.read_only or not self.lock:
            raise PermissionError("工作台以只读模式打开。")
        self.lock.require_acquired()

    def approval_settings(self):
        return {**self.approval.read(), "local_autosave": True, "read_only": self.read_only}

    def update_approval_settings(self, **values):
        self.writable()
        with self.lock.thread_lock:
            self.approval.update(**values)
            return self.approval_settings()

    def _save_local_proposal(self, proposal):
        """Internal transaction/validation boundary, not an extra UI approval."""
        self.writable()
        if proposal["action"] != "accept_local":
            raise ValueError("Not a local save")
        return self.proposals.accept(proposal["id"], approved_by="local_autosave")

    def _apply_execution_policy(self, proposal, consent=None):
        """New requests only. Mode changes never drain previously pending actions."""
        from application.approval import allows
        if proposal["status"] != "pending":
            return proposal
        current = self.approval.read()
        # A running Agent may not inherit a later escalation of permission.
        if consent is not None and consent != current:
            return proposal
        payload = self.proposals.read_private(proposal["id"])
        if allows(current["mode"], proposal["action"], payload):
            result = self.decide(proposal["id"], "accept", policy=current)
            return {**self.proposals.get(proposal["id"]), "execution_job_id": result["job"]["id"]}
        return proposal

    def create_project(self, **values):
        self.writable()
        from plc.project_capabilities import normalize_project_selection, DEFAULT_PLC_MODEL
        values.update(normalize_project_selection(values.get("plc_model", DEFAULT_PLC_MODEL),
                      values.get("target_mode", "ladder"), values.get("native_project_type"), values.get("use_labels")))
        with self.lock.thread_lock:
            project = self.store.create_project(**values)
            return self.projects.project(project["id"])

    def update_project(self, project_id, **values):
        self.writable()
        with self.lock.thread_lock:
            current = self.projects.raw_project(project_id)
            if any(key in values for key in ("plc_model", "target_mode", "native_project_type", "use_labels")):
                from plc.project_capabilities import normalize_project_selection
                values.update(normalize_project_selection(values.get("plc_model", current["plc_model"]),
                              values.get("target_mode", current["target_mode"]),
                              values.get("native_project_type", current.get("native_project_type")),
                              values.get("use_labels", current.get("use_labels"))))
            self.store.update_project_settings(project_id, **values)
            return self.projects.project(project_id)

    def delete_project(self, project_id):
        """Delete one managed project after proving no live work still references it."""
        self.writable()
        with self.lock.thread_lock:
            self.projects.raw_project(project_id)
            active_jobs = [
                job for job in (self.jobs.list(project_id) if self.jobs else [])
                if job.get("status") in {"queued", "running", "cancelling"}
            ]
            if active_jobs:
                raise ConflictError("Project still has active jobs")
            active_proposals = [
                proposal for proposal in (self.proposals.list(project_id) if self.proposals else [])
                if proposal.get("status") in {"pending", "executing"}
            ]
            if active_proposals:
                raise ConflictError("Project still has pending or executing proposals")
            self.store.delete_project(project_id)
            return {"deleted": True, "project_id": project_id}

    def activate_version(self, project_id, version_id, expected_active_version_id):
        self.writable()
        with self.lock.thread_lock:
            project = self.projects.raw_project(project_id)
            self.projects.raw_version(project_id, version_id)
            if project.get("active_version_id") != expected_active_version_id:
                raise ConflictError("当前版本已被其他窗口改变，请刷新。")
            self.store.activate_version(project_id, version_id)
            return self.projects.project(project_id)

    def set_spec(self, project_id, spec, expected_hash):
        from plc.specification.confirmed import (
            canonicalize_confirmed_spec,
            preserve_io_user_edits,
            validate_spec_draft,
        )
        from plc.ir import canonical_sha256
        self.writable()

        def audited(result):
            diagnostics.record_operator_action(
                self.state_dir, "spec_update", project_id=project_id,
                payload={"spec": spec, "expected_hash": expected_hash}, result=result,
            )
            return result

        with self.lock.thread_lock:
            project = self.projects.raw_project(project_id)
            current = project.get("confirmed_spec")
            from plc.specification.provenance import matches_migrated_spec_hash
            if not matches_migrated_spec_hash(project, expected_hash):
                raise ConflictError("确认规格已变化，请重新加载。")
            candidate_spec = preserve_io_user_edits(current, spec)
            candidate_spec = copy.deepcopy(candidate_spec)
            intent_ids = candidate_spec.pop("confirmed_operation_intent_ids", [])
            behavior_ids = candidate_spec.pop('confirmed_behavior_ids', [])
            if behavior_ids:
                from plc.specification.behavior import confirm_behavior_constraints
                candidate_spec['behavior_constraints'] = confirm_behavior_constraints(
                    candidate_spec.get('behavior_constraints', []), behavior_ids)
            if intent_ids:
                from plc.instruction_binding import confirm_operation_intents
                candidate_spec["operation_intents"] = confirm_operation_intents(
                    candidate_spec.get("operation_intents", []), intent_ids,
                )
            issues = validate_spec_draft(candidate_spec, project.get("plc_model"))
            if issues.get("errors"):
                return audited({"valid": False, "issues": public(issues)})
            normalized = canonicalize_confirmed_spec(candidate_spec)
            issues = validate_spec_draft(normalized, project.get("plc_model"))
            if issues.get("errors"):
                return audited({"valid": False, "issues": public(issues)})
            self.store.set_confirmed_spec(project_id, normalized)
            persisted = self.projects.raw_project(project_id)["confirmed_spec"]
            return audited({"valid": True, "spec": public(persisted), "hash": canonical_sha256(persisted),
                            **({"issues": public(issues)} if issues.get("warnings") else {})})

    def upload_attachment(self, project_id, filename, data_base64):
        from storage.session import detect_image_media_type
        self.writable()
        self.projects.raw_project(project_id)
        data = base64.b64decode(data_base64, validate=True)
        if not data or len(data) > 30 * 1024 * 1024 or not detect_image_media_type(data):
            raise ValueError("请添加总计不超过 30 MiB 的 JPEG、PNG、GIF 或 WebP 图片。")
        with self.lock.thread_lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.state_dir, prefix="upload-") as directory:
                path = Path(directory) / (Path(filename.replace("\\", "/")).name or "image")
                path.write_bytes(data)
                record = self.store.import_image_attachments(project_id, [path])[0]
            atomic_json(self.state_dir / "attachments" / (record["attachment_id"] + ".json"),
                        {"project_id": project_id, "record": record})
            return {key: record[key] for key in ("attachment_id", "filename", "media_type", "size_bytes")}

    def _attachments(self, project_id, ids):
        from model_runtime.provider import ImageAttachment
        attachments = []
        for attachment_id in ids:
            path = contained(self.state_dir / "attachments" / (record_id(attachment_id) + ".json"), self.state_dir)
            saved = read_json(path)
            if saved["project_id"] != project_id:
                raise ValueError("Attachment belongs to another project")
            record = saved["record"]
            data = self.store.load_image_attachment(project_id, record)
            attachments.append(ImageAttachment(record["filename"], record["media_type"], data))
        if sum(len(item.data) for item in attachments) > 30 * 1024 * 1024:
            raise ValueError("Attachments exceed 30 MiB")
        return attachments

    def _check_snapshot(self, snapshot):
        project = self.projects.raw_project(snapshot["project_id"])
        old = snapshot["project"]
        if any(project.get(k) != old.get(k) for k in ("active_version_id", "confirmed_spec", "target_mode", "plc_model")):
            raise ConflictError("任务执行期间工程状态已变化，请根据当前版本重新提出候选。")
        version = snapshot.get("version")
        if version and self.projects.raw_version(project["id"], version["id"]) != version:
            raise ConflictError("任务绑定的基础版本已变化。")
        if snapshot.get("program_ir") is not None:
            if canonical_hash(self.projects.program(project["id"], version["id"])) != canonical_hash(snapshot["program_ir"]):
                raise ConflictError("任务绑定的程序内容已变化。")
        if snapshot.get("fbd_baseline") is not None:
            current = self.projects.artifact(project["id"], version["id"], "gxw").read_bytes()
            if current != base64.b64decode(snapshot["fbd_baseline"]):
                raise ConflictError("任务绑定的 GXW 工程已变化。")

    def output(self, job_id):
        if not self.jobs:
            raise KeyError("No job outputs in read-only mode")
        self.jobs.get(job_id)
        path = contained(self.state_dir / "outputs" / (record_id(job_id) + ".json"), self.state_dir)
        if not path.is_file():
            raise KeyError("No output is available yet")
        output = read_json(path)
        if isinstance(output, dict) and isinstance(output.get("analysis"), dict):
            from plc.specification.confirmed import restore_review_choices
            if isinstance(output.get("spec_draft"), dict):
                output["spec_draft"] = restore_review_choices(output["spec_draft"], output["analysis"])
        return public(output)

    def _render_ladder_preview(
        self, program, *, theme=None, confirmed_spec=None, validation_profile="strict"
    ):
        """Deterministic, in-memory view. Never trust an old rendered-file cache."""
        from plc.ir import ir_to_ladder, validate_plc_ir
        from plc.st_renderer import render_plc_ir_to_st
        from rendering.ladder_svg import AdvancedSVGLadder

        validate_plc_ir(
            program, confirmed_spec=confirmed_spec,
            validate_ladder=(validation_profile != "generation_structural"),
        )
        ladder = ir_to_ladder(program)
        svg = AdvancedSVGLadder().generate_ladder(json.dumps(ladder, ensure_ascii=False))
        return {"target_mode": "ladder", "ladder": ladder, "program": public(program),
                "svg": self.projects.themed_svg(svg, theme), "st": render_plc_ir_to_st(program)}

    def version_preview(self, project_id, version_id, *, theme=None):
        """Re-render a saved ladder from its verified IR without changing a version.

        A missing/corrupt SVG can be recovered as a display-only response. The
        original files, approval state, project history and GX state stay intact.
        """
        from plc.ir import canonical_sha256

        version = self.projects.raw_version(project_id, version_id)
        if version.get("target_mode") != "ladder":
            raise ValueError("Only a ladder version has a regenerable ladder preview")
        program = self.projects.program(project_id, version_id)
        if not program:
            raise KeyError("Canonical program is unavailable")
        if version.get("ir_sha256") and canonical_sha256(program) != version["ir_sha256"]:
            raise ConflictError("Version IR changed after validation")
        return {**self._render_ladder_preview(
                    program, theme=theme,
                    confirmed_spec=version.get("confirmed_spec_snapshot"),
                    validation_profile=version.get("validation_profile", "strict")),
                "version_id": version_id, "read_only": True}

    def generation_preview(self, job_id, *, theme=None):
        """Inspect a completed candidate, including explicitly blocked diagnostics.

        This is not an acceptance route. Legacy completed jobs retain their
        staged IR and can be inspected without paying for another generation.
        """
        from plc.ir import canonical_sha256

        if not self.jobs:
            raise KeyError("Generation jobs are unavailable")
        job = self.jobs.get(job_id)
        if job["kind"] not in {"generation", "direct_generation"} or job["status"] != "completed":
            raise KeyError("No completed generation is available")
        self.projects.raw_project(job["project_id"])
        output = self.output(job_id)
        if output.get("proposal_id"):
            proposal = self.proposals.get(output["proposal_id"])
            if proposal["project_id"] != job["project_id"]:
                raise ConflictError("Generation proposal belongs to another project")
            return {**self.proposal_preview(proposal["id"], theme=theme), "job_id": job_id,
                    "read_only": True, "proposal_id": proposal["id"]}
        metadata = output.get("generation") or {}
        mismatch = metadata.get("contract_mismatch")
        if not mismatch or output.get("status") != "contract_mismatch" or metadata.get("target_mode") != "ladder":
            raise KeyError("No inspectable generation candidate is available")
        root = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir / "staging")
        path = contained(root / "program.ir.json", root)
        if not path.is_file():
            raise KeyError("Generated IR is unavailable")
        program = read_json(path)
        if canonical_sha256(program) != metadata.get("ir_sha256"):
            raise ConflictError("Generated IR changed after validation")
        # Display the known approach mismatch; do not waive it for acceptance.
        # Structural, instruction and IR consistency checks remain mandatory.
        return {**self._render_ladder_preview(program, theme=theme),
                "job_id": job_id, "read_only": True, "status": "contract_mismatch",
                "contract_mismatch": public(mismatch),
                "validation": public(metadata.get("validation") or {})}

    def repair_generation(self, job_id, request_id):
        """Submit one operator-confirmed repair without re-running normal generation."""
        self.writable()
        record_id(request_id)
        with self.lock.thread_lock:
            if not self.jobs:
                raise KeyError(job_id)
            record = self.jobs._load(record_id(job_id))
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
                current = self.projects.raw_project(project_id)
                frozen = snapshot.get("project") or {}
                preserved_id = record["result"]["version_id"]
                if current.get("active_version_id") != preserved_id or any(
                    current.get(key) != frozen.get(key)
                    for key in ("confirmed_spec", "target_mode", "plc_model")
                ):
                    raise ConflictError("错误候选保存后工程状态已变化，请重新生成或选择当前候选。")
            else:
                self._check_snapshot(snapshot)
            version_id = snapshot.get("version_id")
            root = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir / "staging")
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
                    preserved_output = self.output(job_id)
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
            command["user_fact_text"] = self._direct_user_fact_text(snapshot)
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
        return self.submit(command)

    def submit(self, command):
        self.writable()
        # Mode is a frozen job input, never a PLC contract or mutable UI setting.
        command = copy.deepcopy(command)
        if command.get("generation_action") is not None and command.get("kind") != "generation":
            raise ValueError("生成动作仅适用于生成程序任务。")
        if command.get("generation_action") is None:
            command.pop("generation_action", None)
        if command.get("clarification_job_id") and command.get("kind") != "direct_generation":
            raise ValueError("续答仅适用于 Direct 一次生成。")
        if not command.get("clarification_job_id"):
            command.pop("clarification_job_id", None)
        if command.get("clarification_answers") is None:
            command.pop("clarification_answers", None)
        elif command.get("kind") != "direct_generation" or not command.get("clarification_job_id"):
            raise ValueError("逐项答复必须绑定 Direct 待补充信息的作业。")
        if command.get("kind") == "analysis":
            command["analysis_mode"] = "design" if command.get("analysis_mode") == "design" else "direct"
        else:
            command.pop("analysis_mode", None)
        # Retry identity is the original HTTP command, not a newly observed
        # project/model snapshot. This also avoids reopening credentials on retry.
        request_key = hashlib.sha256(command["request_id"].encode()).hexdigest()
        path = self.state_dir / "job_commands" / (request_key + ".json")
        digest = canonical_hash(command)
        with self.lock.thread_lock:
            if path.exists():
                saved = read_json(path)
                if saved["command_hash"] != digest:
                    # A retry from an older client may predate this field. Reuse
                    # that old job; never reinterpret or rerun its analysis.
                    legacy = {key: value for key, value in command.items() if key != "analysis_mode"}
                    legacy_direct_retry = (
                        command.get("kind") == "analysis"
                        and command.get("analysis_mode") == "direct"
                        and saved["command_hash"] == canonical_hash(legacy)
                    )
                    if not legacy_direct_retry:
                        raise ConflictError("Request ID is already bound to another command")
                return self.jobs.get(saved["job_id"])
            job = self._submit_job(command)
            atomic_json(path, {"command_hash": digest, "job_id": job["id"]})
            return job

    def _direct_user_fact_text(self, snapshot):
        """Recover only human input when resuming an older clarification snapshot."""
        parts, visited = [], set()
        while True:
            saved = snapshot.get("user_fact_text")
            parts.append(saved if isinstance(saved, str) else snapshot.get("text", ""))
            parent_id = snapshot.get("clarification_job_id")
            if isinstance(saved, str) or not parent_id:
                return "\n用户续答：\n".join(reversed(parts))
            if parent_id in visited:
                raise ConflictError("续答作业链存在循环。")
            visited.add(parent_id)
            snapshot = self.jobs._load(record_id(parent_id))["snapshot"]

    @staticmethod
    def _direct_answer_rows(questions, answers):
        """Bind actual answers to the server's questions, without promoting options."""
        if not isinstance(answers, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in answers.items()
        ):
            raise ValueError("逐项答复必须为问题 ID 与文字答案。")
        question_ids = {row["id"] for row in questions}
        if set(answers) - question_ids:
            raise ValueError("答复包含不属于当前作业的问题，请重新读取问题。")
        if sum(len(value) for value in answers.values()) > 64000:
            raise ValueError("逐项答复总长度不能超过 64000 字符。")
        rows = []
        for question in questions:
            answer = answers.get(question["id"], "").strip()
            if not answer and question.get("required", True):
                raise ValueError("请回答所有必填问题：" + question["question"])
            if answer:
                rows.append({"id": question["id"], "question": question["question"], "answer": answer})
        return rows

    def _submit_job(self, command):
        self.writable()
        project_id = command["project_id"]
        with self.lock.thread_lock:
            context = self.projects.tool_context(project_id, command.get("version_id"))
            scope = self._command_scope(command, context)
            project = dict(context.project)
            snapshot = {**copy.deepcopy(command), "project": project, "version": context.version,
                        "version_id": context.version_id or None, "program_ir": context.program_ir,
                        "change_scope": scope}
            generation_action = command.get("generation_action")
            if generation_action:
                if generation_action not in {"edit", "regenerate"}:
                    raise ValueError("无效的生成动作。")
                if context.version is None:
                    raise ValueError("修改或重新生成必须绑定已选择的程序版本。")
                if generation_action == "edit":
                    if not str(command.get("text") or "").strip():
                        raise ValueError("请描述希望修改的程序行为。")
                    snapshot["fresh_confirmed_generation"] = False
                else:
                    if (project.get("target_mode") != "ladder"
                            or context.version.get("target_mode") != "ladder"
                            or not project.get("confirmed_spec")):
                        raise ValueError("完整重新生成需要已确认规格和 Ladder 基线版本。")
                    if scope is not None or command.get("repair_mode") or command.get("format_repair"):
                        raise ValueError("完整重新生成不能同时指定局部修改或修复。")
                    snapshot["fresh_confirmed_generation"] = True
            if command["kind"] == "direct_generation":
                if project.get("target_mode") != "ladder" or context.program_ir is not None or scope is not None:
                    raise ValueError("Direct 一次生成用于新 Ladder 程序；已有工程编辑使用生成程序入口。")
                if not str(command.get("text") or "").strip() and not command.get("clarification_answers"):
                    raise ValueError("请输入原始需求或缺参答复。")
                parent_id = command.get("clarification_job_id")
                if parent_id:
                    parent = self.jobs._load(record_id(parent_id))
                    if (parent.get("kind") != "direct_generation" or parent.get("status") != "completed"
                            or parent.get("snapshot", {}).get("project_id") != project_id
                            or (parent.get("result") or {}).get("status") != "needs_input"):
                        raise ConflictError("续答必须绑定同一工程中等待补充信息的 Direct 作业。")
                    if parent.get("request_id") == command["request_id"]:
                        raise ConflictError("续答需要新的请求 ID。")
                    self._check_snapshot(parent["snapshot"])
                    previous = self.output(parent_id)
                    prior_text = parent["snapshot"].get("raw_requirement", parent["snapshot"].get("text", ""))
                    questions = (previous.get("generation") or {}).get("missing_info", [])
                    answer_text = command.get("text", "")
                    if "clarification_answers" in command:
                        rows = self._direct_answer_rows(questions, command["clarification_answers"])
                        snapshot["clarification_answers"] = {row["id"]: row["answer"] for row in rows}
                        snapshot["raw_requirement"] = (prior_text
                            + "\n\n逐项用户答复（question 仅用于对应问题，answer 才是用户回答）：\n"
                            + json.dumps(rows, ensure_ascii=False)
                            + ("\n用户补充说明：\n" + answer_text if answer_text.strip() else ""))
                        # The raw request retains question/answer correspondence. Only human
                        # text enters Core fact extraction, never model questions or options.
                        answer_text = "\n".join([*(row["answer"] for row in rows), answer_text]).strip()
                    else:
                        snapshot["raw_requirement"] = (prior_text + "\n\n待补充事实：\n"
                            + "\n".join(str(q.get("question", "")) for q in questions)
                            + "\n用户续答：\n" + answer_text)
                    snapshot["user_fact_text"] = (self._direct_user_fact_text(parent["snapshot"])
                        + "\n用户续答：\n" + answer_text)
                    snapshot["attachment_ids"] = list(dict.fromkeys([
                        *parent["snapshot"].get("attachment_ids", []), *command.get("attachment_ids", [])]))
                else:
                    snapshot["raw_requirement"] = command["text"]
                    snapshot["user_fact_text"] = command["text"]
            if context.version and context.version.get("target_mode") == "fbd":
                raw = self.projects.artifact(project_id, context.version_id, "gxw").read_bytes()
                snapshot["fbd_baseline"] = base64.b64encode(raw).decode("ascii")
                snapshot["fbd_program"] = context.version.get("program_name")
            # Resolve files and credentials at submission, never later from mutable UI state.
            images = self._attachments(project_id, snapshot.get("attachment_ids", []))
            repair_plan = command.get("repair_plan") if isinstance(command.get("repair_plan"), dict) else {}
            repair_target = repair_plan.get("target") if isinstance(repair_plan.get("target"), dict) else {}
            deterministic_generation_repair = (
                command["kind"] == "generation"
                and repair_plan.get("mode") == "field_patch"
                and repair_target.get("strategy") == "deterministic"
            )
            requires_model = (
                command["kind"] not in ("gx_read", "gx_inspect")
                and (command["kind"] != "review" or command.get("deep", True))
                and not deterministic_generation_repair
            )
            provider, model = self.model_factory() if requires_model else (None, {})
            snapshot["model"] = model
            snapshot["approval_consent"] = self.approval.read()
            if command["kind"] == "debug_plan":
                snapshot["saved_run"] = self.projects.simulator_run(project_id, context.version_id, command.get("run_id"))

        def worker(ctx):
            if command["kind"] in ("gx_read", "gx_inspect"):
                try:
                    return self._read_gx(ctx, snapshot)
                except ChangeScopeError as error:
                    ctx.emit("progress", {"message": str(error), "code": "change_scope_violation"})
                    raise
            from application.model_api import provider_scope
            from shared.i18n import language_context
            from model_runtime.provider import response_policy_scope
            from model_runtime.usage import usage_scope
            from application.model_progress import ModelJobContext, ModelProgressReporter
            ctx.checkpoint()
            model_context = ModelJobContext(ctx)
            model_progress = ModelProgressReporter(model_context)
            context_audit = ContextAudit(lambda report: ctx.emit("context_audit", report))
            with usage_scope(lambda kind, data: ctx.emit(kind, data)), language_context(snapshot["response_language"]), context_audit_scope(context_audit), provider_scope(
                    provider, model_name=model.get("model")), response_policy_scope(
                    enforce_language=False, on_progress=model_progress, on_preview=model_progress.preview):
                try:
                    result = self._run_job(model_context, snapshot, context, images, provider)
                except ChangeScopeError as error:
                    ctx.emit("progress", {"message": str(error), "code": "change_scope_violation"})
                    raise
                finally:
                    model_context.flush()
            return result
        return self.jobs.submit(command["kind"], snapshot, worker, request_id=command["request_id"])

    def _read_gx(self, ctx, snapshot):
        with self.lock.thread_lock:
            ctx.checkpoint()
            self._check_snapshot(snapshot)
            result = self.execution.submit_read("read_gx" if snapshot["kind"] == "gx_read" else "inspect_gx",
                {"project_id": snapshot["project_id"], "version_id": snapshot["version_id"]},
                progress=lambda *values: ctx.emit("progress", {"message": str(values[-1])})).result()
            output = public(result)
            if result.get("_candidate_ir"):
                candidate = self._with_candidate_diff(snapshot["project_id"], snapshot["version_id"],
                    {"_candidate_ir": result["_candidate_ir"], "_confirmed_spec": result.get("_confirmed_spec"),
                     "target_mode": "ladder", "change_scope": snapshot.get("change_scope")})
                proposal = self.proposals.create("accept_local", snapshot["project_id"],
                    candidate,
                    public_summary={"summary": "从 GX Works2 读取的程序", "diff": self._diff_summary(candidate["_preview_diff"])},
                    base_version_id=snapshot["version_id"], request_id=ctx.job_id)
                proposal = self._save_local_proposal(proposal)
                output["proposal_id"] = proposal["id"]
                output["version_id"] = proposal["result"]["version_id"]
            atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
            return {"status": result.get("status"), "proposal_id": output.get("proposal_id"), "version_id": output.get("version_id"), "passed": False}

    def _run_job(self, ctx, snapshot, context, images, provider):
        kind, project_id, text = snapshot["kind"], snapshot["project_id"], snapshot.get("text", "")
        project, version = snapshot["project"], snapshot.get("version")
        language = snapshot["response_language"]
        output = None
        from plc.change_scope import scope_instruction
        scoped_text = text + scope_instruction(snapshot.get("change_scope"))
        if kind == "analysis":
            from application.model_api import analyze_requirement_streaming
            from plc.specification.confirmed import build_review_draft
            ctx.emit("progress", {"message": "正在分析需求"})
            analysis_mode = snapshot.get("analysis_mode", "direct")
            analysis = analyze_requirement_streaming(text, confirmed_spec=project.get("confirmed_spec"),
                analysis_mode=analysis_mode,
                allow_repair=snapshot.get("allow_analysis_repair", True),
                conversation_history=project.get("messages", []), image_attachments=images,
                on_reasoning_chunk=lambda t: ctx.emit("reasoning", {"text": t}),
                on_content_chunk=lambda t: ctx.emit("content", {"text": t}), response_language=language,
                on_format_repair=lambda: ctx.emit("progress", {"message": "正在修正需求分析的回复格式"}))
            if not isinstance(analysis, dict):
                raise ValueError("需求分析未完成。")
            output = {"analysis_mode": analysis_mode, "analysis": analysis,
                      "spec_draft": build_review_draft(analysis, project.get("confirmed_spec")),
                      "spec_base_hash": public_spec_hash(project.get("confirmed_spec")), "base_version_id": snapshot.get("version_id")}
        elif kind in {"generation", "direct_generation"}:
            from application.generation import GenerationRequest, GenerationWorkflow, GenerationDependencies
            from plc.ir import ir_to_ladder
            out_dir = self.state_dir / "staging" / ctx.job_id
            if project["target_mode"] == "fbd" or (version or {}).get("target_mode") == "fbd":
                from application.fbd import generate_candidate
                fbd_payload = generate_candidate(out_dir, snapshot, images, ctx)
                metadata = fbd_payload["metadata"]
                metadata["artifacts"] = {k: v["path"] for k, v in fbd_payload["artifacts"].items()}
            else:
                program = snapshot.get("program_ir")
                repair_mode = bool(snapshot.get("repair_mode"))
                format_repair = bool(snapshot.get("format_repair"))
                repair_baseline = snapshot.get("repair_baseline") if repair_mode else None
                repair_plan = snapshot.get("repair_plan") if repair_mode else None
                fresh_confirmed_generation = bool(snapshot.get("fresh_confirmed_generation"))
                if repair_mode:
                    previous_json = copy.deepcopy(repair_baseline)
                elif format_repair or fresh_confirmed_generation:
                    previous_json = None
                else:
                    previous_json = ir_to_ladder(program) if program else None
                request = GenerationRequest(
                    user_input=snapshot.get("raw_requirement", scoped_text), effort=None, target_mode=project["target_mode"],
                    user_fact_text=snapshot.get("user_fact_text"),
                    previous_json=previous_json,
                    previous_ir=None if fresh_confirmed_generation else program,
                    confirmed_context=project.get("confirmed_spec"),
                    decision_receipt_id=project.get("confirmed_decision_receipt_id"),
                    conversation_history=[] if (repair_mode or format_repair) else project.get("messages", []),
                    task_type=snapshot.get("task_type"),
                    plc_model=project.get("plc_model", "FX3U"), program_name=(program or {}).get("program_name", "MAIN"),
                    revision=(program or {}).get("revision", 0) + 1,
                    requirement_text=snapshot.get("repair_requirement", text),
                    repair_mode=repair_mode, format_repair=format_repair,
                    allowed_rung_ids=snapshot.get("allowed_rung_ids"),
                    allowed_addresses=snapshot.get("allowed_addresses"), repair_plan=repair_plan,
                    source_handoff=(snapshot.get("repair_source_handoff") if (repair_mode or format_repair)
                                    else None if fresh_confirmed_generation else (version or {}).get("generation_handoff")),
                    image_attachments=images, model_name=snapshot.get("model", {}).get("model"),
                    response_language=language, construction_examples=snapshot.get("construction_examples"),
                    fresh_confirmed_generation=fresh_confirmed_generation,
                    direct_generation=kind == "direct_generation")
                metadata = GenerationWorkflow(request, out_dir, ctx.emit, GenerationDependencies(
                    provider=provider, check_cancelled=ctx.checkpoint, preserve_rejected_candidate=True
                )).run()
            ctx.checkpoint()
            output = {"generation": metadata}
            if snapshot.get("generation_action"):
                output["generation_action"] = snapshot["generation_action"]
            if metadata.get("status") == "needs_input":
                with self.lock.thread_lock:
                    self._check_snapshot(snapshot)
                output.update(status="needs_input", clarification_job_id=ctx.job_id,
                              missing_info=copy.deepcopy(metadata["missing_info"]))
                atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
                return {"status": "needs_input", "clarification_job_id": ctx.job_id}
            payload = {"project_id": project_id, "target_mode": metadata["target_mode"],
                       "plc_model": project.get("plc_model", "FX3U"), "_confirmed_spec": project.get("confirmed_spec"),
                       "_validation_profile": metadata.get("validation_profile", "strict"),
                       "_generation_handoff": copy.deepcopy(metadata.get("generation_handoff")),
                       "maintainability_review": copy.deepcopy(metadata.get("maintainability_review")),
                       "normalization": metadata.get("normalization"),
                       "_construction_binding": copy.deepcopy((metadata.get('first_pass_pipeline') or {}).get('construction_binding')),
                       "change_scope": snapshot.get("change_scope")}
            if metadata["target_mode"] == "ladder":
                payload["_candidate_ir"] = json.loads((out_dir / metadata["artifacts"]["ir"]).read_text(encoding="utf-8"))
            else:
                payload.update(staging_dir=str(out_dir), metadata=metadata, artifacts={k: {
                    "path": v, "sha256": hashlib.sha256((out_dir / v).read_bytes()).hexdigest()}
                    for k, v in metadata["artifacts"].items()})
            with self.lock.thread_lock:
                self._check_snapshot(snapshot)
                payload = self._with_candidate_diff(project_id, (version or {}).get("id"), payload)
                proposal = self.proposals.create("accept_local", project_id, payload,
                    public_summary={"summary": text[:500], "validation": metadata["validation"], "normalization": metadata.get("normalization"), "diff": self._diff_summary(payload["_preview_diff"])},
                    base_version_id=(version or {}).get("id"), request_id=ctx.job_id)
                ctx.checkpoint()
                proposal = self._save_local_proposal(proposal)
            output.update(proposal_id=proposal["id"], version_id=proposal["result"]["version_id"],
                          status='saved_behavior_draft' if proposal['result'].get('status') == 'saved_behavior_draft' else 'saved')
            ctx.emit("progress", {"stage": "version_saved", "version_id": output["version_id"],
                "message": "程序已生成并自动保存；可选 Review、仿真或 GX 验证。"})
        elif kind == "agent":
            from agent_runtime.agent import run_tool_agent
            result = run_tool_agent(text, context=context, runtime=self.projects.runtime, provider=provider, read_only=True,
                conversation_history=project.get("messages", []), response_language=language,
                on_progress=lambda m: ctx.emit("progress", {"message": m}),
                on_reasoning_chunk=lambda t: ctx.emit("reasoning", {"text": t}),
                on_content_chunk=lambda t: ctx.emit("content", {"text": t}))
            ctx.checkpoint()
            with self.lock.thread_lock:
                self._check_snapshot(snapshot)
                if result.pending_actions:
                    raise ValueError("工程问答不能提出程序修改或外部操作。")
                self.store.add_message(project_id, "assistant", result.content, kind="agent")
            output = {"content": result.content, "audit": result.audit, "proposal_ids": []}
        else:
            output = self._plan_or_review(ctx, snapshot, provider)
        atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
        return {key: output[key] for key in ("proposal_id", "proposal_ids", "version_id", "report_id", "plan_id", "status", "generation_action") if key in output}

    def _plan_or_review(self, ctx, snapshot, provider):
        # Pure workflow services are imported only when requested. Their signatures
        # are kept here, outside HTTP routes, for reuse by application clients.
        from application.review import InspectionWorkflow
        from application.planning import SimulatorTestPlanWorkflow, EvidenceDebugPlanWorkflow
        from plc.ir import ir_to_ladder
        project, version, program = snapshot["project"], snapshot.get("version"), snapshot.get("program_ir")
        if not version or not program:
            raise ValueError("当前版本没有可用于检查或测试的 PLC IR。")
        project_id, version_id = project["id"], version["id"]
        common = {"on_event": ctx.emit, "response_language": snapshot["response_language"],
                  "provider": provider, "model_name": snapshot.get("model", {}).get("model"),
                  "effort": None, "program_ir": program}
        def before_save():
            ctx.checkpoint()
            self._check_snapshot(snapshot)
        if snapshot["kind"] == "review":
            report = InspectionWorkflow(ctx.job_id, "program_review", snapshot.get("text", ""), ir_to_ladder(program),
                version_id, project.get("plc_model", "FX3U"), project_id=project_id,
                confirmed_spec=project.get("confirmed_spec"), deep=snapshot.get("deep", True), **common).run()
            with self.lock.thread_lock:
                before_save()
                report = self.store.create_report(project_id, report)
            return {"report_id": report["report_id"], "report": report}
        common.update(before_save=before_save, write_lock=self.lock.thread_lock)
        if snapshot["kind"] == "test_plan":
            plan = SimulatorTestPlanWorkflow(ctx.job_id, self.store, project_id, version_id, **common).run()
            plan_id = plan["binding"]["plan_id"]
        else:
            store = _SnapshotStore(self.store, snapshot)
            plan = EvidenceDebugPlanWorkflow(ctx.job_id, store, project_id, version_id, snapshot["run_id"],
                saved_run=snapshot["saved_run"], **common).run()
            plan_id = plan["plan_id"]
        return {"plan_id": plan_id, "plan": plan}

    def _pending_proposal(self, pending, request_id, *, base_version_id=None, consent=None, direct_request=False,
                          change_scope=None):
        if pending.get('type') in ('accept_fbd_candidate', 'accept_sfc_candidate', 'accept_st_candidate'):
            from application.fbd import unpack_candidate
            mode = pending['target_mode']
            candidate_key = '_' + mode + '_candidate'
            # Runtime artifacts remain private; materialize them only inside
            # managed state while the existing proposal service freezes them.
            with tempfile.TemporaryDirectory(dir=self.state_dir, prefix=mode + '-tool-') as directory:
                prepared = unpack_candidate(pending[candidate_key], directory)
                payload = {key: value for key, value in pending.items() if key != candidate_key}
                payload.update(prepared, type='accept_generated_program')
                return self._pending_proposal(payload, request_id, base_version_id=base_version_id,
                    consent=consent, direct_request=direct_request, change_scope=change_scope)
        kind = pending.get("type")
        if kind in ("accept_candidate_patch", "accept_generated_program"):
            action = "accept_local"
        elif kind == "import_current_program_to_gxworks2":
            action = "gx_import"
        elif kind == 'execute_native_engineering':
            action = 'native_engineering'
        else:
            raise ValueError("Unsupported pending engineering action")
        base_id = pending.get("base_version_id") or pending.get("version_id") or base_version_id
        if change_scope is not None and base_id != base_version_id:
            raise ConflictError("局部修改候选必须使用已选择的基线版本。")
        pending = {**pending, "change_scope": change_scope}
        payload = self._with_candidate_diff(pending["project_id"], base_id, pending) if action == "accept_local" else dict(pending)
        proposal = self.proposals.create(action, pending["project_id"], payload,
            public_summary={"summary": "Agent 提出的工程操作", "diff": self._diff_summary(payload["_preview_diff"]) if "_preview_diff" in payload else pending.get("diff"), "validation": pending.get("validation"), "normalization": pending.get("normalization")},
            base_version_id=base_id, request_id=request_id)
        if action == "accept_local":
            # Direct operator and local MCP requests save without a second prompt.
            # Connected HTTP agents are delegated explicitly by auto/full;
            # default ask retains their existing confirmation boundary.
            current = self.approval.read()
            if direct_request or (current["mode"] in {"auto", "full"} and (consent is None or consent == current)):
                return self._save_local_proposal(proposal)
            return proposal
        return self._apply_execution_policy(proposal, consent)

    def mcp_activity(self, project_id):
        """Report successful client calls seen by this service instance only.

        A launcher check only lists tools and never advances this state. Keep
        summaries in memory: old receipts must not imply a restarted client has
        loaded this service. Do not retain arguments, prompts or credentials.
        """
        return dict(self._mcp_activity.get(project_id, {
            "client_observed": False, "last_tool": None, "last_call_at": None,
            "generation_context_observed": False, "candidate_proposal_id": None,
        }))

    def _observe_mcp_call(self, command, response):
        if response.get("is_error"):
            return
        project_id, name = command["project_id"], command["name"]
        current = self.mcp_activity(project_id)
        current.update(client_observed=True, last_tool=name,
                       last_call_at=datetime.now(timezone.utc).isoformat())
        if name == "get_generation_context":
            current["generation_context_observed"] = True
        if name in {"create_program_candidate", "patch_program", "create_fbd_candidate", "create_sfc_candidate", "create_st_candidate"} and response.get("proposal_id"):
            current["candidate_proposal_id"] = response["proposal_id"]
        self._mcp_activity[project_id] = current

    def agent_call(self, command, *, observe_client=True, direct_request=False):
        self.writable()
        if command.get('name') in {'get_operation_status', 'get_native_engineering_session', 'pause_native_engineering_session'}:
            # Execution workers may hold the workspace writer lock while GX
            # runs. Read atomic public receipts without waiting for that write
            # transaction or replaying a cached observation from an old query.
            from agent_runtime.plc_tools import build_tool_context
            context = build_tool_context(self.projects.raw_project(command['project_id']),
                                         operation_status=self.operation_status,
                                         native_session_status=self.native_session_status,
                                         native_session_pause=self.native_session_pause)
            result = self.projects.runtime.invoke(ToolCall(command['call_id'], command['name'],
                command.get('arguments', {})), context)
            envelope = public_tool_result_data(result)
            return {'data': envelope, 'content': json.dumps(envelope, ensure_ascii=False),
                    'is_error': result.is_error, 'call_id': command['call_id'], 'name': command['name']}
        request_key = hashlib.sha256((command["project_id"] + ":" + command["call_id"]).encode()).hexdigest()
        path = self.state_dir / "agent_calls" / (request_key + ".json")
        # This is an application invocation option, never an Agent argument.
        digest = canonical_hash({"command": command, "direct_request": True} if direct_request else command)
        with self.lock.thread_lock:
            if path.exists():
                saved = read_json(path)
                if saved["input_hash"] != digest:
                    raise ConflictError("Agent call ID was already used with other arguments")
                if observe_client:
                    self._observe_mcp_call(command, saved["response"])
                return saved["response"]
            context = self.projects.tool_context(command["project_id"], command.get("version_id"))
            from dataclasses import replace
            context = replace(context, operation_status=self.operation_status,
                              native_session_status=self.native_session_status,
                              native_session_pause=self.native_session_pause)
            scope = self._command_scope(command, context)
            result = self.projects.runtime.invoke(ToolCall(command["call_id"], command["name"], command.get("arguments", {})), context)
            envelope = public_tool_result_data(result)
            response = {"data": envelope, "content": json.dumps(envelope, ensure_ascii=False), "is_error": result.is_error,
                        "call_id": command["call_id"], "name": command["name"]}
            pending = (result.data.get("data") or {}).get("pending_action")
            if not result.is_error and result.data.get("status") == "confirmation_required" and pending:
                proposal = self._pending_proposal(pending, request_key, base_version_id=context.version_id or None,
                                                  change_scope=scope, direct_request=direct_request)
                response["proposal_id"] = proposal["id"]
                if (proposal.get("result") or {}).get("version_id"):
                    response["version_id"] = proposal["result"]["version_id"]
                if proposal.get("execution_job_id"):
                    response["execution_job_id"] = proposal["execution_job_id"]
            atomic_json(path, {"input_hash": digest, "response": response})
            if observe_client:
                self._observe_mcp_call(command, response)
            return response

    def native_session_status(self, project_id, session_id=None):
        project_id = record_id(project_id)
        self.projects.raw_project(project_id)
        if self.execution is None:
            if session_id is not None:
                raise KeyError('No native session is owned by this application process')
            return {'mode': 'background', 'status': 'not_started', 'project_id': project_id}
        from application.live_engineering import live_engineering_summary
        return live_engineering_summary(self.execution.live_session_status(project_id, session_id))

    def native_session_pause(self, project_id, session_id):
        project_id = record_id(project_id)
        self.projects.raw_project(project_id)
        if self.execution is None:
            raise KeyError('No native session is owned by this application process')
        return self.execution.request_live_session_pause(project_id, session_id)

    def operation_status(self, project_id, *, proposal_id=None, execution_job_id=None):
        """Read durable public receipts for one selected project, without execution.

        Proposal writes use atomic JSON replacement. Reading their public
        snapshot here deliberately avoids the workspace transaction lock held
        by a running external operation. Frozen inputs never leave this method.
        """
        project_id = record_id(project_id)
        self.projects.raw_project(project_id)
        if (proposal_id is None) == (execution_job_id is None):
            raise ValueError('Provide exactly one proposal_id or execution_job_id')
        from application.jobs import JobManager
        from application.proposals import ProposalService
        from application.workspace import bind_workspace_state
        state_dir = bind_workspace_state(self.state_dir, self.store.base_dir, read_only=True)

        def read_record(group, identifier):
            directory = contained(state_dir / group, state_dir)
            path = contained(directory / (identifier + '.json'), directory)
            if not path.is_file():
                raise KeyError(identifier)
            record = read_json(path)
            if record.get('id') != identifier:
                raise ValueError('Invalid operation record')
            return record

        def proposal_record(identifier):
            record = read_record('proposals', record_id(identifier))
            if record.get('project_id') != project_id:
                raise KeyError('Operation not found in the selected project')
            return public(ProposalService._public(record))

        proposal, job = None, None
        if proposal_id is not None:
            proposal = proposal_record(proposal_id)
        else:
            execution_job_id = record_id(execution_job_id)
            record = read_record('jobs', execution_job_id)
            snapshot = record.get('snapshot') or {}
            if snapshot.get('project_id') != project_id:
                raise KeyError('Operation not found in the selected project')
            job = public(JobManager._public(record))
            proposal_id = snapshot.get('proposal_id')
            if proposal_id is not None:
                proposal = proposal_record(proposal_id)
                if snapshot.get('version_id') != proposal.get('base_version_id'):
                    raise ValueError('Execution job and proposal use different saved baselines')
                observed = job.get('result')
                if (isinstance(observed, dict) and observed.get('proposal_id') is not None
                        and observed['proposal_id'] != proposal_id):
                    raise ValueError('Execution job result belongs to a different proposal')

        result = (proposal or {}).get('result')
        if result is None and job is not None:
            result = job.get('result')
            if isinstance(result, dict) and proposal is not None:
                result = result.get('result')
        if isinstance(result, dict) and result.get('project_id') not in (None, project_id):
            raise ValueError('Execution result belongs to a different project')
        response = {'project_id': project_id, 'status': (job or proposal)['status'],
                    'execution_result': copy.deepcopy(result)}
        if proposal is not None:
            response.update(proposal_id=proposal['id'], proposal=proposal,
                            base_version_id=proposal.get('base_version_id'))
        if job is not None:
            response.update(execution_job_id=job['id'], job=job)
            response.setdefault('base_version_id', job.get('version_id'))
        if isinstance(result, dict) and result.get('version_id') is not None:
            version_id = record_id(result['version_id'])
            self.projects.raw_version(project_id, version_id)
            response['version_id'] = version_id
        return response

    @staticmethod
    def _command_scope(command, context):
        from plc.change_scope import validate_scope_baseline
        scope = command.get("change_scope")
        if scope is not None and command.get("kind") not in (None, "generation", "gx_read"):
            raise ValueError("修改范围仅适用于生成程序和读取程序候选。")
        mode = (context.version or {}).get("target_mode") or context.project.get("target_mode", "ladder")
        if command.get("kind") == "generation" and context.project.get("target_mode") != "ladder":
            mode = context.project["target_mode"]
        return validate_scope_baseline(scope, context.program_ir, target_mode=mode)

    def execution_proposal(self, command):
        self.writable()
        project_id, version_id = command["project_id"], command["version_id"]
        with self.lock.thread_lock:
            version = self.projects.raw_version(project_id, version_id)
            manual_backup = command.get("manual_backup_acknowledged", False)
            if type(manual_backup) is not bool or (manual_backup and (
                command["action"] != "gx_import" or version.get("target_mode") == "fbd"
                or not all((version.get("artifacts") or {}).get(key) for key in ("program_csv", "comment_csv"))
            )):
                raise ValueError("Manual backup acknowledgement is only supported for CSV GX imports")
            if command["action"] == "gx_import":
                from application.execution import ExecutionUnavailableError, read_gx_environment
                from gxworks2.project_profiles import can_prepare_program_project

                environment = read_gx_environment()
                auto_prepare = (version.get("target_mode") != "fbd"
                                and can_prepare_program_project(version.get("plc_model") or self.store.get_project(project_id).get("plc_model"))
                                and environment.get("gx_works2_installed") is True)
                if not environment.get("gx_works2_running") and not auto_prepare:
                    raise ExecutionUnavailableError(
                        environment.get("message")
                        or "GX Works2 未运行，请先启动 GX Works2 后再发送。"
                    )
                if version.get("target_mode") != "fbd" and environment.get("project_open") is False and not auto_prepare:
                    raise ExecutionUnavailableError(
                        "GX Works2 已运行，但尚未新建或打开目标工程。"
                    )
            payload = {"project_id": project_id, "version_id": version_id}
            if command['action'] == 'native_engineering':
                from application.native_engineering import normalize_native_plan, validate_native_baseline
                from application.execution import native_saved_cpu
                if version.get('target_mode') not in {'fbd', 'st'} or command.get('plan_id') is not None:
                    raise ValueError('Native engineering requires a saved structured FBD or ST version and an explicit batch plan')
                cpu = native_saved_cpu(version, self.store.get_project(project_id))
                if (command.get('plan') or {}).get('mode') == 'live':
                    from application.live_engineering import normalize_live_plan
                    payload['plan'] = normalize_live_plan(command['plan'], plc_model=cpu)
                else:
                    payload['plan'] = normalize_native_plan(command.get('plan'), plc_model=cpu)
                validate_native_baseline(self.projects.artifact(project_id, version_id, 'gxw').read_bytes(),
                                         plc_model=cpu, live=payload['plan'].get('mode') == 'live')
            elif command.get('plan') is not None:
                raise ValueError('An explicit batch plan is only supported for native engineering')
            if manual_backup:
                payload["manual_backup_acknowledged"] = True
            plan_id = command.get("plan_id")
            if command["action"] in ("simulation", "debug"):
                record_id(plan_id)
                plan = self.projects.plan(project_id, version_id, plan_id, kind=command["action"])
                payload["plan"] = plan
            # A replay returns the original proposal; a mode escalation must not execute it.
            prior = next((p for p in self.proposals.list(project_id)
                          if self.proposals._load(p["id"]).get("request_id") == command["request_id"]), None)
            proposal = self.proposals.create(command["action"], project_id, payload,
                public_summary={"summary": {"gx_import": "将指定版本导入 GX Works2", "simulation": "导入指定版本并运行指定仿真方案", "debug": "执行指定调试方案",
                                           'native_engineering': '在独立 GX Works2 工程副本中执行已冻结的可见工程方案'}[command["action"]],
                                "version_id": version_id, "plan_id": plan_id},
                base_version_id=version_id, request_id=command["request_id"])
            return proposal if prior else self._apply_execution_policy(proposal)

    def decide(self, proposal_id, decision, *, policy=None):
        self.writable()
        proposal = self.proposals.get(proposal_id)
        if decision == "reject":
            return {"proposal": self.proposals.reject(proposal_id)}
        if proposal["action"] == "accept_local":
            return {"proposal": self.proposals.accept(proposal_id)}
        def worker(ctx):
            # A cancel request while waiting for the engineering lock is still
            # before any external effect and must be honored after acquiring it.
            with self.lock.thread_lock:
                ctx.checkpoint()
                if policy is not None:
                    from application.approval import allows
                    current = self.approval.read()
                    payload = self.proposals.read_private(proposal_id)
                    if current != policy or not allows(current["mode"], proposal["action"], payload):
                        raise PermissionError("Approval settings changed before execution; manual review required")
                def execute(payload, approved_id):
                    native = self.execution.submit_approved({"gx_import": "import_gx", "simulation": "simulate", "debug": "debug",
                        'native_engineering': 'native_engineering'}[proposal['action']],
                        payload, approval_id=approved_id,
                        progress=lambda *m: ctx.emit("progress", {"message": str(m[-1])})).result()
                    if proposal['action'] == 'native_engineering':
                        return self._complete_native_engineering(payload, approved_id, native)
                    return native
                result = self.proposals.accept(proposal_id, approved_by="policy" if policy else "user",
                    approval_mode=policy["mode"] if policy else None, executor=execute)
            return {"proposal_id": proposal_id, "status": result["status"], "result": result.get("result")}
        return {"job": self.jobs.submit("execution", {"project_id": proposal["project_id"], "version_id": proposal["base_version_id"],
                    "proposal_id": proposal_id, "approval_policy": policy}, worker,
                    request_id=("policy_" + str(policy["revision"]) + "_" if policy else "approve_") + proposal_id)}

    def _complete_native_engineering(self, payload, approved_id, result):
        if (payload.get('plan') or {}).get('mode') == 'live':
            return self._complete_live_engineering(payload, approved_id, result)
        from application.native_engineering import normalize_native_plan, verified_native_data, native_engineering_summary
        receipt = result.get('_native_result') if isinstance(result, dict) else None
        if not isinstance(receipt, dict):
            summary = native_engineering_summary(result)
            summary.update(status='interrupted', passed=False,
                message='原生工程会话未完成，未创建活动版本。')
            return summary
        project_id, base_id = payload['project_id'], payload['version_id']
        version = self.projects.raw_version(project_id, base_id)
        from application.execution import native_saved_cpu
        cpu = native_saved_cpu(version, self.store.get_project(project_id))
        plan = normalize_native_plan(payload.get('plan'), plc_model=cpu)
        try:
            raw = verified_native_data(receipt, plan)
        except ValueError:
            summary = native_engineering_summary(receipt)
            summary.update(status='interrupted', passed=False,
                message='请求的原生验证层未完成，未创建活动版本。')
            return summary
        summary = native_engineering_summary(receipt)
        # Artifact projection is pure Core readback. Do not open an offline
        # Workspace or compile a second copy to replace this GUI evidence.
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix='native-result-') as directory:
            if version['target_mode'] == 'st':
                from application.native_st import prepare_candidate
            else:
                from application.fbd import prepare_candidate
            candidate = prepare_candidate(directory, imported=raw, plc_model=cpu,
                program_name=version.get('program_name'), summary='可见原生工程会话')
            validation = candidate['metadata']['validation']
            validation.update(native_save='passed', native_source_save_required=True,
                gx_compile=summary['gx_compile_status'],
                messages=['已核对同一原生会话保存的声明、实现、图形和配置；运行对应关系仍未验证。'])
            candidate['metadata']['generation_metadata'] = _native_generation_metadata(cpu, receipt, summary)
            saved = self.proposals.complete_native_candidate(approved_id, candidate)
        return {**summary, **saved, 'status': 'completed', 'base_version_id': base_id,
                'project_id': project_id}

    def _complete_live_engineering(self, payload, approved_id, result):
        from application.live_engineering import live_engineering_summary, verified_live_data
        receipt = result.get('_live_result') if isinstance(result, dict) else None
        if not isinstance(receipt, dict):
            failure = result if isinstance(result, dict) else {}
            return {'status': failure.get('status') if failure.get('status') in ('error', 'unavailable') else 'interrupted',
                    'passed': False, 'mode': 'live',
                    'message': failure.get('message') or '持续原生工程执行没有返回可确认的会话回执。'}
        summary = live_engineering_summary(receipt)
        successful = receipt.get('passed') is True and receipt.get('status') in ('completed', 'paused', 'ready', 'ended')
        if not successful:
            return {**summary, 'status': 'interrupted', 'passed': False}
        summary.update(session_status=receipt['status'], status='completed', passed=True)
        if not isinstance(receipt.get('data'), bytes):
            return summary
        project_id, base_id = payload['project_id'], payload['version_id']
        version = self.projects.raw_version(project_id, base_id)
        from application.execution import native_saved_cpu
        cpu = native_saved_cpu(version, self.store.get_project(project_id))
        baseline = self.projects.artifact(project_id, base_id, 'gxw').read_bytes()
        raw = verified_live_data(receipt, baseline)
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix='live-native-result-') as directory:
            if version['target_mode'] == 'st':
                from application.native_st import prepare_candidate
            else:
                from application.fbd import prepare_candidate
            candidate = prepare_candidate(directory, imported=raw, plc_model=cpu,
                program_name=version.get('program_name'), summary='持续原生工程阶段保存')
            current_compile = receipt.get('compile') or {}
            compile_status = (current_compile.get('status') if current_compile.get('revision') == receipt['data_revision']
                              else 'unverified')
            candidate['metadata']['validation'].update(native_save='passed',
                native_source_save_required=True, gx_compile=compile_status,
                messages=['已核对本阶段保存与实际原生页面；编译和地址结果分别绑定其修订。'])
            candidate['metadata']['generation_metadata'] = _native_generation_metadata(cpu, receipt, summary)
            saved = self.proposals.complete_native_candidate(approved_id, candidate)
        self.execution.rebase_live_session(project_id, receipt['session_id'], base_id, saved['version_id'])
        summary.update(version_revision=receipt['data_revision'],
                       version_matches_active_source=receipt['data_revision'] == receipt['revision'])
        return {**summary, **saved, 'status': 'completed', 'project_id': project_id,
                'base_version_id': base_id, 'data_revision': receipt['data_revision']}

    def _candidate_diff(self, project_id, base_version_id, payload):
        """Review the proposal's bound version, never the UI's active selection."""
        if "_candidate_ir" in payload:
            from plc.core import PLCCore
            before = self.projects.program(project_id, base_version_id) if base_version_id else None
            expected = payload.get("base_ir_sha256")
            if expected and canonical_hash(before) != expected:
                raise ConflictError("The proposal's base program changed")
            result = dict(PLCCore().diff_programs(before, payload["_candidate_ir"]))
        elif payload.get("target_mode") == "fbd":
            from application.fbd import graph_diff, staged_bytes
            from gxw.object_model import read_project_context
            before = None
            artifacts = staged_bytes(payload, self.state_dir)
            after = json.loads(artifacts['fbd'])
            if base_version_id and self.projects.raw_version(project_id, base_version_id)["target_mode"] == "fbd":
                base = self.projects._fbd_context(project_id, base_version_id)
                program = after['program']
                if program not in base.programs:
                    # Native save verifies Body/Program role normalization and
                    # records that mapping; never guess it from an FB name.
                    report = json.loads(artifacts['write_report'])
                    matches = [row['object'] for row in report.get('objects', [])
                               if row.get('saved_object') == program and row.get('object') in base.programs]
                    if len(matches) == 1:
                        program = matches[0]
                if program in base.programs:
                    before = read_project_context(base.raw, program).object_model()
            result = graph_diff(before, after)
            report = json.loads(artifacts['write_report'])
            if report.get('operation') == 'native_compilation_save':
                result['compilation_save'] = {'public_check':report['cold_validation']['public_check'],
                    'saved_code':report['cold_validation']['inspection']['saved_code'],
                    'gui_configuration':report.get('validation', {}).get('gui_configuration', 'not_verified'), 'execution':'not_run'}
                result['has_changes'] = True
                result['unified_diff'] += '\n+ 已保存原生编译结果；冷重开代码与重新编译一致，运行尚未验证。\n'
                if report.get('validation', {}).get('gui_configuration') != 'matched':
                    result['unified_diff'] += '+ GUI 自动分配配置尚未完整验证，不能视为可见原生会话通过。\n'
            if report.get('operation') == 'native_library_import':
                definition = report['library_import']
                result['library_changes'] = {'added': [definition['name']],
                    'definitions': [row['name'] for row in definition['definitions']],
                    'structures': [row['name'] for row in definition['structures']],
                    'global_tables': [row['name'] for row in definition['global_tables']]}
                result['has_changes'] = True
            if report.get('operation') == 'native_definition_authoring':
                from difflib import unified_diff
                previous, current = report['definition_before'], report['definition_after']
                result['definition_changes'] = {'before': previous, 'after': current}
                text = lambda value: json.dumps(value, ensure_ascii=False, indent=2).splitlines(True)
                result['unified_diff'] += ''.join(unified_diff(text(previous), text(current),
                    fromfile='base/definition', tofile='candidate/definition'))
                result['has_changes'] = result['has_changes'] or previous != current
        elif payload.get("target_mode") == "sfc":
            from application.sfc import read_snapshot, source_diff
            from application.fbd import staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            after = json.loads(artifacts['sfc'])
            before = None
            if base_version_id:
                base = self.projects.raw_version(project_id, base_version_id)
                if base.get('target_mode') == 'sfc':
                    raw = self.projects.artifact(project_id, base_version_id, 'gxw').read_bytes()
                    report = json.loads(artifacts['write_report'])
                    originals = [row['object'] for row in report.get('objects', [])
                                 if row.get('saved_object', row.get('object')) == after['program']]
                    if len(originals) > 1:
                        raise ValueError('SFC 候选的原始源码名称不唯一。')
                    before = read_snapshot(raw, originals[0] if originals else after['program'])['model']
            result = source_diff(before, after)
        elif payload.get("target_mode") == "st":
            from difflib import unified_diff
            before = ""
            if base_version_id:
                base = self.projects.raw_version(project_id, base_version_id)
                artifacts = base.get('artifacts') or {}
                binding = ((payload.get('metadata') or {}).get('validation') or {}).get('source_binding')
                if (isinstance(binding, dict) and binding.get('kind') == 'program'
                        and binding.get('language') == 'st' and 'gxw' in artifacts):
                    from gxw.fb_authoring import st_program_snapshot
                    saved = self.projects.artifact(project_id, base_version_id, 'gxw').read_bytes()
                    same_owner = [row for row in st_program_snapshot(saved)['programs']
                                  if row['name'].casefold() == binding['owner'].casefold()]
                    if len(same_owner) > 1:
                        raise ValueError('ST 候选在基础工程中的 Program 归属不唯一。')
                    before = same_owner[0]['st_text'] if same_owner else ''
                else:
                    artifact_id = next((key for key in ('st', 'st_from_ir') if key in artifacts), None)
                    if artifact_id:
                        before = self.projects.artifact(project_id, base_version_id, artifact_id).read_text(encoding="utf-8")
            root = contained(Path(payload["staging_dir"]), self.state_dir)
            entry = payload["artifacts"]["st"]
            data = contained(root / entry["path"], root).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ConflictError("Staged candidate changed")
            after = data.decode("utf-8")
            lines = unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"{base_version_id or 'empty'}/program.st", tofile="candidate/program.st")
            result = {"kind": "st", "has_changes": before != after, "before": before, "after": after,
                "unified_diff": "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)}
        else:
            raise ValueError("Candidate has no reviewable program")
        result["base_version_id"] = base_version_id
        return result

    @staticmethod
    def _diff_summary(diff):
        if diff["kind"] in ("fbd", "sfc"):
            return {key: value for key, value in diff.items() if key != "unified_diff"}
        if diff["kind"] == "st":
            return {"kind": "st", "modified": ["program.st"] if diff["has_changes"] else []}
        summary = {key: diff[key] for key in ("kind", "added", "deleted", "modified", "device_comments_changed",
                    "before_network_count", "after_network_count")}
        summary["changes"] = [{key: change[key] for key in ("marker", "network", "comment", "instruction_count")}
                              for change in diff["changes"]]
        return summary

    def _with_candidate_diff(self, project_id, base_version_id, payload):
        payload = copy.deepcopy(payload)
        # Freeze review data inside the hash-bound private payload. Reopening an
        # accepted or stale proposal therefore still displays its original base.
        payload["_preview_diff"] = self._candidate_diff(project_id, base_version_id, payload)
        return payload

    def proposal_preview(self, proposal_id, *, theme=None):
        record = self.proposals.get(proposal_id)
        payload = self.proposals.read_private(proposal_id)
        if payload.get("target_mode") == "fbd":
            from application.fbd import editor_snapshot, staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            model = json.loads(artifacts['fbd'])
            editor = public(editor_snapshot(artifacts['gxw'], model['program']))
            # SVG is a verified engineering artifact, not metadata containing
            # host paths. Use the same bytes as the proposal's diagram.
            editor['source_svg'] = artifacts['svg'].decode('utf-8')
            return {"target_mode": "fbd", "program": public(json.loads(artifacts["fbd"])),
                    "svg": artifacts["svg"].decode("utf-8"), "diff": public(payload["_preview_diff"]),
                    'editor': editor}
        if "_candidate_ir" in payload:
            # read_private verified the frozen payload hash. Re-render each read
            # instead of returning a stale/corrupt cache. No project writes occur.
            return {**self._render_ladder_preview(
                        payload["_candidate_ir"], theme=theme,
                        confirmed_spec=payload.get("_confirmed_spec"),
                        validation_profile=payload.get("_validation_profile", "strict")),
                    "diff": public(payload.get("_preview_diff") or self._candidate_diff(record["project_id"], record["base_version_id"], payload))}
        if payload.get("target_mode") == "sfc":
            from application.fbd import staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            return {'target_mode': 'sfc', 'program': public(json.loads(artifacts['sfc'])),
                    'validation': public(payload['metadata']['validation']),
                    'diff': public(payload['_preview_diff'])}
        if payload.get("target_mode") == "st":
            entry = payload["artifacts"]["st"]
            path = contained(Path(payload["staging_dir"]) / entry["path"], self.state_dir)
            return {"target_mode": "st", "st": path.read_text(encoding="utf-8"),
                    "diff": public(payload.get("_preview_diff") or self._candidate_diff(record["project_id"], record["base_version_id"], payload))}
        version_id = record["base_version_id"]
        if not version_id or payload.get("version_id", version_id) != version_id:
            raise ConflictError("Execution proposal has no consistent bound version")
        with self.lock.thread_lock:
            project_id = record["project_id"]
            version = self.projects.version(project_id, version_id)
            artifacts = {item["id"] for item in version["artifacts"]}
            mode = version["target_mode"]
            program = self.projects.program(project_id, version_id) if mode in ("ladder", "fbd") else None
            if mode in ("ladder", "fbd") and program is None:
                raise ValueError("The execution proposal's program is unavailable")
            svg = self.projects.svg_preview(project_id, version_id, theme=theme) if "svg" in artifacts else None
            st_id = "st" if mode == "st" else "st_from_ir" if "st_from_ir" in artifacts else "st"
            st = self.projects.artifact(project_id, version_id, st_id).read_text(encoding="utf-8") if st_id in artifacts else None
            if mode == "st" and st is None:
                raise ValueError("The execution proposal's ST artifact is unavailable")
            return {"action": record["action"], "version_id": version_id, "version": version, "target_mode": mode,
                    "program": public(program), "svg": svg, "st": st, "plan": public(payload.get("plan"))}


def sfc_requirement(steps):
    from plc.sfc import linear_requirement
    return linear_requirement(steps)


def public_spec_hash(spec):
    from plc.ir import canonical_sha256
    return canonical_sha256(spec) if spec is not None else None


class _SnapshotStore:
    """Frozen read side for multi-stage planning; writes use the owned store."""
    def __init__(self, store, snapshot):
        self._store, self._snapshot = store, copy.deepcopy(snapshot)

    def __getattr__(self, name):
        return getattr(self._store, name)

    def get_project(self, project_id):
        if project_id != self._snapshot["project_id"]:
            raise ValueError("Project is outside the job snapshot")
        return copy.deepcopy(self._snapshot["project"])

    def get_version(self, project_id, version_id):
        self.get_project(project_id)
        if version_id != self._snapshot["version_id"]:
            raise ValueError("Version is outside the job snapshot")
        return copy.deepcopy(self._snapshot["version"])

    def load_program_ir(self, project_id, version_id, **kwargs):
        self.get_version(project_id, version_id)
        return copy.deepcopy(self._snapshot["program_ir"])

    def load_simulator_run(self, project_id, version_id, run_id):
        self.get_version(project_id, version_id)
        if run_id != self._snapshot["run_id"]:
            raise ValueError("Run is outside the job snapshot")
        return copy.deepcopy(self._snapshot["saved_run"])
