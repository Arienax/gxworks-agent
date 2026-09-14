from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker count {text.count(old)} != 1")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# field_repair: batch independent scalar targets in one explicit repair.
path = Path("src/application/field_repair.py")
text = path.read_text(encoding="utf-8")
start = text.index('def plan(base, violations, plc_model="FX3U"):\n')
end = text.index('\ndef deterministic_response(repair_payload):\n', start)
new_plan = r'''def _plan_single_target(saved, row, plc_model):
    reason = str(row.get("reason") or "invalid_ladder_structure")
    segments = _segments(row.get("path"))

    if reason == "invalid_shared_input":
        return None
    if not segments or segments[0] != "rungs":
        return _blocked(saved, row, segments)["target"]
    try:
        current = _lookup(saved, segments)
        parent = _lookup(saved, segments[:-1])
    except KeyError:
        return _blocked(saved, row, segments)["target"]

    leaf = segments[-1]
    if (
        leaf == "opcode" and isinstance(parent, dict)
        and str(parent.get("type") or "").upper() == "APP_INSTR"
    ):
        observed = str(row.get("observed_opcode") or current or "").strip().upper()
        operands = parent.get("operands") if isinstance(parent.get("operands"), list) else []
        candidates, basis, deterministic = _opcode_repair_candidates(observed, operands, plc_model)
        if candidates:
            context = {
                "mutable_field": "opcode",
                "observed_opcode": observed,
                "candidate_basis": basis,
                "allowed_values": list(candidates),
                "immutable_operands": copy.deepcopy(operands),
            }
            return _target(
                saved, row, segments,
                strategy="deterministic" if deterministic else "constrained_model",
                current_value=current,
                value_schema={"type": "string", "enum": list(candidates)},
                deterministic_value=candidates[0] if deterministic else None,
                context=context,
            )["target"]
        return _blocked(saved, row, segments, current)["target"]

    if isinstance(leaf, str):
        rule = _value_schema(parent, leaf, reason)
        if rule is not None:
            if leaf in _OPTIONAL_TEXT_FIELDS:
                deterministic_value = current[:MAX_LABEL_LEN] if isinstance(current, str) else None
            else:
                branch_index = _branch_index(segments)
                if branch_index is None:
                    return _blocked(saved, row, segments, current)["target"]
                deterministic_value = branch_index + 1 if leaf == "branch_id" else branch_index
            return _target(
                saved, row, segments,
                strategy="deterministic",
                current_value=current,
                value_schema=rule,
                deterministic_value=deterministic_value,
            )["target"]

    return _blocked(saved, row, segments, current)["target"]


def _plan_targets(plan):
    if not isinstance(plan, dict):
        return []
    targets = plan.get("targets")
    if isinstance(targets, list):
        return [item for item in targets if isinstance(item, dict)]
    target = plan.get("target")
    return [target] if isinstance(target, dict) else []


def plan(base, violations, plc_model="FX3U"):
    """Plan all independent validator-proven repairs from one validation pass.

    Pure structural shared-input errors still use the bounded whole-rung path.
    Scalar violations are batched into one field_patch. A mixed structure +
    scalar case becomes composite only when every scalar correction is already
    deterministic; the caller can apply those locally before one structure call.
    """
    saved = candidate_base(base)
    rows = [row for row in (violations or []) if isinstance(row, dict)]
    if saved is None:
        return None
    if not rows:
        row = {"path": "content$.rungs", "reason": "invalid_ladder_structure"}
        return _blocked(saved, row, ["rungs"])

    targets = []
    structural = []
    for row in rows:
        target = _plan_single_target(saved, row, plc_model)
        if target is None:
            structural.append(copy.deepcopy(row))
        else:
            targets.append(target)

    if structural:
        if not targets:
            return None
        if all(target.get("strategy") == "deterministic" for target in targets):
            return {
                "schema_version": SCHEMA_VERSION,
                "mode": "composite",
                "base_sha256": base_sha256(saved),
                "targets": targets,
                "structural_violations": structural,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "blocked_batch",
            "base_sha256": base_sha256(saved),
            "targets": targets,
            "structural_violations": structural,
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": base_sha256(saved),
        "targets": targets,
    }
    if len(targets) == 1:
        result["target"] = copy.deepcopy(targets[0])
    return result

'''
text = text[:start] + new_plan + text[end:]

start = text.index('def deterministic_response(repair_payload):\n')
end = text.index('\ndef _pointer_segments(pointer):\n', start)
new_det = r'''def deterministic_response(repair_payload):
    """Materialize every deterministic field patch without calling a model."""
    if not isinstance(repair_payload, dict) or repair_payload.get("repair_mode") != MODE:
        return None
    targets = _plan_targets(repair_payload)
    if not targets:
        return None
    patches = []
    for target in targets:
        strategy = target.get("strategy")
        if strategy == "deterministic":
            value = copy.deepcopy(target.get("deterministic_value"))
        elif strategy == "blocked":
            value = None
        else:
            return None
        patches.append({"path": target.get("path"), "value": value})
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": repair_payload.get("base_sha256"),
        "patches": patches,
    }

'''
text = text[:start] + new_det + text[end:]

start = text.index('def apply(base, response, repair_plan):\n')
new_apply = r'''def apply(base, response, repair_plan):
    """Apply one authorized batch of path-addressed patches to an immutable baseline."""
    saved = candidate_base(base)
    if saved is None or not isinstance(repair_plan, dict) or repair_plan.get("mode") not in {MODE, "composite"}:
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    if base_sha256(saved) != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    targets = _plan_targets(repair_plan)
    if not targets:
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")
    blocked = next((target for target in targets if target.get("strategy") == "blocked"), None)
    if blocked is not None:
        raise _blocked_error(blocked)

    required = {"schema_version", "mode", "base_sha256", "patches"}
    if not isinstance(response, dict) or set(response) != required:
        raise RepairAssemblyError("$", "repair_shape_invalid")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("mode") != MODE:
        raise RepairAssemblyError("$.mode", "repair_shape_invalid")
    if response.get("base_sha256") != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    patches = response.get("patches")
    if not isinstance(patches, list) or len(patches) != len(targets):
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")
    if any(not isinstance(item, dict) or set(item) != {"path", "value"} for item in patches):
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")

    by_path = {target.get("path"): target for target in targets}
    if None in by_path or len(by_path) != len(targets):
        raise RepairAssemblyError("$.patches", "repair_scope_violation")
    patch_paths = [patch.get("path") for patch in patches]
    if len(set(patch_paths)) != len(patch_paths) or set(patch_paths) != set(by_path):
        raise RepairAssemblyError("$.patches", "repair_scope_violation")

    changed = False
    for patch in patches:
        target = by_path[patch["path"]]
        _check_value(patch.get("value"), target.get("value_schema") or {})
        segments = _pointer_segments(target["path"])
        current = _lookup(saved, segments)
        if current != patch.get("value"):
            changed = True
        parent = _lookup(saved, segments[:-1])
        leaf = segments[-1]
        if isinstance(leaf, int):
            parent[leaf] = copy.deepcopy(patch["value"])
        else:
            if not isinstance(parent, dict) or leaf not in parent:
                raise RepairAssemblyError("$.patches", "repair_scope_violation")
            parent[leaf] = copy.deepcopy(patch["value"])
    if not changed:
        raise RepairAssemblyError("$.patches", "repair_no_progress")
    return saved
'''
text = text[:start] + new_apply + "\n"
path.write_text(text, encoding="utf-8")

# API field-patch schema/prompt: one patch per validator-proven target.
path = Path("src/api.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '- Copy base_sha256 and path exactly from the payload.\n- Return exactly one patch and only the replacement scalar value.\n',
    '- Copy base_sha256 and every target path exactly from the payload.\n- Return exactly one patch for every target and no other path.\n',
    1,
)
start = text.index('def _native_field_patch_response_format(repair_payload):\n')
end = text.index('\ndef _native_partial_repair_response_format(repair_payload):\n', start)
new_schema = r'''def _native_field_patch_response_format(repair_payload):
    targets = repair_payload.get("targets") if isinstance(repair_payload, dict) else None
    if not isinstance(targets, list):
        target = repair_payload.get("target") if isinstance(repair_payload, dict) else None
        targets = [target] if isinstance(target, dict) else []
    targets = [target for target in targets if isinstance(target, dict)]
    if not targets or any(not isinstance(target.get("path"), str) for target in targets):
        raise ValueError("field repair targets are required")
    base_sha = str(repair_payload.get("base_sha256") or "")
    choices = []
    for target in targets:
        value_schema = json.loads(json.dumps(target.get("value_schema") or {}))
        if not value_schema:
            raise ValueError("field repair value schema is required")
        choices.append({
            "type": "object",
            "properties": {
                "path": {"type": "string", "enum": [target["path"]]},
                "value": value_schema,
            },
            "required": ["path", "value"],
            "additionalProperties": False,
        })
    schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "mode": {"type": "string", "enum": ["field_patch"]},
            "base_sha256": {"type": "string", "enum": [base_sha]},
            "patches": {
                "type": "array", "minItems": len(targets), "maxItems": len(targets),
                "items": {"oneOf": choices},
            },
        },
        "required": ["schema_version", "mode", "base_sha256", "patches"],
        "additionalProperties": False,
    }
    return {"type": "json_schema", "json_schema": {
        "name": "ladder_field_patch", "strict": True, "schema": schema,
    }}

'''
text = text[:start] + new_schema + text[end:]
path.write_text(text, encoding="utf-8")

# generation: carry all targets; deterministic only when every target is deterministic.
path = Path("src/application/generation.py")
text = path.read_text(encoding="utf-8")
old = '''                    repair_payload = {
                        "repair_mode": "field_patch",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                        "base_sha256": self.repair_plan.get("base_sha256"),
                        "target": copy.deepcopy(self.repair_plan.get("target") or {}),
                    }
                    deterministic_field_patch = (
                        repair_payload["target"].get("strategy") == "deterministic"
                    )
'''
new = '''                    targets = self.repair_plan.get("targets")
                    if not isinstance(targets, list):
                        target = self.repair_plan.get("target")
                        targets = [target] if isinstance(target, dict) else []
                    repair_payload = {
                        "repair_mode": "field_patch",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                        "base_sha256": self.repair_plan.get("base_sha256"),
                        "targets": copy.deepcopy(targets),
                    }
                    if len(targets) == 1:
                        repair_payload["target"] = copy.deepcopy(targets[0])
                    deterministic_field_patch = bool(targets) and all(
                        isinstance(target, dict) and target.get("strategy") == "deterministic"
                        for target in targets
                    )
'''
if old not in text:
    raise SystemExit("generation field payload marker missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# workbench: batch blocking, composite pre-application, and model-free detection.
path = Path("src/application/workbench.py")
text = path.read_text(encoding="utf-8")
old = '''            if local_repair and isinstance(repair_plan, dict):
                target = repair_plan.get("target") or {}
                if target.get("strategy") == "blocked":
                    raise ConflictError(
                        "该校验错误涉及指令、地址或参数语义，局部修复不会猜测修改；请重新生成候选或手动修正。"
                    )
            if local_repair and not inherited_local_repair and repair_plan is None:
'''
new = '''            if local_repair and isinstance(repair_plan, dict):
                targets = repair_plan.get("targets")
                if not isinstance(targets, list):
                    target = repair_plan.get("target")
                    targets = [target] if isinstance(target, dict) else []
                if repair_plan.get("mode") in {"blocked_batch"} or any(
                    isinstance(target, dict) and target.get("strategy") == "blocked"
                    for target in targets
                ):
                    raise ConflictError(
                        "本次校验已一次性发现多个问题，其中至少一个语义字段没有唯一或有界修复证据；系统不会猜测修改。"
                    )
                if repair_plan.get("mode") == "composite":
                    from application.field_repair import apply as apply_field_patch, deterministic_response
                    payload = {
                        "repair_mode": "field_patch",
                        "base_sha256": repair_plan.get("base_sha256"),
                        "targets": copy.deepcopy(targets),
                    }
                    response = deterministic_response(payload)
                    if response is None:
                        raise ConflictError("混合结构/字段修复包含非确定性语义修改，系统不会拆成多轮猜测。")
                    field_plan = {
                        "schema_version": repair_plan.get("schema_version", 1),
                        "mode": "composite",
                        "base_sha256": repair_plan.get("base_sha256"),
                        "targets": copy.deepcopy(targets),
                    }
                    repair_base = apply_field_patch(repair_base, response, field_plan)
                    repair_plan = None
            if local_repair and not inherited_local_repair and repair_plan is None:
'''
if old not in text:
    raise SystemExit("workbench plan handling marker missing")
text = text.replace(old, new, 1)
old = '''        if local_repair and repair_plan is not None:
            target = repair_plan.get("target") or {}
            repair_text = (
                "这是用户明确确认的一次字段级 JSON 修复。不要返回梯级、分支或完整程序。"
                "只返回 field_patch 协议对象，并且只能修改 target.path 指定的一个字段。"
                "不要改变其他地址、参数、触点极性或结构。\\n"
                f"目标字段：{target.get('diagnostic_path') or target.get('path')}\\n"
                f"失败位置：{location_text}"
            )
'''
new = '''        if local_repair and repair_plan is not None:
            targets = repair_plan.get("targets")
            if not isinstance(targets, list):
                target = repair_plan.get("target")
                targets = [target] if isinstance(target, dict) else []
            target_text = "；".join(
                str(target.get("diagnostic_path") or target.get("path") or "")
                for target in targets if isinstance(target, dict)
            )
            repair_text = (
                "这是用户明确确认的一次字段级 JSON 修复。不要返回梯级、分支或完整程序。"
                "只返回 field_patch 协议对象，并且只能修改 targets 中明确列出的字段；所有其他字段冻结。"
                "不要改变其他地址、参数、触点极性或结构。\\n"
                f"目标字段：{target_text}\\n"
                f"失败位置：{location_text}"
            )
'''
if old not in text:
    raise SystemExit("workbench repair text marker missing")
text = text.replace(old, new, 1)
old = '''            repair_plan = command.get("repair_plan") if isinstance(command.get("repair_plan"), dict) else {}
            repair_target = repair_plan.get("target") if isinstance(repair_plan.get("target"), dict) else {}
            deterministic_generation_repair = (
                command["kind"] == "generation"
                and repair_plan.get("mode") == "field_patch"
                and repair_target.get("strategy") == "deterministic"
            )
'''
new = '''            repair_plan = command.get("repair_plan") if isinstance(command.get("repair_plan"), dict) else {}
            repair_targets = repair_plan.get("targets")
            if not isinstance(repair_targets, list):
                repair_target = repair_plan.get("target")
                repair_targets = [repair_target] if isinstance(repair_target, dict) else []
            deterministic_generation_repair = (
                command["kind"] == "generation"
                and repair_plan.get("mode") == "field_patch"
                and bool(repair_targets)
                and all(
                    isinstance(target, dict) and target.get("strategy") == "deterministic"
                    for target in repair_targets
                )
            )
'''
if old not in text:
    raise SystemExit("workbench provider marker missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
