"""Conservative transport selection; never infer retry permission from prose.

A timeout/connection loss can occur after paid work has started. Changing stream
mode is allowed only after an explicit stream rejection with no response events.
"""
from collections.abc import Mapping
import re

STREAM_REJECTION_STATUSES = frozenset({400, 405, 422})
STREAM_FALLBACK_CODES = frozenset({"stream_not_supported"})


def is_explicit_stream_rejection(error):
    """Recognize a structured server rejection, not an arbitrary HTTP 400."""
    if getattr(error, "status_code", None) not in STREAM_REJECTION_STATUSES:
        return False
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return False
    detail = body.get("error", body)
    if not isinstance(detail, Mapping):
        return False
    code, parameter = detail.get("code"), detail.get("param")
    return (code in {"stream_not_supported", "streaming_not_supported"}
            or (parameter == "stream" and code == "unsupported_parameter"))


def can_fallback_to_non_stream(error, *, had_events=False):
    """No replay after content, reasoning, tool events, usage or opaque state."""
    status = getattr(error, "status_code", None)
    return (not had_events
            and getattr(error, "code", None) in STREAM_FALLBACK_CODES
            and (status is None or status in STREAM_REJECTION_STATUSES))


def preferred_streaming(profile, *, model=None, api_key=None):
    """Honor the materialized streaming capability before sending a request."""
    from model_runtime.runtime_profile import RuntimeModelProfile, materialize_runtime_profile

    if isinstance(profile, RuntimeModelProfile):
        runtime = profile
    elif isinstance(profile, Mapping):
        try:
            runtime = materialize_runtime_profile(
                profile, api_key=api_key, model=model
            )
        except (TypeError, ValueError):
            return True
    else:
        return True
    descriptor = runtime.contract.capabilities.get("streaming")
    return descriptor is None or descriptor.status != "unsupported"


def parameter_error(error, name):
    """Classify a named parameter rejection without exposing provider text."""
    status = getattr(error, "status_code", None)
    if status not in (400, 422):
        return "unknown"
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return "unknown"
    body = body.get("error", body)
    if not isinstance(body, Mapping):
        return "unknown"
    parameter = str(body.get("param") or body.get("parameter") or "")
    message = str(body.get("message") or "").lower()
    mentions_parameter = parameter.split(".")[-1] == name or bool(
        re.search(r"(?<![a-z_])" + re.escape(name) + r"(?![a-z_])", message)
    )
    if not mentions_parameter:
        return "unknown"
    code = str(body.get("code") or body.get("type") or "").lower()
    if code in {"unsupported_parameter", "unknown_parameter", "unrecognized_parameter"}:
        return "unsupported"
    return "rejected"
