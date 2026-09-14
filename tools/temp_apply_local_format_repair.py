from pathlib import Path
import re


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one match, got {text.count(old)}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. api.py: even direct callers of the legacy mode must use local patches.
replace_once(
    "src/api.py",
    '''    if not isinstance(repair_payload, dict):
        raise TypeError("repair_payload must be an object")
    repair_payload = dict(repair_payload)
    if mode == "partial":
''',
    '''    if not isinstance(repair_payload, dict):
        raise TypeError("repair_payload must be an object")
    repair_payload = dict(repair_payload)
    if mode == "format":
        # Compatibility name only: the model is never allowed to rewrite the
        # whole ladder. Deterministic recovery runs first, then at most a small
        # syntax-only before/after patch is requested and applied locally.
        from application.format_patch_repair import format_repair_response
        return format_repair_response(
            repair_payload, model_name, effort,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
        )
    if mode == "partial":
''',
)

api_path = Path("src/api.py")
api_text = api_path.read_text(encoding="utf-8")
api_text, count = re.subn(
    r'FORMAT_LADDER_REPAIR_SYSTEM_PROMPT = """.*?"""\n\n',
    '''FORMAT_LADDER_REPAIR_SYSTEM_PROMPT = """# Legacy compatibility name\nFull-program format rewriting is disabled. Production format repair uses only\ndeterministic recovery or a bounded local syntax patch. Never return a complete\nladder from a format-repair model call.\n"""\n\n''',
    api_text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("src/api.py: format prompt block not found")
api_path.write_text(api_text, encoding="utf-8")

# 2. workbench.py: preserved invalid generations remain explicitly repairable.
replace_once(
    "src/application/workbench.py",
    '''            record = self.jobs._load(record_id(job_id))
            if (record.get("kind") != "generation" or record.get("status") != "failed"
                    or record.get("error_code") != "generation_validation_failed"):
                raise ConflictError("Only a failed structural generation can be repaired")
            snapshot = copy.deepcopy(record.get("snapshot") or {})
            self._check_snapshot(snapshot)
            project_id = snapshot["project_id"]
            version_id = snapshot.get("version_id")
''',
    '''            record = self.jobs._load(record_id(job_id))
            failed_validation = (
                record.get("kind") == "generation"
                and record.get("status") == "failed"
                and record.get("error_code") == "generation_validation_failed"
            )
            saved_invalid = (
                record.get("kind") == "generation"
                and record.get("status") == "completed"
                and isinstance(record.get("result"), dict)
                and record["result"].get("status") == "saved_invalid"
                and isinstance(record["result"].get("version_id"), str)
            )
            if not (failed_validation or saved_invalid):
                raise ConflictError("Only a rejected generation candidate can be repaired")
            snapshot = copy.deepcopy(record.get("snapshot") or {})
            project_id = snapshot["project_id"]
            if saved_invalid:
                # Preserving an invalid candidate intentionally activates a
                # diagnostic version. Accept that one known state transition,
                # but reject unrelated project/spec changes before repairing.
                current = self.projects.raw_project(project_id)
                frozen = snapshot.get("project") or {}
                preserved_id = record["result"]["version_id"]
                if current.get("active_version_id") != preserved_id or any(
                    current.get(key) != frozen.get(key)
                    for key in ("confirmed_spec", "target_mode", "plc_model")
                ):
                    raise ConflictError("错误候选保存后工程状态已变化，请重新生成或选择当前候选。")
            else:
                self._check_snapshot(snapshot)
            version_id = snapshot.get("version_id")
''',
)

replace_once(
    "src/application/workbench.py",
    '''            details = record.get("error_details") or {}
            violations = details.get("violations") if isinstance(details, dict) else []
''',
    '''            details = record.get("error_details") or {}
            if saved_invalid and not details:
                try:
                    preserved_output = self.output(job_id)
                    preserved_validation = ((preserved_output.get("generation") or {}).get("validation") or {})
                    if isinstance(preserved_validation, dict):
                        details = preserved_validation
                except (KeyError, ValueError, OSError):
                    details = {}
            violations = details.get("violations") if isinstance(details, dict) else []
''',
)

replace_once(
    "src/application/workbench.py",
    '''        else:
            repair_text = (
                "这是用户明确确认的一次 JSON 格式修复。失败候选本身无法安全解析，因此不能执行局部 rung 合并。"
                "不要重新分析需求，不要改变控制逻辑、地址、参数或触点极性。只补全/修正 JSON 协议与闭合结构。"
                "返回完整 ladder JSON，不要返回 mode=\\"partial\\"，不要输出解释文本。\\n"
                f"失败位置：{location_text}\\n\\n失败候选 JSON：\\n{candidate_text}"
            )
''',
    '''        else:
            repair_text = (
                "这是用户明确确认的一次局部 JSON 格式修复。完整失败候选由系统持有，严禁模型重写完整程序。"
                "先执行确定性语法恢复；只有仍有歧义时才允许模型返回 format_patch 的短 before/after 文本替换。"
                "不得返回 ladder、rungs、branch 或任何完整候选，不得改变地址、指令、参数、触点极性或控制语义。\\n"
                f"失败位置：{location_text}\\n\\n失败候选 JSON：\\n{candidate_text}"
            )
''',
)
replace_once(
    "src/application/workbench.py",
    '''            "task_type": "contract_repair" if local_repair else "generate",
''',
    '''            "task_type": "contract_repair",
''',
)

# 3. Web: completed saved_invalid jobs expose the same repair action.
replace_once(
    "web/src/App.tsx",
    '''  async function repairFailedGeneration(job: Job) {
    if (job.kind !== "generation" || job.error_code !== "generation_validation_failed") return;
    if (!window.confirm(t("将调用模型一次，仅修复当前候选的结构/协议错误，不重新分析需求。继续吗？"))) return;
''',
    '''  async function repairFailedGeneration(job: Job) {
    const savedInvalid = job.kind === "generation" && job.status === "completed" && job.result?.status === "saved_invalid";
    if ((job.kind !== "generation" || job.error_code !== "generation_validation_failed") && !savedInvalid) return;
    if (!window.confirm(t("只修复当前候选的局部格式/结构错误，不重新分析需求，也不重写完整程序。继续吗？"))) return;
''',
)
replace_once(
    "web/src/App.tsx",
    '''                    onRetry={() => { setOutputRetry((n) => n + 1); void reloadProjectSilently(pid); }}
                    onSpec={() => setPanel("spec")} t={t} />
''',
    '''                    onRetry={() => { setOutputRetry((n) => n + 1); void reloadProjectSilently(pid); }}
                    onRepair={() => { if (currentJob) void guarded(() => repairFailedGeneration(currentJob)); }}
                    onSpec={() => setPanel("spec")} t={t} />
''',
)

# 4. Replace obsolete regression assumptions that expected whole-program format rewrites.
test_path = Path("tests/test_user_confirmed_generation_repair.py")
test_text = test_path.read_text(encoding="utf-8")
start = test_text.index("class FormatThenStructuralProvider:")
end = test_text.index("def test_failure_ui_offers_explicit_repair_not_fake_automatic_attempts():")
replacement = '''class FormatRewriteProvider:\n    def __init__(self):\n        self.requests = []\n\n    def stream(self, request):\n        self.requests.append(request)\n        if len(self.requests) == 1:\n            yield TextDelta('{"device_comments":{"X0":"Input","Y0":"Output"},"rungs":[')\n            return\n        if len(self.requests) == 2:\n            # Deliberately violate the new repair contract. The backend must\n            # reject this attempted whole-program rewrite rather than adopting it.\n            yield TextDelta(json.dumps(_ladder(), ensure_ascii=False))\n            return\n        raise AssertionError("format repair must never request another full-program rewrite")\n\n\ndef test_format_repair_rejects_model_whole_program_rewrite(offline, tmp_path):\n    provider = FormatRewriteProvider()\n    service = WorkbenchService(\n        tmp_path / "workspace", tmp_path / "state",\n        model_factory=lambda: (provider, {"model": "offline"}),\n    )\n    with TestClient(_app(service.store.base_dir, service.state_dir, service=service), base_url=ORIGIN) as client:\n        headers = _login(client)\n        project = client.post("/api/projects", json={"name": "format-local-only"}, headers=headers).json()["id"]\n        service.store.set_confirmed_spec(project, {"summary": "X0 controls Y0", "io_table": [], "parameters": []})\n        first = client.post("/api/jobs", headers=headers, json={\n            "project_id": project, "kind": "generation", "request_id": "format-local-only-bad",\n            "text": "X0 controls Y0", "response_language": "zh-CN",\n        }).json()["id"]\n        service.jobs._futures[first].result(timeout=15)\n        response = client.post(f"/api/jobs/{first}/repair", headers=headers, json={"request_id": "format-local-only-repair"})\n        assert response.status_code == 202, response.text\n        job = response.json()["id"]\n        service.jobs._futures[job].result(timeout=15)\n        failed = client.get(f"/api/jobs/{job}").json()\n        assert failed["status"] == "failed"\n        assert len(provider.requests) == 2\n        prompt = str(provider.requests[1].messages[0].content)\n        assert "local format patch" in prompt\n        assert "Never output the full repaired JSON" in prompt\n        assert "complete top-level ladder JSON" not in prompt\n        assert service.projects.project(project)["version_count"] == 0\n\n\n'''
test_path.write_text(test_text[:start] + replacement + test_text[end:], encoding="utf-8")

print("local format repair migration applied")
