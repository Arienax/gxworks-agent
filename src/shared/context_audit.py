"""Privacy-safe prompt/context diagnostics with no runtime policy ownership."""
from __future__ import annotations

import copy
import hashlib
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_audit: ContextVar[Optional["ContextAudit"]] = ContextVar(
    "plc_prompt_context_audit", default=None
)


class ContextAudit:
    """Metadata-only request/context diagnostics.

    The collector never changes prompt assembly, retrieval, model parameters,
    or acceptance behavior. It stores counts and hashes rather than prompt text.
    """

    def __init__(
        self, on_request: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> None:
        self._sections: List[Dict[str, Any]] = []
        self._requests: List[Dict[str, Any]] = []
        self._on_request = on_request
        self._dropped = 0

    def add(
        self,
        section: str,
        text: str,
        *,
        status: str,
        reason: str,
        source: str,
    ) -> None:
        for identifier in (section, status, reason, source):
            if not isinstance(identifier, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,120}", identifier
            ):
                raise ValueError("Audit labels must be fixed, bounded identifiers")
        if len(self._sections) >= 256:
            self._dropped += 1
            return
        self._sections.append(
            {
                "section": section,
                "source": source,
                "status": status,
                "reason": reason,
                "chars": len(text),
                "sha256": _digest(text),
            }
        )

    def request(self, messages: Any) -> Dict[str, Any]:
        summary: List[Dict[str, Any]] = []
        for message in messages:
            if isinstance(message, Mapping):
                role, content = message.get("role"), message.get("content")
                images = ()
            else:
                role, content = (
                    getattr(message, "role", None),
                    getattr(message, "content", None),
                )
                images = getattr(message, "images", ()) or ()
            role = (
                role
                if role in ("system", "developer", "user", "assistant", "tool")
                else "unknown"
            )
            text_parts: List[str] = []
            image_count = len(images) if isinstance(images, (list, tuple)) else 0
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, (list, tuple)):
                for part in content:
                    if isinstance(part, Mapping):
                        if part.get("type") in ("text", "input_text") and isinstance(
                            part.get("text"), str
                        ):
                            text_parts.append(part["text"])
                        elif part.get("type") in ("image_url", "input_image"):
                            image_count += 1
            text = "".join(text_parts)
            summary.append(
                {
                    "role": role,
                    "text_chars": len(text),
                    "images": image_count,
                    "sha256": _digest(text),
                }
            )
        report = {
            "schema_version": 1,
            "request_index": len(self._requests) + 1,
            "messages": summary,
            "message_text_chars": sum(x["text_chars"] for x in summary),
            "sections": [dict(x) for x in self._sections],
            "dropped_sections": self._dropped,
            "measurement": "characters_not_tokens",
            "section_counts_are_additive": False,
        }
        self._sections.clear()
        self._dropped = 0
        self._requests.append(report)
        if self._on_request:
            try:
                self._on_request(copy.deepcopy(report))
            except Exception:
                print("Context audit sink unavailable", file=sys.stderr)
        return self.snapshot()["requests"][-1]

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(
            {
                "requests": self._requests,
                "pending_sections": self._sections,
                "dropped_sections": self._dropped,
            }
        )


@contextmanager
def context_audit_scope(
    audit: Optional[ContextAudit] = None,
) -> Iterator[Optional[ContextAudit]]:
    token = _audit.set(audit if audit is not None else _audit.get())
    try:
        yield audit
    finally:
        _audit.reset(token)


def audit_section(
    section: str,
    text: str = "",
    *,
    status: str = "included",
    reason: str = "required",
    source: str = "application",
) -> None:
    collector = _audit.get()
    if collector is not None:
        collector.add(
            section, text, status=status, reason=reason, source=source
        )


def audit_retrieval_fragment(
    result: Mapping[str, Any], block: str, *, included: bool = True
) -> None:
    if _audit.get() is None:
        return

    def identifier(value: Any, fallback: str) -> str:
        text = str(value) if isinstance(value, (str, int)) else ""
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", text):
            return text
        return fallback + ("_" + _digest(text)[:16] if text else "")

    source = identifier(result.get("manual_id"), "unknown_source")
    chunk = identifier(result.get("id", result.get("chunk_id")), "unknown_chunk")
    audit_section(
        "manual_chunk:" + chunk,
        block,
        status="included" if included else "excluded",
        reason="retrieved_chunk" if included else "context_budget",
        source=source,
    )


def audit_request(messages: Any) -> None:
    collector = _audit.get()
    if collector is not None:
        collector.request(messages)
