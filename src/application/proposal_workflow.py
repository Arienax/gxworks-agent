# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Approval, conflict checks and execution proposal creation."""
from __future__ import annotations
import tempfile
from application.projects import record_id
from application.workspace import ConflictError

class ProposalWorkflow:
    """Create proposals and apply the existing approval policy."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 lock,
                 proposals,
                 jobs,
                 execution,
                 approval,
                 previews,
                 require_writable,
                 complete_native,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.lock = lock
        self.proposals = proposals
        self.jobs = jobs
        self.execution = execution
        self.approval = approval
        self.previews = previews
        self.require_writable = require_writable
        self.complete_native = complete_native
        self.store = projects.store

    def save_local(self, proposal):
        """Internal transaction/validation boundary, not an extra UI approval."""
        self.require_writable()
        if proposal["action"] != "accept_local":
            raise ValueError("Not a local save")
        return self.proposals.accept(proposal["id"], approved_by="local_autosave")

    def apply_execution_policy(self, proposal, consent=None):
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

    def pending_proposal(self, pending, request_id, *, base_version_id=None, consent=None, direct_request=False,
                          change_scope=None):
        if pending.get('type') in ('accept_fbd_candidate', 'accept_sfc_candidate', 'accept_st_candidate', 'accept_gxw_candidate'):
            from application.fbd import unpack_candidate
            mode = pending['target_mode']
            candidate_key = '_gxw_candidate' if pending['type'] == 'accept_gxw_candidate' else '_' + mode + '_candidate'
            # Runtime artifacts remain private; materialize them only inside
            # managed state while the existing proposal service freezes them.
            with tempfile.TemporaryDirectory(dir=self.state_dir, prefix=mode + '-tool-') as directory:
                prepared = unpack_candidate(pending[candidate_key], directory)
                payload = {key: value for key, value in pending.items() if key != candidate_key}
                payload.update(prepared, type='accept_generated_program')
                return self.pending_proposal(payload, request_id, base_version_id=base_version_id,
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
        payload = self.previews.with_candidate_diff(pending["project_id"], base_id, pending) if action == "accept_local" else dict(pending)
        proposal = self.proposals.create(action, pending["project_id"], payload,
            public_summary={"summary": "Agent 提出的工程操作", "diff": self.previews.diff_summary(payload["_preview_diff"]) if "_preview_diff" in payload else pending.get("diff"), "validation": pending.get("validation"), "normalization": pending.get("normalization")},
            base_version_id=base_id, request_id=request_id)
        if action == "accept_local":
            # Direct operator and local MCP requests save without a second prompt.
            # Connected HTTP agents are delegated explicitly by auto/full;
            # default ask retains their existing confirmation boundary.
            current = self.approval.read()
            if direct_request or (current["mode"] in {"auto", "full"} and (consent is None or consent == current)):
                return self.save_local(proposal)
            return proposal
        return self.apply_execution_policy(proposal, consent)

    def create_execution(self, command):
        self.require_writable()
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
            prior = self.proposals.find_request(project_id, command["request_id"])
            proposal = self.proposals.create(command["action"], project_id, payload,
                public_summary={"summary": {"gx_import": "将指定版本导入 GX Works2", "simulation": "导入指定版本并运行指定仿真方案", "debug": "执行指定调试方案",
                                           'native_engineering': '在独立 GX Works2 工程副本中执行已冻结的可见工程方案'}[command["action"]],
                                "version_id": version_id, "plan_id": plan_id},
                base_version_id=version_id, request_id=command["request_id"])
            return proposal if prior else self.apply_execution_policy(proposal)

    def decide(self, proposal_id, decision, *, policy=None):
        self.require_writable()
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
                        return self.complete_native(payload, approved_id, native)
                    return native
                result = self.proposals.accept(proposal_id, approved_by="policy" if policy else "user",
                    approval_mode=policy["mode"] if policy else None, executor=execute)
            return {"proposal_id": proposal_id, "status": result["status"], "result": result.get("result")}
        return {"job": self.jobs.submit("execution", {"project_id": proposal["project_id"], "version_id": proposal["base_version_id"],
                    "proposal_id": proposal_id, "approval_policy": policy}, worker,
                    request_id=("policy_" + str(policy["revision"]) + "_" if policy else "approve_") + proposal_id)}
