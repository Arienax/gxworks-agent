# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Workspace-scoped execution consent, separate from validation and HTTP security.

This policy never exposes a new tool or relaxes PLC validators. Directly
requested local edits are saved with history; only existing external actions
are classified here. Delegation uses deterministic rules, not an invented AI
reviewer. Independent MCP local saves use the shared transaction; external
actions retain this policy. Saved-snapshot transports remain read-only.
"""
from __future__ import annotations

from .workspace import ConflictError, atomic_json, contained, read_json

MODES = frozenset({"ask", "auto", "full"})
EXTERNAL_ACTIONS = frozenset({"gx_import", "simulation", "debug", "native_engineering"})


def allows(mode, action, payload):
    if mode not in MODES or action not in EXTERNAL_ACTIONS:
        return False
    if not payload.get("project_id") or not payload.get("version_id"):
        return False
    if action in {"simulation", "debug", "native_engineering"} and not isinstance(payload.get("plan"), dict):
        return False
    return mode == "full" or (mode == "auto" and action in {"simulation", "debug"})


class ApprovalPolicy:
    def __init__(self, state_dir):
        self.state_dir = state_dir

    @property
    def path(self):
        return contained(self.state_dir / "approval-policy.json", self.state_dir)

    def read(self):
        if not self.path.is_file():
            return {"mode": "ask", "revision": 0}
        value = read_json(self.path)
        if not isinstance(value, dict) or value.get("mode") not in MODES or type(value.get("revision")) is not int or value["revision"] < 0:
            raise ValueError("Invalid approval settings; no automatic execution is allowed")
        return {"mode": value["mode"], "revision": value["revision"]}

    def update(self, *, mode, expected_revision, confirm_full_access=False):
        # Caller holds the workspace writer lock; ordinary reads never initialize.
        current = self.read()
        if mode not in MODES or type(expected_revision) is not int:
            raise ValueError("Unknown approval mode")
        if expected_revision != current["revision"]:
            raise ConflictError("Approval settings changed in another window")
        if mode == "full" and current["mode"] != "full" and confirm_full_access is not True:
            raise PermissionError("Explicit consent is required to enable full access")
        if mode != current["mode"]:
            current = {"mode": mode, "revision": current["revision"] + 1}
            atomic_json(self.path, current)
        return current
