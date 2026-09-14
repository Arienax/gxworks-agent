import copy
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
        if len(self.requests) != 1:
            raise AssertionError("deterministic field repair must not call the provider again")
        payload = _ladder()
        payload["rungs"][0]["debug_note"] = "过长说明" * 20
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


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


def test_user_confirmed_deterministic_field_repair_uses_no_second_model_call(offline, tmp_path):
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
        assert failed["error_details"]["violations"][0]["reason"] == "field_too_long"
        assert len(provider.requests) == 1
        assert service.projects.project(project)["version_count"] == 0

        repaired = client.post(f"/api/jobs/{bad_job}/repair", headers=headers, json={"request_id": "repair-once"})
        assert repaired.status_code == 202, repaired.text
        repair_job = repaired.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 1, "field repair is deterministic and must not call the provider"
        repair_snapshot = service.jobs._load(repair_job)["snapshot"]
        assert repair_snapshot["repair_mode"] is True
        assert repair_snapshot["format_repair"] is False
        assert repair_snapshot["task_type"] == "contract_repair"
        assert repair_snapshot["repair_plan"]["mode"] == "field_patch"
        assert repair_snapshot["repair_plan"]["target"]["strategy"] == "deterministic"
        assert repair_snapshot["repair_plan"]["target"]["path"] == "/rungs/0/debug_note"
        assert repair_snapshot["allowed_rung_ids"] == []
        assert repair_snapshot["context_policy"]["name"] == "minimal"
        assert service.projects.project(project)["version_count"] == 1


class ModifierTypoProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("unique modifier repair must not call the provider")
        payload = _ladder()
        payload["rungs"][0]["branches"][0]["outputs"] = [{
            "type": "APP_INSTR", "opcode": "DDADDP",
            "operands": ["D0", "D2", "D4"], "label": None,
        }]
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


def test_user_confirmed_unique_opcode_repair_is_model_free(offline, tmp_path):
    provider = ModifierTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, {"model": "offline"}

    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state", model_factory=factory,
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "opcode-repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 triggers one calculation", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "bad-modifier",
            "text": "X0 triggers one calculation", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        failed = client.get(f"/api/jobs/{first}").json()
        assert failed["status"] == "failed"
        assert failed["error_details"]["violations"][0]["observed_opcode"] == "DDADDP"
        assert len(provider.requests) == 1 and len(factory_calls) == 1

        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "fix-modifier"})
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 1, "deterministic modifier repair must not call model"
        assert len(factory_calls) == 1, "deterministic modifier repair must not initialize provider"
        snapshot = service.jobs._load(repair_job)["snapshot"]
        target = snapshot["repair_plan"]["target"]
        assert target["strategy"] == "deterministic"
        assert target["deterministic_value"] == "DADDP"
        assert service.projects.project(project)["version_count"] == 1


class FormatThenStructuralProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TextDelta('{"device_comments":{"X0":"Input","Y0":"Output"},"rungs":[')
            return
        if len(self.requests) == 2:
            payload = _ladder()
            payload["rungs"][0]["shared_inputs"] = [{
                "type": "parallel_block",
                "branches": [
                    [{"type": "NO", "address": "X0", "label": None}],
                    [{"type": "NO", "address": "X1", "label": None}],
                ],
            }]
            payload["rungs"][0]["branches"][0]["inputs"] = []
            payload["device_comments"]["X1"] = "Input 2"
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
            return
        request_payload = json.loads(str(request.messages[-1].content))
        rung = copy.deepcopy(request_payload["baseline_subset"]["rungs"][0])
        parallel = rung["shared_inputs"].pop(0)
        rung["branches"][0]["inputs"].append(parallel)
        yield TextDelta(json.dumps({
            "mode": "partial", "device_comments": {},
            "rungs": [rung], "delete_rung_ids": [],
        }, ensure_ascii=False))


def test_format_repair_may_escalate_only_to_structure_only_rung_repair(offline, tmp_path):
    provider = FormatThenStructuralProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "format-structural"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "format-bad-structural",
            "text": "X0 controls Y0", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "format-structural-once"})
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        service.jobs._futures[job].result(timeout=15)
        completed = client.get(f"/api/jobs/{job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 3
        assert "PLC ladder JSON format repair" in str(provider.requests[1].messages[0].content)
        assert "structural representation repair" in str(provider.requests[2].messages[0].content)
        structural_payload = json.loads(str(provider.requests[2].messages[-1].content))
        assert structural_payload["repair_mode"] == "partial"
        assert "repair_contract" not in structural_payload
        assert service.projects.project(project)["version_count"] == 1


class FormatThenOpcodeProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TextDelta('{"device_comments":{"X0":"Input","Y0":"Output"},"rungs":[')
            return
        if len(self.requests) == 2:
            payload = _ladder()
            payload["rungs"][0]["branches"][0]["outputs"] = [{
                "type": "APP_INSTR", "opcode": "NOT_A_REAL_OPCODE",
                "operands": ["D0", "D1"], "label": None,
            }]
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
            return
        raise AssertionError("invalid opcode must not trigger a semantic repair model call")


def test_format_repair_does_not_guess_invalid_opcode_or_fall_back_to_whole_rung(offline, tmp_path):
    provider = FormatThenOpcodeProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "format-opcode"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "format-bad-opcode",
            "text": "X0 controls Y0", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "format-opcode-once"})
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        service.jobs._futures[job].result(timeout=15)
        failed = client.get(f"/api/jobs/{job}").json()
        assert failed["status"] == "failed"
        assert failed["error_details"]["violations"][0]["reason"] == "invalid_ladder_structure"
        assert len(provider.requests) == 2
        assert service.projects.project(project)["version_count"] == 0

        blocked = client.post(
            f"/api/jobs/{job}/repair",
            headers=headers,
            json={"request_id": "opcode-repair-must-stop"},
        )
        assert blocked.status_code == 409, blocked.text
        assert len(provider.requests) == 2, "blocked semantic repair must not create another model job"


def test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():
    text = open("web/src/features/JobFailure.tsx", encoding="utf-8").read()
    assert "让 AI 修复" in text
    assert "repairableGenerationFailure(job)" in text
    assert "系统没有自动再次调用模型" in text
    assert "系统不会猜测修复" in text
    assert "已执行结构修复" not in text



class BatchModifierTypoProvider:
    def __init__(self):
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("batch deterministic repair must not call provider again")
        payload = _ladder()
        payload["rungs"][0]["branches"][0]["outputs"] = [
            {"type": "APP_INSTR", "opcode": "DDADDP", "operands": ["D0", "D2", "D4"], "label": None},
            {"type": "APP_INSTR", "opcode": "DDMOVP", "operands": ["D10", "D12"], "label": None},
        ]
        yield TextDelta(json.dumps(payload, ensure_ascii=False))


def test_user_sees_all_opcode_errors_once_and_one_repair_fixes_all(offline, tmp_path):
    provider = BatchModifierTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, {"model": "offline"}

    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state", model_factory=factory,
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "batch-opcode-repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "two calculations", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "bad-two-modifiers",
            "text": "two calculations", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        failed = client.get(f"/api/jobs/{first}").json()
        assert failed["status"] == "failed"
        observed = {row.get("observed_opcode") for row in failed["error_details"]["violations"]}
        assert {"DDADDP", "DDMOVP"} <= observed
        assert failed["error_details"]["violation_count"] >= 2
        assert len(provider.requests) == 1 and len(factory_calls) == 1

        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "fix-two-modifiers"})
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 1
        assert len(factory_calls) == 1
        snapshot = service.jobs._load(repair_job)["snapshot"]
        targets = snapshot["repair_plan"]["targets"]
        assert [target["deterministic_value"] for target in targets] == ["DADDP", "DMOVP"]
        assert service.projects.project(project)["version_count"] == 1
