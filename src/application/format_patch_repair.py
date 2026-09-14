"""Syntax-only repair for rejected ladder responses.

The model never owns the repaired program on this path.  Deterministic recovery
runs first.  If bytes are still ambiguous, the model may propose only short,
exact text substitutions; local code applies them to the immutable raw response
and then expands the complete candidate through the ordinary generation path.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from response_language import ResponseContract


FORMAT_PATCH_RESPONSE = ResponseContract("ladder_format_patch", "json")

FORMAT_PATCH_SYSTEM_PROMPT = """# PLC ladder JSON local format patch
You repair JSON syntax only. The backend owns the complete rejected candidate.
You must NOT return the repaired candidate, ladder, rungs, branches, PLC logic,
or any large copied section of the input.

Return exactly one small JSON patch object:
{"schema_version":1,"mode":"format_patch","base_sha256":"...","patches":[{"before":"short exact broken substring","after":"short exact corrected substring"}]}

Rules:
- Copy base_sha256 exactly.
- Each patch is one exact local substring replacement. `before` must be copied
  byte-for-byte from the rejected candidate and should be as short as possible.
- Change JSON punctuation/quoting/escaping only. Never change letters, digits,
  addresses, opcodes, operands, values, contact polarity, labels or comments.
- Do not add, delete, rewrite or reorder rungs or PLC behavior.
- Maximum 24 patches; each before/after string must be at most 240 characters.
- Never output the full repaired JSON, even if it would be easier.
- No markdown or explanation.
"""

_CANDIDATE_MARKERS = (
    "失败候选 JSON：\n",
    "失败候选 JSON:\n",
    "失败候选 JSON：",
    "失败候选 JSON:",
)
_JSON_SYNTAX = re.compile(r"[\s{}\[\]\"':,\\]+")


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    return text.strip()


def candidate_from_payload(repair_payload: dict[str, Any]) -> str:
    """Extract the immutable raw candidate from the existing repair command."""
    explicit = repair_payload.get("candidate_text")
    if isinstance(explicit, str) and explicit.strip():
        return _clean(explicit)
    instruction = str(repair_payload.get("instruction") or "")
    for marker in _CANDIDATE_MARKERS:
        index = instruction.rfind(marker)
        if index >= 0:
            value = _clean(instruction[index + len(marker):])
            if value:
                return value
    raise ValueError("format repair candidate is missing")


def _semantic_skeleton(text: str) -> str:
    """Ignore JSON punctuation only; every engineering token must stay identical."""
    return _JSON_SYNTAX.sub("", text)


def apply_format_patch(candidate: str, patch: Any) -> str:
    """Apply bounded syntax-only substitutions to one immutable response."""
    raw = _clean(candidate)
    if not isinstance(patch, dict) or set(patch) != {
        "schema_version", "mode", "base_sha256", "patches"
    }:
        raise ValueError("format patch has invalid top-level fields")
    if patch.get("schema_version") != 1 or patch.get("mode") != "format_patch":
        raise ValueError("format patch protocol mismatch")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if patch.get("base_sha256") != digest:
        raise ValueError("format patch base_sha256 mismatch")
    changes = patch.get("patches")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 24:
        raise ValueError("format patch requires 1..24 local substitutions")

    result = raw
    structural_edits = 0
    for index, item in enumerate(changes):
        if not isinstance(item, dict) or set(item) != {"before", "after"}:
            raise ValueError(f"format patch {index} has invalid fields")
        before, after = item.get("before"), item.get("after")
        if not isinstance(before, str) or not isinstance(after, str) or not before:
            raise ValueError(f"format patch {index} requires strings")
        if len(before) > 240 or len(after) > 240:
            raise ValueError(f"format patch {index} is too large")
        if _semantic_skeleton(before) != _semantic_skeleton(after):
            raise ValueError(f"format patch {index} changes PLC/content tokens")
        if result.count(before) != 1:
            raise ValueError(f"format patch {index} must target exactly one substring")
        structural_edits += abs(len(after) - len(before)) + sum(
            left != right for left, right in zip(before, after)
        )
        if structural_edits > 192:
            raise ValueError("format patch changes too many syntax characters")
        result = result.replace(before, after, 1)
    return result


def _strict_ladder(candidate: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a complete ladder only; partial diagnostic salvage is forbidden here."""
    from application.rejected_generation_preview import _repair_compact_json

    raw = _clean(candidate)
    repaired, repair_count = _repair_compact_json(raw)
    attempts = [raw]
    if repaired != raw:
        attempts.append(repaired)
    last_error: Exception | None = None
    for text in attempts:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as error:
            last_error = error
            continue
        if not isinstance(parsed, dict):
            last_error = ValueError("candidate must be one JSON object")
            continue
        if isinstance(parsed.get("rungs"), list):
            return parsed, {
                "source_format": "ladder_v1",
                "syntax_repairs": repair_count if text == repaired else 0,
            }
        if isinstance(parsed.get("r"), list):
            try:
                from application.generation_agent import _expand_compact_ladder
                ladder = _expand_compact_ladder(parsed, {})
            except Exception as error:
                last_error = error
                continue
            return ladder, {
                "source_format": "compact_ladder",
                "syntax_repairs": repair_count if text == repaired else 0,
            }
        last_error = ValueError("candidate is neither ladder_v1 nor compact ladder")
    if last_error is not None:
        raise last_error
    raise ValueError("candidate cannot be recovered")


def _patch_schema(base_sha256: str) -> dict[str, Any]:
    local = {
        "type": "object",
        "properties": {
            "before": {"type": "string", "minLength": 1, "maxLength": 240},
            "after": {"type": "string", "maxLength": 240},
        },
        "required": ["before", "after"],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ladder_format_patch",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "schema_version": {"type": "integer", "enum": [1]},
                    "mode": {"type": "string", "enum": ["format_patch"]},
                    "base_sha256": {"type": "string", "enum": [base_sha256]},
                    "patches": {
                        "type": "array", "minItems": 1, "maxItems": 24,
                        "items": local,
                    },
                },
                "required": ["schema_version", "mode", "base_sha256", "patches"],
                "additionalProperties": False,
            },
        },
    }


def format_repair_response(
    repair_payload,
    model_name,
    effort,
    *,
    on_reasoning_chunk=None,
    on_content_chunk=None,
):
    """Return a full local candidate while allowing the model to emit only a patch.

    The return shape intentionally matches ``api.repair_ladder_response`` so the
    existing GenerationWorkflow can keep its validation/rendering pipeline.
    If the patch request itself fails, return the original raw candidate. That
    forces the ordinary parser to record a GenerationValidationError, which in
    turn preserves and renders the rejected candidate instead of losing it.
    """
    raw = candidate_from_payload(repair_payload)

    # Most recurring compact-response damage is an unambiguous missing quote.
    # Recover it without spending another model call.
    try:
        ladder, _info = _strict_ladder(raw)
        return "", json.dumps(ladder, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    base_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    parser_error = ""
    try:
        json.loads(raw)
    except json.JSONDecodeError as error:
        parser_error = f"{error.msg} at line {error.lineno}, column {error.colno}, char {error.pos}"

    request = {
        "schema_version": 1,
        "mode": "format_patch",
        "base_sha256": base_sha,
        "parser_error": parser_error,
        "rejected_candidate": raw,
    }
    try:
        import api

        response = api._request_model(
            [
                {"role": "system", "content": FORMAT_PATCH_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False, separators=(",", ":"))},
            ],
            model_name=model_name,
            effort=effort,
            stream=True,
            max_retries=0,
            options={"response_format": _patch_schema(base_sha)},
            response_contract=FORMAT_PATCH_RESPONSE,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
            fallback_to_non_stream=True,
        )
        patch = json.loads(_clean(response.message.content))
        patched = apply_format_patch(raw, patch)
        ladder, _info = _strict_ladder(patched)
        return response.message.reasoning, json.dumps(
            ladder, ensure_ascii=False, separators=(",", ":")
        )
    except Exception:
        # Do not convert a failed patch request into a provider/job failure. The
        # original candidate is more valuable: GenerationWorkflow will persist
        # it and the diagnostic renderer will still make SVG/CSV available.
        return "", raw
