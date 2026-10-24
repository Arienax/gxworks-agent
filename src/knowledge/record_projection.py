"""Typed index-record projection, stable selection and reference formatting."""
from __future__ import annotations

import json

from knowledge.index_access import (
    quote_identifier, row_value, CHUNK_ID_COLUMNS, TEXT_COLUMNS, SOURCE_COLUMNS,
    PAGE_COLUMNS, PDF_PAGE_COLUMNS, SECTION_COLUMNS, MODEL_COLUMNS,
)
from knowledge.scope import row_in_scope

MAX_TOP_K = 50


def default_source(meta, path):
    for key in (
        "source",
        "manual_title",
        "title",
        "manual_number",
        "document",
    ):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return path.stem if path is not None else "authored_facts"


def chunk_result(row, meta, path, plc_model, task_type, connection=None):
    if row is None or not row_in_scope(row, plc_model, task_type):
        return None
    text = row_value(row, TEXT_COLUMNS)
    if text is None or not str(text).strip():
        return None
    chunk_id = row_value(row, CHUNK_ID_COLUMNS, row["_chunk_rowid"])
    source = row_value(row, SOURCE_COLUMNS, default_source(meta, path))
    page = row_value(row, PAGE_COLUMNS, "")
    pdf_page = row_value(row, PDF_PAGE_COLUMNS, "")
    if page in (None, ""):
        page = pdf_page
    page_end = row_value(
        row,
        ("printed_page_end", "page_end", "end_page", "pdf_page_end"),
        "",
    )
    section = row_value(row, SECTION_COLUMNS, "")
    result = {
        "id": str(chunk_id),
        "source": str(source),
        "page": page,
        "page_end": page_end,
        "pdf_page": pdf_page,
        "section": str(section or ""),
        "text": str(text).strip(),
        "manual_id": str(row_value(row, ("manual_id",), "") or ""),
        "manual_number": str(row_value(row, ("manual_number",), "") or ""),
        "revision": str(row_value(row, ("revision",), "") or ""),
        "manual_type": str(row_value(row, ("manual_type",), "") or ""),
        "chunk_type": str(row_value(row, ("chunk_type",), "") or ""),
        "instruction_opcode": str(
            row_value(row, ("instruction_opcode", "opcode"), "") or ""
        ),
        "plc_models": str(row_value(row, MODEL_COLUMNS, "") or ""),
        "source_applicability": source_applicability(row),
        "table_binding_status": str(row_value(row, ("device_table_binding_status",), "") or ""),
        "manual_priority": int(
            row_value(row, ("manual_priority", "priority"), 0) or 0
        ),
    }
    if meta.get("corpus_kind") == "authored_plc_facts_v1":
        try:
            authored = json.loads(row_value(row, ("metadata_json",), "{}"))
        except (ValueError, TypeError):
            return None
        from knowledge.fact_corpus import PUBLICATION_STATUS
        if (not isinstance(authored, dict) or authored.get("distribution_status") != PUBLICATION_STATUS
                or authored.get("content_origin") not in {"authored_fact", "original_case"}):
            return None
        result.update(authored)
        if authored.get("runtime_owner") is not None:
            if connection is None:
                return None
            from knowledge.runtime_fact_publication import hydrate_owner_packet
            result["text"] = json.dumps(hydrate_owner_packet(connection, authored["runtime_owner"]),
                                        ensure_ascii=False, separators=(",", ":"))
    return result


def source_applicability(row):
    """Return source conditions separately from executable CPU admission."""
    raw = row_value(row, ("source_applicability_json",), "")
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {"basis": "invalid_source_metadata", "instruction_support": "unknown"}
    return value if isinstance(value, dict) else {}


def structured_instruction_record(connection, schema, chunk_id):
    """Render authoritative instruction fields from SQLite at retrieval time."""
    table = schema.get("instructions")
    if not table:
        return ""
    required = {"chunk_id", "opcode", "operands_json", "restrictions_json"}
    if not required.issubset(set(table["columns"])):
        return ""
    selected = ["opcode", "operands_json", "restrictions_json"]
    if "completion_flags_json" in table["columns"]:
        selected.append("completion_flags_json")
    row = connection.execute(
        "SELECT {} FROM {} WHERE chunk_id=? LIMIT 1".format(
            ",".join(quote_identifier(column) for column in selected),
            quote_identifier(table["name"]),
        ),
        (chunk_id,),
    ).fetchone()
    if row is None:
        return ""
    try:
        operands = json.loads(str(row["operands_json"] or "[]"))
    except (TypeError, ValueError):
        operands = []
    try:
        restrictions = json.loads(str(row["restrictions_json"] or "[]"))
    except (TypeError, ValueError):
        restrictions = []
    flags = []
    if "completion_flags_json" in selected:
        try:
            flags = json.loads(str(row["completion_flags_json"] or "[]"))
        except (TypeError, ValueError):
            flags = []
    lines = ["[STRUCTURED INSTRUCTION RECORD]", f"INSTRUCTION: {row['opcode']}"]
    rendered = []
    for item in operands if isinstance(operands, list) else []:
        if not isinstance(item, dict):
            continue
        part = f"{item.get('position', '')}: {item.get('description', '')}".strip()
        if item.get("data_type"):
            part += f" [{item['data_type']}]"
        devices = item.get("applicable_devices") or []
        if isinstance(devices, list) and devices:
            part += " applicable=" + ",".join(str(value) for value in devices)
        if part:
            rendered.append(part)
    if rendered:
        lines.append("OPERANDS: " + "; ".join(rendered))
    if flags:
        lines.append("COMPLETION_FLAGS: " + ", ".join(str(value) for value in flags))
    if restrictions:
        lines.append("KEY_RESTRICTIONS: " + " | ".join(str(value) for value in restrictions[:3]))
    return "\n".join(lines) if len(lines) > 2 else ""


def augment_structured_instruction(connection, schema, result):
    if result is None:
        return None
    prefix = structured_instruction_record(connection, schema, result.get("id"))
    if not prefix:
        return result
    enriched = dict(result)
    body = str(enriched.get("text") or "")
    if body.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        _old, separator, remainder = body.partition("\n\n")
        body = remainder if separator else ""
    enriched["text"] = prefix + ("\n\n" + body if body else "")
    return enriched


def format_result_block(result):
    citation = {
        "id": result.get("id", ""),
        "source": result.get("source", ""),
        "page": result.get("page", ""),
    }
    if result.get("pdf_page") not in (None, ""):
        citation["pdf_page"] = result["pdf_page"]
    if result.get("page_end") not in (None, ""):
        citation["page_end"] = result["page_end"]
    if result.get("section"):
        citation["section"] = result["section"]
    for key in ("manual_id", "manual_number", "revision", "plc_models", "fact_kind",
                "fact_target", "device_definition_status", "evidence_scope", "content_status"):
        if key in result:
            citation[key] = result[key]
    return "[KNOWLEDGE {}]\n{}\n[/KNOWLEDGE]".format(
        json.dumps(citation, ensure_ascii=False, separators=(",", ":")),
        result.get("text", ""),
    )


def select_ranked(candidates, top_k):
    selected = []
    seen = set()
    for candidate in candidates:
        chunk_id = str(candidate.get("id", ""))
        if not chunk_id or chunk_id in seen:
            continue
        selected.append(candidate)
        seen.add(chunk_id)
        if len(selected) >= top_k:
            break
    return selected
