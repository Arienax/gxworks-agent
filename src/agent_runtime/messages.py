"""Transport-neutral tool messages shared by model providers and runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    data: Mapping[str, Any] = field(default_factory=dict)
    is_error: bool = False


__all__ = ["ToolCall", "ToolResult"]
