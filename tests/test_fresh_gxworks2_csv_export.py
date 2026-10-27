import csv
import io
import json
import zipfile

from application.fresh_exports import build_gxworks2_csv_bundle
from plc.ir import build_plc_ir, canonical_sha256


def _range_network(output="M30"):
    branches = []
    for index in range(16):
        branches.append([
            {"type": "NO", "address": f"M{100 + index}", "label": ""},
            {"type": "BLOCK_INPUT", "expression": f">= D{200 + index} K1", "label": ""},
            {"type": "BLOCK_INPUT", "expression": f"<= D{200 + index} K3", "label": ""},
            {"type": "BLOCK_INPUT", "expression": f">= D{220 + index} D101", "label": ""},
            {"type": "BLOCK_INPUT", "expression": f"<= D{220 + index} D102", "label": ""},
        ])
    return {
        "rung_id": 492,
        "debug_note": "",
        "header_element": None,
        "shared_inputs": [],
        "branches": [{
            "branch_id": 1,
            "y_offset_level": 0,
            "inputs": [{"type": "parallel_block", "branches": branches}],
            "outputs": [{"type": "COIL", "address": output, "label": ""}],
        }],
    }


class _Projects:
    def __init__(self, program):
        self._program = program
        self._version = {
            "id": "v-existing",
            "target_mode": "ladder",
            "ir_sha256": canonical_sha256(program),
        }
        self.program_calls = 0

    def raw_version(self, project_id, version_id):
        assert project_id == "p1"
        assert version_id == "v-existing"
        return dict(self._version)

    def program(self, project_id, version_id):
        self.program_calls += 1
        return self._program


def _program_rows(raw):
    text = raw.decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))


def _out_terminated_blocks(rows):
    blocks = []
    current = []
    for row in rows[3:]:
        if len(row) < 4 or not str(row[2] or "").strip():
            continue
        if row[2] == "END":
            break
        current.append(row)
        if row[2] == "OUT":
            blocks.append(current)
            current = []
    assert not current
    return blocks


def test_fresh_export_rebuilds_csv_from_saved_ir_without_changing_version():
    ladder = {"device_comments": {}, "rungs": [_range_network()]}
    program = build_plc_ir(ladder, plc_model="FX3U", program_name="MAIN")
    projects = _Projects(program)

    first = build_gxworks2_csv_bundle(projects, "p1", "v-existing")
    second = build_gxworks2_csv_bundle(projects, "p1", "v-existing")

    # Fixed ZIP metadata makes identical saved IR produce identical fresh bytes.
    assert first == second
    assert projects.program_calls == 2
    assert projects._version["ir_sha256"] == canonical_sha256(program)

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert sorted(archive.namelist()) == ["COMMENT.csv", "MAIN.csv", "manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["model_called"] is False
        assert manifest["version_id"] == "v-existing"
        rows = _program_rows(archive.read("MAIN.csv"))

    blocks = _out_terminated_blocks(rows)
    assert [len(block) for block in blocks] == [24, 24, 24, 24, 5]
    assert max(map(len, blocks)) <= 24
    assert blocks[-1][-1][2:4] == ["OUT", "M30"]


def test_fresh_export_rejects_non_ladder_version():
    ladder = {"device_comments": {}, "rungs": [_range_network()]}
    program = build_plc_ir(ladder, plc_model="FX3U", program_name="MAIN")
    projects = _Projects(program)
    projects._version["target_mode"] = "st"

    try:
        build_gxworks2_csv_bundle(projects, "p1", "v-existing")
    except ValueError as error:
        assert "梯形图" in str(error)
    else:
        raise AssertionError("non-ladder fresh export must be rejected")


# One HTTP sentinel verifies that the saved-IR export stays model-free and read-only.
def test_saved_version_can_reexport_gxworks2_csv_without_model_or_mutation(tmp_path):
    import pytest
    pytest.importorskip("fastapi", reason="Web integration requires requirements-web.txt")
    pytest.importorskip("httpx", reason="Web integration requires requirements-web.txt")
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from tests.test_web_api import ORIGIN, _app, _files, _legacy_workspace, _login
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
