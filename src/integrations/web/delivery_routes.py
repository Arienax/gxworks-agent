# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from application.delivery import delivery_summary
from .responses import DeliverySummary


def register(app, service):
    @app.get("/api/projects/{project_id}/versions/{version_id}/delivery", response_model=DeliverySummary, response_model_exclude_unset=True)
    def delivery(project_id: str, version_id: str):
        return delivery_summary(service, project_id, version_id)
