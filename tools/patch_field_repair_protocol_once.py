#!/usr/bin/env python3
from pathlib import Path
import re


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(path, pattern, replacement, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, found {count}")
    p.write_text(new, encoding="utf-8")


# api.py: field patches have their own response contract, prompt and strict schema.
replace_once(
    "src/api.py",
    "    ANALYSIS_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, INSPECTION_RESPONSE,\n    LADDER_RESPONSE, PATCH_RESPONSE, ST_RESPONSE, TEST_SUITE_RESPONSE,\n",
    "    ANALYSIS_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, FIELD_PATCH_RESPONSE,\n    INSPECTION_RESPONSE, LADDER_RESPONSE, PATCH_RESPONSE, ST_RESPONSE, TEST_SUITE_RESPONSE,\n",
    "api response contract import",
)
replace_once(
    "src/api.py",
    'PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder local structural repair\n',
    '''FIELD_PATCH_REPAIR_SYSTEM_PROMPT = """# PLC ladder JSON field repair\nYou repair exactly one scalar field in an immutable rejected ladder candidate.\nThe backend owns the full ladder and applies your patch deterministically.\n\nReturn one JSON object only:\n{\"schema_version\":1,\"mode\":\"field_patch\",\"base_sha256\":\"...\",\"patches\":[{\"path\":\"/...\",\"value\":\"...\"}]}\n\nRules:\n- Copy base_sha256 and path exactly from the payload.\n- Return exactly one patch and only the replacement scalar value.\n- Do not return a rung, branch, ladder program, markdown or explanation.\n- Use target.context only to choose the corrected value; do not redesign control logic.\n- The provider schema is authoritative for the allowed replacement value.\n"""\n\n\nPARTIAL_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder local structural repair\n''',
    "field repair prompt",
)
replace_once(
    "src/api.py",
    "def _native_partial_repair_response_format(repair_payload):\n",
    '''def _native_field_patch_response_format(repair_payload):\n    target = repair_payload.get("target") if isinstance(repair_payload, dict) else None\n    if not isinstance(target, dict) or not isinstance(target.get("path"), str):\n        raise ValueError("field repair target is required")\n    value_schema = json.loads(json.dumps(target.get("value_schema") or {}))\n    if not value_schema:\n        raise ValueError("field repair value schema is required")\n    base_sha = str(repair_payload.get("base_sha256") or "")\n    schema = {\n        "type": "object",\n        "properties": {\n            "schema_version": {"type": "integer", "enum": [1]},\n            "mode": {"type": "string", "enum": ["field_patch"]},\n            "base_sha256": {"type": "string", "enum": [base_sha]},\n            "patches": {\n                "type": "array", "minItems": 1, "maxItems": 1,\n                "items": {\n                    "type": "object",\n                    "properties": {\n                        "path": {"type": "string", "enum": [target["path"]]},\n                        "value": value_schema,\n                    },\n                    "required": ["path", "value"],\n                    "additionalProperties": False,\n                },\n            },\n        },\n        "required": ["schema_version", "mode", "base_sha256", "patches"],\n        "additionalProperties": False,\n    }\n    return {"type": "json_schema", "json_schema": {\n        "name": "ladder_field_patch", "strict": True, "schema": schema,\n    }}\n\n\ndef _native_partial_repair_response_format(repair_payload):\n''',
    "native field repair schema",
)
regex_once(
    "src/api.py",
    r'@language_scoped\ndef repair_ladder_response\(repair_payload, model_name, effort, \*, mode,.*?return response\.message\.reasoning, response\.message\.content\n\n\n@language_scoped\ndef stream_model_response',
    '''@language_scoped\ndef repair_ladder_response(repair_payload, model_name, effort, *, mode,\n                           on_reasoning_chunk=None, on_content_chunk=None):\n    """One explicit repair request that bypasses normal generation context."""\n    if mode not in {"field_patch", "partial", "format"}:\n        raise ValueError("Unsupported ladder repair mode")\n    if not isinstance(repair_payload, dict):\n        raise TypeError("repair_payload must be an object")\n    repair_payload = dict(repair_payload)\n    if mode == "partial":\n        plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"\n        repair_payload["repair_contract"] = {\n            "plc_model": plc_model,\n            "app_instr_opcode_enum": list(generation_app_instr_mnemonics(plc_model)),\n            "app_instr_forbidden_typed_opcodes": sorted(GENERATION_TYPED_OUTPUT_OPCODES),\n            "dedicated_output_types": ["COIL", "PLS", "PLF", "TIMER", "COUNTER"],\n        }\n    if mode == "field_patch":\n        native_response_format = _native_field_patch_response_format(repair_payload)\n        system_prompt = FIELD_PATCH_REPAIR_SYSTEM_PROMPT\n        response_contract = FIELD_PATCH_RESPONSE\n        audit_reason = "explicit_field_repair"\n    elif mode == "partial":\n        native_response_format = _native_partial_repair_response_format(repair_payload)\n        system_prompt = PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT\n        response_contract = LADDER_RESPONSE\n        audit_reason = "explicit_local_repair"\n    else:\n        native_response_format = None\n        system_prompt = FORMAT_LADDER_REPAIR_SYSTEM_PROMPT\n        response_contract = LADDER_RESPONSE\n        audit_reason = "explicit_format_repair"\n    audit_section("repair_system_prompt", system_prompt, reason=audit_reason, source="api")\n    messages = [\n        {"role": "system", "content": system_prompt},\n        {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False, separators=(",", ":"))},\n    ]\n    response = _request_model(\n        messages, model_name=model_name, effort=effort, stream=True,\n        options={"response_format": native_response_format} if native_response_format else None,\n        response_contract=response_contract,\n        preserved_annotations=source_annotations(repair_payload),\n        on_reasoning_chunk=on_reasoning_chunk, on_content_chunk=on_content_chunk,\n        fallback_to_non_stream=True,\n    )\n    return response.message.reasoning, response.message.content\n\n\n@language_scoped\ndef stream_model_response''',
    "repair api function",
)

# generation.py: carry a repair plan, call field protocol, deterministically apply before ladder validation.
replace_once(
    "src/application/generation.py",
    "    allowed_rung_ids: object = None\n    allowed_addresses: object = None\n    image_attachments: object = None\n",
    "    allowed_rung_ids: object = None\n    allowed_addresses: object = None\n    repair_plan: object = None\n    image_attachments: object = None\n",
    "generation request repair plan",
)
replace_once(
    "src/application/generation.py",
    '''        self.allowed_addresses = {\n            str(item).strip().upper()\n            for item in (self.allowed_addresses or [])\n            if str(item).strip()\n        }\n        self.image_attachments = tuple(self.image_attachments or ())\n''',
    '''        self.allowed_addresses = {\n            str(item).strip().upper()\n            for item in (self.allowed_addresses or [])\n            if str(item).strip()\n        }\n        self.repair_plan = copy.deepcopy(self.repair_plan) if isinstance(self.repair_plan, dict) else None\n        self.image_attachments = tuple(self.image_attachments or ())\n''',
    "generation repair plan init",
)
regex_once(
    "src/application/generation.py",
    r'            repair_payload = None\n            if repair_call:.*?            try:\n                stream_model_response =',
    '''            repair_payload = None\n            repair_kind = "format"\n            if repair_call:\n                if self.repair_mode and isinstance(self.repair_plan, dict) and self.repair_plan.get("mode") == "field_patch":\n                    repair_kind = "field_patch"\n                    repair_payload = {\n                        "repair_mode": "field_patch",\n                        "plc_model": self.plc_model,\n                        "instruction": model_user_input,\n                        "base_sha256": self.repair_plan.get("base_sha256"),\n                        "target": copy.deepcopy(self.repair_plan.get("target") or {}),\n                    }\n                elif self.repair_mode:\n                    repair_kind = "partial"\n                    baseline = self.previous_json if isinstance(self.previous_json, dict) else {}\n                    selected_rungs = [copy.deepcopy(rung) for rung in baseline.get("rungs", [])\n                                      if isinstance(rung, dict) and rung.get("rung_id") in self.allowed_rung_ids]\n                    comments = baseline.get("device_comments", {}) if isinstance(baseline.get("device_comments"), dict) else {}\n                    repair_payload = {\n                        "repair_mode": "partial", "plc_model": self.plc_model,\n                        "instruction": model_user_input,\n                        "allowed_rung_ids": sorted(self.allowed_rung_ids),\n                        "allowed_addresses": sorted(self.allowed_addresses),\n                        "baseline_subset": {\n                            "device_comments": {key: value for key, value in comments.items()\n                                                if str(key).strip().upper() in self.allowed_addresses},\n                            "rungs": selected_rungs,\n                        },\n                    }\n                else:\n                    repair_payload = {"repair_mode": "format", "plc_model": self.plc_model,\n                                      "instruction": model_user_input}\n            try:\n                stream_model_response =''',
    "generation repair payload",
)
text = Path("src/application/generation.py").read_text(encoding="utf-8")
old_mode = 'mode="partial" if self.repair_mode else "format"'
if text.count(old_mode) != 2:
    raise SystemExit(f"repair call mode: expected two anchors, found {text.count(old_mode)}")
Path("src/application/generation.py").write_text(text.replace(old_mode, "mode=repair_kind"), encoding="utf-8")
replace_once(
    "src/application/generation.py",
    '''                if self.target_mode == "ladder":\n                    prepared_candidate = prepare_ladder_candidate(\n                        parsed, plc_model=self.plc_model, program_name=self.program_name,\n                        revision=self.revision, confirmed_spec=self.confirmed_context,\n                        previous_ladder=self.previous_json, repair_mode=self.repair_mode,\n                        allowed_rung_ids=self.allowed_rung_ids,\n                        allowed_addresses=self.allowed_addresses, task_type=self.task_type,\n                        on_progress=emit_parsing_progress,\n                    )\n                    validation_messages.extend(prepared_candidate["validation_messages"])\n                    return prepared_candidate["ladder"]\n''',
    '''                if self.target_mode == "ladder":\n                    if self.repair_mode and isinstance(self.repair_plan, dict) and self.repair_plan.get("mode") == "field_patch":\n                        from application.field_repair import apply as apply_field_patch\n                        parsed = apply_field_patch(self.previous_json, parsed, self.repair_plan)\n                        prepared_candidate = prepare_ladder_candidate(\n                            parsed, plc_model=self.plc_model, program_name=self.program_name,\n                            revision=self.revision, confirmed_spec=self.confirmed_context,\n                            previous_ladder=None, repair_mode=False, task_type=self.task_type,\n                            on_progress=emit_parsing_progress,\n                        )\n                    else:\n                        prepared_candidate = prepare_ladder_candidate(\n                            parsed, plc_model=self.plc_model, program_name=self.program_name,\n                            revision=self.revision, confirmed_spec=self.confirmed_context,\n                            previous_ladder=self.previous_json, repair_mode=self.repair_mode,\n                            allowed_rung_ids=self.allowed_rung_ids,\n                            allowed_addresses=self.allowed_addresses, task_type=self.task_type,\n                            on_progress=emit_parsing_progress,\n                        )\n                    validation_messages.extend(prepared_candidate["validation_messages"])\n                    return prepared_candidate["ladder"]\n''',
    "parse field patch",
)
replace_once(
    "src/application/generation.py",
    '''                diagnostic = validation_diagnostic(error)\n                path = str(diagnostic.get("path") or "")\n                match = re.search(r"(?:^|\\.)rungs\\.(\\d+)(?:\\.|$)", path)\n''',
    '''                diagnostic = validation_diagnostic(error)\n                path = str(diagnostic.get("path") or "")\n                from application.field_repair import apply as apply_field_patch, plan as plan_field_patch\n                field_plan = plan_field_patch(base, [diagnostic], self.plc_model)\n                if field_plan is not None:\n                    payload = {\n                        "repair_mode": "field_patch", "plc_model": self.plc_model,\n                        "instruction": f"JSON 格式已经恢复，但字段校验失败。只修复指定字段。错误位置：{path}；原因：{diagnostic.get('reason','invalid_ladder_structure')}",\n                        "base_sha256": field_plan["base_sha256"],\n                        "target": copy.deepcopy(field_plan["target"]),\n                    }\n                    self._emit("progress", {"stage": "field_repair", "severity": "warning",\n                        "message": tr('JSON 格式已恢复；正在对新暴露的字段错误执行一次精确修复。')})\n                    def on_field_reasoning(token): self._emit("reasoning", token)\n                    def on_field_content(token): self._emit("content", token)\n                    repair_fn = self.dependencies.repair_response or api.repair_ladder_response\n                    _r, local_text = model_call(repair_fn, payload, self.model_name, self.effort,\n                        mode="field_patch", on_reasoning_chunk=on_field_reasoning,\n                        on_content_chunk=on_field_content)\n                    patch = json.loads(clean_json_text(local_text))\n                    patched = apply_field_patch(base, patch, field_plan)\n                    prepared_candidate = prepare_ladder_candidate(\n                        patched, plc_model=self.plc_model, program_name=self.program_name,\n                        revision=self.revision, confirmed_spec=self.confirmed_context,\n                        previous_ladder=None, repair_mode=False, task_type="contract_repair",\n                        on_progress=emit_parsing_progress)\n                    validation_messages.extend(prepared_candidate["validation_messages"])\n                    repair_attempts += 1\n                    return prepared_candidate["ladder"]\n                match = re.search(r"(?:^|\\.)rungs\\.(\\d+)(?:\\.|$)", path)\n''',
    "format cascade field patch",
)

# workbench.py: choose field patch first and preserve it across retries.
replace_once(
    "src/application/workbench.py",
    '''            from application.generation_repair import candidate_base\n            inherited_repair_base = (\n                candidate_base(snapshot.get("repair_baseline"))\n                if snapshot.get("repair_mode") else None\n            )\n            inherited_local_repair = inherited_repair_base is not None\n''',
    '''            from application.generation_repair import candidate_base\n            from application.field_repair import plan as plan_field_patch\n            inherited_repair_base = (\n                candidate_base(snapshot.get("repair_baseline"))\n                if snapshot.get("repair_mode") else None\n            )\n            inherited_repair_plan = (copy.deepcopy(snapshot.get("repair_plan"))\n                                     if snapshot.get("repair_mode") and isinstance(snapshot.get("repair_plan"), dict)\n                                     else None)\n            inherited_local_repair = inherited_repair_base is not None\n''',
    "workbench inherited field plan",
)
replace_once(
    "src/application/workbench.py",
    '''                allowed_addresses = {\n                    str(item).strip().upper()\n                    for item in (snapshot.get("allowed_addresses") or [])\n                    if isinstance(item, str) and item.strip()\n                }\n            else:\n''',
    '''                allowed_addresses = {\n                    str(item).strip().upper()\n                    for item in (snapshot.get("allowed_addresses") or [])\n                    if isinstance(item, str) and item.strip()\n                }\n                repair_plan = inherited_repair_plan\n            else:\n''',
    "workbench retry plan",
)
replace_once(
    "src/application/workbench.py",
    '''                local_repair = repair_base is not None\n                allowed_rung_ids = set()\n                allowed_addresses = set()\n            if local_repair and not inherited_local_repair:\n''',
    '''                local_repair = repair_base is not None\n                allowed_rung_ids = set()\n                allowed_addresses = set()\n                repair_plan = plan_field_patch(\n                    repair_base, violations, snapshot.get("project", {}).get("plc_model", "FX3U")\n                ) if local_repair else None\n            if local_repair and not inherited_local_repair and repair_plan is None:\n''',
    "workbench choose field plan",
)
replace_once(
    "src/application/workbench.py",
    '''        location_text = "；".join(locations) if locations else "ladder schema"\n        if local_repair:\n            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"\n''',
    '''        location_text = "；".join(locations) if locations else "ladder schema"\n        if local_repair and repair_plan is not None:\n            target = repair_plan.get("target") or {}\n            repair_text = (\n                "这是用户明确确认的一次字段级 JSON 修复。不要返回梯级、分支或完整程序。"\n                "只返回 field_patch 协议对象，并且只能修改 target.path 指定的一个字段。"\n                "不要改变其他地址、参数、触点极性或结构。\\n"\n                f"目标字段：{target.get('diagnostic_path') or target.get('path')}\\n"\n                f"失败位置：{location_text}"\n            )\n        elif local_repair:\n            rung_text = ", ".join(map(str, sorted(allowed_rung_ids))) or "无（仅允许修复注释字段）"\n''',
    "workbench field repair text",
)
replace_once(
    "src/application/workbench.py",
    '''        if local_repair:\n            command.update(\n                repair_baseline=repair_base,\n                allowed_rung_ids=sorted(allowed_rung_ids),\n                allowed_addresses=sorted(allowed_addresses),\n            )\n''',
    '''        if local_repair:\n            command.update(\n                repair_baseline=repair_base,\n                allowed_rung_ids=sorted(allowed_rung_ids),\n                allowed_addresses=sorted(allowed_addresses),\n            )\n            if repair_plan is not None:\n                command["repair_plan"] = copy.deepcopy(repair_plan)\n''',
    "workbench persist repair plan",
)
replace_once(
    "src/application/workbench.py",
    '''                repair_baseline = snapshot.get("repair_baseline") if repair_mode else None\n                if repair_mode:\n''',
    '''                repair_baseline = snapshot.get("repair_baseline") if repair_mode else None\n                repair_plan = snapshot.get("repair_plan") if repair_mode else None\n                if repair_mode:\n''',
    "run job repair plan",
)
replace_once(
    "src/application/workbench.py",
    '''                    allowed_rung_ids=snapshot.get("allowed_rung_ids"),\n                    allowed_addresses=snapshot.get("allowed_addresses"),\n                    image_attachments=images, model_name=snapshot.get("model", {}).get("model"), response_language=language)\n''',
    '''                    allowed_rung_ids=snapshot.get("allowed_rung_ids"),\n                    allowed_addresses=snapshot.get("allowed_addresses"), repair_plan=repair_plan,\n                    image_attachments=images, model_name=snapshot.get("model", {}).get("model"), response_language=language)\n''',
    "generation request repair plan wiring",
)
