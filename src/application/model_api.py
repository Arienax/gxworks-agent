# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

from application.analysis_response import (
    _analysis_json_payload, _validate_fresh_analysis_content, _parse_analysis_response, request_analysis_response,
)
from application.model_response_schemas import (
    _repair_baseline_tokens,
    _constrain_native_repair_schema,
    _native_field_patch_response_format,
    _native_partial_repair_response_format,
)
from application.analysis_context import assemble_analysis_prompt

import copy
import os
import json
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from shared.i18n import language_scoped, tr
from model_runtime.responses import TEXT_RESPONSE, preserved_annotations as source_annotations
from application.response_contracts import (
    ANALYSIS_RESPONSE, ANALYSIS_REPAIR_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, FIELD_PATCH_RESPONSE,
    INSPECTION_RESPONSE, LADDER_RESPONSE, PATCH_RESPONSE, ST_RESPONSE, TEST_SUITE_RESPONSE,
)
from plc.specification.approach import normalize_approach
from rendering.ladder_svg import AdvancedSVGLadder
from storage.config import get_api_key, get_model_profile, load_full_config
from model_runtime.provider import (
    ImageAttachment,
    ModelProviderError,
    ModelRequest,
    ResponseRejectedError,
    UserMessage,
    collect_response,
    get_active_provider,
    reload_model_provider,
    reset_model_provider,
    strip_legacy_provider_fields,
)
from shared.paths import resource_path
from application.generation_context import (
    ST_SYSTEM_PROMPT, LADDER_SYSTEM_PROMPT, _st_system_prompt_for_model,
    _build_knowledge_query, _build_knowledge_context, _select_system_prompt,
    _routing_text_with_selected_approach, _load_plc_models, _build_model_context,
    _confirmed_context_text, _with_confirmed_context, build_generation_instructions,
)
from shared.context_audit import audit_request, audit_section
from plc.validation import PLCJsonValidationError, parse_device_address
from plc.hardware_profiles import ensure_hardware_questions
from plc.instructions import (
    DEFAULT_INSTRUCTION_REGISTRY, GENERATION_TYPED_OUTPUT_OPCODES,
    generation_app_instr_mnemonics,
)
from plc.generation_contract import ladder_response_schema
from knowledge.patterns import (
    assemble_prompt,
    build_workflow_prompt,
    classify_request,
)
from application.prompts import ANALYSIS_SYSTEM_PROMPT, DEBUG_EVIDENCE_DIAGNOSIS_SYSTEM_PROMPT, DEBUG_EVIDENCE_PATCH_SYSTEM_PROMPT, DEBUG_REPORT_SYSTEM_PROMPT, FIELD_PATCH_REPAIR_SYSTEM_PROMPT, INSPECTION_SYSTEM_PROMPT, MULTI_AGENT_SPECIALIST_PROMPTS, PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT, SIMULATOR_TEST_SUITE_SYSTEM_PROMPT
from application.analysis_results import (
    AnalysisProtocolError,
    _ANALYSIS_IO_KINDS,
    _ASSUMPTION_MARKERS,
    _iter_analysis_text,
    _normalize_analysis_result,
    validate_current_analysis_protocol,
)


_provider_session = ContextVar("workflow_provider_session", default=None)
_workflow_model = ContextVar("workflow_model_name", default=None)


@contextmanager
def provider_scope(provider=None, *, model_name=None):
    """Keep one provider for an entire workflow, including fallback and repair.

    A caller may supply a provider captured when a job is submitted. Otherwise
    resolution is lazy, so deterministic and injected offline workflows do not
    initialize credentials or SDK clients. Nested workflows reuse the session.
    """
    existing = _provider_session.get()
    session = existing if provider is None and existing is not None else [provider]
    token = _provider_session.set(session)
    model_token = _workflow_model.set(model_name or _workflow_model.get())
    try:
        yield
    finally:
        _workflow_model.reset(model_token)
        _provider_session.reset(token)


def _workflow_provider():
    session = _provider_session.get()
    if session is None:
        return get_active_provider()
    if session[0] is None:
        session[0] = get_active_provider()
    return session[0]


def load_config():
    """Compatibility projection for callers that still expect key/base URL."""

    config = load_full_config()
    api_key = get_api_key(config)
    base_url = get_model_profile(config).get("baseUrl", "").strip()

    if not api_key:
        raise ValueError("未配置 API Key，请打开“设置”中的 API 设置完成配置。")

    return api_key, base_url

def reload_api_client():
    """One-release compatibility alias for the provider cache."""

    return reload_model_provider()


def reset_api_client():
    """One-release compatibility alias for the provider cache."""

    reset_model_provider()


def _active_model_name(config=None):
    return _workflow_model.get() or str(get_model_profile(config or load_full_config()).get("model") or "")


@language_scoped
def _request_model(
    messages,
    *,
    model_name=None,
    effort=None,
    stream=False,
    tools=None,
    request_timeout=None,
    max_retries=None,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_event=None,
    fallback_to_non_stream=False,
    on_fallback=None,
    options=None,
    response_contract=TEXT_RESPONSE,
    preserved_annotations=(),
):
    """Run one canonical request without exposing provider response shapes."""

    # Legacy effort arguments/options are not an application tuning source.
    # Saved model settings are applied by the provider after this boundary.
    from model_runtime.request_policy import without_workflow_effort
    provider = _workflow_provider()
    request_options = without_workflow_effort(
        options, getattr(provider, "profile", {}), model=model_name,
        api_key=getattr(provider, "api_key", None),
    )
    # Format and transport are independent. Preserve an explicitly supplied
    # native schema on streaming requests as well as non-streaming requests.
    if response_contract.format == "text":
        request_options.setdefault("response_format", None)
    request = ModelRequest.from_messages(
        messages,
        model=model_name or None,
        tools=tuple(tools or ()),
        options=request_options,
        stream=bool(stream),
        timeout=request_timeout,
        max_retries=max_retries,
        response_contract=response_contract,
        preserved_annotations=preserved_annotations,
    )
    audit_request(request.messages)
    return collect_response(
        provider,
        request,
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        on_event=on_event,
        fallback_to_non_stream=fallback_to_non_stream,
        on_fallback=on_fallback,
    )


def current_provider():
    """Return the provider frozen for this workflow; never switch profiles mid-call."""
    return _workflow_provider()


def bound_provider_profile():
    """Return an already-bound profile without initializing credentials/providers."""
    session = _provider_session.get()
    provider = session[0] if session and session[0] is not None else None
    profile = getattr(provider, "profile", None)
    return copy.deepcopy(profile) if isinstance(profile, dict) else {}


def request_model(
    messages, *, model_name=None, effort=None, stream=False, tools=None,
    request_timeout=None, max_retries=None, on_reasoning_chunk=None,
    on_content_chunk=None, on_event=None, fallback_to_non_stream=False,
    on_fallback=None, options=None, response_contract=TEXT_RESPONSE,
    preserved_annotations=(),
):
    """Public model gateway. Protocol decoding and acceptance stay in the runtime."""
    return _request_model(
        messages, model_name=model_name, effort=None, stream=stream, tools=tools,
        request_timeout=request_timeout, max_retries=max_retries,
        on_reasoning_chunk=on_reasoning_chunk, on_content_chunk=on_content_chunk,
        on_event=on_event, fallback_to_non_stream=fallback_to_non_stream,
        on_fallback=on_fallback, options=options, response_contract=response_contract,
        preserved_annotations=preserved_annotations,
    )


def _user_message_with_images(content, image_attachments=None):
    """Build one canonical user message without exposing provider wire fields."""

    images = tuple(image_attachments or ())
    if any(not isinstance(item, ImageAttachment) for item in images):
        raise TypeError("image_attachments must contain ImageAttachment values")
    return UserMessage(content, images)


# ============================
# 阶段1：需求分析 Prompt + 函数
# ============================
# PLC 型号识别 + 特殊软元件注入
# ============================


def _detect_plc_model(user_input: str) -> str:
    """从用户输入中检测 PLC 型号，找不到则用 config 默认"""
    from plc.target_support import normalize_target_cpu, target_cpu_names
    input_upper = user_input.upper()
    # Match complete identities, longest first (Q00J is not Q00, and a merged
    # native menu must not be reduced to its first concrete member).
    for model in sorted(target_cpu_names(), key=lambda name: (-len(name), name)):
        if re.search(r"(?<![A-Z0-9_])" + re.escape(model.upper()) + r"(?![A-Z0-9_])", input_upper):
            return model
    config = load_full_config()
    return normalize_target_cpu(config.get("plc_model") or _load_plc_models().get("default", "FX3U"))


def _resolve_plc_model(user_input="", confirmed_context=None, explicit_model=None):
    """Resolve the selected project model before consulting global config."""
    from plc.target_support import normalize_target_cpu
    if explicit_model:
        return normalize_target_cpu(explicit_model)
    if isinstance(confirmed_context, dict):
        candidate = confirmed_context.get("plc_model")
        if candidate:
            return normalize_target_cpu(candidate)
    return _detect_plc_model(str(user_input or ""))


def _request_analysis_response(*args, **kwargs):
    return request_analysis_response(*args, request_model=_request_model, provider_scope=provider_scope, **kwargs)


@language_scoped
def analyze_requirement(
    user_requirement: str, conversation_history=None, confirmed_context=None,
    confirmed_spec=None, task_type=None, image_attachments=None,
    on_format_repair=None, analysis_mode="direct",
) -> dict:
    """Analyze a requirement using the same contract as the streaming entry."""
    return _analyze_requirement(
        user_requirement, conversation_history=conversation_history,
        confirmed_context=confirmed_context, confirmed_spec=confirmed_spec,
        image_attachments=image_attachments, on_format_repair=on_format_repair,
        analysis_mode=analysis_mode,
    )


@language_scoped
def analyze_requirement_streaming(
    user_requirement: str, on_reasoning_chunk=None, on_content_chunk=None,
    conversation_history=None, confirmed_context=None, confirmed_spec=None,
    task_type=None, image_attachments=None, on_format_repair=None,
    analysis_mode="direct", allow_repair=True,
):
    """Analyze a requirement with incremental reasoning and content callbacks."""
    return _analyze_requirement(
        user_requirement, conversation_history=conversation_history,
        confirmed_context=confirmed_context, confirmed_spec=confirmed_spec,
        image_attachments=image_attachments, on_format_repair=on_format_repair,
        analysis_mode=analysis_mode, allow_repair=allow_repair, stream=True,
        on_reasoning_chunk=on_reasoning_chunk, on_content_chunk=on_content_chunk,
    )


def _analyze_requirement(
    user_requirement, *, conversation_history=None, confirmed_context=None,
    confirmed_spec=None, image_attachments=None, on_format_repair=None,
    analysis_mode="direct", allow_repair=True, stream=False,
    on_reasoning_chunk=None, on_content_chunk=None,
):
    confirmed_context = confirmed_spec if confirmed_spec is not None else confirmed_context
    model = _resolve_plc_model(user_requirement, confirmed_context)
    analysis_prompt = assemble_analysis_prompt(
        user_requirement,
        plc_model=model,
        confirmed_context=confirmed_context,
        analysis_mode=analysis_mode,
        model_loader=_load_plc_models,
        knowledge_builder=_build_knowledge_context,
        resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form,
        audit=audit_section,
    )
    knowledge_ctx = analysis_prompt.knowledge_context
    sys_prompt = analysis_prompt.system_prompt
    print(f"阶段1: 需求分析中... (PLC={model})")

    messages = _build_clean_messages(conversation_history or [], sys_prompt)
    messages.append(
        _user_message_with_images(user_requirement, image_attachments)
    )

    try:
        response = _request_analysis_response(
            messages,
            contract_stage=analysis_prompt.contract_stage,
            user_text=user_requirement,
            plc_model=model,
            confirmed_spec=confirmed_context,
            effort=None,
            stream=stream,
            allow_repair=allow_repair,
            **({"max_retries": 0} if not allow_repair else {}),
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
            on_format_repair=on_format_repair,
        )
        raw = _clean_json_response(response.message.content)

        result = _parse_analysis_response(raw, model, user_requirement, confirmed_context,
                                         contract_stage=analysis_prompt.contract_stage)
        if getattr(response, "repair_receipt", None):
            result["analysis_repair_receipt"] = copy.deepcopy(response.repair_receipt)
        # Application metadata, not a mode selected by the model response.
        result["analysis_mode"] = "design" if analysis_prompt.route.include_design else "direct"
        from application.analysis_results import attach_analysis_evidence
        result = attach_analysis_evidence(result, knowledge_ctx, plc_model=model,
                                          knowledge_builder=_build_knowledge_context)
        print(f"阶段1 分析完成: {result.get('summary', '')[:80]}...")
        return result

    except (ModelProviderError, AnalysisProtocolError):
        raise
    except Exception as e:
        print(f"阶段1 分析失败: {e}")
        return None


def _build_clean_messages(conversation_history, system_prompt):
    """构建发送给模型的消息列表，仅保留用户可见正文。"""
    from application.generation_wire import render_wire_messages
    return render_wire_messages(system_prompt, conversation_history)


def _clean_json_response(raw):
    raw = str(raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    return raw.strip()

def _prepare_api_call(
    user_requirement,
    model_name,
    effort,
    target_mode,
    is_edit_mode=False,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    persist_history=None,
    task_type=None,
    review_mode=None,
    current_version_json=None,
    plc_model=None,
    image_attachments=None,
    on_generation_context=None,
):
    """
    共用准备逻辑：复制调用方历史、追加用户消息、选取系统提示词、
    构建清洗后的统一消息列表。
    返回 (messages, conversation_history, persist_history)
    """
    if conversation_history is None:
        conversation_history = []
    else:
        conversation_history = [dict(item) for item in conversation_history]
    if persist_history:
        raise ValueError("Model calls do not persist history; use the project application service.")
    if confirmed_spec is not None:
        confirmed_context = confirmed_spec
    normalized_task = str(task_type or review_mode or ("edit" if is_edit_mode else "generate")).strip().casefold()
    if (target_mode == "ladder" and not is_edit_mode and normalized_task == "generate"
            and isinstance(confirmed_context, dict) and confirmed_context):
        from application.confirmed_generation_context import CONFIRMED_GENERATION_REQUEST
        user_requirement = CONFIRMED_GENERATION_REQUEST
        # The full-wire adapter must have the same isolation as compact Agent B.
        # Recorded user intent is already part of the confirmed source envelope.
        conversation_history = []
    conversation_history.append({"role": "user", "content": user_requirement})

    selected_model = _resolve_plc_model(
        user_requirement,
        confirmed_context,
        explicit_model=plc_model,
    )
    system_prompt = build_generation_instructions(
        user_requirement,
        plc_model=selected_model,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
        task_type=task_type,
        review_mode=review_mode,
        confirmed_context=confirmed_context,
        current_version_json=current_version_json,
        # Keep API extension/test hooks while sharing the actual assembly.
        prompt_builder=_select_system_prompt,
        knowledge_builder=_build_knowledge_context,
        profile_builder=_build_model_context,
        confirmed_builder=_with_confirmed_context,
        on_context=on_generation_context,
        model_profile=bound_provider_profile(),
        wire_history=conversation_history,
    )
    compiled_wire = getattr(system_prompt, "wire_packet", None)
    if isinstance(compiled_wire, dict) and isinstance(compiled_wire.get("messages"), list):
        messages_to_send = copy.deepcopy(compiled_wire["messages"])
    else:
        messages_to_send = _build_clean_messages(conversation_history, system_prompt)
    if image_attachments:
        messages_to_send[-1] = _user_message_with_images(
            user_requirement,
            image_attachments,
        )

    return messages_to_send, conversation_history, False


@language_scoped
def debug_ladder(
    user_question,
    current_version_json,
    confirmed_spec=None,
    conversation_history=None,
    local_findings=None,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
    plc_model="FX3U",
    debug_context=None,
):
    """Analyze the current ladder JSON and return a structured debug report."""
    config = load_full_config()
    model_name = model_name or _active_model_name(config)
    workflow_prompt, _route = build_workflow_prompt(
        user_question,
        target_mode="ladder",
        forced_task="debug",
    )
    knowledge_ctx = _build_knowledge_context(
        user_question,
        plc_model=plc_model,
        task_type="debug",
        confirmed_context=confirmed_spec,
        evidence={
            "debug_context": debug_context or {},
            "local_findings": local_findings or [],
            "current_ladder_json": current_version_json,
        },
    )
    system_prompt = _with_confirmed_context(
        DEBUG_REPORT_SYSTEM_PROMPT
        + f"\nSelected PLC model: {plc_model}\n"
        + _build_model_context(
            plc_model,
            confirmed_spec,
            compact=bool(knowledge_ctx),
        )
        + workflow_prompt
        + knowledge_ctx,
        confirmed_spec,
    )
    local_findings = local_findings or []
    debug_payload = {
        "user_question": user_question,
        "debug_context": debug_context or {},
        "plc_model": plc_model,
        "local_findings": local_findings,
        "current_ladder_json": current_version_json,
    }
    messages = _build_clean_messages(conversation_history or [], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(debug_payload, ensure_ascii=False, indent=2),
        }
    )

    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=None,
            stream=False,
            response_contract=DEBUG_RESPONSE,
            request_timeout=request_timeout,
            max_retries=0,
        )
        raw = _clean_json_response(response.message.content)
        report = json.loads(raw)
    except ResponseRejectedError:
        raise
    except Exception as error:
        print(f"debug api request failed: {error}")
        if raise_errors:
            raise RuntimeError(f"Debug API request failed: {error}") from error
        return None
    return _normalize_debug_report(report)


def _normalize_debug_report(report):
    if not isinstance(report, dict):
        report = {}
    return {
        "summary": str(report.get("summary", "")).strip() or str(tr("调试分析完成")),
        "possible_causes": _string_list(report.get("possible_causes")),
        "related_rungs": _int_list(report.get("related_rungs")),
        "recommended_changes": _string_list(report.get("recommended_changes")),
        "needs_fix": _strict_bool(report.get("needs_fix", False)),
        "fix_instruction": str(report.get("fix_instruction", "")).strip(),
    }


@language_scoped
def _call_debug_evidence_json(
    system_prompt,
    payload,
    *,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
    response_contract=INSPECTION_RESPONSE,
    prefer_stream=False,
):
    config = load_full_config()
    selected_model = model_name or _active_model_name(config)
    messages = _build_clean_messages([], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }
    )
    try:
        wants_stream = any(
            callback is not None
            for callback in (on_reasoning_chunk, on_content_chunk, on_progress)
        )
        if prefer_stream:
            from model_runtime.runtime_profile import materialize_runtime_profile
            provider = current_provider()
            wants_stream = True
            profile = getattr(provider, "profile", None)
            if profile is not None:
                runtime = materialize_runtime_profile(
                    profile, api_key=getattr(provider, "api_key", None), model=selected_model,
                )
                streaming = runtime.contract.capabilities.get("streaming")
                wants_stream = not (streaming and streaming.status == "unsupported")
        if wants_stream and on_progress:
            on_progress(str(tr("AI 正在生成仿真测试方案（流式）")))
        response = _request_model(
            messages,
            model_name=selected_model,
            effort=None,
            stream=wants_stream,
            response_contract=response_contract,
            preserved_annotations=source_annotations(payload),
            request_timeout=request_timeout,
            max_retries=0,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
            fallback_to_non_stream=wants_stream,
            on_fallback=(
                (lambda error: on_progress(
                    str(tr("流式显示不可用，正在切换普通模式：{error}", error=error))
                ))
                if on_progress
                else None
            ),
        )
        raw_content = response.message.content
        if not raw_content.strip():
            raise RuntimeError("模型响应没有返回正文")

        if on_progress:
            on_progress(str(tr("正在解析模型输出：清理并校验 JSON 结构")))
        parsed = json.loads(_clean_json_response(raw_content))
        if not isinstance(parsed, dict):
            raise ValueError("response must be a JSON object")
        return parsed
    except ResponseRejectedError:
        raise
    except Exception as error:
        if raise_errors:
            raise RuntimeError(f"Debug evidence API request failed: {error}") from error
        return None


@language_scoped
def debug_evidence_diagnosis(
    evidence,
    *,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
):
    """Ask the model for a diagnosis; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_DIAGNOSIS_SYSTEM_PROMPT,
        {"evidence": evidence},
        response_contract=DIAGNOSIS_RESPONSE,
        model_name=model_name,
        effort=None,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


@language_scoped
def debug_evidence_patch(
    evidence,
    diagnosis,
    *,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
):
    """Ask the model for a local patch; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_PATCH_SYSTEM_PROMPT,
        {"evidence": evidence, "diagnosis": diagnosis},
        response_contract=PATCH_RESPONSE,
        model_name=model_name,
        effort=None,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


@language_scoped
def generate_simulator_test_suite(
    test_context,
    *,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
):
    """Generate Test DSL only; deterministic validation occurs in simulator.planning."""

    return _call_debug_evidence_json(
        SIMULATOR_TEST_SUITE_SYSTEM_PROMPT,
        {"context": test_context},
        response_contract=TEST_SUITE_RESPONSE,
        model_name=model_name,
        effort=None,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        on_progress=on_progress,
    )


@language_scoped
def run_multi_agent_specialist(
    role,
    payload,
    *,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
):
    """Call one advisory P9 specialist; its JSON is validated by Supervisor."""

    normalized_role = str(role or "").strip().lower()
    prompt = MULTI_AGENT_SPECIALIST_PROMPTS.get(normalized_role)
    if prompt is None:
        raise ValueError(f"Unsupported multi-agent specialist role: {role}")
    context = payload.get("context") if isinstance(payload, dict) else None
    if normalized_role in {'reviewer', 'timing_planner'}:
        from shared.model_context import technical_value
        payload = technical_value(payload)
        context = payload.get('context') if isinstance(payload, dict) else None
    if isinstance(context, dict):
        plc_model = str(
            ((context.get("plc") or {}).get("cpu")) or "FX3U"
        ).upper()
        retrieval_query = _build_knowledge_query(
            normalized_role,
            context.get("request"),
            context.get("networks"),
            context.get("logic"),
            context.get("timing"),
            context.get("deterministic_analysis"),
            context.get("local_report"),
        )
        prompt += _build_knowledge_context(
            retrieval_query,
            plc_model=plc_model,
            task_type="program_review",
            confirmed_context=context.get("confirmed_spec"),
        )
    return _call_debug_evidence_json(
        prompt,
        payload,
        response_contract=INSPECTION_RESPONSE,
        model_name=model_name,
        effort=None,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
        prefer_stream=True,
    )


def _strict_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "是"}:
            return True
        if normalized in {"false", "0", "no", "否", ""}:
            return False
    return False


def _string_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _int_list(value):
    result = []
    if not isinstance(value, list):
        value = [value] if value is not None else []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


@language_scoped
def inspect_ladder(
    report_type,
    request,
    current_version_json,
    local_report,
    *,
    plc_model="FX3U",
    confirmed_spec=None,
    conversation_history=None,
    model_name=None,
    effort=None,
    request_timeout=None,
    raise_errors=False,
):
    """Return an AI inspection candidate for later strict local normalization."""
    if report_type == "fault_debug":
        report_type = "debug"
    if report_type not in {"program_review", "debug"}:
        raise ValueError(f"Unsupported inspection report type: {report_type}")
    config = load_full_config()
    model_name = model_name or _active_model_name(config)
    request_text = (
        json.dumps(request, ensure_ascii=False)
        if isinstance(request, (dict, list))
        else str(request or "")
    )
    workflow_prompt, _route = build_workflow_prompt(
        f"{plc_model}\n{request_text}",
        target_mode="ladder",
        forced_task="debug" if report_type == "debug" else "program_review",
    )
    knowledge_ctx = _build_knowledge_context(
        request_text,
        plc_model=plc_model,
        task_type=report_type,
        confirmed_context=confirmed_spec,
        evidence={"current_ladder_json": current_version_json,
                  **({'local_report': local_report} if report_type == 'debug' else {})},
    )
    system_prompt = _with_confirmed_context(
        INSPECTION_SYSTEM_PROMPT
        + f"\nSelected PLC model: {plc_model}\n"
        + _build_model_context(
            plc_model,
            confirmed_spec,
            compact=bool(knowledge_ctx),
        )
        + f"Inspection type: {report_type}\n"
        + workflow_prompt
        + knowledge_ctx,
        confirmed_spec,
    )
    payload = {
        "report_type": report_type,
        "plc_model": plc_model,
        "request": request,
        "local_report": local_report,
        "current_ladder_json": current_version_json,
    }
    if report_type != 'debug':
        from shared.model_context import technical_value
        payload = technical_value(payload)
    messages = _build_clean_messages(conversation_history or [], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }
    )
    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=None,
            stream=False,
            request_timeout=request_timeout,
            max_retries=0,
            response_contract=INSPECTION_RESPONSE,
        )
        raw = _clean_json_response(response.message.content)
        candidate = json.loads(raw)
        if not isinstance(candidate, dict):
            raise ValueError("inspection response must be a JSON object")
        return candidate
    except ResponseRejectedError:
        raise
    except Exception as error:
        print(f"inspection api request failed: {error}")
        if raise_errors:
            raise RuntimeError(f"Inspection API request failed: {error}") from error
        return None


def review_ladder(
    review_focus,
    current_version_json,
    local_report,
    **kwargs,
):
    return inspect_ladder(
        "program_review",
        {"review_focus": str(review_focus or "").strip()},
        current_version_json,
        local_report,
        **kwargs,
    )


@language_scoped
def repair_ladder_response(repair_payload, model_name, effort, *, mode,
                           on_reasoning_chunk=None, on_content_chunk=None):
    """One explicit repair request that bypasses normal generation context."""
    if mode not in {"field_patch", "partial", "format"}:
        raise ValueError("Unsupported ladder repair mode")
    if not isinstance(repair_payload, dict):
        raise TypeError("repair_payload must be an object")
    repair_payload = dict(repair_payload)
    if mode == "format":
        # Compatibility name only: the model is never allowed to rewrite the
        # whole ladder. Deterministic recovery runs first, then at most a small
        # syntax-only before/after patch is requested and applied locally.
        from application.format_patch_repair import format_repair_response
        return format_repair_response(
            repair_payload, model_name, None,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
        )
    if mode == "partial":
        plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"
        baseline_tokens = _repair_baseline_tokens(repair_payload)
        app_instr_instances = [
            {"opcode": item["opcode"], "operands": list(item["operands"])}
            for item in baseline_tokens["app_instr_instances"]
        ]
        repair_payload["repair_contract"] = {
            "plc_model": plc_model,
            "semantic_policy": "copy_only",
            "app_instr_opcode_enum": sorted({item["opcode"] for item in app_instr_instances}),
            "app_instr_instances": app_instr_instances,
            "app_instr_forbidden_typed_opcodes": sorted(GENERATION_TYPED_OUTPUT_OPCODES),
            "dedicated_output_types": ["COIL", "PLS", "PLF", "TIMER", "COUNTER"],
        }
    if mode == "field_patch":
        native_response_format = _native_field_patch_response_format(repair_payload)
        system_prompt = FIELD_PATCH_REPAIR_SYSTEM_PROMPT
        response_contract = FIELD_PATCH_RESPONSE
        audit_reason = "explicit_field_repair"
    elif mode == "partial":
        native_response_format = _native_partial_repair_response_format(repair_payload)
        system_prompt = PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT
        response_contract = LADDER_RESPONSE
        audit_reason = "explicit_local_repair"
    audit_section("repair_system_prompt", system_prompt, reason=audit_reason, source="api")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False, separators=(",", ":"))},
    ]
    response = _request_model(
        messages, model_name=model_name, effort=None, stream=True,
        options={"response_format": native_response_format} if native_response_format else None,
        response_contract=response_contract,
        preserved_annotations=source_annotations(repair_payload),
        on_reasoning_chunk=on_reasoning_chunk, on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    return response.message.reasoning, response.message.content


def _native_ladder_generation_options(plc_model, *, allow_partial=False):
    """Use native JSON Schema only when the v3 model contract declares it."""
    provider = _workflow_provider()
    profile = getattr(provider, "profile", None)
    if not isinstance(profile, dict):
        return None
    from model_runtime.runtime_profile import materialize_runtime_profile
    try:
        runtime = materialize_runtime_profile(
            profile,
            api_key=getattr(provider, "api_key", None),
        )
    except (TypeError, ValueError):
        return None
    capability = runtime.contract.capabilities.get("structured_output")
    if not (
        capability
        and capability.status in {"supported", "conditional"}
        and "json_schema" in capability.modes
        and capability.source in {"metadata", "catalog", "manual", "legacy"}
    ):
        return None

    selected_model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    schema = ladder_response_schema(
        allow_partial=bool(allow_partial),
        plc_model=selected_model,
    )
    name = "ladder_candidate"
    if allow_partial:
        # Ordinary edit generation prefers a partial candidate. Use that branch
        # directly so providers do not have to support a top-level oneOf.
        schema = json.loads(json.dumps(schema["oneOf"][1]))
        schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]
        name = "ladder_partial_candidate"
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": True,
                "schema": schema,
            },
        }
    }


@language_scoped
def stream_model_response(user_requirement, model_name, effort, target_mode,
                             on_reasoning_chunk=None, on_content_chunk=None,
                             is_edit_mode=False, conversation_history=None,
                             confirmed_context=None, persist_history=None,
                             task_type=None, review_mode=None,
                             confirmed_spec=None,
                             current_version_json=None,
                             plc_model=None,
                             image_attachments=None, on_fallback=None, on_generation_context=None):
    """
    通过当前 ModelProvider 流式调用模型，实时返回工程推理摘要。

    参数:
        on_reasoning_chunk(token: str) — 收到推理内容片段时回调
        on_content_chunk(token: str)   — 收到输出内容片段时回调

    返回:
        (full_reasoning: str, full_content: str)
    """
    print(f"思考中(流式)... (模型配置参数, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, None, target_mode,
        is_edit_mode=is_edit_mode,
        conversation_history=conversation_history,
        confirmed_context=confirmed_context,
        confirmed_spec=confirmed_spec,
        persist_history=persist_history,
        task_type=task_type,
        review_mode=review_mode,
        current_version_json=current_version_json,
        plc_model=plc_model,
        image_attachments=image_attachments,
        on_generation_context=on_generation_context,
    )

    native_options = (
        _native_ladder_generation_options(plc_model, allow_partial=is_edit_mode)
        if target_mode == "ladder" else None
    )
    response = _request_model(
        messages,
        model_name=model_name,
        effort=None,
        stream=True,
        max_retries=0,
        fallback_to_non_stream=True,
        on_fallback=on_fallback,
        options=native_options,
        response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
        preserved_annotations=source_annotations(current_version_json, confirmed_spec, confirmed_context),
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
    )
    full_reasoning = response.message.reasoning
    full_content = response.message.content

    # ---------- 保存历史（含思考过程） ----------
    conversation_history.append({
        "role": "assistant",
        "content": full_content,
        "reasoning": full_reasoning
    })

    return full_reasoning, full_content


@language_scoped
def generate_model_json(user_requirement: str, model_name: str, effort: str,
                                   target_mode: str, is_edit_mode: bool = False,
                                   conversation_history=None,
                                   confirmed_context=None,
                                   confirmed_spec=None,
                                   persist_history=None,
                                   request_timeout=None,
                                   max_retries=None,
                                   raise_errors=False,
                                   task_type=None,
                                   review_mode=None,
                                   current_version_json=None,
                                   plc_model=None,
                                   image_attachments=None) -> str:
    print(f"思考中... (模型配置参数, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, None, target_mode,
        is_edit_mode=is_edit_mode,
        conversation_history=conversation_history,
        confirmed_context=confirmed_context,
        confirmed_spec=confirmed_spec,
        persist_history=persist_history,
        task_type=task_type,
        review_mode=review_mode,
        current_version_json=current_version_json,
        plc_model=plc_model,
        image_attachments=image_attachments,
    )

    native_options = (
        _native_ladder_generation_options(plc_model, allow_partial=is_edit_mode)
        if target_mode == "ladder" else None
    )
    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=None,
            stream=False,
            options=native_options,
            request_timeout=request_timeout,
            max_retries=max_retries,
            response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
            preserved_annotations=source_annotations(current_version_json, confirmed_spec, confirmed_context),
        )
        assistant_message = response.message

        conversation_history.append({
            "role": "assistant",
            "content": assistant_message.content
        })

        raw_content = assistant_message.content.strip()
        if raw_content.startswith("```"):
            raw_content = raw_content.split("\n", 1)[1]
        if raw_content.endswith("```"):
            raw_content = raw_content.rsplit("\n", 1)[0]
        return raw_content.strip()

    except ResponseRejectedError:
        raise
    except Exception as e:
        print(f"api 接入失败: {e}")
        if raise_errors:
            raise RuntimeError(f"API request failed: {e}") from e
        return ""
