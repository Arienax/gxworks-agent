# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Headless engineering sessions using the shared application transactions.

Session tools manage selection and receipts; PLC tools retain their existing
registry, validation, artifact bindings and native execution coordinator.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import (SAFE_TOOL_NAMES, READ_ONLY_TOOL_NAMES, DISCOVERY_TOOL_NAMES,
                                     ToolDefinition, ToolRegistry, build_tool_context)
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS
from agent_runtime.runtime import public_tool_result_data
from application.workbench import WorkbenchService
from application.design_session import DesignSession
from application.instruction_research import new_candidate
from application.workspace import (
    ConflictError, WorkspaceBusyError, atomic_json, canonical_hash,
    default_state_dir, read_json, record_id,
)
from storage.session import default_workspace_dir


SESSION_TOOL_NAMES = (
    "list_projects", "create_project", "select_project", "import_gxw_project",
    "get_project_artifacts", "get_operation_status", "get_design_context", "propose_design_plan", "confirm_design_plan",
)


def default_mcp_workspace() -> Path:
    override = os.environ.get("GXWORKS_MCP_WORKSPACE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else default_workspace_dir("mcp-workspace")


class _ExternalClientSettings:
    def model_snapshot(self):
        raise RuntimeError("The external MCP client owns model planning; no application model request is available")


class EngineeringSession:
    """A local client owns its selection and one persistent application service.

    Writers share a workspace only after explicit close releases ownership.
    Native work finishes before its worker and writer lock are released.
    Startup consent comes from operator CLI/configuration, not tool arguments.
    """

    def __init__(self, workspace=None, *, project_id=None, version_id=None,
                 state_dir=None, approval_mode=None, confirm_full_access=False):
        if approval_mode == "full" and confirm_full_access is not True:
            raise PermissionError("--approval-mode full requires --confirm-full-access")
        if approval_mode is not None and approval_mode not in {"ask", "auto", "full"}:
            raise ValueError("Unknown approval mode")
        self.workspace = Path(workspace or default_mcp_workspace()).expanduser().resolve()
        state_dir = Path(state_dir or default_state_dir(self.workspace)).expanduser().resolve()
        if state_dir.is_relative_to(self.workspace):
            raise ValueError("Application state must be outside the engineering workspace")
        self.service = WorkbenchService(self.workspace, state_dir, exclusive_workspace=True,
                                        settings=_ExternalClientSettings())
        self.design = DesignSession(self.service, separate_tasks=True)
        self.project_id = record_id(project_id, "project") if project_id else None
        self.version_id = record_id(version_id, "version") if version_id else None
        if self.version_id and not self.project_id:
            raise ValueError("--version requires --project")
        if self.project_id:
            self.service.projects.tool_context(self.project_id, self.version_id, requirement='model')
        self.approval_mode = approval_mode
        self.confirm_full_access = confirm_full_access
        self._consent_initialized = False
        self._call_lock = threading.RLock()
        self._closed = False
        self.registry = self._session_registry()
        self._manual_reads = {}
        self.inspection = self.service.projects.inspection

    @classmethod
    def attach(cls, service, *, project_id, version_id=None, change_scope=None, program=None, before_write=None,
               design_job_id=None, direct_input=None):
        """Bind host-owned tools to the Web service without creating another workspace."""
        session = cls.__new__(cls)
        session.service = service
        session.workspace = service.projects.workspace
        session.project_id = record_id(project_id, 'project')
        session.version_id = record_id(version_id, 'version') if version_id else None
        session.design = DesignSession(service, job_id=design_job_id)
        session.approval_mode = None
        session.confirm_full_access = False
        session._consent_initialized = True
        session._call_lock = threading.RLock()
        session._closed = False
        session._host_owned = True
        session.change_scope = change_scope
        session.program = program
        session.before_write = before_write
        session.direct_input = direct_input
        session.registry = session._session_registry()
        session._manual_reads = {}
        session.inspection = service.projects.inspection
        return session

    def _session_registry(self):
        registry = ToolRegistry()
        if self.service.receipt_store is not None:
            from application.read_delivery import ReadDeliveryStore
            registry._read_delivery = ReadDeliveryStore(receipt_store=self.service.receipt_store)
        definitions = (
            ("list_projects", "List saved PLC projects and this MCP session's explicit selection.", {}, (), self._list_projects),
            ("create_project", "Create and select a PLC project using the shared project service; specify the actual CPU. target_mode defaults to ladder unless the user requests another language.",
             {"name": {"type": "string", "minLength": 1, "maxLength": 200},
              "plc_model": {"type": "string", "minLength": 1, "maxLength": 100},
              "target_mode": {"type": "string", "enum": ["ladder", "st", "fbd", "sfc"]},
              "generation_mode": {"type": "string", "enum": ["design", "direct"],
                                  "description": "Select by specification completeness: use direct for cross-vendor/cross-language program porting or translation, complete logic/step/timing/algorithm specifications, deterministic FB/FUNCTION authoring, or explicit direct requests; use design for open-ended process/machine requirements that benefit from comparing 1-3 approaches before human approval."},
              "native_project_type": {"type": "string", "minLength": 1, "maxLength": 32},
              "use_labels": {"type": "boolean"}},
             ("name", "plc_model"), self._create_project),
            ("get_design_context", "Start Design with the complete human requirement. To recover a pending task, omit user_requirement; the host selects a unique task or accepts analysis_id or repair_id. Use the context to compare 1-3 approaches before generation. Use Direct for porting or an already complete specification.",
             {"user_requirement": {"type": "string", "minLength": 1}, "instruction_details": {"type": "boolean"},
              "analysis_id": {"type": "string", "minLength": 1}, "repair_id": {"type": "string", "minLength": 1}}, (), self._design_context),
            ("propose_design_plan", "Submit the external model's Web Design analysis JSON, including 1-3 approaches. If repair_required, submit only the listed path/value repairs for that repair_id. Empty arguments recall the draft or repair targets. Resolve required facts with the user before confirmation.",
             {"analysis_id": {"type": "string"}, "analysis": {"type": "object"}, "repair_id": {"type": "string"},
              "repairs": {"type": "array", "items": {"type": "object", "required": ["path", "value"],
                  "properties": {"path": {"type": "string"}, "value": {}}, "additionalProperties": False}}}, (), self._design_proposal),
            ("confirm_design_plan", "Persist the user-approved approach and the user's parameter answers for this exact draft. Follow next_actions to generate from the confirmed specification.",
             {"draft_id": {"type": "string"}, "approach_id": {"type": "string"},
              "parameter_answers": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1},
                                    "description": "Map parameter or open-question IDs from the current review to answers supplied by the user."}},
             ("draft_id", "approach_id"), self._design_confirmation),
            ("select_project", "Select an existing project for this MCP session. Optional version_id pins a saved baseline; it does not activate a version.",
             {"project_id": {"type": "string", "minLength": 1, "maxLength": 128},
              "version_id": {"type": "string", "minLength": 1, "maxLength": 128}},
             ("project_id",), self._select_project),
            ("get_project_artifacts", "Return verified managed artifact paths for the selected saved version, without exporting or overwriting files.",
             {}, (), self._artifacts),
            ('import_gxw_project', 'Import a supplied local GXW path or data_base64 as a new source version, without GX Works2. CPU and source language come from the file. program is required for a multi-program file. Specify project_id/version_id only to import against that explicit existing baseline.',
             {'data_base64': {'type': 'string', 'minLength': 1, 'maxLength': 41943040},
              'path': {'type': 'string', 'minLength': 1, 'maxLength': 32767},
              'filename': {'type': 'string', 'maxLength': 255},
              'program': {'type': 'string', 'minLength': 1, 'maxLength': 512},
              'project_id': {'type': 'string', 'maxLength': 128},
              'version_id': {'type': 'string', 'maxLength': 128},
              'request_id': {'type': 'string', 'maxLength': 128}}, (), self._import_gxw),
        )
        for name, description, properties, required, handler in definitions:
            registry.register(ToolDefinition(name, description,
                {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}, handler))
        shared = self.service.projects.runtime.registry._tools['get_operation_status']
        registry.register(replace(shared, handler=self._operation_status))
        return registry

    def list_tools(self, context=None):
        return self.registry.schemas() + [tool for tool in self.service.projects.runtime.list_tools()
            if tool['function']['name'] not in self.registry.names]

    def check(self):
        """Read-only setup diagnostics; do not create state or inspect credentials."""
        return {"ok": True, "mode": "local", "project_id": self.project_id,
                "tool_count": len(self.list_tools()), "local_autosave": True,
                "approval_mode": self.approval_mode or self.service.approval.read()["mode"]}

    def _require_project(self):
        if not self.project_id:
            raise ToolPreconditionError("NO_PROJECT_SELECTED", "No project selected; discover a native source or select/create an Agent project.",
                                        next_actions=PROJECT_ACTIONS)
        return self.project_id

    def _list_projects(self, _context, _arguments):
        return {"projects": self.service.projects.list_projects(),
                "selected_project_id": self.project_id, "pinned_version_id": self.version_id}

    def _create_project(self, _context, arguments):
        options = dict(arguments)
        mode = options.pop('generation_mode', os.environ.get('GXWORKS_MCP_NEW_PROJECT_MODE', 'direct'))
        project = self.service.create_project(**{"target_mode": "ladder", **options})
        self.project_id, self.version_id = project["id"], None
        result = {"status": "created", "project": project, "selected_project_id": self.project_id}
        if mode == 'design':
            self.design.start(self.project_id)
            result['next_actions'] = [{'tool': 'get_design_context'}]
        elif mode == 'direct':
            result['next_actions'] = [{'tool': 'get_generation_context'}]
        return result

    def _new_design_project(self):
        project_id = self._require_project()
        context = self.service.projects.tool_context(project_id, self.version_id, requirement='model')
        if context.version_id and not getattr(self, '_host_owned', False):
            raise ValueError('Design 新建程序需要尚无程序版本的工程。已有版本请使用当前版本的编辑流程。')
        return project_id

    def _design_context(self, _context, arguments):
        project_id = self._new_design_project()
        if not arguments.get('user_requirement'):
            return self.design.resume_context(project_id, analysis_id=arguments.get('analysis_id'),
                                              repair_id=arguments.get('repair_id'))
        if arguments.get('analysis_id') or arguments.get('repair_id'):
            raise ValueError('新任务传入用户原文，恢复已有任务只需任务引用。')
        return self.design.context(project_id, arguments['user_requirement'],
                                   manual_context=_context if arguments.get('instruction_details') else None,
                                   defer_read=_context.defer_read)

    def finish_host_read(self, tool, document_id, accepted):
        with self._call_lock, self.service.lock.thread_lock:
            project_id = self._require_project()
            registry = self.registry if tool == 'get_design_context' else self.service.projects.runtime.registry
            store = registry._read_delivery
            if store is None:
                return False
            completed = store.finish_host_read(tool, document_id, project_id, accepted=accepted)
            if completed is None:
                return False
            if accepted and tool == 'get_design_context':
                analysis_id = completed['receipt']
                self.design.mark_read_complete(project_id, analysis_id, citations=store.design_citations(analysis_id))
            if accepted and tool == 'search_plc_manual':
                self._manual_reads.setdefault(project_id, set()).add(document_id)
            return True

    def _design_proposal(self, _context, arguments):
        project_id = self._new_design_project()
        citations = {}
        if 'analysis' in arguments or 'repairs' in arguments:
            state, _, _ = self.design._pending(project_id, analysis_id=arguments.get('analysis_id'),
                                               repair_id=arguments.get('repair_id'))
            expected = state['analysis_id']
            if not state.get('read_complete'):
                if self.registry._read_delivery is None:
                    raise ToolPreconditionError('DESIGN_CONTEXT_REQUIRED', '请先读取当前工程的 Design 上下文。',
                                                next_actions=[{'tool': 'get_design_context'}])
                self.registry._read_delivery.check_design_read(expected)
            arguments = dict(arguments)
            arguments.setdefault('analysis_id', expected)
            if 'repairs' in arguments and state.get('repair_id'):
                arguments.setdefault('repair_id', state['repair_id'])
            if not state.get('read_complete'):
                self.design.mark_read_complete(project_id, expected)
            if self.registry._read_delivery is not None:
                citations.update(self.registry._read_delivery.design_citations(expected))
            store = self.service.projects.runtime.registry._read_delivery
            manual_documents = self._manual_reads.get(project_id)
            if store is not None:
                from application.read_delivery import manual_binding
                from plc.specification.fact_citations import referenced_fact_ids
                context = self.service.projects.tool_context(project_id, self.version_id, requirement='model')
                scope = manual_binding(context)
                if manual_documents:
                    citations.update(store.design_citations(manual_scope=scope, manual_documents=manual_documents))
                references = referenced_fact_ids(arguments.get('analysis') or arguments.get('repairs'))
                if 'repairs' in arguments:
                    references.update(referenced_fact_ids((state.get('repair') or {}).get('base')))
                citations.update(store.referenced_citations(project_id, scope, references - citations.keys()))
        return self.design.propose(project_id, arguments, citations=citations)

    def _design_confirmation(self, _context, arguments):
        return self.design.confirm(self._new_design_project(), **arguments)

    def _select_project(self, _context, arguments):
        project_id = record_id(arguments["project_id"], "project")
        version_id = record_id(arguments["version_id"], "version") if arguments.get("version_id") else None
        self.service.projects.tool_context(project_id, version_id, requirement='model')
        self.project_id, self.version_id = project_id, version_id
        return {"status": "selected", "project": self.service.projects.project(project_id),
                "selected_project_id": project_id, "pinned_version_id": version_id}

    def _import_gxw(self, _context, arguments):
        result = self.service.gxw_files.import_project(**arguments)
        self.project_id, self.version_id = result['project_id'], result['version_id']
        return {**result, 'selected_project_id': self.project_id, 'pinned_version_id': self.version_id}

    def _artifacts(self, _context, _arguments):
        project_id = self._require_project()
        context = self.service.projects.tool_context(project_id, self.version_id, requirement='model')
        if not context.version_id:
            raise ValueError("The selected project has no saved version")
        return self._version_artifacts(project_id, context.version_id)

    def _version_artifacts(self, project_id, version_id):
        version = self.service.projects.raw_version(project_id, version_id)
        artifacts = self.service.projects.artifacts(project_id, version_id)
        for artifact in artifacts:
            if artifact["available"]:
                artifact["path"] = str(self.service.projects.artifact(project_id, version_id, artifact["id"]))
        return {"project_id": project_id, "version_id": version_id, "artifacts": artifacts,
                "delivery": version.get('delivery'), "validation": version.get('validation')}

    def _operation_status(self, _context, arguments):
        context = build_tool_context(self.service.projects.raw_project(self._require_project()),
                                     operation_status=self.service.operation_status)
        shared = self.service.projects.runtime.registry._tools['get_operation_status']
        return shared.handler(context, arguments)

    @staticmethod
    def _error(call, code, message):
        envelope = {"ok": False, "tool": call.name, "error": {"code": code, "message": message, "next_actions": []}}
        return ToolResult(call.id, call.name, json.dumps(envelope, ensure_ascii=False), envelope, is_error=True)

    def invoke(self, call: ToolCall, context=None) -> ToolResult:
        if self._closed:
            return self._error(call, 'SESSION_CLOSED', 'The engineering session is closed')
        if call.name not in (*SESSION_TOOL_NAMES, *SAFE_TOOL_NAMES):
            return self._error(call, "UNKNOWN_TOOL", "Tool is not in the engineering tool registry")
        try:
            arguments = ToolRegistry._decode_arguments(call.arguments)
            if call.name == 'create_st_candidate' and 'st_code' in arguments:
                if 'st_text' in arguments:
                    raise ValueError('Provide st_text (do not pass both st_text and st_code)')
                arguments['st_text'] = arguments.pop('st_code')
            digest = canonical_hash({"name": call.name, "arguments": arguments,
                                     **({'defer_read': True} if call.defer_read else {})})
        except ValueError as error:
            return self._error(call, "INVALID_ARGUMENTS", str(error))
        try:
            if call.name in {'get_operation_status', 'get_native_engineering_session',
                             'pause_native_engineering_session'}:
                definition = self.service.projects.runtime.registry._tools[call.name]
                try:
                    ToolRegistry._validate_arguments(definition.parameters, arguments)
                except ValueError as error:
                    return self._error(call, 'INVALID_ARGUMENTS', str(error))
                # The in-flight engineering call holds _call_lock while waiting
                # for its real job. These callbacks read caches or set an Event.
                # Capture selection once so a later selection cannot redirect it.
                project_id = self.project_id
                project = self.service.projects.raw_project(project_id) if project_id else {}
                context = build_tool_context(project,
                    operation_status=lambda _project, **query: self.service.operation_status(project_id, **query),
                    native_session_status=lambda _project, session_id=None:
                        self.service.native_session_status(project_id, session_id),
                    native_session_pause=lambda _project, session_id:
                        self.service.native_session_pause(project_id, session_id))
                return self.service.projects.runtime.invoke(call, context)
            with self._call_lock:
                if self._closed:
                    return self._error(call, 'SESSION_CLOSED', 'The engineering session is closed')
                if (self.project_id and not getattr(self, 'host_direct', False)
                        and (call.name == 'get_generation_context' or new_candidate(call.name, arguments))):
                    self.design.guard_generation(self.project_id, arguments, tool_name=call.name)
                # Inspection never initializes a writer, migrates projects, or
                # inherits an execution approval merely to read a source.
                if call.name in READ_ONLY_TOOL_NAMES and not arguments.get("native"):
                    from dataclasses import replace
                    independent = (call.name in DISCOVERY_TOOL_NAMES - {"get_current_project"}
                                   or bool(arguments.get("source_ref")))
                    context = (build_tool_context({}) if independent or not self.project_id else
                               self.service.projects.invocation_context(self.project_id, self.version_id, call.name))
                    if not independent and getattr(self, 'program', None) and context.native_snapshot is not None:
                        from application.gxw_files import source_context
                        context = source_context(context, self.program)
                    context = replace(context, inspection=self.inspection,
                                      read_revision=self.service.projects.read_revision,
                                      operation_status=self.service.operation_status,
                                      native_session_status=self.service.native_session_status,
                                      native_session_pause=self.service.native_session_pause)
                    return self.service.projects.runtime.invoke(call, context)
                if call.name in SAFE_TOOL_NAMES:
                    definition = self.service.projects.runtime.registry._tools[call.name]
                    try:
                        ToolRegistry._validate_arguments(definition.parameters, arguments)
                    except ValueError as error:
                        return self._error(call, "INVALID_ARGUMENTS", str(error))
                    self._require_project()
                if not getattr(self, '_host_owned', False):
                    self.service.start()
                if not self._consent_initialized:
                    if self.approval_mode is not None:
                        self.service.update_approval_settings(mode=self.approval_mode,
                            expected_revision=self.service.approval.read()["revision"],
                            confirm_full_access=self.confirm_full_access)
                    self._consent_initialized = True
                path = self.service.state_dir / "mcp_calls" / (hashlib.sha256(call.id.encode()).hexdigest() + ".json")
                is_cursor_read = bool(arguments.get('cursor'))
                if not is_cursor_read and path.is_file():
                    saved = read_json(path)
                    if saved["input_hash"] != digest:
                        raise ConflictError("MCP call ID was already used with other arguments")
                    envelope = saved["response"]
                elif call.name in SESSION_TOOL_NAMES:
                    with self.service.lock.thread_lock:
                        if getattr(self, 'before_write', None):
                            self.before_write()
                        selected = (self.service.projects.tool_context(self._require_project(), self.version_id, requirement='model')
                                    if call.name == 'get_design_context' else None)
                        if call.defer_read and selected is not None:
                            from dataclasses import replace
                            selected = replace(selected, defer_read=True)
                        envelope = self.registry.call(call.name, arguments, selected, defer_read=call.defer_read)
                        if not is_cursor_read:
                            atomic_json(path, {"input_hash": digest, "response": envelope})
                else:
                    response = self.service.agent_call({"project_id": self._require_project(),
                        "version_id": self.version_id, "name": call.name,
                        "arguments": arguments, "call_id": call.id,
                        **({'defer_read': True} if call.defer_read else {}),
                        **({'change_scope': self.change_scope} if getattr(self, 'change_scope', None) else {}),
                        **({'program': self.program} if getattr(self, 'program', None) else {})},
                        direct_request=True, observe_client=False, before_write=getattr(self, 'before_write', None),
                        **({'direct_input': self.direct_input} if getattr(self, 'direct_input', None) is not None else {}))
                    # Wait only for this operation's real job; retain its worker
                    # and apartment-bound native sessions for the next call.
                    if response.get('execution_job_id'):
                        self.service.jobs.wait(response['execution_job_id'])
                    envelope = self._completed_response(response)
                    if not is_cursor_read:
                        atomic_json(path, {"input_hash": digest, "response": envelope})
                if not call.defer_read and call.name == 'get_design_context' and envelope.get('ok') and self.design.separate_tasks:
                    delivery = (envelope.get('data') or {}).get('delivery') or {}
                    store = self.registry._read_delivery
                    if store is not None and delivery.get('complete'):
                        analysis_id = store.completed_design(delivery['document_id'])
                        if analysis_id:
                            with self.service.lock.thread_lock:
                                self.design.mark_read_complete(self.project_id, analysis_id,
                                    citations=store.design_citations(analysis_id))
                if not call.defer_read and call.name == 'search_plc_manual' and envelope.get('ok'):
                    delivery = (envelope.get('data') or {}).get('delivery') or {}
                    if delivery.get('complete') and delivery.get('document_id'):
                        self._manual_reads.setdefault(self.project_id, set()).add(delivery['document_id'])
            if call.name == 'get_design_context' and envelope.get('ok'):
                from application.read_delivery import PreparedReadEnvelope
                envelope = PreparedReadEnvelope(envelope)
            public = public_tool_result_data(ToolResult(call.id, call.name, "", envelope))
            return ToolResult(call.id, call.name, json.dumps(public, ensure_ascii=False), public, is_error=not bool(public.get("ok")))
        except WorkspaceBusyError:
            return self._error(call, "WORKSPACE_BUSY", "Another operation owns this workspace. Retry after it finishes or configure a separate --local --workspace.")
        except ConflictError:
            return self._error(call, "CONFLICT", "The call or saved engineering inputs changed; reload the selected context")
        except ToolPreconditionError as error:
            envelope = {"ok": False, "tool": call.name, "error": error.payload()}
            return ToolResult(call.id, call.name, json.dumps(envelope, ensure_ascii=False), envelope, is_error=True)
        except PermissionError as error:
            return self._error(call, "PERMISSION_DENIED", str(error))
        except KeyError as error:
            return self._error(call, "RESOURCE_NOT_FOUND", str(error))
        except ValueError as error:
            return self._error(call, "CONTEXT_UNAVAILABLE", str(error))

    def close(self):
        with self._call_lock:
            if not self._closed:
                self._closed = True
                if not getattr(self, '_host_owned', False):
                    self.service.close()

    shutdown = close

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _completed_response(self, response):
        envelope = copy.deepcopy(response["data"])
        proposal_id = response.get("proposal_id")
        if not proposal_id:
            return envelope
        proposal = self.service.proposals.get(proposal_id)
        envelope["proposal_id"] = proposal_id
        for key in ('execution_job_id', 'version_id'):
            if response.get(key) is not None:
                envelope[key] = response[key]
        data = envelope.setdefault("data", {})
        data.update(proposal_id=proposal_id, proposal_status=proposal["status"])
        result = proposal.get("result") or {}
        if result.get('version_id') is not None:
            envelope['version_id'] = result['version_id']
        if proposal['action'] in {'spec_update', 'spec_confirm'}:
            envelope['status'] = 'saved' if proposal['status'] == 'accepted' else proposal['status']
            if proposal['status'] == 'accepted':
                data.clear()
                data.update(self.service.project_commands.get_spec_document(self._require_project(), self.version_id))
                data.update(proposal_id=proposal_id, proposal_status='accepted', requires_confirmation=False)
            elif proposal['status'] == 'pending':
                data['message'] = 'Specification change awaits approval in the workbench.'
            else:
                envelope['ok'] = False
            return envelope
        if proposal["action"] == "accept_local" and proposal["status"] == "accepted":
            status = result.get('status')
            envelope["status"] = status if status in {'saved_native_diagnostic', 'saved_behavior_draft'} else 'saved'
            data.update(requires_confirmation=False, version_id=result["version_id"], save_result=result)
            saved = self.service.projects.raw_version(self.project_id, result['version_id'])
            data.update(delivery=saved.get('delivery'), validation=saved.get('validation'))
            for key in ('program_size', 'behavior_result', 'behavior_check'):
                if key in saved:
                    data[key] = copy.deepcopy(saved[key])
            data['activation_blocked'] = bool(saved.get('activation_blocked'))
            data['artifacts'] = self._version_artifacts(self.project_id, result['version_id'])['artifacts']
            data["message"] = {
                'saved_native_diagnostic': "Candidate saved with native compilation diagnostics. Activation is blocked.",
                'saved_behavior_draft': "Candidate saved with validation findings. Activation is blocked.",
            }.get(status, "Local version saved. See validation and behavior results for the checks performed.")
            if saved.get('target_mode') == 'ladder':
                data['continuation'] = {
                    'version_id': result['version_id'],
                    'tool': 'get_generation_context', 'arguments': {},
                    'instructions': 'For a correction, read the saved version edit context with only the change request. '
                                    'Reuse previously read manuals; do not resubmit the original generation receipt. '
                                    'Canonical ladder edits retain compact syntax and accept partial rungs.'}
            data.pop("pending_action", None)
        elif proposal["status"] == "pending":
            envelope["status"] = "execution_consent_required"
            data["message"] = "This native engineering operation requires operator startup consent: --approval-mode full --confirm-full-access. Submit a new operation call after configuration; existing pending actions are not executed automatically."
        else:
            envelope["status"] = proposal["status"]
            data.update(requires_confirmation=False, execution_result=result)
            data.pop("pending_action", None)
            if proposal["status"] != "accepted":
                envelope["ok"] = False
                envelope["error"] = {"code": proposal.get("error_code") or "EXECUTION_FAILED",
                                     "message": "The native operation did not complete successfully; inspect execution_result."}
        return envelope
