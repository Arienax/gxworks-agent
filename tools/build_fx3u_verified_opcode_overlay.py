#!/usr/bin/env python3
"""Build conservative FX3U opcode-identity coverage from structured manual data.

This tool deliberately does NOT promote extracted operand tables into hard
arity/write/device rules.  A structured row may be a partial table slice.  The
output only verifies that an opcode itself exists for FX3U; detailed semantics
remain retrieval-backed until separately verified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "resources" / "knowledge" / "fx3u_knowledge.sqlite"
CATALOG_DIR = ROOT / "resources" / "instructions" / "mitsubishi"
DEFAULT_OUTPUT = CATALOG_DIR / "fx3u_verified_opcodes.json"
DEFAULT_QUARANTINE = CATALOG_DIR / "fx3u_instruction_quarantine.json"


def _json(value, fallback):
    try:
        return json.loads(value or json.dumps(fallback))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _existing_opcodes():
    out = set()
    for name in ("common.json", "fx3u.json", "fx5u.json"):
        payload = json.loads((CATALOG_DIR / name).read_text(encoding="utf-8"))
        for item in payload.get("instructions", []):
            if isinstance(item, dict) and item.get("mnemonic"):
                out.add(str(item["mnemonic"]).strip().upper())
    return out


def _strong_identity(opcode, row):
    fnc = str(row.get("fnc_number") or "").strip()
    if not fnc:
        return False
    variants = {
        str(item).strip().upper()
        for item in _json(row.get("variants_json"), [])
        if str(item).strip()
    }
    if opcode not in variants:
        return False
    # Use the section title, not the summary.  Summary text may contain OCR
    # splits such as "FLDE L" that can make a prefix look like a real opcode.
    # The next-character guard also rejects BK when the official heading is BK+.
    title = str(row.get("title") or "").upper()
    pattern = (
        rf"FNC\s*0*{re.escape(fnc)}\s*[–—\-:/]*\s*"
        rf"{re.escape(opcode)}(?![A-Z0-9_+\-])"
    )
    return bool(re.search(pattern, title, flags=re.I))


def build(database=DEFAULT_DB):
    existing = _existing_opcodes()
    with sqlite3.connect(database) as con:
        cur = con.execute("SELECT * FROM instructions ORDER BY opcode,manual_id,id")
        columns = [item[0] for item in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    grouped = {}
    for row in rows:
        manual = str(row.get("manual_id") or "")
        opcode = str(row.get("opcode") or "").strip().upper()
        if not opcode or opcode in existing:
            continue
        if not (manual.startswith("fx3_") or manual == "fx3_programming_r"):
            continue
        grouped.setdefault(opcode, []).append(row)

    verified = []
    quarantine = []
    for opcode, candidates in sorted(grouped.items()):
        evidence = [row for row in candidates if _strong_identity(opcode, row)]
        if not evidence:
            quarantine.append({
                "opcode": opcode,
                "status": "retrieval_only",
                "reason": "no_exact_fnc_heading_identity",
                "evidence": [
                    {
                        "manual_id": str(row.get("manual_id") or ""),
                        "fnc_number": str(row.get("fnc_number") or ""),
                        "title": str(row.get("title") or ""),
                    }
                    for row in candidates
                ],
            })
            continue
        best = max(
            evidence,
            key=lambda row: (
                len(_json(row.get("operands_json"), [])),
                len(str(row.get("summary") or "")),
                str(row.get("manual_id") or ""),
            ),
        )
        fnc = str(best.get("fnc_number") or "").strip()
        manual = str(best.get("manual_id") or "").strip()
        verified.append({
            "mnemonic": opcode,
            "canonical_op": opcode,
            "category": "action",
            "semantic_kind": "vendor",
            "cpu_support": ["FX3U"],
            "contract_level": "opcode_only",
            "notes": f"Opcode identity verified from {manual} FNC{fnc}; operand semantics remain retrieval-only.",
        })

    overlay = {
        "schema_version": 1,
        "vendor": "mitsubishi",
        "description": "FX3U opcode identities verified from bundled structured manual records. Operand contracts are intentionally not hardened from partial table extraction.",
        "instructions": verified,
    }
    quarantine_payload = {
        "schema_version": 1,
        "vendor": "mitsubishi",
        "description": "Structured instruction candidates intentionally excluded from generation until opcode identity/signature is verified.",
        "entries": quarantine,
    }
    return overlay, quarantine_payload


def _text(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    overlay, quarantine = build(args.database)
    outputs = ((args.output, _text(overlay)), (args.quarantine, _text(quarantine)))
    if args.check:
        mismatches = [str(path) for path, text in outputs if not path.is_file() or path.read_text(encoding="utf-8") != text]
        if mismatches:
            raise SystemExit("generated instruction contract files are stale: " + ", ".join(mismatches))
    else:
        for path, text in outputs:
            path.write_text(text, encoding="utf-8")
    print(json.dumps({"verified_opcode_identities": len(overlay["instructions"]), "quarantined": len(quarantine["entries"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
