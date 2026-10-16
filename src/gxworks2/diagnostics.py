"""Structured diagnostics for read-side GX Works2 synchronization."""

from __future__ import annotations

import errno
from typing import Any, Dict, Optional

from .models import GXSyncErrorCode


def _safe_repr(value: BaseException) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__} repr unavailable>"


def describe_exception(error: BaseException) -> str:
    """Return a useful description even when an exception has no message."""

    try:
        message = str(error).strip()
    except Exception:
        message = ""
    if message:
        return message
    return f"{type(error).__name__} ({_safe_repr(error)})"


def _root_exception(error: BaseException) -> BaseException:
    if isinstance(error, GXAutomationError) and error.original_error is not None:
        return error.original_error
    return error


def exception_details(error: Optional[BaseException]) -> Dict[str, str]:
    if error is None:
        return {
            "exception_type": "",
            "exception_repr": "",
            "exception_message": "",
        }
    root = _root_exception(error)
    return {
        "exception_type": type(root).__name__,
        "exception_repr": _safe_repr(root),
        "exception_message": describe_exception(root),
    }


class GXAutomationError(RuntimeError):
    """An automation failure tagged with a stable sync code and stage."""

    def __init__(
        self,
        code: GXSyncErrorCode,
        stage: str,
        message: str = "",
        *,
        retryable: bool = False,
        original_error: Optional[BaseException] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        resolved = str(message or "").strip()
        if not resolved and original_error is not None:
            resolved = describe_exception(original_error)
        if not resolved:
            resolved = code.value
        super().__init__(resolved)
        self.code = code
        self.stage = str(stage or "ui_automation")
        self.retryable = bool(retryable)
        self.original_error = original_error
        self.details = dict(details or {})


def _exception_chain(error: BaseException):
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        if isinstance(current, GXAutomationError) and current.original_error is not None:
            current = current.original_error
        else:
            current = current.__cause__ or current.__context__


def is_access_denied_error(error: BaseException) -> bool:
    for item in _exception_chain(error):
        if isinstance(item, PermissionError):
            return True
        if getattr(item, "winerror", None) == 5:
            return True
        if getattr(item, "errno", None) in {errno.EACCES, errno.EPERM}:
            return True
    return False


def is_timeout_error(error: BaseException) -> bool:
    for item in _exception_chain(error):
        if isinstance(item, TimeoutError):
            return True
        if type(item).__name__.casefold() in {"timeouterror", "timeout"}:
            return True
    return False


def classify_automation_error(
    error: BaseException,
    *,
    default_code: GXSyncErrorCode,
    stage: str,
    retryable: bool,
    message: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> GXAutomationError:
    if isinstance(error, GXAutomationError):
        return error
    code = default_code
    may_retry = bool(retryable)
    if is_access_denied_error(error):
        code = GXSyncErrorCode.GX_UIA_ACCESS_DENIED
        may_retry = False
    elif is_timeout_error(error):
        code = GXSyncErrorCode.GX_UIA_TIMEOUT
        may_retry = True
    return GXAutomationError(
        code,
        stage,
        message or describe_exception(error),
        retryable=may_retry,
        original_error=error,
        details=details,
    )


__all__ = [
    "GXAutomationError",
    "classify_automation_error",
    "describe_exception",
    "exception_details",
    "is_access_denied_error",
    "is_timeout_error",
]
