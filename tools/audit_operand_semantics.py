#!/usr/bin/env python3
"""Audit all Mitsubishi registry forms and promote corroborated operand semantics.

The existing knowledge SQLite is opened read-only and remains the evidence store.
This tool never infers arity/order from operands_json: an FX3U form is eligible
only after the independent signature ledger has already corroborated its exact
native operand order. Structured operand rows may then corroborate the registry's
declared read/write roles and, conservatively, data types.

No LLM, network request, database mutation, or new SQLite database is involved.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as REGISTRY
from tools.audit_fx3u_contracts import DB, DIRECTORY, NATIVE, OUTPUT as SIGNATURE_LEDGER, source_scan

OUTPUT = DIRECTORY / "fx3u_operand_semantic_promotions.json"
REPORT = DIRECTORY / "operand_semantic_coverage.json"
METHOD = "signature-gated-native-operands-v1"


def sha(value):
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _signature_map():
    payload = json.loads(SIGNATURE_LEDGER.read_text(encoding="utf-8"))
    result = {}
    for entry in payload.get("entries") or []:
        for form in entry.get("forms") or []:
            result[str(form).upper()] = entry
    return payload, result


def _manual_operand_rows(database, source_lock):
    """Return native manual operand rows keyed by literal/base forms."""
    expected = source_lock[NATIVE]
    result = {}
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        manual = db.execute(
            "SELECT manual_number,revision,source_sha256 FROM manuals WHERE manual_id=?",
            (NATIVE,),
        ).fetchone()
        if manual is None:
            raise ValueError("Native instruction manual is missing")
        if (
            manual["manual_number"] != expected["manual"]
            or manual["revision"] != expected["revision"]
            or manual["source_sha256"] != expected["sha256"]
        ):
            raise ValueError("Native operand source does not match locked manual")

        for row in db.execute(
            "SELECT opcode,variants_json,operands_json,page_start,page_end "
            "FROM instructions WHERE manual_id=? ORDER BY page_start,opcode",
            (NATIVE,),
        ):
            try:
                variants = json.loads(row["variants_json"] or "[]")
                operands = json.loads(row["operands_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(variants, list) or not isinstance(operands, list):
                continue
            forms = {str(row["opcode"] or "").upper()}
            forms.update(str(item).upper() for item in variants if str(item or "").strip())
            value = {
                "opcode": str(row["opcode"] or "").upper(),
                "operands": operands,
                "page_start": int(row["page_start"] or 0),
                "page_end": int(row["page_end"] or 0),
                "proof": sha(
                    json.dumps(
                        {
                            "opcode": row["opcode"],
                            "variants": variants,
                            "operands": operands,
                            "page_start": row["page_start"],
                            "page_end": row["page_end"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
            }
            for form in forms:
                result.setdefault(form, []).append(value)
    return result


def _normalized_manual_type(value):
    text = " ".join(str(value or "").strip().casefold().split())
    if not text:
        return None
    if text in {"bit", "bool", "boolean"}:
        return "bit"
    if "32" in text or "double word" in text:
        return "dword"
    if "16" in text or text in {"word", "integer"}:
        return "word"
    if "real" in text or "float" in text:
        return "real"
    if "string" in text:
        return "string"
    return None


def _symbol_role(symbol):
    token = str(symbol or "").upper()
    if token.startswith("S"):
        return "read"
    if token.startswith(("N", "M")):
        return "read"
    if token.startswith("D"):
        return "write"
    return None


def _select_operand_source(form, signature, manual_rows):
    rows = list(manual_rows.get(form) or ())
    native_page = int(signature.get("native_page") or 0)
    page_matches = [
        row for row in rows
        if row["page_start"] <= native_page <= row["page_end"]
    ]
    rows = page_matches or rows
    exact = []
    expected = [str(item).upper() for item in signature.get("native_order") or ()]
    for row in rows:
        operands = row.get("operands") or []
        positions = [
            str(item.get("position") or "").strip().upper()
            for item in operands if isinstance(item, dict)
        ]
        if positions == expected and len(positions) == len(operands):
            exact.append(row)
    if len(exact) != 1:
        return None, "operand_rows_unresolved" if not exact else "ambiguous_operand_rows"
    return exact[0], "exact"


def _semantic_decision(form, signature, manual_rows):
    resolution = REGISTRY.resolve_form(form)
    if resolution is None:
        return None, "registry_form_missing"
    spec = resolution.spec
    order = [str(item).upper() for item in signature.get("native_order") or ()]
    if len(spec.operands) != len(order):
        return None, "registry_operand_count_mismatch"

    source, source_status = _select_operand_source(form, signature, manual_rows)
    if source is None:
        return None, source_status
    rows = source["operands"]
    if any(not str(item.get("description") or "").strip() for item in rows):
        return None, "operand_description_incomplete"

    expected_roles = [operand.role.value for operand in spec.operands]
    source_roles = [_symbol_role(symbol) for symbol in order]
    role_verified = bool(source_roles) and all(source_roles)
    if role_verified:
        # Destination symbols do not prove read-modify-write behavior. Promote
        # only where the registry already declares the simple source/destination
        # role that the native symbols independently corroborate.
        role_verified = source_roles == expected_roles

    declared_types = [operand.data_type for operand in spec.operands]
    source_types = [_normalized_manual_type(item.get("data_type")) for item in rows]
    type_verified = (
        bool(source_types)
        and all(source_types)
        and all(value != "any" for value in declared_types)
        and source_types == declared_types
    )

    promoted = []
    if role_verified:
        promoted.append("operand_roles")
    if type_verified:
        promoted.append("operand_types")
    if not promoted:
        if any(role == "read_write" for role in expected_roles):
            reason = "read_write_requires_explicit_semantics"
        elif source_roles != expected_roles:
            reason = "source_role_conflicts_with_registry"
        else:
            reason = "no_additional_verified_dimension"
        return {
            "source": source,
            "roles": expected_roles,
            "types": declared_types,
            "source_types": source_types,
            "verified_fields": [],
        }, reason

    return {
        "source": source,
        "roles": expected_roles,
        "types": declared_types,
        "source_types": source_types,
        "verified_fields": promoted,
    }, "promoted"


def build(database=DB):
    locks, _native, _signatures = source_scan(database)
    signature_payload, signatures = _signature_map()
    manual_rows = _manual_operand_rows(database, locks)

    rows = []
    promotions = []
    all_forms = sorted(REGISTRY.known_mnemonics())
    for opcode in all_forms:
        neutral = REGISTRY.resolve_form(opcode)
        fx3 = REGISTRY.resolve_form(opcode, cpu="FX3U")
        fx5 = REGISTRY.resolve_form(opcode, cpu="FX5U")
        spec = neutral.spec if neutral is not None else None
        signature = signatures.get(opcode)
        decision = "no_fx3_signature_evidence"
        semantic = None
        if signature is not None:
            semantic, decision = _semantic_decision(opcode, signature, manual_rows)

        row = {
            "opcode": opcode,
            "base_opcode": neutral.base_mnemonic if neutral else opcode,
            "declared_operands": len(spec.operands) if spec else 0,
            "declared_roles": [item.role.value for item in spec.operands] if spec else [],
            "declared_types": [item.data_type for item in spec.operands] if spec else [],
            "fx3u_supported": bool(fx3 and fx3.spec.supports_cpu("FX3U")),
            "fx5u_supported": bool(fx5 and fx5.spec.supports_cpu("FX5U")),
            "signature_verified_fx3u": bool(signature),
            "decision": decision,
            "verified_fields": list(semantic.get("verified_fields") or ()) if semantic else [],
        }
        if semantic and semantic.get("source"):
            source = semantic["source"]
            row.update(
                native_order=list(signature.get("native_order") or ()),
                native_page=signature.get("native_page"),
                operand_source_page=[source["page_start"], source["page_end"]],
                operand_source_proof=source["proof"],
            )
        rows.append(row)

        if semantic and semantic.get("verified_fields"):
            source = semantic["source"]
            promotions.append({
                "form": opcode,
                "native_order": list(signature.get("native_order") or ()),
                "operand_roles": list(semantic["roles"]),
                **(
                    {"operand_types": list(semantic["types"])}
                    if "operand_types" in semantic["verified_fields"] else {}
                ),
                "verified_fields": list(semantic["verified_fields"]),
                "native_page": int(signature["native_page"]),
                "operand_page_start": source["page_start"],
                "operand_page_end": source["page_end"],
                "operand_proof": source["proof"],
            })

    source = locks[NATIVE]
    ledger = {
        "schema_version": 1,
        "cpu": "FX3U",
        "method": METHOD,
        "signature_ledger_method": signature_payload.get("method"),
        "signature_ledger_sha256": sha(SIGNATURE_LEDGER.read_bytes()),
        "source": source,
        "entries": promotions,
    }
    decisions = Counter(row["decision"] for row in rows)
    dimensions = Counter(
        field for row in rows for field in row.get("verified_fields") or ()
    )
    report = {
        "schema_version": 1,
        "scope": "all_mitsubishi_registry_forms",
        "evidence_database": "resources/knowledge/fx3u_knowledge.sqlite",
        "database_mutated": False,
        "registry_forms": len(rows),
        "forms_with_declared_operands": sum(bool(row["declared_operands"]) for row in rows),
        "fx3u_supported_forms": sum(row["fx3u_supported"] for row in rows),
        "fx5u_supported_forms": sum(row["fx5u_supported"] for row in rows),
        "fx3u_signature_verified_forms": sum(row["signature_verified_fx3u"] for row in rows),
        "operand_role_promotions": dimensions["operand_roles"],
        "operand_type_promotions": dimensions["operand_types"],
        "decisions": dict(sorted(decisions.items())),
        "rows": rows,
    }
    return ledger, report


def _json_text(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--promote", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    for target in (args.output, args.report):
        if target.resolve() == args.database.resolve() or (
            target.exists() and args.database.exists() and target.samefile(args.database)
        ):
            parser.error("Audit output must not overwrite the source database")

    ledger, report = build(args.database)
    ledger_text = _json_text(ledger)
    report_text = _json_text(report)
    if args.promote:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(ledger_text, encoding="utf-8")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_text, encoding="utf-8")
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != ledger_text:
            parser.error("Operand-semantic promotion ledger is not reproducible")
        if not args.report.is_file() or args.report.read_text(encoding="utf-8") != report_text:
            parser.error("Operand-semantic coverage report is not reproducible")
    print(json.dumps({
        key: value for key, value in report.items()
        if key != "rows"
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
