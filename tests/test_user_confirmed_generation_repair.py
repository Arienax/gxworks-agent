import json

from fastapi.testclient import TestClient

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from application.workbench import WorkbenchService
from model_provider import TextDelta
from test_web_api import ORIGIN, _app, _ladder, _login, offline


class RepairProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = _ladder()
            payload["rungs"][0]["debug_note"] = "过长说明" * 20
        else:
            request_payload = json.loads(str(request.messages[-1].content))
            target = request_payload["target"]
            payload = {
                "schema_version": 1, "mode": "field_patch",
                "base_sha256": request_payload["base_sha256"],
                "patches": [{"path": target["path"], "value": None}],
            }
        raw = json.dumps(payload, ensure_ascii=False)
        yield TextDelta(raw)


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


def test_structural_failure_waits_for_user_then_repairs_once(offline, tmp_path):
    provider = RepairProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})
        created = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "bad-generation",
            "text": "X0 controls Y0",
            "response_language": "zh-CN",
        })
        assert created.status_code == 202, created.text
        bad_job = created.json()["id"]
        service.jobs._futures[bad_job].result(timeout=15)
        failed = client.get(f"/api/jobs/{bad_job}").json()
        assert failed["status"] == "failed"
        assert failed["error_code"] == "generation_validation_failed"
        assert failed["error_details"]["attempt_count"] == 0
        assert failed["error_details"]["max_attempts"] == 0
        assert failed["error_details"]["violations"][0]["reason"] == "field_too_long"
        assert len(provider.requests) == 1, "structural failure must not auto-call the model again"
        assert service.projects.project(project)["version_count"] == 0
        candidate = service.state_dir / "staging" / bad_job / "repair_candidate.json"
        assert candidate.is_file()

        repaired = client.post(f"/api/jobs/{bad_job}/repair", headers=headers, json={"request_id": "repair-once"})
        assert repaired.status_code == 202, repaired.text
        repair_job = repaired.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 2
        repair_snapshot = service.jobs._load(repair_job)["snapshot"]
        assert repair_snapshot["repair_mode"] is True
        assert repair_snapshot["format_repair"] is False
        assert repair_snapshot["task_type"] == "contract_repair"
        assert repair_snapshot["repair_plan"]["mode"] == "field_patch"
        assert repair_snapshot["repair_plan"]["target"]["path"] == "/rungs/0/debug_note"
        assert repair_snapshot["allowed_rung_ids"] == []
        assert repair_snapshot["context_policy"]["name"] == "minimal"
        system_prompt = str(provider.requests[1].messages[0].content)
        repair_payload = json.loads(str(provider.requests[1].messages[-1].content))
        native_format = provider.requests[1].options["response_format"]
        assert native_format["type"] == "json_schema"
        assert native_format["json_schema"]["strict"] is True
        native_schema = native_format["json_schema"]["schema"]
        assert native_schema["properties"]["mode"]["enum"] == ["field_patch"]
        assert native_schema["properties"]["base_sha256"]["enum"] == [repair_payload["base_sha256"]]
        patch_schema = native_schema["properties"]["patches"]["items"]
        assert patch_schema["properties"]["path"]["enum"] == ["/rungs/0/debug_note"]
        assert patch_schema["properties"]["value"]["maxLength"] == 64
        assert "rungs" not in native_schema["properties"]
        assert "PLC ladder JSON field repair" in system_prompt
        assert "工业常识模式库" not in system_prompt
        assert "Retrieved-knowledge precedence" not in system_prompt
        assert len(system_prompt) < 3000
        assert repair_payload["repair_mode"] == "field_patch"
        assert repair_payload["target"]["path"] == "/rungs/0/debug_note"
        assert repair_payload["target"]["current_value"]
        assert "baseline_subset" not in repair_payload
        assert "用户明确确认的一次字段级 JSON 修复" in repair_payload["instruction"]
        assert "失败候选 JSON" not in repair_payload["instruction"]
        assert service.projects.project(project)["version_count"] == 1


class RetryRepairProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = _ladder()
            payload["rungs"][0]["debug_note"] = "过长说明" * 20
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
        elif len(self.requests) == 2:
            yield TextDelta('{"schema_version":1,"mode":"field_patch","patches":[')
        else:
            request_payload = json.loads(str(request.messages[-1].content))
            target = request_payload["target"]
            yield TextDelta(json.dumps({
                "schema_version": 1, "mode": "field_patch",
                "base_sha256": request_payload["base_sha256"],
                "patches": [{"path": target["path"], "value": None}],
            }, ensure_ascii=False))


def test_failed_partial_repair_keeps_original_local_scope(offline, tmp_path):
    provider = RetryRepairProvider()
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "repair-retry"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={"project_id": project, "kind": "generation",
            "request_id": "bad-generation-retry", "text": "X0 controls Y0", "response_language": "zh-CN"}).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        second = client.post(f"/api/jobs/{first}/repair", headers=headers,
            json={"request_id": "repair-incomplete"}).json()["id"]
        service.jobs._futures[second].result(timeout=15)
        assert client.get(f"/api/jobs/{second}").json()["status"] == "failed"

        third_response = client.post(f"/api/jobs/{second}/repair", headers=headers,
            json={"request_id": "repair-incomplete-again"})
        assert third_response.status_code == 202, third_response.text
        third = third_response.json()["id"]
        service.jobs._futures[third].result(timeout=15)
        completed = client.get(f"/api/jobs/{third}").json()
        assert completed["status"] == "completed", completed
        snapshot = service.jobs._load(third)["snapshot"]
        assert snapshot["repair_mode"] is True
        assert snapshot["format_repair"] is False
        assert snapshot["repair_plan"]["mode"] == "field_patch"
        assert snapshot["repair_plan"]["target"]["path"] == "/rungs/0/debug_note"
        system_prompt = str(provider.requests[2].messages[0].content)
        retry_payload = json.loads(str(provider.requests[2].messages[-1].content))
        assert "PLC ladder JSON field repair" in system_prompt
        assert retry_payload["repair_mode"] == "field_patch"
        assert retry_payload["target"]["path"] == "/rungs/0/debug_note"
        assert service.projects.project(project)["version_count"] == 1


class FormatThenStructuralProvider:
    def __init__(self): self.requests=[]
    def stream(self, request):
        self.requests.append(request)
        if len(self.requests)==1:
            yield TextDelta('{"device_comments":{"X0":"Input","Y0":"Output"},"rungs":[')
        elif len(self.requests)==2:
            payload=_ladder()
            payload["rungs"][0]["branches"][0]["outputs"]=[{"type":"APP_INSTR","opcode":"NOT_A_REAL_OPCODE","operands":["D0","D1"],"label":None}]
            yield TextDelta(json.dumps(payload,ensure_ascii=False))
        else:
            request_payload = json.loads(str(request.messages[-1].content))
            target = request_payload["target"]
            yield TextDelta(json.dumps({
                "schema_version": 1, "mode": "field_patch",
                "base_sha256": request_payload["base_sha256"],
                "patches": [{"path": target["path"], "value": "MOV"}],
            },ensure_ascii=False))


def test_one_repair_cascades_format_then_local_structure(offline,tmp_path):
    provider=FormatThenStructuralProvider()
    service=WorkbenchService(tmp_path/"workspace",tmp_path/"state",model_factory=lambda:(provider,{"model":"offline"}))
    with TestClient(_app(service.store.base_dir,service.state_dir,service=service),base_url=ORIGIN) as client:
        headers=_login(client)
        project=client.post("/api/projects",json={"name":"format-cascade"},headers=headers).json()["id"]
        service.store.set_confirmed_spec(project,{"summary":"X0 controls Y0","io_table":[],"parameters":[]})
        first=client.post("/api/jobs",headers=headers,json={"project_id":project,"kind":"generation","request_id":"format-bad","text":"X0 controls Y0","response_language":"zh-CN"}).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        assert client.get(f"/api/jobs/{first}").json()["status"]=="failed"
        response=client.post(f"/api/jobs/{first}/repair",headers=headers,json={"request_id":"format-cascade-once"})
        assert response.status_code==202,response.text
        job=response.json()["id"]
        service.jobs._futures[job].result(timeout=15)
        completed=client.get(f"/api/jobs/{job}").json()
        assert completed["status"]=="completed",completed
        assert len(provider.requests)==3
        assert "PLC ladder JSON format repair" in str(provider.requests[1].messages[0].content)
        assert "PLC ladder JSON field repair" in str(provider.requests[2].messages[0].content)
        followup=json.loads(str(provider.requests[2].messages[-1].content))
        assert followup["repair_mode"]=="field_patch"
        assert followup["target"]["path"]=="/rungs/0/branches/0/outputs/0/opcode"
        assert "NOT_A_REAL_OPCODE" not in followup["target"]["value_schema"]["enum"]
        assert "MOV" in followup["target"]["value_schema"]["enum"]
        assert service.projects.project(project)["version_count"]==1


def test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():
    text = open("web/src/features/JobFailure.tsx", encoding="utf-8").read()
    assert "让 AI 修复" in text
    assert "系统没有自动再次调用模型" in text
    assert "已执行结构修复" not in text
