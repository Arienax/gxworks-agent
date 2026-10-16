"""Loopback-only operator sessions and a separate, unprivileged Agent token."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request
from starlette.responses import JSONResponse


@dataclass
class OperatorSession:
    csrf: str
    expires: float


class LocalSecurity:
    def __init__(self, origin: str, operator_token: str, agent_token: str | None = None):
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path not in ("", "/"):
            raise ValueError("The web workbench only supports an explicit loopback HTTP origin")
        if not operator_token or (agent_token and secrets.compare_digest(agent_token, operator_token)):
            raise ValueError("Operator and Agent credentials must be separate")
        self.origin = origin.rstrip("/")
        self.host = parsed.netloc
        self.operator_token = operator_token
        self.agent_token = agent_token
        self.sessions: dict[str, OperatorSession] = {}

    def session(self, request: Request):
        key = request.cookies.get("gx_operator", "")
        session = self.sessions.get(key)
        if session and session.expires <= time.monotonic():
            self.sessions.pop(key, None)
            session = None
        return session

    def login(self, token: str):
        if not secrets.compare_digest(token, self.operator_token):
            raise PermissionError("Invalid operator credential")
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires > time.monotonic()}
        if len(self.sessions) >= 32:
            raise ValueError("Too many operator sessions")
        key, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[key] = OperatorSession(csrf, time.monotonic() + 12 * 3600)
        return key, csrf

    async def middleware(self, request: Request, call_next):
        def denied(code, message):
            return JSONResponse({"error": {"code": code, "message": message}}, status_code=403 if code != "authentication_required" else 401)
        if request.headers.get("host") != self.host:
            return denied("invalid_host", "Untrusted host")
        origin = request.headers.get("origin")
        if origin and origin != self.origin:
            return denied("invalid_origin", "Untrusted origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return denied("invalid_origin", "Cross-site requests are not accepted")
        if request.method == "OPTIONS":
            return denied("cors_disabled", "Cross-origin access is disabled")
        path = request.url.path
        if path.startswith("/api/agent/"):
            supplied = request.headers.get("authorization", "")
            if not self.agent_token or not secrets.compare_digest(supplied, "Bearer " + self.agent_token):
                return denied("agent_authentication_required", "Agent credential required")
            request.state.role = "agent"
        elif path.startswith("/api/") and path not in ("/api/health", "/api/session"):
            session = self.session(request)
            if not session:
                return denied("authentication_required", "Operator session required")
            if request.method not in ("GET", "HEAD"):
                if origin != self.origin or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
                    return denied("csrf_required", "Operator write protection failed")
            request.state.role = "operator"
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return denied("invalid_length", "Invalid content length")
            if length > 42 * 1024 * 1024:
                return JSONResponse({"error": {"code": "body_too_large", "message": "Request exceeds 42 MiB"}}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 42 * 1024 * 1024:
                    return JSONResponse({"error": {"code": "body_too_large", "message": "Request exceeds 42 MiB"}}, status_code=413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return response
