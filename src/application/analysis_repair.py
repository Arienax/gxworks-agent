# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Private, path-scoped repairs of Agent-A drafts; the application owns assembly."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, replace

from jsonschema import Draft202012Validator

from application.analysis_results import (
    AnalysisProtocolError, analysis_grounding_details, current_analysis_protocol_details,
    prepare_analysis_payload, _CURRENT_APPROACH_FORBIDDEN_FIELDS, _CURRENT_ROOT_FORBIDDEN_FIELDS,
)
from model_runtime.provider import CollectedResponse
from plc.execution_intent import (
    TRIGGER_KINDS,
    execution_claim_anchors, execution_claim_preservable_fields,
    extract_explicit_execution_semantics,
)
from plc.specification.approach import INSTRUCTION_CHOICES_SCHEMA, SUPPORTED_STRUCTURES
from plc.specification.decision_basis import (
    DECISION_BASIS_SCHEMA, DecisionSources, review_decision_basis,
)
from plc.specification.explicit_constraint_claims import (
    OPERATIONS, SCOPES, compile_explicit_constraint_claims, explicit_claim_anchors,
    explicit_constraint_protocol, explicit_target_contracts, normalize_explicit_claim_representations,
)
from plc.specification.implementation import PLAN_ROW_SCHEMAS, uncovered_requirements
from plc.specification.output_logic import OUTPUT_LOGIC_SCHEMA, output_logic_details


REPAIR_SYSTEM_PROMPT = """# Analysis local repair
应用持有完整分析对象；只修 targets 中的失败片段，保留其余需求、方案、结构标签和问题。
只返回 {"repairs":[{"path":"/…","value":…}]}。每个 path 必须恰好出现一次，不能输出完整分析对象或额外字段。
claim 的 value 是 0..n 个规范 claim 的数组；普通字段按 value_schema 替换；remove 字段用 null。
只从 current_request 复制逐字 evidence，不翻译、不改写。must_preserve 中的身份、操作、作用范围必须保留，不增添别的目标。
无依据候选允许删除；有原文依据的目标不能删。具体设备列表只放具体地址；方法描述由应用保留原文，不发明 target 类型。
不补默认地址或证据，不新增首扫、工艺假设、问题或硬件。操作数原样保留。只输出 JSON。
""" + explicit_constraint_protocol()

SYNTAX_SYSTEM_PROMPT = """# Analysis JSON syntax repair
只修 rejected_draft 的 JSON 语法，返回完整 JSON 对象。保留所有文字、字段值、设备、方案和问题；不补工艺事实、不改方案、不生成 PLC 代码。
只改标点、引号、转义或结构性的字段分隔；不要用其他字段填充缺失内容。无 markdown 或解释。
"""


@dataclass(frozen=True)
class AnalysisResponse(CollectedResponse):
    repair_receipt: dict = field(default_factory=dict, repr=False)
    continuation_message: object = field(default=None, repr=False)


def assembled_response(response, payload, receipt, attempts=None):
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    message = replace(response.message, content=content)
    if content != response.message.content and message._provider_fields.get("responses_items"):
        # Responses items include the original answer and cannot be replayed
        # as the assembled answer. Keep the corrected assistant text.
        message = replace(message, _provider_fields={})
    return AnalysisResponse(message=message,
                            usage=response.usage, response_language=response.response_language,
                            raw_attempts=response.raw_attempts if attempts is None else attempts,
                            repair_receipt=copy.deepcopy(receipt))


def _segments(path):
    return [int(index) if index else name for index, name in re.findall(r"\[(\d+)\]|\.([A-Za-z_][A-Za-z0-9_]*)", path)]


def _pointer(segments):
    return "/" + "/".join(str(s).replace("~", "~0").replace("/", "~1") for s in segments)


def _get(value, segments):
    for key in segments:
        try:
            value = value[key]
        except (KeyError, IndexError, TypeError):
            return None
    return value


def _semantic_schema():
    return {"type": "object", "required": ["kind", "status"],
            "properties": {"kind": {"enum": ["structure"]}, "status": {"enum": ["required", "forbidden", "any_of"]},
                           "value": {"enum": sorted(SUPPORTED_STRUCTURES)},
                           "values": {"type": "array", "items": {"enum": sorted(SUPPORTED_STRUCTURES)}}}}


def _approach_schema(*, require_decision_basis=False, require_output_logic=False):
    required = ["approach_id", "name", "description", "implementation_semantics"]
    if require_decision_basis:
        required.append("decision_basis")
    if require_output_logic:
        required.append("output_logic")
    return {"type": "object", "required": required,
            "properties": {
                **dict.fromkeys(("approach_id", "name", "description", "pros", "cons", "generation_guide"), {"type": "string"}),
                "implementation_semantics": {"type": "array", "items": _semantic_schema()},
                "instruction_choices": copy.deepcopy(INSTRUCTION_CHOICES_SCHEMA),
                "output_logic": copy.deepcopy(OUTPUT_LOGIC_SCHEMA),
                "decision_basis": copy.deepcopy(DECISION_BASIS_SCHEMA),
            }}


def design_submission_schema():
    """Wire shape for external Design models; grounding and acceptance still use the Web validators."""
    text = {"type": "string"}
    strings = {"type": "array", "items": text}
    evidence = {"type": "array", "minItems": 0, "items": {"type": "string", "minLength": 1},
                "description": "Verbatim spans of the current user request."}
    inferred = {"type": "boolean",
                "description": "Inferred, not quoted: receipt only, never binding."}

    def record(fields, required=()):
        return {"type": "object", "properties": fields, "required": list(required)}

    def rows(item):
        return {"type": "array", "items": item}

    trigger = record({"kind": {"enum": sorted(TRIGGER_KINDS)}, "source_devices": strings,
                      "from": {"enum": [0, 1]}, "to": {"enum": [0, 1]},
                      "period_ms": {"type": "number", "exclusiveMinimum": 0}}, ["kind"])
    execution = record({"trigger": trigger, "evidence": evidence, "inferred": inferred,
                        "effect": record({"kind": text, "devices": strings}),
                        "rearm": {"type": ["string", "boolean"]}}, ["trigger"])
    explicit = record({"operation": {"enum": sorted(OPERATIONS)}, "scope": {"enum": sorted(SCOPES)},
                       "target": {"anyOf": [c["schema"] for c in explicit_target_contracts().values()]},
                       "evidence": evidence, "inferred": inferred}, ["operation", "scope", "target"])
    return record({"summary": text, "approaches": {**rows(_approach_schema(require_decision_basis=True, require_output_logic=True)), "minItems": 1, "maxItems": 3},
                   "execution_intent_claims": rows(execution), "explicit_constraint_claims": rows(explicit),
                   "suggested_io": {"type": "object", "description": "Group user-declared address-to-label maps by device type."},
                   "missing_info": rows(record({"id": text, "question": text, "required": {"type": "boolean"},
                                                "options": strings}, ["id", "question"])),
                   "hardware_config": {"type": "object"}, "assumptions": strings,
                   **copy.deepcopy(PLAN_ROW_SCHEMAS)}, ["summary", "approaches"])


def _field_schema(segments, *, require_decision_basis=False, require_output_logic=False):
    schema = PLAN_ROW_SCHEMAS.get(segments[0]) if segments else None
    if schema is not None:
        for segment in segments[1:]:
            schema = schema["items"] if isinstance(segment, int) else schema["properties"][segment]
        return copy.deepcopy(schema)
    if not segments or segments == ["approaches"]:
        return {"type": "array", "items": _approach_schema(require_decision_basis=require_decision_basis, require_output_logic=require_output_logic)}
    if segments == ["summary"]:
        return {"type": "string", "pattern": r"\S"}
    if segments[-1] == "instruction_choices":
        return copy.deepcopy(INSTRUCTION_CHOICES_SCHEMA)
    if segments[-1] == "output_logic":
        return copy.deepcopy(OUTPUT_LOGIC_SCHEMA)
    if "decision_basis" in segments:
        schema = DECISION_BASIS_SCHEMA
        for segment in segments[segments.index("decision_basis") + 1:]:
            schema = schema["items"] if isinstance(segment, int) else schema["properties"][segment]
        return copy.deepcopy(schema)
    if "implementation_semantics" in segments:
        schema = _semantic_schema()
        return {"type": "array", "items": schema} if segments[-1] == "implementation_semantics" else {"anyOf": [schema, {"type": "null"}]}
    return {"type": "array"} if str(segments[-1]).endswith("claims") else {"type": "object"}


def design_coverage_target(payload, working_draft, user_text):
    if working_draft is None:
        return None

    from application.spec_document import merge_analysis

    sections = ("requirements", "implementation_items")
    previous = {key: working_draft[key] for key in ("plc_model", "target_mode", *sections) if key in working_draft}
    updates = {key: payload[key] for key in sections if key in payload}
    merged = merge_analysis(previous, {}, updates, user_text)
    missing = uncovered_requirements(merged)
    if not missing:
        return None

    fields = (*PLAN_ROW_SCHEMAS["implementation_items"]["items"]["required"], "requirement_ids")
    items = [{key: row[key] for key in fields if key in row} for row in merged["implementation_items"]
             if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()]
    return {
        "path": "/implementation_items",
        "mode": "requirement_links",
        "current_value": items,
        "missing_requirements": [{"id": row["id"], "text": row.get("text", "")} for row in missing],
        "instruction": "将每个遗漏的需求分配给实际负责它的现有实现项。value 用实现项 ID 映射到需追加的需求 ID 列表，只列需要补充的关联。",
        "value_schema": {
            "type": "object", "minProperties": 1,
            "propertyNames": {"enum": [row["id"] for row in items]},
            "additionalProperties": {"type": "array", "minItems": 1, "uniqueItems": True,
                                     "items": {"enum": [row["id"] for row in missing]}},
        },
        "errors": [{"code": "invalid_project_plan", "path": "$.requirements",
                    "message": f"需求 {row['id']} 尚未安排实现分工"} for row in missing],
    }


def citation_repair_target(path, current, schema):
    fields = schema["properties"]
    editable = [key for key in ("references", "status", "conclusion") if key in fields]
    return {"path": path, "mode": "decision_citation", "current_value": current,
            "value_schema": {"type": "object", "minProperties": 1,
                             "properties": {key: fields[key] for key in editable}, "additionalProperties": False},
            "must_preserve": {key: value for key, value in current.items() if key not in editable}}


def plan_analysis_repair(payload, user_text, *, plc_model="FX3U", confirmed_spec=None, contract_stage="bound",
                         working_draft=None, decision_sources=None, require_output_logic=False):
    """Normalize known representations, then whitelist all remaining failures."""
    base = copy.deepcopy(payload)
    local = []
    if "explicit_constraint_claims" in base:
        base["explicit_constraint_claims"], local = normalize_explicit_claim_representations(base["explicit_constraint_claims"])
    prepared = prepare_analysis_payload(base, contract_stage=contract_stage, user_text=user_text,
                                        confirmed_spec=confirmed_spec)
    for section, action in (("implementation_items", "normalize_instance_names"),
                            ("missing_info", "normalize_review_questions"),
                            ("open_questions", "normalize_review_questions")):
        if prepared.get(section) != base.get(section):
            base[section] = prepared[section]
            local.append({"path": f"/{section}", "action": action})
    deferred = {item["index"] for item in prepared.get("_deferred_execution_claims", [])}
    claims = base.get("execution_intent_claims")
    execution_indices = [i for i in range(len(claims)) if i not in deferred] if isinstance(claims, list) else []
    details = [*current_analysis_protocol_details(prepared),
               *output_logic_details(prepared, required=require_output_logic),
               *analysis_grounding_details(prepared, user_text, plc_model=plc_model, confirmed_spec=confirmed_spec,
                                           decision_sources=decision_sources)]
    for item in details:
        match = re.match(r"\$\.execution_intent_claims\[(\d+)\]", item["path"])
        if match and execution_indices:
            item["path"] = item["path"].replace(match[0], f"$.execution_intent_claims[{execution_indices[int(match[1])]}]", 1)
    targets = {}

    def add(segments, mode, error=None):
        pointer = _pointer(segments)
        if pointer not in targets:
            current = copy.deepcopy(_get(base, segments))
            target = {"path": pointer, "mode": mode, "current_value": current, "errors": []}
            if mode == "claim":
                family = segments[0]
                if family == "explicit_constraint_claims":
                    preserve = {"targets": explicit_claim_anchors(current, user_text, plc_model)}
                    # Prose methods are not supported device/opcode candidates.
                    # Their exact source remains in the receipt and request.
                    original_target = current.get("target") if isinstance(current, dict) else None
                    preserve["unsupported_target"] = bool(
                        not preserve["targets"] and isinstance(original_target, dict)
                        and str(original_target.get("kind") or "").strip().casefold() != "category"
                    )
                    for name, choices in (("operation", OPERATIONS), ("scope", SCOPES)):
                        if isinstance(current, dict) and str(current.get(name) or "").strip().casefold() in choices:
                            preserve[name] = current[name]
                else:
                    preserve = execution_claim_anchors(current, user_text, confirmed_spec)
                    preserve.update(execution_claim_preservable_fields(current))
                    if (isinstance(current, dict) and not current.get("evidence")
                            and isinstance(current.get("trigger"), dict)
                            and str(current["trigger"].get("kind") or "").strip().casefold() in {"first_scan", "cyclic"}):
                        semantic = {"first_scan": "FIRST_SCAN", "cyclic": "CYCLIC"}[
                            str(current["trigger"]["kind"]).strip().casefold()]
                        explicit_spans = [r["evidence"] for r in extract_explicit_execution_semantics(user_text)
                                          if r["semantic"] == semantic]
                        if explicit_spans:
                            preserve["explicit_scan_evidence"] = explicit_spans
                        elif not preserve["source_devices"] and not preserve["effect_devices"]:
                            preserve["unsupported_candidate"] = True
                target["must_preserve"] = preserve
                target["value_schema"] = {"type": "array", "items": {"type": "object", "required": ["target" if family == "explicit_constraint_claims" else "trigger"]}}
            else:
                target["value_schema"] = ({"type": "null"} if mode == "remove" else
                    _field_schema(segments, require_decision_basis=decision_sources is not None, require_output_logic=require_output_logic))
                if segments[-1] == "output_logic":
                    approach = _get(base, segments[:2])
                    target["comparison"] = {key: copy.deepcopy(approach[key]) for key in (
                        "name", "description", "generation_guide", "instruction_choices", "decision_basis",
                    ) if key in approach}
                    target["instruction"] = (
                        "由模型补全该方案从状态到输出的实现，比较同一条件下的可行输出方法并选定。"
                        "说明触发、更新顺序、并发合并、释放及中断恢复；不适用时说明原因。"
                        "selection 记录规模和行为取舍，不只写算法名称。"
                        "保持既定方案、用户要求及未知条件，不把技术方法转交用户，不新增工艺条件。"
                    )
                if segments == ["approaches"]:
                    target["instruction"] = (
                        "依据本轮需求和已有分工补齐候选方案。方案文字写在 description。"
                        "implementation_semantics 是结构对象数组，没有明确结构约束时写 []。"
                    )
                if "decision_basis" in segments:
                    if segments[-1] == "decision_basis":
                        approach = _get(base, segments[:2])
                        target["comparison"] = {key: approach[key] for key in (
                            "name", "description", "pros", "cons", "generation_guide",
                        ) if key in approach}
                    elif len(segments) == 4:
                        sources = decision_sources or DecisionSources(user_text, confirmed_spec)
                        reviewed, _ = review_decision_basis([current], path=pointer,
                                                            resolve_reference=sources.resolve)
                        target["supported_references"] = reviewed[0]["references"] if reviewed else []
                        target["available_sources"] = sources.reference_prompt()
                        if reviewed and (error or {}).get("code") in {
                            "ungrounded_decision_basis", "unconfirmed_decision_basis",
                        }:
                            target["must_preserve"] = {"variant": current["variant"]}
                            target["original_premise"] = current["premise"]
                    elif len(segments) == 6 and segments[-2] == "applicability_conditions" and isinstance(current, dict):
                        sources = decision_sources or DecisionSources(user_text, confirmed_spec)
                        target["supported_references"] = [ref for ref in current.get("references", []) if sources.matches(ref)]
                        target["available_sources"] = sources.reference_prompt()
                        target["original_premise"] = current.get("premise", "")
                    if isinstance(current, dict) and (error or {}).get("code") in {
                        "ungrounded_decision_basis", "unconfirmed_decision_basis", "unconfirmed_applicability_condition",
                    } and len(segments) in (4, 6):
                        target.update(citation_repair_target(pointer, current, target["value_schema"]))
                        target["instruction"] = "只返回需要改动的引用、状态或结论字段，未提交字段由本地保留。"
                if len(segments) == 4 and "implementation_semantics" in segments and isinstance(current, dict):
                    preserve = {}
                    if str(current.get("kind") or "").strip().casefold() == "structure":
                        preserve["kind"] = current["kind"]
                    if isinstance(current.get("status"), str) and current["status"].strip().casefold() in {"required", "forbidden", "any_of"}:
                        preserve["status"] = current["status"]
                    if isinstance(current.get("value"), str) and current["value"].strip().casefold() in SUPPORTED_STRUCTURES:
                        preserve["value"] = current["value"]
                    if isinstance(current.get("values"), list) and current["values"] and all(isinstance(v, str) and v.strip().casefold() in SUPPORTED_STRUCTURES for v in current["values"]):
                        preserve["values"] = current["values"]
                    target["must_preserve"] = preserve
                    target["unsupported_structure"] = not (
                        isinstance(current.get("value"), str) and current["value"].strip().casefold() in SUPPORTED_STRUCTURES
                        or isinstance(current.get("values"), list) and bool(current["values"])
                        and all(isinstance(v, str) and v.strip().casefold() in SUPPORTED_STRUCTURES for v in current["values"])
                    )
            targets[pointer] = target
        if error:
            # Target examples are already in the shared system contract. Keep
            # execution examples here: from/to must never be inferred from prose.
            omitted = {"contracts", "schema", "actual"}
            if segments[0] != "execution_intent_claims" or ".trigger" not in error["path"]:
                omitted.add("example")
            targets[pointer]["errors"].append({k: copy.deepcopy(v) for k, v in error.items() if k not in omitted})

    for error in details:
        segments = _segments(error["path"])
        if len(segments) >= 2 and segments[0] in {"explicit_constraint_claims", "execution_intent_claims"} and isinstance(segments[1], int):
            add(segments[:2], "claim", error)
        elif not segments:
            for name in _CURRENT_ROOT_FORBIDDEN_FIELDS.intersection(base):
                add([name], "remove", error)
        elif len(segments) == 2 and segments[0] == "approaches" and isinstance(_get(base, segments), dict) and "must not emit" in error["message"]:
            for name in _CURRENT_APPROACH_FORBIDDEN_FIELDS.intersection(_get(base, segments)):
                add([*segments, name], "remove", error)
        else:
            for name in ("operation_intents", "behavior_constraints"):
                if segments == [name]:
                    add(segments, "remove", error)
                    break
            else:
                if "implementation_semantics" in segments and len(segments) > 4:
                    segments = segments[:4]
                add(segments, "field", error)
    if not any(error["path"].startswith(("$.requirements", "$.implementation_items")) for error in details):
        coverage = design_coverage_target(base, working_draft, user_text)
        if coverage:
            targets[coverage["path"]] = coverage
    if "/approaches" in targets and working_draft is not None:
        targets["/approaches"]["value_schema"].update(minItems=1, maxItems=3)
        summary = base.get("summary")
        if not working_draft.get("summary") and not (isinstance(summary, str) and summary.strip()):
            add(["summary"], "field", {"path": "$.summary", "code": "invalid_analysis_shape",
                                       "message": "initial review draft needs a summary"})
        if "/implementation_items" not in targets and base.get("implementation_items"):
            targets["/approaches"]["implementation_items"] = base["implementation_items"]
    return {"base": base, "targets": list(targets.values()), "receipt": {"local": local, "model_paths": [], "retained_text": []}}


def repair_messages(plan, user_text):
    prompt = REPAIR_SYSTEM_PROMPT
    if any("/decision_basis" in target["path"] for target in plan["targets"]):
        prompt += ("\n修复 decision_basis 时可使用 current_request、supported_references 和 available_sources 中的依据。decision_citation 的 value 只提交有变化的引用、状态或结论。"
                   "保留有依据的引用。缺少来源的前提用 unknown 或 inferred，结论用 conditional。")
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"current_request": user_text, "targets": plan["targets"]}, ensure_ascii=False, separators=(",", ":"))}]


def _identity(target):
    return json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _target_identities(target):
    if target.get("kind") in {"device", "opcode"}:
        return {_identity({"kind": target["kind"], "values": [v]}) for v in target.get("values", [])}
    return {_identity(target)}


def _reject(path, message):
    raise AnalysisProtocolError([path + ": " + message], details=[{"path": path, "code": "repair_scope_violation", "message": message}])


def _validate_claim_replacement(target, values, user_text, plc_model, confirmed_spec):
    path, original = target["path"], target["current_value"]
    if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
        _reject(path, "claim replacement must be an array of objects")
    preserve = target["must_preserve"]
    if preserve.get("unsupported_candidate") and values:
        _reject(path, "an unsupported scan candidate must be removed, not given invented evidence")
    if preserve.get("explicit_scan_evidence"):
        if not values:
            _reject(path, "an explicitly requested scan requirement cannot be removed")
        if any(not isinstance(claim.get("evidence"), list) or not any(" ".join(span.split()) in " ".join(evidence.split())
                       for span in preserve["explicit_scan_evidence"]
                       for evidence in claim["evidence"] if isinstance(evidence, str)) for claim in values):
            _reject(path, "scan repair must retain its explicit current-request evidence")
    if path.startswith("/explicit_constraint_claims/"):
        receipt = compile_explicit_constraint_claims(values, user_text, plc_model)
        non_inferred = [row for row in receipt["rejected"] if row.get("reason") != "inferred_claim_not_grounded"]
        if non_inferred:
            _reject(path, "replacement has ungrounded targets or evidence: " + non_inferred[0]["reason"])
        actual = set()
        inferred_claims = [row["claim"] for row in receipt["rejected"]
                           if row.get("reason") == "inferred_claim_not_grounded" and isinstance(row.get("claim"), dict)]
        for claim in [*receipt["accepted"], *inferred_claims]:
            for name in ("operation", "scope"):
                if name in preserve and str(claim.get(name) or "").strip().casefold() != str(preserve[name]).strip().casefold():
                    _reject(path, "repair changes a valid " + name)
            if "claim_id" in claim:
                actual.update(_target_identities(claim["target"]))
            else:
                for anchor in explicit_claim_anchors(claim, user_text, plc_model):
                    actual.update(_target_identities(anchor))
        expected = set().union(*(_target_identities(t) for t in preserve["targets"])) if preserve["targets"] else set()
        if expected and actual != expected:
            _reject(path, "repair loses or adds an identified user-fixed target")
    else:
        from plc.execution_intent import compile_execution_intent_claims, execution_claim_anchors
        receipt = compile_execution_intent_claims(values, user_text, confirmed_spec=confirmed_spec)
        non_inferred = [row for row in receipt["rejected"] if row.get("reason") != "inferred_claim_not_grounded"]
        if non_inferred:
            _reject(path, "replacement has ungrounded execution evidence")
        actual = {"source_devices": set(), "effect_devices": set()}
        for claim in values:
            for name, devices in execution_claim_anchors(claim, user_text, confirmed_spec).items():
                actual[name].update(devices)
            for name, value in preserve.get("trigger", {}).items():
                if claim.get("trigger", {}).get(name) != value:
                    _reject(path, "repair changes an existing trigger field")
            for name in ("effect", "rearm"):
                if name == "effect" and name in preserve:
                    if any(claim.get("effect", {}).get(k) != v for k, v in preserve[name].items()):
                        _reject(path, "repair changes existing execution metadata")
                elif name in preserve and claim.get(name) != preserve[name]:
                    _reject(path, "repair changes existing execution metadata")
        for name in actual:
            if preserve[name] and actual[name] != set(preserve[name]):
                _reject(path, "repair loses or adds an identified execution device")
        if not values and any(preserve[name] for name in actual):
            _reject(path, "repair removes a device-bound execution claim")
    if not values and isinstance(original, dict):
        spans = original.get("evidence") or []
        if not preserve.get("unsupported_target") and any(isinstance(s, str) and s.strip() and " ".join(s.split()) in " ".join(user_text.split()) for s in spans):
            _reject(path, "empty replacement cannot delete a supported claim")


def apply_analysis_repair(plan, patch, user_text, *, plc_model="FX3U", confirmed_spec=None):
    """Apply only listed paths to the private baseline, with stable original indices."""
    if not isinstance(patch, dict) or set(patch) != {"repairs"} or not isinstance(patch["repairs"], list):
        _reject("$", "expected only the repairs array")
    expected = {t["path"]: t for t in plan["targets"]}
    changes = {}
    for repair in patch["repairs"]:
        if not isinstance(repair, dict) or set(repair) != {"path", "value"}:
            _reject("$", "repair requires exactly path and value")
        path = repair["path"]
        if not isinstance(path, str) or path not in expected or path in changes:
            _reject("$", "duplicate or unlisted repair path")
        target = expected[path]
        value = repair["value"]
        if target["mode"] == "claim":
            _validate_claim_replacement(target, value, user_text, plc_model, confirmed_spec)
        elif target["mode"] == "decision_citation":
            if not isinstance(value, dict):
                _reject(path, "citation repair requires the changed fields")
            # Older clients may echo the full row. Accept only identical
            # immutable fields and apply only the editable values.
            for key in value.keys() & target["must_preserve"].keys():
                if value[key] != target["must_preserve"][key]:
                    _reject(path, "citation repair changes an immutable field")
            value = {key: item for key, item in value.items() if key not in target["must_preserve"]}
            if not Draft202012Validator(target["value_schema"]).is_valid(value):
                _reject(path, "citation repair requires references, status or conclusion")
        elif target["mode"] == "field" and (
            path in ("/approaches", "/summary") or path.endswith(("/instruction_choices", "/output_logic")) or "/decision_basis" in path
        ):
            if not Draft202012Validator(target["value_schema"]).is_valid(repair["value"]):
                _reject(path, "repair value must match the requested field schema")
            if any(repair["value"].get(key) != value for key, value in target.get("must_preserve", {}).items()):
                _reject(path, "citation repair changes the original variant")
            if ("original_premise" in target and repair["value"]["status"] == "known"
                    and repair["value"]["premise"] != target["original_premise"]):
                _reject(path, "citation repair changes the premise it claims is known")
        elif target["mode"] == "requirement_links":
            value = repair["value"]
            if not Draft202012Validator(target["value_schema"]).is_valid(value):
                _reject(path, "repair must add missing requirement IDs to existing implementation items")
            if {ref for refs in value.values() for ref in refs} != {row["id"] for row in target["missing_requirements"]}:
                _reject(path, "repair must cover every missing requirement ID")
        elif target.get("unsupported_structure"):
            if repair["value"] is not None:
                _reject(path, "unsupported structure candidates must be removed, not approximated")
        elif target.get("must_preserve"):
            if not isinstance(repair["value"], dict) or any(repair["value"].get(k) != v for k, v in target["must_preserve"].items()):
                _reject(path, "repair changes valid structure fields")
        if target["mode"] == "remove" and repair["value"] is not None:
            _reject(path, "forbidden metadata removal requires null")
        changes[path] = copy.deepcopy(value)
    if set(changes) != set(expected):
        _reject("$", "every failed path requires exactly one repair")
    result = copy.deepcopy(plan["base"])
    receipt = copy.deepcopy(plan["receipt"])
    # All paths refer to the baseline. Descending indices prevent splice drift.
    ordered = sorted(expected.values(), key=lambda t: tuple((1, int(s)) if s.isdigit() else (0, s) for s in t["path"].split("/")[1:]), reverse=True)
    for target in ordered:
        segments = [int(s) if s.isdigit() else s.replace("~1", "/").replace("~0", "~") for s in target["path"].split("/")[1:]]
        parent = _get(result, segments[:-1])
        value, leaf = changes[target["path"]], segments[-1]
        if target["mode"] == "claim":
            parent[leaf:leaf+1] = value
            original = target["current_value"]
            if isinstance(original, dict):
                spans = [s for s in original.get("evidence") or [] if isinstance(s, str) and s.strip()
                         and " ".join(s.split()) in " ".join(user_text.split())]
                if spans:
                    retained = {"path": target["path"], "evidence": spans, "status": "preserved_in_request"}
                    old_target = original.get("target")
                    if isinstance(old_target, dict) and isinstance(old_target.get("values"), list):
                        unresolved = [v for v in old_target["values"] if not explicit_claim_anchors(
                            {**original, "target": {**old_target, "values": [v]}}, user_text, plc_model)]
                        if unresolved:
                            retained.update(unresolved_values=copy.deepcopy(unresolved), projection_status="not_projected")
                    receipt["retained_text"].append(retained)
        elif target["mode"] == "decision_citation":
            parent[leaf].update(value)
        elif target["mode"] == "requirement_links":
            rows = result.setdefault("implementation_items", [])
            by_id = {row["id"]: row for row in rows}
            for item in target["current_value"]:
                identity = item["id"]
                if identity not in value:
                    continue
                row = by_id.get(identity)
                if row is None:
                    row = copy.deepcopy(item)
                    rows.append(row)
                existing = item.get("requirement_ids")
                row["requirement_ids"] = [*(existing if isinstance(existing, list) else []), *value[identity]]
        elif target["mode"] == "remove" or (isinstance(leaf, int) and value is None and segments[0] not in PLAN_ROW_SCHEMAS):
            if isinstance(parent, list):
                parent.pop(leaf)
            else:
                parent.pop(leaf, None)
        else:
            parent[leaf] = value
        receipt["model_paths"].append(target["path"])
    return result, receipt
