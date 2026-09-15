#!/usr/bin/env python3
"""Audit strict-text error records for row-boundary semantic bleed."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def no_error_text(value: object) -> str:
    return re.sub(r"^[\s⎯—-]+", "", clean(value)).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    con = sqlite3.connect(args.database)
    rows = con.execute(
        """
        SELECT id,error_code,error_code_norm,message,cause,corrective_action,raw_text,
               manual_id,pdf_page,table_index,row_index
        FROM error_records
        ORDER BY manual_id,pdf_page,table_index,row_index,id
        """
    ).fetchall()
    issues = []
    no_error_records = 0
    for row in rows:
        rid, code, code_norm, message, cause, action, raw, manual, page, table_index, row_index = row
        identity = {
            "id": int(rid), "manual_id": str(manual), "pdf_page": int(page),
            "table_index": int(table_index), "row_index": int(row_index),
            "error_code": str(code),
        }
        normalized_message = no_error_text(message)
        if str(code_norm).casefold() == "0000":
            no_error_records += 1
            if normalized_message.casefold() != "no error":
                issues.append({"severity":"error","code":"0000_message_not_no_error","message":clean(message),**identity})
            if clean(cause):
                issues.append({"severity":"error","code":"0000_cause_row_bleed","cause":clean(cause),**identity})
            if clean(action):
                issues.append({"severity":"error","code":"0000_action_row_bleed","corrective_action":clean(action),**identity})
            if no_error_text(raw).casefold() != "no error":
                issues.append({"severity":"error","code":"0000_raw_text_row_bleed","raw_text":clean(raw)[:700],**identity})
        elif normalized_message.casefold() == "no error":
            issues.append({"severity":"error","code":"nonzero_code_has_no_error_message","message":clean(message),**identity})
        elif normalized_message.casefold() in {"to", "through"}:
            issues.append({"severity":"error","code":"range_connector_as_error_message","message":clean(message),**identity})

        # The strict text parser intentionally uses a broad source window. A
        # stored field containing another plausible four-digit error code is a
        # useful warning that the window may have crossed a row boundary.
        for field_name, value in (("message",message),("cause",cause),("corrective_action",action)):
            tokens = set(re.findall(r"(?<![A-Z0-9])([3-9][0-9A-F]{3})(?:H)?(?![A-Z0-9])", clean(value), re.I))
            tokens.discard(str(code).upper().removesuffix("H"))
            if tokens:
                issues.append({
                    "severity":"warning", "code":"field_contains_other_error_code",
                    "field":field_name, "other_codes":sorted(tokens),
                    "value":clean(value)[:700], **identity,
                })
    con.close()
    severity = Counter(item["severity"] for item in issues)
    codes = Counter(item["code"] for item in issues)
    report = {
        "records": len(rows),
        "no_error_records": no_error_records,
        "issue_severity_counts": dict(sorted(severity.items())),
        "issue_code_counts": dict(sorted(codes.items())),
        "issues": issues,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
