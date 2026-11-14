# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""One compact ladder candidate from raw Direct input or a confirmed specification.

The optional Agent A workflow still supplies only its confirmed projection to B.
Direct retains the full raw requirement and returns a program with generator notes.
Both use deterministic compact expansion before ordinary Core/IR/persistence checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

from jsonschema import Draft202012Validator

import shared.diagnostics as diagnostics
from application.compact_protocol import (
    PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION, CompactProtocolError,
    compact_response_schema as _compact_response_schema,
    decode_compact as _json_object, expand_compact_ladder as _expand_compact_ladder,
    normalize_compact, _simple_input, _branch_input, _output, _confirmed_comments,
    compact_protocol_prompt, compact_capability_prompt, compact_generation_prompt,
)

from application.construction_examples import prepare_construction_examples
from application.ladder_instruction_tool import INSTRUCTION_LOOKUP_PROMPT, INSTRUCTION_LOOKUP_TOOL, instruction_tool_for
from application.ladder_model_session import LadderModelSession, TOOL_PROMPT, ladder_tools
from application.prompts import DECISION_BASIS_PROMPT, IMPLEMENTATION_COMPARISON_PROMPT
from plc.instruction_binding import operation_intent_prompt, materialize_operation_references
from plc.construction import construction_prompt, materialize_construction_references, isolate_initialization
from plc.specification.approach import INSTRUCTION_CHOICES_SCHEMA, normalize_instruction_choices
from plc.specification.decision_basis import DECISION_BASIS_SCHEMA, DecisionSources, review_decision_basis
from shared.i18n import tr


_INSTRUCTION_CHOICES_VALIDATOR = Draft202012Validator(INSTRUCTION_CHOICES_SCHEMA)


def _construction_delivery():
    """Experiment runner may substitute description delivery; no product setting."""
    return 'instantiate'


def direct_response_parts(value, *, decision_sources=None):
    """Unwrap the supported Direct envelope and validate generator notes."""
    if set(value) == {"result"} and isinstance(value["result"], dict):
        value = value["result"]
    if value.get("status") == "needs_input":
        raise CompactProtocolError("Direct 响应必须包含程序；模型返回了旧 needs_input 格式。")
    notes = {key: value.get(key, []) for key in ("decisions", "open_items")}
    selection = notes["decisions"]
    if isinstance(selection, dict):
        candidates = selection.get("candidates")
        selected = selection.get("selected")
        rationale = selection.get("rationale")
        if (set(selection) != {"candidates", "selected", "rationale"}
                or not isinstance(candidates, list) or type(selected) is not int
                or not 0 <= selected < len(candidates)
                or not isinstance(rationale, str) or not rationale.strip()):
            raise CompactProtocolError("Direct decisions 缺少候选、有效的候选索引或选型理由。")
        reviewed_candidates, basis_issues = [], []
        for index, candidate in enumerate(candidates):
            if (not isinstance(candidate, dict) or set(candidate) - {"method", "tradeoff", "instruction_choices", "decision_basis"}
                    or any(not isinstance(candidate.get(key), str) or not candidate[key].strip()
                           for key in ("method", "tradeoff"))):
                raise CompactProtocolError("Direct 候选须提供结构名称及具体取舍。")
            if ("instruction_choices" in candidate
                    and not _INSTRUCTION_CHOICES_VALIDATOR.is_valid(candidate["instruction_choices"])):
                raise CompactProtocolError("Direct 候选的 instruction_choices 必须为指令名称和职责的列表。")
            reviewed = {**candidate, "instruction_choices": normalize_instruction_choices(candidate.get("instruction_choices"))}
            if "decision_basis" in candidate:
                reviewed["decision_basis"], issues = review_decision_basis(
                    candidate["decision_basis"], path=f"$.decisions.candidates[{index}].decision_basis",
                    resolve_reference=decision_sources.resolve if decision_sources is not None else None,
                )
                basis_issues.extend(issues)
            reviewed_candidates.append(reviewed)
        notes["decisions"] = [f"{row['method']} ({row['tradeoff']})" for row in candidates]
        notes["decisions"].append(f"{candidates[selected]['method']} ({rationale})")
        if any("instruction_choices" in row or "decision_basis" in row for row in candidates):
            notes["selection"] = {**selection, "candidates": reviewed_candidates}
        if decision_sources is not None and (used := decision_sources.used_citations(reviewed_candidates)):
            notes["fact_citations"] = used
        if basis_issues:
            notes["decision_basis_issues"] = basis_issues
            notes["decisions"].append(tr("部分选型依据未通过来源或状态检查。"))
        notes["decisions"].extend(
            tr("适用条件待核对（{variant}）：{premise}", variant=row["variant"], premise=row["premise"])
            for row in reviewed_candidates[selected].get("decision_basis", [])
            if row["status"] != "known" or row["conclusion"] == "conditional"
        )
    for key in ("decisions", "open_items"):
        items = notes[key]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise CompactProtocolError(f"{key} 必须为字符串数组。")
    program = value.get("program", {k: v for k, v in value.items() if k not in notes})
    if not isinstance(program, dict):
        raise CompactProtocolError("Direct program 必须为对象。")
    return program, notes


def prepare_model_candidate(value, projected, plc_model, *, construction_delivery=None):
    """One shared first-candidate boundary for production and measurements."""
    # The engineering projection intentionally omits the CPU. The runtime owns
    # this value; construction compilation must not infer it from that projection.
    runtime_spec = {**projected, 'plc_model': plc_model}
    compact, changes = normalize_compact(value)
    compact, construction = materialize_construction_references(
        compact, runtime_spec, delivery=construction_delivery or _construction_delivery(), output_expander=_output)
    compact, binding = materialize_operation_references(compact, projected, target_model=plc_model)
    ladder, representation = _decode_generated_ladder(compact, projected, plc_model)
    if (construction_delivery or _construction_delivery()) == 'instantiate':
        ladder, construction = isolate_initialization(ladder, runtime_spec, construction)
    from plc.specification.semantic_validation import bind_confirmed_predicates
    ladder, predicates = bind_confirmed_predicates(ladder, projected, plc_model=plc_model)
    return {'ladder': ladder, 'representation': representation, 'changes': changes,
            'operation_binding': binding, 'construction_binding': construction,
            'confirmed_predicate_binding': predicates}

from model_runtime.provider import TextDelta
from application.generation_context import _build_knowledge_context
from plc.generation_contract import CYCLIC_ASSIGNMENT_PROMPT
from shared.context_audit import audit_section
from model_runtime.responses import ResponseContract


from application.confirmed_generation_context import (
    CONFIRMED_GENERATION_REQUEST as _GENERATION_REQUEST,
    acceptance_cases_prompt, build_confirmed_generation_context, generation_execution_prompt,
    project_confirmed_specification as _strict_generation_projection,
)

_COMPACT_RESPONSE = ResponseContract("compact_ladder", "json")

_COMPACT_PROTOCOL = compact_generation_prompt()


def _generation_schema(projected, *, direct=False, bindings=True):
    allow_construct = bool((projected.get('selected_approach') or {}).get('construction_plan')) and _construction_delivery() == 'instantiate'
    return (direct_response_schema(allow_construct=allow_construct, bindings=bindings) if direct else
            _compact_response_schema(allow_construct=allow_construct, bindings=bindings))


def _compact_wire_renderer(plc_model, *, example_block=None, direct=False,
                           output_schema=None, include_output_schema=True, instruction_lookup=False,
                           bindings=True, candidate_tools=False, acceptance_cases=None, answers=None,
                           structured_facts=False):
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    # Capture once; compiler remeasurement must render the same experiment arm.
    examples = example_block if example_block is not None else prepare_construction_examples(model)

    def render(
        runtime_spec, evidence_text, generation_request, _current_program,
        context_checkpoint, _wire_history,
    ):
        from application.generation_wire import (
            render_context_checkpoint, render_wire_messages,
        )
        from plc.specification.provenance import SOURCE_PRECEDENCE
        from shared.model_context import technical_value, technical_context_text, technical_knowledge_coverage
        core_spec = runtime_spec
        runtime_spec = technical_value(runtime_spec)
        coverage = technical_knowledge_coverage(evidence_text)
        evidence_text = technical_context_text(evidence_text)

        from shared.model_context import technical_context_units
        from application.compact_bindings import instruction_form_units
        from plc.instruction_knowledge import programming_system_prompt
        # Rules belong in the system message. Project facts and knowledge are
        # structured once in the user message measured by ContextCompiler.
        rules = _DIRECT_PROTOCOL if direct else _COMPACT_PROTOCOL
        if not bindings:
            rules = (_DIRECT_RULES + compact_protocol_prompt(bindings=False) if direct else
                     compact_generation_prompt(bindings=False))
        if candidate_tools:
            rules = rules.replace("按 decisions、program、open_items 的顺序返回 result。",
                                  "分别提交选型记录和程序，选型记录含 decisions 和 open_items。")
            rules = rules.replace("仅输出协议 JSON。", "将该协议对象放在程序提交工具的 program 参数中。")
            if not direct and generation_request == _GENERATION_REQUEST:
                generation_request = generation_request.split("只返回一份最终 JSON", 1)[0] + "用工具提交完整程序。"
        sources = DecisionSources(confirmed_spec=runtime_spec, answers=answers) if direct else None
        system_prompt = (
            rules
            + (INSTRUCTION_LOOKUP_PROMPT if instruction_lookup else '')
            + ('\n' + TOOL_PROMPT if candidate_tools else '')
            + '\n\n' + programming_system_prompt() + '\n\n'
            + ('' if direct else SOURCE_PRECEDENCE)
            + generation_execution_prompt(core_spec, evidence_text=evidence_text,
                task_type="generate", plc_model=model, raw_request=direct, inline_predicates=not structured_facts)
            + ('\n当前输入条件、验收用例和选型引用见 input_conditions、behavior_cases 和 decision_references。'
               if structured_facts else '')
            + ('' if structured_facts else acceptance_cases_prompt(acceptance_cases))
            + (sources.reference_prompt() if sources and not structured_facts else ""))
        knowledge = technical_context_units(evidence_text)
        payload = {
            'context_schema_version': 3, 'plc_model': model,
            'request': generation_request, 'confirmed_spec': runtime_spec,
            'knowledge': knowledge, 'knowledge_coverage': coverage,
            'capability_constraints': compact_capability_prompt(model, runtime_spec),
            'examples': examples.text,
            'operation_bindings': operation_intent_prompt(core_spec, target_model=model),
            'construction_bindings': construction_prompt(
                {**core_spec, 'plc_model': model}, delivery=_construction_delivery()),
            'historical_context': render_context_checkpoint(context_checkpoint),
        }
        if bindings:
            from application.compact_bindings import CompactBindings
            payload['input_conditions'] = CompactBindings(model, runtime_spec).conditions
            knowledge.extend(instruction_form_units(model, knowledge))
        if structured_facts:
            if acceptance_cases:
                payload['behavior_cases'] = acceptance_cases
            if sources:
                references = {}
                for row in sources.reference_entries():
                    references.setdefault(row['source'], {})[row['ref']] = row['quote']
                payload['decision_references'] = references
        if direct:
            from plc.specification.fact_citations import cite_units, fact_version
            payload['knowledge'], _ = cite_units(knowledge, model=model, language="ladder", version=fact_version())
        if include_output_schema:
            payload['output_schema'] = (copy.deepcopy(output_schema) if output_schema is not None
                                        else _generation_schema(core_spec, direct=direct, bindings=bindings))
        return {'messages': render_wire_messages(system_prompt,
            [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]),
            **({"tools": ladder_tools()} if candidate_tools else {})}

    return render


_DIRECT_RULES = f"""# Direct ladder generation
在本次任务内，先比较可行的控制结构，再生成程序。已有确认方案时只比较尚未固定的部分。
以原文和确认规格中的工艺约束筛选候选并核对生成的程序。逐项检查触发条件、生效时点、输出及状态的预期变化，以及条件在同一扫描成立时的优先级。状态何时推进、保留或清除，以工艺约束为准。
{IMPLEMENTATION_COMPARISON_PROMPT}
{DECISION_BASIS_PROMPT}
按 decisions、program、open_items 的顺序返回 result。decisions.candidates 每项含 method、tradeoff、instruction_choices、decision_basis。method 概述完整实现的状态组织、输入、推进和输出；tradeoff 写共同工艺条件下的规模、具体适用条件及取舍。instruction_choices 为 [{{"opcode":"指令或系统软元件名称","role":"在此方案中承担的操作"}}]，只列影响方案成立的选择，无适用选择时用 []。selected 为选中项从 0 开始的索引，rationale 说明选择理由。program 实现选定结构及其指令职责，只写一份完整 compact 程序。
open_items 仅说明程序已经采用某种允许选择后，用户还可以主动调整的事项，可以为空；它不是待回答的问题或生成前置条件。
可由已知条件推导的参数直接推导，不要求用户批准内部实现。不把真实现场信息伪造为已确认事实，不新增未要求的现场 I/O 或功能。
历史续答按问题与答案关系理解，保留原需求和明确答复，直接生成。只输出一份程序及简短说明，顶层对象闭合后结束。
""" + CYCLIC_ASSIGNMENT_PROMPT + """
NO=位1，NC=位0；COIL 每扫描写导通真假，同一普通 Y/M 一个 COIL，输入 OR 放同一输出路径。
T/C 用 TIMER/COUNTER，定时器使能须能复位；事件用边沿，持续条件用电平。
"""
_DIRECT_PROTOCOL = _DIRECT_RULES + compact_protocol_prompt()


def direct_response_schema(*, allow_construct=False, bindings=True):
    """Every new Direct response contains a program and non-blocking notes."""
    # The local reader accepts older decisions without applicability_conditions.
    # Strict output must declare every property required; [] means no extra conditions.
    decision_basis = copy.deepcopy(DECISION_BASIS_SCHEMA)
    decision_basis["items"]["required"] = list(decision_basis["items"]["properties"])
    return {
        "type": "object", "required": ["result"], "additionalProperties": False,
        "properties": {"result": {
            "type": "object", "additionalProperties": False,
            "required": ["decisions", "program", "open_items"],
            "properties": {
                "decisions": {"type": "object", "additionalProperties": False,
                    "required": ["candidates", "selected", "rationale"], "properties": {
                        "candidates": {"type": "array", "minItems": 1, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["method", "tradeoff", "instruction_choices", "decision_basis"], "properties": {
                                "method": {"type": "string", "minLength": 1},
                                "tradeoff": {"type": "string", "minLength": 1},
                                "instruction_choices": copy.deepcopy(INSTRUCTION_CHOICES_SCHEMA),
                                "decision_basis": decision_basis,
                            },
                        }},
                        "selected": {"type": "integer", "minimum": 0},
                        "rationale": {"type": "string", "minLength": 1},
                    }},
                "program": _compact_response_schema(allow_construct=allow_construct, bindings=bindings),
                "open_items": {"type": "array", "items": {"type": "string"}},
            },
        }},
    }


def generate_direct_ladder(user_requirement, plc_model="FX3U", *, confirmed_spec=None,
                           model_name=None, on_stage=None, on_context=None, on_candidate=None,
                           construction_examples=None, image_attachments=(), user_fact_text=None,
                           user_facts=None, answers=None, candidate_flow=None):
    """Generate a Direct candidate with optional exact instruction lookup."""
    import application.model_api as api
    from application.confirmed_generation_context import build_direct_generation_context
    from model_runtime.response_format import response_plan
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(user_facts if user_facts is not None else confirmed_spec)
    examples = prepare_construction_examples(model, construction_examples, projected)
    base_provider = api.current_provider()
    # Plain JSON keeps the original compact string protocol. Named bindings
    # belong to the tool path that can fetch missing forms in the same session.
    schema = _generation_schema(projected, direct=True, bindings=False)
    options, streaming = response_plan(getattr(base_provider, "profile", {}), schema,
        model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    lookup = instruction_tool_for(base_provider, model_name=model_name, plc_model=model, options=options,
                                  stream=streaming, on_stage=on_stage,
                                  tools=ladder_tools() if candidate_flow is not None else None)
    if lookup is not None:
        schema = _generation_schema(projected, direct=True)
        options, streaming = response_plan(getattr(base_provider, "profile", {}), schema,
            model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    context = build_direct_generation_context(
        user_requirement, model, confirmed_spec=confirmed_spec,
        user_fact_text=user_fact_text, user_facts=user_facts,
        knowledge_builder=_build_knowledge_context,
        model_profile=getattr(base_provider, "profile", {}),
        wire_renderer=_compact_wire_renderer(model, example_block=examples, direct=True,
            bindings=lookup is not None,
            output_schema=schema, include_output_schema=(options.get('response_format') or {}).get('type') != 'json_schema'
            and not (lookup and candidate_flow is not None),
            instruction_lookup=lookup is not None, candidate_tools=lookup is not None and candidate_flow is not None,
            acceptance_cases=candidate_flow.cases[0] if candidate_flow is not None else None, answers=answers),
    )
    handoff = context.to_dict()["handoff"]
    handoff["construction_examples"] = examples.manifest()
    if on_context:
        on_context(copy.deepcopy(handoff))
    if on_stage:
        on_stage("direct_generation", "正在根据完整需求生成程序及实现说明")
    messages = copy.deepcopy(context.wire_packet["messages"])
    if image_attachments:
        from model_runtime.provider import UserMessage
        messages[-1] = UserMessage(messages[-1]['content'], tuple(image_attachments))
    handoff["output_contract"] = {
        "protocol": PROTOCOL_VERSION if lookup is not None else LEGACY_PROTOCOL_VERSION,
        "transport": (options.get("response_format") or {}).get("type", "text"),
    }
    if on_context:
        on_context(copy.deepcopy(handoff))
    decision_roots = ("result.decisions", "decisions")
    decision_prose = tuple(root + path for root in decision_roots for path in (
        "", ".candidates.*.method", ".candidates.*.tradeoff",
        ".candidates.*.instruction_choices.*.role", ".candidates.*.decision_basis.*.variant",
        ".candidates.*.decision_basis.*.premise", ".rationale"))
    contract = ResponseContract("direct_generation", "json", (
        *decision_prose, "result.open_items.*", "open_items.*"), structured_paths=decision_roots)
    from shared.model_context import technical_context_units
    from shared.model_context import technical_value
    origins = {}
    knowledge = technical_context_units(context.knowledge_context, origins=origins)
    from plc.specification.fact_citations import cite_units, fact_version
    delivered = json.loads(context.wire_packet["messages"][-1]["content"])["knowledge"]
    _, citations = cite_units(delivered, model=model, language="ladder", version=fact_version(), origins=origins)

    def decision_sources(additional):
        return DecisionSources(user_requirement if user_fact_text is None else user_fact_text, technical_value(context.confirmed_spec),
            {"knowledge": json.dumps([*knowledge, *additional], ensure_ascii=False, separators=(",", ":"))},
            answers=answers, citations={**citations, **(getattr(lookup, "citations", {}) or {})})

    if candidate_flow is not None:
        candidate_flow.bind_specification(context.confirmed_spec, user_text=user_requirement if user_fact_text is None else user_fact_text)
        session = LadderModelSession(phase="direct", lookup=lookup, candidate=candidate_flow,
                                    decision_sources=decision_sources, on_stage=on_stage)
        tool_options = _session_options(session, model, knowledge)
    else:
        session = None
        tool_options = _instruction_tool_options(lookup, context.knowledge_context)
    with api.provider_scope(_FirstJSONObjectProvider(base_provider), model_name=model_name):
        response = api.request_model(messages, model_name=model_name, effort=None,
            stream=streaming, max_retries=0, options=options, response_contract=contract, **tool_options)
    if lookup and lookup.receipts:
        handoff["instruction_lookup"] = lookup.receipts
    # Private failure staging must see the original bytes before local expansion
    # can reject their schema. This callback does not publish a candidate.
    if on_candidate and response.message.content:
        on_candidate(response.message.content, copy.deepcopy(context.confirmed_spec))
    if session is not None:
        result = _session_result(session, handoff, response)
        result["user_facts"] = context.confirmed_spec
        return result
    sources = decision_sources(lookup.knowledge if lookup else [])
    payload = _json_object(response.message.content)
    program, notes = direct_response_parts(payload, decision_sources=sources)
    prepared = prepare_model_candidate(program, context.confirmed_spec, model)
    from application.conversation_model import current_model_turn
    turn = current_model_turn()
    if turn is not None:
        message = response.message
        if notes.get("decision_basis_issues"):
            # Resume from the applicable metadata, not the rejected premises.
            payload.get("result", payload)["decisions"] = notes["selection"]
            message = replace(message, content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                              _provider_fields={key: value for key, value in message._provider_fields.items()
                                                if key != "responses_items"})
        turn.accept(message, contract.name)
    handoff["generator_notes"] = {"source": "generator", **copy.deepcopy(notes)}
    for key in ("operation_binding", "construction_binding", "confirmed_predicate_binding"):
        handoff[key] = copy.deepcopy(prepared[key])
    if prepared["changes"]:
        handoff["representation_normalization"] = copy.deepcopy(prepared["changes"])
        diagnostics.emit("compact_normalized", stage="compact_protocol", protocol=PROTOCOL_VERSION,
                         changes=prepared["changes"])
        if on_stage:
            on_stage("compact_normalized", "已在本地规范化格式；未增加模型请求")
    return {**prepared, **notes, "model_calls": len(getattr(response, "raw_attempts", ())) or 1, "generation_handoff": handoff,
            "user_facts": context.confirmed_spec}


def _instruction_tool_options(lookup, knowledge_context):
    if lookup is None:
        return {}
    from shared.model_context import technical_context_units
    from application.compact_bindings import instruction_form_units
    units = technical_context_units(knowledge_context)
    lookup.remember([*units, *instruction_form_units(lookup.plc_model, units)])
    return {"tools": (INSTRUCTION_LOOKUP_TOOL,), "instruction_tool": lookup}


def _session_options(session, model, knowledge):
    from application.compact_bindings import instruction_form_units
    session.remember([*knowledge, *instruction_form_units(model, knowledge)])
    return {"tools": ladder_tools() if session.lookup else (), "ladder_session": session}


def _session_result(session, handoff, response):
    flow = session.candidate
    calls = len(getattr(response, "raw_attempts", ())) or 1
    handoff["candidate_flow"] = {**session.receipt(), "model_calls": calls}
    if session.notes:
        handoff["generator_notes"] = {"source": "generator", **copy.deepcopy(session.notes)}
    if flow.rendered is None:
        from plc.candidate_repair import GenerationValidationError
        from shared.i18n import get_language
        error = flow.error
        if error is None and session.format_raw:
            try:
                json.loads(session.format_raw)
            except json.JSONDecodeError as parse_error:
                error = parse_error
        failure = GenerationValidationError(
            [error or CompactProtocolError("model did not submit a complete program")],
            attempts=session.repairs, max_attempts=2, language=get_language(),
            stop_reason="attempt_limit" if session.repairs >= 2 else "final_validation",
        )
        failure.rejected_candidate = (session.format_raw or
            (session.original_text if flow.ladder is None else None) or flow.rejection_text())
        failure.generation_handoff = copy.deepcopy(handoff)
        raise failure
    for key in ("operation_binding", "construction_binding", "confirmed_predicate_binding"):
        if key in flow.expansion:
            handoff[key] = copy.deepcopy(flow.expansion[key])
    if flow.expansion.get("changes"):
        handoff["representation_normalization"] = copy.deepcopy(flow.expansion["changes"])
    return {**flow.expansion, "ladder": flow.ladder, **session.notes, "generation_handoff": handoff,
            "model_calls": calls}


class _FirstJSONObjectStream:
    """Frame the first complete top-level JSON object without cutting transport."""

    def __init__(self):
        self.started = False
        self.finished = False
        self.passthrough = False
        self.in_string = False
        self.escaped = False
        self.stack = []

    def feed(self, text):
        value = str(text or "")
        if not value or self.finished:
            return "", self.finished
        if self.passthrough:
            return value, False

        emitted = []
        for index, char in enumerate(value):
            emitted.append(char)
            if not self.started:
                if char.isspace():
                    continue
                if char != "{":
                    self.passthrough = True
                    emitted.extend(value[index + 1 :])
                    return "".join(emitted), False
                self.started = True
                self.stack.append("}")
                continue

            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.in_string = False
                continue

            if char == '"':
                self.in_string = True
            elif char == "{":
                self.stack.append("}")
            elif char == "[":
                self.stack.append("]")
            elif char in "}]":
                if not self.stack or char != self.stack[-1]:
                    self.passthrough = True
                    emitted.extend(value[index + 1 :])
                    return "".join(emitted), False
                self.stack.pop()
                if not self.stack:
                    self.finished = True
                    return "".join(emitted), True

        return "".join(emitted), False


class _FirstJSONObjectProvider:
    """One candidate, but preserve usage/finish events after the JSON closes."""

    def __init__(self, provider):
        self._provider = provider
        self.profile = getattr(provider, "profile", {})
        self.api_key = getattr(provider, "api_key", None)

    def stream(self, request):
        if not request.stream:
            yield from self._provider.stream(request)
            return

        scanner = _FirstJSONObjectStream()
        iterator = iter(self._provider.stream(request))
        try:
            for event in iterator:
                if isinstance(event, TextDelta) and not scanner.passthrough:
                    was_complete = scanner.finished
                    text, complete = scanner.feed(event.text)
                    if text:
                        yield TextDelta(text)
                    if complete and not was_complete:
                        diagnostics.emit("local_json_complete", stage="response_framing", protocol=PROTOCOL_VERSION)
                    # Do not close the HTTP iterator here: trailing usage and
                    # provider finish diagnostics still belong to this call.
                    # Only duplicate output text is excluded from the candidate;
                    # reasoning and non-text transport events remain observable.
                else:
                    yield event
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()


def _response_options(provider, *, model_name=None, effort=None):
    from model_runtime.response_format import response_plan
    return response_plan(
        getattr(provider, "profile", {}), _compact_response_schema(),
        model=model_name, api_key=getattr(provider, "api_key", None),
        hints=None,
    )[0]


def _decode_generated_ladder(value, projected, plc_model):
    """Accept documented representations, never guess an unknown wrapper.

    Some compatible endpoints return the existing ladder_v1 representation or
    its already-supported long-key compact alias despite the compact prompt.
    Reuse their validators/converter rather than pay for a new generation.
    """
    if isinstance(value, dict) and "r" in value and set(value) <= {"r", "device_comments"}:
        return _expand_compact_ladder(value, projected, plc_model=plc_model), "compact_ladder"
    if isinstance(value, dict) and "rungs" in value and set(value) <= {"rungs", "device_comments"}:
        from application.compact_alias import expand_hybrid_compact_ladder
        converted = expand_hybrid_compact_ladder(value, projected)
        ladder = converted if converted is not None else copy.deepcopy(value)
        return ladder, "compact_alias_ladder" if converted is not None else "ladder_v1"
    raise CompactProtocolError("unknown or ambiguous ladder representation")


def _build_agent_b_prompt(projected, plc_model, *, context=None, construction_examples=None):
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    renderer = None
    if context is None or not context.wire_packet:
        renderer = _compact_wire_renderer(
            model, example_block=prepare_construction_examples(
                model, construction_examples, projected
            ),
        )
    context = context or build_confirmed_generation_context(
        projected,
        model,
        knowledge_builder=_build_knowledge_context,
        wire_renderer=renderer,
    )
    packet = context.wire_packet
    if not packet:
        packet = renderer(
            context.confirmed_spec,
            context.knowledge_context,
            context.generation_request,
            context.current_program,
            "",
            [],
        )
    prompt = packet["messages"][0]["content"]
    audit_section(
        "system_prompt",
        prompt,
        reason="confirmed_spec_compact_generation",
        source="application",
    )
    return prompt


def generate_confirmed_ladder(
    confirmed_spec,
    plc_model="FX3U",
    *,
    model_name=None,
    effort=None,
    on_stage=None,
    on_context=None,
    decision_receipt_id=None,
    construction_examples: bool | None = None,
    project_occupied_devices=(),
    candidate_flow=None,
):
    """Generate from a confirmed specification with optional instruction lookup."""
    import application.model_api as api

    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")
    plan = (projected.get('selected_approach') or {}).get('construction_plan')
    if plan is not None and project_occupied_devices:
        plan['occupied_devices'] = sorted(set(plan.get('occupied_devices', [])) | set(project_occupied_devices))

    examples = prepare_construction_examples(
        model, construction_examples, projected
    )
    audit_section(
        "construction_examples", examples.text,
        status="included" if examples.text else "excluded",
        reason=examples.reason, source="builtin_plc_ir",
    )
    base_provider = api.current_provider()
    from model_runtime.response_format import response_plan
    schema = _generation_schema(projected, bindings=False)
    options, streaming = response_plan(getattr(base_provider, "profile", {}), schema,
        model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    lookup = instruction_tool_for(base_provider, model_name=model_name, plc_model=model, options=options,
                                  stream=streaming, on_stage=on_stage,
                                  tools=ladder_tools() if candidate_flow is not None else None)
    if lookup is not None:
        schema = _generation_schema(projected)
        options, streaming = response_plan(getattr(base_provider, "profile", {}), schema,
            model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    context = build_confirmed_generation_context(
        projected,
        model,
        knowledge_builder=_build_knowledge_context,
        model_profile=getattr(base_provider, "profile", {}),
        decision_receipt_id=decision_receipt_id,
        wire_renderer=_compact_wire_renderer(model, example_block=examples, output_schema=schema,
            bindings=lookup is not None,
            include_output_schema=(options.get('response_format') or {}).get('type') != 'json_schema'
            and not (lookup and candidate_flow is not None),
            instruction_lookup=lookup is not None, candidate_tools=lookup is not None and candidate_flow is not None,
            acceptance_cases=candidate_flow.cases[0] if candidate_flow is not None else None),
    )
    handoff = context.to_dict()["handoff"]
    handoff["construction_examples"] = examples.manifest()
    if on_context:
        on_context(copy.deepcopy(handoff))
    system_prompt = _build_agent_b_prompt(projected, model, context=context)
    if on_stage:
        if examples.requested:
            on_stage(
                "construction_examples",
                "本次生成已加入路由后的构造范例"
                if examples.text
                else "构造范例未注入：" + examples.reason,
            )
        on_stage("confirmed_spec_generation", "正在根据已确认规格生成梯形图")

    provider = _FirstJSONObjectProvider(base_provider)
    if candidate_flow is not None:
        from shared.model_context import technical_context_units
        candidate_flow.bind_specification(projected)
        session = LadderModelSession(phase="confirmed", lookup=lookup, candidate=candidate_flow, on_stage=on_stage)
        tool_options = _session_options(session, model, technical_context_units(context.knowledge_context))
    else:
        session = None
        tool_options = _instruction_tool_options(lookup, context.knowledge_context)
    with api.provider_scope(provider, model_name=model_name):
        response = api.request_model(
            context.wire_packet["messages"],
            model_name=model_name,
            effort=None,
            stream=streaming,
            max_retries=0,
            options=options,
            response_contract=_COMPACT_RESPONSE,
            **tool_options,
        )
    if lookup and lookup.receipts:
        handoff["instruction_lookup"] = lookup.receipts
    if session is not None:
        return _session_result(session, handoff, response)
    raw_digest = hashlib.sha256(response.message.content.encode("utf-8")).hexdigest()
    compact = _json_object(response.message.content)
    compact, changes = normalize_compact(compact)
    if changes:
        diagnostics.emit("compact_normalized", stage="compact_protocol", protocol=PROTOCOL_VERSION, changes=changes, raw_sha256=raw_digest,
                         canonical_sha256=hashlib.sha256(json.dumps(compact, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())
        if on_stage:
            on_stage("compact_normalized", "已在本地兼容确定性的表示差异；正在展开梯形图，未增加模型请求")
    prepared = prepare_model_candidate(compact, projected, model)
    binding_receipt = prepared['operation_binding']
    if binding_receipt["receipts"]:
        diagnostics.emit("operation_binding", stage="Core_operation_binding", receipt=binding_receipt)
        if on_stage:
            on_stage("operation_binding", "已按确认效果绑定指令参数")
    ladder, representation = prepared['ladder'], prepared['representation']
    predicate_binding = prepared['confirmed_predicate_binding']
    if predicate_binding['changes'] or predicate_binding['diagnostics']:
        diagnostics.emit('confirmed_predicate_binding', stage=predicate_binding['stage'], receipt=predicate_binding)
    diagnostics.emit("generation_representation", stage="compact_protocol", representation=representation)
    compaction = (
        context.handoff.get("budget_report", {}).get("context_compaction", {})
        if isinstance(context.handoff, dict) else {}
    )
    compaction_calls = int(compaction.get("model_calls") or 0)
    return {
        "ladder": ladder,
        "model_calls": (len(getattr(response, "raw_attempts", ())) or 1) + compaction_calls,
        "generation_handoff": handoff,
        "operation_binding": binding_receipt,
        "construction_binding": prepared['construction_binding'],
        "confirmed_predicate_binding": predicate_binding,
    }
