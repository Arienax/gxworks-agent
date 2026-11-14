# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Locked project and confirmed specification mutations."""
from __future__ import annotations
import copy
from contextlib import nullcontext
import shared.diagnostics as diagnostics
from application.projects import ProjectNotFoundError, public
from application.workspace import ConflictError
from application.spec_document import (
    apply_changes, bind_plan_interfaces, check_revision, document_path, editable_spec, read_document, write_document,
)
from plc.specification.approach import normalize_approach


class SpecConfirmationError(ValueError):
    """The current draft needs a user correction before confirmation."""


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
            self._initialize_spec_document(project)
            return self.projects.project(project["id"])

    def create_imported_project(self, *, name, raw, program):
        """An existing source is admitted by its file, not the New dialog menu."""
        self.require_writable()
        from gxw.offline_project import read_offline_project
        from gxw.project_metadata import read_project_settings
        from gxworks2.project_profiles import native_project_profile
        source = read_offline_project(raw)
        selected = source.select(program)
        profile = native_project_profile(source.context['cpu'])
        if profile is None or not profile.program_supported or selected['language'] is None:
            raise ValueError('Imported source requires a supported stored CPU and program language')
        metadata, = [value for name, value in source.streams.items() if name.endswith('.prj')]
        info = [value for name, value in source.streams.items() if name.endswith('.pji')]
        if len(info) > 1:
            raise ValueError('Imported source requires unambiguous saved project settings')
        settings = read_project_settings(metadata, info[0] if info else None)
        with self.lock.thread_lock:
            project = self.store.create_project(name=name, plc_model=source.context['cpu'],
                target_mode=selected['language'], **settings)
            # New-project defaults must not invent missing saved settings.
            project.update(settings)
            self.store.save_project(project)
            self._initialize_spec_document(project)
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
            new_target = {k: values[k] for k in ("plc_model", "target_mode") if k in values and values[k] != current[k]}
            document = self.get_spec_document(project_id) if new_target else None
            if document:
                check_revision(document, document["draft_revision"])
            self.store.update_project_settings(project_id, **values)
            if document:
                self.store.set_confirmed_spec(project_id, None)
                write_document(document, {**document["draft"], **new_target})
            return self.projects.project(project_id)

    def delete_project(self, project_id):
        """Delete one managed project after proving no live work still references it."""
        self.require_writable()
        with self.lock.thread_lock:
            try:
                project = self.projects.raw_project(project_id)
            except ProjectNotFoundError:
                project = None
            active_proposals = [
                proposal for proposal in (self.proposals.list(project_id) if self.proposals else [])
                if proposal.get("status") in {"pending", "executing"}
            ]
            if active_proposals:
                raise ConflictError("Project still has pending or executing proposals")
            cleanup = self.jobs.deleting_project(project_id) if self.jobs else nullcontext(False)
            with cleanup as has_jobs:
                if project is None and not has_jobs:
                    raise KeyError(project_id)
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

    def _initialize_spec_document(self, project):
        from application.workspace import atomic_json
        path = document_path(self.store, project["id"])
        if not path.exists():
            atomic_json(path, editable_spec(project))

    def get_spec_document(self, project_id, version_id=None):
        project = self.projects.raw_project(project_id)
        version = self.projects.raw_version(project_id, version_id) if version_id else None
        if self.lock:
            with self.lock.thread_lock:
                project = self.projects.raw_project(project_id)
                self._initialize_spec_document(project)
                return read_document(self.store, project, version)
        return read_document(self.store, project, version)

    def update_spec_document(self, project_id, changes, expected_revision):
        self.require_writable()
        with self.lock.thread_lock:
            document = self.get_spec_document(project_id)
            check_revision(document, expected_revision)
            draft = apply_changes(document["draft"], changes)
            write_document(document, draft)
            return self.get_spec_document(project_id)

    def save_analysis_draft(self, project_id, draft, expected_revision):
        self.require_writable()
        with self.lock.thread_lock:
            document = self.get_spec_document(project_id)
            check_revision(document, expected_revision)
            write_document(document, draft)
            return self.get_spec_document(project_id)

    def confirm_spec_document(self, project_id, expected_revision, version_id=None, *, allow_selected_approach=False):
        self.require_writable()
        with self.lock.thread_lock:
            document = self.get_spec_document(project_id)
            check_revision(document, expected_revision)
            draft = document["draft"]
            if document["issues"]["errors"]:
                raise SpecConfirmationError(document["issues"]["errors"][0]["message"])
            if not draft.get("requirements"):
                raise SpecConfirmationError("请先填写控制需求。")
            if not draft.get("implementation_items"):
                if not allow_selected_approach:
                    raise SpecConfirmationError("请先填写实现分工，再确认工程方案。")
                if not normalize_approach(draft.get("selected_approach")):
                    raise SpecConfirmationError("请先选择编程方案或填写实现分工。")
            response = self.set_spec(project_id, draft, document["base_revision"], from_document=True, version_id=version_id)
            if not response["valid"]:
                raise SpecConfirmationError(response["issues"]["errors"][0]["message"])
            return self.get_spec_document(project_id)

    def set_spec(self, project_id, spec, expected_hash, *, from_document=False, version_id=None):
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
            if not from_document:
                document = read_document(self.store, project)
                check_revision(document, document["draft_revision"])
                if document["differences"] and document["draft"] != spec:
                    raise ConflictError("plc-spec.json 有待确认的修改，请重新读取并确认当前草稿。")
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
            if any(row.get("template") and row.get("language") == "fbd"
                   for row in candidate_spec.get("implementation_items", [])):
                bind_plan_interfaces(self.projects.tool_context(project_id, version_id), candidate_spec)
            normalized = canonicalize_confirmed_spec(candidate_spec)
            issues = validate_spec_draft(normalized, project.get("plc_model"))
            if issues.get("errors"):
                return audited({"valid": False, "issues": public(issues)})
            self.store.set_confirmed_spec(project_id, normalized)
            persisted = self.projects.raw_project(project_id)["confirmed_spec"]
            from application.workspace import atomic_json
            atomic_json(document_path(self.store, project_id), editable_spec({**project, "confirmed_spec": persisted}))
            return audited({"valid": True, "spec": public(persisted), "hash": canonical_sha256(persisted),
                            **({"issues": public(issues)} if issues.get("warnings") else {})})
