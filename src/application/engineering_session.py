# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

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
from application.workspace import (
    ConflictError, WorkspaceBusyError, atomic_json, canonical_hash,
    default_state_dir, read_json, record_id,
)
from storage.session import default_workspace_dir


SESSION_TOOL_NAMES = (
    "list_projects", "create_project", "select_project", "import_gxw_project",
    "get_project_artifacts", "get_operation_status",
)


def default_mcp_workspace() -> Path:
    override = os.environ.get("GXWORKS_MCP_WORKSPACE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else default_workspace_dir().parent / "mcp-workspace"


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
        self.service = WorkbenchService(self.workspace, state_dir,
                                        settings=_ExternalClientSettings())
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
        self.inspection = self.service.projects.inspection

    def _session_registry(self):
        registry = ToolRegistry()
        definitions = (
            ("list_projects", "List saved PLC projects and this MCP session's explicit selection.", {}, (), self._list_projects),
            ("create_project", "Create and select a PLC project using the shared project service; specify the actual CPU and source type.",
             {"name": {"type": "string", "minLength": 1, "maxLength": 200},
              "plc_model": {"type": "string", "minLength": 1, "maxLength": 100},
              "target_mode": {"type": "string", "minLength": 1, "maxLength": 32},
              "native_project_type": {"type": "string", "minLength": 1, "maxLength": 32},
              "use_labels": {"type": "boolean"}},
             ("name", "plc_model", "target_mode"), self._create_project),
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
        project = self.service.create_project(**arguments)
        self.project_id, self.version_id = project["id"], None
        return {"status": "created", "project": project, "selected_project_id": self.project_id}

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
        artifacts = self.service.projects.artifacts(project_id, context.version_id)
        for artifact in artifacts:
            if artifact["available"]:
                artifact["path"] = str(self.service.projects.artifact(project_id, context.version_id, artifact["id"]))
        return {"project_id": project_id, "version_id": context.version_id, "artifacts": artifacts}

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
            digest = canonical_hash({"name": call.name, "arguments": arguments})
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
                # Inspection never initializes a writer, migrates projects, or
                # inherits an execution approval merely to read a source.
                if call.name in READ_ONLY_TOOL_NAMES and not arguments.get("native"):
                    from dataclasses import replace
                    independent = (call.name in DISCOVERY_TOOL_NAMES - {"get_current_project"}
                                   or bool(arguments.get("source_ref")))
                    context = (build_tool_context({}) if independent or not self.project_id else
                               self.service.projects.invocation_context(self.project_id, self.version_id, call.name))
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
                self.service.start()
                if not self._consent_initialized:
                    if self.approval_mode is not None:
                        self.service.update_approval_settings(mode=self.approval_mode,
                            expected_revision=self.service.approval.read()["revision"],
                            confirm_full_access=self.confirm_full_access)
                    self._consent_initialized = True
                path = self.service.state_dir / "mcp_calls" / (hashlib.sha256(call.id.encode()).hexdigest() + ".json")
                if path.is_file():
                    saved = read_json(path)
                    if saved["input_hash"] != digest:
                        raise ConflictError("MCP call ID was already used with other arguments")
                    envelope = saved["response"]
                elif call.name in SESSION_TOOL_NAMES:
                    with self.service.lock.thread_lock:
                        envelope = self.registry.call(call.name, arguments, None)
                        atomic_json(path, {"input_hash": digest, "response": envelope})
                else:
                    response = self.service.agent_call({"project_id": self._require_project(),
                        "version_id": self.version_id, "name": call.name,
                        "arguments": arguments, "call_id": call.id}, direct_request=True, observe_client=False)
                    # Wait only for this operation's real job; retain its worker
                    # and apartment-bound native sessions for the next call.
                    if response.get('execution_job_id'):
                        self.service.jobs.wait(response['execution_job_id'])
                    envelope = self._completed_response(response)
                    atomic_json(path, {"input_hash": digest, "response": envelope})
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
        if proposal["action"] == "accept_local" and proposal["status"] == "accepted":
            envelope["status"] = "saved"
            data.update(requires_confirmation=False, version_id=result["version_id"], save_result=result)
            data["message"] = "Validated local version saved by the shared application transaction."
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
