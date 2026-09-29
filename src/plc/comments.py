"""PLC/GX device-comment policy.

Device comments are compact human-facing names, not a second copy of the
requirement.  Requirement prose stays in intent/semantic fields.  GX Works2
device comments are capped at 16 characters at every generated/exported
boundary so UI previews and CSV artifacts agree.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from plc.device_identity import canonical_device, canonical_device_map


GXWORKS2_DEVICE_COMMENT_MAX_CHARS = 16

_COMMENT_BREAK_RE = re.compile(r"[\r\n，,；;。]+")
_SURROUNDING_QUOTES = "\"'“”‘’「」『』"


def short_device_comment(value, *, max_chars=GXWORKS2_DEVICE_COMMENT_MAX_CHARS):
    """Return a compact device-purpose label without carrying behavior prose."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text.casefold() == "null":
        return ""
    text = text.strip(_SURROUNDING_QUOTES + " ")
    # A device comment is a name/purpose phrase. Everything after the first
    # natural clause separator belongs in intent/semantics, not in the comment.
    text = _COMMENT_BREAK_RE.split(text, maxsplit=1)[0].strip()
    text = text.strip(_SURROUNDING_QUOTES + " ")
    return text[:max(0, int(max_chars))]


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


__all__ = [
    "GXWORKS2_DEVICE_COMMENT_MAX_CHARS",
    "normalize_device_comments",
    "short_device_comment",
]
