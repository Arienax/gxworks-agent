"""Generated plans must complete their job after immutable persistence."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from model_runtime.provider import TextDelta
from test_web_api import ORIGIN, _app, _login
from test_workbench_service import saved


def test_generated_test_plan_job_returns_persisted_binding_and_retries_without_model(saved, tmp_path):
    store, project, version, _program = saved
    suite = {"name": "Generated start/stop", "plc_model": "FX3U", "tests": [{
        "name": "Input follows output", "initial": {"X0": 0},
        "steps": [{"id": "start", "at_ms": 10, "set": {"X0": 1}, "expect": {"Y0": 1}},
                  {"id": "stop", "at_ms": 20, "set": {"X0": 0}, "expect": {"Y0": 0}}],
        "trace_devices": ["X0", "Y0"], "sample_ms": 5, "timeout_ms": 100,
    }]}

    class Provider:
        calls = 0

        def stream(self, request):
            self.calls += 1
            yield TextDelta(json.dumps(suite))

    provider = Provider()
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (provider, {"model": "offline"}))
    with TestClient(_app(store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        command = {"kind": "test_plan", "project_id": project["id"], "version_id": version["id"],
                   "request_id": "generate-one-plan", "response_language": "en"}
        submitted = client.post("/api/jobs", json=command, headers=headers)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["id"]
        service.jobs._futures[job_id].result(timeout=15)
        completed = client.get(f"/api/jobs/{job_id}").json()
        assert completed["status"] == "completed", completed
        output = client.get(f"/api/jobs/{job_id}/output")
        assert output.status_code == 200, output.text
        payload = output.json()
        plan_id = payload["plan"]["binding"]["plan_id"]
        assert payload["plan_id"] == completed["result"]["plan_id"] == plan_id
        state = client.get(f'/api/projects/{project["id"]}/versions/{version["id"]}/simulation-workbench').json()
        assert state["plans"][0]["binding"]["plan_id"] == plan_id
        assert [test["name"] for test in state["plans"][0]["suite"]["tests"]] == ["Input follows output"]
        retry = client.post("/api/jobs", json=command, headers=headers)
        assert retry.status_code == 202 and retry.json()["id"] == job_id
        assert retry.json()["status"] == "completed"
        assert provider.calls == 1
        records = service.projects.raw_version(project["id"], version["id"])["simulator_test_plans"]
        assert len(records) == 1 and records[0]["plan_id"] == plan_id
