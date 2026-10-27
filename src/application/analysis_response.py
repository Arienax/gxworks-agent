# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""One response validation and bounded repair flow for all analysis transports."""
import json
from dataclasses import replace
from application.response_contracts import (
    ANALYSIS_RESPONSE, ANALYSIS_REPAIR_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, FIELD_PATCH_RESPONSE,
    INSPECTION_RESPONSE, LADDER_RESPONSE, PATCH_RESPONSE, ST_RESPONSE, TEST_SUITE_RESPONSE,
)
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
from shared.context_audit import audit_request, audit_section
from application.analysis_results import (
    AnalysisProtocolError,
    _ANALYSIS_IO_KINDS,
    _ASSUMPTION_MARKERS,
    _iter_analysis_text,
    _normalize_analysis_result,
    validate_current_analysis_protocol,
)

def _analysis_json_payload(raw):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    return json.loads(text.strip())


def _validate_fresh_analysis_content(raw, *, contract_stage="bound", user_text="", plc_model="FX3U", confirmed_spec=None):
    from application.analysis_results import prepare_analysis_payload, analysis_grounding_details
    payload = _analysis_json_payload(raw)
    payload = prepare_analysis_payload(payload, contract_stage=contract_stage, user_text=user_text,
                                       confirmed_spec=confirmed_spec)
    validate_current_analysis_protocol(payload)
    details = analysis_grounding_details(payload, user_text, plc_model=plc_model, confirmed_spec=confirmed_spec)
    if details:
        raise AnalysisProtocolError([d["path"] + ": " + d["message"] for d in details], details=details)
    return payload


def _parse_analysis_response(raw, plc_model="FX3U", user_text="", confirmed_spec=None, *, contract_stage="bound"):
    result = _validate_fresh_analysis_content(raw, contract_stage=contract_stage, user_text=user_text, plc_model=plc_model, confirmed_spec=confirmed_spec)
    return _normalize_analysis_result(result, plc_model, user_text, confirmed_spec)


def request_analysis_response(messages, *, request_model, provider_scope, on_format_repair=None, contract_stage="bound", user_text="", plc_model="FX3U", confirmed_spec=None, allow_repair=True, **kwargs):
    """One shared repair budget, private patch output, Core-validated assembly."""
    from application.analysis_repair import (
        SYNTAX_SYSTEM_PROMPT, apply_analysis_repair, assembled_response,
        plan_analysis_repair, repair_messages,
    )
    from model_runtime.provider import ReasoningDelta, TextDelta, private_response_scope
    if not user_text:
        for message in reversed(messages):
            role = message.get("role") if isinstance(message, dict) else ("user" if isinstance(message, UserMessage) else "")
            if role == "user":
                user_text = str(message.get("content") if isinstance(message, dict) else message.content)
                break
    callbacks = {name: kwargs.pop(name, None) for name in ("on_content_chunk", "on_reasoning_chunk", "on_event")}

    def publish(response):
        _validate_fresh_analysis_content(response.message.content, contract_stage=contract_stage, user_text=user_text,
                                        plc_model=plc_model, confirmed_spec=confirmed_spec)
        from model_runtime.responses import inspect_response
        request = ModelRequest.from_messages(messages, response_contract=ANALYSIS_RESPONSE)
        if request.enforce_response_language:
            violations = inspect_response(response.message.content, request.response_language, ANALYSIS_RESPONSE,
                                          source_texts=(user_text,))
            if violations:
                raise ResponseRejectedError(request, response.raw_attempts, violations)
        # Provider deltas from both attempts remain private until Core accepts.
        events = []
        if response.message.reasoning:
            events.append(ReasoningDelta(response.message.reasoning))
        events.append(TextDelta(response.message.content))
        if response.usage:
            events.append(response.usage)
        for event in events:
            if callbacks["on_event"]:
                callbacks["on_event"](event)
            if isinstance(event, TextDelta) and callbacks["on_content_chunk"]:
                callbacks["on_content_chunk"](event.text)
            if isinstance(event, ReasoningDelta) and callbacks["on_reasoning_chunk"]:
                callbacks["on_reasoning_chunk"](event.text)
        return response

    with provider_scope():
        first_attempts = ()
        plan = None

        try:
            first = request_model(
                messages, response_contract=ANALYSIS_RESPONSE, **kwargs
            )
        except ResponseRejectedError as rejected:
            if not allow_repair:
                raise
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
                location = (
                    f"line {syntax_error.lineno}, column {syntax_error.colno}"
                )
            else:
                raise rejected
            first_attempts = rejected.raw_attempts
            repair_context = [{"role": "system", "content": SYNTAX_SYSTEM_PROMPT},
                              UserMessage(json.dumps({"parser_error": location, "rejected_draft": rejected.raw_response.message.content}, ensure_ascii=False))]
            repair_contract = ANALYSIS_RESPONSE
        else:
            first_attempts = first.raw_attempts
            plan = plan_analysis_repair(_analysis_json_payload(first.message.content), user_text, plc_model=plc_model,
                                        confirmed_spec=confirmed_spec, contract_stage=contract_stage)
            if not plan["targets"]:
                return publish(assembled_response(first, plan["base"], plan["receipt"]) if plan["receipt"]["local"] else first)
            if not allow_repair:
                try:
                    return publish(assembled_response(first, plan["base"], plan["receipt"]))
                except (AnalysisProtocolError, ResponseRejectedError) as error:
                    error.raw_attempts = first.raw_attempts
                    error.raw_response = first.message
                    raise
            repair_context = repair_messages(plan, user_text)
            repair_contract = ANALYSIS_REPAIR_RESPONSE

        if on_format_repair is not None:
            on_format_repair()
        audit_section("analysis_repair", "\n\n".join(str(m.get("content", "")) if isinstance(m, dict) else str(m.content) for m in repair_context),
                      reason="analysis_local_repair", source="analysis_repair")
        try:
            with private_response_scope():
                repaired = request_model(
                    repair_context,
                    response_contract=repair_contract,
                    **kwargs,
                )
        except ModelProviderError as error:
            error.raw_attempts = (*first_attempts, *error.raw_attempts)
            raise

        combined_attempts = (*first_attempts, *repaired.raw_attempts)
        repair_message = repaired.message
        try:
            if plan is not None:
                payload, receipt = apply_analysis_repair(plan, _analysis_json_payload(repaired.message.content), user_text,
                                                        plc_model=plc_model, confirmed_spec=confirmed_spec)
                repaired = assembled_response(repaired, payload, receipt, combined_attempts)
            else:
                repaired = replace(repaired, raw_attempts=combined_attempts)
            return publish(repaired)
        except (AnalysisProtocolError, ResponseRejectedError) as error:
            error.raw_attempts = combined_attempts
            if isinstance(error, AnalysisProtocolError):
                error.raw_response = repair_message
            raise
