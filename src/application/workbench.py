# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Application commands shared by the HTTP operator and connected MCP agents."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from application.candidate_views import CandidateViews
from application.projects import ProjectService, record_id
from application.workspace import (
    WorkspaceWriterLock,
    ConflictError,
    atomic_json,
    canonical_hash,
    read_json,
)
from agent_runtime.messages import ToolCall
from agent_runtime.runtime import public_tool_result_data


from application.attachments import AttachmentService
from application.project_commands import ProjectCommands
from application.job_submission import JobSubmission
from application.job_execution import JobExecution
from application.proposal_workflow import ProposalWorkflow
from application.native_completion import NativeCompletion
from application.operation_status import OperationStatus
from application.projects import public_spec_hash
from plc.change_scope import ChangeScopeError

__all__ = ["WorkbenchService", "sfc_requirement", "ChangeScopeError", "public_spec_hash"]


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

    @property
    def attachments(self):
        return AttachmentService(
            projects=self.projects,
            state_dir=self.state_dir,
            lock=self.lock,
            require_writable=self.writable,
        )

    @property
    def project_commands(self):
        return ProjectCommands(
            projects=self.projects,
            state_dir=self.state_dir,
            lock=self.lock,
            jobs=self.jobs,
            proposals=self.proposals,
            require_writable=self.writable,
        )

    @property
    def job_submission(self):
        return JobSubmission(
            projects=self.projects,
            state_dir=self.state_dir,
            lock=self.lock,
            model_factory=self.model_factory,
            approval=self.approval,
            jobs=self.jobs,
            attachments=self.attachments,
            previews=self.previews,
            run_job=self._run_job,
            read_gx=self._read_gx,
            validate_snapshot=self._check_snapshot,
            require_writable=self.writable,
        )

    @property
    def job_execution(self):
        return JobExecution(
            projects=self.projects,
            state_dir=self.state_dir,
            lock=self.lock,
            proposals=self.proposals,
            execution=self.execution,
            previews=self.previews,
            pending_proposal=self._pending_proposal,
            save_local_proposal=self._save_local_proposal,
        )

    @property
    def proposal_workflow(self):
        return ProposalWorkflow(
            projects=self.projects,
            state_dir=self.state_dir,
            lock=self.lock,
            proposals=self.proposals,
            jobs=self.jobs,
            execution=self.execution,
            approval=self.approval,
            previews=self.previews,
            require_writable=self.writable,
            complete_native=self._complete_native_engineering,
        )

    @property
    def native_completion(self):
        return NativeCompletion(
            projects=self.projects,
            state_dir=self.state_dir,
            proposals=self.proposals,
            execution=self.execution,
        )

    @property
    def operation_receipts(self):
        return OperationStatus(
            projects=self.projects,
            state_dir=self.state_dir,
        )

    @property
    def previews(self):
        return CandidateViews(self.projects, self.state_dir, self.jobs, self.proposals, self.lock)

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
        return self.proposal_workflow.save_local(proposal)

    def _apply_execution_policy(self, proposal, consent=None):
        return self.proposal_workflow.apply_execution_policy(proposal, consent)

    def create_project(self, **values):
        return self.project_commands.create_project(**values)

    def update_project(self, project_id, **values):
        return self.project_commands.update_project(project_id, **values)

    def delete_project(self, project_id):
        return self.project_commands.delete_project(project_id)

    def activate_version(self, project_id, version_id, expected_active_version_id):
        return self.project_commands.activate_version(project_id, version_id, expected_active_version_id)

    @staticmethod
    def spec_presentation(spec):
        from rendering.specification import describe_specification
        return describe_specification(spec)

    @staticmethod
    def edit_spec_io_row(row=None, address=None):
        from plc.specification.confirmed import edit_io_row
        return edit_io_row(row, address)

    def set_spec(self, project_id, spec, expected_hash):
        return self.project_commands.set_spec(project_id, spec, expected_hash)

    def upload_attachment(self, project_id, filename, data_base64):
        return self.attachments.upload(project_id, filename, data_base64)

    @property
    def gxw_files(self):
        from application.gxw_files import GXWFileService
        return GXWFileService(self)

    def _attachments(self, project_id, ids):
        return self.attachments.load(project_id, ids)

    def _check_snapshot(self, snapshot):
        return self.job_execution.validate_snapshot(snapshot)

    def output(self, job_id):
        return self.previews.output(job_id)

    def _render_ladder_preview(self, program, *, theme=None, confirmed_spec=None, validation_profile='strict'):
        return self.previews.render_ladder_preview(program, theme=theme, confirmed_spec=confirmed_spec, validation_profile=validation_profile)

    def version_preview(self, project_id, version_id, *, theme=None):
        return self.previews.version_preview(project_id, version_id, theme=theme)

    def generation_preview(self, job_id, *, theme=None):
        return self.previews.generation_preview(job_id, theme=theme)

    def repair_generation(self, job_id, request_id):
        from application.generation_repair_workflow import repair_generation
        return repair_generation(self, job_id, request_id)

    def validate_snapshot(self, snapshot):
        self._check_snapshot(snapshot)

    def direct_user_fact_text(self, snapshot):
        return self._direct_user_fact_text(snapshot)

    def submit(self, command):
        return self.job_submission.submit(command)

    def _direct_user_fact_text(self, snapshot):
        return self.job_submission.direct_user_fact_text(snapshot)

    @staticmethod
    def _direct_answer_rows(questions, answers):
        return JobSubmission._direct_answer_rows(questions, answers)

    def _direct_snapshot_facts(self, snapshot):
        return self.job_submission._direct_snapshot_facts(snapshot)

    def _submit_job(self, command):
        return self.job_submission.submit_job(command)

    def _read_gx(self, ctx, snapshot):
        return self.job_execution.read_gx(ctx, snapshot)

    def _run_job(self, ctx, snapshot, context, images, provider):
        return self.job_execution.run(ctx, snapshot, context, images, provider)

    def _plan_or_review(self, ctx, snapshot, provider):
        return self.job_execution.plan_or_review(ctx, snapshot, provider)

    def _pending_proposal(self, pending, request_id, *, base_version_id=None, consent=None, direct_request=False, change_scope=None):
        return self.proposal_workflow.pending_proposal(pending, request_id, base_version_id=base_version_id, consent=consent, direct_request=direct_request, change_scope=change_scope)

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
        if name in {"create_program_candidate", "patch_program", "create_fbd_candidate", "create_sfc_candidate", "create_st_candidate", "edit_gxw_project"} and response.get("proposal_id"):
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
            context = self.projects.invocation_context(command["project_id"], command.get("version_id"), command["name"])
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
        return self.operation_receipts.get(project_id, proposal_id=proposal_id, execution_job_id=execution_job_id)

    @staticmethod
    def _command_scope(command, context):
        return JobSubmission.command_scope(command, context)

    def execution_proposal(self, command):
        return self.proposal_workflow.create_execution(command)

    def decide(self, proposal_id, decision, *, policy=None):
        return self.proposal_workflow.decide(proposal_id, decision, policy=policy)

    def _complete_native_engineering(self, payload, approved_id, result):
        return self.native_completion.complete(payload, approved_id, result)

    def _complete_live_engineering(self, payload, approved_id, result):
        return self.native_completion.complete_live(payload, approved_id, result)

    def _candidate_diff(self, project_id, base_version_id, payload):
        return self.previews.candidate_diff(project_id, base_version_id, payload)

    @staticmethod
    def _diff_summary(diff):
        return CandidateViews.diff_summary(diff)

    def _with_candidate_diff(self, project_id, base_version_id, payload):
        return self.previews.with_candidate_diff(project_id, base_version_id, payload)

    def proposal_preview(self, proposal_id, *, theme=None):
        return self.previews.proposal_preview(proposal_id, theme=theme)


def sfc_requirement(steps):
    from plc.sfc import linear_requirement
    return linear_requirement(steps)
