"""HTTP integration acceptance against temporary workspaces and offline models."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from xml.etree import ElementTree

import pytest

pytest.importorskip("fastapi", reason="Web integration requires requirements-web.txt")
pytest.importorskip("httpx", reason="Web integration requires requirements-web.txt")
from fastapi.testclient import TestClient

import application.model_workflows as api
from application.workbench import WorkbenchService
from rendering.ladder import AdvancedSVGLadder
from integrations.web.app import create_app
from model_runtime.provider import TextDelta
from storage.session import SessionStore


ORIGIN = "http://127.0.0.1:8765"
OPERATOR = "operator-test"
AGENT = "agent-test"


def _ladder():
    return {
        "device_comments": {"X0": "Input", "Y0": "Output"},
        "rungs": [{"rung_id": 1, "header_element": None, "shared_inputs": [],
                   "branches": [{"branch_id": 1, "y_offset_level": 0,
                                 "inputs": [{"type": "NO", "address": "X0", "label": "Input"}],
                                 "outputs": [{"type": "COIL", "address": "Y0", "label": "Output"}]}]}],
    }


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _assert_svg_theme(svg, theme):
    root = ElementTree.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    assert root.find(ns + "rect").get("fill") == ("#ffffff" if theme == "light" else "#181818")
    text_colors = {node.get("fill") for node in root.iter(ns + "text")}
    assert ("#1e1e1e" if theme == "light" else "#cccccc") in text_colors
    assert ("#0066b8" if theme == "light" else "#9cdcfe") in text_colors


def _legacy_workspace(root):
    store = SessionStore(base_dir=root, legacy_dir=root.parent)
    project = store.create_project("Legacy HTTP fixture", plc_model="FX3U")
    version, directory = store.prepare_version(project["id"])
    raw = json.dumps(_ladder(), ensure_ascii=False)
    (directory / "ladder.json").write_text(raw, encoding="utf-8")
    (directory / "ladder.svg").write_text(AdvancedSVGLadder().generate_ladder(raw), encoding="utf-8")
    store.complete_version(project["id"], version, {
        "target_mode": "ladder", "plc_model": "FX3U",
        "artifacts": {"json": "ladder.json", "svg": "ladder.svg"},
    })
    report = store.create_report(project["id"], {
        "report_type": "program_review", "base_version_id": version,
        "status": "complete", "summary": "Saved local report", "findings": [],
    })
    assert not (directory / "program.ir.json").exists()
    return store, project["id"], version, report["report_id"]


def _login(client):
    response = client.post("/api/session", json={"token": OPERATOR}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]}


def _app(workspace, state, **kwargs):
    return create_app(workspace, state_dir=state, origin=ORIGIN,
                      operator_token=OPERATOR, agent_token=AGENT, **kwargs)


def _complete(client, service, response):
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    service.jobs._futures[job_id].result(timeout=15)
    current = client.get("/api/jobs/" + job_id)
    assert current.status_code == 200, current.text
    assert current.json()["status"] == "completed", current.text
    return job_id, client.get("/api/jobs/" + job_id + "/output").json()


class _Provider:
    def __init__(self):
        self.requests = []
        self.wrong_language = False

    def stream(self, request):
        self.requests.append(request)
        if request.response_contract.name == "analysis":
            payload = {
                "summary": "The input controls the output.", "approaches": [], "missing_info": [],
                "suggested_io": {"X": {"X0": "Input"}, "Y": {"Y0": "Output"}}, "assumptions": [],
            }
        else:
            payload = _ladder()
            if self.wrong_language:
                payload["device_comments"]["Y0"] = "正在检查输入并准备输出。"
        raw = json.dumps(payload, ensure_ascii=False)
        yield TextDelta(raw[:len(raw) // 2])
        yield TextDelta(raw[len(raw) // 2:])


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "_build_model_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "load_full_config", lambda: {})
    monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("Unexpected real provider"))
    monkeypatch.setattr(api, "_save_history", lambda *a: pytest.fail("Unexpected global history write"))


def test_legacy_workspace_http_reads_never_migrate_or_initialize_execution(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    store, project, version, report = _legacy_workspace(workspace)
    before = _files(workspace)
    service = WorkbenchService(workspace, tmp_path / "state", read_only=True,
                               model_factory=lambda: pytest.fail("Model initialized while browsing"))
    import simulator.runtime
    monkeypatch.setattr(simulator.runtime, "get_simulator_gateway_runtime",
                        lambda *a, **k: pytest.fail("Gateway initialized while browsing"))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        _login(client)
        paths = ["/api/projects", "/api/projects/" + project,
                 f"/api/projects/{project}/versions/{version}",
                 f"/api/projects/{project}/versions/{version}/program",
                 f"/api/projects/{project}/versions/{version}/diagnostics",
                 f"/api/projects/{project}/versions/{version}/artifacts/svg",
                 f"/api/projects/{project}/reports/{report}"]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.headers["cache-control"] == "no-store"
        program = client.get(paths[3]).json()
        assert program["networks"][0]["id"] == "N0001"
        svg = client.get(paths[5])
        assert "image/svg+xml" in svg.headers["content-type"]
        assert "sandbox" in svg.headers["content-security-policy"]
        assert client.get(paths[6]).json()["summary"] == "Saved local report"
    assert _files(workspace) == before
    assert not (tmp_path / "state").exists()


def test_svg_artifact_theme_changes_real_colors_without_writing_legacy_files(tmp_path):
    workspace = tmp_path / "workspace"
    _, project_id, version_id, _ = _legacy_workspace(workspace)
    before = _files(workspace)
    service = WorkbenchService(workspace, tmp_path / "state", read_only=True,
        model_factory=lambda: pytest.fail("Theme changes must not initialize a model"))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        _login(client)
        artifact = f"/api/projects/{project_id}/versions/{version_id}/artifacts/svg"
        program = f"/api/projects/{project_id}/versions/{version_id}/program"
        ir = client.get(program).json()
        original = client.get(artifact).content
        for theme in ("light", "dark"):
            response = client.get(artifact, params={"theme": theme})
            assert response.status_code == 200
            assert "image/svg+xml" in response.headers["content-type"]
            assert "sandbox" in response.headers["content-security-policy"]
            _assert_svg_theme(response.text, theme)
        assert client.get(artifact, params={"theme": "unknown"}).status_code == 422
        assert client.get(artifact).content == original
        assert client.get(program).json() == ir
    assert _files(workspace) == before
    assert not (tmp_path / "state").exists()


def test_candidate_and_execution_preview_theme_preserves_ir_diff_and_proposal_hash(tmp_path, monkeypatch):
    import application.execution
    monkeypatch.setattr(application.execution, "read_gx_environment", lambda: {
        "status": "ready", "passed": True, "desktop_execution_required": True,
        "gx_works2_running": True, "project_open": True, "message": "GX Works2 已运行。",
    })
    workspace = tmp_path / "workspace"
    _, project_id, version_id, _ = _legacy_workspace(workspace)
    service = WorkbenchService(workspace, tmp_path / "state",
        model_factory=lambda: pytest.fail("Preview theme changes must not initialize a model"))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        candidate = service.agent_call({"project_id": project_id, "version_id": version_id,
            "name": "create_program_candidate", "arguments": {"ladder": _ladder()}, "call_id": "theme-candidate"})
        execution = client.post("/api/proposals", headers=headers, json={"action": "gx_import",
            "project_id": project_id, "version_id": version_id, "request_id": "theme-execution"})
        assert execution.status_code == 201, execution.text
        before = _files(workspace)
        for proposal_id in (candidate["proposal_id"], execution.json()["id"]):
            url = f"/api/proposals/{proposal_id}/preview"
            original = client.get(url)
            assert original.status_code == 200, original.text
            original = original.json()
            # Warm the managed candidate cache before checking subsequent requests.
            state_before = _files(tmp_path / "state")
            for theme in ("light", "dark"):
                response = client.get(url, params={"theme": theme})
                assert response.status_code == 200, response.text
                preview = response.json()
                _assert_svg_theme(preview["svg"], theme)
                assert {k: v for k, v in preview.items() if k != "svg"} == {k: v for k, v in original.items() if k != "svg"}
            assert _files(tmp_path / "state") == state_before
        assert _files(workspace) == before


def test_http_read_surface_imports_without_qt_model_or_automation(tmp_path):
    workspace = tmp_path / "workspace"
    _, project, version, _ = _legacy_workspace(workspace)
    script = '''
import importlib.abc, sys
class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "main" or fullname == "qt_compat" or fullname.startswith(("PyQt", "PySide", "pywinauto")):
            raise AssertionError("Desktop import: " + fullname)
sys.meta_path.insert(0, BlockDesktop())
from fastapi.testclient import TestClient
from integrations.web.app import create_app
from application.workbench import WorkbenchService
def forbidden():
    raise AssertionError("Model factory invoked")
service = WorkbenchService(sys.argv[1], sys.argv[2], read_only=True, model_factory=forbidden)
app = create_app(sys.argv[1], state_dir=sys.argv[2], origin="http://127.0.0.1:8765", operator_token="operator-test", service=service)
with TestClient(app, base_url="http://127.0.0.1:8765") as client:
    assert client.post("/api/session", json={"token":"operator-test"}).status_code == 200
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/projects/" + sys.argv[3] + "/versions/" + sys.argv[4] + "/program").status_code == 200
assert "qt_compat" not in sys.modules and "main" not in sys.modules
assert "api" not in sys.modules
assert "simulator.runtime" not in sys.modules
'''
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    result = subprocess.run([sys.executable, "-c", script, str(workspace), str(tmp_path / "state"), project, version],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_auth_host_origin_csrf_and_agent_approval_boundaries(tmp_path):
    app = _app(tmp_path / "workspace", tmp_path / "state")
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
        assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.options("/api/projects", headers={"Origin": ORIGIN}).status_code == 403
        assert client.post("/api/session", json={"token": AGENT}, headers={"Origin": ORIGIN}).status_code == 403
        assert client.post("/api/proposals/imaginary/decision", json={"decision": "accept"},
                           headers={"Origin": ORIGIN, "Authorization": "Bearer " + AGENT}).status_code == 401
        headers = _login(client)
        assert client.post("/api/projects", json={"name": "No CSRF"}).status_code == 403
        assert client.post("/api/projects", json={"name": "No origin"},
                           headers={"X-CSRF-Token": headers["X-CSRF-Token"]}).status_code == 403
        assert client.post("/api/projects", json={"name": "Wrong origin"},
                           headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/agent/tools").status_code == 403
        authorized = client.get("/api/agent/tools", headers={"Authorization": "Bearer " + AGENT})
        assert authorized.status_code == 200
        names = {tool["function"]["name"] for tool in authorized.json()["tools"]}
        assert "create_program_candidate" in names
        assert not {"approve_proposal", "write_plc", "force_device"} & names


def test_schema_failures_never_echo_secret_inputs(tmp_path):
    secret = "SECRET_DO_NOT_ECHO_624879"
    with TestClient(_app(tmp_path / "workspace", tmp_path / "state"), base_url=ORIGIN,
                    raise_server_exceptions=False) as client:
        headers = _login(client)
        cases = [("put", "/api/settings", {"api_key": {"value": secret}}),
                 ("post", "/api/projects", {"name": "test", "api_key": secret}),
                 ("post", "/api/jobs", {"kind": secret, "request_id": "invalid", "project_id": "p1"})]
        for method, path, body in cases:
            result = getattr(client, method)(path, json=body, headers=headers)
            assert result.status_code == 422, result.text
            assert secret not in result.text
            assert result.json()["error"]["code"] == "invalid_command"


def test_analysis_confirm_generate_preview_accept_and_replay_events(offline, tmp_path):
    provider = _Provider()
    workspace = tmp_path / "workspace"
    service = WorkbenchService(workspace, tmp_path / "state", model_factory=lambda: (provider, {"model": "offline"}))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        created = client.post("/api/projects", json={"name": "Generated project"}, headers=headers)
        assert created.status_code == 201, created.text
        project = created.json()["id"]
        command = {"kind": "analysis", "project_id": project, "request_id": "analysis-1",
                   "text": "FX3U X0 controls Y0", "response_language": "en"}
        analysis_job, analysis = _complete(client, service, client.post("/api/jobs", json=command, headers=headers))
        assert analysis["analysis"]["summary"] == "The input controls the output."
        assert analysis["spec_base_hash"] is None
        assert analysis["base_version_id"] is None
        confirmed = client.put(f"/api/projects/{project}/spec", json={"spec": analysis["spec_draft"], "expected_hash": None}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["valid"] is True, confirmed.text
        # Replaying an old task must carry its original basis so a client cannot
        # silently apply it as a draft against the newly confirmed specification.
        saved_project = client.get("/api/projects/" + project).json()
        replayed = client.get(f"/api/jobs/{analysis_job}/output").json()
        assert replayed["spec_base_hash"] is None
        assert saved_project["confirmed_spec_hash"] is not None
        assert client.get("/api/projects/" + project).json() == saved_project
        command.update(kind="generation", request_id="generation-1")
        submission = client.post("/api/jobs", json=command, headers=headers)
        job, generated = _complete(client, service, submission)
        repeated = client.post("/api/jobs", json=command, headers=headers)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["id"] == job
        assert len(provider.requests) == 2
        proposal = generated["proposal_id"]
        pending = client.get("/api/proposals/" + proposal)
        assert pending.json()["status"] == "accepted"
        assert generated["version_id"] == pending.json()["result"]["version_id"]
        assert "_candidate_ir" not in pending.text
        assert "_confirmed_spec" not in pending.text
        assert client.get("/api/projects/" + project).json()["version_count"] == 1
        preview = client.get("/api/proposals/" + proposal + "/preview")
        assert preview.status_code == 200, preview.text
        assert "<svg" in preview.json()["svg"]
        assert preview.json()["ladder"]["rungs"] == _ladder()["rungs"]
        all_events = client.get(f"/api/jobs/{job}/events")
        assert all_events.status_code == 200
        ids = [int(line[4:]) for line in all_events.text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(set(ids))
        assert len(ids) >= 4
        cursor = ids[len(ids) // 2]
        resumed = client.get(f"/api/jobs/{job}/events", headers={"Last-Event-ID": str(cursor)})
        resumed_ids = [int(line[4:]) for line in resumed.text.splitlines() if line.startswith("id: ")]
        assert resumed_ids == [value for value in ids if value > cursor]
        assert client.get(f"/api/jobs/{job}").json()["status"] == "completed"
        first = client.post(f"/api/proposals/{proposal}/decision", json={"decision": "accept"}, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["proposal"]["status"] == "accepted"
        second = client.post(f"/api/proposals/{proposal}/decision", json={"decision": "accept"}, headers=headers)
        assert second.status_code == 200, second.text
        assert first.json()["proposal"]["result"] == second.json()["proposal"]["result"]
        assert client.get("/api/projects/" + project).json()["version_count"] == 1
        assert [r.response_language for r in provider.requests] == ["en", "en"]


def test_agent_candidate_is_persisted_by_service_and_only_operator_accepts(tmp_path):
    workspace = tmp_path / "workspace"
    store = SessionStore(base_dir=workspace, legacy_dir=tmp_path)
    project = store.create_project("Connected agent candidate")["id"]
    before = _files(workspace)
    app = _app(workspace, tmp_path / "state")
    with TestClient(app, base_url=ORIGIN) as client:
        command = {"project_id": project, "name": "create_program_candidate", "arguments": {"ladder": _ladder()}, "call_id": "candidate-1"}
        result = client.post("/api/agent/tools/call", json=command, headers={"Authorization": "Bearer " + AGENT})
        assert result.status_code == 200, result.text
        assert result.json()["is_error"] is False, result.text
        assert result.json()["data"]["status"] == "confirmation_required"
        proposal = result.json()["proposal_id"]
        assert "_candidate_ir" not in result.text
        assert "_confirmed_spec" not in result.text
        assert _files(workspace) == before
        repeated = client.post("/api/agent/tools/call", json=command, headers={"Authorization": "Bearer " + AGENT})
        assert repeated.json()["proposal_id"] == proposal
        denied = client.post(f"/api/proposals/{proposal}/decision", json={"decision": "accept"},
                             headers={"Authorization": "Bearer " + AGENT, "Origin": ORIGIN})
        assert denied.status_code == 401
        headers = _login(client)
        accepted = client.post(f"/api/proposals/{proposal}/decision", json={"decision": "accept"}, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["proposal"]["status"] == "accepted"
        assert store.get_project(project)["active_version_id"]
        assert len(store.get_project(project)["versions"]) == 1


def test_artifact_paths_cannot_escape_the_selected_version(tmp_path):
    workspace = tmp_path / "workspace"
    store, project, version, _ = _legacy_workspace(workspace)
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("NEVER_RETURN_FILE_CONTENT", encoding="utf-8")
    record = store.get_project(project)
    record["versions"][0]["artifacts"]["leak"] = str(secret)
    store.save_project(record)
    with TestClient(_app(workspace, tmp_path / "state", read_only=True), base_url=ORIGIN) as client:
        _login(client)
        for path in [f"/api/projects/{project}/versions/{version}/artifacts/leak",
                     "/api/projects/%2e%2e%5coutside-secret.txt"]:
            result = client.get(path)
            assert result.status_code in (400, 404), result.text
            assert "NEVER_RETURN_FILE_CONTENT" not in result.text
            assert str(secret) not in result.text


def test_two_tabs_cannot_accept_a_candidate_after_another_base_is_active(tmp_path):
    workspace = tmp_path / "workspace"
    store, project, version, _ = _legacy_workspace(workspace)
    with TestClient(_app(workspace, tmp_path / "state"), base_url=ORIGIN) as client:
        proposals = []
        for suffix in ("a", "b"):
            response = client.post("/api/agent/tools/call", json={
                "project_id": project, "version_id": version, "name": "create_program_candidate",
                "arguments": {"ladder": _ladder()}, "call_id": "tab-" + suffix,
            }, headers={"Authorization": "Bearer " + AGENT})
            assert response.status_code == 200, response.text
            proposals.append(response.json()["proposal_id"])
        headers = _login(client)
        first = client.post(f"/api/proposals/{proposals[1]}/decision", json={"decision": "accept"}, headers=headers)
        assert first.status_code == 200, first.text
        active = store.get_project(project)["active_version_id"]
        assert active != version
        stale = client.post(f"/api/proposals/{proposals[0]}/decision", json={"decision": "accept"}, headers=headers)
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "conflict"
        assert store.get_project(project)["active_version_id"] == active
        assert len(store.get_project(project)["versions"]) == 2


def test_refresh_during_generation_reads_same_running_job_without_restart(offline, tmp_path):
    entered, release = threading.Event(), threading.Event()
    class PausedProvider(_Provider):
        def stream(self, request):
            entered.set()
            assert release.wait(10), "Offline generation was not released"
            yield from super().stream(request)
    provider = PausedProvider()
    workspace = tmp_path / "workspace"
    store = SessionStore(base_dir=workspace, legacy_dir=tmp_path)
    project = store.create_project("Refresh during generation")["id"]
    service = WorkbenchService(workspace, tmp_path / "state", model_factory=lambda: (provider, {"model": "offline"}))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post("/api/jobs", json={
            "kind": "generation", "project_id": project, "request_id": "refresh-generation",
            "text": "FX3U X0 controls Y0", "response_language": "en",
        }, headers=headers)
        try:
            assert response.status_code == 202, response.text
            job = response.json()["id"]
            assert entered.wait(5)
            current = client.get("/api/jobs/" + job)
            assert current.status_code == 200
            assert current.json()["status"] == "running"
            listed = client.get("/api/jobs", params={"project_id": project}).json()["jobs"]
            assert [item["id"] for item in listed] == [job]
            assert client.get("/api/session").json()["authenticated"] is True
            assert client.get(f"/api/jobs/{job}/output").status_code == 404
        finally:
            release.set()
        _, output = _complete(client, service, response)
        assert output["proposal_id"]
        assert len(provider.requests) == 1
        assert len(store.get_project(project)["versions"]) == 1
        assert store.get_project(project)["active_version_id"] == output["version_id"]


def test_live_activity_is_persisted_before_final_content_or_candidate_exists(offline, tmp_path):
    entered, release = threading.Event(), threading.Event()
    class PausedProvider(_Provider):
        def stream(self, request):
            yield TextDelta('{"summary":"')
            entered.set()
            assert release.wait(10)
            yield TextDelta('启停控制","approaches":[],"missing_info":[]}')
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                              model_factory=lambda: (PausedProvider(), {"model": "offline"}))
    with TestClient(_app(tmp_path / "workspace", tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = service.create_project(name="Live analysis")["id"]
        response = client.post("/api/jobs", headers=headers, json={"kind": "analysis", "project_id": project,
            "request_id": "live-analysis", "text": "起保停", "response_language": "zh-CN"})
        try:
            assert response.status_code == 202
            job = response.json()["id"]
            assert entered.wait(5)
            assert client.get("/api/jobs/" + job).json()["status"] == "running"
            events = service.jobs.events(job)
            assert any(e["event_type"] == "model_progress" and e["payload"]["phase"] == "receiving" for e in events)
            assert any(e["event_type"] == "model_preview" and e["payload"].get("content") == '{"summary":"' for e in events)
            assert not any(e["event_type"] in ("content", "reasoning", "completed") for e in events)
            assert client.get(f"/api/jobs/{job}/output").status_code == 404
            assert client.get("/api/proposals").json()["proposals"] == []
        finally:
            release.set()
        _complete(client, service, response)


def test_language_preference_does_not_block_a_valid_program_autosave(offline, tmp_path):
    workspace = tmp_path / "workspace"
    store = SessionStore(base_dir=workspace, legacy_dir=tmp_path)
    project = store.create_project("Rejected language")["id"]
    provider = _Provider()
    provider.wrong_language = True
    before = _files(workspace)
    service = WorkbenchService(workspace, tmp_path / "state", model_factory=lambda: (provider, {"model": "offline"}))
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post("/api/jobs", json={
            "kind": "generation", "project_id": project, "request_id": "rejected-generation",
            "text": "FX3U X0 controls Y0", "response_language": "en",
        }, headers=headers)
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        service.jobs._futures[job].result(timeout=15)
        state = client.get("/api/jobs/" + job).json()
        assert state["status"] == "completed"
        assert len(provider.requests) == 1
        assert provider.requests[0].enforce_response_language is False
        proposals = client.get("/api/proposals").json()["proposals"]
        assert len(proposals) == 1 and proposals[0]["status"] == "accepted"
        assert client.get(f"/api/jobs/{job}/output").status_code == 200
        assert list((tmp_path / "state" / "staging").rglob("*.svg"))
    assert _files(workspace) != before
    assert len(store.get_project(project)["versions"]) == 1


def test_retry_uses_original_command_after_project_messages_change(offline, tmp_path):
    workspace = tmp_path / "workspace"
    store = SessionStore(base_dir=workspace, legacy_dir=tmp_path)
    project = store.create_project("Stable request identity")["id"]
    provider = _Provider()
    captures = []
    def snapshot():
        captures.append(True)
        return provider, {"model": "offline"}
    service = WorkbenchService(workspace, tmp_path / "state", model_factory=snapshot)
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        command = {"kind": "analysis", "project_id": project, "request_id": "stable-analysis-command",
                   "text": "FX3U X0 controls Y0", "response_language": "en"}
        original = client.post("/api/jobs", json=command, headers=headers)
        job, _ = _complete(client, service, original)
        with service.lock.thread_lock:
            store.add_message(project, "assistant", "Another completed conversation message.")
        retried = client.post("/api/jobs", json=command, headers=headers)
        assert retried.status_code == 202, retried.text
        assert retried.json()["id"] == job
        assert retried.json()["status"] == "completed"
        assert len(provider.requests) == len(captures) == 1
        assert len(client.get("/api/jobs").json()["jobs"]) == 1
        changed = client.post("/api/jobs", json={**command, "text": "Changed command with reused identity"}, headers=headers)
        assert changed.status_code == 409


def test_cancel_approved_job_waiting_for_engineering_lock_never_calls_executor(tmp_path, monkeypatch):
    from concurrent.futures import Future
    workspace = tmp_path / "workspace"
    _, project, version, _ = _legacy_workspace(workspace)
    service = WorkbenchService(workspace, tmp_path / "state")
    import application.execution
    monkeypatch.setattr(application.execution, "read_gx_environment", lambda: {
        "status": "ready", "passed": True, "desktop_execution_required": True,
        "gx_works2_running": True, "project_open": True, "message": "GX Works2 已运行。",
    })
    attempts, release, held = threading.Event(), threading.Event(), threading.Event()
    executor_calls, holders = [], []
    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        created = client.post("/api/proposals", json={"action": "gx_import", "project_id": project,
                              "version_id": version, "request_id": "cancel-approved-import"}, headers=headers)
        assert created.status_code == 201, created.text
        proposal = created.json()["id"]
        underlying = service.lock.thread_lock

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name.startswith("plc-job"):
                    attempts.set()
                underlying.acquire()
                return self
            def __exit__(self, *_args):
                underlying.release()

        def hold_engineering_lock():
            with underlying:
                held.set()
                assert release.wait(10), "Test did not release engineering lock"

        original_submit = service.jobs.submit
        def gated_submit(kind, snapshot, worker, request_id=None):
            holder = threading.Thread(target=hold_engineering_lock)
            holders.append(holder)
            holder.start()
            assert held.wait(5)
            return original_submit(kind, snapshot, worker, request_id=request_id)

        def fake_executor(*args, **kwargs):
            executor_calls.append((args, kwargs))
            future = Future()
            future.set_result({"status": "unavailable", "passed": False})
            return future

        monkeypatch.setattr(service.lock, "thread_lock", ObservedLock())
        monkeypatch.setattr(service.jobs, "submit", gated_submit)
        monkeypatch.setattr(service.execution, "submit_approved", fake_executor)
        try:
            approved = client.post(f"/api/proposals/{proposal}/decision", json={"decision": "accept"}, headers=headers)
            assert approved.status_code == 200, approved.text
            job = approved.json()["job"]["id"]
            # This event fires at the actual engineering-lock acquire attempt,
            # after any misplaced checkpoint before that lock would have run.
            assert attempts.wait(5)
            assert client.get("/api/jobs/" + job).json()["status"] == "running"
            cancelled = client.post(f"/api/jobs/{job}/cancel", headers=headers)
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["cancel_requested"] is True
        finally:
            release.set()
            for holder in holders:
                holder.join(timeout=5)
        service.jobs._futures[job].result(timeout=5)
        assert client.get("/api/jobs/" + job).json()["status"] == "cancelled"
        assert executor_calls == []
        assert client.get("/api/proposals/" + proposal).json()["status"] == "pending"
