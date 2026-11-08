# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""The workbench serves a list of workspaces, each owning its own projects."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from integrations.web.app import create_app

ORIGIN = "http://127.0.0.1:8765"


def open_page(app):
    page = TestClient(app, base_url=ORIGIN)
    page.__enter__()
    session = page.get("/api/session")
    assert session.status_code == 200
    page.headers.update({"x-csrf-token": session.json()["csrf"], "origin": ORIGIN})
    return page


@pytest.fixture()
def client(tmp_path):
    app = create_app(None, state_dir=tmp_path / "state", origin=ORIGIN,
                     operator_token=None, agent_token="agent-token")
    page = open_page(app)
    try:
        yield page
    finally:
        page.__exit__(None, None, None)


def test_before_any_workspace_exists_the_list_is_empty(client):
    index = client.get("/api/workspaces").json()
    assert index["active"] is None
    assert index["workspaces"] == []
    assert client.get("/api/workspace").json()["selected"] is False


def test_work_that_needs_a_workspace_reports_the_missing_choice(client):
    response = client.get("/api/projects")
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "workspace_required"


def test_session_still_answers_before_a_workspace_exists(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_adding_a_workspace_lists_it_with_its_projects_and_activates_it(client, tmp_path):
    added = client.post("/api/workspaces", json={"path": str(tmp_path / "line-a")})
    assert added.status_code == 200
    index = added.json()
    assert len(index["workspaces"]) == 1
    row = index["workspaces"][0]
    assert row["label"] == "line-a"
    assert row["active"] is True
    assert row["projects"] == []
    assert index["active"] == row["id"]
    assert client.get("/api/projects").status_code == 200


def test_a_second_workspace_becomes_active_and_the_first_stays_listed(client, tmp_path):
    first = client.post("/api/workspaces", json={"path": str(tmp_path / "one")}).json()
    second = client.post("/api/workspaces", json={"path": str(tmp_path / "two")}).json()
    notes = {row["label"]: row for row in second["workspaces"]}
    assert set(notes) == {"one", "two"}
    assert notes["two"]["active"] is True
    assert notes["one"]["active"] is False
    assert second["active"] == notes["two"]["id"]
    assert first["active"] == notes["one"]["id"]


def test_activating_an_earlier_workspace_serves_it_again(client, tmp_path):
    first = client.post("/api/workspaces", json={"path": str(tmp_path / "one")}).json()
    client.post("/api/workspaces", json={"path": str(tmp_path / "two")})
    identifier = first["workspaces"][0]["id"]
    switched = client.post(f"/api/workspaces/{identifier}/activate")
    assert switched.status_code == 200
    assert switched.json()["active"] == identifier
    assert client.get("/api/workspace").json()["name"] == "one"


def test_forgetting_a_workspace_keeps_its_folder_and_projects_on_disk(client, tmp_path):
    target = tmp_path / "kept"
    added = client.post("/api/workspaces", json={"path": str(target)}).json()
    identifier = added["workspaces"][0]["id"]
    removed = client.delete(f"/api/workspaces/{identifier}")
    assert removed.status_code == 200
    assert removed.json()["workspaces"] == []
    assert removed.json()["active"] is None
    assert target.is_dir()
    assert client.get("/api/projects").status_code == 428


def test_forgetting_an_unknown_workspace_is_not_found(client):
    assert client.delete("/api/workspaces/nope").status_code == 404


def test_selecting_a_single_project_directory_is_refused(client, tmp_path):
    project = tmp_path / "one-project"
    project.mkdir()
    (project / "project.json").write_text("{}", encoding="utf-8")
    response = client.post("/api/workspaces", json={"path": str(project)})
    assert response.status_code == 400


def test_the_sidebar_chooser_adds_the_folder_the_system_returns(client, tmp_path, monkeypatch):
    import integrations.web.app as web_app

    chosen = tmp_path / "from-explorer"
    monkeypatch.setattr(web_app, "pick_native_directory", lambda *a, **k: str(chosen))
    response = client.post("/api/workspaces/pick", json={})
    assert response.status_code == 200
    index = response.json()
    assert [row["label"] for row in index["workspaces"]] == ["from-explorer"]
    assert index["workspaces"][0]["active"] is True
    assert Path(chosen).is_dir()


def test_cancelling_the_system_chooser_changes_nothing(client, monkeypatch):
    import integrations.web.app as web_app

    monkeypatch.setattr(web_app, "pick_native_directory", lambda *a, **k: None)
    response = client.post("/api/workspaces/pick", json={})
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert response.json()["workspaces"] == []


@pytest.mark.parametrize("code", ["picker_unavailable", "directory-unreadable"])
def test_a_chooser_failure_is_reported_without_registering(client, monkeypatch, code):
    import integrations.web.app as web_app
    from integrations.web.workspaces import WorkspacePathError

    def fail(*_args, **_kwargs):
        raise WorkspacePathError(code, "no desktop session")

    monkeypatch.setattr(web_app, "pick_native_directory", fail)
    response = client.post("/api/workspaces/pick", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert client.get("/api/workspaces").json()["workspaces"] == []


def test_a_workspace_lists_every_project_it_owns(client, tmp_path):
    """One workspace holds many projects; each workspace row carries its own list."""
    from storage.session import SessionStore

    folder = tmp_path / "line-with-projects"
    folder.mkdir()
    store = SessionStore(folder)
    store.create_project("分拣线", plc_model="FX3U", target_mode="ladder")
    store.create_project("码垛", plc_model="FX3U", target_mode="fbd")

    index = client.post("/api/workspaces", json={"path": str(folder)}).json()
    names = sorted(row["name"] for row in index["workspaces"][0]["projects"])
    assert names == ["分拣线", "码垛"]
    # The active workspace's projects are also served by the normal project API.
    served = {row["name"] for row in client.get("/api/projects").json()["projects"]}
    assert served == {"分拣线", "码垛"}


def test_the_agent_credential_stays_separate_from_the_page_session(tmp_path):
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state", origin=ORIGIN,
                     operator_token=None, agent_token="agent-token")
    page = open_page(app)
    try:
        assert page.get("/api/agent/health").status_code == 403
        assert page.get("/api/agent/health",
                        headers={"authorization": "Bearer agent-token"}).status_code != 403
    finally:
        page.__exit__(None, None, None)


def test_the_workspace_list_survives_a_restart(tmp_path):
    state = tmp_path / "state"
    first_app = create_app(None, state_dir=state, origin=ORIGIN, operator_token=None, agent_token="a")
    page = open_page(first_app)
    try:
        page.post("/api/workspaces", json={"path": str(tmp_path / "remembered")})
    finally:
        page.__exit__(None, None, None)

    second_app = create_app(None, state_dir=state, origin=ORIGIN, operator_token=None, agent_token="a")
    page = open_page(second_app)
    try:
        index = page.get("/api/workspaces").json()
        assert [row["label"] for row in index["workspaces"]] == ["remembered"]
        # The remembered workspace is bound again, so project work needs no pick.
        assert page.get("/api/projects").status_code == 200
    finally:
        page.__exit__(None, None, None)
