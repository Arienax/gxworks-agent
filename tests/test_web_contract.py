"""Public resource schemas preserve engineering data and reject private state."""
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Web contracts require requirements-web.txt")
pytest.importorskip("httpx", reason="Web contracts require the optional HTTP test client")
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from application.workbench import WorkbenchService
from integrations.web.app import create_app
from integrations.web.responses import (
    Artifact, Capabilities, Job, JobEvent, JobList, JobOutput, ModelSettings,
    Project, ProjectList, Proposal, ProposalPreview, PublicObject, Validation,
    Version, document_sse_events,
)
from scripts.export_web_schema import build_schema, export_schema
from test_web_api import ORIGIN, _legacy_workspace, _ladder, _login


def _roundtrip(model, value):
    parsed = model.model_validate(value)
    # Excluding unset fields tests preservation of the actual application DTO,
    # separately from useful empty-list defaults for clients.
    assert parsed.model_dump(mode="json", exclude_unset=True) == value
    return parsed


def test_real_project_and_version_projections_are_lossless(tmp_path):
    store, project_id, version_id, _ = _legacy_workspace(tmp_path / "workspace")
    service = WorkbenchService(store.base_dir, tmp_path / "state", read_only=True)
    summary = service.projects.list_projects()[0]
    project = service.projects.project(project_id)
    version = service.projects.version(project_id, version_id)
    _roundtrip(ProjectList, {"projects": [summary]})
    parsed = _roundtrip(Project, summary)
    assert parsed.versions == parsed.messages == parsed.reports == []
    _roundtrip(Project, project)
    _roundtrip(Version, version)
    for artifact in version["artifacts"]:
        _roundtrip(Artifact, artifact)
    program = service.projects.program(project_id, version_id)
    _roundtrip(PublicObject, program)
    _roundtrip(PublicObject, service.projects.diagnostics(project_id, version_id))


def test_dynamic_public_fields_and_capabilities_are_preserved_without_fake_pass():
    capabilities = {
        "body_form": "structured_experimental", "operations": {"view": True, "gx_compile": False},
        "representations": ["structured_document"],
        "new_public_capability": {"state": "experimental", "supported_backends": ["read_only"]},
    }
    parsed = _roundtrip(Capabilities, capabilities)
    assert parsed.model_extra["new_public_capability"] == capabilities["new_public_capability"]
    validation = _roundtrip(Validation, {"messages": [], "rules_checked": ["coil_ownership"], "counts": {"warning": 2}})
    assert validation.status is None
    output = {"status": "unavailable", "generation": {"validation": {"status": "unknown"}},
              "future_public_report": {"json_path": "$.rungs[0]", "network_id": "N0001", "details": list(range(5000))}}
    _roundtrip(JobOutput, output)
    _roundtrip(PublicObject, output["future_public_report"])
    artifact = Artifact.model_validate({"id": "missing", "available": False})
    assert artifact.filename is None and artifact.size is None


@pytest.mark.parametrize("private_key", ["_candidate_ir", "_confirmed_spec", "private_payload", "api_key", "apiKey", "credentialTarget", "operator_token", "gateway_token", "password"])
def test_read_contracts_reject_nested_private_data(private_key):
    secret = "private-value-must-not-leave-backend"
    for model, value in [
        (PublicObject, {"nested": [{private_key: secret}]}),
        (JobOutput, {"generation": {"nested": {private_key: secret}}}),
        (JobEvent, {"job_id": "job", "sequence": 1, "event_type": "progress", "created_at": "2026-09-09T00:00:00Z", "payload": {private_key: secret}}),
    ]:
        with pytest.raises(ValidationError, match="Private data cannot be returned"):
            model.model_validate(value)


def test_unknown_resource_fields_fail_instead_of_disappearing_silently():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Artifact.model_validate({"id": "svg", "available": True, "new_checksum": "abc"})


def test_real_job_events_and_connected_proposal_obey_read_contracts(tmp_path):
    from storage.session import SessionStore
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("Contract fixture")["id"]
    service = WorkbenchService(store.base_dir, tmp_path / "state")
    service.start()
    try:
        def worker(ctx):
            ctx.emit("progress", {"message": "Only deterministic data", "counts": {"networks": 1}})
            return {"status": "unverified", "summary": "No GX compilation performed"}
        job = service.jobs.submit("contract", {"project_id": project}, worker, request_id="contract-job")
        service.jobs._futures[job["id"]].result(timeout=5)
        _roundtrip(Job, service.jobs.get(job["id"]))
        _roundtrip(JobList, {"jobs": service.jobs.list(project)})
        for event in service.jobs.events(job["id"]):
            _roundtrip(JobEvent, event)
        result = service.agent_call({"project_id": project, "version_id": None, "name": "create_program_candidate",
                                     "arguments": {"ladder": _ladder()}, "call_id": "contract-candidate"})
        proposal = service.proposals.get(result["proposal_id"])
        _roundtrip(Proposal, proposal)
        assert proposal["status"] == "pending"
        assert "_candidate_ir" not in json.dumps(proposal)
        preview = service.proposal_preview(proposal["id"])
        assert TypeAdapter(ProposalPreview).validate_python(preview).model_dump(exclude_unset=True) == preview
    finally:
        service.close()


def test_model_settings_contract_keeps_capability_flags_without_credentials():
    settings = {
        "language": "en", "active_profile_id": "offline", "profiles": [{
            "id": "offline", "name": "Offline", "model": "test-model", "base_url": "http://127.0.0.1:9000/v1",
            "configured": False, "capabilities": {"tools": True, "multimodal": False},
        }],
    }
    _roundtrip(ModelSettings, settings)
    assert not ModelSettings.model_validate(settings).profiles[0].configured
    corrupted = copy.deepcopy(settings)
    corrupted["profiles"][0]["api_key"] = "never-return"
    with pytest.raises(ValidationError):
        ModelSettings.model_validate(corrupted)


def test_schema_export_constructs_only_a_read_only_app_and_never_starts_services(tmp_path, monkeypatch):
    import scripts.export_web_schema as exporter
    import integrations.web.app as web_app
    from application.settings import SettingsService
    from application.workspace import WorkspaceWriterLock
    seen = []
    real_create = web_app.create_app
    def create(*args, **kwargs):
        seen.append(kwargs)
        return real_create(*args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("Schema export started or acquired a runtime resource")
    monkeypatch.setattr(web_app, "create_app", create)
    monkeypatch.setattr(WorkbenchService, "start", forbidden)
    monkeypatch.setattr(WorkspaceWriterLock, "acquire", forbidden)
    monkeypatch.setattr(SettingsService, "model_snapshot", forbidden)
    output = tmp_path / "export" / "openapi.json"
    exporter.export_schema(output)
    schema = json.loads(output.read_text(encoding="utf-8"))
    assert seen and seen[0]["read_only"] is True
    assert not (exporter.ROOT / ".schema-only-workspace").exists()
    assert not (exporter.ROOT / ".schema-only-state").exists()
    assert schema["openapi"].startswith("3.")
    assert "schema-export-never-started" not in output.read_text(encoding="utf-8")
    assert set(tmp_path.iterdir()) == {tmp_path / "export"}


def test_exported_openapi_documents_all_major_read_resources_and_sse(tmp_path):
    output = export_schema(tmp_path / "openapi.json")
    schema = json.loads(output.read_text(encoding="utf-8"))
    models = schema["components"]["schemas"]
    assert {"Project", "Version", "Artifact", "Job", "JobEvent", "Proposal", "ModelSettings"} <= set(models)
    checks = {
        ("/api/projects", "get", "200"): "ProjectList",
        ("/api/projects/{project_id}", "get", "200"): "Project",
        ("/api/projects/{project_id}/versions/{version_id}", "get", "200"): "Version",
        ("/api/jobs", "post", "202"): "Job",
        ("/api/jobs/{job_id}", "get", "200"): "Job",
        ("/api/proposals/{proposal_id}", "get", "200"): "Proposal",
        ("/api/settings", "get", "200"): "ModelSettings",
    }
    for (path, method, status), model in checks.items():
        response = schema["paths"][path][method]["responses"][status]
        assert response["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/" + model
    sse = schema["paths"]["/api/jobs/{job_id}/events"]["get"]["responses"]["200"]
    assert set(sse["content"]) == {"text/event-stream"}
    assert sse["x-event-schema"] == {"$ref": "#/components/schemas/JobEvent"}
    assert models["Version"]["properties"]["validation"].get("default") is None


def test_http_response_validation_rejects_private_settings_without_echoing_secret(tmp_path):
    secret = "http-response-contract-private-key"
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", read_only=True)
    service.settings.public_settings = lambda: {"language": "en", "active_profile_id": "test", "profiles": [{
        "id": "test", "configured": True, "api_key": secret,
    }]}
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state", service=service,
                     origin=ORIGIN, operator_token="operator-test")
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        _login(client)
        response = client.get("/api/settings")
        assert response.status_code == 500
        assert secret not in response.text
        assert response.json()["error"]["code"] == "internal_error"
