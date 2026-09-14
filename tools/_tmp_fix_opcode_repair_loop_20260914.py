from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


def replace_in_function(path, function_name, old, new):
    text = read(path)
    marker = f"def {function_name}("
    start = text.index(marker)
    next_def = text.find("\n\ndef ", start + len(marker))
    next_decorated = text.find("\n\n@", start + len(marker))
    ends = [value for value in (next_def, next_decorated) if value != -1]
    end = min(ends) if ends else len(text)
    block = text[start:end]
    count = block.count(old)
    if count != 1:
        raise SystemExit(f"{path}:{function_name}: expected one match, found {count}")
    block = block.replace(old, new, 1)
    write(path, text[:start] + block + text[end:])


# 1) Blocked semantic repair plans are not executable repair jobs.
replace_once(
    "src/application/workbench.py",
    "            if local_repair and not inherited_local_repair and repair_plan is None:\n",
    '''            if local_repair and isinstance(repair_plan, dict):
                target = repair_plan.get("target") or {}
                if target.get("strategy") == "blocked":
                    raise ConflictError(
                        "该校验错误涉及指令、地址或参数语义，局部修复不会猜测修改；请重新生成候选或手动修正。"
                    )
            if local_repair and not inherited_local_repair and repair_plan is None:
''',
)


# 2) Workflow response_format beats stale profile requestOverrides.
replace_once(
    "src/model_provider.py",
    '''        params = _deep_merge(
            self.profile.get("generationDefaults") or {},
            request.options or {},
        )
''',
    '''        response_format_unset = object()
        explicit_response_format = (
            copy.deepcopy(request.options["response_format"])
            if "response_format" in request.options
            else response_format_unset
        )
        params = _deep_merge(
            self.profile.get("generationDefaults") or {},
            request.options or {},
        )
''',
)
replace_once(
    "src/model_provider.py",
    '''        params = _deep_merge(params, self.profile.get("requestOverrides") or {})

        params["model"] = request.model or str(self.profile.get("model") or "")
''',
    '''        params = _deep_merge(params, self.profile.get("requestOverrides") or {})
        # A workflow-specific response contract must beat a persisted/profile-level
        # response_format. Otherwise an old native schema can accept values that
        # the current PLC validator rejects after the model response is accepted.
        if explicit_response_format is not response_format_unset:
            if explicit_response_format is None:
                params.pop("response_format", None)
            else:
                params["response_format"] = explicit_response_format

        params["model"] = request.model or str(self.profile.get("model") or "")
''',
)


# 3) Profiles already using native json_schema get the current PLC-model ladder contract.
api_path = "src/api.py"
api_text = read(api_path)
marker = "@language_scoped\ndef stream_model_response("
if marker not in api_text:
    raise SystemExit("api.py: stream_model_response marker missing")
helper = '''def _native_ladder_generation_options(plc_model, *, allow_partial=False):
    """Use the current ladder contract when the selected profile already uses native JSON Schema.

    Profiles that use json_object/text keep their existing transport behavior.
    This only replaces a persisted/native json_schema so its opcode enum cannot
    drift behind the registry enforced by final PLC validation.
    """
    provider = _workflow_provider()
    profile = getattr(provider, "profile", None)
    if not isinstance(profile, dict):
        return None
    response_format = None
    for key in ("generationDefaults", "requestOverrides"):
        source = profile.get(key)
        if isinstance(source, dict) and "response_format" in source:
            response_format = source.get("response_format")
    if not (isinstance(response_format, dict) and response_format.get("type") == "json_schema"):
        return None

    selected_model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    schema = ladder_response_schema(
        allow_partial=bool(allow_partial),
        plc_model=selected_model,
    )
    name = "ladder_candidate"
    if allow_partial:
        # Ordinary edit generation prefers a partial candidate. Use that branch
        # directly so providers do not have to support a top-level oneOf.
        schema = json.loads(json.dumps(schema["oneOf"][1]))
        schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]
        name = "ladder_partial_candidate"
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": True,
                "schema": schema,
            },
        }
    }


'''
api_text = api_text.replace(marker, helper + marker, 1)
write(api_path, api_text)

replace_in_function(
    "src/api.py",
    "stream_model_response",
    '''    response = _request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
''',
    '''    native_options = (
        _native_ladder_generation_options(plc_model, allow_partial=is_edit_mode)
        if target_mode == "ladder" else None
    )
    response = _request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        options=native_options,
        response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
''',
)
replace_in_function(
    "src/api.py",
    "generate_model_json",
    '''    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=effort,
            stream=False,
''',
    '''    native_options = (
        _native_ladder_generation_options(plc_model, allow_partial=is_edit_mode)
        if target_mode == "ladder" else None
    )
    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=effort,
            stream=False,
            options=native_options,
''',
)


# 4) UI only advertises Repair for the narrow deterministic/structural cases.
failure_path = "web/src/features/JobFailure.tsx"
failure_text = read(failure_path)
old_function = 'function FailureMessage({ job, t }: { job: Job; t: (key: string) => string }) {'
helper_ts = '''function repairableGenerationFailure(job: Job) {
  const rows = job.error_details?.violations || [];
  if (rows.length !== 1) return false;
  const violation = rows[0];
  if (violation.reason === "invalid_json_object" || violation.reason === "invalid_shared_input") return true;
  const leaf = violation.path.split(".").at(-1) || "";
  if (violation.reason === "field_too_long")
    return leaf === "label" || leaf === "debug_note" || leaf.startsWith("comment[");
  return violation.reason === "invalid_ladder_structure" &&
    (leaf === "branch_id" || leaf === "y_offset_level");
}

function FailureMessage({ job, t }: { job: Job; t: (key: string) => string }) {'''
if failure_text.count(old_function) != 1:
    raise SystemExit("JobFailure.tsx: FailureMessage marker mismatch")
failure_text = failure_text.replace(old_function, helper_ts, 1)
old_message = '<p className="muted">{t("系统没有自动再次调用模型。可由你确认后仅修复当前候选的结构问题。")}</p>'
new_message = '''<p className="muted">{t(repairableGenerationFailure(job)
      ? "系统没有自动再次调用模型。可由你确认后仅修复当前候选的结构问题。"
      : "该错误涉及指令、地址或参数语义，系统不会猜测修复；请重新生成候选或手动修改。")}</p>'''
if failure_text.count(old_message) != 1:
    raise SystemExit("JobFailure.tsx: repair message marker mismatch")
failure_text = failure_text.replace(old_message, new_message, 1)
old_repairable = '  const repairable = job.kind === "generation" && job.error_code === "generation_validation_failed" && !!onRepair;'
new_repairable = '  const repairable = job.kind === "generation" && job.error_code === "generation_validation_failed" && repairableGenerationFailure(job) && !!onRepair;'
if failure_text.count(old_repairable) != 1:
    raise SystemExit("JobFailure.tsx: repairable marker mismatch")
failure_text = failure_text.replace(old_repairable, new_repairable, 1)
write(failure_path, failure_text)

replace_once(
    "web/src/i18n.ts",
    '  "系统没有自动再次调用模型。可由你确认后仅修复当前候选的结构问题。": ["The model was not called again automatically. You can confirm a repair limited to structural issues in the current candidate.", "モデルの自動再呼び出しは行っていません。確認後、現在の候補の構造上の問題だけを修復できます。"],\n',
    '  "系统没有自动再次调用模型。可由你确认后仅修复当前候选的结构问题。": ["The model was not called again automatically. You can confirm a repair limited to structural issues in the current candidate.", "モデルの自動再呼び出しは行っていません。確認後、現在の候補の構造上の問題だけを修復できます。"],\n  "该错误涉及指令、地址或参数语义，系统不会猜测修复；请重新生成候选或手动修改。": ["This error involves instruction, address, or parameter semantics. The system will not guess a repair; regenerate the candidate or edit it manually.", "このエラーは命令、アドレス、またはパラメータの意味に関係します。推測による修復は行わないため、候補を再生成するか手動で修正してください。"],\n',
)


# 5) Regression: invalid opcode after one format repair cannot start another repair job.
test_path = "tests/test_user_confirmed_generation_repair.py"
test_text = read(test_path)
old = '''        assert failed["status"] == "failed"
        assert failed["error_details"]["violations"][0]["reason"] == "invalid_ladder_structure"
        assert len(provider.requests) == 2
        assert service.projects.project(project)["version_count"] == 0
'''
new = '''        assert failed["status"] == "failed"
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
'''
if test_text.count(old) != 1:
    raise SystemExit("test_user_confirmed_generation_repair.py: opcode block mismatch")
test_text = test_text.replace(old, new, 1)
old_ui = '''    assert "让 AI 修复" in text
    assert "系统没有自动再次调用模型" in text
    assert "已执行结构修复" not in text
'''
new_ui = '''    assert "让 AI 修复" in text
    assert "repairableGenerationFailure(job)" in text
    assert "系统没有自动再次调用模型" in text
    assert "系统不会猜测修复" in text
    assert "已执行结构修复" not in text
'''
if test_text.count(old_ui) != 1:
    raise SystemExit("test_user_confirmed_generation_repair.py: UI block mismatch")
write(test_path, test_text.replace(old_ui, new_ui, 1))


# 6) Regression: current native schema owns opcode enum and request-level format wins.
provider_test_path = "tests/test_model_provider.py"
provider_tests = read(provider_test_path)
addition = r'''


def test_ladder_generation_replaces_stale_native_schema_with_current_opcode_contract(monkeypatch):
    captured = {}
    profile = _profile("zhipu-glm-5.3-flash")
    profile["requestOverrides"]["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "stale_ladder",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    monkeypatch.setattr(api, "_workflow_provider", lambda: SimpleNamespace(profile=profile))
    monkeypatch.setattr(
        api,
        "_prepare_api_call",
        lambda *args, **kwargs: ([{"role": "system", "content": "system"}], [], False),
    )

    def request(messages, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(
                reasoning="",
                content='{"device_comments":{},"rungs":[]}',
            )
        )

    monkeypatch.setattr(api, "_request_model", request)
    api.stream_model_response(
        "X0 controls Y0", "offline", "low", "ladder", plc_model="FX3U"
    )

    native = captured["options"]["response_format"]
    assert native["type"] == "json_schema"
    assert native["json_schema"]["name"] == "ladder_candidate"
    schema = native["json_schema"]["schema"]

    def app_instr_rule(value):
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                type_rule = properties.get("type")
                if isinstance(type_rule, dict) and type_rule.get("const") == "APP_INSTR":
                    return value
            for child in value.values():
                found = app_instr_rule(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = app_instr_rule(child)
                if found is not None:
                    return found
        return None

    rule = app_instr_rule(schema)
    assert rule is not None
    allowed = set(rule["properties"]["opcode"]["enum"])
    assert "MOV" in allowed
    assert "NOT_A_REAL_OPCODE" not in allowed
    assert "FLDE" not in allowed
    assert "OUT" not in allowed


def test_workflow_response_format_overrides_stale_profile_request_override():
    profile = _profile("zhipu-glm-5.3-flash")
    stale = {
        "type": "json_schema",
        "json_schema": {
            "name": "stale",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
    profile["requestOverrides"]["response_format"] = stale
    provider = OpenAICompatibleProvider(profile, "key", client=_Client([iter([])]))
    requested = {
        "type": "json_schema",
        "json_schema": {
            "name": "current",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }
    params = provider._request_params(
        ModelRequest(
            (UserMessage("generate"),),
            options={"response_format": requested},
            stream=True,
        )
    )
    assert params["response_format"] == requested

    params = provider._request_params(
        ModelRequest(
            (UserMessage("plain"),),
            options={"response_format": None},
            stream=True,
        )
    )
    assert "response_format" not in params
'''
if "test_ladder_generation_replaces_stale_native_schema_with_current_opcode_contract" in provider_tests:
    raise SystemExit("test_model_provider.py: regression already present")
write(provider_test_path, provider_tests.rstrip() + addition + "\n")
