#!/usr/bin/env python3
"""One-shot patcher: wire Workbench repair into the existing partial-repair path."""
from pathlib import Path


WORKBENCH = Path("src/application/workbench.py")
TEST = Path("tests/test_user_confirmed_generation_repair.py")


def patch_workbench():
    text = WORKBENCH.read_text(encoding="utf-8")
    if "import json\nimport re\nimport tempfile" not in text:
        text = text.replace("import json\nimport tempfile", "import json\nimport re\nimport tempfile", 1)

    start = text.index("    def repair_generation(self, job_id, request_id):")
    end = text.index("    def submit(self, command):", start)
    method = '''    def repair_generation(self, job_id, request_id):
        """Submit one operator-confirmed repair without re-running normal generation."""
        self.writable()
        record_id(request_id)
        with self.lock.thread_lock:
            if not self.jobs:
                raise KeyError(job_id)
            record = self.jobs._load(record_id(job_id))
            if (record.get("kind") != "generation" or record.get("status") != "failed"
                    or record.get("error_code") != "generation_validation_failed"):
                raise ConflictError("Only a failed structural generation can be repaired")
            snapshot = copy.deepcopy(record.get("snapshot") or {})
            self._check_snapshot(snapshot)
            project_id = snapshot["project_id"]
            version_id = snapshot.get("version_id")
            root = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir / "staging")
            candidate_path = contained(root / "repair_candidate.json", root)
            if not candidate_path.is_file():
                raise ConflictError("The rejected candidate is no longer available for repair")
            candidate_text = candidate_path.read_text(encoding="utf-8")
            if not candidate_text.strip() or len(candidate_text) > 512000:
                raise ConflictError("The rejected candidate is too large or empty")
            details = record.get("error_details") or {}
            violations = details.get("violations") if isinstance(details, dict) else []
            locations = []
            for item in violations or []:
                if isinstance(item, dict):
                    path = item.get("path")
                    reason = item.get("reason")
                    if isinstance(path, str):
                        locations.append(path + (f" ({reason})" if isinstance(reason, str) else ""))
            language = snapshot.get("response_language") if snapshot.get("response_language") in ("zh-CN", "en", "ja") else "zh-CN"

            # A parseable rejected ladder is a safe repair baseline. Keep the
            # model on a partial replacement contract and enforce the same scope
            # again after materialization. Syntax-broken JSON has no trustworthy
            # rung identity and therefore uses the separate full-format path.
            try:
                parsed_candidate = json.loads(candidate_text)
            except (TypeError, ValueError):
                parsed_candidate = None
            from application.generation_repair import candidate_base
            repair_base = candidate_base(parsed_candidate)
            local_repair = repair_base is not None
            allowed_rung_ids = set()
            allowed_addresses = set()
            if local_repair:
                rungs = repair_base["rungs"]
                by_index = {index: rung for index, rung in enumerate(rungs)}
                saw_rung_path = False
                saw_comment_path = False
                unresolved_rung_path = False
                for item in violations or []:
                    path = item.get("path") if isinstance(item, dict) else None
                    if not isinstance(path, str):
                        continue
                    saw_comment_path |= "device_comments" in path
                    if "rungs" not in path:
                        continue
                    saw_rung_path = True
                    match = re.search(r"rungs(?:\\.|\\[)(\\d+)", path)
                    if match:
                        rung = by_index.get(int(match.group(1)))
                        rung_id = rung.get("rung_id") if isinstance(rung, dict) else None
                        if isinstance(rung_id, int) and not isinstance(rung_id, bool):
                            allowed_rung_ids.add(rung_id)
                        else:
                            unresolved_rung_path = True
                    else:
                        unresolved_rung_path = True
                if (unresolved_rung_path or (not saw_rung_path and not saw_comment_path)) and not allowed_rung_ids:
                    allowed_rung_ids = {
                        rung["rung_id"] for rung in rungs
                        if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int)
                        and not isinstance(rung.get("rung_id"), bool)
                    }
                selected = [rung for rung in rungs if rung.get("rung_id") in allowed_rung_ids]
                from contract_repair import patch_device_addresses
                allowed_addresses.update(patch_device_addresses({
                    "mode": "partial", "rungs": selected,
                    "delete_rung_ids": [], "device_comments": {},
                }))
                allowed_addresses.update(
                    str(address).strip().upper()
                    for address in repair_base.get("device_comments", {})
                    if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\\d+", address.strip())
                )

        location_text = "；".join(locations) if locations else "ladder schema"
        if local_repair:
            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"
            repair_text = (
                "这是用户明确确认的一次局部结构修复。系统已把失败候选作为 Current version JSON 提供给你。"
                "不要重新分析需求，不要重新生成完整程序，不要改变控制逻辑、地址、参数、触点极性或未出错梯级。"
                "只返回一个完整可解析的 JSON 对象，并且必须使用 mode=\\\"partial\\\"。"
                "rungs 只包含需要替换的完整梯级，delete_rung_ids 必须为空，device_comments 只列确实需要修复的现有地址。"
                "debug_note 是可选字段，默认删除；label、debug_note、device_comment 单条不得超过64字符。\\n"
                f"允许修改的 rung_id：{rung_text}\\n"
                f"失败位置：{location_text}"
            )
        else:
            repair_text = (
                "这是用户明确确认的一次 JSON 格式修复。失败候选本身无法安全解析，因此不能执行局部 rung 合并。"
                "不要重新分析需求，不要改变控制逻辑、地址、参数或触点极性。只补全/修正 JSON 协议与闭合结构。"
                "返回完整 ladder JSON，不要返回 mode=\\\"partial\\\"，不要输出解释文本。\\n"
                f"失败位置：{location_text}\\n\\n失败候选 JSON：\\n{candidate_text}"
            )
        command = {
            "kind": "generation",
            "project_id": project_id,
            "version_id": version_id,
            "request_id": request_id,
            "text": repair_text,
            "response_language": language,
            "attachment_ids": [],
            "change_scope": snapshot.get("change_scope"),
            "repair_origin_job_id": job_id,
            "repair_mode": local_repair,
            "format_repair": not local_repair,
            "task_type": "contract_repair" if local_repair else "generate",
        }
        if local_repair:
            command.update(
                repair_baseline=repair_base,
                allowed_rung_ids=sorted(allowed_rung_ids),
                allowed_addresses=sorted(allowed_addresses),
            )
        return self.submit(command)

'''
    text = text[:start] + method + text[end:]

    old_policy = '''            snapshot["context_policy"] = resolve_context_policy(
                None if requires_model else "legacy"
            ).snapshot()'''
    new_policy = '''            repair_context = "minimal" if command.get("repair_mode") or command.get("format_repair") else None
            snapshot["context_policy"] = resolve_context_policy(
                repair_context if requires_model else "legacy"
            ).snapshot()'''
    if old_policy not in text:
        raise RuntimeError("context policy marker not found")
    text = text.replace(old_policy, new_policy, 1)

    request_start = text.index('                program = snapshot.get("program_ir")\n                request = GenerationRequest(', text.index('elif kind == "generation":'))
    request_end = text.index('                metadata = GenerationWorkflow(', request_start)
    request_block = '''                program = snapshot.get("program_ir")
                repair_mode = bool(snapshot.get("repair_mode"))
                format_repair = bool(snapshot.get("format_repair"))
                repair_baseline = snapshot.get("repair_baseline") if repair_mode else None
                if repair_mode:
                    previous_json = copy.deepcopy(repair_baseline)
                elif format_repair:
                    previous_json = None
                else:
                    previous_json = ir_to_ladder(program) if program else None
                request = GenerationRequest(
                    user_input=scoped_text, effort=project.get("effort"), target_mode=project["target_mode"],
                    previous_json=previous_json, previous_ir=program,
                    confirmed_context=project.get("confirmed_spec"),
                    conversation_history=[] if (repair_mode or format_repair) else project.get("messages", []),
                    task_type=snapshot.get("task_type"),
                    plc_model=project.get("plc_model", "FX3U"), program_name=(program or {}).get("program_name", "MAIN"),
                    revision=(program or {}).get("revision", 0) + 1,
                    requirement_text=text, repair_mode=repair_mode,
                    allowed_rung_ids=snapshot.get("allowed_rung_ids"),
                    allowed_addresses=snapshot.get("allowed_addresses"),
                    image_attachments=images, model_name=snapshot.get("model", {}).get("model"), response_language=language)
'''
    text = text[:request_start] + request_block + text[request_end:]
    WORKBENCH.write_text(text, encoding="utf-8")


def patch_test():
    text = TEST.read_text(encoding="utf-8")
    old = '''        payload = _ladder()
        if len(self.requests) == 1:
            payload["rungs"][0]["debug_note"] = "过长说明" * 20
        raw = json.dumps(payload, ensure_ascii=False)
        yield TextDelta(raw)'''
    new = '''        if len(self.requests) == 1:
            payload = _ladder()
            payload["rungs"][0]["debug_note"] = "过长说明" * 20
        else:
            payload = {"mode": "partial", "device_comments": {},
                       "rungs": [_ladder()["rungs"][0]], "delete_rung_ids": []}
        raw = json.dumps(payload, ensure_ascii=False)
        yield TextDelta(raw)'''
    if old not in text:
        raise RuntimeError("RepairProvider marker not found")
    text = text.replace(old, new, 1)

    marker = '''        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 2
        second_prompt = str(provider.requests[1].messages[-1].content)
        assert "用户明确确认的一次结构修复" in second_prompt
        assert "只修复" in second_prompt
        assert "debug_note" in second_prompt
        assert service.projects.project(project)["version_count"] == 1'''
    replacement = '''        assert completed["status"] == "completed", completed
        assert len(provider.requests) == 2
        repair_snapshot = service.jobs._load(repair_job)["snapshot"]
        assert repair_snapshot["repair_mode"] is True
        assert repair_snapshot["format_repair"] is False
        assert repair_snapshot["task_type"] == "contract_repair"
        assert repair_snapshot["allowed_rung_ids"] == [1]
        assert repair_snapshot["context_policy"]["name"] == "minimal"
        second_prompt = str(provider.requests[1].messages[-1].content)
        assert "用户明确确认的一次局部结构修复" in second_prompt
        assert 'mode="partial"' in second_prompt
        assert "不要重新生成完整程序" in second_prompt
        assert "失败候选 JSON" not in second_prompt
        assert service.projects.project(project)["version_count"] == 1'''
    if marker not in text:
        raise RuntimeError("repair assertion marker not found")
    text = text.replace(marker, replacement, 1)
    TEST.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_workbench()
    patch_test()
