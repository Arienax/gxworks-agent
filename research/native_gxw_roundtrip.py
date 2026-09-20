"""Offline P -> M -> P -> M cross-check using the installed native converter.

This verifies the complete machine byte sequence and ordered native readings,
not just device occurrence sets. It does not execute instructions or establish
source freshness for a compiled resource. Original source bytes are never
replaced with the reverse-converted representation (which can omit text).
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from native_gxw_tokens import (native_batch, native_il_records, canonical_record,
                               listing_records, DEFAULT_DLL, DLL_SHA256)
from gxw.lossless import inspect_project, sha256
from gxw.token_listing import decode_token_program
from gxw.token_pou import parse_token_pou, parse_token_fragment
from gxw.token_resource import parse_token_resource


OK = "0x00000000"


def _request(mode, body, source_index):
    return {"mode": mode, "input_base64": base64.b64encode(body).decode(), "source_index": source_index}


def _output(row):
    return base64.b64decode(row["output_base64"])


def roundtrip_bodies(cases, directory, *, dll=DEFAULT_DLL, cpu_code=0x208):
    """Cases contain body_base64 and arbitrary provenance; retain every stage.

    Native failure offsets are reported in the vendor's own units. Only the
    P -> M path's offset is known here to be a source-token byte offset, often
    at the end of the failing instruction rather than its beginning.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bodies = [base64.b64decode(c["body_base64"], validate=True) for c in cases]
    kwargs = {"dll": dll, "cpu_code": cpu_code}
    forward = native_batch([_request("machinecode", b, i) for i, b in enumerate(bodies)], directory / "forward", **kwargs)
    eligible = [i for i, r in enumerate(forward) if r["return_code"] == OK and r["output_bytes"] > 0]
    reverse = dict(zip(eligible, native_batch([_request("from-machinecode", _output(forward[i]), i)
                                               for i in eligible], directory / "reverse", **kwargs))) if eligible else {}
    reversible = [i for i in eligible if reverse[i]["return_code"] == OK and reverse[i]["output_bytes"] > 0]
    repeated = dict(zip(reversible, native_batch([_request("machinecode", _output(reverse[i]), i)
                                                  for i in reversible], directory / "recompiled", **kwargs))) if reversible else {}
    read_requests = []
    for i in reversible:
        read_requests.extend([dict(_request("decode", bodies[i] + b"\0", i), role="source"),
                              dict(_request("decode", _output(reverse[i]) + b"\0", i), role="reverse")])
    readings = native_batch(read_requests, directory / "native-readings", **kwargs) if read_requests else []
    readings = {i: readings[j * 2:j * 2 + 2] for j, i in enumerate(reversible)}
    checks = []
    for i, (case, body, first) in enumerate(zip(cases, bodies, forward)):
        item = {"index": i, "provenance": case.get("provenance"), "source_sha256": sha256(body),
                "source_bytes": len(body), "forward_return_code": first["return_code"],
                "forward_error_byte_offset": first.get("error_offset"),
                "machinecode_bytes_equal": False, "consumed_all_inputs": False}
        checks.append(item)
        if first["return_code"] != OK:
            item["status"] = "forward-rejected"
            continue
        item.update(machinecode_sha256=sha256(_output(first)), machinecode_bytes=first["output_bytes"],
                    native_words=first["native_value"])
        if i not in reverse:
            item["status"] = "no-machinecode-output"
            continue
        back = reverse[i]
        item["reverse_return_code"] = back["return_code"]
        if i not in repeated:
            item["status"] = "reverse-rejected" if back["return_code"] != OK else "no-reverse-output"
            continue
        again, rebuilt = repeated[i], _output(back)
        item.update(recompiled_return_code=again["return_code"], reverse_source_sha256=sha256(rebuilt),
                    source_bytes_equal=body == rebuilt,
                    machinecode_bytes_equal=again["return_code"] == OK and _output(first) == _output(again),
                    consumed_all_inputs=first["consumed_bytes"] == len(body)
                    and back["consumed_bytes"] == first["output_bytes"]
                    and again["return_code"] == OK and again["consumed_bytes"] == len(rebuilt))
        a, b = readings[i]
        item["native_readings_complete"] = (a["return_code"] == b["return_code"] == OK
                                            and a["consumed_bytes"] == len(body) and b["consumed_bytes"] == len(rebuilt))
        if item["native_readings_complete"]:
            original_records, reverse_records = native_il_records(_output(a)), native_il_records(_output(b))
            item.update(source_native_records=original_records, reverse_native_records=reverse_records,
                        ordered_native_records_equal=original_records == reverse_records,
                        ordered_nontext_records_equal=[r for r in original_records if r["kind"] not in ("statement", "note")] == reverse_records)
        try:
            program = parse_token_fragment(body, 0, len(body))
            ours = decode_token_program(program, text_encoding="cp936")
            item["core_gaps"] = len(ours.gaps)
            item["core_native_readings_agree"] = (item["native_readings_complete"]
                and [canonical_record(r) for r in listing_records(ours)]
                == [canonical_record(r) for r in item["source_native_records"]])
            # Framing-only comparison; no textual re-encoding or device sets.
            without_text = b"".join(t.raw for t in program.tokens if not 0x80 <= t.raw[1] <= 0x8F)
            text_omitted_only = body != rebuilt and without_text == rebuilt
        except (ValueError, IndexError) as exc:
            item["core_projection_error"] = str(exc)
            text_omitted_only = False
        if not item["machinecode_bytes_equal"]:
            item["status"] = "machinecode-differs"
        elif not item["consumed_all_inputs"]:
            item["status"] = "incomplete-consumption"
        elif body == rebuilt:
            item["status"] = "source-byte-identical"
        elif text_omitted_only:
            item["status"] = "source-text-omitted"
        elif item.get("ordered_native_records_equal"):
            item["status"] = "source-normalized"
        else:
            item["status"] = "source-records-differ"
    result = {"dll_sha256": DLL_SHA256, "cpu_code": cpu_code,
              "scope": "offline memory conversion; no execution, project freshness or native project compile/reopen claim",
              "summary": dict(Counter(c["status"] for c in checks)), "cases": checks}
    (directory / "roundtrip.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def require_machinecode_roundtrip(bodies, directory, *, dll=DEFAULT_DLL):
    """Check newly encoded material before composing an experimental project.

    Untouched project regions are not re-encoded, normalized, or discarded.
    The caller remains responsible for source grammar and patch provenance.
    """
    result = roundtrip_bodies([{"body_base64": base64.b64encode(b).decode(), "provenance": {"encoded_index": i}}
                              for i, b in enumerate(bodies)], directory, dll=dll)
    if any(not c["machinecode_bytes_equal"] or not c["consumed_all_inputs"] for c in result["cases"]):
        raise ValueError("native machinecode roundtrip rejected new instructions; input and failure evidence retained")
    return result


def main():
    from gxw_corpus import inputs
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--bodies", type=Path, help="JSON list of body_base64/provenance cases")
    parser.add_argument("--compiled-resources", action="store_true", help="also inspect stored .res code; freshness remains unknown")
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    parser.add_argument("-o", "--output", type=Path, required=True, help="new local evidence directory")
    args = parser.parse_args()
    cases = json.loads(args.bodies.read_text(encoding="utf-8")) if args.bodies else []
    skipped = []
    for location, source in inputs(args.paths):
        for stream in inspect_project(source).streams:
            name = stream.logical_name or stream.name
            kind = "source" if name.endswith(".Program.pou") else "resource" if args.compiled_resources and name.endswith(".res") else None
            if kind is None or stream.raw is None:
                continue
            provenance = {"location": location, "project_sha256": sha256(source), "stream": name, "view": kind}
            try:
                programs = [parse_token_pou(stream.raw)] if kind == "source" else parse_token_resource(stream.raw).code_regions
                for p in programs:
                    cases.append({"body_base64": base64.b64encode(p.body).decode(),
                                  "provenance": dict(provenance, body_offset=p.body_offset)})
            except ValueError as exc:
                skipped.append(dict(provenance, reason=str(exc)))
    if not cases:
        parser.error("no bounded token bodies were found")
    result = roundtrip_bodies(cases, args.output, dll=args.dll)
    (args.output / "skipped.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "skipped": len(skipped)}))


if __name__ == "__main__":
    main()
