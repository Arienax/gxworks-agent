#!/usr/bin/env python3
"""Evidence-aware wrapper for the schema-v3 structured knowledge audit.

The legacy audit intentionally used a broad heuristic to discover suspicious
S/D/N/M/P-number records. After the first review that heuristic is too coarse:
a chunk may contain both an operand-definition table and a concrete program
example. This wrapper keeps every structural/error check from the legacy audit
but suppresses only ``possible_operand_placeholder_device_record`` warnings when
that exact token has concrete device/pointer evidence in its provenance.
"""
from __future__ import annotations

import re

import audit_structured_knowledge_quality as base
from audit_device_placeholder_evidence import TOKEN_BOUNDARY, occurrence_signals, token_source_pattern

_LEGACY_AUDIT_DEVICES = base.audit_devices
_REAL_SIGNAL_NAMES = {
    "concrete_instruction_use",
    "concrete_range_use",
    "pointer_or_label_use",
    "example_semantics",
}


def has_concrete_device_evidence(connection, device_norm: str, record_type: str, token: str) -> bool:
    """Return true only for concrete PLC address/pointer evidence.

    N<number> is intentionally never accepted here: N is not a published device
    family in this builder. It represents operand/count syntax or MC/MCR nesting
    semantics and must be reclassified by the builder/migration instead.
    """
    token = str(token or "").upper()
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
        text = str(raw_text or "")
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            line = text[line_start:line_end]
            if re.search(rf"{re.escape(match.group(0))}\s+steps\b", line, re.I):
                continue
            signals = occurrence_signals(token, text, match)
            if any(signals.get(name) for name in _REAL_SIGNAL_NAMES):
                return True
    return False


def audit_devices(connection):
    issues, stats = _LEGACY_AUDIT_DEVICES(connection)
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
