"""Freeze model and project inputs before scheduling work."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from application.projects import record_id
from application.workspace import (
    ConflictError,
    atomic_json,
    canonical_hash,
    read_json,
)
from plc.change_scope import ChangeScopeError
from shared.context_audit import ContextAudit, context_audit_scope

class JobSubmission:
    """Freeze job inputs, user facts and idempotent submission."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 lock,
                 model_factory,
                 approval,
                 jobs,
                 attachments,
                 previews,
                 run_job,
                 read_gx,
                 validate_snapshot,
                 require_writable,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.lock = lock
        self.model_factory = model_factory
        self.approval = approval
        self.jobs = jobs
        self.attachments = attachments
        self.previews = previews
        self.run_job = run_job
        self.read_gx = read_gx
        self.validate_snapshot = validate_snapshot
        self.require_writable = require_writable

    def submit(self, command):
        self.require_writable()
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
            job = self.submit_job(command)
            atomic_json(path, {"command_hash": digest, "job_id": job["id"]})
            return job

    def submit_job(self, command):
        self.require_writable()
        project_id = command["project_id"]
        with self.lock.thread_lock:
            context = self.projects.tool_context(project_id, command.get("version_id"))
            scope = self.command_scope(command, context)
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
                from application.confirmed_generation_context import project_direct_user_facts
                if project.get("target_mode") != "ladder" or context.program_ir is not None or scope is not None:
                    raise ValueError("Direct 一次生成用于新 Ladder 程序；已有工程编辑使用生成程序入口。")
                if not str(command.get("text") or "").strip() and not command.get("clarification_answers"):
                    raise ValueError("请输入原始需求或缺参答复。")
                parent_id = command.get("clarification_job_id")
                if parent_id:
                    parent = self.jobs.record(record_id(parent_id))
                    if (parent.get("kind") != "direct_generation" or parent.get("status") != "completed"
                            or parent.get("snapshot", {}).get("project_id") != project_id
                            or (parent.get("result") or {}).get("status") != "needs_input"):
                        raise ConflictError("续答必须绑定同一工程中等待补充信息的 Direct 作业。")
                    if parent.get("request_id") == command["request_id"]:
                        raise ConflictError("续答需要新的请求 ID。")
                    self.validate_snapshot(parent["snapshot"])
                    previous = self.previews.output(parent_id)
                    prior_text = parent["snapshot"].get("raw_requirement", parent["snapshot"].get("text", ""))
                    questions = (previous.get("generation") or {}).get("missing_info", [])
                    answer_text = command.get("text", "")
                    rows = []
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
                    snapshot["user_fact_text"] = (self.direct_user_fact_text(parent["snapshot"])
                        + "\n用户续答：\n" + answer_text)
                    parent_facts = self._direct_snapshot_facts(parent["snapshot"])
                    snapshot["direct_user_facts"] = project_direct_user_facts(
                        command.get("text", ""), project["plc_model"], parent_facts, answers=rows)
                    snapshot["attachment_ids"] = list(dict.fromkeys([
                        *parent["snapshot"].get("attachment_ids", []), *command.get("attachment_ids", [])]))
                else:
                    snapshot["raw_requirement"] = command["text"]
                    snapshot["user_fact_text"] = command["text"]
                    snapshot["direct_user_facts"] = project_direct_user_facts(
                        command["text"], project["plc_model"], project.get("confirmed_spec"))
            if context.version and (context.version.get("target_mode") == "fbd" or
                    context.version.get('target_mode') == 'st' and context.native_snapshot is not None):
                raw = self.projects.artifact(project_id, context.version_id, "gxw").read_bytes()
                snapshot["fbd_baseline"] = base64.b64encode(raw).decode("ascii")
                snapshot["fbd_program"] = context.version.get("program_name")
            # Resolve files and credentials at submission, never later from mutable UI state.
            images = self.attachments.load(project_id, snapshot.get("attachment_ids", []))
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
                    return self.read_gx(ctx, snapshot)
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
                    result = self.run_job(model_context, snapshot, context, images, provider)
                except ChangeScopeError as error:
                    ctx.emit("progress", {"message": str(error), "code": "change_scope_violation"})
                    raise
                finally:
                    model_context.flush()
            return result
        return self.jobs.submit(command["kind"], snapshot, worker, request_id=command["request_id"])

    def direct_user_fact_text(self, snapshot):
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
            snapshot = self.jobs.record(record_id(parent_id))["snapshot"]

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
                rows.append({"id": question["id"], "question": question["question"], "answer": answer,
                             **({"io_binding": copy.deepcopy(question["io_binding"])}
                                if isinstance(question.get("io_binding"), dict) else {})})
        return rows

    def _direct_snapshot_facts(self, snapshot):
        """Replay legacy human revisions by turn, retaining their answer targets."""
        from application.confirmed_generation_context import project_direct_user_facts
        chain, visited = [], set()
        while "direct_user_facts" not in snapshot:
            chain.append(snapshot)
            parent_id = snapshot.get("clarification_job_id")
            if not parent_id:
                break
            if parent_id in visited:
                raise ConflictError("续答作业链存在循环。")
            visited.add(parent_id)
            snapshot = self.jobs.record(record_id(parent_id))["snapshot"]
        facts = snapshot.get("direct_user_facts", snapshot.get("project", {}).get("confirmed_spec"))
        for turn in reversed(chain):
            answers = []
            if turn.get("clarification_job_id") and isinstance(turn.get("clarification_answers"), dict):
                previous = self.previews.output(turn["clarification_job_id"])
                questions = (previous.get("generation") or {}).get("missing_info", [])
                # Recovery accepts partial historical answers; new submissions
                # still use the existing form validation above.
                known_ids = {q["id"] for q in questions}
                answers = self._direct_answer_rows([{**q, "required": False} for q in questions],
                    {key: value for key, value in turn["clarification_answers"].items() if key in known_ids})
            facts = project_direct_user_facts(turn.get("text", ""),
                turn["project"].get("plc_model", "FX3U"), facts, answers=answers)
        return copy.deepcopy(facts)

    @staticmethod
    def command_scope(command, context):
        from plc.change_scope import validate_scope_baseline
        scope = command.get("change_scope")
        if scope is not None and command.get("kind") not in (None, "generation", "gx_read"):
            raise ValueError("修改范围仅适用于生成程序和读取程序候选。")
        mode = (context.version or {}).get("target_mode") or context.project.get("target_mode", "ladder")
        if command.get("kind") == "generation" and context.project.get("target_mode") != "ladder":
            mode = context.project["target_mode"]
        return validate_scope_baseline(scope, context.program_ir, target_mode=mode)
