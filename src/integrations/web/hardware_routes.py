# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Workbench physical read routes with version-bound authorization."""
from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import Field

from application.hardware import HardwareService, HardwareError
from application.projects import public
from .responses import HardwareState, HardwareSession, HardwareObservation
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
    def hardware():
        # The workbench owns this companion so it is closed with the workspace.
        bound = service.current() if hasattr(service, "current") else service
        value = getattr(bound, "hardware", None)
        if value is None:
            value = HardwareService(bound)
            bound.hardware = value
        return value

    @app.exception_handler(HardwareError)
    async def hardware_error(_request, error):
        return JSONResponse({"error": {"code": "hardware_read_rejected", "message": public(str(error))}}, status_code=400)

    base = "/api/projects/{project_id}/versions/{version_id}/hardware"

    @app.get(base, response_model=HardwareState, response_model_exclude_unset=True)
    def status(project_id: str, version_id: str):
        return hardware().list(project_id, version_id)

    @app.post(base + "/sessions", status_code=201, response_model=HardwareSession, response_model_exclude_unset=True)
    def propose(project_id: str, version_id: str, command: HardwareProposal):
        return hardware().propose(project_id, version_id, command.model_dump())

    @app.post(base + "/sessions/{session_id}/approve", response_model=HardwareSession, response_model_exclude_unset=True)
    def approve(project_id: str, version_id: str, session_id: str, command: HardwareApproval):
        return hardware().approve(project_id, version_id, session_id, command.model_dump())

    @app.post(base + "/sessions/{session_id}/read", response_model=HardwareObservation, response_model_exclude_unset=True)
    def read(project_id: str, version_id: str, session_id: str, command: HardwareRead):
        return hardware().read(project_id, version_id, session_id, command.model_dump())

    @app.post(base + "/sessions/{session_id}/revoke", response_model=HardwareSession, response_model_exclude_unset=True)
    def revoke(project_id: str, version_id: str, session_id: str):
        return hardware().revoke(project_id, version_id, session_id)
