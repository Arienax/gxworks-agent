"""Canonical model-message rendering for generation context budgeting.

This module owns only the model-visible message envelope. Engineering projection,
prompt policy and retrieval remain with their existing owners.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence

from knowledge.evidence import estimate_tokens


def render_wire_messages(system_prompt, history=()):
    """Return the canonical system/user/assistant messages sent to a provider."""
    messages = [{"role": "system", "content": str(system_prompt or "")}]
    for raw in history or ():
        if not isinstance(raw, Mapping):
            continue
        role = raw.get("role")
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": str(raw.get("content", ""))})
    return messages


def normalize_wire_packet(value):
    """Detach and validate a renderer result without interpreting its content."""
    messages = value.get("messages") if isinstance(value, Mapping) else value
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise TypeError("wire renderer must return model messages")
    normalized = []
    for item in messages:
        if not isinstance(item, Mapping):
            raise TypeError("wire message must be an object")
        role = str(item.get("role") or "")
        if role not in {"system", "user", "assistant"}:
            raise ValueError("unsupported wire message role")
        normalized.append({"role": role, "content": str(item.get("content", ""))})
    if not normalized or normalized[0]["role"] != "system":
        raise ValueError("generation wire must start with a system message")
    return {"messages": normalized}


def wire_payload_text(packet):
    normalized = normalize_wire_packet(packet)
    return json.dumps(
        normalized["messages"],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def wire_token_estimate(packet):
    return estimate_tokens(wire_payload_text(packet))


def wire_sha256(packet):
    return hashlib.sha256(wire_payload_text(packet).encode("utf-8")).hexdigest()


def detached_wire_packet(packet):
    return copy.deepcopy(normalize_wire_packet(packet))
