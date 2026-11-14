# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

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
    return page


@pytest.fixture()
def client(tmp_path):
    app = create_app(None, state_dir=tmp_path / "state", origin=ORIGIN, agent_token="agent-token")
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


def test_workspace_status_answers_before_a_workspace_exists(client):
    response = client.get("/api/workspace")
    assert response.status_code == 200
    assert response.json()["selected"] is False
    assert response.json()["read_only"] is False


def test_read_only_workspace_reports_the_reason_for_rejecting_creation(tmp_path):
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state", origin=ORIGIN, read_only=True)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/workspace").json()["read_only"] is True
        response = client.post("/api/projects", json={"name": "Read only", "plc_model": "FX3U"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "read_only_workspace"


def test_another_workbench_process_can_write_without_interrupting_a_running_job(tmp_path):
    import os
    import subprocess
    import threading

    workspace, state = tmp_path / "workspace", tmp_path / "state"
    app = create_app(workspace, state_dir=state, origin=ORIGIN)
    started, finish = threading.Event(), threading.Event()

    def worker(_ctx):
        started.set()
        assert finish.wait(45)
        return {"status": "complete"}

    script = """
import sys
from fastapi.testclient import TestClient
from integrations.web.app import create_app

origin = "http://127.0.0.1:8765"
app = create_app(sys.argv[1], state_dir=sys.argv[2], origin=origin)
with TestClient(app, base_url=origin) as client:
    assert client.get("/api/jobs/" + sys.argv[3]).json()["status"] == "running"
    response = client.post("/api/projects", json={"name": "Second workbench", "plc_model": "FX3U"})
    assert response.status_code == 201, response.text
"""
    with TestClient(app, base_url=ORIGIN) as client:
        first = client.post("/api/projects", json={"name": "First workbench", "plc_model": "FX3U"})
        assert first.status_code == 201, first.text
        manager = app.state.service.jobs
        job = manager.submit("test", {}, worker)
        try:
            assert started.wait(5)
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            result = subprocess.run(
                [sys.executable, "-c", script, str(workspace), str(state), job["id"]],
                env=env, capture_output=True, timeout=35,
            )
            assert result.returncode == 0, result.stderr.decode(errors="replace")
            assert manager.get(job["id"])["status"] == "running"
        finally:
            finish.set()
        assert manager.wait(job["id"], timeout=5)["status"] == "completed"
        assert {row["name"] for row in client.get("/api/projects").json()["projects"]} == {
            "First workbench", "Second workbench",
        }


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


def test_failed_workspace_start_keeps_the_current_binding(client, tmp_path, monkeypatch):
    from application.workbench import WorkbenchService

    original = client.post("/api/workspaces", json={"path": str(tmp_path / "current")}).json()
    unavailable = tmp_path / "unavailable"
    start = WorkbenchService.start

    def start_if_available(service):
        if service.store.base_dir == unavailable:
            raise ValueError("Cannot start the selected workspace")
        return start(service)

    monkeypatch.setattr(WorkbenchService, "start", start_if_available)
    assert client.post("/api/workspaces", json={"path": str(unavailable)}).status_code == 400
    assert client.get("/api/workspaces").json() == original
    assert client.post("/api/projects", json={"name": "Current workspace", "plc_model": "FX3U"}).status_code == 201


def test_other_workbench_keeps_its_binding_and_remembered_selection(tmp_path):
    state = tmp_path / "state"
    first = create_app(tmp_path / "first", state_dir=state, origin=ORIGIN)
    with TestClient(first, base_url=ORIGIN) as a:
        first_id = a.get("/api/workspaces").json()["active"]
        unused_id = a.post("/api/workspaces", json={"path": str(tmp_path / "unused")}).json()["active"]
        assert a.post(f"/api/workspaces/{first_id}/activate").status_code == 200
        second = create_app(tmp_path / "second", state_dir=state, origin=ORIGIN)
        with TestClient(second, base_url=ORIGIN) as b:
            second_id = b.get("/api/workspaces").json()["active"]
            assert second_id != first_id
            assert a.delete(f"/api/workspaces/{unused_id}").json()["active"] == first_id
            assert b.get("/api/workspace").json()["name"] == "second"
            restarted = create_app(state_dir=state, origin=ORIGIN)
            with TestClient(restarted, base_url=ORIGIN) as c:
                assert c.get("/api/workspaces").json()["active"] == second_id


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


def test_the_agent_api_still_requires_its_credential(tmp_path):
    app = create_app(tmp_path / "workspace", state_dir=tmp_path / "state", origin=ORIGIN, agent_token="agent-token")
    page = open_page(app)
    try:
        assert page.get("/api/agent/tools").status_code == 403
        assert page.get("/api/agent/tools",
                        headers={"authorization": "Bearer agent-token"}).status_code == 200
    finally:
        page.__exit__(None, None, None)


def test_the_workspace_list_survives_a_restart(tmp_path):
    state = tmp_path / "state"
    first_app = create_app(None, state_dir=state, origin=ORIGIN, agent_token="a")
    page = open_page(first_app)
    try:
        page.post("/api/workspaces", json={"path": str(tmp_path / "remembered")})
    finally:
        page.__exit__(None, None, None)

    second_app = create_app(None, state_dir=state, origin=ORIGIN, agent_token="a")
    page = open_page(second_app)
    try:
        index = page.get("/api/workspaces").json()
        assert [row["label"] for row in index["workspaces"]] == ["remembered"]
        # The remembered workspace is bound again, so project work needs no pick.
        assert page.get("/api/projects").status_code == 200
    finally:
        page.__exit__(None, None, None)
