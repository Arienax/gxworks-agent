"""Operator-only product onboarding routes for local MCP clients.

This module also hosts the small deterministic saved-IR export endpoint because
it is registered from the same application bootstrap hook and requires no new
model/job plumbing.
"""
from __future__ import annotations

from fastapi.responses import Response

from application.projects import public
from application.mcp_integrations import MCPIntegrationError
from . import responses as dto
from .schemas import MCPIntegrationCommand


def _connection_result(value, origin: str) -> dto.MCPIntegrationResult:
    # Project explicit public fields: generic credential/path redaction cannot
    # distinguish readiness flags and HTTP origins from secrets and file paths.
    return dto.MCPIntegrationResult(
        status=value["status"],
        project_id=value["project_id"],
        message=public(value["message"]),
        service_url=origin,
        tool_count=value["tool_count"],
        codex_connected=value.get("codex_connected"),
        replaced_existing=value.get("replaced_existing"),
    )


def register_mcp_routes(app, service, security) -> None:
    @app.get("/api/integrations/mcp", response_model=dto.MCPIntegrationStatus)
    def mcp_status(project_id: str):
        service.projects.project(project_id)
        from application.mcp_integrations import status
        value = status(project_id, security.origin)
        activity = service.mcp_activity(project_id)
        return dto.MCPIntegrationStatus(
            service_url=security.origin,
            project_id=value["project_id"],
            bound_project_id=value["bound_project_id"],
            credential_ready=value["credential_ready"],
            launcher_ready=value["launcher_ready"],
            codex_configured=value["codex_configured"],
            codex_cli_available=value["codex_cli_available"],
            codex_command=public(value["codex_command"]),
            client_observed=activity["client_observed"],
            last_tool=public(activity["last_tool"]),
            last_call_at=activity["last_call_at"],
            generation_context_observed=activity["generation_context_observed"],
            candidate_proposal_id=public(activity["candidate_proposal_id"]),
        )

    @app.post("/api/integrations/mcp/test", response_model=dto.MCPIntegrationResult,
              response_model_exclude_none=True)
    def mcp_test(command: MCPIntegrationCommand):
        service.writable()
        service.projects.project(command.project_id)
        from application.mcp_integrations import test_connection
        try:
            return _connection_result(test_connection(command.project_id, security.origin), security.origin)
        except MCPIntegrationError as error:
            return {"status": "failed", "message": public(str(error)), "project_id": command.project_id}

    @app.post("/api/integrations/mcp/codex/connect", response_model=dto.MCPIntegrationResult,
              response_model_exclude_none=True)
    def mcp_connect_codex(command: MCPIntegrationCommand):
        service.writable()
        service.projects.project(command.project_id)
        from application.mcp_integrations import connect_codex
        try:
            return _connection_result(connect_codex(command.project_id, security.origin), security.origin)
        except MCPIntegrationError as error:
            return {
                "status": "failed",
                "codex_connected": False,
                "message": public(str(error)),
                "project_id": command.project_id,
            }

    @app.get("/api/projects/{project_id}/versions/{version_id}/exports/gxworks2-csv")
    def fresh_gxworks2_csv(project_id: str, version_id: str):
        """Download a fresh CSV pair from the saved IR without calling a model."""
        from application.fresh_exports import build_gxworks2_csv_bundle

        data = build_gxworks2_csv_bundle(service.projects, project_id, version_id)
        filename = f"gxworks2-csv-{version_id}.zip"
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
