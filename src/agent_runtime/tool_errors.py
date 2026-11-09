"""Actionable engineering preconditions shared by all tool transports."""
from __future__ import annotations


class ToolPreconditionError(ValueError):
    def __init__(self, code, message, *, next_actions=(), details=None):
        super().__init__(message)
        self.code = code
        self.next_actions = list(next_actions)
        self.details = dict(details or {})

    def payload(self):
        return {"code": self.code, "message": str(self),
                "next_actions": self.next_actions, **self.details}


PROJECT_ACTIONS = ({"tool": "list_projects"}, {"tool": "list_gxworks2_sessions"})
SOURCE_ACTIONS = ({"tool": "get_current_program_info"}, {"tool": "list_gxworks2_sessions"})
