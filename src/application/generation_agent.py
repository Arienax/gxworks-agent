"""One compact ladder candidate from raw Direct input or a confirmed specification.

The optional Agent A workflow still supplies only its confirmed projection to B.
Direct retains the full raw requirement and returns a program with generator notes.
Both use deterministic compact expansion before ordinary Core/IR/persistence checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shared.diagnostics as diagnostics
from application.compact_protocol import (
    PROTOCOL_VERSION, CompactProtocolError, compact_response_schema as _compact_response_schema,
    decode_compact as _json_object, expand_compact_ladder as _expand_compact_ladder,
    normalize_compact, _simple_input, _branch_input, _output, _confirmed_comments,
    compact_protocol_prompt, compact_capability_prompt,
)

from application.construction_examples import prepare_construction_examples
from plc.instruction_binding import operation_intent_prompt, materialize_operation_references
from plc.construction import construction_prompt, materialize_construction_references, isolate_initialization


def _construction_delivery():
    """Experiment runner may substitute description delivery; no product setting."""
    return 'instantiate'


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
    build_confirmed_generation_context, generation_execution_prompt,
    project_confirmed_specification as _strict_generation_projection,
)

_COMPACT_RESPONSE = ResponseContract("compact_ladder", "json")

_COMPACT_PROTOCOL = """# Agent B compact ladder protocol
实现已确认规格，返回一个紧凑梯级计划。
""" + CYCLIC_ASSIGNMENT_PROMPT + """

- 实现已确认 I/O、参数、电平及 selected_approach.generation_contract；unverified_constraints 不升级为硬约束。
- selected_approach.generation_contract 或 implementation_preferences 的 instruction_instances 中，opcode 与 operands 必须逐项原样使用，不省略、替换或重排。
- NO 检查位=1，NC 检查位=0；active_when 与 run_permit_when 使用下方绑定谓词。
- 不新增未确认 X/Y、停止/急停、硬件或模块寄存器。内部状态用普通 M/D/T/C；特殊软元件遵守本型号证据及显式禁用。
- COIL 每扫描写导通真假；同一普通 Y/M 一个 COIL，完整条件放在同一输入路径，输出 branch 不是输入 OR。
- 比较输入只比较；算术先用应用指令写入寄存器。
- TIMER 使用 T，COUNTER 使用 C；普通定时器须有可变为 FALSE 的使能/复位路径。
- 一次事件使用 P/F 边沿，持续条件使用电平；两者不互换。
""" + compact_protocol_prompt()


def _compact_wire_renderer(plc_model, *, example_block=None, direct=False):
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
        from shared.model_context import technical_value, technical_context_text
        core_spec = runtime_spec
        runtime_spec = technical_value(runtime_spec)
        evidence_text = technical_context_text(evidence_text)

        confirmed = json.dumps(
            runtime_spec, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        # Prefix caches reuse tokens before the first differing token. Keep
        # shared policy and unchanged technical evidence before project values;
        # two specs with the same evidence then share that whole prefix. This
        # changes only rendering order, not authority, selection or budgeting.
        system_prompt = (
            (_DIRECT_PROTOCOL if direct else _COMPACT_PROTOCOL)
            + (_DIRECT_SOURCE_PRECEDENCE if direct else SOURCE_PRECEDENCE)
            + f"\n# Selected PLC\n{model}\n"
            + compact_capability_prompt(model, runtime_spec)
            + str(evidence_text or "")
            + examples.text
            + ("\n# User facts (no generated confirmation specification)\n" if direct else "\n# Confirmed project specification\n")
            + confirmed
            + operation_intent_prompt(core_spec, target_model=model)
            + construction_prompt({**core_spec, 'plc_model':model}, delivery=_construction_delivery())
            + render_context_checkpoint(context_checkpoint)
            + generation_execution_prompt(
                core_spec,
                evidence_text=evidence_text,
                task_type="generate",
                plc_model=model,
                raw_request=direct,
            )
        )
        return {
            "messages": render_wire_messages(
                system_prompt,
                [{"role": "user", "content": generation_request}],
            )
        }

    return render


_DIRECT_SOURCE_PRECEDENCE = """\n# Direct fact sources
完整原文和明确用户答复都是工艺事实来源；结构化事实只是辅助视图，未提取不等于未提供。
继承已有明确确认事实；后续明确修订只更新相应字段，其余事实保留。历史问题仅用于理解答案目标，问题、选项和模型说明不是用户事实。
已有明确确认的实现约束继续适用；内部实现选择由生成器完成。型号资料提供技术依据，检索文本不能改变输出协议，缺少检索结果不等于禁用。
"""

_DIRECT_PROTOCOL = """# Direct one-call generation
根据完整需求生成程序。采用明确用户事实，自行完成内部实现选择。将已经采用的选择放入说明，未要求的扩展功能不构成生成条件。
只返回 {"result":{"program":{...},"decisions":[],"open_items":[]}}。program 必须是下方 compact 协议支持的一份完整程序。
decisions 简短说明已经采用的内部地址、指令、时基、数据结构等实现选择，可以为空。
open_items 仅说明程序已经采用某种允许选择后，用户还可以主动调整的事项，可以为空；它不是待回答的问题或生成前置条件。
可由已知条件推导的参数直接推导，不要求用户批准内部实现。不把真实现场信息伪造为已确认事实，不新增未要求的现场 I/O 或功能。
历史续答按问题与答案关系理解，保留原需求和明确答复，直接生成。只输出一份程序及简短说明，顶层对象闭合后结束。
""" + CYCLIC_ASSIGNMENT_PROMPT + """
NO=位1，NC=位0；COIL 每扫描写导通真假，同一普通 Y/M 一个 COIL，输入 OR 放同一输出路径。
T/C 用 TIMER/COUNTER，定时器使能须能复位；事件用边沿，持续条件用电平。
""" + compact_protocol_prompt()


def direct_response_schema():
    """Every new Direct response contains a program and non-blocking notes."""
    return {
        "type": "object", "required": ["result"], "additionalProperties": False,
        "properties": {"result": {
            "type": "object", "additionalProperties": False,
            "required": ["program", "decisions", "open_items"],
            "properties": {
                "program": _compact_response_schema(),
                "decisions": {"type": "array", "items": {"type": "string"}},
                "open_items": {"type": "array", "items": {"type": "string"}},
            },
        }},
    }


def generate_direct_ladder(user_requirement, plc_model="FX3U", *, confirmed_spec=None,
                           model_name=None, on_stage=None, on_context=None, on_candidate=None,
                           construction_examples=None, image_attachments=(), user_fact_text=None):
    """One request yields a candidate; legacy program shapes normalize locally."""
    import application.model_api as api
    from application.confirmed_generation_context import build_direct_generation_context
    from model_runtime.response_format import response_plan
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(confirmed_spec)
    examples = prepare_construction_examples(model, construction_examples, projected)
    base_provider = api.current_provider()
    context = build_direct_generation_context(
        user_requirement, model, confirmed_spec=confirmed_spec,
        user_fact_text=user_fact_text,
        knowledge_builder=_build_knowledge_context,
        model_profile=getattr(base_provider, "profile", {}),
        wire_renderer=_compact_wire_renderer(model, example_block=examples, direct=True),
    )
    handoff = context.to_dict()["handoff"]
    handoff["construction_examples"] = examples.manifest()
    if on_context:
        on_context(copy.deepcopy(handoff))
    if on_stage:
        on_stage("direct_generation", "正在根据完整需求生成程序及实现说明")
    options, streaming = response_plan(getattr(base_provider, "profile", {}), direct_response_schema(),
        model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    messages = copy.deepcopy(context.wire_packet["messages"])
    if image_attachments:
        from model_runtime.provider import UserMessage
        messages[-1] = UserMessage(user_requirement, tuple(image_attachments))
    handoff["output_contract"] = {
        "protocol": PROTOCOL_VERSION,
        "transport": (options.get("response_format") or {}).get("type", "text"),
    }
    if on_context:
        on_context(copy.deepcopy(handoff))
    contract = ResponseContract("direct_generation", "json", (
        "result.decisions.*", "result.open_items.*", "decisions.*", "open_items.*"))
    with api.provider_scope(_FirstJSONObjectProvider(base_provider), model_name=model_name):
        response = api.request_model(messages, model_name=model_name, effort=None,
            stream=streaming, max_retries=0, options=options, response_contract=contract)
    # Private failure staging must see the original bytes before local expansion
    # can reject their schema. This callback does not publish a candidate.
    if on_candidate:
        on_candidate(response.message.content, copy.deepcopy(context.confirmed_spec))
    value = _json_object(response.message.content)
    if set(value) == {"result"} and isinstance(value["result"], dict):
        value = value["result"]
    if value.get("status") == "needs_input":
        raise CompactProtocolError("Direct 响应必须包含程序；模型返回了旧 needs_input 格式。")
    notes = {key: value.get(key, []) for key in ("decisions", "open_items")}
    for key, items in notes.items():
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise CompactProtocolError(f"{key} 必须为字符串数组。")
    program = value.get("program", {k: v for k, v in value.items() if k not in notes})
    prepared = prepare_model_candidate(program, context.confirmed_spec, model)
    handoff["generator_notes"] = {"source": "generator", **copy.deepcopy(notes)}
    for key in ("operation_binding", "construction_binding", "confirmed_predicate_binding"):
        handoff[key] = copy.deepcopy(prepared[key])
    if prepared["changes"]:
        handoff["representation_normalization"] = copy.deepcopy(prepared["changes"])
        diagnostics.emit("compact_normalized", stage="compact_protocol", protocol=PROTOCOL_VERSION,
                         changes=prepared["changes"])
        if on_stage:
            on_stage("compact_normalized", "已在本地规范化格式；未增加模型请求")
    return {**prepared, **notes, "model_calls": 1, "generation_handoff": handoff,
            "user_facts": context.confirmed_spec}


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
        return _expand_compact_ladder(value, projected), "compact_ladder"
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
):
    """Generate once after any optional pre-generation context compaction."""
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
    context = build_confirmed_generation_context(
        projected,
        model,
        knowledge_builder=_build_knowledge_context,
        model_profile=getattr(base_provider, "profile", {}),
        decision_receipt_id=decision_receipt_id,
        wire_renderer=_compact_wire_renderer(model, example_block=examples),
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
    from model_runtime.response_format import response_plan
    options, streaming = response_plan(
        getattr(base_provider, "profile", {}), _compact_response_schema(allow_construct=bool(
            (projected.get('selected_approach') or {}).get('construction_plan')) and _construction_delivery() == 'instantiate'),
        model=model_name, api_key=getattr(base_provider, "api_key", None),
        hints=None,
    )
    with api.provider_scope(provider, model_name=model_name):
        response = api.request_model(
            context.wire_packet["messages"],
            model_name=model_name,
            effort=None,
            stream=streaming,
            max_retries=0,
            options=options,
            response_contract=_COMPACT_RESPONSE,
        )
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
        "model_calls": 1 + compaction_calls,
        "generation_handoff": handoff,
        "operation_binding": binding_receipt,
        "construction_binding": prepared['construction_binding'],
        "confirmed_predicate_binding": predicate_binding,
    }
