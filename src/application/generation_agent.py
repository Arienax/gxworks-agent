"""One compact ladder candidate from raw Direct input or a confirmed specification.

The optional Agent A workflow still supplies only its confirmed projection to B.
Direct retains the full raw requirement and can return necessary questions instead.
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
from shared.context_audit import audit_section
from model_runtime.responses import ResponseContract


from application.confirmed_generation_context import (
    CONFIRMED_GENERATION_REQUEST as _GENERATION_REQUEST,
    build_confirmed_generation_context, generation_execution_prompt,
    project_confirmed_specification as _strict_generation_projection,
)

_COMPACT_RESPONSE = ResponseContract("compact_ladder", "json")

_COMPACT_PROTOCOL = """# Agent B compact ladder protocol
把当前已确认规格实现为一个紧凑梯级计划。

- 当前确认的 I/O、参数、输入有效电平和 selected_approach.generation_contract 是实现依据；unverified_constraints 不升级为额外硬约束。
- selected_approach.generation_contract 或 implementation_preferences 中的 instruction_instances 是已选实现的完整应用指令调用；opcode 与 operands 必须逐项原样使用，不得省略、替换、重排或自行改写。
- io_bindings.active_level=0 表示位为0时信号动作，不是程序触点类型。程序 NO 检查位=1，NC 检查位=0；具体 active_when 与 run_permit_when 使用下方由绑定派生的谓词。停止/联锁须在输出路径实际生效。
- 不新增未确认的 X/Y、停止/急停、硬件或模块寄存器。内部状态使用普通 M/D/T/C；已确认语义需要的内部特殊软元件以当前型号资料/手册证据为准，已有显式禁用仍须遵守。
- 同一普通 Y/M 只有一个 COIL owner，多条件并入该输出的输入结构；不把输入 OR 拆成多个输出 branch。
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

        confirmed = json.dumps(
            runtime_spec, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        # Prefix caches reuse tokens before the first differing token. Keep
        # shared policy and unchanged technical evidence before project values;
        # two specs with the same evidence then share that whole prefix. This
        # changes only rendering order, not authority, selection or budgeting.
        system_prompt = (
            (_DIRECT_PROTOCOL if direct else _COMPACT_PROTOCOL)
            + SOURCE_PRECEDENCE
            + f"\n# Selected PLC\n{model}\n"
            + compact_capability_prompt(model, runtime_spec)
            + str(evidence_text or "")
            + examples.text
            + ("\n# User facts (no generated confirmation specification)\n" if direct else "\n# Confirmed project specification\n")
            + confirmed
            + operation_intent_prompt(runtime_spec, target_model=model)
            + construction_prompt({**runtime_spec, 'plc_model':model}, delivery=_construction_delivery())
            + render_context_checkpoint(context_checkpoint)
            + generation_execution_prompt(
                runtime_spec,
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


_DIRECT_PROTOCOL = """# Direct one-call generation
根据用户完整原始需求直接实现可检查的程序，不执行独立需求分析，不输出方案摘要或确认表，也不自动切换 Design。
当前用户明确的事实和原文为工艺依据，型号资料为技术依据。实现可自行选择内部 M/D/T/C 地址；不得默默猜测缺失的关键工艺参数、输入电平或新增现场 I/O。
信息充分时只返回下方既有 compact 梯形图 JSON。真实缺参且影响正确实现时，只返回
{"status":"needs_input","missing_info":[{"id":"稳定问题标识","question":"简短具体问题","required":true,"options":[],"default":""}]}。
不问已在原文或用户事实中提供的内容；不让用户确认内部地址、算法或模型整理的规格。缺参回复不得混入梯级、程序或 CSV。
续答与原文同为用户输入；解决后直接生成。只输出一个最终 JSON，完成后结束，不追加自检重写。
NO 读取位=1，NC 读取位=0，信号有效电平与触点类型不同。停止/故障须按需求在同扫描优先；OR 放在同一输出路径。
同一普通 Y/M 只保留一个 COIL owner。T/C 分别使用 TIMER/COUNTER，定时器使能须能复位；事件与持续条件按需求区分。
""" + compact_protocol_prompt()


def generate_direct_ladder(user_requirement, plc_model="FX3U", *, confirmed_spec=None,
                           model_name=None, on_stage=None, on_context=None,
                           construction_examples=None, image_attachments=(), user_fact_text=None):
    """One request yields either a compact candidate or minimal questions."""
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
        on_stage("direct_generation", "正在从原始需求一次生成候选；真实缺参时只提出必要问题")
    schema = {"anyOf": [_compact_response_schema(), {
        "type": "object", "required": ["status", "missing_info"], "additionalProperties": False,
        "properties": {"status": {"const": "needs_input"}, "missing_info": {
            "type": "array", "minItems": 1, "items": {"type": "object",
                "required": ["id", "question"], "properties": {
                    "id": {"type": "string"}, "question": {"type": "string"},
                    "required": {"type": "boolean"}, "options": {"type": "array", "items": {"type": "string"}},
                    "default": {"type": "string"},
                }},
        }},
    }]}
    options, streaming = response_plan(getattr(base_provider, "profile", {}), schema,
        model=model_name, api_key=getattr(base_provider, "api_key", None), hints=None)
    messages = copy.deepcopy(context.wire_packet["messages"])
    if image_attachments:
        from model_runtime.provider import UserMessage
        messages[-1] = UserMessage(user_requirement, tuple(image_attachments))
    contract = ResponseContract("direct_generation", "json", ("missing_info.*.question",))
    with api.provider_scope(_FirstJSONObjectProvider(base_provider), model_name=model_name):
        response = api.request_model(messages, model_name=model_name, effort=None,
            stream=streaming, max_retries=0, options=options, response_contract=contract)
    value = _json_object(response.message.content)
    if value.get("status") == "needs_input":
        from plc.specification.confirmed import normalize_missing_info
        questions = value.get("missing_info")
        if (set(value) != {"status", "missing_info"} or not isinstance(questions, list)
                or not questions or any(not isinstance(q, dict) or not isinstance(q.get("id"), str)
                    or not q["id"].strip() or not isinstance(q.get("question"), str)
                    or not q["question"].strip() for q in questions)
                or len({q["id"] for q in questions}) != len(questions)):
            raise CompactProtocolError("needs_input 必须只含非空且标识唯一的 missing_info 问题；不得包含程序。")
        return {"status": "needs_input", "missing_info": normalize_missing_info(questions),
                "model_calls": 1, "generation_handoff": handoff}
    prepared = prepare_model_candidate(value, context.confirmed_spec, model)
    return {**prepared, "model_calls": 1, "generation_handoff": handoff,
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
    if isinstance(value, dict) and set(value) == {"r"}:
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
