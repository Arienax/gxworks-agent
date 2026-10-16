"""Operator-only physical read routes; never registered in the Agent runtime."""
import hashlib
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import Field

from application.hardware import HardwareService, HardwareError
from application.projects import public
from .responses import PublicObject
from .schemas import Command


class HardwareProposal(Command):
    logical_station: int = Field(ge=0, le=1023, strict=True)
    target_label: str = Field(min_length=1, max_length=160)
    addresses: list[str] = Field(min_length=1, max_length=64)
    ttl_seconds: int = Field(default=60, ge=30, le=300, strict=True)


class HardwareApproval(HardwareProposal):
    mapping_confirmed: Literal[True]


class HardwareRead(Command):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    addresses: list[str] = Field(min_length=1, max_length=64)


def register(app, service):
    hardware = getattr(service, "hardware", None) or HardwareService(service)
    service.hardware = hardware

    def owner(request):
        if getattr(request.state, "role", None) != "operator" or not app.state.security.session(request):
            raise PermissionError("Hardware access requires an operator session")
        return hashlib.sha256(request.cookies["gx_operator"].encode()).hexdigest()

    @app.exception_handler(HardwareError)
    async def hardware_error(_request, error):
        return JSONResponse({"error": {"code": "hardware_read_rejected", "message": public(str(error))}}, status_code=400)

    base = "/api/projects/{project_id}/versions/{version_id}/hardware"

    @app.get(base, response_model=PublicObject)
    def status(project_id: str, version_id: str, request: Request):
        return hardware.list(project_id, version_id, owner(request))

    @app.post(base + "/sessions", status_code=201, response_model=PublicObject)
    def propose(project_id: str, version_id: str, command: HardwareProposal, request: Request):
        return hardware.propose(project_id, version_id, owner(request), command.model_dump())

    @app.post(base + "/sessions/{session_id}/approve", response_model=PublicObject)
    def approve(project_id: str, version_id: str, session_id: str, command: HardwareApproval, request: Request):
        return hardware.approve(project_id, version_id, session_id, owner(request), command.model_dump())

    @app.post(base + "/sessions/{session_id}/read", response_model=PublicObject)
    def read(project_id: str, version_id: str, session_id: str, command: HardwareRead, request: Request):
        return hardware.read(project_id, version_id, session_id, owner(request), command.model_dump())

    @app.post(base + "/sessions/{session_id}/revoke", response_model=PublicObject)
    def revoke(project_id: str, version_id: str, session_id: str, request: Request):
        return hardware.revoke(project_id, version_id, session_id, owner(request))
