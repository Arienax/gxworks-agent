"""Client discovery and validated candidates are distinct from tool-list checks."""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from test_web_api import AGENT, ORIGIN, _app, _ladder, _login


@pytest.fixture
def client_service(tmp_path):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: pytest.fail("MCP must not call a model"))
    project = service.store.create_project("MCP activity")
    other = service.store.create_project("Other project")
    app = _app(service.store.base_dir, service.state_dir, service=service)
    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        yield service, client, project["id"], other["id"]


def _call(client, project_id, name, call_id, arguments=None):
    response = client.post("/api/agent/tools/call", headers={"Authorization": "Bearer " + AGENT},
        json={"project_id": project_id, "name": name, "call_id": call_id, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()


def test_list_only_is_not_a_client_engineering_call(client_service):
    service, client, project, other = client_service
    assert client.get("/api/agent/tools", headers={"Authorization": "Bearer " + AGENT}).status_code == 200
    assert service.mcp_activity(project)["client_observed"] is False
    result = _call(client, project, "get_generation_context", "read-context")
    assert not result["is_error"]
    activity = service.mcp_activity(project)
    assert activity["client_observed"] and activity["generation_context_observed"]
    assert activity["last_tool"] == "get_generation_context"
    assert activity["last_call_at"] and activity["candidate_proposal_id"] is None
    assert service.mcp_activity(other)["client_observed"] is False
    activity["last_tool"] = "changed-copy"
    assert service.mcp_activity(project)["last_tool"] == "get_generation_context"


def test_invalid_ladder_does_not_produce_candidate_evidence(client_service):
    service, client, project, _ = client_service
    ladder = _ladder()
    duplicate = copy.deepcopy(ladder["rungs"][0])
    duplicate["rung_id"] = 2
    duplicate["branches"][0]["outputs"][0]["address"] = "INVALID"
    ladder["rungs"].append(duplicate)
    failed = _call(client, project, "create_program_candidate", "bad-output-address", {"ladder": ladder})
    assert failed["is_error"] is True
    assert not failed.get("proposal_id")
    assert service.mcp_activity(project)["candidate_proposal_id"] is None
    assert service.proposals.list(project) == []
    assert service.projects.project(project)["version_count"] == 0


def test_validated_candidate_links_real_pending_proposal_and_retry(client_service):
    service, client, project, _ = client_service
    _call(client, project, "get_generation_context", "context-before-candidate")
    arguments = {"ladder": _ladder()}
    result = _call(client, project, "create_program_candidate", "valid-candidate", arguments)
    assert not result["is_error"] and result["proposal_id"]
    proposal = service.proposals.get(result["proposal_id"])
    assert proposal["status"] == "pending"
    assert service.projects.project(project)["version_count"] == 0
    assert service.mcp_activity(project)["candidate_proposal_id"] == proposal["id"]
    retry = _call(client, project, "create_program_candidate", "valid-candidate", arguments)
    assert retry == result
    assert len(service.proposals.list(project)) == 1
    assert "_candidate_ir" not in str(result)
    restarted = WorkbenchService(service.store.base_dir, service.state_dir)
    assert restarted.mcp_activity(project)["client_observed"] is False
