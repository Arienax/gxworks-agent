"""Public resource schemas preserve engineering data and reject private state."""
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Web contracts require requirements-web.txt")
pytest.importorskip("httpx", reason="Web contracts require the optional HTTP test client")
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from hypothesis import example, given, strategies as st

from application.workbench import WorkbenchService
from integrations.web.app import create_app
from integrations.web.responses import (
    Artifact, Capabilities, Job, JobEvent, JobList, JobOutput, ModelSettings,
    Project, ProjectList, Proposal, ProposalPreview, PublicObject, Validation,
    Version, PLCProgram, DiagnosticsEnvelope, document_sse_events,
)
from scripts.export_web_schema import build_schema, export_schema
from test_web_api import ORIGIN, _legacy_workspace, _ladder, _login


def _roundtrip(model, value):
    parsed = model.model_validate(value)
    # Excluding unset fields tests preservation of the actual application DTO,
    # separately from useful empty-list defaults for clients.
    assert parsed.model_dump(mode="json", exclude_unset=True) == value
    return parsed


@pytest.mark.parametrize('invalid', [
    {}, {'$ref': 'other'}, {'$ref': '#/objects/unit-1', 'extra': True},
    {'$ref': '#/objects/unit-1', 'api_key': 'private'}, {'$ref': '#/objects/'},
])
def test_paged_read_document_is_exactly_one_object_reference(invalid):
    from integrations.web.responses import ReadObjectReference
    ref = {'$ref': '#/objects/unit-1'}
    assert ReadObjectReference.model_validate(ref).model_dump(by_alias=True) == ref
    with pytest.raises(ValidationError):
        ReadObjectReference.model_validate(invalid)


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
    _roundtrip(PLCProgram, program)
    _roundtrip(DiagnosticsEnvelope, service.projects.diagnostics(project_id, version_id))
    from agent_runtime.runtime import public_tool_result_data
    from integrations.web.responses import NetworkEnvelope
    read = service.projects.invoke(project_id, version_id, "contract-network", "read_network",
        {"network_id": program["networks"][0]["id"]})
    parsed_network = _roundtrip(NetworkEnvelope, public_tool_result_data(read))
    assert parsed_network.data.network.id == program["networks"][0]["id"]


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


@pytest.mark.parametrize("cpu,family", [("Q03UDV", "q"), ("L02", "l"), ("FX3U", "fx")])
def test_native_target_extensions_survive_strict_http_projection_without_family_promotion(cpu, family):
    from plc.project_capabilities import project_capabilities
    value = project_capabilities({"plc_model": cpu, "target_mode": "fbd"})
    parsed = _roundtrip(Capabilities, value)
    assert parsed.project_type == family and len(parsed.creation.native_targets) == 60
    assert {row["family"] for row in parsed.creation.native_targets} == {"Q", "L", "FX"}
    assert all(row["status"] == "unverified" for row in parsed.target_support["operations"].values())
    assert sum(row["program_supported"] for row in parsed.creation.native_targets) == 57
    for row in parsed.creation.native_targets:
        if row["program_supported"] is False:
            parameter_only = _roundtrip(Capabilities, project_capabilities({"plc_model": row["menu_cpu"], "target_mode": "fbd"}))
            assert parameter_only.operations["view"] is True and parameter_only.operations["generate"] is False
            assert parameter_only.operations["gx_import"] is False


@pytest.mark.parametrize("private_key", ["_candidate_ir", "_confirmed_spec", "private_payload", "api_key", "apiKey", "credentialTarget", "operator_token", "gateway_token", "password", "api-key", "cookie", "proxy_authorization"])
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
    from application.session import ProjectSession as SessionStore
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


def test_model_settings_contract_keeps_canonical_contract_without_credentials():
    settings = {
        "language": "en", "active_profile_id": "offline", "profiles": [{
            "id": "offline", "name": "Offline", "model": "test-model", "base_url": "http://127.0.0.1:9000/v1",
            "configured": False,
            "contract": {"schema_version": 3, "capabilities": {
                "tools": {"status": "supported", "source": "catalog"},
                "vision": {"status": "unsupported", "source": "catalog"},
            }},
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


_engineering_keys = st.one_of(st.sampled_from(["token", "secret", "password", "_state", "source_id", "api-key"]),
                             st.text(max_size=16))
_json_leaf = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text()
_engineering_json = st.recursive(_json_leaf, lambda children:
    st.lists(children, max_size=5) | st.dictionaries(_engineering_keys, children, max_size=5), max_leaves=24)


@given(st.dictionaries(_engineering_keys, _engineering_json, max_size=6))
@example({"token": {"_state": [{"secret": "C:\\source\\PLC.st", "password": 4}]}})
def test_engineering_documents_roundtrip_without_key_or_value_loss(metadata):
    from application.projects import public
    from agent_runtime.runtime import public_tool_result_data
    from types import SimpleNamespace
    from integrations.web.responses import FBDModel
    model = {"schema_version": 1, "program": "MAIN", "nodes": [], "wires": [], "metadata": metadata,
             "declaration_edits": {name: {"renames": {"token": "_state", "password": "secret"}}
                                   for name in ("Labels", "token", "_state", "source_id")}}
    projected = public(model, document="program")
    assert projected == model
    assert FBDModel.model_validate(projected).model_dump(mode="json", by_alias=True, exclude_unset=True) == model
    tool = public_tool_result_data(SimpleNamespace(name="get_current_program_info", data=model))
    assert tool["metadata"] == metadata and tool["declaration_edits"] == model["declaration_edits"]


@given(st.dictionaries(_engineering_keys, _engineering_json, max_size=6))
def test_specification_user_data_is_lossless_at_declared_nested_locations(data):
    from application.projects import public
    from integrations.web.responses import Specification

    value = {"variables": data, "structs": data, "io_table": [{"metadata": data}],
             "parameters": [{"id": "user-value", "value": data, "required_when": data}],
             "operation_intents": [{"id": "named-parameters", "effects": [],
                 "enable": {"op": "constant", "type": {"kind": "bool"}, "value": True},
                 "parameters": {key: {"op": "constant", "type": {"kind": "opaque"}, "value": value}
                                for key, value in data.items()}}]}
    projected = public(value, document="spec")
    assert projected == value
    _roundtrip(Specification, projected)


@pytest.mark.parametrize("key", ["token", "secret", "password", "_state"])
def test_parameter_system_metadata_stays_private_outside_its_user_value(key):
    from application.projects import public
    from integrations.web.responses import Specification

    value = {"parameters": [{"id": "question", "value": {key: "user-data"}, key: "private"}]}
    with pytest.raises(ValidationError, match="Private data cannot be returned"):
        Specification.model_validate(value)
    projected = public(value, document="spec")
    assert projected == {"parameters": [{"id": "question", "value": {key: "user-data"}}]}
    _roundtrip(Specification, projected)


def test_recorded_timeline_preserves_named_signals_and_checks_its_system_metadata():
    from application.projects import public
    from integrations.web.simulation_contracts import SimulationTimeline
    from simulator.observation import SimulationObservation

    names = ("token", "secret", "password", "_state")
    config = {"title": "Named signals", "plc_model": "FX3U", "duration_ms": 100,
              "sample_ms": 50, "signals": [{"id": name, "label": name, "address": "M0",
                  "addresses": ["M0"], "data_type": "BOOL", "role": "state", "unit": ""}
                  for name in names]}
    view = SimulationObservation(config)
    values = dict.fromkeys(names, True)
    view.sample_values(values, errors=dict.fromkeys(names, "No source value"), at_ms=0)
    value = view.snapshot()
    projected = public({"timeline": value}, document="simulation")["timeline"]
    assert projected == value
    _roundtrip(SimulationTimeline, projected)
    with pytest.raises(ValidationError, match="Private data cannot be returned"):
        SimulationTimeline.model_validate({**value, "source": {"kind": "private", "token": "credential"}})


@pytest.mark.parametrize("key", ["token", "secret", "password", "_state"])
def test_metadata_and_payload_flags_cannot_claim_engineering_exemption(key):
    from application.projects import public
    from integrations.web.responses import FBDModel
    value = {"schema_version": 1, "program": "MAIN", "nodes": [], "wires": [],
             "unknown_extension": {"engineering_data": True, key: "private"}}
    with pytest.raises(ValidationError, match="Private data cannot be returned"):
        FBDModel.model_validate(value)
    assert key not in public(value)["unknown_extension"]


def test_http_engineering_maps_survive_program_and_diagnostics_boundaries(tmp_path):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state", read_only=True)
    metadata = {"token": {"_state": [1, {"password": "user-field"}]}, "secret": 3}
    program = {"schema_version": 1, "program": "MAIN", "nodes": [
        {"id": "n1", "template": "contact_no", "symbol": "token", "x": 1, "y": 2}],
        "wires": [], "metadata": metadata}
    service.projects.program = lambda *_: copy.deepcopy(program)
    diagnostics = {"ok": True, "tool": "get_diagnostics", "data": {"variables": metadata}}
    service.projects.diagnostics = lambda *_: copy.deepcopy(diagnostics)
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state", service=service,
                     origin=ORIGIN, operator_token="operator-test")
    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        response = client.get("/api/projects/p/versions/v/program")
        assert response.status_code == 200 and response.json() == program
        # Core's native editor accepts integer coordinates, including on a
        # roundtrip through Python HTTP clients that preserve 1.0 as a float.
        assert type(response.json()["nodes"][0]["x"]) is int
        response = client.get("/api/projects/p/versions/v/diagnostics")
        assert response.status_code == 200 and response.json() == diagnostics


def test_http_and_mcp_share_real_runtime_engineering_projection(tmp_path):
    from agent_runtime.plc_tools import ToolDefinition, ToolRegistry
    from agent_runtime.runtime import InProcessToolRuntime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter

    store, project_id, version_id, _ = _legacy_workspace(tmp_path / "workspace")
    service = WorkbenchService(store.base_dir, tmp_path / "state")
    data = {"token": {"password": 5, "_state": ["secret"]}}
    model = {"schema_version": 1, "program": "MAIN", "nodes": [], "wires": [], "metadata": data}
    registry = ToolRegistry()
    registry.register(ToolDefinition('get_diagnostics', 'contract', {'type': 'object'},
        lambda *_: {'variables': copy.deepcopy(data), '_native': 'never-return'}))
    registry.register(ToolDefinition('read_fbd_project', 'contract', {'type': 'object'},
        lambda *_: {'available': True, 'model': copy.deepcopy(model), 'token': 'never-return'}))
    runtime = InProcessToolRuntime(registry)
    service.projects.runtime = runtime
    app = create_app(store.base_dir, state_dir=tmp_path / "state", service=service,
                     origin=ORIGIN, operator_token="operator-test", agent_token="agent-engineering-contract")
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        diagnostics = client.get(f'/api/projects/{project_id}/versions/{version_id}/diagnostics')
        assert diagnostics.status_code == 200 and diagnostics.json()['data'] == {'variables': data}
        response = client.post('/api/agent/tools/call', headers={"Authorization": "Bearer agent-engineering-contract"}, json={
            'project_id': project_id, 'version_id': version_id, 'name': 'read_fbd_project',
            'arguments': {}, 'call_id': 'engineering-contract',
        })
        assert response.status_code == 200, response.text
        value = response.json()
        assert value['data']['data'] == {'available': True, 'model': model}
        assert json.loads(value['content']) == value['data']
        mcp = MCPToolAdapter(runtime, SessionToolContextProvider(store.base_dir, project_id, version_id))
        result = mcp.call_tool('read_fbd_project', {}, 'engineering-mcp')
        assert not result.is_error and result.structured_content == value['data']


def test_unrelated_tool_cannot_get_engineering_exemption_by_naming_a_model_field():
    from integrations.web.responses import AgentToolResult

    with pytest.raises(ValidationError):
        AgentToolResult.model_validate({'name': 'get_current_project', 'call_id': 'metadata',
            'is_error': False, 'content': '', 'data': {'ok': True, 'tool': 'get_current_project',
                'data': {'model': {'metadata': {'token': 'credential'}}}}})


def test_tool_envelope_cannot_impersonate_another_resource_owner():
    from integrations.web.responses import AgentToolResult

    with pytest.raises(ValidationError, match="registered name"):
        AgentToolResult.model_validate({'name': 'get_current_project', 'call_id': 'wrong-owner',
            'is_error': False, 'content': '', 'data': {'ok': True, 'tool': 'read_fbd_project',
                'data': {'model': {'schema_version': 1, 'program': 'MAIN', 'nodes': [], 'wires': [],
                                  'metadata': {'token': 'user-field'}}}}})


@given(st.dictionaries(_engineering_keys, _engineering_json, max_size=6))
def test_preview_and_delivery_property_diffs_keep_declared_engineering_values(data):
    from application.projects import public
    from integrations.web.program_contracts import ProgramPropertyChanges

    changes = {key: {"before": None, "after": data} for key in ("metadata", "variables", "structs", "io_map")}
    diff = {"property_changes": changes}
    projected = public(diff, document="preview_diff")
    assert projected == diff
    _roundtrip(ProgramPropertyChanges, projected["property_changes"])
    assert public({"changes": diff}, document="delivery") == {"changes": diff}
    with pytest.raises(ValidationError, match="Private data cannot be returned"):
        ProgramPropertyChanges.model_validate({"cpu": {"before": None, "after": {"token": "credential"}}})


@pytest.mark.parametrize("name", ["token", "secret", "password", "_state", "source_id"])
def test_definition_and_label_member_names_survive_source_and_diff_projection(name):
    from application.projects import public
    from integrations.web.program_contracts import FBDModel
    from integrations.web.responses import FBDDefinitionChanges, SourceSnapshotToolData

    declaration = {"name": name, "data_type": "UserStruct", "initial_value": "'C:\\PLC\\source.st'",
                   "comment": "C:\\PLC\\source.st", "member_devices": {
        name: {"device": "D0", "iec_address": "%MW0"}}}
    model = {"schema_version": 1, "program": "MAIN", "nodes": [], "wires": [],
             "labels": {name: [declaration]}}
    definition = {"name": name, "kind": "function_block", "model": model, "declarations": [declaration],
                  "st_text": "(* C:\\project\\source.st *)"}
    source = {"definitions": [definition]}
    assert public(source, document="source_snapshot") == source
    _roundtrip(SourceSnapshotToolData, source)
    assert public(model, document="program") == model
    _roundtrip(FBDModel, model)
    editor = {"presentation": {"rows": model["labels"]}}
    assert public(editor, document="editor") == editor
    assert public({"fbd_declarations": model["labels"]}, document="delivery") == {"fbd_declarations": model["labels"]}
    diff = {"definition_changes": {"before": definition, "after": definition}}
    assert public(diff, document="preview_diff") == diff
    _roundtrip(FBDDefinitionChanges, diff["definition_changes"])

    private = copy.deepcopy(model)
    private["labels"][name][0]["token"] = "credential"
    private["labels"][name][0]["member_devices"][name]["password"] = "credential"
    with pytest.raises(ValidationError, match="Private data cannot be returned"):
        FBDModel.model_validate(private)
    assert public(private, document="program") == model


def test_http_candidate_preview_keeps_real_core_metadata_diff(tmp_path, monkeypatch):
    from plc.core import PLCCore

    store, project_id, version_id, _ = _legacy_workspace(tmp_path / "workspace")
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (None, {}))
    service.start()
    before = service.projects.program(project_id, version_id)
    after = copy.deepcopy(before)
    metadata = {"token": {"password": ["C:\\PLC\\source.st", {"_state": True}]}}
    after["metadata"] = metadata
    diff = PLCCore().diff_programs(before, after)
    monkeypatch.setattr(service.proposals, 'get', lambda *_: {
        'project_id': project_id, 'base_version_id': version_id})
    monkeypatch.setattr(service.proposals, 'read_private', lambda *_: {
        '_candidate_ir': after, '_preview_diff': diff})
    app = create_app(store.base_dir, state_dir=tmp_path / "state", service=service,
                     origin=ORIGIN, operator_token="operator-test")
    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        response = client.get('/api/proposals/review/preview')
        assert response.status_code == 200, response.text
        assert response.json()['diff']['property_changes']['metadata']['after'] == metadata


def test_openapi_success_resources_do_not_fall_back_to_untyped_objects():
    schema = build_schema()
    serialized = json.dumps(schema)
    assert '"PublicObject"' not in serialized and '"NullablePublicObject"' not in serialized
    models = schema["components"]["schemas"]
    for name, fields in {"FBDModel": {"nodes", "wires", "program"},
                         "SFCModel": {"graph", "children", "actions"},
                         "SimulationWorkbench": {"devices", "plans", "requirements"},
                         "NativeEvidence": {"records", "source_gxw_sha256"},
                         "HardwareSession": {"target", "audit", "status"}}.items():
        assert fields <= set(models[name]["properties"])
