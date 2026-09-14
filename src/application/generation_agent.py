"""Agent B: generate one complete ladder candidate from the confirmed specification.

Requirement analysis, grilling, clarification and specification confirmation remain in
Agent A elsewhere in the application. This module deliberately has no access to the
original requirement conversation. The confirmed specification is the only engineering
interface between the two agents.
"""
from __future__ import annotations

import copy
import json

from model_provider import TextDelta
from plc_generation_context import build_generation_instructions, public_generation_specification
from plc_generation_contract import ladder_response_schema
from response_language import preserved_annotations
from workflow_response_contracts import LADDER_RESPONSE


_GENERATION_REQUEST = (
    "根据已经由用户确认的规格生成完整 ladder_v1 JSON。"
    "不得重新分析需求、提出问题或改变已确认 I/O、触点极性、参数和所选方案。"
    "输入条件的 OR 必须在同一个输出分支的 branch.inputs 中使用 parallel_block 表示；"
    "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches 来表达。"
    "shared_inputs 只放所有分支共同串联的输入，parallel_block 不得放入 shared_inputs，且不得嵌套。"
    "只返回一份最终梯形图 JSON；顶层对象闭合后立即结束回复，"
    "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON。"
)


class _FirstJSONObjectStream:
    """Cut a streamed response after its first complete top-level JSON object.

    Some OpenAI-compatible models emit a complete large JSON object and then
    immediately self-correct by emitting the whole object again. Waiting for the
    provider finish signal spends the second copy's tokens before normal response
    validation can reject the resulting ``{} {}`` payload. Agent B has a stricter
    contract: exactly one top-level object. Once that first object closes, no later
    bytes can be part of the accepted answer, so stop consuming them locally.

    If the response does not begin with an object or its delimiters become
    inconsistent, switch to pass-through mode and let the normal collector/schema
    validator produce the authoritative rejection instead of trying to repair it.
    """

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
    """Streaming-only facade that prevents a second full Agent-B JSON copy."""

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
                        return
                else:
                    yield event
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()


def _response_options(plc_model, provider):
    """Use native schema when available; otherwise keep one JSON-object request."""
    profile = getattr(provider, "profile", None)
    capabilities = profile.get("capabilities", {}) if isinstance(profile, dict) else {}
    if capabilities.get("structured_output"):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "confirmed_spec_ladder",
                    "strict": True,
                    "schema": copy.deepcopy(
                        ladder_response_schema(
                            allow_partial=False,
                            plc_model=str(plc_model or "FX3U").strip().upper() or "FX3U",
                        )
                    ),
                },
            }
        }
    return {"response_format": {"type": "json_object"}}


def _json_object(text):
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    value = json.loads(raw.strip())
    if not isinstance(value, dict):
        raise ValueError("confirmed-spec generation must return one JSON object")
    return value


def _strict_generation_projection(confirmed_spec):
    """Expose structured confirmed facts, not Agent-A implementation prose.

    ``description`` and ``generation_guide`` originate as model-authored proposal
    text. They used to become a second hidden semantic channel after the user
    confirmed the review draft, so a phrase such as "DMOV K0 increment" could
    still steer Agent B even after the corresponding hard opcode constraint was
    removed. Agent B needs the selected approach id plus its structured contract;
    prose stays on the Agent-A/review side of the boundary.
    """
    projected = public_generation_specification(confirmed_spec) or {}
    selected = projected.get("selected_approach")
    if isinstance(selected, dict):
        selected.pop("name", None)
        selected.pop("description", None)
        selected.pop("generation_guide", None)
    return projected


def generate_confirmed_ladder(
    confirmed_spec,
    plc_model="FX3U",
    *,
    model_name=None,
    effort=None,
    on_stage=None,
):
    """Make exactly one streaming model request for one complete ladder_v1 candidate."""
    import api

    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")

    system_prompt = build_generation_instructions(
        _GENERATION_REQUEST,
        plc_model=model,
        target_mode="ladder",
        is_edit_mode=False,
        task_type="generate",
        confirmed_context=projected,
        current_version_json=None,
    )
    if on_stage:
        on_stage("confirmed_spec_generation", "正在根据已确认规格一次生成完整梯形图")

    base_provider = api._workflow_provider()
    provider = _FirstJSONObjectProvider(base_provider)
    with api.provider_scope(provider, model_name=model_name):
        response = api._request_model(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _GENERATION_REQUEST},
            ],
            model_name=model_name,
            effort=effort,
            stream=True,
            max_retries=0,
            options=_response_options(model, provider),
            response_contract=LADDER_RESPONSE,
            preserved_annotations=preserved_annotations(projected),
        )
    return {"ladder": _json_object(response.message.content), "model_calls": 1}
