# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Vendor-neutral model messages and OpenAI-compatible transports."""

from __future__ import annotations

import copy
import base64
import json
import hashlib
import inspect
import threading
import time
import shared.diagnostics as diagnostics
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Protocol, Sequence, Tuple, Union

from shared.i18n import get_language, normalize_language, response_language_instruction, translate
from model_runtime.responses import (
    ResponseContract, TEXT_RESPONSE, inspect_response, preserved_annotations,
    recover_json_object_tail,
)
from agent_runtime.messages import ToolCall, ToolResult
from model_runtime.catalog import endpoint_id
from model_runtime.contract import RESPONSE_CONTROLS, scoped_contract
from model_runtime.request_policy import resolve_request, contract_capability_available
from model_runtime.responses_wire import OUTPUT_LIMITS, encode_options, wire_response_format
from model_runtime.runtime_profile import materialize_runtime_profile
from model_runtime.legacy_migration import detection_profile
from model_runtime.transport_policy import (
    can_fallback_to_non_stream, is_explicit_stream_rejection, preferred_streaming,
    parameter_error,
)


@dataclass(frozen=True)
class SystemMessage:
    content: Any


@dataclass(frozen=True)
class ImageAttachment:
    """Vendor-neutral inline image carried only by a user message."""

    filename: str
    media_type: str
    data: bytes

    def __post_init__(self):
        media_type = str(self.media_type or "").strip().lower()
        if media_type not in {
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        }:
            raise ValueError(f"不支持的图片格式：{media_type or '未知格式'}")
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("图片附件内容为空。")
        if len(self.data) > 32 * 1024 * 1024:
            raise ValueError("单张图片不能超过 32 MiB。")
        object.__setattr__(self, "filename", str(self.filename or "图片"))
        object.__setattr__(self, "media_type", media_type)


@dataclass(frozen=True)
class UserMessage:
    content: Any
    images: Tuple[ImageAttachment, ...] = ()


@dataclass(frozen=True)
class AssistantMessage:
    content: str = ""
    tool_calls: Tuple[ToolCall, ...] = ()
    reasoning: str = ""
    # Canonical backend messages alone may carry provider-required replay.
    # This is never visible prose and is deliberately absent from dict coercion.
    _provider_reasoning: str = field(default="", repr=False)
    _provider_fields: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


ModelMessage = Union[SystemMessage, UserMessage, AssistantMessage, ToolResult]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStart:
    call_id: str
    name: str


@dataclass(frozen=True)
class ToolCallEnd:
    tool_call: ToolCall


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: Optional[int] = None
    raw_usage: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class _ProviderState:
    """Private replay state; collect_response must never publish this event."""
    fields: Mapping[str, Any] = field(repr=False)


ModelEvent = Union[TextDelta, ReasoningDelta, ToolCallStart, ToolCallEnd, Usage]


@dataclass(frozen=True)
class ResponseProgress:
    """Transport activity only, with no unaccepted text or executable calls."""
    phase: str
    received_characters: int = 0


@dataclass(frozen=True)
class ResponsePreview:
    """Optional, explicitly provisional text; never used as a tool event."""
    kind: str
    text: str = ""


_language_enforcement = ContextVar("model_language_enforcement", default=True)
_progress_observer = ContextVar("model_progress_observer", default=None)
_preview_observer = ContextVar("model_preview_observer", default=None)


@contextmanager
def response_policy_scope(*, enforce_language=True, on_progress=None, on_preview=None):
    """Select presentation policy for this task without changing wire options.

    The workbench uses language as a preference. Existing strict callers keep
    their contract; neither policy relaxes JSON or declared field types.
    """
    language_token = _language_enforcement.set(bool(enforce_language))
    progress_token = _progress_observer.set(on_progress)
    preview_token = _preview_observer.set(on_preview)
    try:
        yield
    finally:
        _preview_observer.reset(preview_token)
        _progress_observer.reset(progress_token)
        _language_enforcement.reset(language_token)


@contextmanager
def private_response_scope():
    """Keep internal protocol output out of provisional presentation callbacks."""
    token = _preview_observer.set(None)
    try:
        yield
    finally:
        _preview_observer.reset(token)


@dataclass(frozen=True)
class ModelRequest:
    messages: Tuple[ModelMessage, ...]
    model: Optional[str] = None
    tools: Tuple[Mapping[str, Any], ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = True
    timeout: Optional[float] = None
    max_retries: Optional[int] = None
    response_language: str = field(default_factory=lambda: get_language())
    response_contract: ResponseContract = TEXT_RESPONSE
    tool_response_contracts: Tuple[Tuple[str, ResponseContract], ...] = ()
    preserved_annotations: Tuple[str, ...] = ()
    enforce_response_language: bool = field(default_factory=lambda: _language_enforcement.get())
    # Backend-only synthetic verification sends no application prompt/context.
    _synthetic_probe: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "response_language", normalize_language(self.response_language))
        object.__setattr__(self, "tool_response_contracts", tuple(tuple(pair) for pair in self.tool_response_contracts))
        object.__setattr__(self, "preserved_annotations", tuple(self.preserved_annotations))

    @classmethod
    def from_messages(
        cls,
        messages: Sequence[Union[ModelMessage, Mapping[str, Any]]],
        **kwargs: Any,
    ) -> "ModelRequest":
        return cls(tuple(coerce_message(item) for item in messages), **kwargs)


@dataclass(frozen=True)
class RawModelResponse:
    message: AssistantMessage
    usage: Optional[Usage] = None
    events: Tuple[ModelEvent, ...] = ()
    stream: bool = True
    error_code: str = ""


@dataclass(frozen=True)
class CollectedResponse:
    """An accepted response; callbacks and callers receive these same bytes."""
    message: AssistantMessage
    usage: Optional[Usage] = None
    # Backend-only diagnostics can contain suppressed reasoning or failed
    # attempts. Keep them out of routine accepted-result representations.
    raw_attempts: Tuple[RawModelResponse, ...] = field(default=(), repr=False)
    response_language: str = ""


def with_response_language(request: ModelRequest) -> ModelRequest:
    """Apply the application preference to every model path without mutating history."""
    instruction = response_language_instruction(request.response_language)
    messages = list(request.messages)
    first_system = None
    for index, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            content = str(message.content or "")
            # Replace only exact application-owned blocks, never source text.
            for language in ("zh-CN", "en", "ja"):
                content = content.replace(response_language_instruction(language), "").rstrip()
            if first_system is None:
                first_system = index
                content = (content + "\n\n" if content else "") + instruction
            if content != message.content:
                messages[index] = replace(message, content=content)
    if first_system is None:
        messages.insert(0, SystemMessage(instruction))
    return replace(request, messages=tuple(messages))


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = bool(retryable)
        self.status_code = status_code


class ResponseRejectedError(ModelProviderError):
    """A completed generation failed acceptance, not a transport retry signal."""

    def __init__(self, request, attempts, violations):
        self.response_language = request.response_language
        self.contract_name = request.response_contract.name
        self.raw_attempts = tuple(attempts)
        self.raw_response = attempts[-1]
        self.violations = tuple(violations)
        self.response_sha256 = hashlib.sha256(
            repr(self.raw_response.message).encode("utf-8")
        ).hexdigest()
        template = translate(
            "模型响应未通过语言验收（{language}）。请重试或更换模型。位置：{paths}；诊断：{diagnostic}",
            request.response_language,
        )
        super().__init__(
            template.format(
                language=request.response_language,
                paths=", ".join(f"{v.path}:{v.reason}" for v in violations[:8]),
                diagnostic=self.response_sha256[:16],
            ),
            code="response_language_rejected",
            retryable=False,
        )


class ModelProvider(Protocol):
    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...


def _value(owner: Any, name: str, default: Any = None) -> Any:
    if isinstance(owner, Mapping):
        return owner.get(name, default)
    return getattr(owner, name, default)


def _tool_call_from_value(value: Any) -> ToolCall:
    function = _value(value, "function", {})
    arguments = _value(function, "arguments", "{}")
    return ToolCall(
        id=str(_value(value, "id", "") or ""),
        name=str(_value(function, "name", "") or ""),
        arguments=arguments,
    )


def coerce_message(value: Union[ModelMessage, Mapping[str, Any]]) -> ModelMessage:
    if isinstance(value, (SystemMessage, UserMessage, AssistantMessage, ToolResult)):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("model message must be a canonical message or mapping")
    role = str(value.get("role") or "").strip().lower()
    if role == "system":
        return SystemMessage(value.get("content", ""))
    if role == "user":
        images = []
        for item in value.get("images", ()) or ():
            if isinstance(item, ImageAttachment):
                images.append(item)
                continue
            if not isinstance(item, Mapping):
                raise TypeError("user message image must be an ImageAttachment or mapping")
            raw_data = item.get("data", b"")
            if isinstance(raw_data, str):
                raw_data = base64.b64decode(raw_data, validate=True)
            images.append(
                ImageAttachment(
                    str(item.get("filename") or "图片"),
                    str(item.get("media_type") or ""),
                    raw_data,
                )
            )
        return UserMessage(value.get("content", ""), tuple(images))
    if role == "assistant":
        # Do not accept _provider_reasoning from untrusted/history dictionaries.
        # Only collect_response may populate the canonical private replay field.
        return AssistantMessage(
            content=str(value.get("content") or ""),
            reasoning=str(
                value.get("reasoning") or value.get("reasoning_content") or ""
            ),
            tool_calls=tuple(
                _tool_call_from_value(item)
                for item in (value.get("tool_calls") or [])
            ),
        )
    if role == "tool":
        return ToolResult(
            call_id=str(value.get("tool_call_id") or value.get("call_id") or ""),
            name=str(value.get("name") or ""),
            content=str(value.get("content") or ""),
            is_error=bool(value.get("is_error", False)),
        )
    raise ValueError(f"unsupported model message role: {role!r}")


def strip_legacy_provider_fields(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove transport-only fields from legacy domain snapshots."""

    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"reasoning_content", "raw_response", "raw_attempts", "_provider_reasoning", "_provider_fields"}
    }


def _wire_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


def _chat_message(message: ModelMessage, origin):
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        if not message.images:
            return {"role": "user", "content": message.content}
        content = message.content
        if isinstance(content, list):
            content = copy.deepcopy(content)
        else:
            content = [{"type": "text", "text": str(content)}] if content not in (None, "") else []
        for attachment in message.images:
            encoded = base64.b64encode(attachment.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{attachment.media_type};base64,{encoded}",
            }})
        return {"role": "user", "content": content}
    if isinstance(message, ToolResult):
        return {"role": "tool", "tool_call_id": message.call_id, "content": message.content}

    payload = {"role": "assistant", "content": message.content or None}
    state = message._provider_fields
    replay = state if state.get("origin") == origin else {}
    payload.update(copy.deepcopy(replay.get("message") or {}))
    if message.tool_calls:
        extensions = replay.get("tools") or {}
        payload["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": _wire_arguments(call.arguments),
            }, **copy.deepcopy(extensions.get(call.id, {}))}
            for call in message.tool_calls
        ]
    return payload


_CHAT_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")
_OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1"


def _chat_replay(value):
    return {name: copy.deepcopy(_value(value, name))
            for name in (*_CHAT_REASONING_FIELDS, "reasoning_details", "extra_content")
            if _value(value, name, None) is not None}


def _merge_chat_replay(base, addition):
    result = copy.deepcopy(base)
    for name, value in addition.items():
        previous = result.get(name)
        if isinstance(value, Mapping) and isinstance(previous, Mapping):
            value = _merge_chat_replay(previous, value)
        elif isinstance(value, str) and isinstance(previous, str) and name in _CHAT_REASONING_FIELDS:
            value = previous + value
        elif isinstance(value, list) and isinstance(previous, list):
            value = previous + value
        result[name] = copy.deepcopy(value)
    return result


def _chat_text_events(message):
    reasoning = next((_value(message, name) for name in _CHAT_REASONING_FIELDS
                      if isinstance(_value(message, name), str) and _value(message, name)), "")
    if reasoning:
        yield ReasoningDelta(reasoning)
    content = _value(message, "content")
    if content:
        yield TextDelta(str(content))


def _output_parts(items, item_type, field, part_type):
    return "".join(
        _value(part, "text", "") or ""
        for item in items if _value(item, "type") == item_type
        for part in _value(item, field, ()) or ()
        if _value(part, "type") == part_type
    )


def _response_call(item):
    return ToolCall(_value(item, "call_id", ""), _value(item, "name", ""),
                    _value(item, "arguments", "{}"))


def _response_text(items):
    return _output_parts((item for item in items if _value(item, "phase") != "commentary"),
                         "message", "content", "output_text")


def _configuration_update(effort):
    return {"type": "configuration_update", "reasoning": {"effort": effort}}


def _response_input(message: ModelMessage, origin=None, *, configure_effort=False, system_role="system"):
    if isinstance(message, SystemMessage):
        return [{"role": system_role, "content": message.content}]
    if isinstance(message, UserMessage):
        content = message.content
        if not isinstance(content, list):
            content = [{"type": "input_text", "text": str(content)}] if content not in (None, "") else []
        else:
            parts = content
            content = []
            for part in parts:
                if part.get("type") == "text":
                    content.append({"type": "input_text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    image = part["image_url"]
                    content.append({"type": "input_image", "image_url": image["url"],
                                    "detail": image.get("detail", "auto")})
                else:
                    content.append(copy.deepcopy(part))
        for attachment in message.images:
            encoded = base64.b64encode(attachment.data).decode("ascii")
            content.append({"type": "input_image", "detail": "auto",
                            "image_url": f"data:{attachment.media_type};base64,{encoded}"})
        return [{"role": "user", "content": content or ""}]
    if isinstance(message, ToolResult):
        return [{"type": "function_call_output", "call_id": message.call_id, "output": message.content}]
    state = message._provider_fields
    replay = state.get("responses_items")
    if (replay and state.get("origin") == origin
            and _response_text(replay) == message.content
            and tuple(_response_call(item) for item in replay if item.get("type") == "function_call") == message.tool_calls):
        prefix = [_configuration_update(state["effort_update"])] if configure_effort and state.get("effort_update") else []
        return prefix + copy.deepcopy(replay)
    items = [{"role": "assistant", "content": message.content}] if message.content else []
    items.extend({"type": "function_call", "call_id": call.id, "name": call.name,
                  "arguments": _wire_arguments(call.arguments)} for call in message.tool_calls)
    return items


def _normalize_error(error: Exception) -> ModelProviderError:
    if isinstance(error, ResponseRejectedError):
        return error
    status = getattr(error, "status_code", None)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    name = type(error).__name__.lower()
    if isinstance(error, ModelProviderError):
        code, retryable = error.code, error.retryable
    elif is_explicit_stream_rejection(error):
        code, retryable = "stream_not_supported", False
    elif status in {401, 403} or "authentication" in name or (
            "permission" in name and not isinstance(error, PermissionError)):
        code, retryable = "authentication", False
    elif status == 429 or "ratelimit" in name or "rate_limit" in name:
        code, retryable = "rate_limit", True
    elif "timeout" in name:
        code, retryable = "timeout", True
    elif status == 400 or "badrequest" in name:
        code, retryable = "invalid_request", False
    elif status is not None and status >= 500:
        code, retryable = "unavailable", True
    elif "connection" in name:
        code, retryable = "unavailable", True
    else:
        code, retryable = "provider_error", False
    messages = {
        "stream_not_supported": "模型服务明确拒绝流式请求。",
        "authentication": "模型服务认证失败，请检查 API Key 和访问权限。",
        "rate_limit": "模型服务请求过于频繁，请稍后重试。",
        "timeout": "模型服务请求超时，请稍后重试。",
        "invalid_request": "模型服务拒绝了请求，请检查模型配置和输入。",
        "unavailable": "模型服务暂时不可用，请检查连接或稍后重试。",
        "protocol": "模型未返回有效候选结果，请重试或更换模型。",
        "responses_unavailable": "模型服务地址的 Responses 接口不可用，请检查 API URL 是否支持 /responses。",
        "image_not_supported": "当前模型不支持图片输入，请切换到带视觉能力的模型。",
        "image_payload_too_large": "图片编码后的请求体过大，请减少图片数量或压缩图片。",
        "context_budget_exceeded": "最终模型请求超过输入预算；请缩小本次范围或选择更大窗口的模型。内容未发送或截断。",
        "provider_error": "模型服务调用失败，请检查配置或稍后重试。",
    }
    if code not in messages:
        code = "provider_error"
    # SDK errors can echo the full Authorization/API key or request body. Only
    # this classified message may cross a workflow/event/report boundary.
    return ModelProviderError(translate(messages[code], get_language()),
        code=code, retryable=retryable, status_code=status)


def public_model_error(error: Exception) -> ModelProviderError:
    """Find a wrapped provider classification without disclosing exception text."""
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModelProviderError):
            return _normalize_error(current)
        current = current.__cause__
    return _normalize_error(error)


def _usage_event(value: Any) -> Optional[Usage]:
    if value is None:
        return None
    input_tokens = int(
        _value(value, "input_tokens", _value(value, "prompt_tokens", 0)) or 0
    )
    output_tokens = int(
        _value(value, "output_tokens", _value(value, "completion_tokens", 0)) or 0
    )
    total_tokens = int(_value(value, "total_tokens", 0) or 0)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    if not any((input_tokens, output_tokens, total_tokens)):
        return None
    # Keep the provider's metering separately from canonical totals. In
    # particular, absent reasoning usage is unknown, not zero or a text estimate.
    details = _value(value, "completion_tokens_details", None) or _value(value, "output_tokens_details", None)
    reasoning_tokens = _value(details, "reasoning_tokens", None)
    if type(reasoning_tokens) is not int or reasoning_tokens < 0:
        reasoning_tokens = None
    if isinstance(value, Mapping):
        raw_usage = copy.deepcopy(dict(value))
    elif callable(getattr(value, "model_dump", None)):
        raw_usage = value.model_dump(mode="json")
    else:
        raw_usage = {name: copy.deepcopy(_value(value, name)) for name in (
            "prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens",
            "prompt_cache_hit_tokens")
            if _value(value, name, None) is not None}
        for name in ("prompt_tokens_details", "input_tokens_details"):
            cached = _value(_value(value, name, None), "cached_tokens", None)
            if cached is not None:
                raw_usage[name] = {"cached_tokens": cached}
        if reasoning_tokens is not None:
            raw_usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return Usage(input_tokens, output_tokens, total_tokens, reasoning_tokens, raw_usage)


class OpenAICompatibleProvider:
    """The only module that knows OpenAI SDK response and request shapes."""

    def __init__(self, profile: Mapping[str, Any], api_key: str, *, client: Any = None):
        self.profile = copy.deepcopy(dict(profile))
        self.api_key = str(api_key or "").strip()
        if str(self.profile.get("adapter") or "") != "openai_compatible":
            raise ValueError("当前版本只支持 openai_compatible adapter。")
        if not self.api_key:
            raise ValueError("未配置当前模型 Profile 的 API Key。")
        self._client = client or self._create_client()
        self.observation_sink = None
        self._runtime_profiles = {}

    def _create_client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url=str(self.profile.get("baseUrl") or "").strip(),
        )

    def _runtime_profile(self, model=None):
        selected = str(model or self.profile.get("model") or "").strip()
        runtime = self._runtime_profiles.get(selected)
        if runtime is None:
            runtime = materialize_runtime_profile(
                self.profile,
                api_key=self.api_key,
                model=selected,
            )
            self._runtime_profiles[selected] = runtime
        return runtime

    @classmethod
    def preview_request(cls, profile, api_key, request):
        """Run the production request builder without constructing a transport.

        An empty key is valid here and remains empty for capability binding.
        The temporary instance has no client and cannot send a request.
        """
        provider = cls.__new__(cls)
        provider.profile = copy.deepcopy(dict(profile))
        provider.api_key = str(api_key or "").strip()
        provider._runtime_profiles = {}
        summaries = []
        provider._request_params(request, on_resolved=summaries.append)
        return summaries[0]

    def _request_params(self, request: ModelRequest, *, on_resolved=None) -> Dict[str, Any]:
        if not request._synthetic_probe:
            request = with_response_language(request)

        selected_model = str(request.model or self.profile.get("model") or "").strip()
        try:
            runtime = self._runtime_profile(selected_model)
        except ValueError as error:
            raise ModelProviderError(str(error), code="invalid_request") from error

        image_attachments = [
            image
            for message in request.messages
            if isinstance(message, UserMessage)
            for image in message.images
        ]
        encoded_bytes = sum(
            4 * ((len(image.data) + 2) // 3) for image in image_attachments
        )
        if encoded_bytes > 45 * 1024 * 1024:
            raise ModelProviderError(
                "图片编码后的请求体过大，请减少图片数量或压缩图片。",
                code="image_payload_too_large",
            )

        # Protocol ownership stays with the application/provider. Capability and
        # parameter semantics come only from the materialized v3 contract.
        reserved = {"model", "messages", "tools", "tool_choice", "stream", "response_format"} | RESPONSE_CONTROLS
        protocol = {
            "model": selected_model,
            "stream": bool(request.stream),
        }
        if request.tools:
            protocol["tools"] = copy.deepcopy(list(request.tools))
            protocol["tool_choice"] = copy.deepcopy(
                request.options.get("tool_choice", "auto")
            )
        else:
            protocol["tools"] = None
            protocol["tool_choice"] = None
        if "response_format" in request.options:
            protocol["response_format"] = copy.deepcopy(
                request.options["response_format"]
            )

        try:
            use_responses = endpoint_id(runtime.endpoint) == _OPENAI_RESPONSES_ENDPOINT
            transport_defaults = {}
            if not use_responses:
                if request.stream:
                    transport_defaults["stream_options"] = {"include_usage": True}
                if contract_capability_available(runtime.contract, "thinking_required", options=protocol):
                    transport_defaults.setdefault("extra_body", {})["thinking"] = {"type": "enabled"}
                if request.stream and request.tools and contract_capability_available(
                    runtime.contract, "tool_stream", options=protocol
                ):
                    transport_defaults.setdefault("extra_body", {})["tool_stream"] = True
            effective = resolve_request(
                runtime,
                request.options,
                protocol=protocol,
                model=selected_model,
                api_key=self.api_key,
                transport_defaults=transport_defaults,
            )
            resolved = effective.options
            if reserved.intersection(resolved.get("extra_body") or {}) or RESPONSE_CONTROLS.intersection(resolved):
                raise ValueError("Model options cannot override conversation or protocol fields")

            if image_attachments and not contract_capability_available(
                runtime.contract, "vision", options=resolved
            ):
                raise ModelProviderError(
                    "当前模型或参数组合不支持图片输入。",
                    code="image_not_supported",
                )
            if use_responses:
                params, reasoning_tokens = self._responses_params(runtime, request.messages, resolved)
            else:
                params = self._chat_params(runtime, request.messages, resolved)
                reasoning_tokens = 0
            from model_runtime.context_budget import final_request_budget
            report = final_request_budget({**self.profile, 'capabilityContract': runtime.contract.to_dict()},
                                          params, replayed_reasoning_tokens=reasoning_tokens)
            if report['fits'] is False:
                raise ModelProviderError(
                    f"最终模型请求超过输入预算：{report['estimated_input_tokens']} > "
                    f"{report['usable_input_tokens']} 估算 token；未发送或截断内容。",
                    code='context_budget_exceeded')
            if on_resolved is not None:
                from model_runtime.usage import request_summary
                on_resolved(request_summary(runtime, effective, options=params,
                    profile_name=str(self.profile.get("name") or ""), api_key=self.api_key))
            return params
        except (KeyError, TypeError, ValueError) as error:
            raise ModelProviderError(str(error), code="invalid_request") from error

    def _chat_params(self, runtime, messages, resolved):
        params = copy.deepcopy(dict(resolved))
        params.update(params.pop("extra_body", {}) or {})
        if not params["stream"]:
            params.pop("stream_options", None)
        limit = params.pop("max_output_tokens", None)
        if limit is not None and "max_completion_tokens" not in params:
            params.setdefault("max_tokens", limit)
        thinking = params.get("thinking")
        if (params.get("tools") and isinstance(thinking, Mapping) and thinking.get("type") == "enabled"
                and contract_capability_available(runtime.contract, "disable_tool_choice_with_thinking",
                                                  options=resolved)):
            params.pop("tool_choice", None)
        origin = self._origin(runtime.model)
        params["messages"] = [_chat_message(message, origin) for message in messages]
        return params

    def _responses_params(self, runtime, messages, resolved):
        effort_descriptor = runtime.contract.parameters.get("reasoning_effort")
        params = encode_options(resolved, reasoning_levels=(
            effort_descriptor.ui_hint.get("reasoning_levels") if effort_descriptor else None
        ))
        reasoning = params.get("reasoning") or {}
        effort = reasoning.get("effort")
        configure_effort = (
            bool(effort and effort != "none") and reasoning.get("mode", "standard") == "standard"
            and (not self.api_key or self.api_key.startswith("sk-"))
            and contract_capability_available(runtime.contract, "mid_convo_effort", options=resolved)
        )
        origin = self._origin(runtime.model)
        native_reasoning = contract_capability_available(runtime.contract, "reasoning", options=resolved)
        inputs, reasoning_tokens = [], 0
        for message in messages:
            items = _response_input(message, origin, configure_effort=configure_effort,
                                    system_role="developer" if native_reasoning else "system")
            inputs.extend(items)
            if isinstance(message, AssistantMessage) and any(item.get("type") == "reasoning" for item in items):
                reasoning_tokens += message._provider_fields.get("reasoning_tokens") or 0
        if configure_effort:
            reasoning["effort"] = "medium"
            inputs.append(_configuration_update(effort))
        params["input"] = inputs
        if native_reasoning and effort != "none":
            include = list(params.get("include") or ())
            if "reasoning.encrypted_content" not in include:
                params["include"] = [*include, "reasoning.encrypted_content"]
            if effort:
                reasoning.setdefault("summary", "auto")
        return params, reasoning_tokens

    def _origin(self, model=None):
        return (str(self.profile.get("baseUrl") or "").rstrip("/"),
                str(model or self.profile.get("model") or ""),
                hashlib.sha256(self.api_key.encode("utf-8")).hexdigest())

    @staticmethod
    def _sdk_params(create, params):
        """Move extensions unknown to the installed SDK to extra_body.

        This is SDK-signature based, not a per-model parameter allowlist. The
        canonical protocol fields were already protected by _request_params.
        """
        try:
            signature = inspect.signature(create)
        except (TypeError, ValueError):
            return params
        if any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values()):
            return params
        params = copy.deepcopy(params)
        extra = {}
        for name in list(params):
            if name == "extra_body" or name not in signature.parameters:
                extra[name] = params.pop(name)
        if extra:
            params["extra_body"] = extra
        return params

    def for_detection(self, *, parameters=None):
        """Same transport extensions, with legacy tuning stripped in migration."""
        contract = scoped_contract(self.profile)
        declared = {**(contract.parameters if contract else {}), **(parameters or {})}
        profile = detection_profile(self.profile, declared)
        return OpenAICompatibleProvider(profile, self.api_key, client=self._client)

    def _create_response(self, client, params):
        if "messages" in params:
            create = client.chat.completions.create
            return create(**self._sdk_params(create, params))
        create = client.responses.create
        try:
            return create(**self._sdk_params(create, params))
        except Exception as error:
            status = getattr(error, "status_code", None)
            if status in {404, 405, 501} and getattr(error, "code", None) != "model_not_found":
                raise ModelProviderError("Responses endpoint unavailable", code="responses_unavailable",
                                         status_code=status) from error
            raise

    def probe_parameter(self, model, name=None, value=None, *, timeout=15.0, context=None):
        """One bounded request over the profile's generation protocol."""
        use_responses = endpoint_id(self.profile["baseUrl"]) == _OPENAI_RESPONSES_ENDPOINT
        options = {"response_format": None, **(context or {})}
        if name is not None:
            options[name] = value
        if not any(key in options or key in (options.get("extra_body") or {}) for key in OUTPUT_LIMITS):
            options["max_completion_tokens" if use_responses else "max_tokens"] = 128
        request = ModelRequest((UserMessage("Reply with OK."),), model=model, stream=False,
                               options=options, timeout=timeout, max_retries=0, _synthetic_probe=True)
        params = self._request_params(request)
        client = self._client.with_options(timeout=timeout, max_retries=0)
        try:
            response = self._create_response(client, params)
            if use_responses and (_value(response, "status") == "completed" or self._output_limited(response)):
                return "accepted"
            if not use_responses and _value(response, "choices"):
                return "accepted"
            raise ModelProviderError("Probe response did not complete", code="protocol")
        except Exception as error:
            if name is not None:
                if name in OUTPUT_LIMITS:
                    target = next((key for key in OUTPUT_LIMITS if key in params), name)
                else:
                    target = "effort" if use_responses and name == "reasoning_effort" else name
                outcome = parameter_error(error, target)
                if outcome != "unknown":
                    return outcome
            raise _normalize_error(error) from error

    def list_model_metadata(self, *, timeout=15.0):
        try:
            client = self._client.with_options(timeout=timeout, max_retries=0)
            response = client.models.list()
            return tuple({name: copy.deepcopy(_value(item, name))
                          for name in ("id", "parameters", "parameter_schema", "supported_parameters", "capabilities", "constraints", "context_window")
                          if _value(item, name, None) is not None}
                         for item in (_value(response, "data", []) or []))
        except Exception as error:
            raise _normalize_error(error) from error

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        client = self._client
        options = {}
        if request.timeout is not None:
            options["timeout"] = request.timeout
        if request.max_retries is not None:
            options["max_retries"] = request.max_retries
        if options:
            client = client.with_options(**options)
        try:
            from model_runtime.usage import emit_usage
            params = self._request_params(request, on_resolved=emit_usage)
            diagnostics.emit("provider_request", stage="provider_transport", stream=request.stream,
                model=params.get("model"), response_format=wire_response_format(params) or "unspecified",
                max_output_tokens=next((params[key] for key in OUTPUT_LIMITS if key in params), None))
            response = self._create_response(client, params)
            origin = self._origin(params["model"])
            if "messages" in params:
                parse = self._chat_streaming_events if request.stream else self._chat_complete_events
                events = parse(response, origin=origin, allow_incomplete=request._synthetic_probe)
            else:
                last_input = params["input"][-1] if params["input"] else {}
                effort_update = (last_input["reasoning"]["effort"]
                                 if last_input.get("type") == "configuration_update" else None)
                parse = self._streaming_events if request.stream else self._complete_events
                events = parse(response, origin=origin, effort_update=effort_update,
                               allow_incomplete=request._synthetic_probe)
            observed, size = [], 0
            try:
                for event in events:
                    if self.observation_sink and isinstance(event, (TextDelta, ToolCallEnd)):
                        size += len(event.text) if isinstance(event, TextDelta) else len(str(event.tool_call.arguments))
                        if size <= 65536:
                            observed.append(event)
                    yield event
            finally:
                events.close()
            if size > 65536:
                observed = []
            self._observe(params, observed)
        except Exception as error:
            if "params" in locals():
                self._observe(params, [], error)
            raise _normalize_error(error) from error

    def _observe(self, params, events, error=None):
        if self.observation_sink is not None:
            try:
                self.observation_sink(params, events, error)
            except Exception:
                # Observation storage is best-effort, never a retry trigger.
                pass

    def _chat_complete_events(self, response, *, origin, allow_incomplete=False):
        choices = _value(response, "choices") or ()
        choice = choices[0] if choices else None
        message = _value(choice, "message")
        finish = _value(choice, "finish_reason")
        refusal = bool(_value(message, "refusal"))
        usage = _usage_event(_value(response, "usage"))
        diagnostics.emit("provider_result", stage="provider_transport", stream=False,
            model=_value(response, "model"), finish_reason=finish, finish_seen=finish is not None,
            choice_count=len(choices), refusal_present=refusal,
            reasoning_tokens=usage.reasoning_tokens if usage else None)
        if usage:
            yield usage
        calls = _value(message, "tool_calls") or ()
        if refusal or finish == "content_filter":
            raise ModelProviderError("Chat Completions returned no usable candidate", code="protocol")
        yield from _chat_text_events(message)
        if not (_value(message, "content") or calls or (allow_incomplete and finish == "length")):
            raise ModelProviderError("Chat Completions returned no usable candidate", code="protocol")
        state = {"origin": origin, "message": _chat_replay(message), "tools": {},
                 "reasoning_tokens": usage.reasoning_tokens if usage else None}
        for index, raw_call in enumerate(calls):
            call = _tool_call_from_value(raw_call)
            if not call.id:
                call = replace(call, id=f"tool_call_{index}")
            extension = _value(raw_call, "extra_content")
            if extension is not None:
                state["tools"][call.id] = {"extra_content": copy.deepcopy(extension)}
            yield ToolCallStart(call.id, call.name)
            yield ToolCallEnd(call)
        if state["message"] or state["tools"]:
            yield _ProviderState(state)

    def _chat_streaming_events(self, response, *, origin, allow_incomplete=False):
        fragments = {}
        state = {"origin": origin, "message": {}, "tools": {}}
        usage = finish = model = None
        chunk_count = 0
        content_seen = refused = False
        try:
            for chunk in response:
                chunk_count += 1
                model = _value(chunk, "model", model)
                usage = _usage_event(_value(chunk, "usage")) or usage
                choices = _value(chunk, "choices") or ()
                if not choices:
                    continue
                finish = _value(choices[0], "finish_reason") or finish
                delta = _value(choices[0], "delta")
                if delta is None:
                    continue
                refused |= bool(_value(delta, "refusal"))
                state["message"] = _merge_chat_replay(state["message"], _chat_replay(delta))
                for event in _chat_text_events(delta):
                    content_seen |= isinstance(event, TextDelta)
                    yield event
                for fallback_index, raw_call in enumerate(_value(delta, "tool_calls") or ()):
                    index = _value(raw_call, "index")
                    if type(index) is not int:
                        index = fallback_index
                    fragment = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    function = _value(raw_call, "function")
                    fragment["id"] += _value(raw_call, "id") or ""
                    fragment["name"] += _value(function, "name") or ""
                    arguments = _value(function, "arguments")
                    if arguments not in (None, ""):
                        fragment["arguments"] += _wire_arguments(arguments)
                    extension = _value(raw_call, "extra_content")
                    if isinstance(extension, Mapping):
                        fragment["extra_content"] = _merge_chat_replay(fragment.get("extra_content", {}), extension)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            diagnostics.emit("provider_result", stage="provider_transport", stream=True,
                model=model, chunk_count=chunk_count, finish_reason=finish, finish_seen=finish is not None,
                refusal_present=refused, reasoning_tokens=usage.reasoning_tokens if usage else None)
        if usage:
            yield usage
            state["reasoning_tokens"] = usage.reasoning_tokens
        if refused or finish == "content_filter" or not (content_seen or fragments or (allow_incomplete and finish == "length")):
            raise ModelProviderError("Chat Completions returned no usable candidate", code="protocol")
        for index, fragment in sorted(fragments.items()):
            call = ToolCall(fragment["id"] or f"tool_call_{index}", fragment["name"], fragment["arguments"] or "{}")
            if "extra_content" in fragment:
                state["tools"][call.id] = {"extra_content": fragment["extra_content"]}
            yield ToolCallStart(call.id, call.name)
            yield ToolCallEnd(call)
        if state["message"] or state["tools"]:
            yield _ProviderState(state)

    @staticmethod
    def _output_limited(response):
        return (_value(response, "status") == "incomplete"
                and _value(_value(response, "incomplete_details"), "reason") == "max_output_tokens")

    def _complete_events(self, response, *, origin=None, effort_update=None,
                         allow_incomplete=False, streamed_text="", streamed_summary="", chunk_count=0):
        status = _value(response, "status")
        items = list(_value(response, "output", ()) or ())
        usage = _usage_event(_value(response, "usage"))
        refusal = any(_value(part, "type") == "refusal" for item in items
                      if _value(item, "type") == "message" for part in _value(item, "content", ()) or ())
        diagnostics.emit("provider_result", stage="provider_transport", stream=bool(chunk_count),
            model=_value(response, "model"), finish_reason=status, finish_seen=status is not None,
            chunk_count=chunk_count, output_item_count=len(items), refusal_present=refusal,
            reasoning_tokens=usage.reasoning_tokens if usage else None)
        if usage:
            yield usage
        if refusal or status not in {"completed", "incomplete"}:
            code = _value(_value(response, "error"), "code")
            raise ModelProviderError("Responses request did not complete",
                code={"rate_limit_exceeded": "rate_limit", "server_error": "unavailable"}.get(code, "protocol"))
        content = _response_text(items)
        summary = _output_parts(items, "reasoning", "summary", "summary_text")
        if not content.startswith(streamed_text) or not summary.startswith(streamed_summary):
            raise ModelProviderError("Responses stream and terminal output differ", code="protocol")
        if summary[len(streamed_summary):]:
            yield ReasoningDelta(summary[len(streamed_summary):])
        if content[len(streamed_text):]:
            yield TextDelta(content[len(streamed_text):])
        if status == "incomplete" and not (allow_incomplete and self._output_limited(response)):
            raise ModelProviderError("Responses output was incomplete", code="protocol")
        calls = [_response_call(item) for item in items if _value(item, "type") == "function_call"]
        if not (content or calls or allow_incomplete):
            raise ModelProviderError("Responses returned no text or function call", code="protocol")
        for call in calls:
            if not call.id or not call.name:
                raise ModelProviderError("Responses function call has no ID or name", code="protocol")
            yield ToolCallStart(call.id, call.name)
            yield ToolCallEnd(call)
        if items:
            yield _ProviderState({
                "origin": origin or self._origin(),
                "responses_items": [copy.deepcopy(dict(item)) if isinstance(item, Mapping)
                                    else item.model_dump(mode="json", exclude_none=True) for item in items],
                "reasoning_tokens": usage.reasoning_tokens if usage else None,
                "effort_update": effort_update,
            })

    def _streaming_events(self, response: Iterable[Any], **options) -> Iterator[ModelEvent]:
        text, summary = [], []
        commentary = set()
        chunk_count = 0
        terminal = False
        try:
            for event in response:
                chunk_count += 1
                kind = _value(event, "type")
                delta = _value(event, "delta")
                if kind in {"response.output_item.added", "response.output_item.done"}:
                    item = _value(event, "item")
                    if _value(item, "phase") == "commentary":
                        commentary.add(_value(item, "id"))
                elif (kind == "response.output_text.delta" and isinstance(delta, str)
                      and _value(event, "item_id") not in commentary):
                    text.append(delta)
                    yield TextDelta(delta)
                elif kind == "response.reasoning_summary_text.delta" and isinstance(delta, str):
                    summary.append(delta)
                    yield ReasoningDelta(delta)
                elif kind in {"response.completed", "response.incomplete", "response.failed"}:
                    terminal = True
                    yield from self._complete_events(_value(event, "response"), **options,
                        streamed_text="".join(text), streamed_summary="".join(summary), chunk_count=chunk_count)
                    return
                elif kind == "error":
                    raise ModelProviderError("Responses stream reported an error", code="protocol")
            raise ModelProviderError("Responses stream ended before a terminal event", code="protocol")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if not terminal:
                diagnostics.emit("provider_result", stage="provider_transport", stream=True,
                                 chunk_count=chunk_count, finish_seen=False)

    def list_models(self, *, timeout: Optional[float] = None) -> Sequence[str]:
        try:
            client = self._client
            if timeout is not None:
                client = client.with_options(timeout=timeout, max_retries=0)
            response = client.models.list()
            return tuple(
                str(_value(item, "id", ""))
                for item in (_value(response, "data", []) or [])
                if _value(item, "id", None)
            )
        except Exception as error:
            raise _normalize_error(error) from error


def collect_response(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    on_reasoning_chunk: Optional[Callable[[str], None]] = None,
    on_content_chunk: Optional[Callable[[str], None]] = None,
    on_event: Optional[Callable[[ModelEvent], None]] = None,
    fallback_to_non_stream: bool = False,
    on_fallback: Optional[Callable[[ModelProviderError], None]] = None,
    allow_intermediate_tool_content: bool = False,
) -> CollectedResponse:
    """Collect atomically, accept once, then publish callbacks and tool events.

    Adapters emit raw events. No partial failed attempt or rejected prose is
    delivered as accepted content, even when the transport falls back. Language
    failure never triggers transport fallback or automatic PLC regeneration.
    """
    from model_runtime.usage import emit_token_usage

    if (fallback_to_non_stream and request.stream and not preferred_streaming(
        getattr(provider, "profile", None), model=request.model,
        api_key=getattr(provider, "api_key", None),
    )):
        request = replace(request, stream=False)
    request = with_response_language(request)
    diagnostics.begin_request(request, provider)
    attempts = []
    progress_observer = _progress_observer.get()
    preview_observer = _preview_observer.get()

    def preview(kind, text=""):
        if preview_observer is not None:
            preview_observer(ResponsePreview(kind, text))

    def consume(current: ModelRequest) -> RawModelResponse:
        diagnostics.begin_attempt()
        attempt_started = time.monotonic()
        reasoning = []
        content = []
        calls = []
        events = []
        usage = None
        received_characters = 0
        provider_fields = {}
        saw_provider_state = False

        def progress(phase):
            if progress_observer is not None:
                progress_observer(ResponseProgress(phase, received_characters))

        def snapshot(error_code=""):
            return RawModelResponse(
                AssistantMessage("".join(content), tuple(calls), "".join(reasoning),
                                 _provider_fields=copy.deepcopy(provider_fields)),
                usage, tuple(events), current.stream, error_code,
            )

        try:
            progress("waiting")
            preview("start")
            for event in provider.stream(current):
                if isinstance(event, _ProviderState):
                    saw_provider_state = True
                    provider_fields = copy.deepcopy(event.fields)
                    continue
                events.append(event)
                if isinstance(event, ReasoningDelta):
                    reasoning.append(event.text)
                    received_characters += len(event.text)
                    progress("thinking")
                    preview("reasoning", event.text)
                elif isinstance(event, TextDelta):
                    content.append(event.text)
                    received_characters += len(event.text)
                    progress("receiving")
                    preview("content", event.text)
                elif isinstance(event, ToolCallEnd):
                    calls.append(event.tool_call)
                elif isinstance(event, Usage):
                    usage = event
        except Exception as error:
            diagnostics.exception_record(error, event="provider_exception", stage="provider_transport")
            diagnostics.response_received(snapshot(), current)
            preview("discard")
            safe_error = public_model_error(error)
            attempts.append(snapshot(safe_error.code))
            safe_error.raw_attempts = tuple(attempts)
            # A structured stream rejection is the only replay permission.
            # Even an explicit code is insufficient once any output has arrived.
            safe_error._stream_fallback_allowed = can_fallback_to_non_stream(
                safe_error, had_events=bool(events or saw_provider_state),
            )
            if safe_error is error:
                raise
            raise safe_error from error
        finally:
            emit_token_usage(usage)
        raw = snapshot()
        diagnostics.response_received(raw, current)
        diagnostics.emit("attempt_finished", stage="response_acceptance",
                         elapsed_ms=int((time.monotonic() - attempt_started) * 1000))
        attempts.append(raw)
        progress("validating")
        return raw

    try:
        raw = consume(request)
    except ModelProviderError as error:
        if isinstance(error, ResponseRejectedError):
            raise
        if not (fallback_to_non_stream and request.stream and error._stream_fallback_allowed):
            raise
        if on_fallback is not None:
            on_fallback(error)
        raw = consume(replace(request, stream=False))

    # Original quotations may keep the source language. They must be explicitly
    # quoted and an exact substring of request evidence, never a blanket JSON or
    # markdown exemption. Tool results remain untouched for the next model turn.
    sources = []
    annotations = set(request.preserved_annotations)
    for message in request.messages:
        if isinstance(message, (UserMessage, SystemMessage)):
            sources.append(str(message.content or ""))
        elif isinstance(message, ToolResult):
            # Inspect the evidence the model actually saw, not UI-private data.
            sources.append(message.content)
            try:
                annotations.update(preserved_annotations(json.loads(message.content)))
            except (ValueError, TypeError):
                pass
    # A tool-only turn has no final content to parse. The eventual final answer
    # still has to satisfy the same JSON contract; tool arguments are checked
    # below before any event or executable call is released.
    content, tail_recovered = (recover_json_object_tail(raw.message.content)
        if request.response_contract.format == "json" else (raw.message.content, False))
    if tail_recovered:
        diagnostics.emit("local_json_tail_recovered", stage="response_framing")
    tool_turn = bool(raw.message.tool_calls) and (not content.strip() or allow_intermediate_tool_content)
    violations = [] if tool_turn else list(inspect_response(
        content, request.response_language, request.response_contract,
        source_texts=sources, annotations=annotations,
    ))
    reasoning_violations = inspect_response(
        raw.message.reasoning, request.response_language, source_texts=sources,
        path_prefix="reasoning",
    )
    tool_contracts = dict(request.tool_response_contracts)
    for index, call in enumerate(raw.message.tool_calls):
        if call.name in tool_contracts:
            arguments = call.arguments
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            violations.extend(inspect_response(
                arguments, request.response_language, tool_contracts[call.name],
                source_texts=sources, annotations=annotations,
                path_prefix=f"tool_calls[{index}].arguments",
            ))
    if not request.enforce_response_language:
        # Human-language preference must not reject an otherwise usable
        # engineering response. JSON syntax and schema field failures remain
        # hard failures; PLC/GXW validators still run in their existing layers.
        language_reasons = {"unsupported_script", "non_english_script", "japanese_script",
                            "latin_prose", "ambiguous_han_only"}
        violations = [v for v in violations if v.reason not in language_reasons]
    if violations:
        diagnostics.emit("response_rejected", stage="response_acceptance",
                         violations=[{"reason": v.reason} for v in violations])
        preview("discard")
        raise ResponseRejectedError(request, attempts, violations)

    # Reasoning is optional display material. A mismatch hides that entire
    # channel; it never relaxes content/tool acceptance, modifies their bytes,
    # or triggers another request. Raw attempts remain backend-only evidence.
    accepted_message = replace(raw.message, content=content,
                               _provider_fields=raw.message._provider_fields if content == raw.message.content else {})
    if reasoning_violations:
        accepted_message = replace(accepted_message, reasoning="", _provider_reasoning=raw.message.reasoning)
    accepted_events = []
    text_emitted = False
    for event in raw.events:
        if reasoning_violations and isinstance(event, ReasoningDelta):
            continue
        if tail_recovered and isinstance(event, TextDelta):
            if text_emitted:
                continue
            event = TextDelta(content)
            text_emitted = True
        accepted_events.append(event)
    diagnostics.emit("model_accepted", stage="publication")
    preview("complete")
    for event in accepted_events:
        if on_event is not None:
            on_event(event)
        if isinstance(event, ReasoningDelta) and on_reasoning_chunk is not None:
            on_reasoning_chunk(event.text)
        elif isinstance(event, TextDelta) and on_content_chunk is not None:
            on_content_chunk(event.text)
    return CollectedResponse(accepted_message, raw.usage, tuple(attempts), request.response_language)


_provider_lock = threading.Lock()
_provider_cache_key = None
_provider_cache: Optional[ModelProvider] = None


def create_provider(
    profile: Mapping[str, Any], api_key: str, *, client: Any = None
) -> ModelProvider:
    adapter = str(profile.get("adapter") or "")
    if adapter != "openai_compatible":
        raise ValueError(f"不支持的模型 adapter：{adapter}")
    return OpenAICompatibleProvider(profile, api_key, client=client)


def get_active_provider(config: Optional[Mapping[str, Any]] = None) -> ModelProvider:
    # Canonical ToolCall/ToolResult imports must not initialize Windows
    # credentials or desktop model settings in an external tool server.
    from storage.config import get_api_key, get_model_profile, load_full_config

    global _provider_cache_key, _provider_cache
    config = dict(config) if config is not None else load_full_config()
    profile = get_model_profile(config)
    api_key = get_api_key(config, profile["id"])
    cache_key = (
        json.dumps(profile, ensure_ascii=False, sort_keys=True),
        api_key,
    )
    with _provider_lock:
        if _provider_cache is None or _provider_cache_key != cache_key:
            _provider_cache = create_provider(profile, api_key)
            _provider_cache_key = cache_key
        return _provider_cache


def reset_model_provider() -> None:
    global _provider_cache_key, _provider_cache
    with _provider_lock:
        _provider_cache_key = None
        _provider_cache = None


def reload_model_provider() -> ModelProvider:
    reset_model_provider()
    return get_active_provider()


def test_model_profile(profile: Mapping[str, Any], api_key: str) -> str:
    provider = create_provider(profile, api_key)
    selected = str(profile.get("model") or "")
    try:
        model_ids = set(provider.list_models(timeout=5.0))
    except ModelProviderError as error:
        if error.status_code in {404, 405, 501}:
            raise ModelProviderError("服务未提供模型列表。", code="metadata_unavailable", status_code=error.status_code) from error
        raise
    if model_ids and selected not in model_ids:
        return "模型列表接口可访问，但列表中未找到当前模型；尚未验证其生成权限。"
    return "模型列表接口可访问；尚未验证当前模型的生成权限。没有发送生成请求。"


def sdk_runtime_self_test() -> bool:
    """Verify packaged adapter dependencies without issuing a network request."""

    try:
        import jiter
        import pydantic
        import pydantic_core

        provider = OpenAICompatibleProvider(
            {
                "adapter": "openai_compatible",
                "baseUrl": "https://api.deepseek.com",
                "model": "offline-package-self-test",
            },
            "sk-offline-package-self-test",
        )
        return bool(callable(provider._client.chat.completions.create)
                    and callable(provider._client.responses.create) and jiter and pydantic and pydantic_core)
    except Exception:
        return False


__all__ = [
    "AssistantMessage",
    "CollectedResponse",
    "ImageAttachment",
    "ModelEvent",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "RawModelResponse",
    "ResponseRejectedError",
    "ResponseProgress",
    "ResponsePreview",
    "response_policy_scope",
    "OpenAICompatibleProvider",
    "ReasoningDelta",
    "SystemMessage",
    "TextDelta",
    "ToolCall",
    "ToolCallEnd",
    "ToolCallStart",
    "ToolResult",
    "Usage",
    "UserMessage",
    "coerce_message",
    "collect_response",
    "create_provider",
    "get_active_provider",
    "public_model_error",
    "reload_model_provider",
    "reset_model_provider",
    "sdk_runtime_self_test",
    "strip_legacy_provider_fields",
    "test_model_profile",
]
