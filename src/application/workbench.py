"""Application commands shared by the HTTP operator and connected MCP agents."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from application.projects import ProjectService, contained, public, record_id
from application.settings import SettingsService
from application.workspace import WorkspaceWriterLock, ConflictError, atomic_json, canonical_hash, read_json
from tool_messages import ToolCall
from tool_runtime import public_tool_result_data
from plc_change_scope import ChangeScopeError
from prompt_context_policy import ContextAudit, context_policy_scope, resolve_context_policy


class WorkbenchService:
    def __init__(self, workspace, state_dir, *, read_only=False, settings=None, model_factory=None):
        self.projects = ProjectService(workspace)
        self.store = self.projects.store
        self.state_dir = Path(state_dir).resolve()
        self.read_only = read_only
        self.settings = settings or SettingsService()
        self.model_factory = model_factory or self.settings.model_snapshot
        self.lock = None
        self.jobs = None
        self.proposals = None
        self.execution = None
        self._mcp_activity = {}
        from application.approval import ApprovalPolicy
        self.approval = ApprovalPolicy(self.state_dir)
        from application.fbd import FBDService
        self.fbd = FBDService(self)

    def start(self):
        if not self.read_only:
            from application.jobs import JobManager
            from application.proposals import ProposalService
            from application.execution import GXExecutionCoordinator
            self.lock = WorkspaceWriterLock(self.store.base_dir).acquire()
            try:
                self.jobs = JobManager(self.state_dir, self.lock)
                self.proposals = ProposalService(self.store, self.state_dir, self.lock)
                self.execution = GXExecutionCoordinator(self.store)
            except Exception:
                self.close()
                raise

    def close(self):
        # Keep ownership until outstanding workers reach completion/checkpoints.
        if getattr(self, "hardware", None):
            self.hardware.close()
        if self.jobs:
            self.jobs.shutdown(wait=True)
        if self.execution:
            self.execution.close(wait=True)
        if self.lock:
            self.lock.release()

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
        with self.lock.thread_lock:
            project = self.store.create_project(**values)
            return self.projects.project(project["id"])

    def update_project(self, project_id, **values):
        self.writable()
        with self.lock.thread_lock:
            self.projects.raw_project(project_id)
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
        from confirmed_spec import canonicalize_confirmed_spec, validate_spec_draft
        from plc_ir import canonical_sha256
        self.writable()
        with self.lock.thread_lock:
            project = self.projects.raw_project(project_id)
            current = project.get("confirmed_spec")
            if (canonical_sha256(current) if current is not None else None) != expected_hash:
                raise ConflictError("确认规格已变化，请重新加载。")
            issues = validate_spec_draft(spec, project.get("plc_model"))
            if issues.get("errors"):
                return {"valid": False, "issues": public(issues)}
            normalized = canonicalize_confirmed_spec(spec)
            issues = validate_spec_draft(normalized, project.get("plc_model"))
            if issues.get("errors"):
                return {"valid": False, "issues": public(issues)}
            self.store.set_confirmed_spec(project_id, normalized)
            persisted = self.projects.raw_project(project_id)["confirmed_spec"]
            return {"valid": True, "spec": public(persisted), "hash": canonical_sha256(persisted)}

    def upload_attachment(self, project_id, filename, data_base64):
        from session_store import detect_image_media_type
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
        from model_provider import ImageAttachment
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
            from confirmed_spec import restore_review_choices
            if isinstance(output.get("spec_draft"), dict):
                output["spec_draft"] = restore_review_choices(output["spec_draft"], output["analysis"])
        return public(output)

    def _render_ladder_preview(
        self, program, *, theme=None, confirmed_spec=None, validation_profile="strict"
    ):
        """Deterministic, in-memory view. Never trust an old rendered-file cache."""
        from plc_ir import ir_to_ladder, validate_plc_ir
        from plc_st_renderer import render_plc_ir_to_st
        from draw import AdvancedSVGLadder

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
        from plc_ir import canonical_sha256

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
        from plc_ir import canonical_sha256

        if not self.jobs:
            raise KeyError("Generation jobs are unavailable")
        job = self.jobs.get(job_id)
        if job["kind"] != "generation" or job["status"] != "completed":
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
            if (record.get("kind") != "generation" or record.get("status") != "failed"
                    or record.get("error_code") != "generation_validation_failed"):
                raise ConflictError("Only a failed structural generation can be repaired")
            snapshot = copy.deepcopy(record.get("snapshot") or {})
            self._check_snapshot(snapshot)
            project_id = snapshot["project_id"]
            version_id = snapshot.get("version_id")
            root = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir / "staging")
            candidate_path = contained(root / "repair_candidate.json", root)
            if not candidate_path.is_file():
                raise ConflictError("The rejected candidate is no longer available for repair")
            candidate_text = candidate_path.read_text(encoding="utf-8")
            if not candidate_text.strip() or len(candidate_text) > 512000:
                raise ConflictError("The rejected candidate is too large or empty")
            details = record.get("error_details") or {}
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
            try:
                parsed_candidate = json.loads(candidate_text)
            except (TypeError, ValueError):
                parsed_candidate = None
            from application.generation_repair import candidate_base
            repair_base = candidate_base(parsed_candidate)
            local_repair = repair_base is not None
            allowed_rung_ids = set()
            allowed_addresses = set()
            if local_repair:
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
                from contract_repair import patch_device_addresses
                allowed_addresses.update(patch_device_addresses({
                    "mode": "partial", "rungs": selected,
                    "delete_rung_ids": [], "device_comments": {},
                }))
                allowed_addresses.update(
                    str(address).strip().upper()
                    for address in repair_base.get("device_comments", {})
                    if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\d+", address.strip())
                )

        location_text = "；".join(locations) if locations else "ladder schema"
        if local_repair:
            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"
            repair_text = (
                "这是用户明确确认的一次局部结构修复。系统已把失败候选作为 Current version JSON 提供给你。"
                "不要重新分析需求，不要重新生成完整程序，不要改变控制逻辑、地址、参数、触点极性或未出错梯级。"
                "只返回一个完整可解析的 JSON 对象，并且必须使用 mode=\"partial\"。"
                "rungs 只包含需要替换的完整梯级，delete_rung_ids 必须为空，device_comments 只列确实需要修复的现有地址。"
                "debug_note 是可选字段，默认删除；label、debug_note、device_comment 单条不得超过64字符。\n"
                f"允许修改的 rung_id：{rung_text}\n"
                f"失败位置：{location_text}"
            )
        else:
            repair_text = (
                "这是用户明确确认的一次 JSON 格式修复。失败候选本身无法安全解析，因此不能执行局部 rung 合并。"
                "不要重新分析需求，不要改变控制逻辑、地址、参数或触点极性。只补全/修正 JSON 协议与闭合结构。"
                "返回完整 ladder JSON，不要返回 mode=\"partial\"，不要输出解释文本。\n"
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
        }
        if local_repair:
            command.update(
                repair_baseline=repair_base,
                allowed_rung_ids=sorted(allowed_rung_ids),
                allowed_addresses=sorted(allowed_addresses),
            )
        return self.submit(command)

    def submit(self, command):
        self.writable()
        # Retry identity is the original HTTP command, not a newly observed
        # project/model snapshot. This also avoids reopening credentials on retry.
        request_key = hashlib.sha256(command["request_id"].encode()).hexdigest()
        path = self.state_dir / "job_commands" / (request_key + ".json")
        digest = canonical_hash(command)
        with self.lock.thread_lock:
            if path.exists():
                saved = read_json(path)
                if saved["command_hash"] != digest:
                    raise ConflictError("Request ID is already bound to another command")
                return self.jobs.get(saved["job_id"])
            job = self._submit_job(command)
            atomic_json(path, {"command_hash": digest, "job_id": job["id"]})
            return job

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
            if context.version and context.version.get("target_mode") == "fbd":
                raw = self.projects.artifact(project_id, context.version_id, "gxw").read_bytes()
                snapshot["fbd_baseline"] = base64.b64encode(raw).decode("ascii")
                snapshot["fbd_program"] = context.version.get("program_name")
            # Resolve files and credentials at submission, never later from mutable UI state.
            images = self._attachments(project_id, command.get("attachment_ids", []))
            requires_model = command["kind"] not in ("gx_read", "gx_inspect") and (command["kind"] != "review" or command.get("deep", True))
            provider, model = self.model_factory() if requires_model else (None, {})
            snapshot["model"] = model
            snapshot["approval_consent"] = self.approval.read()
            repair_context = "minimal" if command.get("repair_mode") or command.get("format_repair") else None
            snapshot["context_policy"] = resolve_context_policy(
                repair_context if requires_model else "legacy"
            ).snapshot()
            if command["kind"] == "debug_plan":
                snapshot["saved_run"] = self.projects.simulator_run(project_id, context.version_id, command.get("run_id"))

        def worker(ctx):
            if command["kind"] in ("gx_read", "gx_inspect"):
                try:
                    return self._read_gx(ctx, snapshot)
                except ChangeScopeError as error:
                    ctx.emit("progress", {"message": str(error), "code": "change_scope_violation"})
                    raise
            from api import provider_scope
            from i18n import language_context
            from model_provider import response_policy_scope
            from application.model_progress import ModelJobContext, ModelProgressReporter
            ctx.checkpoint()
            model_context = ModelJobContext(ctx)
            model_progress = ModelProgressReporter(model_context)
            context_audit = ContextAudit(lambda report: ctx.emit("context_audit", report))
            with language_context(snapshot["response_language"]), context_policy_scope(
                    snapshot.get("context_policy", "legacy"), audit=context_audit), provider_scope(
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
        from plc_change_scope import scope_instruction
        scoped_text = text + scope_instruction(snapshot.get("change_scope"))
        if kind == "analysis":
            from api import analyze_requirement_streaming
            from confirmed_spec import build_review_draft
            ctx.emit("progress", {"message": "正在分析需求"})
            analysis = analyze_requirement_streaming(text, confirmed_spec=project.get("confirmed_spec"),
                conversation_history=project.get("messages", []), image_attachments=images,
                on_reasoning_chunk=lambda t: ctx.emit("reasoning", {"text": t}),
                on_content_chunk=lambda t: ctx.emit("content", {"text": t}), response_language=language,
                on_format_repair=lambda: ctx.emit("progress", {"message": "正在修正需求分析的回复格式"}))
            if not isinstance(analysis, dict):
                raise ValueError("需求分析未完成。")
            output = {"analysis": analysis, "spec_draft": build_review_draft(analysis, project.get("confirmed_spec")),
                      "spec_base_hash": public_spec_hash(project.get("confirmed_spec")), "base_version_id": snapshot.get("version_id")}
        elif kind == "generation":
            from application.generation import GenerationRequest, GenerationWorkflow, GenerationDependencies
            from plc_ir import ir_to_ladder
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
                if repair_mode:
                    previous_json = copy.deepcopy(repair_baseline)
                elif format_repair:
                    previous_json = None
                else:
                    previous_json = ir_to_ladder(program) if program else None
                request = GenerationRequest(
                    user_input=scoped_text, effort=project.get("effort"), target_mode=project["target_mode"],
                    previous_json=previous_json, previous_ir=program,
                    confirmed_context=project.get("confirmed_spec"),
                    conversation_history=[] if (repair_mode or format_repair) else project.get("messages", []),
                    task_type=snapshot.get("task_type"),
                    plc_model=project.get("plc_model", "FX3U"), program_name=(program or {}).get("program_name", "MAIN"),
                    revision=(program or {}).get("revision", 0) + 1,
                    requirement_text=text, repair_mode=repair_mode,
                    allowed_rung_ids=snapshot.get("allowed_rung_ids"),
                    allowed_addresses=snapshot.get("allowed_addresses"),
                    image_attachments=images, model_name=snapshot.get("model", {}).get("model"), response_language=language)
                metadata = GenerationWorkflow(request, out_dir, ctx.emit, GenerationDependencies(
                    provider=provider, check_cancelled=ctx.checkpoint, preserve_rejected_candidate=True
                )).run()
            ctx.checkpoint()
            output = {"generation": metadata}
            payload = {"project_id": project_id, "target_mode": metadata["target_mode"],
                       "plc_model": project.get("plc_model", "FX3U"), "_confirmed_spec": project.get("confirmed_spec"),
                       "_validation_profile": metadata.get("validation_profile", "strict"),
                       "normalization": metadata.get("normalization"),
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
            output.update(proposal_id=proposal["id"], version_id=proposal["result"]["version_id"], status="saved")
            ctx.emit("progress", {"stage": "version_saved", "version_id": output["version_id"],
                "message": "程序已根据确认规格生成并自动保存；可选 Review、仿真或 GX 验证。"})
        elif kind == "agent":
            from plc_agent import run_tool_agent
            result = run_tool_agent(scoped_text, context=context, runtime=self.projects.runtime, provider=provider,
                conversation_history=project.get("messages", []), response_language=language,
                on_progress=lambda m: ctx.emit("progress", {"message": m}),
                on_reasoning_chunk=lambda t: ctx.emit("reasoning", {"text": t}),
                on_content_chunk=lambda t: ctx.emit("content", {"text": t}))
            ctx.checkpoint()
            with self.lock.thread_lock:
                self._check_snapshot(snapshot)
                proposals = [self._pending_proposal(p, f"{ctx.job_id}_{i}", base_version_id=snapshot.get("version_id"),
                    consent=snapshot["approval_consent"], direct_request=True, change_scope=snapshot.get("change_scope"))
                             for i, p in enumerate(result.pending_actions)]
                self.store.add_message(project_id, "assistant", result.content, kind="agent")
            output = {"content": result.content, "audit": result.audit, "proposal_ids": [p["id"] for p in proposals]}
            saved = [(p.get("result") or {}).get("version_id") for p in proposals if p["action"] == "accept_local"]
            if saved and saved[-1]:
                output["version_id"] = saved[-1]
        else:
            output = self._plan_or_review(ctx, snapshot, provider)
        atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
        return {key: output[key] for key in ("proposal_id", "proposal_ids", "version_id", "report_id", "plan_id", "status") if key in output}

    def _plan_or_review(self, ctx, snapshot, provider):
        # Pure workflow services are imported only when requested. Their signatures
        # are kept here, outside HTTP routes, to share them with the Qt adapters.
        from application.review import InspectionWorkflow
        from application.planning import SimulatorTestPlanWorkflow, EvidenceDebugPlanWorkflow
        from plc_ir import ir_to_ladder
        project, version, program = snapshot["project"], snapshot.get("version"), snapshot.get("program_ir")
        if not version or not program:
            raise ValueError("当前版本没有可用于检查或测试的 PLC IR。")
        project_id, version_id = project["id"], version["id"]
        common = {"on_event": ctx.emit, "response_language": snapshot["response_language"],
                  "provider": provider, "model_name": snapshot.get("model", {}).get("model"),
                  "effort": project.get("effort"), "program_ir": program}
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
        kind = pending.get("type")
        if kind in ("accept_candidate_patch", "accept_generated_program"):
            action = "accept_local"
        elif kind == "import_current_program_to_gxworks2":
            action = "gx_import"
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
            # Direct UI requests save without a second prompt. Connected API
            # clients are delegated explicitly by auto/full; default ask retains
            # their existing confirmation boundary. Standalone MCP is unchanged.
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
        if name in {"create_program_candidate", "patch_program"} and response.get("proposal_id"):
            current["candidate_proposal_id"] = response["proposal_id"]
        self._mcp_activity[project_id] = current

    def agent_call(self, command):
        self.writable()
        request_key = hashlib.sha256((command["project_id"] + ":" + command["call_id"]).encode()).hexdigest()
        path = self.state_dir / "agent_calls" / (request_key + ".json")
        digest = canonical_hash(command)
        with self.lock.thread_lock:
            if path.exists():
                saved = read_json(path)
                if saved["input_hash"] != digest:
                    raise ConflictError("Agent call ID was already used with other arguments")
                self._observe_mcp_call(command, saved["response"])
                return saved["response"]
            context = self.projects.tool_context(command["project_id"], command.get("version_id"))
            scope = self._command_scope(command, context)
            result = self.projects.runtime.invoke(ToolCall(command["call_id"], command["name"], command.get("arguments", {})), context)
            envelope = public_tool_result_data(result)
            response = {"data": envelope, "content": json.dumps(envelope, ensure_ascii=False), "is_error": result.is_error,
                        "call_id": command["call_id"], "name": command["name"]}
            pending = (result.data.get("data") or {}).get("pending_action")
            if not result.is_error and result.data.get("status") == "confirmation_required" and pending:
                proposal = self._pending_proposal(pending, request_key, base_version_id=context.version_id or None,
                                                  change_scope=scope)
                response["proposal_id"] = proposal["id"]
                if (proposal.get("result") or {}).get("version_id"):
                    response["version_id"] = proposal["result"]["version_id"]
                if proposal.get("execution_job_id"):
                    response["execution_job_id"] = proposal["execution_job_id"]
            atomic_json(path, {"input_hash": digest, "response": response})
            self._observe_mcp_call(command, response)
            return response

    @staticmethod
    def _command_scope(command, context):
        from plc_change_scope import validate_scope_baseline
        scope = command.get("change_scope")
        if scope is not None and command.get("kind") not in (None, "generation", "agent", "gx_read"):
            raise ValueError("修改范围仅适用于生成程序、Agent 和读取程序候选。")
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

                environment = read_gx_environment()
                if not environment.get("gx_works2_running"):
                    raise ExecutionUnavailableError(
                        environment.get("message")
                        or "GX Works2 未运行，请先启动 GX Works2 后再发送。"
                    )
                if version.get("target_mode") != "fbd" and environment.get("project_open") is False:
                    raise ExecutionUnavailableError(
                        "GX Works2 已运行，但尚未新建或打开目标工程。"
                    )
            payload = {"project_id": project_id, "version_id": version_id}
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
                public_summary={"summary": {"gx_import": "将指定版本导入 GX Works2", "simulation": "导入指定版本并运行指定仿真方案", "debug": "执行指定调试方案"}[command["action"]],
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
                result = self.proposals.accept(proposal_id, approved_by="policy" if policy else "user",
                    approval_mode=policy["mode"] if policy else None, executor=lambda payload, approved_id:
                    self.execution.submit_approved({"gx_import": "import_gx", "simulation": "simulate", "debug": "debug"}[proposal["action"]],
                        payload, approval_id=approved_id,
                        progress=lambda *m: ctx.emit("progress", {"message": str(m[-1])})).result())
            return {"proposal_id": proposal_id, "status": result["status"], "result": result.get("result")}
        return {"job": self.jobs.submit("execution", {"project_id": proposal["project_id"], "version_id": proposal["base_version_id"],
                    "proposal_id": proposal_id, "approval_policy": policy}, worker,
                    request_id=("policy_" + str(policy["revision"]) + "_" if policy else "approve_") + proposal_id)}

    def _candidate_diff(self, project_id, base_version_id, payload):
        """Review the proposal's bound version, never the UI's active selection."""
        if "_candidate_ir" in payload:
            from plc_core import PLCCore
            before = self.projects.program(project_id, base_version_id) if base_version_id else None
            expected = payload.get("base_ir_sha256")
            if expected and canonical_hash(before) != expected:
                raise ConflictError("The proposal's base program changed")
            result = dict(PLCCore().diff_programs(before, payload["_candidate_ir"]))
        elif payload.get("target_mode") == "fbd":
            from application.fbd import graph_diff, staged_bytes
            before = None
            if base_version_id and self.projects.raw_version(project_id, base_version_id)["target_mode"] == "fbd":
                before = self.projects.program(project_id, base_version_id)
            after = json.loads(staged_bytes(payload, self.state_dir)["fbd"])
            result = graph_diff(before, after)
        elif payload.get("target_mode") == "st":
            from difflib import unified_diff
            before = ""
            if base_version_id:
                base = self.projects.raw_version(project_id, base_version_id)
                artifact_id = "st" if "st" in (base.get("artifacts") or {}) else "st_from_ir"
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
        if diff["kind"] == "fbd":
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
            from application.fbd import staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            return {"target_mode": "fbd", "program": public(json.loads(artifacts["fbd"])),
                    "svg": artifacts["svg"].decode("utf-8"), "diff": public(payload["_preview_diff"])}
        if "_candidate_ir" in payload:
            # read_private verified the frozen payload hash. Re-render each read
            # instead of returning a stale/corrupt cache. No project writes occur.
            return {**self._render_ladder_preview(
                        payload["_candidate_ir"], theme=theme,
                        confirmed_spec=payload.get("_confirmed_spec"),
                        validation_profile=payload.get("_validation_profile", "strict")),
                    "diff": public(payload.get("_preview_diff") or self._candidate_diff(record["project_id"], record["base_version_id"], payload))}
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
    return "\n".join(f"步骤 {i + 1}：{s['name']}\n动作：{s['action']}\n转移条件：{s.get('transition') or '流程结束'}" for i, s in enumerate(steps))


def public_spec_hash(spec):
    from plc_ir import canonical_sha256
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
