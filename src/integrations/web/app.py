"""Local HTTP/SSE transport. Domain operations belong to application services."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from application.projects import media_type, public
from application.workbench import WorkbenchService, sfc_requirement, ChangeScopeError
from application.fbd import FBDValidationError
from application.execution import ExecutionUnavailableError
from application.workspace import ConflictError, WorkspaceBusyError
from application.settings import ModelConfigurationRequiredError
from .security import LocalSecurity
from .mcp_routes import register_mcp_routes
from . import responses as dto
from .schemas import (Login, ProjectCreate, ProjectUpdate, ActivateVersion, SpecUpdate,
                      JobCreate, GenerationRepair, ProposalDecision, ExecutionProposal, AgentCall,
                      SettingsUpdate, ApprovalSettingsUpdate, ModelProfileCreate, ModelKeyUpdate, ModelConnectionTest, ModelDiscoveryRequest, ModelVerificationRequest,
                      AttachmentUpload, SFCInput, FBDProposal)


def default_state_dir(workspace):
    workspace = Path(workspace).expanduser().resolve()
    key = hashlib.sha256(os.path.normcase(str(workspace)).encode()).hexdigest()[:20]
    return workspace.parent / ".gxworks-state" / key


def create_app(workspace, *, state_dir=None, read_only=False, origin="http://127.0.0.1:8765",
               operator_token=None, agent_token=None, service=None, static_dir=None):
    security = LocalSecurity(origin, operator_token or secrets.token_urlsafe(32), agent_token)
    service = service or WorkbenchService(workspace, state_dir or default_state_dir(workspace), read_only=read_only)

    @asynccontextmanager
    async def lifespan(_app):
        service.start()
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(title="GXWorks Agent Local Workbench", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.security = service, security
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
    async def forbidden(_request, _error):
        return JSONResponse({"error": {"code": "forbidden", "message": "此操作需要有效的操作员权限及可写工作区。"}}, status_code=403)

    @app.get("/api/health", response_model=dto.Health)
    def health():
        return {"status": "ok", "application": "gxworks-agent"}

    @app.post("/api/session", response_model=dto.Session)
    def login(command: Login):
        key, csrf = security.login(command.token)
        response = JSONResponse({"authenticated": True, "csrf": csrf, "read_only": service.read_only})
        response.set_cookie("gx_operator", key, httponly=True, samesite="strict", max_age=12 * 3600, path="/")
        return response

    @app.get("/api/session", response_model=dto.Session)
    def session(request: Request):
        active = security.session(request)
        if not active:
            return JSONResponse({"authenticated": False}, status_code=401)
        return {"authenticated": True, "csrf": active.csrf, "read_only": service.read_only}

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

    @app.get("/api/projects", response_model=dto.ProjectList, response_model_exclude_unset=True)
    def projects():
        return {"projects": service.projects.list_projects()}

    @app.post("/api/projects", status_code=201, response_model=dto.Project, response_model_exclude_unset=True)
    def create_project(command: ProjectCreate):
        return service.create_project(**command.model_dump())

    @app.get("/api/fbd/catalog", response_model=dto.PublicObject)
    def fbd_catalog():
        from application.fbd import catalog_description
        return {"nodes": catalog_description(), "generation_plc_models": ["FX3U"]}

    @app.post("/api/fbd/inspect", response_model=dto.PublicObject)
    def fbd_inspect(command: AttachmentUpload):
        from application.fbd import inspect_upload
        return inspect_upload(command.data_base64)

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

    @app.delete("/api/projects/{project_id}", response_model=dto.PublicObject)
    def delete_project(project_id: str):
        return service.delete_project(project_id)

    @app.post("/api/projects/{project_id}/active-version", response_model=dto.Project, response_model_exclude_unset=True)
    def activate(project_id: str, command: ActivateVersion):
        return service.activate_version(project_id, **command.model_dump())

    @app.put("/api/projects/{project_id}/spec", response_model=dto.SpecResult, response_model_exclude_unset=True)
    def specification(project_id: str, command: SpecUpdate):
        return service.set_spec(project_id, **command.model_dump())

    @app.post("/api/projects/{project_id}/attachments", status_code=201, response_model=dto.AttachmentUploadResult)
    def attachment(project_id: str, command: AttachmentUpload):
        return service.upload_attachment(project_id, **command.model_dump())

    @app.get("/api/projects/{project_id}/versions/{version_id}", response_model=dto.Version, response_model_exclude_unset=True)
    def version(project_id: str, version_id: str):
        return service.projects.version(project_id, version_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/program", response_model=dto.NullablePublicObject)
    def program(project_id: str, version_id: str):
        return public(service.projects.program(project_id, version_id))

    @app.get("/api/projects/{project_id}/versions/{version_id}/preview", response_model=dto.PublicObject)
    def version_preview(project_id: str, version_id: str, theme: Literal["light", "dark"] | None = None):
        return service.version_preview(project_id, version_id, theme=theme)

    @app.get("/api/projects/{project_id}/versions/{version_id}/diagnostics", response_model=dto.PublicObject)
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

    @app.get("/api/projects/{project_id}/reports/{report_id}", response_model=dto.PublicObject)
    def report(project_id: str, report_id: str):
        return service.projects.report(project_id, report_id)

    @app.get("/api/projects/{project_id}/versions/{version_id}/runs/{run_id}", response_model=dto.PublicObject)
    def run(project_id: str, version_id: str, run_id: str):
        return service.projects.simulator_run(project_id, version_id, run_id)

    @app.get("/api/jobs", response_model=dto.JobList, response_model_exclude_unset=True)
    def jobs(project_id: str | None = None):
        return {"jobs": service.jobs.list(project_id) if service.jobs else []}

    @app.post("/api/jobs", status_code=202, response_model=dto.Job, response_model_exclude_unset=True)
    def submit(command: JobCreate):
        return service.submit(command.model_dump())

    @app.get("/api/jobs/{job_id}", response_model=dto.Job, response_model_exclude_unset=True)
    def job(job_id: str):
        if not service.jobs:
            raise KeyError(job_id)
        return service.jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/output", response_model=dto.JobOutput, response_model_exclude_unset=True)
    def output(job_id: str):
        return service.output(job_id)

    @app.get("/api/jobs/{job_id}/preview", response_model=dto.PublicObject)
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
    def job_diagnostics(job_id: str, request: Request):
        # Diagnostic exports are operator-only, not an Agent data-reading tool.
        if not security.session(request):
            raise PermissionError("Operator session required")
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
        if not service.jobs:
            raise KeyError(job_id)
        service.jobs.get(job_id)
        header = request.headers.get("last-event-id")
        if header:
            after = max(after, int(header))
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(service.jobs.events, job_id, cursor)
                for event in batch:
                    cursor = event["sequence"]
                    dto.JobEvent.model_validate(event)
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                current = await asyncio.to_thread(service.jobs.get, job_id)
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

    @app.get("/api/agent/tools", response_model=dto.AgentTools)
    def agent_tools():
        return {"tools": service.projects.runtime.list_tools()}

    @app.post("/api/agent/tools/call", response_model=dto.AgentToolResult, response_model_exclude_unset=True)
    def agent_call(command: AgentCall):
        return service.agent_call(command.model_dump())

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
