"""Detached retrieval provenance alongside the backward-compatible text API."""
from __future__ import annotations

import copy
import hashlib
import math
import re

_CJK = re.compile(r"[\u3400-\u9fff]")


def is_navigation_table(text):
    """Recognize the extractor's rotated chapter index, never technical cells."""
    lines = str(text).strip().splitlines()
    if not lines or not lines[0].startswith("[TABLE"):
        return False
    rows = [line.strip().split("|") for line in lines[1:] if line.strip()]
    labels = {row[0].strip() for row in rows}
    navigation = {"Introduction", "Overview", "List Instruction", "Instruction List",
                  "Applied Instructions", "Basic Instructions", "Table of Contents"}
    chapter_ranges = sum(bool(re.search(r"\bF[MN]C\s*\d+\s*[-–]\s*F[MN]C\s*\d+\b", label, re.I))
                         for label in labels)
    return (len(rows) >= 6
            and all(len(row) == 2 and row[1].strip() == "<blank>" for row in rows)
            and (len(labels & navigation) >= 2
                 or chapter_ranges >= 2 and sum(label.isdigit() for label in labels) >= 3))


def share_fact_source_units(record, delivered):
    """Keep shared raw units once while retaining each opcode's fact packet.

    Call after selection so a reference always points to already delivered text.
    Equality includes source/revision, spans and the exact body; differing facts,
    unknown glyphs and differing source editions cannot be silently coalesced.
    """
    value = copy.deepcopy(record)
    text = str(value.get("text") or "")
    markers = list(re.finditer(r'(?m)^\[UNINTERPRETED SOURCE (\{[^\n]+\})\]\n', text))
    if not markers:
        return value
    parts = [text[:markers[0].start()]]
    references = []
    for index, marker in enumerate(markers):
        end = markers[index+1].start() if index+1 < len(markers) else len(text)
        unit = text[marker.start():end].rstrip()
        identity = (value.get('manual_id'), value.get('revision'), unit)
        if identity in delivered:
            import json
            references.append({**json.loads(marker[1]), "delivered_in": delivered[identity]})
        else:
            parts.append(unit + '\n\n')
            delivered[identity] = value['id']
    if references:
        import json
        parts.append('[SHARED SOURCE REFERENCES ' + json.dumps(references, ensure_ascii=False,
                                                               separators=(',', ':')) + ']')
        value['text'] = ''.join(parts).rstrip()
        value['raw_evidence_references'] = references
    return value


def context_reference(record):
    """Project known extraction wrappers, retaining complete technical units.

    Raw retrieval rows and the index remain untouched. Unknown layouts, glyphs,
    tables and differing values are retained, never interpreted by this pass.
    """
    value = copy.deepcopy(record)
    # Compiled packets already have an atomic receipt and raw-unit projection.
    # Reformatting them here would invalidate complete-block delivery accounting.
    if "definition_fact_groups" in value or "source_spans" in value:
        return value
    text = str(value.get("text") or "")
    redundant = {label + str(value[key]) for label, key in (
        ("SOURCE: ", "source"), ("SECTION: ", "section"),
        ("PDF PAGE: ", "pdf_page"), ("CPU SOURCE SCOPE: ", "plc_models"),
    ) if value.get(key) not in (None, "")}
    if value.get("manual_number") and value.get("revision"):
        redundant.add("MANUAL: " + str(value["manual_number"]) + " Rev." + str(value["revision"]))
    # Only metadata duplicated verbatim in the citation is removable.
    lines = text.splitlines()
    while lines and (lines[0] in redundant or not lines[0].strip()):
        lines.pop(0)
    text = "\n".join(lines)
    markers = list(re.finditer(r"(?m)^\[PAGE (\d+) (PROSE|LAYOUT)\][ \t]*$|^\[TABLE[^\n]*\]$", text))
    prefix = text[:markers[0].start()] if markers else text
    units, prose = [], {}
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[marker.end():end].strip()
        # Line wrapping may differ; preserve delimiters, punctuation, glyphs and
        # order/multiplicity. A missing condition or changed number prevents dedupe.
        words = tuple(re.findall(r"\S+", body))
        units.append((marker, body, words))
        if marker[2] == "PROSE":
            prose.setdefault(marker[1], []).append(words)
    kept, omitted = [prefix.rstrip()] if prefix.strip() else [], []
    for marker, body, words in units:
        if marker[2] == "LAYOUT" and words and words in prose.get(marker[1], []):
            omitted.append("duplicate_layout:" + marker[1])
            continue
        # Extractors sometimes capture the rotated chapter index as a table.
        # Its blank second column and complete navigation labels distinguish it
        # from operand/device matrices, including tables with unknown glyphs.
        if is_navigation_table(marker[0] + "\n" + body):
            omitted.append("navigation_table")
            continue
        kept.append(marker[0] + "\n" + body)
    rendered = "\n\n".join(kept).strip()
    if rendered != str(record.get("text") or "").strip():
        value["text"] = rendered
        value["content_sha256"] = text_sha256(rendered)
        value["context_projection"] = {
            "kind": "source_units", "original_chars": len(str(record.get("text") or "")),
            "rendered_chars": len(rendered), "omitted_wrappers": omitted,
        }
    return value

def estimate_tokens(value):
    """Tokenizer-free deterministic estimate for context size telemetry."""
    text = str(value or "")
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil(max(0, len(text) - cjk) / 4)



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
        "plc_models", "source_applicability", "device_definition_status", "device_definition", "source_evidence",
        "fact_kind", "fact_target", "source_chunk_id", "source_section_id", "source_address_locations", "evidence_scope", "table_binding_status", "content_status", "context_projection", "raw_evidence_references",
        "content_origin", "distribution_status", "technical_status",
        "positioning_context", "positioning_hardware_status", "positioning_lookup_basis",
    ) if key in result}
    kind = result.get("manual_type", "")
    record["role"] = ("design_reference" if kind == "curated_design" else
                      "supporting_reference" if kind == "third_party_skill" else "technical_reference")
    record["content_sha256"] = text_sha256(result.get("text", ""))
    return record


class KnowledgeQuery(str):
    def __new__(cls, text="", truncated=False, *, precompiled=False, metadata=None):
        result = super().__new__(cls, text)
        result.truncated = bool(truncated)
        result.precompiled = bool(precompiled)
        result.metadata = copy.deepcopy(metadata or {})
        return result


def retrieval_failure(error):
    """A small safe failure record instead of swallowing all retrieval errors."""
    dependency = getattr(error, "name", None) if isinstance(error, ImportError) else None
    dependency = dependency if isinstance(dependency, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,100}", dependency) else None
    kind = type(error).__name__
    result = {"code": "dependency_missing" if isinstance(error, ModuleNotFoundError) else
                      "dependency_import_failed" if isinstance(error, ImportError) else "retrieval_exception",
              "error_type": kind if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", kind) else "Exception"}
    if dependency:
        result["dependency"] = dependency
    return result
