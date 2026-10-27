"""Browser fixtures exercise the app without host registrations or device access."""
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from scripts.web_demo import isolated_demo_app


ORIGIN = "http://127.0.0.1:8765"


def login(client):
    response = client.post("/api/session", json={"token": "isolated-browser-acceptance"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]}


def test_demo_has_version_bound_issue_plan_requirement_and_failed_memory_replay(tmp_path):
    with isolated_demo_app(tmp_path, model_delay=0) as (app, ids), TestClient(app, base_url=ORIGIN) as client:
        login(client)
        route = f'/api/projects/{ids["project_id"]}/versions/{ids["version_id"]}'
        issues = client.get(route + "/issues?report_id=review-demo").json()["issues"]
        assert issues[0]["id"] == "review-demo:stop"
        assert issues[0]["tests"] == [{"plan_id": ids["plan_id"], "test_names": ["demo_motor_stop"]}]
        assert "内存夹具" in issues[0]["message"]
        state = client.get(route + "/simulation-workbench").json()
        assert state["requirements"][0]["id"] == "SEM001"
        assert state["plans"][0]["issue_ids"] == ["review-demo:stop"]
        assert state["plans"][0]["requirement_links"] == {"demo_motor_stop": ["SEM001"]}
        assert state["requirements"][0]["latest_run"]["status"] == "failed"
        from application.simulation_workbench import SimulationWorkbenchService
        replay = SimulationWorkbenchService(app.state.service).replay(ids["project_id"], ids["version_id"], ids["run_id"])
        case = replay["cases"][0]
        assert case["backend_kind"] == "test_memory_not_plc_simulator"
        assert replay["status"] == "failed" and case["observations"]
        assertion = next(row for row in case["assertions"] if row["step_id"] == "stop")
        assert assertion["actual"] == 1 and assertion["expected"]["value"] == 0
        assert any(row["values"].get("X1") == 1 and row["values"].get("Y0") == 1 for row in case["observations"])


def test_demo_refuses_mcp_and_hardware_without_reading_host_configuration(tmp_path, monkeypatch):
    import application.mcp_integrations as mcp
    import application.hardware as hardware

    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline browser fixture reached a host integration")

    for name in ("load_service_binding", "bind_project", "_codex_config_path", "_run"):
        monkeypatch.setattr(mcp, name, forbidden)
    monkeypatch.setattr(hardware, "HardwareReader", forbidden)
    monkeypatch.setenv("GX_HARDWARE_READER_EXE", str(tmp_path / "user-configured-reader.exe"))
    with isolated_demo_app(tmp_path, model_delay=0) as (app, ids), TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        pid, vid = ids["project_id"], ids["version_id"]
        response = client.get("/api/integrations/mcp", params={"project_id": pid})
        assert response.status_code == 400
        assert "离线演示不注册" in response.json()["error"]["message"]
        for endpoint in ("test", "codex/connect"):
            response = client.post("/api/integrations/mcp/" + endpoint, json={"project_id": pid}, headers=headers)
            assert response.status_code == 200
            assert response.json()["status"] == "failed" and "离线演示不注册" in response.json()["message"]
        route = f"/api/projects/{pid}/versions/{vid}/hardware"
        status = client.get(route).json()
        assert not status["reader"]["available"] and status["reader"]["backend"] == "disabled_demo"
        response = client.post(route + "/sessions", headers=headers, json={
            "logical_station": 0, "target_label": "offline target", "addresses": ["X0"], "ttl_seconds": 60})
        assert response.status_code == 400 and "离线演示禁止" in response.json()["error"]["message"]
        assert app.state.service.hardware.leases == {}
        with pytest.raises(hardware.HardwareError, match="离线演示禁止"):
            app.state.service.hardware.reader.read_once(0, ["X0"], "FX3U")


def test_demo_generation_autosaves_once_and_scope_marker_is_rejected(tmp_path, capsys):
    with isolated_demo_app(tmp_path, model_delay=0) as (app, ids), TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        service = app.state.service
        pid, vid = ids["project_id"], ids["version_id"]

        def generate(request_id, text, version_id):
            response = client.post("/api/jobs", headers=headers, json={"kind": "generation", "project_id": pid,
                "version_id": version_id, "request_id": request_id, "text": text, "response_language": "zh-CN",
                "change_scope": {"network_ids": ["N0001"]}})
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            service.jobs._futures[job_id].result(timeout=20)
            return service.jobs.get(job_id)

        job = generate("demo-normal", "正常生成，敏感演示标记不应写入计数日志", vid)
        assert job["status"] == "completed", job
        project = service.projects.raw_project(pid)
        assert len(project["versions"]) == 2
        job = generate("demo-outside", "验收越界", project["active_version_id"])
        assert job["status"] == "failed" and job["error_code"] == "change_scope_violation"
        assert len(service.projects.raw_project(pid)["versions"]) == 2
        logs = capsys.readouterr().err
        calls = re.findall(r"^DEMO_MODEL_CALL (\d+) generation$", logs, re.M)
        assert len(calls) == 2 and int(calls[1]) == int(calls[0]) + 1
        assert "验收越界" not in logs and "敏感演示标记" not in logs
