#!/usr/bin/env python3
"""Audit structured instruction operands backfilled from manual tables.

The audit compares a baseline SQLite knowledge database with a candidate database,
then inspects only instruction rows whose operands_json changed from [] to a
non-empty structured value. It avoids opcode-specific rules: checks are based on
record shape, source-table evidence, semantic descriptions, and cross-manual
consistency.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

RAW_BIT_FAMILIES = {"X", "Y", "M", "S"}
DEVICE_HEADER_TOKENS = {"X", "Y", "M", "T", "C", "S", "D", "R", "V", "Z", "K", "H", "E", "P"}
PLACEHOLDER_RE = re.compile(r"^[A-Z](?:\d{0,3})?$")
BLANK_MARKERS = {"", "<blank>", "blank", "-", "—"}


def _load_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT id, manual_id, opcode, opcode_norm, title, source_pages_json,
               operands_json, restrictions_json, chunk_id
        FROM instructions
        ORDER BY manual_id, opcode_norm
        """
    ).fetchall()
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        for field, fallback in (("source_pages_json", []), ("operands_json", []), ("restrictions_json", [])):
            try:
                item[field] = json.loads(item[field] or "[]")
            except (TypeError, ValueError):
                item[field] = fallback
        result[(str(item["manual_id"]), str(item["opcode_norm"]))] = item
    connection.close()
    return result


def _open_candidate(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _source_evidence(connection: sqlite3.Connection, manual_id: str, pages: list[int]) -> str:
    if not pages:
        return ""
    placeholders = ",".join("?" for _ in pages)
    chunks: list[str] = []
    for row in connection.execute(
        f"SELECT compact_layout FROM page_artifacts WHERE manual_id=? AND pdf_page IN ({placeholders}) ORDER BY pdf_page",
        (manual_id, *pages),
    ).fetchall():
        chunks.append(str(row[0] or ""))
    for row in connection.execute(
        f"SELECT table_text FROM tables WHERE manual_id=? AND pdf_page IN ({placeholders}) ORDER BY pdf_page,table_index",
        (manual_id, *pages),
    ).fetchall():
        chunks.append(str(row[0] or ""))
    return "\n".join(chunks)


def _normalized_device_set(item: dict[str, Any]) -> set[str]:
    values = item.get("applicable_devices") or []
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def _is_blank(value: str) -> bool:
    return str(value or "").strip().casefold() in BLANK_MARKERS


def _issue(severity: str, code: str, row: dict[str, Any], **details: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "manual_id": row["manual_id"],
        "opcode": row["opcode"],
        **details,
    }


def audit_record(connection: sqlite3.Connection, row: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    operands = row.get("operands_json") or []
    if not isinstance(operands, list):
        return [_issue("error", "operands_not_list", row)]

    positions: list[str] = []
    evidence = _source_evidence(connection, row["manual_id"], row.get("source_pages_json") or [])
    evidence_upper = evidence.upper()

    for index, item in enumerate(operands):
        if not isinstance(item, dict):
            issues.append(_issue("error", "operand_not_object", row, index=index))
            continue
        position = str(item.get("position") or "").strip().upper()
        description = str(item.get("description") or "").strip()
        data_type = str(item.get("data_type") or "").strip()
        devices = _normalized_device_set(item)
        positions.append(position)

        if not position or not PLACEHOLDER_RE.fullmatch(position):
            issues.append(_issue("error", "invalid_operand_position", row, index=index, position=position))
        if position in {"EN", "ENO"}:
            issues.append(_issue("error", "execution_pin_as_operand", row, index=index, position=position))
        if position and position not in evidence_upper:
            issues.append(_issue("error", "operand_missing_from_source_evidence", row, index=index, position=position))

        description_blank = _is_blank(description)
        data_type_blank = _is_blank(data_type)
        if description_blank and data_type_blank and not devices:
            issues.append(_issue("warning", "operand_has_no_semantics", row, index=index, position=position, description=description))
        if description and description_blank:
            issues.append(_issue("error", "blank_marker_used_as_description", row, index=index, position=position, description=description))
        if isinstance(item.get("applicable_devices"), list) and not devices:
            issues.append(_issue("warning", "empty_applicable_device_list", row, index=index, position=position))

        # Device-family header cells are common in Applicable-devices matrices.
        # If one was emitted as an operand with no independent semantics, the
        # parser almost certainly consumed a header row as an operand row.
        if position in DEVICE_HEADER_TOKENS and position not in {"S", "D"}:
            if description_blank and data_type_blank and not devices:
                issues.append(
                    _issue(
                        "error",
                        "device_header_misread_as_operand",
                        row,
                        index=index,
                        position=position,
                        description=description,
                    )
                )

        semantic = f"{description} {data_type}".casefold()
        word_only = "word device" in semantic and "bit or word" not in semantic and "bit/word" not in semantic
        if word_only:
            raw_bits = sorted(devices.intersection(RAW_BIT_FAMILIES))
            if raw_bits:
                issues.append(
                    _issue(
                        "error",
                        "word_operand_contains_raw_bit_family",
                        row,
                        index=index,
                        position=position,
                        raw_bit_families=raw_bits,
                        description=description,
                    )
                )
        if re.search(r"\b(bit device|head bit)\b", semantic) and "bit or word" not in semantic and devices:
            if not devices.intersection(RAW_BIT_FAMILIES):
                issues.append(
                    _issue(
                        "warning",
                        "bit_operand_has_no_raw_bit_family",
                        row,
                        index=index,
                        position=position,
                        devices=sorted(devices),
                        description=description,
                    )
                )

        if len(devices) > 16:
            issues.append(
                _issue(
                    "warning",
                    "suspiciously_broad_device_set",
                    row,
                    index=index,
                    position=position,
                    device_count=len(devices),
                    devices=sorted(devices),
                )
            )

    duplicates = sorted(value for value, count in Counter(positions).items() if value and count > 1)
    if duplicates:
        issues.append(_issue("error", "duplicate_operand_positions", row, positions=duplicates))
    if len(operands) > 10:
        issues.append(_issue("warning", "suspicious_operand_count", row, count=len(operands), positions=positions))
    return issues


def cross_manual_issues(changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_opcode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in changed:
        by_opcode[str(row["opcode_norm"])].append(row)

    issues: list[dict[str, Any]] = []
    for rows in by_opcode.values():
        if len(rows) < 2:
            continue
        for left_index in range(len(rows)):
            for right_index in range(left_index + 1, len(rows)):
                left, right = rows[left_index], rows[right_index]
                left_map = {
                    str(item.get("position") or "").upper(): _normalized_device_set(item)
                    for item in left.get("operands_json") or []
                    if isinstance(item, dict) and item.get("position")
                }
                right_map = {
                    str(item.get("position") or "").upper(): _normalized_device_set(item)
                    for item in right.get("operands_json") or []
                    if isinstance(item, dict) and item.get("position")
                }
                shared = sorted(set(left_map).intersection(right_map))
                if not shared:
                    continue
                for position in shared:
                    a, b = left_map[position], right_map[position]
                    if not a or not b:
                        continue
                    union = a | b
                    similarity = len(a & b) / len(union) if union else 1.0
                    if similarity < 0.45:
                        issues.append(
                            {
                                "severity": "warning",
                                "code": "cross_manual_device_set_disagreement",
                                "opcode": left["opcode"],
                                "position": position,
                                "manual_a": left["manual_id"],
                                "manual_b": right["manual_id"],
                                "devices_a": sorted(a),
                                "devices_b": sorted(b),
                                "jaccard": round(similarity, 4),
                            }
                        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    baseline = _load_rows(args.baseline)
    candidate = _load_rows(args.candidate)
    changed: list[dict[str, Any]] = []
    for key, row in candidate.items():
        old = baseline.get(key)
        if not old:
            continue
        if old.get("operands_json") == [] and row.get("operands_json"):
            changed.append(row)

    connection = _open_candidate(args.candidate)
    issues: list[dict[str, Any]] = []
    for row in changed:
        issues.extend(audit_record(connection, row))
    connection.close()
    issues.extend(cross_manual_issues(changed))

    severity_counts = Counter(item["severity"] for item in issues)
    code_counts = Counter(item["code"] for item in issues)
    manual_counts = Counter(row["manual_id"] for row in changed)
    position_counts = Counter(
        str(item.get("position") or "").upper()
        for row in changed
        for item in row.get("operands_json") or []
        if isinstance(item, dict)
    )
    examples_by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in changed:
        for item in row.get("operands_json") or []:
            if not isinstance(item, dict):
                continue
            position = str(item.get("position") or "").upper()
            if len(examples_by_position[position]) < 6:
                examples_by_position[position].append(
                    {
                        "manual_id": row["manual_id"],
                        "opcode": row["opcode"],
                        "description": item.get("description", ""),
                        "data_type": item.get("data_type", ""),
                        "applicable_devices": item.get("applicable_devices", []),
                    }
                )
    report = {
        "backfilled_records": len(changed),
        "manual_counts": dict(sorted(manual_counts.items())),
        "operand_position_counts": dict(sorted(position_counts.items())),
        "examples_by_position": dict(sorted(examples_by_position.items())),
        "issue_severity_counts": dict(sorted(severity_counts.items())),
        "issue_code_counts": dict(sorted(code_counts.items())),
        "issues": issues,
    }

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
