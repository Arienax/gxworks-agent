"""Detailed local runtime trace support for operator diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading

_MAX_FILE = 8 * 1024 * 1024
_MAX_STRING = 2 * 1024 * 1024
_LOCK = threading.RLock()

_SENSITIVE_FIELDS = {
    "api_key", "apikey", "password", "secret", "access_token", "refresh_token",
    "authorization", "credential", "credentials", "provider_configuration",
    "operator_token", "agent_token", "token", "cookie", "set_cookie", "proxy_authorization",
}
_BLOB_FIELDS = {"data_base64", "fbd_baseline", "image_base64"}
_KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9._-]{8,}", re.I)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_QUERY_PATTERN = re.compile(r"(?i)([?&](?:token|api_key|apikey|access_token)=)[^&#\s]+")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)


def _redact_text(value):
    value = _KEY_PATTERN.sub("<redacted-key>", value)
    value = _BEARER_PATTERN.sub(lambda match: match.group(1) + "<redacted-token>", value)
    value = _QUERY_PATTERN.sub(lambda match: match.group(1) + "<redacted-token>", value)
    if len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + "\n<diagnostic-text-truncated>"
    return value


def sanitize(value, *, key=None, depth=0):
    """Keep engineering/user text while excluding credentials and large binaries."""
    name = str(key or "").strip().lower().lstrip("_").replace("-", "_")
    if name in _SENSITIVE_FIELDS or name.endswith("_api_key") or name.endswith("_password"):
        return "<redacted>"
    if depth > 24:
        return "<diagnostic-depth-limit>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"binary_omitted": True, "size_bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, str):
        if name in _BLOB_FIELDS and len(value) > 4096:
            return {"blob_omitted": True, "chars": len(value)}
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k), depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth=depth + 1) for item in value]
    return _redact_text(str(value))


def _directory(state_dir):
    state = Path(state_dir).resolve()
    directory = state / "diagnostics"
    if directory.is_symlink() or (directory.exists() and directory.resolve().parent != state):
        raise ValueError("Unsafe diagnostic directory")
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    return directory


def _job_path(state_dir, job_id):
    if not _safe_id(job_id) or not job_id.startswith("job_"):
        raise ValueError("Invalid diagnostic job identifier")
    path = _directory(state_dir) / f"{job_id}.transcript.jsonl"
    if path.is_symlink():
        raise ValueError("Unsafe transcript path")
    return path


def _actions_path(state_dir):
    path = _directory(state_dir) / "operator-actions.jsonl"
    if path.is_symlink():
        raise ValueError("Unsafe action path")
    return path


def _append(path, value):
    data = (json.dumps(sanitize(value), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    with _LOCK:
        size = path.stat().st_size if path.exists() else 0
        if size >= _MAX_FILE or size + len(data) > _MAX_FILE:
            return False
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
    return True


def _message_record(message):
    role = {
        "SystemMessage": "system",
        "UserMessage": "user",
        "AssistantMessage": "assistant",
        "ToolResult": "tool",
    }.get(type(message).__name__, type(message).__name__)
    result = {"role": role, "content": sanitize(getattr(message, "content", None))}
    images = getattr(message, "images", ()) or ()
    if images:
        result["images"] = []
        for item in images:
            data = getattr(item, "data", b"") or b""
            result["images"].append({
                "filename": sanitize(getattr(item, "filename", "")),
                "media_type": sanitize(getattr(item, "media_type", "")),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    reasoning = getattr(message, "reasoning", "")
    if reasoning:
        result["reasoning"] = sanitize(reasoning)
    calls = getattr(message, "tool_calls", ()) or ()
    if calls:
        result["tool_calls"] = [{
            "id": sanitize(getattr(call, "id", "")),
            "name": sanitize(getattr(call, "name", "")),
            "arguments": sanitize(getattr(call, "arguments", None)),
        } for call in calls]
    call_id = getattr(message, "call_id", None)
    if call_id is not None:
        result["call_id"] = sanitize(call_id)
        result["name"] = sanitize(getattr(message, "name", ""))
        result["is_error"] = bool(getattr(message, "is_error", False))
    return result


def record_model_request(state_dir, job_id, request_index, attempt_index, request, provider):
    return _append(_job_path(state_dir, job_id), {
        "schema_version": 1,
        "event": "model_request",
        "timestamp": _now(),
        "job_id": job_id,
        "request_index": request_index,
        "attempt_index": attempt_index,
        "provider": type(provider).__name__,
        "model": getattr(request, "model", None),
        "stream": bool(getattr(request, "stream", False)),
        "timeout": getattr(request, "timeout", None),
        "max_retries": getattr(request, "max_retries", None),
        "response_language": getattr(request, "response_language", None),
        "response_contract": {
            "name": getattr(getattr(request, "response_contract", None), "name", None),
            "format": getattr(getattr(request, "response_contract", None), "format", None),
        },
        "options": sanitize(getattr(request, "options", {})),
        "tools": sanitize(getattr(request, "tools", ())),
        "messages": [_message_record(item) for item in getattr(request, "messages", ())],
    })


def record_model_response(state_dir, job_id, request_index, attempt_index, raw, request):
    message = getattr(raw, "message", None)
    usage = getattr(raw, "usage", None)
    return _append(_job_path(state_dir, job_id), {
        "schema_version": 1,
        "event": "model_response",
        "timestamp": _now(),
        "job_id": job_id,
        "request_index": request_index,
        "attempt_index": attempt_index,
        "stream": bool(getattr(raw, "stream", False)),
        "error_code": sanitize(getattr(raw, "error_code", "")),
        "contract": getattr(getattr(request, "response_contract", None), "name", None),
        "content": sanitize(getattr(message, "content", "")),
        "reasoning": sanitize(getattr(message, "reasoning", "")),
        "tool_calls": sanitize([{
            "id": getattr(call, "id", ""),
            "name": getattr(call, "name", ""),
            "arguments": getattr(call, "arguments", None),
        } for call in (getattr(message, "tool_calls", ()) or ())]),
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
            "raw_usage": sanitize(getattr(usage, "raw_usage", None)),
        } if usage is not None else None,
    })


def record_operator_action(state_dir, action, *, project_id=None, job_id=None, payload=None, result=None):
    if not isinstance(action, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,80}", action):
        raise ValueError("Invalid operator action")
    return _append(_actions_path(state_dir), {
        "schema_version": 1,
        "event": "operator_action",
        "timestamp": _now(),
        "action": action,
        "project_id": project_id if _safe_id(project_id) else None,
        "job_id": job_id if _safe_id(job_id) else None,
        "payload": sanitize(payload),
        "result": sanitize(result),
    })


def _read_jsonl(path, predicate=None):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_FILE:
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict) and (predicate is None or predicate(value)):
                rows.append(sanitize(value))
    return rows


def load_transcript(state_dir, job_id):
    return _read_jsonl(_job_path(state_dir, job_id))


def load_operator_actions(state_dir, *, project_id=None, job_id=None, limit=512):
    def relevant(value):
        if job_id and value.get("job_id") == job_id:
            return True
        if project_id and value.get("project_id") == project_id:
            return True
        return not project_id and not job_id
    return _read_jsonl(_actions_path(state_dir), relevant)[-max(1, int(limit)):]
