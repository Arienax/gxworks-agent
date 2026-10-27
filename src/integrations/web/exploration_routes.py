# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Navigation HTTP adapter; all projections live in the application layer."""
from typing import Literal

from application.exploration import exploration, issues, public_exploration
from .responses import Exploration, IssueList


def register(app, service):
    @app.get("/api/jobs/{job_id}/explorer", response_model=Exploration, response_model_exclude_unset=True)
    def generation_explorer(job_id: str, theme: Literal["light", "dark"] = "dark"):
        preview = service.generation_preview(job_id, theme=theme)
        if preview.get("target_mode") != "ladder" or not preview.get("program"):
            raise ValueError("This generation does not have ladder navigation")
        return public_exploration(preview["program"], theme=theme)

    @app.get("/api/projects/{project_id}/versions/{version_id}/explorer", response_model=Exploration, response_model_exclude_unset=True)
    def explorer(project_id: str, version_id: str, theme: Literal["light", "dark"] = "dark"):
        return exploration(service.projects, project_id, version_id, theme=theme)

    @app.get("/api/projects/{project_id}/versions/{version_id}/issues", response_model=IssueList, response_model_exclude_unset=True)
    def cards(project_id: str, version_id: str, report_id: str | None = None):
        return issues(service.projects, project_id, version_id, report_id)

    @app.get("/api/proposals/{proposal_id}/explorer", response_model=Exploration, response_model_exclude_unset=True)
    def candidate_explorer(proposal_id: str, theme: Literal["light", "dark"] = "dark"):
        preview = service.proposal_preview(proposal_id, theme=theme)
        if preview.get("target_mode") in ("st", "fbd") or not preview.get("program"):
            raise ValueError("This candidate does not have ladder navigation")
        return public_exploration(preview["program"], theme=theme)
