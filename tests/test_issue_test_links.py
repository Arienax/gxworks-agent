"""Issue cards link only real, readable plans bound to the selected version."""
import copy
import json

import pytest

from application.exploration import issues
from application.projects import ProjectService
from tests.test_simulation_workbench import scenario, save_plan
from tests.test_plc_debug_loop import _suite


def report(store, pid, vid):
    return store.create_report(pid, {"report_id": "review-links", "base_version_id": vid,
        "findings": [{"finding_id": "stop", "title": "确认停止行为", "addresses": ["Y0"], "rung_ids": [2],
                      "message": "检查停止后输出。", "evidence": ["输入停止后应断开输出。"]}]})


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_issue_lists_exact_saved_test_names_and_read_only_lookup_does_not_write(scenario):
    store, _service, _simulation, pid, vid, _ = scenario
    review = report(store, pid, vid)
    draft = _suite()
    draft["tests"].append({**copy.deepcopy(draft["tests"][0]), "name": "stop_again"})
    plan = save_plan(scenario, suite=draft, requirement_links={}, issue_ids=["review-links:stop"])
    save_plan(scenario, issue_ids=["unrelated:stop"])
    before = files(store.base_dir)
    result = issues(ProjectService(store.base_dir), pid, vid, review["report_id"])
    assert result["issues"][0]["tests"] == [{"plan_id": plan["binding"]["plan_id"], "test_names": ["motor_stop", "stop_again"]}]
    assert "suite" not in result["issues"][0] and "metadata" not in result["issues"][0]
    assert files(store.base_dir) == before


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "wrong_plan_version", "wrong_metadata_version"])
def test_missing_corrupt_or_cross_version_plans_are_not_presented_as_issue_links(scenario, damage):
    store, _service, _simulation, pid, vid, _ = scenario
    report(store, pid, vid)
    good = save_plan(scenario, issue_ids=["review-links:stop"])
    bad = save_plan(scenario, issue_ids=["review-links:stop"])
    index = next(row for row in store.get_version(pid, vid)["simulator_test_plans"] if row["plan_id"] == bad["binding"]["plan_id"])
    path = store.version_dir(pid, vid) / index["plan_artifact"]
    if damage == "missing":
        path.unlink()
    elif damage == "invalid_json":
        path.write_text("not json", encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if damage == "wrong_plan_version":
            payload["binding"]["version_id"] = "another-version"
        else:
            payload["suite"]["tests"][0]["metadata"]["workbench"]["version_id"] = "another-version"
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = issues(ProjectService(store.base_dir), pid, vid, "review-links")
    assert result["issues"][0]["tests"] == [{"plan_id": good["binding"]["plan_id"], "test_names": ["motor_stop"]}]


def test_issue_links_cross_http_boundary_without_granting_write_or_execution(scenario):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from integrations.web.app import create_app
    store, service, _simulation, pid, vid, _ = scenario
    report(store, pid, vid)
    plan = save_plan(scenario, issue_ids=["review-links:stop"])
    app = create_app(store.base_dir, service=service, operator_token="operator")
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    route = f"/api/projects/{pid}/versions/{vid}/issues?report_id=review-links"
    assert client.get(route).status_code == 401
    client.post("/api/session", json={"token": "operator"})
    before = files(store.base_dir)
    result = client.get(route)
    assert result.status_code == 200, result.text
    assert result.json()["issues"][0]["tests"][0]["plan_id"] == plan["binding"]["plan_id"]
    assert service.proposals.list() == [] and files(store.base_dir) == before
