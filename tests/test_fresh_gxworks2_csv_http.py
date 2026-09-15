import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi", reason="Web integration requires requirements-web.txt")
pytest.importorskip("httpx", reason="Web integration requires requirements-web.txt")
from fastapi.testclient import TestClient

from application.workbench import WorkbenchService
from tests.test_web_api import ORIGIN, _app, _files, _legacy_workspace, _login


def test_saved_version_can_reexport_gxworks2_csv_without_model_or_mutation(tmp_path):
    workspace = tmp_path / "workspace"
    _, project_id, version_id, _ = _legacy_workspace(workspace)
    before = _files(workspace)
    service = WorkbenchService(
        workspace,
        tmp_path / "state",
        read_only=True,
        model_factory=lambda: pytest.fail("fresh CSV export must not initialize a model"),
    )

    with TestClient(_app(workspace, tmp_path / "state", service=service), base_url=ORIGIN) as client:
        _login(client)
        response = client.get(
            f"/api/projects/{project_id}/versions/{version_id}/exports/gxworks2-csv"
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert sorted(archive.namelist()) == ["COMMENT.csv", "MAIN.csv", "manifest.json"]
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["project_id"] == project_id
            assert manifest["version_id"] == version_id
            assert manifest["model_called"] is False
            assert archive.read("MAIN.csv").startswith((b"\xff\xfe", b"\xfe\xff"))

    assert _files(workspace) == before
    assert not (tmp_path / "state").exists()
