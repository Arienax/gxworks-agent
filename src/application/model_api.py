from application.analysis_context import assemble_analysis_prompt

import copy
import os
import json
import re
import sys
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from shared.i18n import language_scoped, tr
from model_runtime.responses import TEXT_RESPONSE, preserved_annotations as source_annotations
from application.response_contracts import (
    ANALYSIS_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, FIELD_PATCH_RESPONSE,
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
from shared.context_policy import (
    audit_request, audit_section, context_policy_scope, manual_lookup_decision,
    resolve_context_policy, select_base_prompt,
)
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
from application.analysis_results import _ANALYSIS_IO_KINDS, _ASSUMPTION_MARKERS, _iter_analysis_text, _normalize_analysis_result




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
        with context_policy_scope():
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

    request_options = dict(options or {})
    if effort is not None:
        request_options["reasoning_effort"] = effort
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
        _workflow_provider(),
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
        messages, model_name=model_name, effort=effort, stream=stream, tools=tools,
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
    input_upper = user_input.upper()
    models = _load_plc_models()
    for model in models:
        if model == "default":
            continue
        if model.upper() in input_upper:
            return model
    config = load_full_config()
    return config.get("plc_model", models.get("default", "FX3U"))


def _resolve_plc_model(user_input="", confirmed_context=None, explicit_model=None):
    """Resolve the selected project model before consulting global config."""
    models = _load_plc_models()
    if explicit_model:
        candidate = str(explicit_model).strip().upper()
        if candidate in models:
            return candidate
    if isinstance(confirmed_context, dict):
        candidate = str(confirmed_context.get("plc_model", "")).strip().upper()
        if candidate in models:
            return candidate
    return _detect_plc_model(str(user_input or ""))








def _parse_analysis_response(raw, plc_model="FX3U", user_text="", confirmed_spec=None):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    result = json.loads(text.strip())
    return _normalize_analysis_result(result, plc_model, user_text, confirmed_spec)


def _request_analysis_response(messages, *, on_format_repair=None, **kwargs):
    """Allow one syntax correction of an unconfirmed analysis draft only.

    Keep the shared collector strict. Language/field rejection, transport
    errors and valid JSON with the wrong root type are not repair signals.
    Neither attempt publishes content until its normal acceptance succeeds.
    """
    with provider_scope():
        try:
            return _request_model(messages, response_contract=ANALYSIS_RESPONSE, **kwargs)
        except ResponseRejectedError as rejected:
            if [(v.path, v.reason) for v in rejected.violations] != [
                ("content", "invalid_json_object")
            ]:
                raise
            raw = rejected.raw_response.message.content.strip()
            if raw.startswith("```") and raw.endswith("```") and "\n" in raw:
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if not raw.startswith("{"):
                raise
            try:
                json.loads(raw)
            except json.JSONDecodeError as syntax_error:
                location = f"line {syntax_error.lineno}, column {syntax_error.colno}"
            else:
                raise rejected
            if on_format_repair is not None:
                on_format_repair()
            correction = (
                "Your previous analysis draft is not valid JSON (" + location + "). "
                "Return the complete corrected JSON object only, using the analysis schema above. "
                "Correct JSON syntax and missing schema keys only; preserve the requirement, "
                "devices, alternatives and questions. Do not invent confirmed answers or generate PLC code. "
                "Use separate property names and values. No markdown or explanations."
            )
            repair_messages = [*messages, rejected.raw_response.message,
                               UserMessage(correction)]
            try:
                repaired = _request_model(repair_messages, response_contract=ANALYSIS_RESPONSE, **kwargs)
            except ResponseRejectedError as error:
                error.raw_attempts = (*rejected.raw_attempts, *error.raw_attempts)
                raise
            return replace(repaired, raw_attempts=(*rejected.raw_attempts, *repaired.raw_attempts))




@language_scoped
def analyze_requirement(
    user_requirement: str,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    task_type=None,
    image_attachments=None,
    on_format_repair=None,
    analysis_mode="direct",
) -> dict:
    """
    Phase 1: fast analysis with effort=low.
    Auto-detect PLC model from user input and inject special soft-element table.
    Returns: dict or None
    """
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
    print(f"阶段1: 需求分析中... (effort=low, PLC={model})")

    messages = _build_clean_messages(conversation_history or [], sys_prompt)
    messages.append(
        _user_message_with_images(user_requirement, image_attachments)
    )

    try:
        response = _request_analysis_response(
            messages,
            effort="low",
            stream=False,
            on_format_repair=on_format_repair,
        )
        raw = response.message.content.strip()

        # 清理 Markdown 包裹
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]
        raw = raw.strip()

        result = _parse_analysis_response(raw, model, user_requirement, confirmed_context)
        # Application metadata, not a mode selected by the model response.
        result["analysis_mode"] = "design" if analysis_prompt.route.include_design else "direct"
        from application.analysis_results import attach_analysis_evidence
        result = attach_analysis_evidence(result, knowledge_ctx, plc_model=model,
                                          knowledge_builder=_build_knowledge_context)
        print(f"阶段1 分析完成: {result.get('summary', '')[:80]}...")
        return result

    except ModelProviderError:
        raise
    except Exception as e:
        print(f"阶段1 分析失败: {e}")
        return None


@language_scoped
def analyze_requirement_streaming(
    user_requirement: str,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    task_type=None,
    image_attachments=None,
    on_format_repair=None,
    analysis_mode="direct",
):
    """
    阶段1 流式版：分析用户需求，实时显示思考过程。

    返回:
      dict: 分析结果 JSON，失败时返回 None
    """
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
    print(f"阶段1(流式): 需求分析中... (effort=low, PLC={model})")

    messages = _build_clean_messages(conversation_history or [], sys_prompt)
    messages.append(
        _user_message_with_images(user_requirement, image_attachments)
    )

    try:
        response = _request_analysis_response(
            messages,
            effort="low",
            stream=True,
            on_format_repair=on_format_repair,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
        )
        full_content = response.message.content

        # 解析返回的 JSON
        raw = full_content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]
        raw = raw.strip()

        result = _parse_analysis_response(raw, model, user_requirement, confirmed_context)
        # Application metadata, not a mode selected by the model response.
        result["analysis_mode"] = "design" if analysis_prompt.route.include_design else "direct"
        from application.analysis_results import attach_analysis_evidence
        result = attach_analysis_evidence(result, knowledge_ctx, plc_model=model,
                                          knowledge_builder=_build_knowledge_context)
        print(f"阶段1 分析完成: {result.get('summary', '')[:80]}...")
        return result

    except ModelProviderError:
        raise
    except Exception as e:
        print(f"阶段1 流式分析失败: {e}")
        return None


HISTORY_FILE = "chat_history.json"
CONFIRMED_CONTEXT_FILE = "confirmed_requirements.json"


def save_confirmed_context(context):
    """Persist the latest user-confirmed specification for the current session."""
    payload = {"context": str(context or "").strip()}
    with open(CONFIRMED_CONTEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_confirmed_context():
    if not os.path.exists(CONFIRMED_CONTEXT_FILE):
        return ""
    try:
        with open(CONFIRMED_CONTEXT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return str(payload.get("context", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("已重置为全新对话。")
            return []
    return []

def _save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def _build_clean_messages(conversation_history, system_prompt):
    """构建发送给模型的消息列表，仅保留用户可见正文。"""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in conversation_history:
        role = msg.get("role")
        if role not in {"user", "assistant"}:
            continue
        clean = {"role": role, "content": str(msg.get("content", ""))}
        messages.append(clean)
    return messages




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
    共用准备逻辑：加载历史、追加用户消息、保存、选取系统提示词、
    构建清洗后的统一消息列表。
    返回 (messages, conversation_history, persist_history)
    """
    if conversation_history is None:
        conversation_history = []
    else:
        conversation_history = [dict(item) for item in conversation_history]
    if persist_history is None:
        persist_history = False
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
    if persist_history:
        _save_history(conversation_history)

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
    )
    messages_to_send = _build_clean_messages(conversation_history, system_prompt)
    if image_attachments:
        messages_to_send[-1] = _user_message_with_images(
            user_requirement,
            image_attachments,
        )

    return messages_to_send, conversation_history, persist_history


@language_scoped
def debug_ladder(
    user_question,
    current_version_json,
    confirmed_spec=None,
    conversation_history=None,
    local_findings=None,
    model_name=None,
    effort="high",
    request_timeout=120,
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
            effort=effort,
            stream=False,
            response_contract=DEBUG_RESPONSE,
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
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
    effort="high",
    request_timeout=120,
    raise_errors=False,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
    response_contract=INSPECTION_RESPONSE,
):
    config = load_full_config()
    selected_model = model_name or _active_model_name(config)
    messages = _build_clean_messages([], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }
    )
    try:
        wants_stream = any(
            callback is not None
            for callback in (on_reasoning_chunk, on_content_chunk, on_progress)
        )
        if wants_stream and on_progress:
            on_progress(str(tr("AI 正在生成仿真测试方案（流式）")))
        response = _request_model(
            messages,
            model_name=selected_model,
            effort=effort,
            stream=wants_stream,
            response_contract=response_contract,
            preserved_annotations=source_annotations(payload),
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
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
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Ask the model for a diagnosis; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_DIAGNOSIS_SYSTEM_PROMPT,
        {"evidence": evidence},
        response_contract=DIAGNOSIS_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


@language_scoped
def debug_evidence_patch(
    evidence,
    diagnosis,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Ask the model for a local patch; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_PATCH_SYSTEM_PROMPT,
        {"evidence": evidence, "diagnosis": diagnosis},
        response_contract=PATCH_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )




@language_scoped
def generate_simulator_test_suite(
    test_context,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
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
        effort=effort,
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
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Call one advisory P9 specialist; its JSON is validated by Supervisor."""

    normalized_role = str(role or "").strip().lower()
    prompt = MULTI_AGENT_SPECIALIST_PROMPTS.get(normalized_role)
    if prompt is None:
        raise ValueError(f"Unsupported multi-agent specialist role: {role}")
    context = payload.get("context") if isinstance(payload, dict) else None
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
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
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
    effort="high",
    request_timeout=120,
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
        evidence={
            "local_report": local_report,
            "current_ladder_json": current_version_json,
        },
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
            effort=effort,
            stream=False,
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
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







def _repair_baseline_tokens(repair_payload):
    """Collect immutable engineering tokens already present in the repair slice."""
    result = {"addresses": set(), "operands": set(), "values": set(),
              "expressions": set(), "app_instr_arities": set(),
              "app_instr_instances": []}

    def walk(value):
        if isinstance(value, dict):
            address = value.get("address")
            if isinstance(address, str) and address.strip():
                result["addresses"].add(address.strip().upper())
            expression = value.get("expression")
            if isinstance(expression, str) and expression.strip():
                result["expressions"].add(expression.strip())
            preset = value.get("value")
            if isinstance(preset, str) and preset.strip():
                result["values"].add(preset.strip())
            operands = value.get("operands")
            if isinstance(operands, list):
                tokens = [str(item).strip() for item in operands if isinstance(item, str) and str(item).strip()]
                result["operands"].update(tokens)
                if value.get("type") == "APP_INSTR":
                    result["app_instr_arities"].add(len(operands))
                    opcode = str(value.get("opcode") or "").strip().upper()
                    if opcode:
                        instance = {"opcode": opcode, "operands": list(tokens)}
                        if instance not in result["app_instr_instances"]:
                            result["app_instr_instances"].append(instance)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(repair_payload.get("baseline_subset") or {})
    result["addresses"].update(
        str(item).strip().upper()
        for item in (repair_payload.get("allowed_addresses") or [])
        if str(item).strip()
    )
    return result


def _constrain_native_repair_schema(schema, repair_payload, plc_model):
    """Prevent a local repair from inventing devices/parameters outside its baseline."""
    tokens = _repair_baseline_tokens(repair_payload)
    addresses = sorted(tokens["addresses"])
    operands = sorted(tokens["operands"])
    values = sorted(tokens["values"])
    expressions = sorted(tokens["expressions"])
    arities = sorted(tokens["app_instr_arities"])
    app_instr_instances = list(tokens["app_instr_instances"])
    baseline_opcodes = sorted({item["opcode"] for item in app_instr_instances})

    def visit(rule):
        if isinstance(rule, list):
            for child in rule:
                visit(child)
            return
        if not isinstance(rule, dict):
            return

        properties = rule.get("properties")
        if isinstance(properties, dict):
            type_rule = properties.get("type")
            type_values = set(type_rule.get("enum", [])) if isinstance(type_rule, dict) else set()
            if "address" in properties and addresses:
                properties["address"]["enum"] = addresses
            if "expression" in properties and expressions:
                properties["expression"]["enum"] = expressions
            if "value" in properties and values:
                properties["value"]["enum"] = values
            if "APP_INSTR" in type_values:
                opcode_rule = properties.get("opcode")
                operand_rule = properties.get("operands")
                if isinstance(opcode_rule, dict):
                    # Structural repair is copy-only: the model may reuse only
                    # APP_INSTR opcodes already present in baseline_subset.
                    opcode_rule["enum"] = baseline_opcodes
                if isinstance(operand_rule, dict):
                    if operands:
                        # ladder_v1_schema reuses the generic token rule for
                        # TIMER/COUNTER values and APP_INSTR operands. Detach
                        # the operand item rule before adding an enum so this
                        # local repair constraint cannot mutate timer presets.
                        operand_rule["items"] = dict(operand_rule.get("items") or {})
                        operand_rule["items"]["enum"] = operands
                    if len(arities) == 1:
                        operand_rule["minItems"] = arities[0]
                        operand_rule["maxItems"] = arities[0]
            for child in properties.values():
                visit(child)
        for key in ("oneOf", "anyOf", "allOf"):
            visit(rule.get(key))
        visit(rule.get("items"))

    visit(schema)
    return schema


def _native_field_patch_response_format(repair_payload):
    target = repair_payload.get("target") if isinstance(repair_payload, dict) else None
    if not isinstance(target, dict) or not isinstance(target.get("path"), str):
        raise ValueError("field repair target is required")
    value_schema = json.loads(json.dumps(target.get("value_schema") or {}))
    if not value_schema:
        raise ValueError("field repair value schema is required")
    base_sha = str(repair_payload.get("base_sha256") or "")
    schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "mode": {"type": "string", "enum": ["field_patch"]},
            "base_sha256": {"type": "string", "enum": [base_sha]},
            "patches": {
                "type": "array", "minItems": 1, "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "enum": [target["path"]]},
                        "value": value_schema,
                    },
                    "required": ["path", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["schema_version", "mode", "base_sha256", "patches"],
        "additionalProperties": False,
    }
    return {"type": "json_schema", "json_schema": {
        "name": "ladder_field_patch", "strict": True, "schema": schema,
    }}


def _native_partial_repair_response_format(repair_payload):
    """Build the provider-enforced partial repair schema from the shared ladder contract."""
    plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"
    combined = ladder_response_schema(allow_partial=True, plc_model=plc_model)
    schema = combined["oneOf"][1]
    schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]
    schema["properties"]["delete_rung_ids"]["maxItems"] = 0

    allowed_rung_ids = sorted({
        int(item) for item in (repair_payload.get("allowed_rung_ids") or [])
        if not isinstance(item, bool)
    })
    rung_array = schema["properties"]["rungs"]
    if allowed_rung_ids:
        rung_array["minItems"] = 1
        rung_array["maxItems"] = len(allowed_rung_ids)
        rung_array["items"]["properties"]["rung_id"] = {
            "type": "integer", "enum": allowed_rung_ids,
        }
        # Structural rung repair must not opportunistically rewrite comments.
        schema["properties"]["device_comments"] = {
            "type": "object", "properties": {}, "required": [],
            "additionalProperties": False,
        }
        _constrain_native_repair_schema(schema, repair_payload, plc_model)
    else:
        rung_array["maxItems"] = 0
        allowed_addresses = sorted({
            str(item).strip().upper() for item in (repair_payload.get("allowed_addresses") or [])
            if str(item).strip()
        })
        comment_properties = {
            address: {"type": "string", "maxLength": 64}
            for address in allowed_addresses
        }
        schema["properties"]["device_comments"] = {
            "type": "object",
            "properties": comment_properties,
            "required": allowed_addresses,
            "additionalProperties": False,
        }

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ladder_partial_repair",
            "strict": True,
            "schema": schema,
        },
    }


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
            repair_payload, model_name, effort,
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
        messages, model_name=model_name, effort=effort, stream=True,
        options={"response_format": native_response_format} if native_response_format else None,
        response_contract=response_contract,
        preserved_annotations=source_annotations(repair_payload),
        on_reasoning_chunk=on_reasoning_chunk, on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    return response.message.reasoning, response.message.content


def _native_ladder_generation_options(plc_model, *, allow_partial=False):
    """Use the current ladder contract when the selected profile already uses native JSON Schema.

    Profiles that use json_object/text keep their existing transport behavior.
    This only replaces a persisted/native json_schema so its opcode enum cannot
    drift behind the registry enforced by final PLC validation.
    """
    provider = _workflow_provider()
    profile = getattr(provider, "profile", None)
    if not isinstance(profile, dict):
        return None
    response_format = None
    for key in ("generationDefaults", "requestOverrides"):
        source = profile.get(key)
        if isinstance(source, dict) and "response_format" in source:
            response_format = source.get("response_format")
    if not (isinstance(response_format, dict) and response_format.get("type") == "json_schema"):
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
    print(f"思考中(流式)... (当前模式: {effort}, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, effort, target_mode,
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
        effort=effort,
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
    if should_persist:
        _save_history(conversation_history)

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
    print(f"思考中... (当前模式: {effort}, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, effort, target_mode,
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
            effort=effort,
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
        if should_persist:
            _save_history(conversation_history)

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


def _deprecated_model_alias(name, target):
    """Keep one-release source compatibility without retaining old parsing."""

    def forward(*args, **kwargs):
        warnings.warn(
            f"api.{name} 已废弃；请改用 api.{target.__name__}。",
            DeprecationWarning,
            stacklevel=2,
        )
        return target(*args, **kwargs)

    forward.__name__ = name
    forward.__doc__ = f"Deprecated forwarding alias for {target.__name__}."
    forward.__deprecated__ = True
    return forward


# One-release source compatibility.  New application code uses only the
# vendor-neutral names; these wrappers are removed in the next release.
call_deepseek_analyze_requirement = _deprecated_model_alias(
    "call_deepseek_analyze_requirement", analyze_requirement
)
call_deepseek_analyze_streaming = _deprecated_model_alias(
    "call_deepseek_analyze_streaming", analyze_requirement_streaming
)
call_deepseek_debug_ladder = _deprecated_model_alias(
    "call_deepseek_debug_ladder", debug_ladder
)
call_deepseek_debug_evidence_diagnosis = _deprecated_model_alias(
    "call_deepseek_debug_evidence_diagnosis", debug_evidence_diagnosis
)
call_deepseek_debug_evidence_patch = _deprecated_model_alias(
    "call_deepseek_debug_evidence_patch", debug_evidence_patch
)
call_deepseek_generate_simulator_test_suite = _deprecated_model_alias(
    "call_deepseek_generate_simulator_test_suite", generate_simulator_test_suite
)
call_deepseek_multi_agent_specialist = _deprecated_model_alias(
    "call_deepseek_multi_agent_specialist", run_multi_agent_specialist
)
call_deepseek_inspection = _deprecated_model_alias(
    "call_deepseek_inspection", inspect_ladder
)
call_deepseek_review_ladder = _deprecated_model_alias(
    "call_deepseek_review_ladder", review_ladder
)
call_deepseek_streaming = _deprecated_model_alias(
    "call_deepseek_streaming", stream_model_response
)
call_deepseek_to_generate_json = _deprecated_model_alias(
    "call_deepseek_to_generate_json", generate_model_json
)
