"""Detached retrieval provenance alongside the backward-compatible text API."""
from __future__ import annotations

import copy
import hashlib


def text_sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class KnowledgeContext(str):
    """Behaves as prompt text; callers can explicitly persist its manifest."""

    def __new__(cls, text="", manifest=None):
        result = super().__new__(cls, text)
        result.manifest = copy.deepcopy(manifest or {})
        return result


def context_manifest(context, *, stage="generate", status=None):
    result = copy.deepcopy(getattr(context, "manifest", {}))
    result.setdefault("stage", stage)
    result.setdefault("status", status or ("untracked_text" if context else "not_recorded"))
    result.setdefault("records", [])
    result["context_sha256"] = text_sha256(context)
    return result


def evidence_record(result):
    # Metadata from the index, not the model, supplies source identity. Preserve
    # unknown manual types honestly rather than upgrading everything to official.
    record = {key: copy.deepcopy(result[key]) for key in (
        "id", "source", "manual_id", "manual_number", "revision", "manual_type",
        "chunk_type", "instruction_opcode", "section", "page", "page_end", "pdf_page",
    ) if key in result}
    kind = result.get("manual_type", "")
    record["role"] = ("design_reference" if kind == "curated_design" else
                      "supporting_reference" if kind == "third_party_skill" else "technical_reference")
    record["content_sha256"] = text_sha256(result.get("text", ""))
    return record


class KnowledgeQuery(str):
    def __new__(cls, text="", truncated=False):
        result = super().__new__(cls, text)
        result.truncated = bool(truncated)
        return result
