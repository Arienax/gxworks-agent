"""Conservative transport selection; never infer retry permission from prose.

A timeout/connection loss can occur after paid work has started. Changing stream
mode is allowed only after an explicit stream rejection with no response events.
"""
from collections.abc import Mapping

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
    """Honor a scoped unsupported flag before sending any request."""
    if not isinstance(profile, Mapping):
        return True
    from model_runtime.contract import scoped_contract
    contract = scoped_contract(profile, model, api_key)
    if contract is not None:
        descriptor = contract.capabilities.get("streaming")
        return descriptor is None or descriptor.status != "unsupported"
    if profile.get("capabilityContract"):
        return True  # Stale metadata must not select a transport for a new endpoint.
    return (profile.get("capabilities") or {}).get("streaming") is not False
