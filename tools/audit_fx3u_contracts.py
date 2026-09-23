#!/usr/bin/env python3
"""Audit every catalogued FX3U form and batch-promote corroborated signatures.

No LLM, network, mutable knowledge DB, or inferred len(operands_json) contracts.
Only complete native set-data symbols inside the table's geometry are matched
against an independent structured manual's explicit ST call. ST order is NOT
native ladder order. Other semantic dimensions remain explicitly unverified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as REGISTRY, generation_app_instr_mnemonics

DB = ROOT / "resources/knowledge/fx3u_knowledge.sqlite"
DIRECTORY = ROOT / "resources/instructions/mitsubishi"
OUTPUT = DIRECTORY / "fx3u_contract_promotions.json"
NATIVE = "fx3_programming_r"
STRUCTURED = "fxcpu_basic_applied_m"
METHOD = "native-table-geometry+independent-ST-signature-v1"
SYMBOL = re.compile(r"(?:[SD]|[nm])\d?")
TOKEN = r"[$A-Z][A-Z0-9_.$@+<>=!\-]*"


def sha(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def page_lines(page):
    """Restore horizontal rows; exclude the rotated right-side chapter tabs."""
    lines = []
    for word in sorted(json.loads(page["word_geometry_json"]), key=lambda w: (w["top"], w["x0"])):
        if word["x0"] >= 538:
            continue
        if lines and abs(word["top"] - lines[-1][0]) < 3.5:
            lines[-1][1].append(word)
        else:
            lines.append((word["top"], [word]))
    return [(y, " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"])), words)
            for y, words in lines]


def native_definition(page, tables):
    lines = page_lines(page)
    text = "\n".join(line for _, line, _ in lines)
    heading = re.search(r"FNC\s*(\d+)\s*[–—−-]\s*(" + TOKEN + r")\s*/", text[:700])
    if not heading:
        return None
    starts = [y for y, line, _ in lines if re.match(r"2\.\s*Set data\b", line, re.I)]
    ends = [y for y, line, _ in lines if re.match(r"3\.\s*Applicable devices\b", line, re.I)]
    formats = re.search(r"1\.\s*Instruction format(.*?)2\.\s*Set data", text, re.S | re.I)
    if not starts or not ends or not formats:
        return None
    boxes = []
    for table in tables:
        rows = json.loads(table["rows_json"])
        header = " ".join(rows[0]).lower() if rows else ""
        box = json.loads(table["bbox_json"])
        if "operand" in header and "description" in header and "data" in header and starts[0] < box[1] < ends[0]:
            boxes.append((box, table))
    if len(boxes) > 1:
        return None
    if boxes:
        box, table = boxes[0]
    else:
        # A failed table detector is not permission to use the partial
        # instructions.operands_json. Use the original complete geometric
        # set-data region, and still require independent full-call agreement.
        body_lines = [(y, line) for y, line, _ in lines if starts[0] < y < ends[0]]
        if not any("operand" in line.lower() and "description" in line.lower() for _, line in body_lines):
            return None
        footnotes = [y for y, line in body_lines if re.match(r"\*\d+\.", line)]
        box = [48, starts[0] + 10, 538, min(footnotes) if footnotes else ends[0]]
        table = {"table_index": 0, "table_text": "\n".join(line for _, line in body_lines),
                 "rows_json": "[]", "bbox_json": json.dumps(box)}
    symbols = []
    for y, line, words in lines:
        if not box[1] <= y < box[3]:
            continue
        # Symbol column only. Do not mistake symbols mentioned in descriptions,
        # footnotes, instruction examples or chapter tabs for operand rows.
        candidates = [w for w in words if box[0] <= w["x0"] < min(140, box[2]) and SYMBOL.fullmatch(w["text"])]
        if len(candidates) == 1:
            symbols.append(candidates[0]["text"].upper())
        elif candidates:
            return None  # conditional/composite cells require a separate review
    no_data = "no set data" in table["table_text"].lower()
    if (not symbols and not no_data) or len(set(symbols)) != len(symbols):
        return None
    # The native extraction must account for every nonempty data row. Merged
    # descriptions or extra conditional rows are not silently dropped.
    rows = json.loads(table["rows_json"])
    if rows and symbols and len(rows) - 1 != len(symbols):
        return None
    y_format = next(y for y, line, _ in lines if re.match(r"1\.\s*Instruction format", line, re.I))
    format_words = [w for y, _, words in lines if y_format < y < starts[0] for w in words]
    columns = [w for w in format_words if w["text"] == "Mnemonic"]
    forms = {}
    for column in columns:
        for word in format_words:
            token = word["text"]
            if (word["top"] <= column["top"] + 5 or abs(word["x0"] - column["x0"]) > 16
                    or not re.fullmatch(TOKEN, token)):
                continue
            # Only the actual Mnemonic columns count. The left-side instruction
            # icon prints base EADD even where only DEADD is a supported form.
            conditions = [w for w in format_words if w["text"] in ("Continuous", "Pulse")
                          and 40 < w["x0"] - column["x0"] < 110
                          and abs(w["top"] - word["top"]) < 10]
            widths = [w["text"] for w in format_words if w["text"] in ("16-bit", "32-bit")
                      and 25 < column["x0"] - w["x0"] < 85 and abs(w["top"] - column["top"]) < 5]
            forms[token] = {"execution_form": conditions[0]["text"].lower() if len(conditions) == 1 else None,
                            "instruction_width": int(widths[0][:2]) if len(widths) == 1 else None}
    return {"base": heading[2], "fnc": heading[1], "symbols": symbols,
            "format": formats[1], "forms": forms, "page": page["pdf_page"],
            "section": page["section"], "table_index": table["table_index"],
            "proof": sha(page["word_geometry_json"] + "\n" + table["rows_json"] + "\n" + table["bbox_json"])}


def structured_signatures(page):
    text = "\n".join(line for _, line, _ in page_lines(page))
    heading = re.search(r"\b\d+\.\d+\s+(" + TOKEN + r")\s*/", text[:700])
    region = re.search(r"1\.\s*Format.*?2\.\s*Set data", text, re.S | re.I)
    if not heading or not region:
        return []
    calls = re.findall(r"(?<![A-Za-z0-9_])(" + TOKEN + r")\s*\(\s*EN\s*(,[a-z0-9\s,]*)?\)\s*;", region[0])
    rows = []
    for form, arguments in calls:
        symbols = [s.strip().upper() for s in arguments.lstrip(",").split(",") if s.strip()]
        if any(not re.fullmatch(r"[SDNM]\d?", s) for s in symbols) or len(set(symbols)) != len(symbols):
            continue
        execution = re.findall(r"(?<![A-Z0-9_])" + re.escape(form) + r"\s+(16|32)\s+bits\s+(Continuous|Pulse)\b", region[0])
        rows.append({"form": form, "heading": heading[1], "symbols": symbols,
                     "execution_form": execution[0][1].lower() if len(execution) == 1 else None,
                     "instruction_width": int(execution[0][0]) if len(execution) == 1 else None,
                     "page": page["pdf_page"], "proof": sha(page["word_geometry_json"])})
    return rows


def source_scan(database):
    sources = {m["id"]: m for m in json.loads((ROOT / "resources/knowledge/sources.json").read_text(encoding="utf-8"))["manuals"]}
    natives, signatures = defaultdict(list), defaultdict(list)
    locks = {}
    # SQLite URI is explicitly read-only: no journal, schema, index or corpus mutation.
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for manual in (NATIVE, STRUCTURED):
            actual = dict(connection.execute("SELECT * FROM manuals WHERE manual_id=?", (manual,)).fetchone())
            expected = sources[manual]
            if actual["source_sha256"] != expected["sha256"] or actual["revision"] != expected["revision"] or actual["manual_number"] != expected["manual_number"]:
                raise ValueError("Source manifest mismatch: " + manual)
            locks[manual] = {"manual": actual["manual_number"], "revision": actual["revision"],
                             "sha256": actual["source_sha256"], "url": actual["official_url"]}
        table_map = defaultdict(list)
        for table in connection.execute("SELECT * FROM tables WHERE manual_id=? ORDER BY pdf_page,table_index", (NATIVE,)):
            table_map[table["pdf_page"]].append(dict(table))
        for page in connection.execute("SELECT * FROM page_artifacts WHERE manual_id IN (?,?) ORDER BY manual_id,pdf_page", (NATIVE, STRUCTURED)):
            if page["manual_id"] == NATIVE:
                definition = native_definition(page, table_map[page["pdf_page"]])
                if definition:
                    natives[definition["base"]].append(definition)
            else:
                for signature in structured_signatures(page):
                    signatures[signature["form"]].append(signature)
    return locks, natives, signatures


def build(database=DB):
    locks, natives, signatures = source_scan(database)
    allowed = tuple(generation_app_instr_mnemonics("FX3U"))
    groups, audited = {}, []
    for opcode in allowed:
        # cpu=None reads the unpromoted compatibility catalogue, making audit
        # independent of whether yesterday's generated promotion file is loaded.
        form = REGISTRY.resolve_form(opcode)
        spec = form.spec
        native_rows = [n for definitions in natives.values() for n in definitions if opcode in n["forms"]]
        pairs = []
        for native in native_rows:
            # Some structured APIs have only pulse/double versions. The exact
            # native form and an exact, documented sibling must both appear in
            # the native format table. Never blindly strip D or P from opcodes.
            for call_form, candidates in signatures.items():
                if call_form not in native["forms"]:
                    continue
                for candidate in candidates:
                    if candidate["heading"] not in native["forms"]:
                        continue
                    pairs.append((native, candidate))
        native_shapes = {tuple(n["symbols"]) for n in native_rows}
        counts = {len(candidate["symbols"]) for _, candidate in pairs}
        reason = "corroborated"
        if not native_rows:
            reason = "native_definition_or_form_unresolved"
        elif len(native_shapes) != 1:
            reason = "conflicting_native_definitions"
        elif not pairs:
            reason = "independent_signature_unresolved"
        elif counts != {len(next(iter(native_shapes)))}:
            reason = "cross_manual_arity_conflict"
        elif not spec.accepts_arity(next(iter(counts))):
            reason = "existing_contract_conflict"
        baseline = spec.contract_coverage() if hasattr(spec, "contract_coverage") else {}
        row = {"opcode": opcode, "base_opcode": form.base_mnemonic,
               "cpu_support": sorted(spec.cpu_support), "legacy_level": spec.contract_level, "before_arity": [spec.min_operands, spec.max_operands],
               "decision": reason, "coverage": baseline}
        if reason == "corroborated":
            native, corroborating = min(pairs, key=lambda pair: (pair[0]["page"], pair[1]["page"], pair[1]["form"]))
            order = native["symbols"]
            key = (native["page"], corroborating["page"], tuple(order))
            entry = groups.setdefault(key, {"forms": [], "arity": len(order), "native_order": order,
                "native_page": native["page"], "native_table": native["table_index"],
                "structured_page": corroborating["page"], "native_proof": native["proof"],
                "structured_proof": corroborating["proof"], "execution_forms": {}, "instruction_widths": {}})
            entry["forms"].append(opcode)
            own_calls = [call for n, call in pairs if n["page"] == native["page"] and call["page"] == corroborating["page"] and call["form"] == opcode]
            for field, target in (("execution_form", "execution_forms"), ("instruction_width", "instruction_widths")):
                native_value = native["forms"][opcode][field]
                values = {call[field] for call in own_calls}
                if native_value is not None and values == {native_value}:
                    entry[target][opcode] = native_value
            row.update(after_arity=[len(order), len(order)], native_order=order,
                       structured_order=corroborating["symbols"], native_page=native["page"],
                       structured_page=corroborating["page"], verified_fields=["arity", "operand_order", "form_identity"])
            for field, target in (("execution_form", "execution_forms"), ("instruction_width", "instruction_widths")):
                if opcode in entry[target]:
                    row["verified_fields"].append(field)
                    row[field] = entry[target][opcode]
        else:
            row["candidate_native_pages"] = sorted({n["page"] for n in native_rows})
            row["candidate_structured_arities"] = sorted(counts)
            row["verified_fields"] = []
        row["after_coverage"] = {key: "source_verified" if key in row["verified_fields"] else value for key, value in baseline.items()}
        audited.append(row)
    ledger = {"schema_version": 1, "cpu": "FX3U", "method": METHOD, "sources": locks,
              "entries": sorted(groups.values(), key=lambda row: row["forms"][0])}
    quarantine = json.loads((DIRECTORY / "fx3u_instruction_quarantine.json").read_text(encoding="utf-8"))["entries"]
    report = {"schema_version": 1, "cpu": "FX3U", "scope": "all_catalogued_generation_forms_not_entire_vendor_ISA",
              "method": METHOD, "sources": locks, "generation_forms": len(allowed),
              "decisions": dict(sorted(Counter(row["decision"] for row in audited).items())),
              "before_exact_arity": sum(row["before_arity"][0] is not None and row["before_arity"][0] == row["before_arity"][1] for row in audited),
              "after_exact_arity": sum(row["decision"] == "corroborated" or (row["before_arity"][0] is not None and row["before_arity"][0] == row["before_arity"][1]) for row in audited),
              "verified_dimension_counts": dict(sorted(Counter(field for row in audited for field in row["verified_fields"]).items())),
              "manual_forms_outside_generation_catalogue": sorted({op for rows in natives.values() for n in rows for op in n["forms"]} - set(allowed)),
              "fully_verified_semantics": 0,
              "not_promoted": ["cpu_applicability", "hardware_applicability", "operand_roles", "operand_types", "device_classes", "execution_conditions", "completion_ownership", "numeric_and_memory_boundaries"],
              "rows": audited, "quarantine": quarantine,
              "plc_compilation": "not_performed", "live_model_calls": 0}
    return ledger, report


def ledger_text(ledger):
    # Compact rows keep this generated evidence ledger diffable and inexpensive.
    head = {k: v for k, v in ledger.items() if k != "entries"}
    prefix = json.dumps(head, ensure_ascii=False, indent=2)[:-2]
    return prefix + ',\n  "entries": [\n' + ',\n'.join('    ' + json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in ledger["entries"]) + '\n  ]\n}\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--promote", action="store_true", help="Write only corroborated signature data; never edit the source DB")
    mode.add_argument("--check", action="store_true", help="Verify the committed signature ledger is reproducible")
    args = parser.parse_args(argv)
    for target in (args.output if args.promote else None, args.report):
        if target is not None and (target.resolve() == args.database.resolve() or
                target.exists() and args.database.exists() and target.samefile(args.database)):
            parser.error("Audit output must not overwrite the source database")
    ledger, report = build(args.database)
    text = ledger_text(ledger)
    if args.promote:
        args.output.write_text(text, encoding="utf-8")
    if args.check and (not args.output.is_file() or args.output.read_text(encoding="utf-8") != text):
        parser.error("Promotion ledger differs from corroborated source evidence; inspect --report before --promote")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"rows", "sources", "quarantine", "manual_forms_outside_generation_catalogue"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
