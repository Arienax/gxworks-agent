import copy
import json

from fastapi.testclient import TestClient

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from application.workbench import WorkbenchService
from model_provider import TextDelta
from test_web_api import ORIGIN, _app, _ladder, _login, offline


def _multi_rung_ladder(count=49):
    comments = {}
    rungs = []
    for index in range(count):
        suffix = format(index, "o")
        x_addr = f"X{suffix}"
        y_addr = f"Y{suffix}"
        comments[x_addr] = f"Input {index + 1}"
        comments[y_addr] = f"Output {index + 1}"
        rungs.append({
            "rung_id": index + 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": x_addr, "label": comments[x_addr]}],
                "outputs": [{"type": "COIL", "address": y_addr, "label": comments[y_addr]}],
            }],
        })
    return {"device_comments": comments, "rungs": rungs}


def test_incremental_prompt_keeps_only_partial_edit_hint(tmp_path):
    observed = {}

    def stream(user_input, *args, **kwargs):
        observed["input"] = user_input
        return "", json.dumps(_ladder(), ensure_ascii=False)

    GenerationWorkflow(
        GenerationRequest(
            "把X0改为上升沿",
            previous_json=_ladder(),
            model_name="offline",
        ),
        tmp_path,
        dependencies=GenerationDependencies(stream_response=stream),
    ).run()
    prompt = observed["input"]
    assert '优先返回 mode="partial"' in prompt
    assert "不要重复输出未修改梯级" in prompt
    assert "输出协议纪律" not in prompt
    assert "debug_note 是可选字段，默认省略" not in prompt
    assert "不要用 debug_note 记录推理" not in prompt
    assert "目标不超过48字符" not in prompt
    assert "已有 device_comments 无必要不要改写" not in prompt


class ReportShapeRepairProvider:
    """Reproduce the uploaded failure topology without teaching production code the case."""

    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = _multi_rung_ladder()
            # Match the uploaded report's first structural failure location:
            # rungs[5].branches[0].outputs[0].type. The two-character punctuation
            # tail separately exercises the existing safe local JSON-tail recovery.
            payload["rungs"][5]["branches"][0]["outputs"][0]["type"] = "BROKEN_OUTPUT"
            yield TextDelta(json.dumps(payload, ensure_ascii=False) + ";;")
            return

        repair_payload = json.loads(str(request.messages[-1].content))
        assert repair_payload["repair_mode"] == "partial"
        assert repair_payload["allowed_rung_ids"] == [6]
        assert [rung["rung_id"] for rung in repair_payload["baseline_subset"]["rungs"]] == [6]

        replacement = copy.deepcopy(repair_payload["baseline_subset"]["rungs"][0])
        replacement["branches"][0]["outputs"][0]["type"] = "COIL"
        yield TextDelta(json.dumps({
            "mode": "partial",
            "device_comments": {},
            "rungs": [replacement],
            "delete_rung_ids": [],
        }, ensure_ascii=False))


def test_uploaded_report_shape_repairs_with_one_explicit_rung_patch(offline, tmp_path):
    provider = ReportShapeRepairProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )

    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "report-shape-repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {
            "summary": "Independent input/output rungs",
            "io_table": [],
            "parameters": [],
        })

        created = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "report-shape-generation",
            "text": "Generate the confirmed program",
            "response_language": "zh-CN",
        })
        assert created.status_code == 202, created.text
        bad_job = created.json()["id"]
        service.jobs._futures[bad_job].result(timeout=15)

        failed = client.get(f"/api/jobs/{bad_job}").json()
        assert failed["status"] == "failed"
        assert failed["error_code"] == "generation_validation_failed"
        assert failed["error_details"]["attempt_count"] == 0
        assert failed["error_details"]["violations"][0] == {
            "path": "content$.rungs.5.branches.0.outputs.0.type",
            "reason": "invalid_ladder_structure",
        }
        assert len(provider.requests) == 1, "normal generation must not auto-repair"
        assert service.projects.project(project)["version_count"] == 0
        candidate = service.state_dir / "staging" / bad_job / "repair_candidate.json"
        assert candidate.is_file()
        # The punctuation tail is local transport cleanup; the retained candidate
        # is the complete parseable ladder that actually failed structural validation.
        staged = json.loads(candidate.read_text(encoding="utf-8"))
        assert len(staged["rungs"]) == 49
        assert staged["rungs"][5]["branches"][0]["outputs"][0]["type"] == "BROKEN_OUTPUT"

        repaired = client.post(
            f"/api/jobs/{bad_job}/repair",
            headers=headers,
            json={"request_id": "report-shape-repair-once"},
        )
        assert repaired.status_code == 202, repaired.text
        repair_job = repaired.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)

        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 2, "one user repair action must make exactly one repair model call"

        snapshot = service.jobs._load(repair_job)["snapshot"]
        assert snapshot["repair_mode"] is True
        assert snapshot["format_repair"] is False
        assert snapshot["task_type"] == "contract_repair"
        assert snapshot["allowed_rung_ids"] == [6]
        assert snapshot["context_policy"]["name"] == "minimal"

        repair_request = provider.requests[1]
        system_prompt = str(repair_request.messages[0].content)
        repair_payload = json.loads(str(repair_request.messages[-1].content))
        assert "PLC ladder local structural repair" in system_prompt
        assert "工业常识模式库" not in system_prompt
        assert "Retrieved-knowledge precedence" not in system_prompt
        assert repair_payload["repair_mode"] == "partial"
        assert repair_payload["allowed_rung_ids"] == [6]
        assert [rung["rung_id"] for rung in repair_payload["baseline_subset"]["rungs"]] == [6]

        output = service.output(repair_job)
        version_id = output["version_id"]
        saved = json.loads(service.projects.artifact(project, version_id, "json").read_text(encoding="utf-8"))
        assert saved == _multi_rung_ladder(), "repair must preserve all unrelated rungs byte-for-byte at ladder JSON level"
        assert service.projects.project(project)["version_count"] == 1


class FormatOnlyRepairProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TextDelta('{"device_comments":{"X0":"Input","Y0":"Output"},"rungs":[')
            return
        payload = _ladder()
        payload["rungs"][0]["branches"][0]["outputs"][0]["type"] = "BROKEN_OUTPUT"
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


def test_format_repair_does_not_hide_a_second_structural_model_call(offline, tmp_path):
    provider = FormatOnlyRepairProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )

    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "format-one-shot"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})

        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "format-invalid",
            "text": "X0 controls Y0",
            "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        assert client.get(f"/api/jobs/{first}").json()["status"] == "failed"

        response = client.post(
            f"/api/jobs/{first}/repair",
            headers=headers,
            json={"request_id": "format-repair-once"},
        )
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        failed = client.get(f"/api/jobs/{repair_job}").json()

        assert failed["status"] == "failed"
        assert failed["error_code"] == "generation_validation_failed"
        assert failed["error_details"]["violations"][0]["path"].endswith("outputs.0.type")
        assert len(provider.requests) == 2, "format repair must not cascade into another hidden model repair"
        assert service.projects.project(project)["version_count"] == 0


def test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():
    text = open("web/src/features/JobFailure.tsx", encoding="utf-8").read()
    assert "让 AI 修复" in text
    assert "系统没有自动再次调用模型" in text
    assert "已执行结构修复" not in text
