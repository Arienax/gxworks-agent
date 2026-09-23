"""Durable, monotonically numbered job events."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from .workspace import public_payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_context_audit(value) -> dict:
    """A dedicated metadata projection; never widen the generic event whitelist."""
    def count(item):
        if type(item) is not int or not 0 <= item <= 2**53 - 1:
            raise ValueError("Invalid context audit count")
        return item

    def digest(item):
        if not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item):
            raise ValueError("Invalid context audit digest")
        return item

    def identifier(item):
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", item):
            raise ValueError("Invalid context audit identifier")
        return item

    try:
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("Invalid context audit schema")
        messages, sections = value["messages"], value["sections"]
        if not isinstance(messages, list) or not isinstance(sections, list) or len(messages) > 4096 or len(sections) > 256:
            raise ValueError("Invalid context audit rows")
        result = {
            "schema_version": 1,
            "request_index": count(value["request_index"]),
            "message_text_chars": count(value["message_text_chars"]),
            "dropped_sections": count(value["dropped_sections"]),
            "measurement": "characters_not_tokens",
            "section_counts_are_additive": False,
            "messages": [], "sections": [],
        }
        for item in messages:
            role = item["role"]
            if role not in ("system", "developer", "user", "assistant", "tool", "unknown"):
                raise ValueError("Invalid role")
            result["messages"].append({"role": role, "text_chars": count(item["text_chars"]),
                                       "images": count(item["images"]), "sha256": digest(item["sha256"])})
        for item in sections:
            result["sections"].append({**{key: identifier(item[key]) for key in ("section", "source", "status", "reason")},
                                        "chars": count(item["chars"]), "sha256": digest(item["sha256"])})
        return result
    except (KeyError, TypeError, ValueError):
        return {"schema_version": 1, "status": "unavailable", "reason": "invalid_context_audit"}


def append_event(record: dict, event_type: str, data=None) -> dict:
    payload = _public_context_audit(data) if event_type == "context_audit" else public_payload(data or {})
    event = {
        "job_id": record["id"],
        "project_id": record.get("snapshot", {}).get("project_id"),
        "version_id": record.get("snapshot", {}).get("version_id") or record.get("snapshot", {}).get("base_version_id"),
        "sequence": int(record.get("last_sequence", 0)) + 1,
        "event_type": str(event_type)[:80],
        "created_at": utc_now(),
        "payload": payload,
    }
    record.setdefault("events", []).append(event)
    record["last_sequence"] = event["sequence"]
    record["updated_at"] = event["created_at"]
    return event
