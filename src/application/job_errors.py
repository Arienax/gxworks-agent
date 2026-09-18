"""Bounded response-acceptance diagnostics without response text or credentials."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Mapping

from application.generation_repair import REPAIR_REASONS, GenerationValidationError
from application.compact_protocol import CompactProtocolError


_REASONS = frozenset({"unsupported_script", "non_english_script", "japanese_script", "latin_prose",
                      "ambiguous_han_only", "invalid_prose_field", "invalid_json_object", "invalid_code_field"}) | REPAIR_REASONS
_CONTRACTS = frozenset({"text", "analysis", "ladder", "st", "debug", "diagnosis", "patch", "inspection",
                       "test_suite", "tool_candidate", "tool_patch", "compact_ladder"})
_MAX_VIOLATIONS = 16


@lru_cache(maxsize=1)
def _schema_segments():
    from response_language import ResponseContract
    import workflow_response_contracts
    result = {"ladder", "patch", "arguments", "comment", "shared_inputs", "header_element", "inputs", "outputs", "branches", "type", "rung_id", "branch_id", "y_offset_level", "address", "expression", "opcode", "operands", "value", "mode", "delete_rung_ids", "confirmed_spec", "selected_approach", "networks"}
    result.update({"r", "h", "s", "b", "i", "o", "or"})
    for contract in vars(workflow_response_contracts).values():
        if isinstance(contract, ResponseContract):
            for selector in contract.human_paths + contract.st_paths + contract.annotation_paths + contract.structured_paths:
                result.update(segment for segment in selector.split(".") if segment not in ("*", "**"))
    return frozenset(result)


def _diagnostic_path(value):
    """Keep schema locations, masking wildcard keys supplied by a model.

    A JSON object key can itself be a secret or a filesystem path. A lexical
    identifier check alone therefore is not sufficient for a public location.
    """
    if not isinstance(value, str) or len(value) > 4096:
        return "response"
    match = re.match(r"^(content|reasoning|tool_calls\[[0-9]{1,6}\]\.arguments)(\$?)(.*)$", value)
    if not match:
        return "response"
    prefix, dollar, tail = match.groups()
    if not tail:
        return prefix + dollar
    if not tail.startswith("."):
        return prefix + dollar + ".*"
    segments = []
    for segment in tail[1:].split(".")[:24]:
        if segment in _schema_segments() or re.fullmatch(r"[0-9]{1,6}", segment):
            segments.append(segment)
        elif re.fullmatch(r"comment\[[0-9]{1,6}\]", segment):
            segments.append(segment)
        else:
            segments.append("*")
    return prefix + dollar + "." + ".".join(segments)


def _diagnostic_opcode(value):
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_.$@+\-]{1,64}", token):
        return None
    lowered = token.lower()
    if any(marker in lowered for marker in ("sk-", "bearer", "secret", "private", "api_key", "token", "password")):
        return None
    return token



def public_error_details(value):
    """Reproject persisted diagnostics as well as newly classified failures."""
    if not isinstance(value, Mapping):
        return None
    language = value.get("response_language")
    contract = value.get("contract_name")
    digest = value.get("diagnostic_id")
    violations = value.get("violations")
    violations = violations if isinstance(violations, (list, tuple)) else ()
    rows = []
    for violation in violations[:_MAX_VIOLATIONS]:
        if isinstance(violation, Mapping):
            reason = violation.get("reason")
            row = {"path": _diagnostic_path(violation.get("path")),
                   "reason": reason if isinstance(reason, str) and reason in _REASONS else "invalid_response"}
            observed_opcode = _diagnostic_opcode(violation.get("observed_opcode"))
            if observed_opcode is not None:
                row["observed_opcode"] = observed_opcode
            rows.append(row)
    count = value.get("violation_count", len(violations))
    if isinstance(count, bool) or not isinstance(count, int):
        count = len(violations)
    count = max(len(rows), min(count, 1000000))
    result = {"response_language": language if isinstance(language, str) and language in ("zh-CN", "en", "ja") else "unknown",
            "contract_name": contract if isinstance(contract, str) and contract in _CONTRACTS else "custom",
            "diagnostic_id": digest if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{16}", digest) else None,
            "violations": rows, "violation_count": count, "truncated": count > len(rows)}
    if value.get("stage") == "generation_validation":
        def bounded_number(key):
            item = value.get(key, 0)
            return min(3, max(0, item)) if isinstance(item, int) and not isinstance(item, bool) else 0
        stop = value.get("stop_reason")
        result.update(stage="generation_validation", attempt_count=bounded_number("attempt_count"),
                      max_attempts=bounded_number("max_attempts"),
                      stop_reason=stop if stop in ("attempt_limit", "time_budget", "final_validation") else "attempt_limit")
    return result


def acceptance_error_details(error):
    """Recognize a real acceptance exception, including workflow wrappers."""
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        if type(error).__name__ == "ResponseRejectedError":
            from model_provider import ResponseRejectedError
            if isinstance(error, ResponseRejectedError):
                digest = getattr(error, "response_sha256", "")
                violations = getattr(error, "violations", ())
                violations = violations if isinstance(violations, (list, tuple)) else ()
                return public_error_details({
                    "response_language": error.response_language, "contract_name": error.contract_name,
                    "diagnostic_id": digest[:16] if isinstance(digest, str) else None,
                    "violation_count": len(violations),
                    "violations": [{"path": getattr(item, "path", None), "reason": getattr(item, "reason", None)}
                                   for item in violations[:_MAX_VIOLATIONS]],
                })
        error = error.__cause__ or error.__context__
    return None


def generation_error_details(error):
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        if isinstance(error, GenerationValidationError):
            return public_error_details(error.diagnostics)
        if isinstance(error, CompactProtocolError):
            from i18n import get_language
            return public_error_details({
                "response_language": get_language(), "contract_name": "compact_ladder",
                "diagnostic_id": error.diagnostic_id,
                "violations": [{"path": error.path, "reason": error.reason}],
                "stage": "generation_validation", "attempt_count": 0, "max_attempts": 0,
                "stop_reason": "final_validation",
            })
        error = error.__cause__ or error.__context__
    return None


def workflow_error_code(error):
    """Classify only known exceptions; never expose SDK messages/attributes."""
    from model_provider import ModelProviderError
    from application.generation_repair import GenerationError
    seen, generation = set(), False
    while isinstance(error, BaseException) and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        generation |= isinstance(error, (GenerationError, CompactProtocolError))
        if isinstance(error, ModelProviderError):
            code = error.code
            allowed = {"authentication", "rate_limit", "timeout", "invalid_request", "unavailable",
                       "protocol", "image_not_supported", "image_payload_too_large", "provider_error"}
            return "model_" + (code if isinstance(code, str) and code in allowed else "provider_error")
        error = error.__cause__ or error.__context__
    return "generation_failed" if generation else None
