"""Vendor-neutral model messages and the OpenAI-compatible transport."""

from __future__ import annotations

import copy
import base64
import json
import hashlib
import inspect
import threading
import time
import runtime_diagnostics as diagnostics
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Protocol, Sequence, Tuple, Union

from i18n import get_language, normalize_language, response_language_instruction, translate
from response_language import ResponseContract, TEXT_RESPONSE, inspect_response, preserved_annotations
from tool_messages import ToolCall, ToolResult
from model_capabilities import apply_parameter_contract, parameter_error


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


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _wire_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


def _wire_message(message: ModelMessage, origin=None) -> Dict[str, Any]:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        if not message.images:
            return {"role": "user", "content": message.content}
        content = []
        if message.content not in (None, ""):
            content.append({"type": "text", "text": str(message.content)})
        for attachment in message.images:
            encoded = base64.b64encode(attachment.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{attachment.media_type};base64,{encoded}"
                    },
                }
            )
        return {"role": "user", "content": content}
    if isinstance(message, ToolResult):
        return {
            "role": "tool",
            "tool_call_id": message.call_id,
            "content": message.content,
        }
    payload: Dict[str, Any] = {
        "role": "assistant",
        "content": message.content or None,
    }
    state = message._provider_fields
    replay_allowed = not state or state.get("origin") == origin
    replay_reasoning = (message._provider_reasoning or message.reasoning) if replay_allowed else ""
    if replay_reasoning:
        # Both supported OpenAI-compatible providers require this replay during
        # an interleaved thinking/tool turn.  It never leaves this adapter.
        payload["reasoning_content"] = replay_reasoning
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": item.id,
                "type": "function",
                "function": {
                    "name": item.name,
                    "arguments": _wire_arguments(item.arguments),
                },
            }
            for item in message.tool_calls
        ]
    if state and replay_allowed:
        payload.update(copy.deepcopy(state.get("message") or {}))
        for call in payload.get("tool_calls", []):
            call.update(copy.deepcopy((state.get("tools") or {}).get(call["id"], {})))
    return payload


def _replay_fields(value):
    # Only backend responses can populate these fields. Do not forward response
    # metadata, URLs, headers, credentials, or arbitrary SDK object attributes.
    return {name: copy.deepcopy(_value(value, name))
            for name in ("reasoning_content", "reasoning", "reasoning_details", "extra_content")
            if _value(value, name, None) is not None}


def _merge_replay(base, addition):
    result = copy.deepcopy(base)
    for name, value in addition.items():
        if isinstance(value, Mapping) and isinstance(result.get(name), Mapping):
            result[name] = _merge_replay(result[name], value)
        elif isinstance(value, str) and isinstance(result.get(name), str):
            # Opaque signatures are whole values, not text deltas. Never append
            # repeated signatures; only concatenate explicitly textual channels.
            result[name] = result[name] + value if name in {"reasoning_content", "reasoning"} else value
        elif isinstance(value, list) and isinstance(result.get(name), list):
            result[name] = result[name] + copy.deepcopy(value)
        else:
            result[name] = copy.deepcopy(value)
    return result


def _normalize_error(error: Exception) -> ModelProviderError:
    if isinstance(error, ResponseRejectedError):
        return error
    status = getattr(error, "status_code", None)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    name = type(error).__name__.lower()
    if isinstance(error, ModelProviderError):
        code, retryable = error.code, error.retryable
    elif status in {401, 403} or "authentication" in name or "permission" in name:
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
        "authentication": "模型服务认证失败，请检查 API Key 和访问权限。",
        "rate_limit": "模型服务请求过于频繁，请稍后重试。",
        "timeout": "模型服务请求超时，请稍后重试。",
        "invalid_request": "模型服务拒绝了请求，请检查模型配置和输入。",
        "unavailable": "模型服务暂时不可用，请检查连接或稍后重试。",
        "protocol": "模型未返回有效候选结果，请重试或更换模型。",
        "image_not_supported": "当前模型不支持图片输入，请切换到带视觉能力的模型。",
        "image_payload_too_large": "图片编码后的请求体过大，请减少图片数量或压缩图片。",
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
    return Usage(input_tokens, output_tokens, total_tokens)


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

    def _create_client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url=str(self.profile.get("baseUrl") or "").strip(),
        )

    def _request_params(self, request: ModelRequest) -> Dict[str, Any]:
        request = with_response_language(request)
        response_format_unset = object()
        explicit_response_format = (
            copy.deepcopy(request.options["response_format"])
            if "response_format" in request.options
            else response_format_unset
        )
        params = _deep_merge(
            self.profile.get("generationDefaults") or {},
            request.options or {},
        )
        capabilities = self.profile.get("capabilities") or {}
        image_attachments = [
            image
            for message in request.messages
            if isinstance(message, UserMessage)
            for image in message.images
        ]
        if image_attachments and not capabilities.get("multimodal"):
            model = str(request.model or self.profile.get("model") or "当前模型")
            raise ModelProviderError(
                f"模型 {model} 不支持图片输入，请切换到带视觉能力的模型。",
                code="image_not_supported",
            )
        encoded_bytes = sum(
            4 * ((len(image.data) + 2) // 3) for image in image_attachments
        )
        if encoded_bytes > 45 * 1024 * 1024:
            raise ModelProviderError(
                "图片编码后的请求体过大，请减少图片数量或压缩图片。",
                code="image_payload_too_large",
            )
        extra_body = copy.deepcopy(params.get("extra_body") or {})
        if capabilities.get("thinking_required"):
            thinking = copy.deepcopy(extra_body.get("thinking") or {})
            thinking["type"] = "enabled"
            extra_body["thinking"] = thinking
        if capabilities.get("tool_stream") and request.stream and request.tools:
            extra_body["tool_stream"] = True
        if extra_body:
            params["extra_body"] = extra_body
        params = _deep_merge(params, self.profile.get("requestOverrides") or {})
        # A workflow-specific response contract must beat a persisted/profile-level
        # response_format. Otherwise an old native schema can accept values that
        # the current PLC validator rejects after the model response is accepted.
        if explicit_response_format is not response_format_unset:
            if explicit_response_format is None:
                params.pop("response_format", None)
            else:
                params["response_format"] = explicit_response_format

        params["model"] = request.model or str(self.profile.get("model") or "")
        params["messages"] = [_wire_message(item, self._origin(params["model"])) for item in request.messages]
        params["stream"] = bool(request.stream)
        if request.tools:
            params["tools"] = copy.deepcopy(list(request.tools))
            params.setdefault("tool_choice", "auto")
        else:
            params.pop("tools", None)
            params.pop("tool_choice", None)
        thinking = (params.get("extra_body") or {}).get("thinking") or {}
        if (
            request.tools
            and capabilities.get("disable_tool_choice_with_thinking")
            and str(thinking.get("type") or "").lower() == "enabled"
        ):
            params.pop("tool_choice", None)
        # SDK extra_body is merged last on the wire. It must not replace the
        # application's canonical context, tools or response contract.
        reserved = {"model", "messages", "tools", "tool_choice", "stream", "response_format"}
        if reserved.intersection(params.get("extra_body") or {}):
            raise ModelProviderError("extra_body cannot override protocol fields", code="invalid_request")
        try:
            return apply_parameter_contract(params, self.profile, request.model)
        except ValueError as error:
            raise ModelProviderError(str(error), code="invalid_request") from error

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
        extra = dict(params.get("extra_body") or {})
        for name in list(params):
            if name not in signature.parameters:
                extra.setdefault(name, params.pop(name))
        if extra:
            params["extra_body"] = extra
        return params

    def for_detection(self):
        """Same transport and thinking extensions, without optional tuning."""
        profile = copy.deepcopy(self.profile)
        profile.pop("parameterSupport", None)
        optional = {"temperature", "reasoning_effort", "top_p", "response_format",
                    "max_tokens", "max_completion_tokens", "stream_options"}
        for group in ("generationDefaults", "requestOverrides"):
            options = profile.setdefault(group, {})
            for key in optional:
                options.pop(key, None)
                if isinstance(options.get("extra_body"), dict):
                    options["extra_body"].pop(key, None)
        return OpenAICompatibleProvider(profile, self.api_key, client=self._client)

    def probe_parameter(self, model, name=None, value=None, *, timeout=15.0, context=None):
        """Bounded raw validation probe; provider error text never crosses this API."""
        options = {"response_format": None, getattr(self, "_probe_token_key", "max_completion_tokens"): 128, **(context or {})}
        if name is not None:
            options[name] = value
        request = ModelRequest((UserMessage("Reply with OK."),), model=model,
                               stream=False, options=options, timeout=timeout, max_retries=0)
        params = self._request_params(request)
        client = self._client.with_options(timeout=timeout, max_retries=0)
        started = time.monotonic()
        for attempt in range(2):
            try:
                response = client.chat.completions.create(**self._sdk_params(client.chat.completions.create, params))
                if not _value(response, "choices", None):
                    raise ModelProviderError("Empty probe response", code="protocol")
                return "accepted"
            except Exception as error:
                # A few compatible APIs accept only the older output-limit key.
                # Retry that *named* schema rejection, never authentication, rate
                # limits, billing errors, arbitrary 400s, or completed generations.
                if attempt == 0 and "max_completion_tokens" in params and parameter_error(error, "max_completion_tokens") in {"unsupported", "rejected"}:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        return "unknown"
                    params["max_tokens"] = params.pop("max_completion_tokens")
                    self._probe_token_key = "max_tokens"
                    client = self._client.with_options(timeout=remaining, max_retries=0)
                    continue
                if name is not None:
                    outcome = parameter_error(error, name)
                    if outcome != "unknown":
                        return outcome
                raise _normalize_error(error) from error
        return "unknown"

    def list_model_metadata(self, *, timeout=15.0):
        try:
            client = self._client.with_options(timeout=timeout, max_retries=0)
            response = client.models.list()
            return tuple({name: copy.deepcopy(_value(item, name))
                          for name in ("id", "parameters", "parameter_schema", "supported_parameters")
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
            params = self._request_params(request)
            response_format = params.get("response_format")
            diagnostics.emit("provider_request", stage="provider_transport", stream=request.stream,
                model=params.get("model"), response_format=(response_format.get("type")
                    if isinstance(response_format, Mapping) else "unspecified"),
                max_tokens=params.get("max_tokens"), max_completion_tokens=params.get("max_completion_tokens"))
            response = client.chat.completions.create(**self._sdk_params(client.chat.completions.create, params))
            if request.stream:
                yield from self._streaming_events(response, origin=self._origin(params["model"]))
            else:
                yield from self._complete_events(response, origin=self._origin(params["model"]))
        except Exception as error:
            raise _normalize_error(error) from error

    def _complete_events(self, response: Any, *, origin=None) -> Iterator[ModelEvent]:
        choices = list(_value(response, "choices", []) or [])
        choice = choices[0] if choices else None
        payload = _value(choice, "message", None)
        finish = _value(choice, "finish_reason", None)
        usage_data = _value(response, "usage", None)
        diagnostics.emit("provider_result", stage="provider_transport", stream=False,
            model=_value(response, "model", None), finish_reason=finish, finish_seen=finish is not None,
            choice_count=len(choices), content_type=type(_value(payload, "content", None)).__name__,
            reasoning_type=type(_value(payload, "reasoning_content", None)).__name__,
            refusal_present=bool(_value(payload, "refusal", None)),
            reasoning_tokens=_value(_value(usage_data, "completion_tokens_details", None), "reasoning_tokens", None))
        if not choices:
            raise ModelProviderError("AI 未返回任何候选结果。", code="protocol")
        message = _value(choices[0], "message")
        if message is None:
            raise ModelProviderError("AI 返回结果缺少 message。", code="protocol")
        reasoning = str(_value(message, "reasoning_content", "") or _value(message, "reasoning", "") or "")
        content = str(_value(message, "content", "") or "")
        if reasoning:
            yield ReasoningDelta(reasoning)
        if content:
            yield TextDelta(content)
        for index, raw_call in enumerate(_value(message, "tool_calls", []) or []):
            call = _tool_call_from_value(raw_call)
            if not call.id:
                call = replace(call, id=f"tool_call_{index}")
            yield ToolCallStart(call.id, call.name)
            yield ToolCallEnd(call)
        state = {"origin": origin or self._origin(), "message": _replay_fields(message), "tools": {}}
        for index, raw_call in enumerate(_value(message, "tool_calls", []) or []):
            extension = _value(raw_call, "extra_content", None)
            if extension is not None:
                state["tools"][str(_value(raw_call, "id") or f"tool_call_{index}")] = {"extra_content": copy.deepcopy(extension)}
        if state["message"] or state["tools"]:
            yield _ProviderState(state)
        usage = _usage_event(_value(response, "usage", None))
        if usage is not None:
            yield usage

    def _streaming_events(self, response: Iterable[Any], *, origin=None) -> Iterator[ModelEvent]:
        fragments: Dict[int, Dict[str, Any]] = {}
        latest_usage = None
        state = {"origin": origin or self._origin(), "message": {}, "tools": {}}
        chunk_count, choice_count = 0, 0
        finish_reason, response_model, reasoning_tokens = None, None, None
        content_type, reasoning_type, refusal_present = "NoneType", "NoneType", False
        try:
            for chunk in response:
                chunk_count += 1
                response_model = _value(chunk, "model", response_model)
                usage = _usage_event(_value(chunk, "usage", None))
                if usage is not None:
                    latest_usage = usage
                choices = list(_value(chunk, "choices", []) or [])
                if not choices:
                    continue
                finish = _value(choices[0], "finish_reason", None)
                if finish is not None:
                    finish_reason = finish
                choice_count = max(choice_count, len(choices))
                details = _value(_value(chunk, "usage", None), "completion_tokens_details", None)
                current_reasoning_tokens = _value(details, "reasoning_tokens", None)
                if current_reasoning_tokens is not None:
                    reasoning_tokens = current_reasoning_tokens
                delta = _value(choices[0], "delta")
                if delta is None:
                    continue
                state["message"] = _merge_replay(state["message"], _replay_fields(delta))
                content_value = _value(delta, "content", None)
                reasoning_value = _value(delta, "reasoning_content", None)
                if content_value is not None:
                    content_type = type(content_value).__name__
                if reasoning_value is not None:
                    reasoning_type = type(reasoning_value).__name__
                refusal_present |= bool(_value(delta, "refusal", None))
                reasoning = str(_value(delta, "reasoning_content", "") or _value(delta, "reasoning", "") or "")
                content = str(_value(delta, "content", "") or "")
                if reasoning:
                    yield ReasoningDelta(reasoning)
                if content:
                    yield TextDelta(content)
                for fallback_index, raw_call in enumerate(
                    _value(delta, "tool_calls", []) or []
                ):
                    raw_index = _value(raw_call, "index", fallback_index)
                    try:
                        index = int(raw_index)
                    except (TypeError, ValueError):
                        index = fallback_index
                    fragment = fragments.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    extension = _value(raw_call, "extra_content", None)
                    if isinstance(extension, Mapping):
                        fragment["extra_content"] = _merge_replay(fragment.get("extra_content", {}), extension)
                    fragment["id"] += str(_value(raw_call, "id", "") or "")
                    function = _value(raw_call, "function", {})
                    fragment["name"] += str(_value(function, "name", "") or "")
                    arguments = _value(function, "arguments", "")
                    if arguments not in (None, ""):
                        fragment["arguments"] += _wire_arguments(arguments)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            diagnostics.emit("provider_result", stage="provider_transport", stream=True,
                model=response_model, chunk_count=chunk_count, choice_count=choice_count,
                finish_reason=finish_reason, finish_seen=finish_reason is not None,
                content_type=content_type, reasoning_type=reasoning_type,
                refusal_present=refusal_present, reasoning_tokens=reasoning_tokens)
        for index in sorted(fragments):
            fragment = fragments[index]
            call = ToolCall(
                fragment["id"] or f"tool_call_{index}",
                fragment["name"],
                fragment["arguments"] or "{}",
            )
            yield ToolCallStart(call.id, call.name)
            yield ToolCallEnd(call)
            if "extra_content" in fragment:
                state["tools"][call.id] = {"extra_content": fragment["extra_content"]}
        if state["message"] or state["tools"]:
            yield _ProviderState(state)
        if latest_usage is not None:
            yield latest_usage

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
) -> CollectedResponse:
    """Collect atomically, accept once, then publish callbacks and tool events.

    Adapters emit raw events. No partial failed attempt or rejected prose is
    delivered as accepted content, even when the transport falls back. Language
    failure never triggers transport fallback or automatic PLC regeneration.
    """
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
            # Unknown adapter exceptions previously aborted without transport
            # fallback. Classification must not add a new automatic retry.
            safe_error._stream_fallback_allowed = isinstance(error, ModelProviderError)
            if safe_error is error:
                raise
            raise safe_error from error
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
    violations = [] if raw.message.tool_calls and not raw.message.content.strip() else list(inspect_response(
        raw.message.content, request.response_language, request.response_contract,
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
    accepted_message = (replace(raw.message, reasoning="", _provider_reasoning=raw.message.reasoning)
                        if reasoning_violations else raw.message)
    accepted_events = tuple(event for event in raw.events
                            if not (reasoning_violations and isinstance(event, ReasoningDelta)))
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
    from config_manager import get_api_key, get_model_profile, load_full_config

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
        model_ids = set(provider.list_models(timeout=15.0))
    except ModelProviderError as error:
        if error.code in {"authentication", "rate_limit"}:
            raise
        # A custom deployment can expose chat/completions without GET /models.
        provider.for_detection().probe_parameter(selected, timeout=15.0)
        return "连接成功；服务未提供模型列表，已验证当前模型的聊天接口。"
    if model_ids and selected not in model_ids:
        return "连接成功；模型列表中未找到当前模型，请确认模型名称。"
    return "连接成功，API Key 和服务地址有效。"


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
        return bool(provider and jiter and pydantic and pydantic_core)
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
