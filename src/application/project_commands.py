"""Locked project and confirmed specification mutations."""
from __future__ import annotations
import copy
import shared.diagnostics as diagnostics
from application.projects import public
from application.workspace import ConflictError

class ProjectCommands:
    """Locked project and confirmed-specification mutations."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 lock,
                 jobs,
                 proposals,
                 require_writable,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.lock = lock
        self.jobs = jobs
        self.proposals = proposals
        self.require_writable = require_writable
        self.store = projects.store

    def create_project(self, **values):
        self.require_writable()
        from plc.project_capabilities import normalize_project_selection, DEFAULT_PLC_MODEL
        values.update(normalize_project_selection(values.get("plc_model", DEFAULT_PLC_MODEL),
                      values.get("target_mode", "ladder"), values.get("native_project_type"), values.get("use_labels")))
        with self.lock.thread_lock:
            project = self.store.create_project(**values)
            return self.projects.project(project["id"])

    def create_imported_project(self, *, name, raw, program):
        """An existing source is admitted by its file, not the New dialog menu."""
        self.require_writable()
        from gxw.offline_project import read_offline_project
        from gxw.project_metadata import project_source_groups
        from gxworks2.project_profiles import native_project_profile
        source = read_offline_project(raw)
        selected = source.select(program)
        profile = native_project_profile(source.context['cpu'])
        if profile is None or not profile.program_supported or selected['language'] is None:
            raise ValueError('Imported source requires a supported stored CPU and program language')
        groups, _ = project_source_groups(source.outer['projectdatalist.xml'])
        simple, _ = project_source_groups(source.outer['projectdatalist.xml'], folder_type=8)
        project_type = 'structured' if selected['source_name'] in groups else 'simple'
        with self.lock.thread_lock:
            project = self.store.create_project(name=name, plc_model=source.context['cpu'],
                target_mode=selected['language'], native_project_type=project_type,
                use_labels=selected['source_name'] in groups or selected['source_name'] in simple)
            return self.projects.project(project['id'])

    def update_project(self, project_id, **values):
        self.require_writable()
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
        self.require_writable()
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
        self.require_writable()
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
        self.require_writable()

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
