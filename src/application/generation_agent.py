"""Agent B: generate one complete ladder candidate from the confirmed specification.

Requirement analysis, grilling, clarification and specification confirmation remain in
Agent A elsewhere in the application. This module deliberately has no access to the
original requirement conversation. The confirmed specification is the only engineering
interface between the two agents.
"""
from __future__ import annotations

import copy
import json

from plc_generation_context import build_generation_instructions, public_generation_specification
from plc_generation_contract import ladder_response_schema
from response_language import preserved_annotations
from workflow_response_contracts import LADDER_RESPONSE


_GENERATION_REQUEST = (
    "根据已经由用户确认的规格生成完整 ladder_v1 JSON。"
    "不得重新分析需求、提出问题或改变已确认 I/O、触点极性、参数和所选方案。"
    "只返回最终梯形图 JSON。"
)


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


def generate_confirmed_ladder(
    confirmed_spec,
    plc_model="FX3U",
    *,
    model_name=None,
    effort=None,
    on_stage=None,
):
    """Make exactly one model request for one complete ladder_v1 candidate."""
    import api

    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = public_generation_specification(confirmed_spec) or {}
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

    provider = api._workflow_provider()
    response = api._request_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _GENERATION_REQUEST},
        ],
        model_name=model_name,
        effort=effort,
        stream=False,
        max_retries=0,
        options=_response_options(model, provider),
        response_contract=LADDER_RESPONSE,
        preserved_annotations=preserved_annotations(projected),
    )
    return {"ladder": _json_object(response.message.content), "model_calls": 1}
