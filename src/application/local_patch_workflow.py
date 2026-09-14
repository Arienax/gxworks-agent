"""One explicit model call for a path-addressed ladder repair."""
from __future__ import annotations

import json

from application.base import model_call
from application.field_repair import apply, build_payload, request_patch
from plc_generation import prepare_ladder_candidate
from plc_json_validator import PLCJsonValidationError


def repair_candidate(
    baseline,
    violations,
    allowed_rung_ids,
    allowed_addresses,
    *,
    plc_model="FX3U",
    program_name="MAIN",
    revision=1,
    confirmed_spec=None,
    provider=None,
    model_name=None,
    effort=None,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
    injected_response=None,
):
    """Request one local patch, apply it, and validate the complete candidate."""
    payload = build_payload(
        baseline, violations, allowed_rung_ids, allowed_addresses, plc_model
    )
    if injected_response is None:
        _reasoning, content = model_call(
            request_patch,
            payload,
            provider,
            model_name,
            effort,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
        )
    else:
        _reasoning, content = injected_response(payload)
    try:
        response = json.loads(str(content or "").strip())
    except (TypeError, ValueError) as error:
        raise PLCJsonValidationError("$: local repair response must be one JSON object") from error
    materialized = apply(
        baseline, response, allowed_rung_ids, allowed_addresses
    )
    prepared = prepare_ladder_candidate(
        materialized,
        plc_model=plc_model,
        program_name=program_name,
        revision=revision,
        confirmed_spec=confirmed_spec,
        previous_ladder=None,
        repair_mode=False,
        task_type="contract_repair",
        on_progress=on_progress,
    )
    return payload, response, materialized, prepared
