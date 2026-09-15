from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from model_provider import TextDelta
from test_web_api import ORIGIN, _app, _login, offline


class BrokenCompactProvider:
    def __init__(self):
        self.requests = []
        self.profile = {"capabilities": {}}

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("missing-quote repair must be deterministic and model-free")
        # One complete compact rung except for the missing quote after D106.
        yield TextDelta('{"r":[{"b":[{"i":["NO X0","> D220 D106],"o":["COIL Y0"]}]}]}')


def test_preserved_invalid_candidate_can_be_repaired_and_immediately_rendered(offline, tmp_path):
    provider = BrokenCompactProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "repair-render"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {
            "summary": "X0 controls Y0 after the tracked position passes D106",
            "io_table": [],
            "parameters": [],
        })

        created = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "broken-compact",
            "text": "generate confirmed ladder",
            "response_language": "zh-CN",
        })
        assert created.status_code == 202, created.text
        bad_job = created.json()["id"]
        service.jobs._futures[bad_job].result(timeout=15)

        preserved = client.get(f"/api/jobs/{bad_job}").json()
        assert preserved["status"] == "completed", preserved
        assert preserved["result"]["status"] == "saved_invalid"
        bad_version = preserved["result"]["version_id"]
        bad_preview = client.get(f"/api/projects/{project}/versions/{bad_version}/preview")
        assert bad_preview.status_code == 200, bad_preview.text
        assert "<svg" in bad_preview.json()["svg"]

        response = client.post(
            f"/api/jobs/{bad_job}/repair",
            headers=headers,
            json={"request_id": "repair-broken-compact"},
        )
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)

        repaired = client.get(f"/api/jobs/{repair_job}").json()
        assert repaired["status"] == "completed", repaired
        assert repaired["result"]["status"] == "saved"
        repaired_version = repaired["result"]["version_id"]
        assert repaired_version != bad_version
        assert len(provider.requests) == 1, "deterministic format repair must not call model"

        job_preview = client.get(f"/api/jobs/{repair_job}/preview")
        assert job_preview.status_code == 200, job_preview.text
        assert "<svg" in job_preview.json()["svg"]

        version = client.get(f"/api/projects/{project}/versions/{repaired_version}").json()
        artifacts = {item["id"]: item for item in version["artifacts"]}
        for artifact_id in ("svg", "program_csv", "comment_csv"):
            assert artifacts[artifact_id]["available"] is True
            data = client.get(
                f"/api/projects/{project}/versions/{repaired_version}/artifacts/{artifact_id}"
            )
            assert data.status_code == 200 and data.content
