#!/usr/bin/env python3
"""Evidence-aware audit of structured PLC knowledge quality."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from audit_device_placeholder_evidence import (TOKEN_BOUNDARY, occurrence_signals, token_source_pattern)

DEVICE_RE = re.compile(r"^(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)(?:\.(\d+))?$", re.I)
DEVICE_RANGE_RE = re.compile(r"^(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)-(ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])(\d+)$", re.I)
OPERAND_POSITION_RE = re.compile(r"^[SDNMP](?:\d{0,3})?$", re.I)
COMPLETION_FLAG_RE = re.compile(r"^M8\d{3}$", re.I)
GLYPH_ONLY_RE = re.compile(r"(?:(?:\[GLYPH-[0-9A-F]+\]|\(cid:\d+\))\d*\s*)+", re.I)
ERROR_CODE_RE = re.compile(r"^(?:0X)?[0-9A-F]{4}(?:H)?$", re.I)
BLANK_MARKERS = {"", "<blank>", "blank", "-", "—"}
HEADERISH = {"error code", "error", "fault code", "message", "cause", "corrective action", "action", "description", "device", "device name", "bit devices", "word devices"}

def issue(severity: str, domain: str, code: str, **details: Any) -> dict[str, Any]:
    return {"severity": severity, "domain": domain, "code": code, **details}

def load_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or json.dumps(fallback))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback

def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def token_in_text(token: str, text: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9_]){re.escape(token)}(?![A-Z0-9_])", text or "", flags=re.I))

def _audit_devices_structural(con: sqlite3.Connection):
    issues = []
    rows = con.execute("SELECT id,device_norm,device,prefix,record_type,description,occurrences,plc_models,source_manuals_json,chunk_id FROM device_records ORDER BY id").fetchall()
    manual_ids = {row[0] for row in con.execute("SELECT manual_id FROM manuals")}
    structured_positions = defaultdict(set)
    instruction_meta = {}
    for manual_id, opcode, chunk_id, payload in con.execute("SELECT manual_id,opcode,chunk_id,operands_json FROM instructions WHERE chunk_id IS NOT NULL"):
        if chunk_id is None:
            continue
        instruction_meta[int(chunk_id)] = (str(manual_id), str(opcode))
        for item in load_json(payload, []):
            if isinstance(item, dict):
                pos = normalize(item.get("position")).upper()
                if pos:
                    structured_positions[int(chunk_id)].add(pos)
    leaked_pairs = []
    for chunk_id, positions in structured_positions.items():
        manual_id, opcode = instruction_meta.get(chunk_id, ("", ""))
        entity_rows = con.execute("SELECT entity,entity_norm,entity_type,occurrences FROM entity_index WHERE chunk_id=? AND entity_type IN ('device','device_range')", (chunk_id,)).fetchall()
        by_entity = {normalize(row[0]).upper(): row for row in entity_rows}
        for pos in sorted(positions):
            if DEVICE_RE.fullmatch(pos) and pos in by_entity:
                row = by_entity[pos]
                leaked = {"manual_id": manual_id, "opcode": opcode, "chunk_id": chunk_id, "position": pos, "entity_type": row[2], "occurrences": int(row[3])}
                leaked_pairs.append(leaked)
                issues.append(issue("error", "device_records", "operand_position_leaked_into_device_index", **leaked))
    prefix_counts, type_counts = Counter(), Counter()
    placeholder_like_records = []
    for row in rows:
        row_id, device_norm, device, prefix, record_type, description, occurrences, _models, source_manuals_json, chunk_id = row
        device, device_norm, prefix, record_type, description = normalize(device), normalize(device_norm), normalize(prefix).upper(), normalize(record_type), normalize(description)
        prefix_counts[prefix] += 1; type_counts[record_type] += 1
        if record_type not in {"device", "device_range", "device_family"}:
            issues.append(issue("error", "device_records", "invalid_record_type", id=row_id, device=device, record_type=record_type)); continue
        manuals = load_json(source_manuals_json, None)
        if manuals is None or not isinstance(manuals, list):
            issues.append(issue("error", "device_records", "invalid_source_manuals_json", id=row_id, device=device)); manuals = []
        unknown = sorted(str(v) for v in manuals if str(v) not in manual_ids)
        if unknown:
            issues.append(issue("error", "device_records", "unknown_source_manual", id=row_id, device=device, manuals=unknown))
        if record_type == "device_family":
            if device != prefix or device_norm != f"family:{prefix.casefold()}" or int(occurrences) != 0 or chunk_id is not None:
                issues.append(issue("error", "device_records", "malformed_device_family_record", id=row_id, device=device, prefix=prefix, device_norm=device_norm, occurrences=int(occurrences), chunk_id=chunk_id))
            continue
        if int(occurrences) <= 0:
            issues.append(issue("error", "device_records", "nonpositive_occurrences", id=row_id, device=device, occurrences=int(occurrences)))
        if chunk_id is None or not con.execute("SELECT 1 FROM chunks WHERE id=?", (chunk_id,)).fetchone():
            issues.append(issue("error", "device_records", "missing_or_orphan_source_chunk", id=row_id, device=device, chunk_id=chunk_id))
        parsed_prefix = ""
        if record_type == "device":
            match = DEVICE_RE.fullmatch(device)
            if not match: issues.append(issue("error", "device_records", "invalid_device_token", id=row_id, device=device))
            else: parsed_prefix = match.group(1).upper()
        else:
            match = DEVICE_RANGE_RE.fullmatch(device)
            if not match: issues.append(issue("error", "device_records", "invalid_device_range", id=row_id, device=device))
            else:
                parsed_prefix = match.group(1).upper()
                if match.group(1).upper() != match.group(3).upper(): issues.append(issue("warning", "device_records", "cross_family_device_range", id=row_id, device=device))
        if parsed_prefix and parsed_prefix != prefix:
            issues.append(issue("error", "device_records", "prefix_mismatch", id=row_id, device=device, prefix=prefix, parsed_prefix=parsed_prefix))
        if description.casefold() in BLANK_MARKERS or (description and GLYPH_ONLY_RE.fullmatch(description)):
            issues.append(issue("warning", "device_records", "low_quality_description", id=row_id, device=device, description=description))
        provenance = con.execute("SELECT e.chunk_id,e.entity_type,e.occurrences,c.chunk_type,c.instruction_opcode,c.text FROM entity_index e JOIN chunks c ON c.id=e.chunk_id WHERE e.entity_norm=? AND e.entity_type=?", (device_norm, record_type)).fetchall()
        if not provenance:
            issues.append(issue("error", "device_records", "missing_entity_index_provenance", id=row_id, device=device, record_type=record_type))
        if record_type == "device" and OPERAND_POSITION_RE.fullmatch(device) and re.search(r"\d", device):
            instruction_occ = sum(int(p[2]) for p in provenance if p[3] == "instruction")
            total_occ = sum(int(p[2]) for p in provenance)
            structured_hits, samples = 0, []
            for p in provenance:
                text = str(p[5] or "")
                if p[3] == "instruction" and ("[STRUCTURED INSTRUCTION RECORD]" in text or re.search(r"\b(?:Set data|Applicable devices|Operand Type)\b", text, re.I)):
                    structured_hits += int(p[2])
                    if len(samples) < 4: samples.append({"chunk_id": int(p[0]), "opcode": str(p[4] or ""), "occurrences": int(p[2])})
            if total_occ and instruction_occ / total_occ >= 0.75 and structured_hits:
                item = {"id": row_id, "device": device, "occurrences": int(occurrences), "instruction_occurrences": instruction_occ, "total_provenance_occurrences": total_occ, "structured_instruction_occurrences": structured_hits, "samples": samples}
                placeholder_like_records.append(item)
                issues.append(issue("warning", "device_records", "possible_operand_placeholder_device_record", **item))
    return issues, {"records": len(rows), "record_types": dict(sorted(type_counts.items())), "prefix_counts": dict(sorted(prefix_counts.items())), "operand_position_leaks": len(leaked_pairs), "placeholder_like_device_records": len(placeholder_like_records)}

_REAL_DEVICE_SIGNAL_NAMES = {
    "concrete_instruction_use",
    "concrete_range_use",
    "pointer_or_label_use",
    "example_semantics",
}


def has_concrete_device_evidence(
    connection: sqlite3.Connection,
    device_norm: str,
    record_type: str,
    token: str,
) -> bool:
    """Return whether this exact token has concrete PLC address/pointer evidence.

    The broad S/D/N/M/P-number heuristic is only candidate discovery. Final
    warning emission is occurrence-local and provenance-aware. N<number> is not
    a PLC device family here; it represents operand/count or MC/MCR nesting
    semantics and must stay outside ``device_records``.
    """
    token = normalize(token).upper()
    prefix_match = re.match(r"[A-Z]+", token)
    prefix = prefix_match.group(0) if prefix_match else ""
    if prefix == "N":
        return False

    pattern = re.compile(
        rf"(?<!{TOKEN_BOUNDARY}){token_source_pattern(token)}(?!{TOKEN_BOUNDARY})",
        re.I,
    )
    rows = connection.execute(
        """
        SELECT c.text
        FROM entity_index e
        JOIN chunks c ON c.id=e.chunk_id
        WHERE e.entity_norm=? AND e.entity_type=?
        """,
        (device_norm, record_type),
    ).fetchall()
    for (raw_text,) in rows:
        source = str(raw_text or "")
        for match in pattern.finditer(source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            line_end = source.find("\n", match.end())
            if line_end < 0:
                line_end = len(source)
            line = source[line_start:line_end]
            # A PDF instruction-size row such as ``D | 17 steps`` can flatten
            # to ``D 17 steps``; that occurrence is not concrete D17 evidence.
            if re.search(rf"{re.escape(match.group(0))}\s+steps\b", line, re.I):
                continue
            signals = occurrence_signals(token, source, match)
            if any(signals.get(name) for name in _REAL_DEVICE_SIGNAL_NAMES):
                return True
    return False


def audit_devices(con: sqlite3.Connection):
    """Run structural checks, then semantically adjudicate placeholder candidates."""
    issues, stats = _audit_devices_structural(con)
    kept = []
    suppressed = 0
    for item in issues:
        if item.get("code") != "possible_operand_placeholder_device_record":
            kept.append(item)
            continue
        row = con.execute(
            "SELECT device_norm,record_type FROM device_records WHERE id=?",
            (item.get("id"),),
        ).fetchone()
        if row and has_concrete_device_evidence(
            con,
            str(row[0]),
            str(row[1]),
            str(item.get("device", "")),
        ):
            suppressed += 1
            continue
        kept.append(item)

    # N is semantic syntax, not a device family. Make any future regression a
    # hard audit error even if it appears outside the old instruction heuristic.
    existing_n_errors = {
        int(item.get("id"))
        for item in kept
        if item.get("code") == "n_syntax_in_device_records" and item.get("id") is not None
    }
    for row_id, device, record_type in con.execute(
        "SELECT id,device,record_type FROM device_records "
        "WHERE prefix='N' AND record_type IN ('device','device_range') ORDER BY id"
    ).fetchall():
        if int(row_id) not in existing_n_errors:
            kept.append(
                issue(
                    "error",
                    "device_records",
                    "n_syntax_in_device_records",
                    id=int(row_id),
                    device=str(device),
                    record_type=str(record_type),
                )
            )

    stats = dict(stats)
    stats["placeholder_like_device_records"] = sum(
        1
        for item in kept
        if item.get("code") == "possible_operand_placeholder_device_record"
    )
    stats["placeholder_warnings_suppressed_by_concrete_evidence"] = suppressed
    stats["n_syntax_device_records"] = sum(
        1 for item in kept if item.get("code") == "n_syntax_in_device_records"
    )
    return kept, stats


def field_quality(identity, field, value):
    out, cleaned = [], normalize(value)
    if not cleaned: return out
    if cleaned.casefold().strip(":") in HEADERISH: out.append(issue("error", "error_records", "table_header_as_field", field=field, value=cleaned, **identity))
    if GLYPH_ONLY_RE.fullmatch(cleaned): out.append(issue("error", "error_records", "glyph_only_field", field=field, value=cleaned, **identity))
    if re.fullmatch(r"[|:/;,.\-\s0-9A-FH]+", cleaned, flags=re.I) and not re.search(r"[G-Z]", cleaned, flags=re.I): out.append(issue("warning", "error_records", "numeric_or_code_like_field", field=field, value=cleaned, **identity))
    return out

def audit_errors(con):
    issues = []
    rows = con.execute("SELECT id,error_code,error_code_norm,message,cause,corrective_action,raw_text,manual_id,pdf_page,table_index,row_index,chunk_id FROM error_records ORDER BY manual_id,pdf_page,table_index,row_index,id").fetchall()
    code_counts, manual_counts, weak_context = Counter(), Counter(), 0
    for row in rows:
        row_id, code, code_norm, message, cause, action, raw_text, manual_id, pdf_page, table_index, row_index, chunk_id = row
        code = normalize(code).upper(); code_counts[code] += 1; manual_counts[str(manual_id)] += 1
        identity = {"id": int(row_id), "manual_id": str(manual_id), "pdf_page": int(pdf_page), "table_index": int(table_index), "row_index": int(row_index), "error_code": code}
        if not ERROR_CODE_RE.fullmatch(code): issues.append(issue("error", "error_records", "invalid_error_code", **identity))
        if normalize(code_norm) != code.casefold(): issues.append(issue("error", "error_records", "error_code_norm_mismatch", code_norm=normalize(code_norm), **identity))
        if chunk_id is None or not con.execute("SELECT 1 FROM chunks WHERE id=?", (chunk_id,)).fetchone(): issues.append(issue("error", "error_records", "missing_or_orphan_chunk", chunk_id=chunk_id, **identity))
        page = con.execute("SELECT chunk_type,outline_path,clean_text,compact_layout FROM page_artifacts WHERE manual_id=? AND pdf_page=?", (manual_id, pdf_page)).fetchone()
        if not page: issues.append(issue("error", "error_records", "missing_source_page", **identity)); continue
        chunk_type, outline_path, clean_text, compact_layout = page; source = f"{clean_text or ''}\n{compact_layout or ''}"
        if str(chunk_type) != "error": issues.append(issue("warning", "error_records", "source_page_not_error_chunk", chunk_type=str(chunk_type), **identity))
        bare = code.removesuffix("H")
        if not token_in_text(bare, source) and not token_in_text(code, source): issues.append(issue("error", "error_records", "error_code_missing_from_source_page", outline_path=str(outline_path), **identity))
        else:
            contexts = [source[max(0,m.start()-120):min(len(source),m.end()+260)] for m in re.finditer(rf"(?<![A-Z0-9]){re.escape(bare)}(?:H)?(?![A-Z0-9])", source, flags=re.I)]
            strong = any(re.search(r"error|fault|cause|corrective|action|check|diagnos", ctx, flags=re.I) or re.search(rf"(?:^|\n)\s*{re.escape(bare)}(?:H)?\b", ctx, flags=re.I) for ctx in contexts)
            if not strong: weak_context += 1; issues.append(issue("warning", "error_records", "weak_error_code_context", outline_path=str(outline_path), **identity))
        if not normalize(message): issues.append(issue("warning", "error_records", "empty_message", **identity))
        for field, value in (("message",message),("cause",cause),("corrective_action",action)): issues.extend(field_quality(identity, field, str(value or "")))
        raw = normalize(raw_text)
        if raw and GLYPH_ONLY_RE.fullmatch(raw): issues.append(issue("error", "error_records", "glyph_only_raw_text", **identity))
    repeated = {code: count for code, count in code_counts.items() if count > 4}
    return issues, {"records": len(rows), "manual_counts": dict(sorted(manual_counts.items())), "unique_codes": len(code_counts), "codes_repeated_more_than_four_times": dict(sorted(repeated.items())), "weak_context_records": weak_context}

def audit_completion_flags(con):
    issues = []
    rows = con.execute("SELECT id,manual_id,opcode,source_pages_json,completion_flags_json,chunk_id FROM instructions ORDER BY manual_id,opcode_norm").fetchall()
    instructions_with_flags, flag_counts = 0, Counter()
    for row_id, manual_id, opcode, pages_json, flags_json, chunk_id in rows:
        flags = load_json(flags_json, None); identity = {"instruction_id": int(row_id), "manual_id": str(manual_id), "opcode": str(opcode)}
        if flags is None or not isinstance(flags, list): issues.append(issue("error", "completion_flags", "invalid_completion_flags_json", **identity)); continue
        if not flags: continue
        instructions_with_flags += 1; normalized_flags = [normalize(v).upper() for v in flags if normalize(v)]
        if len(normalized_flags) != len(flags) or len(set(normalized_flags)) != len(normalized_flags): issues.append(issue("error", "completion_flags", "blank_or_duplicate_completion_flag", flags=normalized_flags, **identity))
        pages = load_json(pages_json, []); pages = pages if isinstance(pages, list) else []
        source = ""
        if pages:
            placeholders = ",".join("?" for _ in pages)
            source = "\n".join(str(r[0] or "") for r in con.execute(f"SELECT clean_text FROM page_artifacts WHERE manual_id=? AND pdf_page IN ({placeholders}) ORDER BY pdf_page", (manual_id, *pages)).fetchall())
        chunk_text = ""
        if chunk_id is not None:
            cr = con.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone(); chunk_text = str(cr[0] or "") if cr else ""
        for flag in normalized_flags:
            flag_counts[flag] += 1
            if not COMPLETION_FLAG_RE.fullmatch(flag): issues.append(issue("error", "completion_flags", "invalid_completion_flag_token", flag=flag, **identity)); continue
            matches = list(re.finditer(rf"(?<![A-Z0-9_]){re.escape(flag)}(?![A-Z0-9_])", source, flags=re.I))
            if not matches: issues.append(issue("error", "completion_flags", "flag_missing_from_source_pages", flag=flag, source_pages=pages, **identity)); continue
            contexts = [source[max(0,m.start()-120):min(len(source),m.end()+180)] for m in matches]
            if not any(re.search(r"complete|completion|completed|finished|finish|end flag", ctx, re.I) for ctx in contexts): issues.append(issue("warning", "completion_flags", "weak_completion_semantics", flag=flag, source_pages=pages, **identity))
            if chunk_text and "[STRUCTURED INSTRUCTION RECORD]" in chunk_text:
                marker = re.search(r"^COMPLETION_FLAGS:\s*(.+)$", chunk_text, flags=re.I|re.M)
                if not marker or flag not in marker.group(1).upper(): issues.append(issue("error", "completion_flags", "structured_chunk_missing_completion_flag", flag=flag, chunk_id=chunk_id, **identity))
    return issues, {"instructions": len(rows), "instructions_with_flags": instructions_with_flags, "flag_counts": dict(sorted(flag_counts.items()))}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--database",type=Path,required=True); p.add_argument("--output",type=Path); a=p.parse_args()
    con=sqlite3.connect(a.database); integrity=con.execute("PRAGMA integrity_check").fetchone()[0]; fk=con.execute("PRAGMA foreign_key_check").fetchall()
    di,ds=audit_devices(con); ei,es=audit_errors(con); ci,cs=audit_completion_flags(con); con.close(); issues=di+ei+ci
    sev=Counter(x["severity"] for x in issues); dom=Counter(x["domain"] for x in issues); codes=Counter(f"{x['domain']}:{x['code']}" for x in issues)
    report={"integrity_check":integrity,"foreign_key_violations":len(fk),"stats":{"device_records":ds,"error_records":es,"completion_flags":cs},"issue_severity_counts":dict(sorted(sev.items())),"issue_domain_counts":dict(sorted(dom.items())),"issue_code_counts":dict(sorted(codes.items())),"issues":issues}
    rendered=json.dumps(report,ensure_ascii=False,indent=2)
    if a.output: a.output.write_text(rendered,encoding="utf-8")
    print(rendered); return 0
if __name__ == "__main__": raise SystemExit(main())
