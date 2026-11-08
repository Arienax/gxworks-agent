# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""The workbench starts without a workspace and adopts the browser's choice."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from integrations.web.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(None, state_dir=tmp_path / "state", origin="http://127.0.0.1:8765",
                     operator_token="operator-token", agent_token="agent-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as opened:
        login = opened.post("/api/session", json={"token": "operator-token"})
        assert login.status_code == 200
        opened.headers.update({"x-csrf-token": login.json()["csrf"],
                               "origin": "http://127.0.0.1:8765"})
        yield opened


def test_before_a_workspace_exists_the_state_says_so(client):
    state = client.get("/api/workspace").json()
    assert state["selected"] is False
    assert state["path"] is None
    assert state["home"]


def test_work_that_needs_a_workspace_reports_the_missing_choice(client):
    response = client.get("/api/projects")
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "workspace_required"


def test_session_still_answers_before_a_workspace_exists(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_browsing_lists_directories_without_a_bound_workspace(client, tmp_path):
    (tmp_path / "line-a").mkdir()
    (tmp_path / "line-b").mkdir()
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    listing = client.get("/api/workspace/browse", params={"path": str(tmp_path)}).json()
    assert [row["name"] for row in listing["entries"]] == ["line-a", "line-b"]
    assert listing["path"] == str(tmp_path)
    assert listing["crumbs"][0]["path"] == Path(str(tmp_path)).anchor


def test_browsing_a_relative_path_is_refused(client):
    response = client.get("/api/workspace/browse", params={"path": "relative/place"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "directory-unreadable"


def test_creating_a_directory_through_the_picker(client, tmp_path):
    response = client.post("/api/workspace/directory",
                           json={"parent": str(tmp_path), "name": "new-line"})
    assert response.status_code == 200
    assert (tmp_path / "new-line").is_dir()


def test_selecting_a_workspace_binds_it_and_unblocks_the_workbench(client, tmp_path):
    target = tmp_path / "engineering"
    chosen = client.post("/api/workspace", json={"path": str(target)})
    assert chosen.status_code == 200
    assert chosen.json()["selected"] is True
    assert Path(chosen.json()["path"]) == target.resolve()
    assert target.is_dir()

    assert client.get("/api/workspace").json()["path"] == str(target.resolve())
    assert client.get("/api/projects").status_code == 200


def test_selecting_the_same_workspace_twice_keeps_the_bound_service(client, tmp_path):
    target = tmp_path / "engineering"
    first = client.post("/api/workspace", json={"path": str(target)}).json()
    second = client.post("/api/workspace", json={"path": str(target)}).json()
    assert first["path"] == second["path"]
    assert client.get("/api/projects").status_code == 200


def test_selecting_a_single_project_directory_is_refused(client, tmp_path):
    project = tmp_path / "one-project"
    project.mkdir()
    (project / "project.json").write_text("{}", encoding="utf-8")
    response = client.post("/api/workspace", json={"path": str(project)})
    assert response.status_code == 400


def test_switching_workspaces_serves_the_new_one(client, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    client.post("/api/workspace", json={"path": str(first)})
    switched = client.post("/api/workspace", json={"path": str(second)})
    assert switched.status_code == 200
    assert Path(switched.json()["path"]) == second.resolve()
    assert client.get("/api/projects").json()["projects"] == []


def test_the_loopback_page_opens_its_own_session_without_a_token(tmp_path):
    # No operator credential configured: the browser never types or reads a token.
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state",
                     origin="http://127.0.0.1:8765", operator_token=None, agent_token="agent-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as page:
        opened = page.get("/api/session")
        assert opened.status_code == 200
        body = opened.json()
        assert body["authenticated"] is True
        assert body["csrf"]
        assert "gx_operator" in opened.cookies

        page.headers.update({"x-csrf-token": body["csrf"], "origin": "http://127.0.0.1:8765"})
        assert page.get("/api/workspace").status_code == 200
        assert page.get("/api/projects").status_code == 200


def test_a_write_still_requires_the_session_csrf_header(tmp_path):
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state",
                     origin="http://127.0.0.1:8765", operator_token=None, agent_token="agent-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as page:
        session = page.get("/api/session").json()
        page.headers.update({"origin": "http://127.0.0.1:8765"})
        refused = page.post("/api/workspace/directory",
                            json={"parent": str(tmp_path), "name": "line"})
        assert refused.status_code == 403
        page.headers.update({"x-csrf-token": session["csrf"]})
        assert page.post("/api/workspace/directory",
                         json={"parent": str(tmp_path), "name": "line"}).status_code == 200


def test_the_agent_credential_stays_separate_from_the_page_session(tmp_path):
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state",
                     origin="http://127.0.0.1:8765", operator_token=None, agent_token="agent-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as page:
        page.get("/api/session")
        assert page.get("/api/agent/health").status_code == 403
        assert page.get("/api/agent/health",
                        headers={"authorization": "Bearer agent-token"}).status_code != 403
