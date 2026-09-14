from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected 1 marker, got {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# field_repair: only validator-proven scalar fields may be unfrozen.
path = "src/application/field_repair.py"
replace_once(path,
    "from plc_generation_contract import MAX_LABEL_LEN\n",
    "from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics\nfrom plc_generation_contract import MAX_LABEL_LEN\n")
replace_once(path,
    "def _target(saved, row, segments, *, strategy, current_value=None,\n            value_schema=None, deterministic_value=None):\n",
    "def _target(saved, row, segments, *, strategy, current_value=None,\n            value_schema=None, deterministic_value=None, context=None):\n")
replace_once(path,
    "    if strategy == \"deterministic\":\n        target[\"deterministic_value\"] = copy.deepcopy(deterministic_value)\n",
    "    if strategy == \"deterministic\":\n        target[\"deterministic_value\"] = copy.deepcopy(deterministic_value)\n    if isinstance(context, dict) and context:\n        target[\"context\"] = copy.deepcopy(context)\n")
marker = "def plan(base, violations, plc_model=\"FX3U\"):\n"
helper = '''def _edit_distance_at_most_one(left, right):
    left = str(left or "").upper()
    right = str(right or "").upper()
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        j += 1
    return True


def _opcode_repair_candidates(observed, operands, plc_model):
    """Return evidence-bounded opcode replacements without semantic guessing."""
    token = str(observed or "").strip().upper()
    if not token:
        return [], "none", False
    count = len(operands) if isinstance(operands, list) else 0
    allowed = []
    for mnemonic in generation_app_instr_mnemonics(plc_model):
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
        if spec is not None and spec.accepts_arity(count):
            allowed.append(mnemonic)
    allowed_set = set(allowed)
    strict = []
    if token.startswith("DD"):
        candidate = token[1:]
        if candidate in allowed_set:
            strict.append(candidate)
    if token.endswith("PP"):
        candidate = token[:-1]
        if candidate in allowed_set and candidate not in strict:
            strict.append(candidate)
    if len(strict) == 1:
        return strict, "modifier_normalization", True
    if strict:
        return sorted(strict), "modifier_normalization", False
    nearby = sorted(mnemonic for mnemonic in allowed if _edit_distance_at_most_one(token, mnemonic))
    if 0 < len(nearby) <= 8:
        return nearby, "single_edit_catalog_match", False
    return [], "none", False


'''
text = Path(path).read_text(encoding="utf-8")
if marker not in text or "def _opcode_repair_candidates" in text:
    raise SystemExit("field_repair helper marker mismatch")
Path(path).write_text(text.replace(marker, helper + marker, 1), encoding="utf-8")
replace_once(path, "    del plc_model\n", "")
replace_once(path,
    "    leaf = segments[-1]\n    if isinstance(leaf, str):\n        rule = _value_schema(parent, leaf, reason)\n",
    '''    leaf = segments[-1]
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
            )
        return _blocked(saved, row, segments, current)

    if isinstance(leaf, str):
        rule = _value_schema(parent, leaf, reason)
''')
replace_once(path,
    "    # Container paths, opcodes, addresses, operands, values and polarity are not\n    # repair recipes. Keep the original diagnostic and require manual/regeneration\n    # context instead of asking a model to infer semantics.\n",
    "    # Every unproven field remains frozen. Address/operand/value/polarity\n    # errors stay blocked until a validator supplies a bounded replacement set.\n")

# generation: unique deterministic field fixes bypass the model.
path = "src/application/generation.py"
replace_once(path,
    "            repair_payload = None\n            repair_kind = \"format\"\n",
    "            repair_payload = None\n            repair_kind = \"format\"\n            deterministic_field_patch = False\n")
replace_once(path,
    '''                    repair_payload = {
                        "repair_mode": "field_patch",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                        "base_sha256": self.repair_plan.get("base_sha256"),
                        "target": copy.deepcopy(self.repair_plan.get("target") or {}),
                    }
''',
    '''                    repair_payload = {
                        "repair_mode": "field_patch",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                        "base_sha256": self.repair_plan.get("base_sha256"),
                        "target": copy.deepcopy(self.repair_plan.get("target") or {}),
                    }
                    deterministic_field_patch = (
                        repair_payload["target"].get("strategy") == "deterministic"
                    )
''')
replace_once(path,
    "                if repair_call:\n                    if self.dependencies.repair_response is not None:\n",
    '''                if repair_call:
                    if deterministic_field_patch:
                        from application.field_repair import deterministic_response
                        local_response = deterministic_response(repair_payload)
                        if local_response is None:
                            raise GenerationError(tr('确定性字段修复计划无效'))
                        full_content = json.dumps(local_response, ensure_ascii=False)
                        self._emit("progress", {
                            "stage": "deterministic_field_repair",
                            "message": tr('已根据唯一校验证据确定性修复字段；未调用模型。'),
                        })
                    elif self.dependencies.repair_response is not None:
''')

# workbench: deterministic repair does not initialize a model provider.
path = "src/application/workbench.py"
replace_once(path,
    '''            images = self._attachments(project_id, command.get("attachment_ids", []))
            requires_model = command["kind"] not in ("gx_read", "gx_inspect") and (command["kind"] != "review" or command.get("deep", True))
            provider, model = self.model_factory() if requires_model else (None, {})
''',
    '''            images = self._attachments(project_id, command.get("attachment_ids", []))
            repair_plan = command.get("repair_plan") if isinstance(command.get("repair_plan"), dict) else {}
            repair_target = repair_plan.get("target") if isinstance(repair_plan.get("target"), dict) else {}
            deterministic_generation_repair = (
                command["kind"] == "generation"
                and repair_plan.get("mode") == "field_patch"
                and repair_target.get("strategy") == "deterministic"
            )
            requires_model = (
                command["kind"] not in ("gx_read", "gx_inspect")
                and (command["kind"] != "review" or command.get("deep", True))
                and not deterministic_generation_repair
            )
            provider, model = self.model_factory() if requires_model else (None, {})
''')

# prompts explicitly describe mutable-vs-frozen fields.
path = "src/api.py"
replace_once(path,
    "- Use target.context only to choose the corrected value; do not redesign control logic.\n- The provider schema is authoritative for the allowed replacement value.\n",
    "- Only `target.path` is mutable. Every other ladder field is immutable.\n- `target.context` contains validator evidence and immutable sibling values.\n- If `target.value_schema.enum` exists, it is exhaustive; choose only from it.\n- The provider schema is authoritative for the allowed replacement value.\n")
replace_once(path,
    "- Preserve control logic, addresses, operands, parameters and contact polarity\n  except for the minimum structural/protocol correction explicitly requested.\n",
    "- This partial path repairs structure/protocol only. Validator-proven scalar\n  semantic errors are repaired separately through field_patch. Preserve control\n  logic, addresses, opcodes, operands, parameters and contact polarity here.\n")
