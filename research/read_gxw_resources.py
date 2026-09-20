"""Inspect compiled FX .res code without assuming it matches current source.

Optionally compare every bounded code region with the offline vendor decoder.
All resource bytes, duplicate regions and source references remain inspectable.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from gxw_corpus import inputs
from gxw.lossless import inspect_project, sha256
from gxw.token_listing import decode_token_program
from gxw.token_resource import parse_token_resource
from native_gxw_tokens import native_batch, native_il_records, canonical_record, listing_records, DLL_SHA256


def inspect_resources(paths, directory, *, native=False, encoding=None):
    directory.mkdir(parents=True, exist_ok=False)
    resources, diagnostics = {}, []
    for location, source in inputs(paths):
        image = inspect_project(source)
        if image.diagnostics:
            diagnostics.append({"source": location, "diagnostics": list(image.diagnostics)})
        diagnostics.extend({"source": location, "stream": s.logical_name or s.name, "error": s.error}
                           for s in image.streams if s.error)
        by_name = {s.logical_name: s for s in image.streams if s.raw is not None and s.logical_name}
        for name, stream in by_name.items():
            if not name.endswith(".res"):
                continue
            key = sha256(stream.raw)
            case = resources.setdefault(key, {"resource_sha256": key, "raw_base64": base64.b64encode(stream.raw).decode(), "sources": []})
            try:
                program_names = parse_token_resource(stream.raw).observed_program_names
            except ValueError:
                program_names = None
            program_name = program_names[0] + ".Program.pou" if program_names else None
            associated = by_name.get(program_name)
            case["sources"].append({"location": location, "resource_name": name,
                "project_sha256": image.sha256, "observed_program_name": program_name,
                "source_program_sha256": sha256(associated.raw) if associated else None})
    cases, requests, bindings = [], [], []
    for case in resources.values():
        raw = base64.b64decode(case["raw_base64"])
        case["source_freshness"] = "not-checkable: .res may predate source edits or a failed compilation"
        try:
            resource = parse_token_resource(raw)
            case.update(code_spans=list(resource.code_spans), opaque_suffix_offset=resource.suffix_offset,
                        byte_replay="byte-identical" if resource.reconstruct() == raw else "differs", regions=[])
            for region in resource.code_regions:
                listing = decode_token_program(region, text_encoding=encoding)
                entry = {"offset": region.body_offset, "length": len(region.body), "body_sha256": sha256(region.body),
                         "records": listing_records(listing), "gaps": len(listing.gaps)}
                case["regions"].append(entry)
                if native:
                    requests.append({"mode": "decode", "resource_sha256": case["resource_sha256"], "offset": region.body_offset,
                                     "input_base64": base64.b64encode(region.body + b"\0").decode()})
                    bindings.append(entry)
        except ValueError as exc:
            case["unsupported"] = str(exc)
        cases.append(case)
    # Inputs and Python findings survive even when the vendor process fails.
    output = directory / "resources.json"
    result = {"scope": "compiled code view; no freshness, source equivalence or PLC execution claim",
              "text_encoding": encoding, "cases": cases, "diagnostics": diagnostics,
              "native_dll_sha256": DLL_SHA256 if native else None}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if native:
        for entry, response in zip(bindings, native_batch(requests, directory / "native")):
            entry["native_response"] = response
            if response["return_code"] == "0x00000000":
                records = native_il_records(base64.b64decode(response["output_base64"]), encoding=encoding or "cp936")
                entry["native_records"] = records
                entry["native_cross_check"] = "agrees" if (response["consumed_bytes"] == entry["length"] and
                    [canonical_record(r) for r in entry["records"]] == [canonical_record(r) for r in records]) else "differs"
            else:
                entry["native_cross_check"] = "native-error"
    result["summary"] = {"unique_resources": len(cases), "source_references": sum(len(c["sources"]) for c in cases),
        "unsupported": sum("unsupported" in c for c in cases),
        "empty_resources": sum(c.get("regions") == [] for c in cases),
        "source_versions_sharing_resource": sum(len({s["source_program_sha256"] for s in c["sources"]}) > 1 for c in cases),
        "code_regions": sum(len(c.get("regions", [])) for c in cases),
        "native_cross_checks": dict(Counter(r["native_cross_check"] for c in cases for r in c.get("regions", []) if "native_cross_check" in r))}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--text-encoding")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_resources(args.paths, args.output, native=args.native, encoding=args.text_encoding)["summary"]))
