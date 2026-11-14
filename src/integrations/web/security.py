# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Loopback HTTP boundary and credentials for the Agent API."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import Request
from starlette.responses import JSONResponse


class LocalSecurity:
    def __init__(self, origin: str, agent_token: str | None = None):
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path not in ("", "/"):
            raise ValueError("The web workbench only supports an explicit loopback HTTP origin")
        self.origin = origin.rstrip("/")
        self.host = parsed.netloc
        self.agent_token = agent_token

    async def middleware(self, request: Request, call_next):
        def denied(code, message):
            return JSONResponse({"error": {"code": code, "message": message}}, status_code=403)
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
