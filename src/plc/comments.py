# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""PLC/GX device-comment policy.

Device comments are compact human-facing names, not a second copy of the
requirement.  Requirement prose stays in intent/semantic fields.  GX Works2
device comments are capped at 16 characters at every generated/exported
boundary so UI previews and CSV artifacts agree.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from plc.device_identity import canonical_device, canonical_device_map


GXWORKS2_DEVICE_COMMENT_MAX_CHARS = 16

_COMMENT_BREAK_RE = re.compile(r"[\r\n：:，,；;。]+")
_CONDITION_CUE_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:OFF|ON)(?![A-Za-z])|→|高电平|低电平|每次|达到|接通|断开|清零|优先|进入RUN"
)
_SURROUNDING_QUOTES = "\"'“”‘’「」『』"


def device_purpose_label(value):
    """Extract the first human purpose phrase without applying GX length limits."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if not text or text.casefold() == "null":
        return ""
    text = text.strip(_SURROUNDING_QUOTES + " ")
    # Behavior after a natural clause separator belongs in intent/semantics.
    text = _COMMENT_BREAK_RE.split(text, maxsplit=1)[0].strip()
    return text.strip(_SURROUNDING_QUOTES + " ")


def short_device_comment(value, *, max_chars=GXWORKS2_DEVICE_COMMENT_MAX_CHARS):
    """Return the GX-safe view of one device-purpose label."""
    return device_purpose_label(value)[:max(0, int(max_chars))]


def normalize_device_comments(value):
    """Canonicalize a comment map and apply the single GX Works2 limit."""
    if not isinstance(value, Mapping):
        return {}
    comments = {}
    for address, raw_comment in value.items():
        identity = canonical_device(str(address or "").strip().upper())
        if not identity:
            continue
        # Preserve an explicit empty declaration as an owner so inferred labels
        # cannot silently refill it later.
        comment = short_device_comment(raw_comment)
        if identity not in comments:
            comments[identity] = comment
    return canonical_device_map(comments)


def confirmed_device_comments(specification):
    """Use confirmed I/O names as the source of generated device comments."""
    rows = specification.get("io_table", []) if isinstance(specification, Mapping) else []
    comments = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        address = str(row.get("address") or "").strip().upper()
        label = device_purpose_label(str(row.get("label") or row.get("description") or ""))
        if address and label and len(label) <= GXWORKS2_DEVICE_COMMENT_MAX_CHARS:
            comments[address] = label
    return canonical_device_map(comments)


def normalize_generated_device_comments(value, specification=None, *, include_confirmed=False):
    """Apply the compact API's confirmed-name source to full candidates too.

    Model comments are kept only when they are already short names. Explanatory
    text is left empty instead of guessing or cutting a sentence in the middle.
    Existing versions are handled separately and are never rewritten in place.
    """
    if not isinstance(value, Mapping):
        return value
    confirmed = confirmed_device_comments(specification)
    comments = {}
    for address, comment in value.items():
        identity = canonical_device(str(address or "").strip().upper())
        if identity in confirmed:
            comments[address] = confirmed[identity]
            continue
        if not isinstance(comment, str):
            comments[address] = comment  # The structure validator handles types.
            continue
        purpose = re.sub(r"\s+", " ", comment).strip().strip(_SURROUNDING_QUOTES + " ")
        comments[address] = (
            purpose if len(purpose) <= GXWORKS2_DEVICE_COMMENT_MAX_CHARS
            and not _COMMENT_BREAK_RE.search(purpose)
            and not _CONDITION_CUE_RE.search(purpose) else ""
        )
    if include_confirmed:
        comments.update(confirmed)
    return comments


__all__ = [
    "GXWORKS2_DEVICE_COMMENT_MAX_CHARS",
    "device_purpose_label",
    "confirmed_device_comments",
    "normalize_device_comments",
    "short_device_comment",
    "normalize_generated_device_comments",
]
