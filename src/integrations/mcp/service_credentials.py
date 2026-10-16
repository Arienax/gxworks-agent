"""Private local MCP service discovery for the product launcher.

The Web process publishes only a loopback origin, an unprivileged Agent token
and an optional project binding. On Windows this record lives in Credential
Manager; it is never exposed through the browser API or project workspace.
Environment variables remain the explicit fallback for CI/headless use.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


SERVICE_CREDENTIAL_TARGET = "GXWorks-Agent/mcp-service/v1"
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_windows() -> bool:
    return os.name == "nt"


def _service_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("MCP service discovery only accepts a loopback HTTP origin")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid local MCP service port") from error
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _project(value: Any) -> str | None:
    if value in (None, ""):
        return None
    value = str(value)
    if not _PROJECT_ID.fullmatch(value):
        raise ValueError("Invalid MCP project binding")
    return value


def _record(service_url: str, token: str, project_id: str | None = None) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token or "\r" in token or "\n" in token:
        raise ValueError("Invalid MCP agent credential")
    return {
        "version": 1,
        "service_url": _service_url(service_url),
        "agent_token": token,
        "project_id": _project(project_id),
    }


def persistent_store_available() -> bool:
    return _is_windows()


def save_service_binding(service_url: str, token: str, project_id: str | None = None) -> bool:
    """Persist the current local service for the same Windows logon session."""
    record = _record(service_url, token, project_id)
    if not _is_windows():
        return False
    from storage.windows_credentials import write_secret

    write_secret(json.dumps(record, ensure_ascii=False, separators=(",", ":")), SERVICE_CREDENTIAL_TARGET)
    return True


def load_service_binding() -> dict[str, Any] | None:
    if not _is_windows():
        return None
    from storage.windows_credentials import read_secret

    raw = read_secret(SERVICE_CREDENTIAL_TARGET).strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        return _record(value.get("service_url"), value.get("agent_token"), value.get("project_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def bind_project(project_id: str) -> dict[str, Any]:
    current = load_service_binding()
    if current is None:
        raise RuntimeError("Local MCP credential is not available")
    current["project_id"] = _project(project_id)
    save_service_binding(current["service_url"], current["agent_token"], current["project_id"])
    return {"service_url": current["service_url"], "project_id": current["project_id"]}


def clear_service_binding(service_url: str, token: str) -> None:
    """Delete only the record owned by this exact Web process."""
    if not _is_windows():
        return
    current = load_service_binding()
    if not current:
        return
    if current["service_url"] != _service_url(service_url) or current["agent_token"] != str(token).strip():
        return
    from storage.windows_credentials import delete_secret

    delete_secret(SERVICE_CREDENTIAL_TARGET)
