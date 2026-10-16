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
            + SOURCE_PRECEDENCE
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


_DIRECT_PROTOCOL = """# Direct one-call generation
直接实现完整原始需求，不追加分析、方案确认或切换 Design。原文/用户事实是工艺依据，型号资料是技术依据。
你负责内部软元件、指令、时基和数据结构的选择，按已知参数推导所需容量，不要求用户批准这些实现选择。不猜关键现场参数、电平或新增现场 I/O。
只返回一个外层对象 {"result":...}，result 内放程序对象或追问对象，二者不能混合。
信息充分时直接实现并返回 {"result":{"r":[...]}}；仅缺少无法从已知事实推导、且会改变已要求外部行为的现场事实时返回
{"result":{"status":"needs_input","missing_info":[{"id":"稳定问题标识","question":"简短具体问题","required":true,"options":[],"default":""}]}}。
首次追问前完整检查当前需求，一次列出所有能识别的必要现场缺参，不要问到一项就结束，也不要分轮保留已知要问的问题。按需求涉及的输入电平、事件识别、时序、并发及停止/恢复行为核对；这些是核对维度，不是必须提问的清单。已有答案、可推导的内部实现和未要求的工况不列入问题。
每个 missing_info 项只问一个可独立回答的事实，使用稳定且唯一的 id；相关但可分别回答的事实分项列出，可提供简短 options，不把默认值当作用户答案。
不追问已知信息，不为未要求的异常工况、额外功能或理论采样问题追加确认；明确的输入歧义或需求矛盾仍须处理。续答时逐项对照问题 id 与用户回答，累计保留已答事实；答复充分后直接生成，不换一种说法重复确认。仅对未回答或确实无法理解的必要事实、用户新答复引入的实质矛盾再次提问，并说明具体缺口；不能以逐步设计为由继续零散追问。模型问题和候选选项本身不是用户声明。
选定可行方案后实现并核对正确性与协议，只输出一份最终程序；真实缺参则汇总必要问题并结束，不混入程序或反复权衡是否提问。
""" + CYCLIC_ASSIGNMENT_PROMPT + """
NO=位1，NC=位0；COIL 每扫描写导通真假，同一普通 Y/M 一个 COIL，输入 OR 放同一输出路径。
T/C 用 TIMER/COUNTER，定时器使能须能复位；事件用边沿，持续条件用电平。
""" + compact_protocol_prompt()


def direct_response_schema():
    """Strict output has an object root; the choice is a nested result value."""
    question = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "question": {"type": "string", "minLength": 1},
            "required": {"type": "boolean"},
            "options": {"type": "array", "items": {"type": "string"}},
            "default": {"type": "string"},
        },
        "required": ["id", "question", "required", "options", "default"],
    }
    clarification = {
        "type": "object", "required": ["status", "missing_info"], "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["needs_input"]},
            "missing_info": {"type": "array", "minItems": 1, "items": question},
        },
    }
    return {
        "type": "object", "required": ["result"], "additionalProperties": False,
        "properties": {"result": {"anyOf": [_compact_response_schema(), clarification]}},
    }


def generate_direct_ladder(user_requirement, plc_model="FX3U", *, confirmed_spec=None,
                           model_name=None, on_stage=None, on_context=None, on_candidate=None,
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
        "result.missing_info.*.question", "missing_info.*.question"))
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
    if prepared["changes"]:
        handoff["representation_normalization"] = copy.deepcopy(prepared["changes"])
        diagnostics.emit("compact_normalized", stage="compact_protocol", protocol=PROTOCOL_VERSION,
                         changes=prepared["changes"])
        if on_stage:
            on_stage("compact_normalized", "已在本地规范化格式；未增加模型请求")
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
