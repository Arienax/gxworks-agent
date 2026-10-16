"""HTTP adapter for simulator plan editing and recorded replay; no execution."""
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import Field

from application.projects import public
from application.simulation_workbench import SimulationWorkbenchService, SimulationWorkbenchError
from .schemas import Command
from .responses import PublicObject


class SimulatorDraftEdit(Command):
    suite: dict[str, Any]
    command: dict[str, Any]


class SimulatorPlanSave(Command):
    suite: dict[str, Any]
    requirement_links: dict[str, list[str]] = Field(default_factory=dict)
    issue_ids: list[str] = Field(default_factory=list, max_length=200)
    source_plan_id: str | None = Field(default=None, max_length=128)
    expected_ir_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def register(app, service):
    simulation = SimulationWorkbenchService(service)

    @app.exception_handler(SimulationWorkbenchError)
    async def invalid_simulation(_request, error):
        return JSONResponse({"error": {"code": "invalid_simulation_plan", "message": public(str(error))}}, status_code=400)

    prefix = "/api/projects/{project_id}/versions/{version_id}/simulation-workbench"

    @app.get(prefix, response_model=PublicObject)
    def read_simulation_workbench(project_id: str, version_id: str):
        return simulation.read(project_id, version_id)

    @app.post(prefix + "/draft", response_model=PublicObject)
    def edit_simulation_draft(project_id: str, version_id: str, command: SimulatorDraftEdit):
        return simulation.edit_draft(project_id, version_id, **command.model_dump())

    @app.post(prefix + "/plans", status_code=201, response_model=PublicObject)
    def save_simulation_plan(project_id: str, version_id: str, command: SimulatorPlanSave):
        return simulation.save(project_id, version_id, **command.model_dump())

    @app.get(prefix + "/runs/{run_id}/replay", response_model=PublicObject)
    def simulation_replay(project_id: str, version_id: str, run_id: str):
        return simulation.replay(project_id, version_id, run_id)

    @app.get(prefix + "/live", response_model=None)
    def simulation_live(project_id: str, version_id: str, after: int = 0, view_id: str = ""):
        # Validate project/version ownership without initiating any simulation.
        service.projects.version(project_id, version_id)
        from application.simulator_view import get_simulation_views
        view = get_simulation_views().latest({"project_id": project_id, "version_id": version_id})
        return view.snapshot(after=max(0, after) if view_id == view.view_id else 0) if view else None
