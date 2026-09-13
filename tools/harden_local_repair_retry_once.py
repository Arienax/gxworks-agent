#!/usr/bin/env python3
from pathlib import Path

WORKBENCH = Path("src/application/workbench.py")
TEST = Path("tests/test_user_confirmed_generation_repair.py")


def patch_workbench():
    text = WORKBENCH.read_text(encoding="utf-8")
    old = '''            try:
                parsed_candidate = json.loads(candidate_text)
            except (TypeError, ValueError):
                parsed_candidate = None
            from application.generation_repair import candidate_base
            repair_base = candidate_base(parsed_candidate)
            local_repair = repair_base is not None
            allowed_rung_ids = set()
            allowed_addresses = set()
            if local_repair:
                rungs = repair_base["rungs"]'''
    new = '''            from application.generation_repair import candidate_base
            inherited_repair_base = (
                candidate_base(snapshot.get("repair_baseline"))
                if snapshot.get("repair_mode") else None
            )
            inherited_local_repair = inherited_repair_base is not None
            if inherited_local_repair:
                # A failed repair attempt is still repairing the same immutable
                # baseline. Never widen it to a full-program regeneration merely
                # because the partial response itself had invalid JSON/shape.
                repair_base = inherited_repair_base
                local_repair = True
                allowed_rung_ids = {
                    int(item) for item in (snapshot.get("allowed_rung_ids") or [])
                    if isinstance(item, int) and not isinstance(item, bool)
                }
                allowed_addresses = {
                    str(item).strip().upper()
                    for item in (snapshot.get("allowed_addresses") or [])
                    if isinstance(item, str) and item.strip()
                }
            else:
                try:
                    parsed_candidate = json.loads(candidate_text)
                except (TypeError, ValueError):
                    parsed_candidate = None
                repair_base = candidate_base(parsed_candidate)
                local_repair = repair_base is not None
                allowed_rung_ids = set()
                allowed_addresses = set()
            if local_repair and not inherited_local_repair:
                rungs = repair_base["rungs"]'''
    if old not in text:
        raise RuntimeError("local repair baseline marker not found")
    text = text.replace(old, new, 1)

    old_comments = '''                allowed_addresses.update(
                    str(address).strip().upper()
                    for address in repair_base.get("device_comments", {})
                    if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\\d+", address.strip())
                )'''
    new_comments = '''                if saw_comment_path:
                    allowed_addresses.update(
                        str(address).strip().upper()
                        for address in repair_base.get("device_comments", {})
                        if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\\d+", address.strip())
                    )'''
    if old_comments not in text:
        raise RuntimeError("allowed comment scope marker not found")
    text = text.replace(old_comments, new_comments, 1)

    old_prompt = '''        if local_repair:
            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"
            repair_text = (
                "这是用户明确确认的一次局部结构修复。系统已把失败候选作为 Current version JSON 提供给你。"'''
    new_prompt = '''        if local_repair:
            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"
            retry_note = (
                "\\n上一次局部修复回复仍未通过校验。只修正下面这个 partial patch 的 JSON/结构问题，"
                "不要扩大修改范围，也不要改成完整程序。\\n上一次失败的局部 patch：\\n" + candidate_text
                if inherited_local_repair else ""
            )
            repair_text = (
                "这是用户明确确认的一次局部结构修复。系统已把失败候选作为 Current version JSON 提供给你。"'''
    if old_prompt not in text:
        raise RuntimeError("local repair prompt marker not found")
    text = text.replace(old_prompt, new_prompt, 1)
    old_tail = '''                f"允许修改的 rung_id：{rung_text}\\n"
                f"失败位置：{location_text}"
            )'''
    new_tail = '''                f"允许修改的 rung_id：{rung_text}\\n"
                f"失败位置：{location_text}"
                + retry_note
            )'''
    if old_tail not in text:
        raise RuntimeError("local repair prompt tail not found")
    text = text.replace(old_tail, new_tail, 1)
    WORKBENCH.write_text(text, encoding="utf-8")


def patch_test():
    text = TEST.read_text(encoding="utf-8")
    marker = '\n\ndef test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():'
    if marker not in text:
        raise RuntimeError("test insertion marker not found")
    addition = r'''

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
            yield TextDelta('{"mode":"partial","device_comments":{},"rungs":[')
        else:
            yield TextDelta(json.dumps({"mode": "partial", "device_comments": {},
                "rungs": [_ladder()["rungs"][0]], "delete_rung_ids": []}, ensure_ascii=False))


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
        assert snapshot["allowed_rung_ids"] == [1]
        prompt = str(provider.requests[2].messages[-1].content)
        assert "上一次局部修复回复仍未通过校验" in prompt
        assert "上一次失败的局部 patch" in prompt
        assert 'mode="partial"' in prompt
        assert service.projects.project(project)["version_count"] == 1
'''
    text = text.replace(marker, addition + marker, 1)
    TEST.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_workbench()
    patch_test()
