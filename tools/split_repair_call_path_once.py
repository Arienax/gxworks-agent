#!/usr/bin/env python3
from pathlib import Path


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"non-unique patch anchor: {label}")
    return text.replace(old, new, 1)

api_path = Path("src/api.py")
api = api_path.read_text(encoding="utf-8")
anchor = "\n\n@language_scoped\ndef stream_model_response(user_requirement, model_name, effort, target_mode,\n"
block = r'''

PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder local structural repair
You repair only the rejected ladder locations supplied in the user payload.
Do not re-analyze the requirement, redesign the program, retrieve manuals, or
regenerate unrelated rungs. The backend owns the immutable full baseline and
will merge and validate your patch.

Return one pure JSON object only, exactly in partial-edit form:
{"mode":"partial","device_comments":{},"rungs":[],"delete_rung_ids":[]}

Rules:
- `mode` must be `partial` and `delete_rung_ids` must be empty.
- `rungs` may contain only complete replacement rungs whose rung_id is listed
  in `allowed_rung_ids`; never add, delete, renumber, or repeat another rung.
- Preserve control logic, addresses, operands, parameters and contact polarity
  except for the minimum structural/protocol correction explicitly requested.
- Use the supplied `baseline_subset` as the only program evidence.
- `device_comments` may contain only addresses listed in `allowed_addresses`.
- Do not output markdown, explanation, diagnostics, or a full ladder program.
"""

FORMAT_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder JSON format repair
Repair JSON syntax/protocol only. Do not re-analyze the PLC requirement, retrieve
manuals, redesign logic, or invent missing behavior. The user payload contains
the rejected raw candidate and the parser failure location.

Return one pure, complete top-level ladder JSON object only:
{"device_comments":{},"rungs":[]}

Rules:
- Preserve every recoverable address, opcode, operand, value, rung_id, branch,
  contact polarity, label and comment from the rejected candidate.
- Correct only JSON syntax, delimiters, container closure and protocol shape.
- Never emit `mode:"partial"` on this path.
- If the text ended early, close structures whose existing content is evident;
  do not synthesize unseen rungs or new PLC logic.
- Do not output markdown or explanation.
"""


@language_scoped
def repair_ladder_response(repair_payload, model_name, effort, *, mode,
                           on_reasoning_chunk=None, on_content_chunk=None):
    """One explicit repair request that bypasses normal generation context."""
    if mode not in {"partial", "format"}:
        raise ValueError("Unsupported ladder repair mode")
    if not isinstance(repair_payload, dict):
        raise TypeError("repair_payload must be an object")
    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT
                     if mode == "partial" else FORMAT_LADDER_REPAIR_SYSTEM_PROMPT)
    audit_section("repair_system_prompt", system_prompt,
                  reason="explicit_local_repair" if mode == "partial" else "explicit_format_repair",
                  source="api")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False, separators=(",", ":"))},
    ]
    response = _request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        response_contract=LADDER_RESPONSE,
        preserved_annotations=source_annotations(repair_payload),
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    return response.message.reasoning, response.message.content
'''
api = replace_once(api, anchor, block + anchor, "api repair function")
api_path.write_text(api, encoding="utf-8")

gen_path = Path("src/application/generation.py")
gen = gen_path.read_text(encoding="utf-8")
gen = replace_once(gen,
    "    repair_mode: bool = False\n    allowed_rung_ids: object = None\n",
    "    repair_mode: bool = False\n    format_repair: bool = False\n    allowed_rung_ids: object = None\n",
    "request format_repair")
gen = replace_once(gen,
    "    stream_response: Optional[Callable] = None\n    generate_json: Optional[Callable] = None\n",
    "    stream_response: Optional[Callable] = None\n    generate_json: Optional[Callable] = None\n    repair_response: Optional[Callable] = None\n",
    "dependency repair_response")
gen = replace_once(gen,
    "        self.repair_mode = bool(self.repair_mode)\n        self.allowed_rung_ids = {int(item) for item in (self.allowed_rung_ids or [])}\n",
    "        self.repair_mode = bool(self.repair_mode)\n        self.format_repair = bool(self.format_repair)\n        if self.repair_mode and self.format_repair:\n            raise ValueError(\"repair_mode and format_repair are mutually exclusive\")\n        self.allowed_rung_ids = {int(item) for item in (self.allowed_rung_ids or [])}\n",
    "init format_repair")
gen = replace_once(gen,
    "                target_mode=self.target_mode, repair_mode=self.repair_mode,\n            )\n            try:\n                stream_model_response = self.dependencies.stream_response or api.stream_model_response\n",
    "                target_mode=self.target_mode, repair_mode=(self.repair_mode or self.format_repair),\n            )\n            repair_call = self.target_mode == \"ladder\" and (self.repair_mode or self.format_repair)\n            repair_payload = None\n            if repair_call:\n                if self.repair_mode:\n                    baseline = self.previous_json if isinstance(self.previous_json, dict) else {}\n                    selected_rungs = [copy.deepcopy(rung) for rung in baseline.get(\"rungs\", [])\n                                      if isinstance(rung, dict) and rung.get(\"rung_id\") in self.allowed_rung_ids]\n                    comments = baseline.get(\"device_comments\", {}) if isinstance(baseline.get(\"device_comments\"), dict) else {}\n                    repair_payload = {\n                        \"repair_mode\": \"partial\",\n                        \"plc_model\": self.plc_model,\n                        \"instruction\": model_user_input,\n                        \"allowed_rung_ids\": sorted(self.allowed_rung_ids),\n                        \"allowed_addresses\": sorted(self.allowed_addresses),\n                        \"baseline_subset\": {\n                            \"device_comments\": {key: value for key, value in comments.items()\n                                                if str(key).strip().upper() in self.allowed_addresses},\n                            \"rungs\": selected_rungs,\n                        },\n                    }\n                else:\n                    repair_payload = {\n                        \"repair_mode\": \"format\",\n                        \"plc_model\": self.plc_model,\n                        \"instruction\": model_user_input,\n                    }\n            try:\n                stream_model_response = self.dependencies.stream_response or api.stream_model_response\n",
    "repair payload")
old_call = '''                self._emit("progress", {"stage": "connecting", "message": tr('正在连接模型')})
                _reasoning, full_content = model_call(
                    stream_model_response,
                    model_user_input,
                    self.model_name,
                    self.effort,
                    self.target_mode,
                    on_reasoning_chunk=on_reasoning,
                    on_content_chunk=on_content,
                    is_edit_mode=is_edit_mode,
                    conversation_history=self.conversation_history,
                    confirmed_context=self.confirmed_context,
                    persist_history=False,
                    task_type=self.task_type,
                    current_version_json=self.current_version_json,
                    plc_model=self.plc_model,
                    image_attachments=self.image_attachments,
                )
                emit_parsing_progress(tr('正在解析模型输出：清理流式文本'))
                streaming_succeeded = True
'''
new_call = '''                self._emit("progress", {"stage": "connecting", "message": tr('正在连接模型')})
                if repair_call:
                    if self.dependencies.repair_response is not None:
                        _reasoning, full_content = model_call(
                            self.dependencies.repair_response, repair_payload, self.model_name, self.effort,
                            mode="partial" if self.repair_mode else "format",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                        )
                    elif self.dependencies.provider is None and self.dependencies.stream_response is not None:
                        # Keep injectable/offline tests compatible without routing production repair
                        # back through the normal generation prompt builder.
                        _reasoning, full_content = model_call(
                            self.dependencies.stream_response,
                            json.dumps(repair_payload, ensure_ascii=False),
                            self.model_name, self.effort, "ladder",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                            is_edit_mode=True, conversation_history=[], confirmed_context=None,
                            persist_history=False, task_type="contract_repair",
                            current_version_json=None, plc_model=self.plc_model, image_attachments=(),
                        )
                    else:
                        _reasoning, full_content = model_call(
                            api.repair_ladder_response, repair_payload, self.model_name, self.effort,
                            mode="partial" if self.repair_mode else "format",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                        )
                else:
                    _reasoning, full_content = model_call(
                        stream_model_response,
                        model_user_input,
                        self.model_name,
                        self.effort,
                        self.target_mode,
                        on_reasoning_chunk=on_reasoning,
                        on_content_chunk=on_content,
                        is_edit_mode=is_edit_mode,
                        conversation_history=self.conversation_history,
                        confirmed_context=self.confirmed_context,
                        persist_history=False,
                        task_type=self.task_type,
                        current_version_json=self.current_version_json,
                        plc_model=self.plc_model,
                        image_attachments=self.image_attachments,
                    )
                emit_parsing_progress(tr('正在解析模型输出：清理流式文本'))
                streaming_succeeded = True
'''
gen = replace_once(gen, old_call, new_call, "generation model call")
gen = replace_once(gen,
    "                elif isinstance(stream_err, ResponseRejectedError):\n                    raise\n                else:\n",
    "                elif isinstance(stream_err, ResponseRejectedError):\n                    raise\n                elif repair_call:\n                    # Explicit repair has its own transport fallback inside the dedicated API.\n                    # Never fall back into a normal full generation request.\n                    raise\n                else:\n",
    "repair no normal fallback")
gen = replace_once(gen,
    "            # Transport fallback is not a semantic repair. It obtains the same\n            # requested candidate once when streaming itself failed.\n            if streaming_succeeded and full_content:\n",
    "            if repair_call and (not streaming_succeeded or not full_content):\n                raise GenerationError(tr('修复调用未返回候选 JSON'))\n\n            # Transport fallback is not a semantic repair. It obtains the same\n            # requested candidate once when streaming itself failed.\n            if streaming_succeeded and full_content:\n",
    "repair empty guard")
gen_path.write_text(gen, encoding="utf-8")

wb_path = Path("src/application/workbench.py")
wb = wb_path.read_text(encoding="utf-8")
wb = replace_once(wb,
    "                    requirement_text=text, repair_mode=repair_mode,\n                    allowed_rung_ids=snapshot.get(\"allowed_rung_ids\"),\n",
    "                    requirement_text=text, repair_mode=repair_mode, format_repair=format_repair,\n                    allowed_rung_ids=snapshot.get(\"allowed_rung_ids\"),\n",
    "workbench format flag")
wb_path.write_text(wb, encoding="utf-8")

test_path = Path("tests/test_user_confirmed_generation_repair.py")
test = test_path.read_text(encoding="utf-8")
old = '''        second_prompt = str(provider.requests[1].messages[-1].content)
        assert "用户明确确认的一次局部结构修复" in second_prompt
        assert 'mode="partial"' in second_prompt
        assert "不要重新生成完整程序" in second_prompt
        assert "失败候选 JSON" not in second_prompt
        assert service.projects.project(project)["version_count"] == 1
'''
new = '''        system_prompt = str(provider.requests[1].messages[0].content)
        repair_payload = json.loads(str(provider.requests[1].messages[-1].content))
        assert "PLC ladder local structural repair" in system_prompt
        assert "工业常识模式库" not in system_prompt
        assert "Retrieved-knowledge precedence" not in system_prompt
        assert len(system_prompt) < 5000
        assert repair_payload["repair_mode"] == "partial"
        assert repair_payload["allowed_rung_ids"] == [1]
        assert [r["rung_id"] for r in repair_payload["baseline_subset"]["rungs"]] == [1]
        assert "用户明确确认的一次局部结构修复" in repair_payload["instruction"]
        assert "失败候选 JSON" not in repair_payload["instruction"]
        assert service.projects.project(project)["version_count"] == 1
'''
test = replace_once(test, old, new, "repair dedicated prompt assertion")
test_path.write_text(test, encoding="utf-8")
