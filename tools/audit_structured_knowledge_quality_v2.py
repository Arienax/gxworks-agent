#!/usr/bin/env python3
"""Evidence-aware wrapper for the schema-v3 structured knowledge audit.

The legacy audit intentionally used a broad heuristic to discover suspicious
S/D/N/M/P-number records.  After the first review that heuristic is too coarse:
a chunk may contain both an operand-definition table and a concrete program
example.  This wrapper keeps every structural/error check from the legacy audit
but suppresses only ``possible_operand_placeholder_device_record`` warnings when
that exact token has concrete device/pointer evidence in its provenance.
"""
from __future__ import annotations

import re

import audit_structured_knowledge_quality as base

_TOKEN_BOUNDARY = r"[A-Z0-9_]"
_PROGRAM_EXAMPLE_RE = re.compile(
    r"program\s+examples?|programming\s+examples?|example\s+program|"
    r"calculation\s+example|control\s+example",
    re.I,
)
_POINTER_LABEL_RE = re.compile(r"pointer|label|jump|subroutine", re.I)


def _token_pattern(token: str) -> re.Pattern[str]:
    match = re.fullmatch(r"([A-Z]+)(\d+)(?:\.(\d+))?", token, re.I)
    if not match:
        return re.compile(r"a^")
    prefix, number, bit = match.groups()
    suffix = rf"\s*\.\s*{re.escape(bit)}" if bit else ""
    return re.compile(
        rf"(?<!{_TOKEN_BOUNDARY}){re.escape(prefix)}\s*{re.escape(number)}{suffix}(?!{_TOKEN_BOUNDARY})",
        re.I,
    )


def _concrete_occurrence(token: str, text: str, match: re.Match[str]) -> bool:
    start, end = match.span()
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    window = text[max(0, start - 220):min(len(text), end + 260)]
    compact_token = re.sub(r"\s+", "", match.group(0)).upper()

    # Instruction-size table artifacts such as ``D 17 steps DABSD`` are not
    # concrete addresses even though they occur on instruction pages.
    if re.search(rf"{re.escape(match.group(0))}\s+steps\b", line, re.I):
        return False

    # Real PLC ranges: D300 to D307, M500-M599, etc.
    family = re.match(r"[A-Z]+", compact_token, re.I)
    if family:
        family_name = family.group(0)
        range_side = rf"{re.escape(family_name)}\s*\d+(?:\.\d+)?"
        if re.search(
            rf"{re.escape(match.group(0))}\s*(?:to|through|~|-|–|—)\s*{range_side}|"
            rf"{range_side}\s*(?:to|through|~|-|–|—)\s*{re.escape(match.group(0))}",
            window,
            re.I,
        ):
            return True

    # P devices are semantic pointers/labels when the local prose says so.
    if compact_token.startswith("P") and _POINTER_LABEL_RE.search(window):
        return True

    # Structured/ST calls and ladder-like instruction rows provide concrete
    # usage even when the same chunk also contains an Applicable-devices table.
    if re.search(
        rf"\b[A-Z][A-Z0-9_]{{1,12}}\s*\([^\n)]{{0,180}}{re.escape(match.group(0))}[^\n)]*\)",
        line,
        re.I,
    ):
        return True
    if re.search(
        rf"\b(?:LD|LDI|LDP|LDF|AND|ANI|OR|ORI|OUT|SET|RST|MOV|DMOV|BMOV|FMOV|"
        rf"ZRST|CJ|CALL|DECO|ENCO|FLT|BIN|PRUN|DEDIV|DEMUL|DEBCD|DINT|FNC\s*\d+)"
        rf"\b[^\n]{{0,120}}{re.escape(match.group(0))}",
        line,
        re.I,
    ):
        return True

    # A token inside a clearly labelled program/example block is concrete unless
    # its own line is an operand/step-definition row.
    if _PROGRAM_EXAMPLE_RE.search(window) and not re.search(
        r"operand|set\s+data|applicable\s+devices|operand\s+type|\bsteps\b",
        line,
        re.I,
    ):
        return True

    return False


def has_concrete_device_evidence(connection, device_norm: str, record_type: str, token: str) -> bool:
    pattern = _token_pattern(token)
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
        text = str(raw_text or "")
        for match in pattern.finditer(text):
            if _concrete_occurrence(token, text, match):
                return True
    return False


def audit_devices(connection):
    issues, stats = base.audit_devices(connection)
    kept = []
    suppressed = 0
    for item in issues:
        if item.get("code") != "possible_operand_placeholder_device_record":
            kept.append(item)
            continue
        row = connection.execute(
            "SELECT device_norm,record_type FROM device_records WHERE id=?",
            (item.get("id"),),
        ).fetchone()
        if row and has_concrete_device_evidence(
            connection,
            str(row[0]),
            str(row[1]),
            str(item.get("device", "")),
        ):
            suppressed += 1
            continue
        kept.append(item)
    stats = dict(stats)
    stats["placeholder_like_device_records"] = sum(
        1 for item in kept if item.get("code") == "possible_operand_placeholder_device_record"
    )
    stats["placeholder_warnings_suppressed_by_concrete_evidence"] = suppressed
    return kept, stats


def main() -> int:
    base.audit_devices = audit_devices
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
