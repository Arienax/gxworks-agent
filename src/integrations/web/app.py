# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Local HTTP/SSE transport. Domain operations belong to application services."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from application.project_commands import SpecConfirmationError
from application.projects import media_type, public
from application.workbench import WorkbenchService, sfc_requirement, ChangeScopeError
from application.fbd import FBDValidationError
from application.execution import ExecutionUnavailableError
from application.workspace import ConflictError, default_state_dir
from shared.paths import local_data_dir
from application.settings import ModelConfigurationRequiredError
from .security import LocalSecurity
from .mcp_routes import register_mcp_routes
from .workspaces import (ServiceHolder, WorkspaceNotSelected, WorkspacePathError,
                         WorkspaceRegistry, create_directory, list_directory,
                         pick_native_directory, summarize_workspace, workspace_id,
                         workspace_snapshot)
from . import responses as dto
from .spec_contracts import SpecDocument
from .schemas import SpecDocumentUpdate, SpecDocumentConfirm
from .schemas import (ProjectCreate, ProjectUpdate, ActivateVersion, SpecUpdate, SpecIORowEdit, SpecPresentation,
                      JobCreate, GenerationRepair, ProposalDecision, ExecutionProposal, AgentCall, AgentReadDelivery,
                      SettingsUpdate, ApprovalSettingsUpdate, ModelProfileCreate, ModelKeyUpdate, ModelConnectionTest, ModelDiscoveryRequest, ModelVerificationRequest,
                      AttachmentUpload, GXWInspectionCommand, GXWImportCommand, GXWEditCommand,
                      SFCInput, SFCProposal, SFCPreview, FBDProposal, ModelRequestPreview,
                      WorkspaceSelection, WorkspaceDirectoryCreate)


def _default_registry_root() -> Path:
    """Where the workspace list lives when no state directory was configured."""
    return local_data_dir()


def create_app(workspace=None, *, state_dir=None, read_only=False, origin="http://127.0.0.1:8765",
               agent_token=None, service=None, static_dir=None):
    security = LocalSecurity(origin, agent_token)
    # The workspace can be chosen or replaced from the browser, so every route
    # keeps one indirection instead of closing over a single bound service.
    holder = ServiceHolder(read_only=read_only)
    binding_changes = RLock()
    registry_root = Path(state_dir) if state_dir is not None else _default_registry_root()
    legacy_registry = local_data_dir("GXWorks Agent") / "workspaces.json" if state_dir is None else None
    workspaces = WorkspaceRegistry(registry_root / "workspaces.json", legacy_path=legacy_registry)
    provided_service = service
    if provided_service is not None:
        holder.bind(provided_service)
    service = holder

    def _workspace_state(resolved: Path) -> Path:
        # Application state belongs to one workspace; a shared state root gets one
        # child per workspace instead of being claimed by two of them.
        return (default_state_dir(resolved) if state_dir is None else
                Path(state_dir) / workspace_id(resolved))

    def _attach_public_read_validator(bound) -> None:
        registry = getattr(bound.projects.runtime, 'registry', None)
        if registry is not None:
            registry.public_read_validator = dto.validate_public_read_document

    def bind_workspace(target: Path, *, remember: bool = False) -> None:
        """Open the target successfully before replacing the current binding."""
        with binding_changes:
            resolved = Path(target).expanduser().resolve()
            previous = holder.current()
            if previous is not None and previous.store.base_dir.resolve() == resolved:
                if remember:
                    workspaces.remember(resolved)
                return
            if resolved.exists() and not resolved.is_dir():
                raise ValueError("工作区必须是文件夹。")
            if (resolved / "project.json").is_file():
                raise ValueError("选中的是单个工程目录；请选择包含 index.json 和 projects 的工作区外层目录。")
            resolved.mkdir(parents=True, exist_ok=True)
            bound = WorkbenchService(resolved, _workspace_state(resolved), read_only=read_only)
            try:
                bound.start()
                _attach_public_read_validator(bound)
                if remember:
                    workspaces.remember(resolved)
            except BaseException:
                bound.close()
                raise
            holder.bind(bound)
            if previous is not None:
                previous.close()

    def bound_workspace_id() -> str | None:
        bound = holder.current()
        return workspace_id(bound.store.base_dir) if bound is not None else None

    def workspace_index() -> dict:
        active = bound_workspace_id()
        rows = [summarize_workspace(row, active=row["id"] == active) for row in workspaces.entries()]
        return {"active": active, "workspaces": rows}

    def activate_registered(identifier: str) -> dict:
        row = workspaces.find(identifier)
        if row is None:
            raise KeyError(identifier)
        bind_workspace(Path(row["path"]), remember=True)
        return workspace_index()

    def register_and_activate(path: str | Path) -> dict:
        bind_workspace(Path(path), remember=True)
        return workspace_index()

    # Restore the workspace this workbench last served, so the page opens on it.
    if provided_service is None:
        if workspace is not None:
            register_and_activate(workspace)
        else:
            remembered = workspaces.active()
            if remembered is not None:
                row = workspaces.find(remembered)
                if row is not None and Path(row["path"]).is_dir():
                    bind_workspace(Path(row["path"]))

    if holder.current() is not None:
        _attach_public_read_validator(holder.current())

    @asynccontextmanager
    async def lifespan(_app):
        bound = holder.current()
        if bound is not None:
            bound.start()
        try:
            yield
        finally:
            bound = holder.current()
            if bound is not None:
                await asyncio.to_thread(bound.close)

    app = FastAPI(title="GXWorks Agent Local Workbench", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.middleware("http")(security.middleware)
    register_mcp_routes(app, service, security)

    @app.exception_handler(Exception)
    async def internal_error(_request, _error):
        return JSONResponse({"error": {"code": "internal_error", "message": "操作未完成，请检查后端日志。"}}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def invalid_schema(_request, _error):
        # Pydantic's default response includes submitted inputs, possibly secrets.
        return JSONResponse({"error": {"code": "invalid_command", "message": "请求字段不符合接口要求。"}}, status_code=422)

    @app.exception_handler(ExecutionUnavailableError)
    async def execution_unavailable(_request, error):
        return JSONResponse(
            {"error": {"code": "execution_unavailable", "message": public(str(error))}},
            status_code=409,
        )

    @app.exception_handler(KeyError)
    async def missing(_request, _error):
        return JSONResponse({"error": {"code": "not_found", "message": "工程资源不存在或尚未生成。"}}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request, _error):
        return JSONResponse({"error": {"code": "invalid_request", "message": "输入或工程状态无效，请检查所选版本及设置。"}}, status_code=400)

    @app.exception_handler(SpecConfirmationError)
    async def invalid_spec_confirmation(_request, error):
        return JSONResponse({"error": {"code": "invalid_spec_confirmation", "message": public(str(error))}}, status_code=400)

    @app.exception_handler(ModelConfigurationRequiredError)
    async def model_configuration_required(_request, _error):
        return JSONResponse({"error": {"code": "model_configuration_required", "message": "尚未配置模型，请在模型 API 设置中新建配置。"}}, status_code=400)

    @app.exception_handler(FBDValidationError)
    async def invalid_fbd(_request, error):
        return JSONResponse({"error": {"code": "invalid_fbd", "message": public(str(error))}}, status_code=400)

    @app.exception_handler(ChangeScopeError)
    async def invalid_scope(_request, error):
        return JSONResponse({"error": {"code": "change_scope_violation", "message": public(str(error))}}, status_code=400)

    @app.exception_handler(ConflictError)
    async def conflict(_request, _error):
        return JSONResponse({"error": {"code": "conflict", "message": "工程或提案状态已变化，请刷新后重新检查。"}}, status_code=409)

    @app.exception_handler(PermissionError)
    async def forbidden(_request, error):
        if service.read_only:
            code, message = "read_only_workspace", "工作台以只读模式打开。"
        else:
            code = "forbidden"
            message = ("无法访问所需文件，请检查工作区目录的读写权限。"
                       if error.errno is not None else public(str(error)))
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=403)

    @app.exception_handler(WorkspaceNotSelected)
    async def workspace_required(_request, error):
        return JSONResponse(error.payload(), status_code=428)

    @app.exception_handler(WorkspacePathError)
    async def workspace_path_invalid(_request, error):
        return JSONResponse(error.payload(), status_code=400)

    @app.get("/api/health", response_model=dto.Health)
    def health():
        return {"status": "ok", "application": "gxworks-agent"}

    @app.get("/api/workspace", response_model=dto.WorkspaceState)
    def workspace_state():
        bound = holder.current()
        return {**workspace_snapshot(str(bound.store.base_dir) if bound is not None else None),
                "read_only": holder.read_only}

    @app.get("/api/workspaces", response_model=dto.WorkspaceIndex)
    def list_workspaces():
        """Every workspace this workbench serves, each with the projects it owns."""
        return workspace_index()

    @app.post("/api/workspaces", response_model=dto.WorkspaceIndex)
    def add_workspace(command: WorkspaceSelection):
        return register_and_activate(command.path)

    @app.post("/api/workspaces/pick", response_model=dto.WorkspaceIndex)
    def pick_workspace():
        """Open the system folder chooser, then add and activate that workspace."""
        chosen = pick_native_directory()
        if chosen is None:
            return {**workspace_index(), "cancelled": True}
        return register_and_activate(chosen)

    @app.post("/api/workspaces/{workspace_id_value}/activate", response_model=dto.WorkspaceIndex)
    def activate_workspace(workspace_id_value: str):
        return activate_registered(workspace_id_value)

    @app.delete("/api/workspaces/{workspace_id_value}", response_model=dto.WorkspaceIndex)
    def forget_workspace(workspace_id_value: str):
        """Remove one workspace from the list; its folder and projects stay on disk."""
        with binding_changes:
            rows = workspaces.entries()
            remaining = [row for row in rows if row["id"] != workspace_id_value]
            if len(remaining) == len(rows):
                raise KeyError(workspace_id_value)
            if bound_workspace_id() == workspace_id_value:
                if remaining:
                    bind_workspace(Path(remaining[0]["path"]), remember=True)
                else:
                    holder.release()
            workspaces.remove(workspace_id_value)
            return workspace_index()

    @app.get("/api/workspace/browse", response_model=dto.WorkspaceListing)
    def browse_workspace(path: str | None = Query(default=None, max_length=4096)):
        return list_directory(path)

    @app.post("/api/workspace/directory", response_model=dto.WorkspaceDirectory)
    def create_workspace_directory(command: WorkspaceDirectoryCreate):
        return {"path": create_directory(command.parent, command.name)}

    @app.get("/api/openapi.json")
    def schema():
        return app.openapi()

    @app.get("/api/capabilities", response_model=dto.Capabilities)
    def capabilities():
        return service.projects.capabilities()

    @app.get("/api/environment", response_model=dto.Environment)
    def environment():
        from application.execution import read_environment
        return public(read_environment())

    @app.post("/api/spec/presentation", response_model=dto.SpecPresentationResource, response_model_exclude_unset=True)
    def spec_presentation(command: SpecPresentation):
        return service.spec_presentation(command.spec)

    @app.post("/api/spec/io-row", response_model=dto.IORow, response_model_exclude_unset=True)
    def edit_spec_io_row(command: SpecIORowEdit):
        return service.edit_spec_io_row(**command.model_dump())

    @app.get("/api/projects", response_model=dto.ProjectList, response_model_exclude_unset=True)
    def projects():
        return {"projects": service.projects.list_projects()}

    @app.post("/api/projects", status_code=201, response_model=dto.Project, response_model_exclude_unset=True)
    def create_project(command: ProjectCreate):
        return service.create_project(**command.model_dump())

    @app.get("/api/fbd/catalog", response_model=dto.FBDCatalog, response_model_exclude_unset=True)
    def fbd_catalog(project_id: str | None = None, version_id: str | None = None, program: str | None = None):
        return service.fbd.catalog(project_id, version_id, program)

    @app.post("/api/fbd/inspect", response_model=dto.FBDInspection, response_model_exclude_unset=True)
    def fbd_inspect(command: AttachmentUpload):
        from application.fbd import inspect_upload
        return inspect_upload(command.data_base64)

    @app.post('/api/gxw/inspect', response_model=dto.GXWInspection, response_model_exclude_unset=True)
    def gxw_inspect(command: GXWInspectionCommand):
        return service.gxw_files.inspect(data_base64=command.data_base64, program=command.program)

    @app.post('/api/gxw/import', status_code=201, response_model=dto.GXWImportResult, response_model_exclude_unset=True)
    def gxw_import(command: GXWImportCommand):
        return service.gxw_files.import_project(**command.model_dump(exclude_none=True))

    @app.post('/api/gxw/edit', status_code=201, response_model=dto.Proposal, response_model_exclude_unset=True)
    def gxw_edit(command: GXWEditCommand):
        return service.gxw_files.edit_project(**command.model_dump(exclude_none=True))

    @app.get('/api/gxw/source', response_model=dto.GXWSourceView, response_model_exclude_unset=True)
    def gxw_source(project_id: str, version_id: str, program: str | None = None):
        return service.gxw_files.source(project_id, version_id, program)

    @app.post("/api/fbd/proposals", status_code=201, response_model=dto.Proposal, response_model_exclude_unset=True)
    def fbd_proposal(command: FBDProposal):
        return service.fbd.propose(command.model_dump(exclude_none=True))

    @app.get("/api/projects/{project_id}", response_model=dto.Project, response_model_exclude_unset=True)
    def project(project_id: str):
        result = service.projects.project(project_id)
        # Match canonical_sha256 used by the existing specification boundary.
        from application.workbench import public_spec_hash
        result["confirmed_spec_hash"] = public_spec_hash(result.get("confirmed_spec"))
        return result

    @app.patch("/api/projects/{project_id}", response_model=dto.Project, response_model_exclude_unset=True)
    def update_project(project_id: str, command: ProjectUpdate):
        return service.update_project(project_id, **command.model_dump(exclude_none=True))

    @app.delete("/api/projects/{project_id}", response_model=dto.DeleteProjectResult, response_model_exclude_unset=True)
    def delete_project(project_id: str):
        return service.delete_project(project_id)

    @app.post("/api/projects/{project_id}/active-version", response_model=dto.Project, response_model_exclude_unset=True)
    def activate(project_id: str, command: ActivateVersion):
        return service.activate_version(project_id, **command.model_dump())

    @app.get("/api/projects/{project_id}/spec-document", response_model=SpecDocument, response_model_exclude_unset=True)
    def spec_document(project_id: str, version_id: str | None = None):
        return service.project_commands.get_spec_document(project_id, version_id=version_id)

    @app.patch("/api/projects/{project_id}/spec-document", response_model=SpecDocument, response_model_exclude_unset=True)
    def update_spec_document(project_id: str, command: SpecDocumentUpdate):
        return service.project_commands.update_spec_document(project_id,
            changes=[change.model_dump(exclude_unset=True) for change in command.changes],
            expected_revision=command.expected_revision)

    @app.post("/api/projects/{project_id}/spec-document/confirm", response_model=SpecDocument, response_model_exclude_unset=True)
    def confirm_spec_document(project_id: str, command: SpecDocumentConfirm):
        return service.project_commands.confirm_spec_document(
            project_id, expected_revision=command.expected_revision, version_id=command.version_id, allow_selected_approach=True)

    @app.put("/api/projects/{project_id}/spec", response_model=dto.SpecResult, response_model_exclude_unset=True)
    def specification(project_id: str, command: SpecUpdate):
        return service.set_spec(project_id, **command.model_dump())

    @app.post("/api/projects/{project_id}/attachments", status_code=201, response_model=dto.AttachmentUploadResult)
    def attachment(project_id: str, command: AttachmentUpload):
        return service.upload_attachment(project_id, **command.model_dump())

    @app.get("/api/projects/{project_id}/versions/{version_id}", response_model=dto.Version, response_model_exclude_unset=True)
    def version(project_id: str, version_id: str):
        return service.projects.version(project_id, version_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/diff",
             response_model=dto.LadderProgramDiff | dto.STTextDiff | dto.FBDDiff | dto.SFCDiff | None,
             response_model_exclude_unset=True)
    def version_diff(project_id: str, version_id: str, base_version_id: str):
        return service.projects.version_diff(project_id, version_id, base_version_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/program", response_model=dto.ProgramDocument | None, response_model_exclude_unset=True)
    def program(project_id: str, version_id: str):
        return public(service.projects.program(project_id, version_id), document="program")

    @app.get("/api/projects/{project_id}/versions/{version_id}/preview", response_model=dto.VersionPreview, response_model_exclude_unset=True)
    def version_preview(project_id: str, version_id: str, theme: Literal["light", "dark"] | None = None):
        return service.version_preview(project_id, version_id, theme=theme)

    @app.get("/api/projects/{project_id}/versions/{version_id}/diagnostics", response_model=dto.DiagnosticsEnvelope, response_model_exclude_unset=True)
    def diagnostics(project_id: str, version_id: str):
        return service.projects.diagnostics(project_id, version_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/artifacts/{artifact_id}")
    def artifact(project_id: str, version_id: str, artifact_id: str, download: bool = False,
                 theme: Literal["light", "dark"] | None = None):
        path = service.projects.artifact(project_id, version_id, artifact_id)
        headers = {"Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff"}
        if theme is not None and path.suffix.lower() == ".svg":
            content = service.projects.svg_preview(project_id, version_id, artifact_id, theme=theme)
            if download:
                headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(path.name)
            return Response(content, media_type="image/svg+xml", headers=headers)
        return FileResponse(path, media_type=media_type(path), filename=path.name if download else None,
            headers=headers)

    @app.get("/api/projects/{project_id}/reports/{report_id}", response_model=dto.ReportDocument, response_model_exclude_unset=True)
    def report(project_id: str, report_id: str):
        return service.projects.report(project_id, report_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/runs/{run_id}", response_model=dto.SimulationRun, response_model_exclude_unset=True)
    def run(project_id: str, version_id: str, run_id: str):
        return service.projects.simulator_run(project_id, version_id, run_id)

    @app.get("/api/jobs", response_model=dto.JobList, response_model_exclude_unset=True)
    def jobs(project_id: str | None = None):
        return {"jobs": service.jobs.list(project_id) if service.jobs else []}

    @app.post("/api/jobs", status_code=202, response_model=dto.Job, response_model_exclude_unset=True)
    def submit(command: JobCreate, request: Request):
        payload = command.model_dump()
        if command.clarification_job_id:
            # Omission means resume the parent's binding, not the schema's
            # default mode/version or the currently selected UI program.
            for field in ('version_id', 'program', 'change_scope', 'analysis_mode', 'generation_action'):
                if field not in command.model_fields_set:
                    payload.pop(field, None)
        if command.kind in {"generation", "direct_generation"}:
            experiment_headers = {
                "construction_examples": request.headers.get("X-GX-Construction-Examples"),
                "fresh_confirmed_generation": request.headers.get("X-GX-Fresh-Confirmed-Generation"),
            }
            for field, value in experiment_headers.items():
                if value is None:
                    continue
                if value not in {"0", "1"}:
                    raise ValueError("Invalid developer generation setting")
                payload[field] = value == "1"
        return service.submit(payload)

    @app.get("/api/jobs/{job_id}", response_model=dto.Job, response_model_exclude_unset=True)
    def job(job_id: str):
        if not service.jobs:
            raise KeyError(job_id)
        return service.jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/output", response_model=dto.JobOutput, response_model_exclude_unset=True)
    def output(job_id: str):
        return service.output(job_id)

    @app.get("/api/jobs/{job_id}/preview", response_model=dto.GenerationPreview, response_model_exclude_unset=True)
    def generation_preview(job_id: str, theme: Literal["light", "dark"] | None = None):
        return service.generation_preview(job_id, theme=theme)

    @app.post("/api/jobs/{job_id}/cancel", response_model=dto.Job, response_model_exclude_unset=True)
    def cancel(job_id: str):
        service.writable()
        return service.jobs.cancel(job_id)

    @app.post("/api/jobs/{job_id}/repair", status_code=202, response_model=dto.Job, response_model_exclude_unset=True)
    def repair_generation(job_id: str, command: GenerationRepair):
        return service.repair_generation(job_id, command.request_id)

    @app.get("/api/jobs/{job_id}/diagnostics")
    def job_diagnostics(job_id: str):
        if not service.jobs:
            raise KeyError(job_id)
        job = service.jobs.get(job_id)
        from shared.diagnostics import export_diagnostics
        data = export_diagnostics(service.state_dir, job)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="gxworks-diagnostics-{job["id"]}.zip"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, after: int = Query(0, ge=0)):
        jobs = service.jobs
        if not jobs:
            raise KeyError(job_id)
        jobs.get(job_id)
        header = request.headers.get("last-event-id")
        if header:
            after = max(after, int(header))
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                try:
                    batch = await asyncio.to_thread(jobs.events, job_id, cursor)
                    current = await asyncio.to_thread(jobs.get, job_id)
                except KeyError:
                    break
                for event in batch:
                    cursor = event["sequence"]
                    dto.JobEvent.model_validate(event)
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if current["status"] in ("completed", "failed", "cancelled", "interrupted") and cursor >= current["last_sequence"]:
                    break
                if not batch:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/proposals", response_model=dto.ProposalList, response_model_exclude_unset=True)
    def proposals(project_id: str | None = None):
        return {"proposals": service.proposals.list(project_id) if service.proposals else []}

    @app.post("/api/proposals", status_code=201, response_model=dto.Proposal, response_model_exclude_unset=True)
    def execution_proposal(command: ExecutionProposal):
        return service.execution_proposal(command.model_dump())

    @app.get("/api/proposals/{proposal_id}", response_model=dto.Proposal, response_model_exclude_unset=True)
    def proposal(proposal_id: str):
        if not service.proposals:
            raise KeyError(proposal_id)
        return service.proposals.get(proposal_id)

    @app.get("/api/proposals/{proposal_id}/preview", response_model=dto.ProposalPreview, response_model_exclude_unset=True)
    def preview(proposal_id: str, theme: Literal["light", "dark"] | None = None):
        if not service.proposals:
            raise KeyError(proposal_id)
        return service.proposal_preview(proposal_id, theme=theme)

    @app.post("/api/proposals/{proposal_id}/decision", response_model=dto.ProposalDecisionResult, response_model_exclude_unset=True)
    def decision(proposal_id: str, command: ProposalDecision):
        return service.decide(proposal_id, command.decision)

    @app.get("/api/settings/approval", response_model=dto.ApprovalSettings)
    def approval_settings():
        return service.approval_settings()

    @app.put("/api/settings/approval", response_model=dto.ApprovalSettings)
    def update_approval_settings(command: ApprovalSettingsUpdate):
        return service.update_approval_settings(**command.model_dump())

    @app.get("/api/settings", response_model=dto.ModelSettings)
    def settings():
        return service.settings.public_settings()

    @app.put("/api/settings", response_model=dto.ModelSettings)
    def update_settings(command: SettingsUpdate):
        service.writable()
        with service.lock.thread_lock:
            return service.settings.update(**command.model_dump(exclude_none=True))

    @app.post("/api/settings/detect", response_model=dto.ModelDiscoveryResult, response_model_exclude_none=True)
    def detect_model_profile(command: ModelDiscoveryRequest):
        service.writable()
        return service.settings.detect_profile(mode=command.mode, refresh=command.refresh,
            **command.profile.model_dump(exclude_none=True))

    @app.post("/api/settings/resolve", response_model=dto.ModelDiscoveryResult, response_model_exclude_none=True)
    def resolve_model_profile(command: ModelDiscoveryRequest):
        service.writable()
        return service.settings.detect_profile(mode="resolve", **command.profile.model_dump(exclude_none=True))

    @app.post("/api/settings/preview", response_model=dict)
    def preview_model_request(command: ModelRequestPreview):
        return service.settings.preview_request(**command.model_dump(exclude_none=True))

    @app.post("/api/settings/verify", response_model=dto.ModelDiscoveryResult, response_model_exclude_none=True)
    def verify_model_profile(command: ModelVerificationRequest):
        service.writable()
        return service.settings.verify_profile(**command.model_dump(exclude_none=True))

    @app.post("/api/settings/profiles", status_code=201, response_model=dto.ModelSettings)
    def create_model_profile(command: ModelProfileCreate):
        service.writable()
        with service.lock.thread_lock:
            return service.settings.create_profile(**command.model_dump(exclude_none=True))

    @app.delete("/api/settings/profiles/{profile_id}", response_model=dto.ModelSettings)
    def delete_model_profile(profile_id: str):
        service.writable()
        with service.lock.thread_lock:
            return service.settings.delete_profile(profile_id)

    @app.put("/api/settings/profiles/{profile_id}/key", response_model=dto.ModelSettings)
    def set_model_key(profile_id: str, command: ModelKeyUpdate):
        service.writable()
        with service.lock.thread_lock:
            return service.settings.set_key(profile_id, command.api_key)

    @app.delete("/api/settings/profiles/{profile_id}/key", response_model=dto.ModelSettings)
    def delete_model_key(profile_id: str):
        service.writable()
        with service.lock.thread_lock:
            return service.settings.delete_key(profile_id)

    @app.post("/api/settings/profiles/{profile_id}/test", response_model=dto.ModelConnectionResult,
              response_model_exclude_none=True)
    def test_model_connection(profile_id: str, command: ModelConnectionTest):
        service.writable()
        # The service freezes the selected draft/key before the bounded network
        # probe. It need not hold the engineering lock for the network request.
        return service.settings.test_connection(profile_id, **command.model_dump(exclude_none=True))

    @app.post("/api/sfc/requirement", response_model=dto.SFCRequirement)
    def sfc(command: SFCInput):
        return {"text": sfc_requirement([s.model_dump() for s in command.steps]), "capability": "requirement_input"}

    @app.get('/api/sfc/source', response_model=dto.SFCSource, response_model_exclude_unset=True)
    def sfc_source(project_id: str, version_id: str, program: str | None = None, native: bool = False):
        return service.sfc.read(project_id, version_id, program, native=native)

    @app.post('/api/sfc/preview', response_model=dto.SFCPreviewResource, response_model_exclude_unset=True)
    def sfc_preview(command: SFCPreview):
        return service.sfc.preview(**command.model_dump(exclude_none=True))

    @app.post('/api/sfc/proposals', status_code=201, response_model=dto.Proposal)
    def sfc_proposal(command: SFCProposal):
        return service.sfc.propose(command.model_dump(exclude_none=True))

    @app.get("/api/agent/tools", response_model=dto.AgentTools)
    def agent_tools():
        return {"tools": service.projects.runtime.list_tools()}

    @app.post("/api/agent/tools/call", response_model=dto.AgentToolResult, response_model_exclude_unset=True)
    def agent_call(command: AgentCall, request: Request):
        options = command.model_dump()
        if request.headers.get('x-gxworks-deferred-read') == '1':
            options['defer_read'] = True
        return service.agent_call(options)

    @app.post("/api/agent/read-delivery", include_in_schema=False)
    def finish_agent_read(command: AgentReadDelivery):
        return {"ok": service.finish_host_read(**command.model_dump())}

    from .exploration_routes import register as register_exploration
    register_exploration(app, service)
    from .simulation_routes import register as register_simulation
    register_simulation(app, service)
    from .native_validation_routes import register as register_native_validation
    register_native_validation(app, service)
    from .delivery_routes import register as register_delivery
    register_delivery(app, service)
    from .hardware_routes import register as register_hardware
    register_hardware(app, service)

    if static_dir is None:
        import sys
        static_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3])) / "web" / "dist"
    static_dir = Path(static_dir)
    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="workbench")
    original_openapi = app.openapi
    def openapi_with_events():
        return dto.document_sse_events(original_openapi())
    app.openapi = openapi_with_events
    return app
