# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Execute frozen generation, planning and inspection jobs."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from application.projects import public
from application.workspace import (
    ConflictError,
    atomic_json,
    canonical_hash,
)
from application.projects import public_spec_hash

class JobExecution:
    """Run one frozen model, inspection or GX read job."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 lock,
                 proposals,
                 execution,
                 previews,
                 pending_proposal,
                 save_local_proposal,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.lock = lock
        self.proposals = proposals
        self.execution = execution
        self.previews = previews
        self.pending_proposal = pending_proposal
        self.save_local_proposal = save_local_proposal
        self.store = projects.store

    def run(self, ctx, snapshot, context, images, provider):
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
            mode = (version or {}).get('target_mode') or project['target_mode']
            ordered = ((snapshot.get('program_ir') or {}).get('source') or {}).get('format') in {'instruction_sequence_v1', 'instruction_sequence_v2'}
            existing_offline = context.native_snapshot is not None and (ordered or mode in {'st', 'fbd', 'sfc'})
            if existing_offline:
                from application.gxw_files import generate_candidate
                source_payload = generate_candidate(out_dir, {**snapshot, 'text': scoped_text}, context, images, ctx)
                metadata = source_payload['metadata']
                metadata['artifacts'] = {key: entry['path'] for key, entry in source_payload['artifacts'].items()}
            elif project["target_mode"] == "fbd" or (version or {}).get("target_mode") == "fbd":
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
                st_baseline, st_context = None, None
                if project['target_mode'] == 'st' and snapshot.get('fbd_baseline'):
                    from gxw.st_program import project_context_snapshot
                    st_baseline = base64.b64decode(snapshot['fbd_baseline'])
                    st_context = project_context_snapshot(st_baseline, (version or {}).get('program_name'))
                if repair_mode:
                    previous_json = copy.deepcopy(repair_baseline)
                elif format_repair or fresh_confirmed_generation:
                    previous_json = None
                else:
                    previous_json = ir_to_ladder(program) if program else None
                request = GenerationRequest(
                    user_input=snapshot.get("raw_requirement", scoped_text), effort=None, target_mode=project["target_mode"],
                    user_fact_text="" if kind == "direct_generation" and "direct_user_facts" in snapshot else snapshot.get("user_fact_text"),
                    previous_json=previous_json,
                    previous_ir=None if fresh_confirmed_generation else program,
                    confirmed_context=project.get("confirmed_spec"),
                    direct_user_facts=snapshot.get("direct_user_facts") if kind == "direct_generation" else None,
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
                    direct_generation=kind == "direct_generation",
                    st_native_baseline=st_baseline, st_project_context=st_context)
                metadata = GenerationWorkflow(request, out_dir, ctx.emit, GenerationDependencies(
                    provider=provider, check_cancelled=ctx.checkpoint, preserve_rejected_candidate=True
                )).run()
            ctx.checkpoint()
            output = {"generation": metadata}
            if metadata.get('native_plan') is not None:
                # The model generated intentions. Existing native transactions
                # own side effects and version activation after verification.
                with self.lock.thread_lock:
                    self.validate_snapshot(snapshot)
                    pending = dict(type='execute_native_engineering', project_id=project_id,
                                   version_id=(version or {}).get('id'), plan=metadata['native_plan'],
                                   st_program_name=metadata.get('candidate_program_name'), validation=metadata['validation'])
                    proposal = self.pending_proposal(pending, ctx.job_id, base_version_id=(version or {}).get('id'))
                output.update(proposal_id=proposal['id'], status='awaiting_native_verification')
                return output
            if snapshot.get("generation_action"):
                output["generation_action"] = snapshot["generation_action"]
            payload = {"project_id": project_id, "target_mode": metadata["target_mode"],
                       "plc_model": project.get("plc_model", "FX3U"), "_confirmed_spec": project.get("confirmed_spec"),
                       "_validation_profile": metadata.get("validation_profile", "strict"),
                       "_generation_handoff": copy.deepcopy(metadata.get("generation_handoff")),
                       "maintainability_review": copy.deepcopy(metadata.get("maintainability_review")),
                       "normalization": metadata.get("normalization"),
                       "_construction_binding": copy.deepcopy((metadata.get('first_pass_pipeline') or {}).get('construction_binding')),
                       "change_scope": snapshot.get("change_scope")}
            if metadata["target_mode"] == "ladder" and 'gxw' not in metadata['artifacts']:
                payload["_candidate_ir"] = json.loads((out_dir / metadata["artifacts"]["ir"]).read_text(encoding="utf-8"))
            else:
                payload.update(staging_dir=str(out_dir), metadata=metadata, artifacts={k: {
                    "path": v, "sha256": hashlib.sha256((out_dir / v).read_bytes()).hexdigest()}
                    for k, v in metadata["artifacts"].items()})
            with self.lock.thread_lock:
                self.validate_snapshot(snapshot)
                payload = self.previews.with_candidate_diff(project_id, (version or {}).get("id"), payload)
                proposal = self.proposals.create("accept_local", project_id, payload,
                    public_summary={"summary": text[:500], "validation": metadata["validation"], "normalization": metadata.get("normalization"), "diff": self.previews.diff_summary(payload["_preview_diff"])},
                    base_version_id=(version or {}).get("id"), request_id=ctx.job_id)
                ctx.checkpoint()
                proposal = self.save_local_proposal(proposal)
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
                self.validate_snapshot(snapshot)
                if result.pending_actions:
                    raise ValueError("工程问答不能提出程序修改或外部操作。")
                self.store.add_message(project_id, "assistant", result.content, kind="agent")
            output = {"content": result.content, "audit": result.audit, "proposal_ids": []}
        else:
            output = self.plan_or_review(ctx, snapshot, provider)
        atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
        return {key: output[key] for key in ("proposal_id", "proposal_ids", "version_id", "report_id", "plan_id", "status", "generation_action") if key in output}

    def read_gx(self, ctx, snapshot):
        with self.lock.thread_lock:
            ctx.checkpoint()
            self.validate_snapshot(snapshot)
            result = self.execution.submit_read("read_gx" if snapshot["kind"] == "gx_read" else "inspect_gx",
                {"project_id": snapshot["project_id"], "version_id": snapshot["version_id"]},
                progress=lambda *values: ctx.emit("progress", {"message": str(values[-1])})).result()
            output = public(result)
            if result.get("_candidate_ir"):
                candidate = self.previews.with_candidate_diff(snapshot["project_id"], snapshot["version_id"],
                    {"_candidate_ir": result["_candidate_ir"], "_confirmed_spec": result.get("_confirmed_spec"),
                     "target_mode": "ladder", "change_scope": snapshot.get("change_scope")})
                proposal = self.proposals.create("accept_local", snapshot["project_id"],
                    candidate,
                    public_summary={"summary": "从 GX Works2 读取的程序", "diff": self.previews.diff_summary(candidate["_preview_diff"])},
                    base_version_id=snapshot["version_id"], request_id=ctx.job_id)
                proposal = self.save_local_proposal(proposal)
                output["proposal_id"] = proposal["id"]
                output["version_id"] = proposal["result"]["version_id"]
            atomic_json(self.state_dir / "outputs" / (ctx.job_id + ".json"), output)
            return {"status": result.get("status"), "proposal_id": output.get("proposal_id"), "version_id": output.get("version_id"), "passed": False}

    def plan_or_review(self, ctx, snapshot, provider):
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
            self.validate_snapshot(snapshot)
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

    def validate_snapshot(self, snapshot):
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
