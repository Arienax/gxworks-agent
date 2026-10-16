from application.delivery import delivery_summary
from .responses import PublicObject


def register(app, service):
    @app.get("/api/projects/{project_id}/versions/{version_id}/delivery", response_model=PublicObject)
    def delivery(project_id: str, version_id: str):
        return delivery_summary(service, project_id, version_id)
