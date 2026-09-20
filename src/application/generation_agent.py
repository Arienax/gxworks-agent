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
你只负责把已确认规格翻译成紧凑梯级计划；不要重新设计需求。

语义规则：
- 用户当前确认的地址、触点极性和参数必须保持；遵守 selected_approach.generation_contract 的结构化约束，不把 unverified_constraints 升级为额外硬约束。
- 不得擅自新增 X/Y、停止/急停、硬件或模块寄存器；内部状态优先使用普通 M/D/T/C 低位地址。
- 实现已确认语义所需的内部特殊软元件，可按当前型号资料/手册证据选用；这不等于新增外部 I/O。已有显式禁用仍须遵守，不得拿其他型号或猜测替代证据。
- 同一普通 Y/M 只保留一个 COIL owner；多个触发条件必须合并到该输出的同一个条件结构。
- 输入 OR 使用 `{"or":[[...],[...]]}` 放在同一 branch 的 `i` 中，不得用多个输出 branch 表达同一输出的 OR。
- OR 的每个分支只能包含简单输入，不允许 OR 嵌套。
- 比较输入直接写前缀表达式，例如 `">= D0 K1"`、`"< D0 K4"`；算术先用应用指令写入寄存器，再比较。
- TIMER 只能写 T，COUNTER 只能写 C；普通定时器必须有可变为 FALSE 的使能/复位路径。
- “每次/按下时只执行一次”使用 P/F/RISING/FALLING 边沿，不用持续电平重复触发。

返回形状只有：`{"r":[rung,...]}`。
- rung: `{"h":可选简单输入或null,"s":可选简单输入数组,"b":[branch,...]}`；通常只需要 `b`。
- branch: `{"i":可选输入数组,"o":[输出字符串,...]}`；无条件时可省略 `i`。
- 未使用的 s/i 数组请写 []，不要写 null；h 没有首触点时可以写 null。
- io_bindings 是已确认的用途、地址和输入有效电平绑定；active_level=0 表示输入位为0时该信号动作，active_level=1 表示输入位为1时动作，不是程序触点的类型。
- 物理常闭不等于程序 NC：程序 NO 检查位=1，NC 检查位=0。停止信号动作时必须切断输出；若停止 active_level=0，则运行允许条件检查位=1（NO），反之检查位=0（NC）。
- 不得把启动和停止合并为一个输入。已确认的停止/联锁必须在输出控制路径中实际起作用，而不是只出现在注释中。
- 简单输入：`"NO X0"`、`"NC M1"`、`"P X2"` 或比较 `"> D0 K3"`。
- i 本身是一维串联列表，例如 `"i":[{"or":[["NO X0"],["NO Y0"]]},"NO X1"]`；不得再包成 `"i":[[...]]`。
- 只有 OR 对象的 or 属性是二维数组；每个 or 子数组是一条串联支路，例如 `{"or":[["NO M20","NC M30"],["NO M21"]]}`。
- 标准输出：`"COIL Y0"`、`"PLS M0"`、`"PLF M0"`、`"TIMER T0 K10"`、`"COUNTER C0 K9"`。
- 其他输出字符串首 token 直接作为 APP_INSTR opcode，例如 `"MOV K1 D0"`、`"INC D0"`；后续 token 是 operands。
- 不要输出 branch_id、y_offset_level、rung_id、label、debug_note、device_comments；这些由本地代码确定性补齐。
- 不输出 Markdown、解释、第二份 JSON 或未定义字段。
"""




class _FirstJSONObjectStream:
    """Cut a streamed response after its first complete top-level JSON object."""

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
    """Streaming facade that prevents a second complete Agent-B JSON copy."""

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
                    text, complete = scanner.feed(event.text)
                    if text:
                        yield TextDelta(text)
                    if complete:
                        diagnostics.emit("local_json_complete", stage="response_framing", protocol=PROTOCOL_VERSION)
                        return
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
        hints={"reasoning_effort": effort} if effort is not None else {},
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
        hints={"reasoning_effort": effort} if effort is not None else {},
    )
    with api.provider_scope(provider, model_name=model_name):
        response = api.request_model(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _GENERATION_REQUEST},
            ],
            model_name=model_name,
            effort=effort,
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
