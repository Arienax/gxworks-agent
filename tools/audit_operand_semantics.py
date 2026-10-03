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
import platform
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as REGISTRY
from tools.audit_fx3u_contracts import DB, DIRECTORY, NATIVE, OUTPUT as SIGNATURE_LEDGER, source_scan

OUTPUT = DIRECTORY / "fx3u_operand_semantic_promotions.json"
SUMMARY = DIRECTORY / "operand_semantic_coverage_summary.json"
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
            result.setdefault("__all__", []).append(value)
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
    if page_matches:
        rows = page_matches
    elif not rows:
        # D/P/literal variants are frequently documented in the base
        # instruction row. The already-corroborated native page is an exact
        # source identity, so use only rows whose page range contains it.
        rows = [
            row for row in manual_rows.get("__all__", ())
            if row["page_start"] <= native_page <= row["page_end"]
        ]
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


def _summary(report):
    return {key: value for key, value in report.items() if key != "rows"}


def _json_text(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def audit_operand_purpose_coverage(*, char_budget=24000, top_k=8):
    """Audit frozen FX3U positions through the existing runtime retrieval owner.

    Candidate delivery and semantic verification are counted separately. The
    audit does not promote, change the SQLite index or call a provider/native PLC.
    """
    from knowledge import core
    from knowledge.evidence import KnowledgeQuery
    from knowledge.instruction_facts import (
        _operand_evidence_bindings, _operand_gap_details, _operand_table_units, _related_units,
    )
    from knowledge.retriever import build_knowledge_context
    from knowledge.structured_facts import resolve_instruction_records
    from plc.instruction_resolution import resolve_instruction_lanes

    if core._index_identity(core._index_path())[0] == "missing":
        raise ValueError("Purpose coverage requires the bundled runtime knowledge index")
    signatures, forms = _signature_map()
    rows, counts, buckets, samples = [], Counter(), Counter(), {}
    complete_forms, verified_forms, off_definition = 0, 0, 0
    for opcode, signature in sorted(forms.items()):
        slots = resolve_instruction_lanes(opcode, plc_model="FX3U")["operand_slots"]
        order = list(signature["native_order"])
        if [slot.get("symbol") for slot in slots] != order:
            raise ValueError(f"{opcode}: runtime slots differ from the frozen native order")
        context = build_knowledge_context(
            KnowledgeQuery(opcode, precompiled=True, metadata={"instruction_fact_mode": "targeted"}),
            plc_model="FX3U", task_type="generate", char_budget=char_budget, top_k=top_k,
        )
        receipt = context.manifest["instruction_facts"]
        records = [row for row in receipt["records"] if row.get("fact_target") == opcode]
        requirements = {row["position"]: row for row in receipt["operand_facts"]
                        if row["opcode"] == opcode and row["facet"] == "purpose"}
        target = next(row for row in receipt["targets"] if row["opcode"] == opcode)
        seeds = resolve_instruction_records([target], plc_model="FX3U", task_type="generate")
        sources, seen = [], set()
        if seeds:
            primary = (seeds[0].get("manual_id"), seeds[0].get("revision"))
            for seed in seeds:
                for source in (seed, *_related_units(seed, "FX3U", "generate")):
                    if source["id"] in seen or (source.get("manual_id"), source.get("revision")) != primary:
                        continue
                    seen.add(source["id"])
                    sources.append(source)
        gaps = _operand_gap_details(seeds[0]) if seeds else []
        tables = [(source, start, end) for source in sources
                  for start, end in _operand_table_units(source["text"])]
        potential = [binding for source, start, end in tables
                     for binding in _operand_evidence_bindings(source, start, end, gaps, order)]
        form_rows = []
        for slot in slots:
            position = slot["position"]
            delivered = [binding for record in records if record.get("included")
                         for binding in record.get("operand_evidence_bindings") or ()
                         if binding["position"] == position and binding["facet"] == "purpose"]
            conflicts = [item for record in records for item in record.get("operand_candidate_conflicts") or ()
                         if item["position"] == position and item["facet"] == "purpose"]
            status = ("source_verified" if slot.get("purpose_status") == "source_verified"
                      else requirements.get(position, {}).get("status", "unresolved"))
            item = {"position": position, "symbol": slot["symbol"], "status": status}
            if delivered:
                # Literal forms share the ledger's native definition page.
                matches = [binding["source"].get("pdf_page") == signature["native_page"]
                           and binding["source"].get("manual_id") == NATIVE for binding in delivered]
                item["native_definition_match"] = any(matches)
                off_definition += not any(matches)
                item["candidates"] = [{"value": binding["value"], "source": binding["source"]}
                                      for binding in delivered]
            if status in {"unresolved", "budget_omitted"}:
                symbol = slot["symbol"]
                if status == "budget_omitted":
                    bucket = "budget_omitted"
                elif conflicts:
                    bucket = "candidate_conflict"
                elif any(binding["position"] == position and binding["facet"] == "purpose" for binding in potential):
                    bucket = "candidate_unit_quarantined_or_not_selected"
                elif not sources:
                    bucket = "definition_not_resolved"
                elif not tables:
                    bucket = "operand_table_not_recovered"
                elif not any(re.search(r"(?<![A-Z0-9])" + re.escape(symbol) + r"(?![A-Z0-9])",
                                       source["text"][start:end], re.I) for source, start, end in tables):
                    bucket = "operand_symbol_not_recovered"
                else:
                    bucket = "operand_row_not_bound"
                item["failure_bucket"] = bucket
                buckets[bucket] += 1
                if len(samples.setdefault(bucket, [])) < 4:
                    sample = {"opcode": opcode, "position": position, "symbol": symbol,
                              "native_page": signature["native_page"]}
                    if conflicts:
                        sample["conflict"] = conflicts[0]
                    sample["source_tables"] = [{"id": source["id"], "manual_id": source.get("manual_id"),
                                                "text": source["text"][start:end][:2200]}
                                               for source, start, end in tables[:3]]
                    samples[bucket].append(sample)
            counts[status] += 1
            form_rows.append(item)
        complete_forms += all(item["status"] in {"source_verified", "candidate_evidence"} for item in form_rows)
        verified_forms += all(item["status"] == "source_verified" for item in form_rows)
        rows.append({"opcode": opcode, "native_page": signature["native_page"], "operands": form_rows})
    return {
        "schema_version": 1, "scope": "frozen_fx3u_signature_forms", "cpu": "FX3U",
        "signature_ledger": (SIGNATURE_LEDGER.relative_to(ROOT).as_posix()
                             if SIGNATURE_LEDGER.is_relative_to(ROOT) else SIGNATURE_LEDGER.as_posix()),
        "manual_sources": signatures["sources"], "evidence_database": "resources/knowledge/fx3u_knowledge.sqlite",
        "database_mutated": False, "promotion_performed": False,
        "measurement_stage": "shared_knowledge_context_receipt", "char_budget": char_budget, "top_k": top_k,
        "environment": {"python": platform.python_version(), "platform": platform.system()},
        "forms": len(rows), "operand_positions": sum(counts.values()),
        "purpose_status_counts": dict(sorted(counts.items())),
        "forms_with_complete_purpose_evidence": complete_forms,
        "forms_with_all_purposes_verified": verified_forms,
        "candidate_positions_without_native_definition_match": off_definition,
        "failure_buckets": dict(sorted(buckets.items(), key=lambda item: (-item[1], item[0]))),
        "failure_samples": samples, "rows": rows,
        "limits": ["candidate delivery is not purpose verification", "no model call, native compile or PLC execution",
                   "context compiler may subsequently omit evidence according to its separate budget"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--promote", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--purpose-coverage", action="store_true")
    args = parser.parse_args(argv)

    for target in (args.output, args.summary, args.report):
        if target is None:
            continue
        if target.resolve() == args.database.resolve() or (
            target.exists() and args.database.exists() and target.samefile(args.database)
        ):
            parser.error("Audit output must not overwrite the source database")

    if args.purpose_coverage:
        from knowledge import core
        if core._index_path() is None or args.database.resolve() != core._index_path().resolve():
            parser.error("Purpose coverage uses the shared runtime index; --database must match that index")
        report = audit_operand_purpose_coverage()
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(_json_text(report), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key not in {"rows", "failure_samples"}},
                         ensure_ascii=False, sort_keys=True))
        return 0

    ledger, report = build(args.database)
    summary = _summary(report)
    if args.promote:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(_json_text(ledger), encoding="utf-8")
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(_json_text(summary), encoding="utf-8")
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(_json_text(report), encoding="utf-8")
    if args.check:
        try:
            committed_ledger = json.loads(args.output.read_text(encoding="utf-8"))
            committed_summary = json.loads(args.summary.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error("Operand-semantic audit outputs are unavailable: " + str(exc))
        if committed_ledger != ledger:
            parser.error("Operand-semantic promotion ledger is not reproducible")
        if committed_summary != summary:
            parser.error("Operand-semantic coverage summary is not reproducible")
        if args.report is not None:
            try:
                committed_report = json.loads(args.report.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                parser.error("Operand-semantic report is unavailable: " + str(exc))
            if committed_report != report:
                parser.error("Operand-semantic full report is not reproducible")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
