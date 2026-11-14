# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Tool and JSON continuations for one Ladder analysis or generation job."""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache

from jsonschema import Draft202012Validator

from agent_runtime.messages import ToolResult
from application.compact_protocol import CompactProtocolError, compact_repair_rungs, compact_response_schema, decode_compact, normalize_compact
from application.format_patch_repair import _clean, _patch_schema, apply_format_patch
from application.ladder_instruction_tool import INSTRUCTION_LOOKUP_TOOL
from plc.candidate_repair import RepairAssemblyError
from plc.specification.decision_basis import DECISION_BASIS_ROW_SCHEMA

LOOKUP = "lookup_plc_instructions"
DECISION = "record_ladder_decision"
SUBMIT = "submit_ladder_candidate"
PATCH = "patch_ladder_candidate"
MAX_REPAIRS = 2
DECISION_PATCH = {"type": "object", "additionalProperties": False, "required": ["decision_repairs"],
    "properties": {"decision_repairs": {"type": "array", "minItems": 1, "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "value"],
        "properties": {"path": {"type": "string"}, "value": {"type": "object",
            "additionalProperties": False,
            "properties": {key: DECISION_BASIS_ROW_SCHEMA["properties"][key]
                           for key in ("references", "status", "conclusion")}}}}}}}


def _tool(name, description, parameters):
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": parameters,
    }}


def _object(name, schema):
    return {"type": "object", "required": [name], "additionalProperties": False,
            "properties": {name: schema}}


def _schema_diagnostic(error):
    while error.context:
        error = max(error.context, key=lambda child: len(child.absolute_path))
    return {"path": error.json_path, "keyword": error.validator, "detail": error.message[:240]}


def _repair_context_delta(repair, previous):
    """Reuse the repair context already present in this model session."""
    if (repair.get("action") != "partial" or not previous
            or previous.get("action") != "partial"
            or repair.get("baseline_format") != "compact_ladder/1.3"
            or previous.get("baseline_format") != repair["baseline_format"]):
        return repair
    from application.conversation_model import _changes

    delta = _changes(previous["baseline_rungs"], repair["baseline_rungs"])
    short = {key: value for key, value in repair.items() if key != "baseline_rungs"}
    short["baseline_changes"] = delta
    short["instruction"] += (" 当前梯级由上一条修复反馈的 baseline_rungs 加 baseline_changes 得到；"
                             "按 JSON Pointer 先 remove 再 set，其余保留。仍返回修改后的完整梯级，不返回差量。")
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return short if size(short) < size(repair) else repair


@lru_cache(maxsize=1)
def ladder_tools():
    from application.generation_agent import direct_response_schema

    decisions = direct_response_schema()["properties"]["result"]
    decision = {**decisions, "required": ["decisions", "open_items"],
                "properties": {key: decisions["properties"][key] for key in ("decisions", "open_items")}}
    format_patch = _patch_schema("")["json_schema"]["schema"]
    format_patch["properties"]["base_sha256"] = {"type": "string", "minLength": 64, "maxLength": 64}
    partial = compact_response_schema(partial=True)
    return (
        INSTRUCTION_LOOKUP_TOOL,
        _tool(DECISION, "Direct 选型记录。提交候选、实际选型和依据，可与程序分开提交。", decision),
        _tool(SUBMIT, "提交一份 Ladder 程序供当前任务检查。编辑时提交 partial 变更。", _object("program", {
            "anyOf": [compact_response_schema(allow_construct=True), partial],
        })),
        _tool(PATCH, "按诊断提交局部梯级、JSON 语法修正或 decision_repairs 引用变更。",
              _object("patch", {"anyOf": [partial, format_patch, DECISION_PATCH]})),
    )


TOOL_PROMPT = """
# Ladder tools
分析阶段用 lookup_plc_instructions 查证缺失的原生写法，随后返回分析 JSON。
生成或修改阶段用 submit_ladder_candidate 提交程序，协议 JSON 是工具参数。Direct 同轮可用 record_ladder_decision 单独提交选型记录。
工具返回 repair 时，只按其 action 和范围补交；已经通过的程序和选型无需重复提交。
工具返回 next=submit_ladder_candidate 时，提交程序以便开始候选检查。
""".strip()


class LadderModelSession:
    def __init__(self, *, phase, lookup=None, candidate=None, decision_sources=None, on_stage=None):
        self.phase = phase
        self.lookup = lookup
        self.candidate = candidate
        self.decision_sources = decision_sources
        self.on_stage = on_stage or (lambda stage, message: None)
        self.repairs = 0
        self.lookup_batches = 0
        self.lookup_in_repair = set()
        self.notes = {}
        self.decision_issue = None
        self.decision_base = None
        self.pending_action = None
        self.format_raw = None
        self.format_origin = None
        self.original_text = None
        self.local_format_recovered = False
        self.last_repair = None
        self.done = False
        self.used_program_tool = False
        self.repair_error = None
        self.repair_diagnostic = None

    def remember(self, units):
        if self.lookup is not None:
            self.lookup.remember(units)

    def _record_decision(self, value):
        from application.generation_agent import direct_response_parts

        sources = self.decision_sources(self.lookup.knowledge if self.lookup is not None else []) if self.decision_sources else None
        try:
            _, notes = direct_response_parts(value, decision_sources=sources)
        except CompactProtocolError as error:
            self.decision_issue = {"category": "decision", "reason": "invalid_decision_record", "detail": str(error)}
            return
        if self.pending_action == "decision" and (self.decision_issue or {}).get("reason") == "invalid_decision_basis":
            from application.conversation_model import _changes
            paths = [target["path"] + "/" + field for target in self._decision_targets().values()
                     for field in target["value_schema"]["properties"]]
            if paths:
                delta = _changes(self.decision_base, value.get("result", value))
                changed = [*delta.get("remove", []), *delta.get("set", {})]
                if any(not any(path == allowed or path.startswith(allowed + "/") for allowed in paths)
                       for path in changed) or self.notes["open_items"] != notes["open_items"]:
                    self.decision_issue = {
                        **self.decision_issue, "repair_violation": "decision_repair_changed_implementation",
                    }
                    return
        self.notes = notes
        self.decision_base = copy.deepcopy(value.get("result", value))
        self.decision_issue = ({"category": "decision", "reason": "invalid_decision_basis",
                               "details": notes["decision_basis_issues"]}
                              if notes.get("decision_basis_issues") else None)
        if self.decision_issue is None and self.pending_action == "decision":
            self.pending_action = None

    def _decision_targets(self):
        from application.analysis_repair import _field_schema, _get, _pointer, _segments, citation_repair_target
        if (self.decision_issue or {}).get("reason") != "invalid_decision_basis":
            return {}
        targets = {}
        for issue in self.decision_issue["details"]:
            if issue["code"] not in {"ungrounded_decision_basis", "unconfirmed_decision_basis",
                                      "unconfirmed_applicability_condition"}:
                return {}
            segments = _segments(issue["path"])
            current = _get(self.decision_base, segments)
            if not isinstance(current, dict):
                return {}
            path = _pointer(segments)
            targets[path] = citation_repair_target(path, current, _field_schema(segments))
        return targets

    def _initial_program(self, value):
        if self.phase == "direct":
            envelope = value.get("result", value)
            if not isinstance(envelope, dict) or envelope.get("status") == "needs_input":
                raise CompactProtocolError("Direct requires a program object")
            record = {key: envelope[key] for key in ("decisions", "open_items") if key in envelope}
            if record or not (self.notes or self.decision_issue):
                self._record_decision(record)
            value = envelope.get("program", {key: item for key, item in envelope.items()
                                             if key not in {"decisions", "open_items"}})
        self._submit(value)

    def _submit(self, value, *, via_tool=False):
        self.used_program_tool = via_tool
        self.on_stage("candidate_transport", "已收到工具提交的候选程序" if via_tool else "已收到结构化 JSON 候选程序")
        self.candidate.assess(value)

    def _format_failure(self, text, origin):
        raw = _clean(text)
        if not raw.startswith("{") or len(raw) > 512000:
            return False
        try:
            json.loads(raw)
        except json.JSONDecodeError as error:
            self.format_raw = raw
            self.format_origin = origin
            self.format_issue = {"category": "format", "reason": "invalid_json_object",
                                 "line": error.lineno, "column": error.colno}
            return True
        return False

    def _apply_patch(self, value):
        if self.pending_action == "format_patch":
            if isinstance(value, dict) and value.keys() & {"r", "root", "rungs", "program", "result"}:
                raise RepairAssemblyError("$", "repair_requires_replan", kind="whole_program_in_format_repair")
            origin = self.format_origin
            text = apply_format_patch(self.format_raw, value)
            try:
                fixed = decode_compact(text)
            except CompactProtocolError:
                if self._format_failure(text, origin):
                    return
                raise
            self.format_raw = self.format_origin = None
            if origin == "submit":
                self.pending_action = None
                schema = next(row["function"]["parameters"] for row in ladder_tools()
                              if row["function"]["name"] == SUBMIT)
                self._accept_tool_program(fixed, schema)
            else:
                self._initial_program(fixed)
        elif self.pending_action == "partial":
            self.candidate.patch(value)
            self.original_text = None
        elif self.pending_action == "decision" and isinstance(value, dict) and set(value) == {"decision_repairs"}:
            from application.analysis_repair import apply_analysis_repair
            targets = self._decision_targets()
            if not targets:
                raise RepairAssemblyError("$", "repair_scope_violation")
            plan = {"base": self.decision_base, "targets": list(targets.values()),
                    "receipt": {"model_paths": []}}
            repaired, _ = apply_analysis_repair(plan, {"repairs": value["decision_repairs"]}, "")
            self.pending_action = None
            self._record_decision(repaired)
        else:
            raise RepairAssemblyError("$.rungs", "repair_scope_violation")
        self.repair_error = self.repair_diagnostic = None

    def _reject_patch(self, error=None, *, reason=None, diagnostic=None):
        self.repair_error = reason or getattr(error, "reason", "repair_shape_invalid")
        self.repair_diagnostic = {"reason": self.repair_error, "path": getattr(error, "path", "$")}
        if isinstance(error, RepairAssemblyError):
            self.repair_diagnostic.update(error.details)
        elif error is not None:
            self.repair_diagnostic["detail"] = str(error)[:240]
        if diagnostic is not None:
            self.repair_diagnostic.update(diagnostic)

    def _repair(self):
        if self.candidate is None or self.repair_error in {"repair_no_progress", "repair_requires_replan"}:
            return None
        if self.format_raw is not None:
            action = "format_patch"
            repair = {"diagnostics": [self.format_issue],
                      "base_sha256": hashlib.sha256(self.format_raw.encode("utf-8")).hexdigest(),
                      "instruction": "只替换上一条响应中的 JSON 标点或转义。patches 的 before/after 必须是唯一的短文本。"}
        elif self.candidate.issues:
            if self.candidate.repair_base is None:
                self._reject_patch(reason="repair_scope_unavailable", diagnostic={
                    "kind": "no_local_scope",
                    "detail": "候选问题无法限定到可修改的梯级，需要重新评估实现范围。",
                })
                return None
            action = "partial"
            repair = {"diagnostics": self.candidate.issues, "allowed_rung_ids": sorted(self.candidate.scope),
                      "baseline_rungs": self.candidate.repair_base["rungs"],
                      "instruction": "只替换列出的梯级。mode=partial，delete_rung_ids 和 device_comments 保持为空。"}
            if not self.candidate.repair_base.get("compact"):
                short = compact_repair_rungs(repair["baseline_rungs"])
                if short is not None:
                    repair["baseline_rungs"] = short
                    repair["baseline_format"] = "compact_ladder/1.3"
            if any(issue.get("opcode") and issue.get("reason") == "instruction_form_unresolved"
                   for issue in self.candidate.issues):
                repair["instruction"] += "缺少指令写法时先查证，依据返回的形式填写参数。"
        elif self.decision_issue:
            action, repair = "decision", {"diagnostics": [self.decision_issue]}
            if self.decision_issue.get("reason") == "invalid_decision_basis":
                if self.decision_sources:
                    sources = self.decision_sources(self.lookup.knowledge if self.lookup is not None else [])
                    repair["available_sources"] = sources.reference_prompt()
                repair["instruction"] = ("保留已提交的候选结构及选型。只修正诊断指出的依据字段。"
                                         "无法对应现有来源时保留为 unknown 和 conditional。")
                if targets := self._decision_targets():
                    repair["decision_paths"] = list(targets)
                    repair["instruction"] += ("用 patch_ladder_candidate 提交 decision_repairs，"
                        "每项只写 path 及变动的 references、status 或 conclusion。")
        else:
            return None
        repair = {"action": action, **repair}
        if self.repair_diagnostic:
            repair["rejected_patch"] = self.repair_diagnostic
        if self.repairs >= MAX_REPAIRS or repair == self.last_repair:
            return None
        presented = _repair_context_delta(repair, self.last_repair)
        self.last_repair = repair
        self.pending_action = action
        self.repairs += 1
        self.on_stage("candidate_repair", f"正在根据检查结果局部修复（{self.repairs}/{MAX_REPAIRS}）")
        return {**presented, "attempt": self.repairs, "max_attempts": MAX_REPAIRS}

    def _lookup(self, call):
        from model_runtime.provider import ModelProviderError

        allowed = self.repairs not in self.lookup_in_repair if self.repairs else self.lookup_batches < 2
        if not allowed:
            if self.candidate is None:
                raise ModelProviderError("指令查证已达本轮上限。", code="instruction_lookup_limit")
            return {"error": "instruction_lookup_limit", "message": "使用本轮已有资料完成当前提交。"}
        if self.repairs:
            self.lookup_in_repair.add(self.repairs)
        else:
            self.lookup_batches += 1
        return self.lookup(call)

    def handle_tools(self, calls):
        """Run independent arguments separately, preserving provider call/result order."""
        from model_runtime.usage import tool_executed

        schemas = {row["function"]["name"]: row["function"]["parameters"] for row in ladder_tools()}
        results = {}
        order = {name: index for index, name in enumerate((LOOKUP, DECISION, SUBMIT, PATCH))}
        handled = set()
        for call in sorted(calls, key=lambda call: order.get(call.name, len(order))):
            name = call.name
            if name in handled:
                data = {"error": "duplicate_tool_call"}
            elif name == LOOKUP and self.lookup is not None:
                data = self._lookup(call)
            elif self.candidate is None or name not in schemas or name == LOOKUP:
                data = {"error": "tool_unavailable_in_this_stage"}
            else:
                data = self._program_tool(call, schemas[name])
            handled.add(name)
            if not isinstance(data, ToolResult):
                tool_executed(name, "error" not in data)
            results[call.id] = data

        repair = None
        if self.candidate is not None:
            submission = bool({DECISION, SUBMIT, PATCH} & handled)
            if self.phase == "direct" and SUBMIT in handled and not self.notes and not self.decision_issue:
                self.decision_issue = {"category": "decision", "reason": "missing_decision_record"}
            if submission:
                repair = self._repair()
                self.done = repair is None and (
                    SUBMIT in handled or self.candidate.wire is not None or self.format_raw is not None)
                if repair is None and not self.done and DECISION in handled:
                    decision = next(call.id for call in calls if call.name == DECISION)
                    results[decision] = {**results[decision], "next": SUBMIT}
        if repair:
            # Give exactly one repair instruction even when decision and program
            # were sent in parallel. Every call still gets its own result.
            key = next(call.id for call in reversed(calls) if not isinstance(results[call.id], ToolResult))
            results[key] = {**results[key], "repair": repair}
        return tuple(value if isinstance(value := results[call.id], ToolResult) else
                     ToolResult(call.id, call.name, json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                                value, "error" in value)
                     for call in calls)

    def _program_tool(self, call, schema):
        name = call.name
        try:
            value = decode_compact(call.arguments) if isinstance(call.arguments, str) else call.arguments
        except CompactProtocolError as error:
            if name == DECISION:
                cause = error.__cause__
                self.decision_issue = {"category": "decision", "reason": "invalid_decision_record",
                                       "detail": str(cause if isinstance(cause, json.JSONDecodeError) else error)}
            elif name == SUBMIT and self.candidate.wire is None and isinstance(call.arguments, str):
                self._format_failure(call.arguments, "submit")
            elif name == PATCH:
                self._reject_patch(error)
            return {"error": "invalid_tool_arguments"}
        checked = value
        try:
            if (name == SUBMIT and self.candidate.wire is None and self.format_raw is None
                    and self.pending_action in (None, "decision")):
                return self._accept_tool_program(value, schema)
            if name == PATCH and isinstance(value, dict) and isinstance(value.get("patch"), dict):
                checked = {**value, "patch": normalize_compact(value["patch"])[0]}
        except CompactProtocolError as error:
            if name == PATCH:
                self._reject_patch(error)
            return {"error": error.reason, "diagnostics": [{"path": error.path, "detail": str(error)}]}
        if error := next(Draft202012Validator(schema).iter_errors(checked), None):
            problem = {"category": "format", "reason": "invalid_tool_arguments", **_schema_diagnostic(error)}
            if name == DECISION:
                self.decision_issue = {**problem, "category": "decision"}
            elif name == PATCH:
                self._reject_patch(reason="invalid_tool_arguments", diagnostic=problem)
            return {"error": "invalid_tool_arguments", "diagnostics": [problem]}
        if name == DECISION and self.phase == "direct":
            self._record_decision(value)
            return ({"error": "invalid_decision_record", "diagnostics": [self.decision_issue]}
                    if self.decision_issue else {"status": "accepted"})
        if name == PATCH:
            try:
                self._apply_patch(checked["patch"])
            except (ValueError, KeyError) as error:
                self._reject_patch(error)
                return {"error": self.repair_error, "diagnostics": [self.repair_diagnostic]}
        else:
            return {"error": "tool_unavailable_in_this_stage"}
        return self._program_tool_result()

    def _accept_tool_program(self, value, schema):
        program = value.get("program") if isinstance(value, dict) else None
        if isinstance(program, dict):
            normalized, _ = normalize_compact(program)
            checked = {**value, "program": normalized}
        else:
            checked = value
        error = next(Draft202012Validator(schema).iter_errors(checked), None)
        if error is not None:
            if isinstance(program, dict) and set(value) == {"program"}:
                form = compact_response_schema(partial=True) if program.get("mode") == "partial" else compact_response_schema(allow_construct=True)
                error = next(Draft202012Validator(_object("program", form)).iter_errors(checked), error)
            problem = {"category": "format", "reason": "invalid_tool_arguments", **_schema_diagnostic(error)}
            location = problem["path"].removeprefix("$.program.")
            error = CompactProtocolError(f"{location}: schema {problem['keyword']}", schema_keyword=problem["keyword"])
            self.candidate.reject_program_shape(checked.get("program") if isinstance(checked, dict) else None, error, problem)
            self.original_text = json.dumps(program if isinstance(program, dict) else value, ensure_ascii=False)
            return {"error": "invalid_tool_arguments", "diagnostics": [problem]}
        if self.phase == "edit" and program.get("mode") != "partial":
            return {"error": "existing_candidate_edit_requires_partial"}
        self._submit(program, via_tool=True)
        return self._program_tool_result()

    def _program_tool_result(self):
        if self.candidate.issues:
            return {"error": "candidate_check_failed", "diagnostics": self.candidate.issues}
        review = (self.candidate.prepared or {}).get("maintainability_review") or {}
        findings = [item for item in review.get("findings", [])
                    if item.get("code") == "selected_implementation_review"]
        rendered = self.candidate.rendered or {}
        delivered = self.candidate.delivery or {}
        behavior = self.candidate.behavior or {}
        size = delivered.get("program_size") or rendered.get("program_size")
        return {"status": "accepted",
                "checks": {"artifact_render": "passed" if rendered else "not_run",
                           "native_compile": (delivered.get("validation") or {}).get("gx_compile", "not_run"),
                           "behavior": behavior.get("status", "unverified")},
                **({"program_size": size} if size else {}),
                **({"advisories": findings} if findings else {})}

    def handle_text(self, assistant, *, invalid_json=False):
        if self.candidate is None:
            return None
        if invalid_json:
            if self.pending_action is not None:
                self._reject_patch(reason="invalid_json_object")
                return self._repair()
            from application.rejected_generation_preview import _repair_compact_json
            text, changes = _repair_compact_json(assistant.content)
            if changes:
                try:
                    value = decode_compact(text)
                except CompactProtocolError:
                    pass
                else:
                    self.local_format_recovered = True
                    self.on_stage("format_recovered", "已在本地补齐缺失引号，继续检查程序")
                    self._initial_program(value)
                    return self._repair()
            if not self._format_failure(assistant.content, "text"):
                return None
        else:
            value = decode_compact(assistant.content)
            envelope = value.get("result", value)
            program_pending = self.candidate.wire is None and isinstance(envelope, dict) and any(
                key in envelope for key in ("program", "r", "rungs"))
            if self.pending_action == "decision" and not program_pending:
                if "decision_repairs" in value:
                    try:
                        self._apply_patch(value)
                    except (ValueError, KeyError) as error:
                        self._reject_patch(error)
                else:
                    self._record_decision(value)
            elif self.pending_action not in (None, "decision"):
                try:
                    self._apply_patch(value)
                except (ValueError, KeyError) as error:
                    # The submitted edit did not fit the authorized patch.
                    # The last valid staged candidate remains the baseline.
                    self._reject_patch(error)
            else:
                self.original_text = assistant.content
                self._initial_program(value)
        return self._repair()

    def repair_schema(self, repair):
        if repair["action"] == "format_patch":
            return _patch_schema(repair["base_sha256"])["json_schema"]["schema"]
        if repair["action"] == "decision":
            if "decision_paths" in repair:
                return DECISION_PATCH
            return next(row["function"]["parameters"] for row in ladder_tools()
                        if row["function"]["name"] == DECISION)
        return compact_response_schema(partial=True)

    def receipt(self):
        return {"transport": "tools" if self.used_program_tool else "json", "repair_attempts": self.repairs,
                "diagnostics": self.candidate.issues if self.candidate is not None else [],
                **({"local_format_recovered": True} if self.local_format_recovered else {}),
                **({"local_field_repaired": True} if self.candidate is not None and self.candidate.local_field_repair else {}),
                **({"repair_error": self.repair_error} if self.repair_error else {}),
                **({"repair_diagnostic": self.repair_diagnostic} if self.repair_diagnostic else {}),
                **({"decision_diagnostic": self.decision_issue} if self.decision_issue else {})}
