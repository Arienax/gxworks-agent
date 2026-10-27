import json
import pytest

from fastapi.testclient import TestClient

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from application.workbench import WorkbenchService
from model_runtime.provider import TextDelta
from test_web_api import ORIGIN, _app, _ladder, _login, offline, offline_runtime_profile


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


class DeterministicDmovTypoProvider:
    """Confirmed Agent B emits compact output; the invalid opcode survives lowering."""

    def __init__(self):
        self.requests = []
        self.profile = offline_runtime_profile()

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("deterministic opcode repair must not call the provider again")
        yield TextDelta(json.dumps({
            "r": [{"b": [{"i": ["NO X0"], "o": ["DDMOV K0 D0"]}]}]
        }, ensure_ascii=False))


def test_user_confirmed_deterministic_field_repair_uses_no_second_model_call(offline, tmp_path):
    provider = DeterministicDmovTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, provider.profile

    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state", model_factory=factory,
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "repair"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 clears D0", "io_table": [], "parameters": []})
        created = client.post("/api/jobs", headers=headers, json={
            "project_id": project,
            "kind": "generation",
            "request_id": "bad-generation",
            "text": "X0 clears D0",
            "response_language": "zh-CN",
        })
        assert created.status_code == 202, created.text
        bad_job = created.json()["id"]
        service.jobs._futures[bad_job].result(timeout=15)

        preserved = client.get(f"/api/jobs/{bad_job}").json()
        assert preserved["status"] == "completed", preserved
        assert preserved["result"]["status"] == "saved_invalid"
        bad_output = client.get(f"/api/jobs/{bad_job}/output").json()
        violation = bad_output["generation"]["validation"]["violations"][0]
        assert violation["observed_opcode"] == "DDMOV"
        assert len(provider.requests) == 1 and len(factory_calls) == 1
        assert service.projects.project(project)["version_count"] == 1

        repaired = client.post(f"/api/jobs/{bad_job}/repair", headers=headers, json={"request_id": "repair-once"})
        assert repaired.status_code == 202, repaired.text
        repair_job = repaired.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert completed["result"]["status"] == "saved"
        assert len(provider.requests) == 1, "deterministic field repair must not call the provider"
        assert len(factory_calls) == 1, "deterministic field repair must not initialize provider"
        repair_snapshot = service.jobs._load(repair_job)["snapshot"]
        assert repair_snapshot["repair_mode"] is True
        assert repair_snapshot["format_repair"] is False
        assert repair_snapshot["task_type"] == "contract_repair"
        assert repair_snapshot["repair_plan"]["mode"] == "field_patch"
        assert repair_snapshot["repair_plan"]["target"]["strategy"] == "deterministic"
        assert repair_snapshot["repair_plan"]["target"]["path"] == "/rungs/0/branches/0/outputs/0/opcode"
        assert repair_snapshot["repair_plan"]["target"]["deterministic_value"] == "DMOV"
        assert repair_snapshot["allowed_rung_ids"] == []
        assert service.projects.project(project)["version_count"] == 2

        preview = client.get(f"/api/jobs/{repair_job}/preview")
        assert preview.status_code == 200, preview.text
        assert "<svg" in preview.json()["svg"]


class ModifierTypoProvider:
    def __init__(self):
        self.requests = []
        self.profile = offline_runtime_profile()

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) != 1:
            raise AssertionError("unique modifier repair must not call the provider")
        yield TextDelta(json.dumps({
            "r": [{"b": [{"i": ["P X0"], "o": ["DDADDP D0 D2 D4"]}]}]
        }, ensure_ascii=False))


def test_user_confirmed_unique_opcode_repair_is_model_free(offline, tmp_path):
    provider = ModifierTypoProvider()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return provider, provider.profile

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
        preserved = client.get(f"/api/jobs/{first}").json()
        assert preserved["status"] == "completed", preserved
        assert preserved["result"]["status"] == "saved_invalid"
        output = client.get(f"/api/jobs/{first}/output").json()
        assert output["generation"]["validation"]["violations"][0]["observed_opcode"] == "DDADDP"
        assert len(provider.requests) == 1 and len(factory_calls) == 1

        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "fix-modifier"})
        assert response.status_code == 202, response.text
        repair_job = response.json()["id"]
        service.jobs._futures[repair_job].result(timeout=15)
        completed = client.get(f"/api/jobs/{repair_job}").json()
        assert completed["status"] == "completed", completed
        assert completed["result"]["status"] == "saved"
        assert len(provider.requests) == 1, "deterministic modifier repair must not call model"
        assert len(factory_calls) == 1, "deterministic modifier repair must not initialize provider"
        snapshot = service.jobs._load(repair_job)["snapshot"]
        target = snapshot["repair_plan"]["target"]
        assert target["strategy"] == "deterministic"
        assert target["deterministic_value"] == "DADDP"
        assert service.projects.project(project)["version_count"] == 2


class FormatRewriteProvider:
    def __init__(self):
        self.requests = []
        self.profile = offline_runtime_profile()

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TextDelta('{"r":[{"b":[{"i":["NO X0"] "o":["COIL Y0"]}]}]}')
            return
        if len(self.requests) == 2:
            # Deliberately violate the new repair contract. The backend must
            # reject this attempted whole-program rewrite rather than adopting it.
            yield TextDelta(json.dumps(_ladder(), ensure_ascii=False))
            return
        raise AssertionError("format repair must never request another full-program rewrite")


def test_format_repair_rejects_model_whole_program_rewrite(offline, tmp_path):
    provider = FormatRewriteProvider()
    service = WorkbenchService(
        tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": "offline"}),
    )
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        project = client.post("/api/projects", json={"name": "format-local-only"}, headers=headers).json()["id"]
        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})
        first = client.post("/api/jobs", headers=headers, json={
            "project_id": project, "kind": "generation", "request_id": "format-local-only-bad",
            "text": "X0 controls Y0", "response_language": "zh-CN",
        }).json()["id"]
        service.jobs._futures[first].result(timeout=15)
        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "format-local-only-repair"})
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        service.jobs._futures[job].result(timeout=15)
        failed = client.get(f"/api/jobs/{job}").json()
        # A whole-program reply is not accepted as a repair. The original
        # candidate remains visible/renderable instead of being silently replaced.
        assert failed["status"] in {"failed", "completed"}
        assert len(provider.requests) == 2
        prompt = str(provider.requests[1].messages[0].content)
        assert "local format patch" in prompt
        assert "Never output the full repaired JSON" in prompt
        assert "complete top-level ladder JSON" not in prompt


def test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():
    text = open("web/src/features/JobFailure.tsx", encoding="utf-8").read()
    assert "让 AI 修复" in text
    assert "job.error_details?.repairable" in text
    assert "branch_id" not in text
    assert "已执行结构修复" not in text


@pytest.mark.parametrize("stale", [False, True])
def test_direct_fallback_repair_preserves_clarification_facts_and_original_candidate(offline, tmp_path, monkeypatch, stale):
    import application.generation_agent as agent
    from test_web_api import _complete, _historical_direct_job

    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    raw = '{"result":{"r":[{"c":["NO X0"] "b":[{"i":[],"o":["COIL Y0"]}]}]}}'
    class Provider:
        profile = offline_runtime_profile()
        requests = []
        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                yield TextDelta(raw)
            elif len(self.requests) == 2:
                import hashlib
                yield TextDelta(json.dumps({"schema_version": 1, "mode": "format_patch",
                    "base_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "patches": [{"before": '["NO X0"] "b"', "after": '["NO X0"],"b"'}]}))
            else:
                raise AssertionError("format repair must not regenerate")

    provider = Provider()
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
        model_factory=lambda: (provider, {"model": provider.profile["model"]}))
    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", json={"name": "Direct 修复", "plc_model": "FX3U"}, headers=headers).json()["id"]
        requirement = "Y0：运行输出。按住输入时运行，松开停止。"
        first = _historical_direct_job(service, pid, requirement, [
            {"id": "input", "question": "请说明输入。X7：候选输入。", "required": True}
        ])
        response = client.post("/api/jobs", headers=headers, json={"kind": "direct_generation",
            "project_id": pid, "request_id": "answer", "text": "", "clarification_job_id": first,
            "clarification_answers": {"input": "X0：启动按钮，按下为1。"}})
        assert response.status_code == 202, response.text
        failed_id = response.json()["id"]
        service.jobs._futures[failed_id].result(timeout=15)
        failed = client.get(f"/api/jobs/{failed_id}").json()
        assert failed["status"] == "failed", failed
        assert failed["error_code"] == "generation_validation_failed"
        assert failed["error_details"]["violations"][0]["reason"] == "invalid_json_object"
        staged = service.state_dir / "staging" / failed_id / "repair_candidate.json"
        assert staged.read_text(encoding="utf-8") == raw
        assert service.projects.project(pid)["version_count"] == 0
        if stale:
            service.store.set_confirmed_spec(pid, {"summary": "changed", "io_table": [], "parameters": []})
        repair = client.post(f"/api/jobs/{failed_id}/repair", headers=headers, json={"request_id": "repair"})
        if stale:
            assert repair.status_code == 409, repair.text
            return
        repair_id, output = _complete(client, service, repair)
        assert output["status"] == "saved", output
        assert len(provider.requests) == 2
        snapshot = service.jobs._load(repair_id)["snapshot"]
        assert requirement in snapshot["repair_requirement"]
        assert "X0" in snapshot["user_fact_text"] and "X7" not in snapshot["user_fact_text"]
        assert snapshot["format_repair"] and not snapshot["repair_mode"]
        assert snapshot["repair_source_handoff"]["mode"] == "direct_generation"
        ladder = json.loads((service.state_dir / "staging" / repair_id / "ladder.json").read_text(encoding="utf-8"))
        assert ladder["device_comments"]["X0"] == "启动按钮"
        assert "X7" not in ladder["device_comments"]
        assert client.get(f"/api/jobs/{repair_id}/preview").status_code == 200
        assert service.projects.project(pid)["version_count"] == 1
        assert staged.read_text(encoding="utf-8") == raw
        assert output["generation"]["generation_handoff"]["confirmed_spec_sha256"] is None
