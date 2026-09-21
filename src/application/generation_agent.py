"""Agent B: translate a confirmed specification into one ladder candidate.

Agent A owns requirement analysis and confirmation. Agent B deliberately receives only
the confirmed engineering projection. To keep generation latency proportional to the
actual control logic, the model emits a compact ladder plan; deterministic local code
expands that plan into the existing ``ladder_v1`` representation before the ordinary
validators, IR builder and persistence path see it.
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

from plc.device_identity import canonical_ladder_devices
from model_runtime.provider import TextDelta
from application.generation_context import _build_knowledge_context
from shared.context_policy import audit_section
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
- io_bindings.active_level=0 表示位为0时信号动作，不是程序触点类型。程序 NO 检查位=1，NC 检查位=0；具体 active_when 与 run_permit_when 使用下方由绑定派生的谓词。停止/联锁须在输出路径实际生效。
- 不新增未确认的 X/Y、停止/急停、硬件或模块寄存器。内部状态使用普通 M/D/T/C；已确认语义需要的内部特殊软元件以当前型号资料/手册证据为准，已有显式禁用仍须遵守。
- 同一普通 Y/M 只有一个 COIL owner，多条件并入该输出的输入结构；不把输入 OR 拆成多个输出 branch。
- 比较输入只比较；算术先用应用指令写入寄存器。
- TIMER 使用 T，COUNTER 使用 C；普通定时器须有可变为 FALSE 的使能/复位路径。
- 一次事件使用 P/F 边沿，持续条件使用电平；两者不互换。
""" + compact_protocol_prompt()


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
        from plc.validation import validate_ladder_candidate_structure
        converted = expand_hybrid_compact_ladder(value, projected)
        ladder = converted if converted is not None else copy.deepcopy(value)
        # Choosing a known representation is not accepting an unchecked program.
        validate_ladder_candidate_structure(ladder, plc_model=plc_model,
                                            require_catalogued_instructions=True)
        return ladder, "compact_alias_ladder" if converted is not None else "ladder_v1"
    raise CompactProtocolError("unknown or ambiguous ladder representation")


def _build_agent_b_prompt(projected, plc_model, *, context=None):
    context = context or build_confirmed_generation_context(
        projected, plc_model, knowledge_builder=_build_knowledge_context,
    )
    model = context.plc_model
    evidence = context.knowledge_context
    confirmed = json.dumps(context.confirmed_spec, ensure_ascii=False, separators=(",", ":"))
    from plc.specification.provenance import SOURCE_PRECEDENCE
    prompt = (
        _COMPACT_PROTOCOL + SOURCE_PRECEDENCE
        + f"\n# Selected PLC\n{model}\n"
        + "\n# Confirmed project specification\n"
        + confirmed
        + compact_capability_prompt(model, context.confirmed_spec)
        + evidence
        + generation_execution_prompt(context.confirmed_spec, evidence_text=evidence)
    )
    audit_section("system_prompt", prompt, reason="confirmed_spec_compact_generation", source="application")
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
):
    """Make one streaming model call, then locally expand the compact plan."""
    import application.model_api as api

    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")

    base_provider = api.current_provider()
    context = build_confirmed_generation_context(
        projected, model, knowledge_builder=_build_knowledge_context,
        model_profile=getattr(base_provider, "profile", {}),
        decision_receipt_id=decision_receipt_id,
    )
    if on_context:
        on_context(context.to_dict()["handoff"])
    system_prompt = _build_agent_b_prompt(projected, model, context=context)
    if on_stage:
        on_stage("confirmed_spec_generation", "正在根据已确认规格生成梯形图")

    provider = _FirstJSONObjectProvider(base_provider)
    from model_runtime.response_format import response_plan
    options, streaming = response_plan(
        getattr(base_provider, "profile", {}), _compact_response_schema(),
        model=model_name, api_key=getattr(base_provider, "api_key", None),
        hints=None,
    )
    with api.provider_scope(provider, model_name=model_name):
        response = api.request_model(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _GENERATION_REQUEST},
            ],
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
    ladder, representation = _decode_generated_ladder(compact, projected, model)
    diagnostics.emit("generation_representation", stage="compact_protocol", representation=representation)
    ladder = canonical_ladder_devices(ladder)
    from plc.specification.checks import check_direct_self_hold
    behavior = check_direct_self_hold(ladder, projected)
    diagnostics.emit("confirmed_primitive_check", stage="generation_validation", **behavior)
    return {"ladder": ladder, "model_calls": 1, "generation_handoff": context.to_dict()["handoff"]}
