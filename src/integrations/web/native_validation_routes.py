"""Transport for draft previews and operator-reported native evidence."""
from typing import Any, Literal

from fastapi.responses import FileResponse
from pydantic import Field

from application.native_validation import NativeValidationService
from .schemas import AttachmentUpload, Command
from .responses import PublicObject


class FBDPreview(Command):
    project_id: str
    version_id: str | None = None
    model: dict[str, Any]


class FBDDraftEdit(Command):
    project_id: str
    version_id: str | None = None
    model: dict[str, Any] | None = None
    command: dict[str, Any] = Field(default_factory=dict)


class SpecIORowEdit(Command):
    row: dict[str, Any] | None = None
    address: str | None = None


class NativeValidationRecord(Command):
    request_id: str = Field(min_length=1, max_length=128)
    source_gxw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator: str = Field(min_length=1, max_length=160)
    tool_version: str = Field(min_length=1, max_length=160)
    outcome: Literal["passed", "failed", "inconclusive"]
    report: str = Field(min_length=1, max_length=64000)
    attested: Literal[True]
    native_gxw: AttachmentUpload | None = None


def register(app, service):
    native = NativeValidationService(service)

    @app.post("/api/spec/io-row", response_model=PublicObject)
    def edit_spec_io_row(command: SpecIORowEdit):
        from plc.specification.confirmed import edit_io_row
        return edit_io_row(command.row, command.address)

    @app.post("/api/fbd/editor", response_model=PublicObject)
    def edit_draft(command: FBDDraftEdit):
        return service.fbd.editor(**command.model_dump())

    @app.post("/api/fbd/preview", response_model=PublicObject)
    def preview(command: FBDPreview):
        return service.fbd.preview(**command.model_dump())

    @app.get("/api/projects/{project_id}/versions/{version_id}/native-validation", response_model=PublicObject)
    def records(project_id: str, version_id: str):
        return native.list(project_id, version_id)

    @app.post("/api/projects/{project_id}/versions/{version_id}/native-validation", status_code=201, response_model=PublicObject)
    def record(project_id: str, version_id: str, command: NativeValidationRecord):
        return native.record(project_id, version_id, command.model_dump(exclude_none=True))

    @app.get("/api/projects/{project_id}/versions/{version_id}/native-validation/{evidence_id}/gxw")
    def artifact(project_id: str, version_id: str, evidence_id: str):
        path = native.artifact(project_id, version_id, evidence_id)
        return FileResponse(path, media_type="application/octet-stream", filename="native-evidence.gxw",
                            headers={"X-Content-Type-Options": "nosniff"})
